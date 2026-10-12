"""Unit tests for the pieces the CLI commands are built from."""

from __future__ import annotations

import json
import math
import os
import pty
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

import seismolog
from conftest import CLI, record_args


# --------------------------------------------------------------------------- #
# Haversine + boundary table
# --------------------------------------------------------------------------- #
def test_haversine_matches_known_great_circle_distances():
    assert seismolog.haversine_km(0, 0, 0, 0) == 0.0
    assert seismolog.haversine_km(0, 0, 0, 1) == pytest.approx(111.19, abs=0.05)
    assert seismolog.haversine_km(0, 0, 0, 90) == pytest.approx(
        seismolog.EARTH_RADIUS_KM * math.pi / 2, rel=1e-6
    )
    assert seismolog.haversine_km(-33.87, 151.21, 51.51, -0.13) == pytest.approx(
        16993, rel=5e-3
    )


def test_haversine_is_symmetric():
    assert seismolog.haversine_km(12.5, -70.1, -45.2, 130.4) == pytest.approx(
        seismolog.haversine_km(-45.2, 130.4, 12.5, -70.1)
    )


def test_boundary_table_is_well_formed_and_named():
    names = [name for name, _ in seismolog.BOUNDARIES]

    assert len(seismolog.BOUNDARIES) >= 8
    assert len(names) == len(set(names))
    assert "Mid-Atlantic Ridge" in names
    assert "East African Rift" in names
    for name, points in seismolog.BOUNDARIES:
        assert name.strip()
        assert len(points) >= 3
        for lat, lon in points:
            assert -90.0 <= lat <= 90.0
            assert -180.0 <= lon <= 180.0


def test_nearest_boundary_picks_the_closest_named_vertex():
    hit = seismolog.nearest_boundary(37.2, -121.9)

    assert hit.name == "San Andreas Fault"
    assert hit.distance_km < 160.0
    assert (hit.vertex_lat, hit.vertex_lon) == (37.0, -122.0)


def test_nearest_boundary_is_never_negative():
    for lat in (-90, -45, 0, 45, 90):
        for lon in (-180, -90, 0, 90, 180):
            assert seismolog.nearest_boundary(lat, lon).distance_km >= 0.0


# --------------------------------------------------------------------------- #
# Timestamps
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2024-03-01", "2024-03-01T00:00:00Z"),
        ("2024-03-01T12:30:45Z", "2024-03-01T12:30:45Z"),
        ("2024-03-01T14:00:00+02:00", "2024-03-01T12:00:00Z"),
        ("2024-03-01T12:00:00", "2024-03-01T12:00:00Z"),
    ],
)
def test_parse_iso_utc_normalises_to_utc(raw, expected):
    assert seismolog.format_iso_utc(seismolog.parse_iso_utc(raw)) == expected


@pytest.mark.parametrize("raw", ["", "yesterday", "01/03/2024", "2024-13-45T99:00:00Z"])
def test_parse_iso_utc_rejects_junk(raw):
    with pytest.raises(ValueError):
        seismolog.parse_iso_utc(raw)


