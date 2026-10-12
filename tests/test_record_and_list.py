"""F1 (record) and F4 (store + list) acceptance tests, driven through the CLI."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from conftest import record_args

ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def store_lines(path: Path) -> list:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# --------------------------------------------------------------------------- #
# T1 -- record
# --------------------------------------------------------------------------- #
def test_record_echoes_the_stored_event(run, store):
    result = run(*record_args(5, 10, "Test Ridge"))

    assert result.code == 0
    assert "#1" in result.out
    assert "Test Ridge" in result.out
    assert re.search(r"^\s*id:\s+1$", result.out, re.M)
    assert re.search(r"^\s*magnitude:\s+5\.0$", result.out, re.M)
    assert re.search(r"^\s*depth km:\s+10\.0$", result.out, re.M)
    stamp = re.search(r"^\s*time:\s+(\S+)$", result.out, re.M)
    assert stamp and ISO_UTC.match(stamp.group(1))

    stored = store_lines(store)
    assert len(stored) == 1
    assert stored[0]["id"] == 1
    assert stored[0]["magnitude"] == 5.0
    assert stored[0]["depth_km"] == 10.0
    assert stored[0]["place"] == "Test Ridge"
    assert stored[0]["time"] == stamp.group(1)


def test_record_defaults_place_to_unknown_and_time_to_now(run, store):
    before = datetime.now(timezone.utc)
    result = run(*record_args(4.5, 10))
    after = datetime.now(timezone.utc)

    assert result.code == 0
    assert "unknown" in result.out
    stored = store_lines(store)[0]
    assert stored["place"] == "unknown"
    moment = datetime.fromisoformat(stored["time"].replace("Z", "+00:00"))
    assert before.replace(microsecond=0) <= moment <= after


def test_record_assigns_a_new_greater_id_each_time(run, store):
    first = run(*record_args(5, 10, "One"))
    second = run(*record_args(6, 20, "Two"))
    third = run(*record_args(7, 30, "Three"))

    assert [first.code, second.code, third.code] == [0, 0, 0]
    assert "#1" in first.out and "#2" in second.out and "#3" in third.out
    ids = [event["id"] for event in store_lines(store)]
    assert ids == [1, 2, 3]


def test_record_accepts_the_inclusive_magnitude_bounds(run, store):
    assert run(*record_args(0, 1)).code == 0
    assert run(*record_args(10, 1)).code == 0
    assert [event["magnitude"] for event in store_lines(store)] == [0.0, 10.0]


def test_record_normalises_an_offset_timestamp_to_utc(run, store):
    result = run(*record_args(4.2, 8, moment="2024-03-01T14:00:00+02:00"))

    assert result.code == 0
    assert store_lines(store)[0]["time"] == "2024-03-01T12:00:00Z"
    assert "2024-03-01T12:00:00Z" in result.out


def test_record_accepts_a_bare_date(run, store):
    result = run(*record_args(4.2, 8, moment="2024-03-01"))

    assert result.code == 0
    assert store_lines(store)[0]["time"] == "2024-03-01T00:00:00Z"


@pytest.mark.parametrize(
    "magnitude, depth",
    [(12, 10), (-1, 10), (10.1, 10), (5, -3), (5, -0.5)],
)
def test_record_rejects_out_of_range_values_without_writing(
    run, store, magnitude, depth
):
    result = run(*record_args(magnitude, depth))

    assert result.code != 0
    assert result.code == 2
    assert result.out == ""  # results never land on stdout when the write fails
    assert result.err.strip().startswith("error:")
    assert not store.exists()


def test_record_rejects_an_out_of_range_magnitude_with_a_readable_message(run, store):
    result = run(*record_args(12, 10))

    assert result.code == 2
    assert "magnitude must be between 0 and 10" in result.err
    assert not store.exists()


def test_record_rejects_a_negative_depth_with_a_readable_message(run, store):
    result = run(*record_args(5, -3))

    assert result.code == 2
    assert "depth must be non-negative" in result.err
    assert not store.exists()


def test_record_rejects_an_unparseable_time(run, store):
    result = run(*record_args(5, 10, moment="yesterday"))

    assert result.code == 2
    assert "ISO-8601" in result.err
    assert not store.exists()


def test_record_rejects_a_non_numeric_magnitude(run, store):
    result = run("record", "-m", "strong", "-d", "10")

    assert result.code == 2
    assert "magnitude must be a number" in result.err
    assert not store.exists()


def test_record_without_arguments_prints_usage_and_a_sample_command(run, store):
    result = run("record")

    assert result.code == 2
    assert "usage: seismolog record -m" in result.err
    assert "seismolog record -m 4.5 -d 10" in result.err
    assert not store.exists()


def test_a_failed_record_never_rewrites_existing_events(run, store):
    run(*record_args(5, 10, "Kept"))
    before = store.read_bytes()

    result = run(*record_args(99, 10))

    assert result.code == 2
    assert store.read_bytes() == before
    assert len(store_lines(store)) == 1


def test_record_keeps_every_line_valid_json(run, store):
    for magnitude, depth, place in [(5, 10, "A"), (6, 20, "B"), (7, 30, "C")]:
        assert run(*record_args(magnitude, depth, place)).code == 0

    raw = store.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    assert len(raw.splitlines()) == 3
    for line in raw.splitlines():
        assert set(json.loads(line)) == {"id", "time", "magnitude", "depth_km", "place"}


def test_record_json_mode_reports_the_stored_event(run, store):
    result = run(
        *record_args(5.2, 3, "Bakersfield, California", moment="2024-08-06T12:09:00Z"),
        "--json",
    )

    assert result.code == 0
    payload = json.loads(result.out)
    assert payload["stored"] == {
        "id": 1,
        "time": "2024-08-06T12:09:00Z",
        "magnitude": 5.2,
        "depth_km": 3.0,
        "place": "Bakersfield, California",
    }
    assert payload["store"] == str(store)


def test_record_surfaces_a_corrupt_store_instead_of_appending(run, store):
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text('{"id": 1, "time": "2024-01-01T00:00:00Z"}\n', encoding="utf-8")

    result = run(*record_args(5, 10))

    assert result.code == 3
    assert str(store) in result.err
    assert store.read_text(encoding="utf-8").count("\n") == 1


# --------------------------------------------------------------------------- #
# T2 -- list
# --------------------------------------------------------------------------- #
def test_list_orders_rows_by_time_not_by_record_order(run, store):
    assert run(*record_args(3.1, 5, "Newest", moment="2024-03-01T00:00:00Z")).code == 0
    assert run(*record_args(6.9, 8, "Oldest", moment="2019-07-04T00:00:00Z")).code == 0
    assert run(*record_args(5.4, 11, "Middle", moment="2023-11-12T00:00:00Z")).code == 0

    result = run("list")

    assert result.code == 0
    rows = [
        line
        for line in result.out.splitlines()
        if line.strip() and not line.startswith("Events")
    ]
    rows = [
        line for line in rows if not line.lstrip().startswith(("id", "\u2500", "-"))
    ]
    assert len(rows) == 3
    assert [row.split()[1] for row in rows] == [
        "2019-07-04T00:00:00Z",
        "2023-11-12T00:00:00Z",
        "2024-03-01T00:00:00Z",
    ]
    assert [row.split()[-2] for row in rows] == ["8.0", "11.0", "5.0"]
    assert "Oldest" in rows[0] and "Middle" in rows[1] and "Newest" in rows[2]
    assert [row.split()[0] for row in rows] == ["2", "3", "1"]


def test_list_shows_an_id_column_for_every_event(run, store):
    run(*record_args(5, 10, "Alpha"))
    run(*record_args(6, 20, "Beta"))

    result = run("list")

    assert result.code == 0
    assert re.search(
        r"^id\s+time\s+mag\s+depth km\s+place$", result.out.strip().splitlines()[1]
    )
    body = result.out.splitlines()[3:]
    assert len(body) == 2
    assert body[0].split()[0] == "1" and "Alpha" in body[0]
    assert body[1].split()[0] == "2" and "Beta" in body[1]


def test_list_on_an_empty_store_invites_a_first_record(run, store):
    result = run("list")

    assert result.code == 0
    assert (
        result.out.strip()
        == "No events recorded yet. Try: seismolog record -m 4.5 -d 10"
    )
    assert not store.exists()


def test_list_does_not_create_the_store(run, store):
    run("list")
    run("chart")

    assert not store.exists()


@pytest.mark.parametrize(
    "content",
    [
        "not json at all\n",
        '{"id": 1, "time": "2024-01-01T00:00:00Z", "magnitude": 4.0}\n',
        '["id", 1]\n',
        '{"id": "one", "time": "2024-01-01T00:00:00Z", "magnitude": 4.0, "depth_km": 5.0}\n',
        '{"id": 1, "time": "whenever", "magnitude": 4.0, "depth_km": 5.0}\n',
        '{"id": 1, "time": "2024-01-01T00:00:00Z", "magnitude": 4.0, "depth_km": 5.0}\n\n',
        '{"id": 1, "time": "2024-01-01T00:00:00Z", "magnitude": 4.0, "depth_km": 5.0}\n{"id": 2,\n',
    ],
)
def test_list_reports_a_corrupt_store_by_path(run, store, content):
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(content, encoding="utf-8")

    result = run("list")

    assert result.code == 3
    assert str(store) in result.err
    assert result.err.strip().startswith("error:")
    assert "Traceback" not in result.err


def test_list_keeps_every_line_within_eighty_columns(run, store):
    store.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for index in range(1, 201):
        month = (index % 12) + 1
        day = (index % 27) + 1
        lines.append(
            json.dumps(
                {
                    "id": index,
                    "time": "20%02d-%02d-%02dT03:04:05Z" % (index % 25, month, day),
                    "magnitude": round(2.0 + (index % 80) / 10.0, 1),
                    "depth_km": float((index * 7) % 700),
                    "place": "A long place description for row number %d, somewhere on a ridge"
                    % index,
                }
            )
        )
    store.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = run("list")

    assert result.code == 0
    assert len(result.out.splitlines()) == 203  # title, header, rule, 200 rows
    assert max(len(line) for line in result.out.splitlines()) <= 80


def test_list_json_mode_is_machine_readable(run, store):
    run(*record_args(3.1, 5, "Newest", moment="2024-03-01T00:00:00Z"))
    run(*record_args(6.9, 8, "Oldest", moment="2019-07-04T00:00:00Z"))

    result = run("list", "--json")

    assert result.code == 0
    payload = json.loads(result.out)
    assert payload["count"] == 2
    assert [event["place"] for event in payload["events"]] == ["Oldest", "Newest"]
    assert [event["id"] for event in payload["events"]] == [2, 1]


def test_list_renders_a_place_with_newlines_on_a_single_row(run, store):
    assert run(*record_args(4.0, 5, "Ridge\ncrest\tCA")).code == 0

    result = run("list")

    assert result.code == 0
    assert "Ridge crest CA" in result.out
    assert len([line for line in result.out.splitlines() if "Ridge" in line]) == 1


def test_output_is_plain_when_piped(run, store):
    run(*record_args(5, 10, "Plain"))

    result = run("list")

    assert result.code == 0
    assert "\x1b[" not in result.out


def test_an_unknown_command_exits_with_the_usage_code(run, store):
    result = run("frobnicate")

    assert result.code == 2
    assert "usage:" in result.err.lower()


def test_bare_invocation_prints_help_and_succeeds(run, store):
    result = run()

    assert result.code == 0
    assert "record" in result.out and "distance" in result.out
    assert "--version" in result.out


def test_version_flag(run, store):
    result = run("--version")

    assert result.code == 0
    assert result.out.strip() == "seismolog 1.0.0"
