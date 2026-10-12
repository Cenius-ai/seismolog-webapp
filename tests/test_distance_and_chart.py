"""F2 (distance) and F3 (chart) acceptance tests, plus the seeded sample data."""

from __future__ import annotations

import json
import re

import pytest

import seismolog
from conftest import record_args

KM = re.compile(r"(\d+(?:\.\d+)?)\s*km")
KILOMETRES = re.compile(r"(\d+(?:\.\d+)?)")


def distance_km(result) -> float:
    match = KM.search(result.out) or KILOMETRES.search(result.out)
    assert match, "no kilometre figure in output: %r" % result.out
    return float(match.group(1))


# --------------------------------------------------------------------------- #
# T3 -- distance
# --------------------------------------------------------------------------- #
def test_distance_names_the_nearest_boundary_and_a_one_decimal_figure(run, store):
    result = run("distance", "--lat", "0", "--lon", "0")

    assert result.code == 0
    names = [name for name, _ in seismolog.BOUNDARIES]
    assert any(name in result.out for name in names)
    assert re.search(r"\d+\.\d\s*km", result.out)


def test_distance_is_deterministic_and_needs_no_store(run, store):
    first = run("distance", "--lat", "48.85", "--lon", "2.35")
    second = run("distance", "--lat", "48.85", "--lon", "2.35")

    assert first.code == second.code == 0
    assert first.out == second.out
    assert not store.exists()


def test_ridge_point_is_closer_than_a_plate_interior_point(run, store):
    ridge = run("distance", "--lat", "0", "--lon", "-30")
    interior = run("distance", "--lat", "40", "--lon", "-100")

    assert ridge.code == 0 and interior.code == 0
    assert "Mid-Atlantic Ridge" in ridge.out
    assert distance_km(ridge) < distance_km(interior)


@pytest.mark.parametrize(
    "lat, lon, expected",
    [
        (95, 0, "latitude must be between -90 and 90"),
        (-90.5, 0, "latitude must be between -90 and 90"),
        (0, 200, "longitude must be between -180 and 180"),
        (0, -181, "longitude must be between -180 and 180"),
    ],
)
def test_distance_rejects_out_of_range_coordinates(run, store, lat, lon, expected):
    result = run("distance", "--lat", str(lat), "--lon", str(lon))

    assert result.code == 2
    assert expected in result.err
    assert result.out == ""


@pytest.mark.parametrize("lat, lon", [("north", "0"), ("0", "east"), ("", "0")])
def test_distance_rejects_non_numeric_coordinates(run, store, lat, lon):
    result = run("distance", "--lat", lat, "--lon", lon)

    assert result.code == 2
    assert "must be a number" in result.err or "is required" in result.err


def test_distance_without_coordinates_prints_usage(run, store):
    result = run("distance")

    assert result.code == 2
    assert "usage: seismolog distance --lat" in result.err


def test_distance_accepts_the_extreme_valid_coordinates(run, store):
    for lat, lon in [(-90, -180), (90, 180), (0, 0)]:
        result = run("distance", "--lat", str(lat), "--lon", str(lon))
        assert result.code == 0, result.err
        assert distance_km(result) >= 0.0


def test_distance_json_mode(run, store):
    result = run("distance", "--lat", "0", "--lon", "-30", "--json")

    assert result.code == 0
    payload = json.loads(result.out)
    assert payload["boundary"] == "Mid-Atlantic Ridge"
    assert payload["query"] == {"lat": 0.0, "lon": -30.0}
    assert isinstance(payload["distance_km"], float)
    assert payload["nearest_vertex"] == {"lat": 0.0, "lon": -28.0}


# --------------------------------------------------------------------------- #
# T4 -- chart
# --------------------------------------------------------------------------- #
def seed_five(run):
    for magnitude, depth, place, moment in [
        (3.0, 5.0, "Micro, Oklahoma", "2020-01-02T00:00:00Z"),
        (4.6, 8.0, "Felt, Italy", "2020-02-03T00:00:00Z"),
        (5.4, 12.0, "Moderate, Chile", "2020-03-04T00:00:00Z"),
        (6.1, 22.0, "Strong, Japan", "2020-04-05T00:00:00Z"),
        (7.2, 30.0, "Major, Alaska", "2020-05-06T00:00:00Z"),
    ]:
        assert run(*record_args(magnitude, depth, place, moment)).code == 0