# --------------------------------------------------------------------------- #
# Store plumbing
# --------------------------------------------------------------------------- #
def test_store_path_prefers_the_environment_override(monkeypatch, tmp_path):
    monkeypatch.setenv("SEISMOLOG_STORE", str(tmp_path / "custom.jsonl"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    assert seismolog.store_path() == tmp_path / "custom.jsonl"


def test_store_path_falls_back_to_home(monkeypatch, tmp_path):
    monkeypatch.delenv("SEISMOLOG_STORE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    assert seismolog.store_path() == tmp_path / "home" / ".seismolog" / "events.jsonl"


def test_store_path_ignores_an_empty_override(monkeypatch, tmp_path):
    monkeypatch.setenv("SEISMOLOG_STORE", "")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    assert seismolog.store_path() == tmp_path / "home" / ".seismolog" / "events.jsonl"


def test_read_events_on_a_missing_store_is_empty(tmp_path):
    assert seismolog.read_events(tmp_path / "nothing" / "here.jsonl") == []


def test_read_events_refuses_a_directory(tmp_path):
    with pytest.raises(seismolog.StoreError):
        seismolog.read_events(tmp_path)


def test_read_events_orders_by_time_then_id(tmp_path):
    path = tmp_path / "events.jsonl"
    payloads = [
        {
            "id": 3,
            "time": "2024-01-01T00:00:00Z",
            "magnitude": 5.0,
            "depth_km": 1.0,
            "place": "c",
        },
        {
            "id": 1,
            "time": "2019-01-01T00:00:00Z",
            "magnitude": 6.0,
            "depth_km": 2.0,
            "place": "a",
        },
        {
            "id": 2,
            "time": "2019-01-01T00:00:00Z",
            "magnitude": 7.0,
            "depth_km": 3.0,
            "place": "b",
        },
    ]
    path.write_text(
        "".join(json.dumps(item) + "\n" for item in payloads), encoding="utf-8"
    )

    assert [event.id for event in seismolog.read_events(path)] == [1, 2, 3]


def test_read_events_defaults_a_missing_place(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(
        json.dumps(
            {
                "id": 1,
                "time": "2024-01-01T00:00:00Z",
                "magnitude": 4.0,
                "depth_km": 5.0,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert seismolog.read_events(path)[0].place == "unknown"


def test_read_events_keeps_earlier_events_when_the_tail_is_truncated(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(
        json.dumps(
            {
                "id": 1,
                "time": "2024-01-01T00:00:00Z",
                "magnitude": 4.0,
                "depth_km": 5.0,
                "place": "intact",
            }
        )
        + "\n"
        + '{"id": 2, "time": "2024-0',
        encoding="utf-8",
    )

    with pytest.raises(seismolog.StoreError) as caught:
        seismolog.read_events(path)

    assert "line 2" in str(caught.value)
    assert "intact" in path.read_text(encoding="utf-8")


def test_next_event_id_is_one_past_the_highest(tmp_path):
    events = [
        seismolog.Event(1, "2024-01-01T00:00:00Z", 4.0, 5.0, "a"),
        seismolog.Event(9, "2024-02-01T00:00:00Z", 5.0, 6.0, "b"),
        seismolog.Event(4, "2024-03-01T00:00:00Z", 6.0, 7.0, "c"),
    ]

    assert seismolog.next_event_id(events) == 10
    assert seismolog.next_event_id([]) == 1


def test_append_event_never_rewrites_existing_lines(tmp_path):
    path = tmp_path / "events.jsonl"
    first = seismolog.Event(1, "2024-01-01T00:00:00Z", 4.0, 5.0, "a")
    second = seismolog.Event(2, "2024-02-01T00:00:00Z", 5.0, 6.0, "b")

    seismolog.append_event(path, first)
    original = path.read_bytes()
    seismolog.append_event(path, second)

    assert path.read_bytes().startswith(original)
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_canonical_stamps_are_fixed_width_and_sort_chronologically():
    stamps = [
        "2024-01-02T03:04:05Z",
        "2019-12-31T23:59:59Z",
        "2024-01-02T03:04:04Z",
    ]

    assert all(len(stamp) == 20 for stamp in stamps)
    assert sorted(stamps) == [
        "2019-12-31T23:59:59Z",
        "2024-01-02T03:04:04Z",
        "2024-01-02T03:04:05Z",
    ]


def test_read_events_handles_a_five_thousand_event_store_in_time_order(tmp_path):
    path = tmp_path / "events.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for index in range(1, 5001):
            handle.write(
                json.dumps(
                    {
                        "id": index,
                        "time": "20%02d-%02d-%02dT%02d:%02d:%02dZ"
                        % (
                            index % 25,
                            index % 12 + 1,
                            index % 27 + 1,
                            index % 24,
                            index % 60,
                            index % 60,
                        ),
                        "magnitude": round(2.0 + (index % 80) / 10.0, 1),
                        "depth_km": float(index % 700),
                        "place": "Station %d" % index,
                    }
                )
                + "\n"
            )

    events = seismolog.read_events(path)

    assert len(events) == 5000
    assert events == sorted(events, key=lambda event: (event.time, event.id))
    assert events[0].time == "2000-01-01T12:00:00Z"
    assert events[0].time < events[-1].time


# --------------------------------------------------------------------------- #
# Rendering helpers
# --------------------------------------------------------------------------- #
def test_chart_bar_maps_magnitude_onto_the_store_range():
    assert (
        seismolog.chart_bar(3.0, 3.0, 7.0)
        == seismolog.GLYPHS.bar * 1 + seismolog.GLYPHS.track * 31
    )
    assert (
        seismolog.chart_bar(7.0, 3.0, 7.0) == seismolog.GLYPHS.bar * seismolog.BAR_WIDTH
    )
    assert (
        seismolog.chart_bar(5.0, 3.0, 7.0)
        == seismolog.GLYPHS.bar * 16 + seismolog.GLYPHS.track * 16
    )


def test_chart_bar_collapses_when_every_magnitude_is_equal():
    assert (
        seismolog.chart_bar(6.2, 6.2, 6.2) == seismolog.GLYPHS.bar * seismolog.BAR_WIDTH
    )


def test_truncate_never_exceeds_the_column_width():
    assert seismolog.truncate("short", 20) == "short"
    truncated = seismolog.truncate("x" * 50, 12)
    assert len(truncated) == 12
    assert truncated.endswith(seismolog.GLYPHS.ellipsis)


def test_display_text_collapses_control_characters():
    assert seismolog.display_text("Ridge\ncrest\tCA") == "Ridge crest CA"


def test_render_table_pads_columns_and_rules():
    lines = seismolog.render_table(
        ("id", "place"), [["1", "Alpha"], ["22", "B"]], ("r", "l")
    )

    assert lines[0] == "id  place"
    assert lines[1] == seismolog.GLYPHS.rule * 2 + "  " + seismolog.GLYPHS.rule * 5
    assert lines[2] == " 1  Alpha"
    assert lines[3] == "22  B"
    assert not any(line.endswith(" ") for line in lines)


# --------------------------------------------------------------------------- #
# Colour discipline
# --------------------------------------------------------------------------- #
def test_colour_is_emitted_on_a_tty_but_not_into_a_pipe(tmp_path):
    env = os.environ.copy()
    env["SEISMOLOG_STORE"] = str(tmp_path / "events.jsonl")
    env["HOME"] = str(tmp_path)
    env.pop("NO_COLOR", None)

    master, slave = pty.openpty()
    try:
        process = subprocess.Popen(
            [sys.executable, str(CLI), *record_args(5, 10, "Colour")],
            stdout=slave,
            stderr=subprocess.PIPE,
            env=env,
            cwd=str(CLI.parent),
        )
        os.close(slave)
        chunks = []
        while True:
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            chunks.append(chunk)
        process.wait(timeout=60)
    finally:
        os.close(master)
    tty_output = b"".join(chunks).decode("utf-8", "replace")

    assert process.returncode == 0
    assert seismolog.ANSI_ACCENT in tty_output

    piped = subprocess.run(
        [sys.executable, str(CLI), "list"], capture_output=True, text=True, env=env
    )
    assert piped.returncode == 0
    assert "\x1b[" not in piped.stdout


def test_no_color_environment_variable_wins_even_on_a_tty(tmp_path):
    env = os.environ.copy()
    env["SEISMOLOG_STORE"] = str(tmp_path / "events.jsonl")
    env["HOME"] = str(tmp_path)
    env["NO_COLOR"] = "1"
    completed = subprocess.run(
        [sys.executable, str(CLI), *record_args(5, 10, "Plain")],
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode == 0
    assert "\x1b[" not in completed.stdout


# --------------------------------------------------------------------------- #
# Product metadata
# --------------------------------------------------------------------------- #
def test_version_constant_matches_the_pyproject_metadata():
    metadata = (Path(CLI.parent) / "pyproject.toml").read_text(encoding="utf-8")

    assert 'version = "%s"' % seismolog.__version__ in metadata


def test_sample_events_are_plausible_and_varied():
    moments = [seismolog.parse_iso_utc(row[0]) for row in seismolog.SAMPLE_EVENTS]
    magnitudes = [row[1] for row in seismolog.SAMPLE_EVENTS]

    assert len(seismolog.SAMPLE_EVENTS) >= 10
    assert all(0.0 <= row[1] <= 10.0 for row in seismolog.SAMPLE_EVENTS)
    assert all(row[2] >= 0.0 for row in seismolog.SAMPLE_EVENTS)
    assert all(row[3].strip() for row in seismolog.SAMPLE_EVENTS)
    assert len({row[3] for row in seismolog.SAMPLE_EVENTS}) == len(
        seismolog.SAMPLE_EVENTS
    )
    assert max(magnitudes) - min(magnitudes) >= 2.0
    assert min(moments) < datetime(2015, 1, 1, tzinfo=timezone.utc)