def test_chart_prints_one_scaled_row_per_event(run, store):
    seed_five(run)

    result = run("chart")

    assert result.code == 0
    lines = result.out.splitlines()
    assert "5 events" in lines[0]
    assert "3.0" in lines[0] and "7.2" in lines[0]
    assert re.search(r"^id\s+date\s+mag\s+place\s+bar$", lines[1])
    body = lines[3:8]
    assert len(body) == 5
    assert [row.split()[0] for row in body] == ["1", "2", "3", "4", "5"]
    assert [row.split()[2] for row in body] == ["3.0", "4.6", "5.4", "6.1", "7.2"]
    assert "Micro, Oklahoma" in body[0]
    assert "Major, Alaska" in body[4]

    lengths = [len(re.findall(r"[\u2588#]", row)) for row in body]
    assert lengths == sorted(lengths)
    assert lengths[0] >= 1
    assert lengths[-1] == seismolog.BAR_WIDTH
    assert "axis" in lines[-2] and "3.0" in lines[-2] and "7.2" in lines[-2]
    assert "proportional" in lines[-1]


def test_chart_is_ordered_by_time(run, store):
    run(*record_args(4.0, 5, "Later", moment="2024-05-01T00:00:00Z"))
    run(*record_args(9.0, 5, "Earlier", moment="2001-05-01T00:00:00Z"))

    result = run("chart")

    assert result.code == 0
    body = result.out.splitlines()[3:5]
    assert "Earlier" in body[0] and "Later" in body[1]


def test_chart_on_an_empty_store_invites_a_first_record(run, store):
    result = run("chart")

    assert result.code == 0
    assert (
        result.out.strip()
        == "No events to chart yet. Try: seismolog record -m 4.5 -d 10"
    )


def test_chart_with_one_event_collapses_the_axis(run, store):
    run(*record_args(7.2, 12, "Solo event", moment="2021-08-14T00:00:00Z"))

    result = run("chart")

    assert result.code == 0
    assert "Traceback" not in result.err
    assert "1 event" in result.out
    body = result.out.splitlines()[3:4]
    assert len(body) == 1
    assert body[0].count("\u2588") == seismolog.BAR_WIDTH
    assert "collapsed" in result.out


def test_chart_reports_a_corrupt_store_by_path(run, store):
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(
        '{"id": 1, "time": "2024-01-01T00:00:00Z", "magnitude": 4,\n', encoding="utf-8"
    )

    result = run("chart")

    assert result.code == 3
    assert str(store) in result.err
    assert "Traceback" not in result.err


def test_chart_stays_within_eighty_columns(run, store):
    seed_five(run)
    run(
        *record_args(
            9.9,
            30,
            "A very long place name that would otherwise wrap the row",
            "2020-06-07T00:00:00Z",
        )
    )

    result = run("chart")

    assert result.code == 0
    assert max(len(line) for line in result.out.splitlines()) <= 80


def test_chart_json_mode_carries_the_scaled_rows(run, store):
    seed_five(run)

    result = run("chart", "--json")

    assert result.code == 0
    payload = json.loads(result.out)
    assert payload["count"] == 5
    assert payload["min_magnitude"] == 3.0 and payload["max_magnitude"] == 7.2
    assert len(payload["rows"][0]["bar"]) == 1
    assert len(payload["rows"][-1]["bar"]) == seismolog.BAR_WIDTH


# --------------------------------------------------------------------------- #
# seed
# --------------------------------------------------------------------------- #
def test_seed_fills_the_store_once_and_is_idempotent(run, store):
    first = run("seed")
    count_after_first = len(store.read_text(encoding="utf-8").splitlines())
    second = run("seed")
    count_after_second = len(store.read_text(encoding="utf-8").splitlines())

    assert first.code == second.code == 0
    assert count_after_first == len(seismolog.SAMPLE_EVENTS)
    assert count_after_second == count_after_first
    assert "nothing seeded" in second.out


def test_seed_force_appends_the_sample_sequence_again(run, store):
    run("seed")
    result = run("seed", "--force")

    assert result.code == 0
    stored = store.read_text(encoding="utf-8").splitlines()
    assert len(stored) == 2 * len(seismolog.SAMPLE_EVENTS)
    assert len({json.loads(line)["id"] for line in stored}) == len(stored)


def test_seeded_store_renders_every_view(run, store):
    assert run("seed").code == 0

    listing = run("list")
    chart = run("chart")

    assert listing.code == 0 and chart.code == 0
    assert "Events %d" % len(seismolog.SAMPLE_EVENTS) in listing.out
    assert len(listing.out.splitlines()) == len(seismolog.SAMPLE_EVENTS) + 3
    assert "%d events" % len(seismolog.SAMPLE_EVENTS) in chart.out.splitlines()[0]
    assert "Tohoku, Japan" in listing.out
    assert "Mandalay, Myanmar" in chart.out
