#!/usr/bin/env python3
"""Seismolog -- record seismic events, measure distance to plate boundaries, chart history.

A single-file, standard-library-only command line tool.

  record    append a seismic event (magnitude, depth, optional place and time)
  list      print the stored events, oldest first, each row carrying its id
  distance  great-circle distance from a point to the nearest bundled plate boundary
  chart     ASCII comparison of the stored event magnitudes over time
  seed      load the bundled sample sequence into the store (idempotent)

Everything is local: no network access, no third-party packages, no data files.
The tectonic boundary table is a literal constant in this module, and every event
lives in one append-only JSON-lines store ($HOME/.seismolog/events.jsonl, or the
path named by $SEISMOLOG_STORE).

Design direction -- calm-precise, terminal dark, monospace, compact:
  accent     oklch(0.58 0.12 156)  ==  #2e8f5b  (truecolor ANSI on a TTY only)
  surfaces   no chrome, box-drawing rules, aligned columns, one spacing scale
  signal     the accent marks primary status only, never colour alone: bars carry
             their own block glyphs and every state carries words.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

__version__ = "1.0.0"
PROG = "seismolog"

# Exit codes: 0 success, 2 bad input / usage, 3 store problem. Distinct per class
# so a script can tell "you typed it wrong" from "your store is unusable".
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_STORE = 3

# --------------------------------------------------------------------------- #
# Design tokens (the committed direction, in one place)
# --------------------------------------------------------------------------- #
ACCENT_OKLCH = "oklch(0.58 0.12 156)"
ACCENT_HEX = "#2e8f5b"
ANSI_RESET = "\x1b[0m"
ANSI_ACCENT = "\x1b[38;2;46;143;91m"  # a non-CSS surface cannot parse oklch()
ANSI_DIM = "\x1b[2m"

# Layout budget: every table is laid out to fit 80 columns unwrapped.
TERM_WIDTH = 80
GAP = "  "
RECORD_USAGE = (
    "usage: %s record -m MAGNITUDE -d DEPTH [-p PLACE] [--time ISO8601]" % PROG
)
DISTANCE_USAGE = "usage: %s distance --lat DEG --lon DEG" % PROG
PLACE_WIDTH = TERM_WIDTH - 44  # id 3 + time 20 + mag 3 + depth 8 + gutters 8 + margin 2
CHART_PLACE_WIDTH = 22
BAR_WIDTH = 32
MAX_PLACE_CHARS = 120


class Glyphs:
    """Box-drawing set, with an ASCII fallback for non-UTF-8 terminals."""

    def __init__(
        self,
        rule: str,
        bar: str,
        track: str,
        ellipsis: str,
        degree: str,
        arrow: str,
        dash: str,
    ) -> None:
        self.rule = rule
        self.bar = bar
        self.track = track
        self.ellipsis = ellipsis
        self.degree = degree
        self.arrow = arrow
        self.dash = dash


def _pick_glyphs() -> Glyphs:
    fancy = Glyphs(
        rule="\u2500",
        bar="\u2588",
        track="\u2591",
        ellipsis="\u2026",
        degree="\u00b0",
        arrow="\u25ba",
        dash="\u2014",
    )
    plain = Glyphs(
        rule="-",
        bar="#",
        track=".",
        ellipsis="...",
        degree="deg",
        arrow=">",
        dash="--",
    )
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        "".join(
            [
                fancy.rule,
                fancy.bar,
                fancy.track,
                fancy.ellipsis,
                fancy.degree,
                fancy.arrow,
                fancy.dash,
            ]
        ).encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return plain
    return fancy


GLYPHS = _pick_glyphs()


class Painter:
    """Colour is decoration only: it is dropped for pipes, files and NO_COLOR."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def accent(self, text: str) -> str:
        return "%s%s%s" % (ANSI_ACCENT, text, ANSI_RESET) if self.enabled else text

    def dim(self, text: str) -> str:
        return "%s%s%s" % (ANSI_DIM, text, ANSI_RESET) if self.enabled else text


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class SeismologError(Exception):
    """Base class for every failure this tool reports to the user."""

    exit_code = EXIT_USAGE

    def __init__(self, message: str, hint: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class ValidationError(SeismologError):
    """The user handed us something we cannot store or compute."""

    exit_code = EXIT_USAGE


class StoreError(SeismologError):
    """The event store is missing, unreadable or corrupt."""

    exit_code = EXIT_STORE


# --------------------------------------------------------------------------- #
# Domain: events
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Event:
    """One recorded seismic event -- exactly one line of the store."""

    id: int
    time: str  # canonical ISO-8601 UTC, e.g. 2024-03-01T00:00:00Z
    magnitude: float
    depth_km: float
    place: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "time": self.time,
            "magnitude": self.magnitude,
            "depth_km": self.depth_km,
            "place": self.place,
        }


def format_iso_utc(moment: datetime) -> str:
    """Canonical second-precision UTC stamp; sorts lexicographically."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso_utc(raw: str) -> datetime:
    """Parse ISO-8601 into an aware UTC datetime; naive input is read as UTC.

    Accepts a bare date ('2024-03-01'), a stamp ('2024-03-01T12:00:00Z') and an
    offset stamp ('2024-03-01T14:00:00+02:00'), which is normalised to UTC.
    """
    text = raw.strip()
    if not text:
        raise ValueError("empty timestamp")
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    moment = datetime.fromisoformat(text)  # ValueError on anything else
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _as_number(value: Any) -> Optional[float]:
    """Numeric value, or None when it is a bool/str/non-finite number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def event_from_payload(payload: Any, origin: str) -> Event:
    """Build an Event from one decoded store line, or raise StoreError."""
    if not isinstance(payload, dict):
        raise StoreError(
            "%s is not a JSON object" % origin,
            "Each store line must be one event object; repair or remove that line.",
        )
    for key in ("id", "time", "magnitude", "depth_km"):
        if key not in payload:
            raise StoreError(
                "%s has no %r field" % (origin, key),
                "Each store line must carry id, time, magnitude and depth_km.",
            )
    raw_id = payload["id"]
    if isinstance(raw_id, bool) or not isinstance(raw_id, int):
        raise StoreError("%s has a non-integer id %r" % (origin, raw_id))
    raw_time = payload["time"]
    if not isinstance(raw_time, str):
        raise StoreError("%s has a non-text time %r" % (origin, raw_time))
    try:
        moment = parse_iso_utc(raw_time)
    except ValueError:
        raise StoreError(
            "%s has an unreadable time %r" % (origin, raw_time),
            "Times are ISO-8601, e.g. 2024-03-01T12:00:00Z.",
        )
    magnitude = _as_number(payload["magnitude"])
    if magnitude is None:
        raise StoreError(
            "%s has a non-numeric magnitude %r" % (origin, payload["magnitude"])
        )
    depth = _as_number(payload["depth_km"])
    if depth is None:
        raise StoreError(
            "%s has a non-numeric depth_km %r" % (origin, payload["depth_km"])
        )
    place = payload.get("place", "unknown")
    if not isinstance(place, str):
        raise StoreError("%s has a non-text place %r" % (origin, place))
    return Event(
        id=raw_id,
        time=format_iso_utc(moment),
        magnitude=magnitude,
        depth_km=depth,
        place=place or "unknown",
    )


# --------------------------------------------------------------------------- #
# Domain: the store
# --------------------------------------------------------------------------- #
def store_path() -> Path:
    """$SEISMOLOG_STORE when set, otherwise $HOME/.seismolog/events.jsonl."""
    override = (os.getenv("SEISMOLOG_STORE") or "").strip()
    if override:
        return Path(override).expanduser()
    home = (os.getenv("HOME") or "").strip()
    if not home:
        try:
            home = str(Path.home())
        except RuntimeError:  # no HOME and no passwd entry: stay relative
            home = "."
    return Path(home) / ".seismolog" / "events.jsonl"


def empty_store_message() -> str:
    return "No events recorded yet. Try: %s record -m 4.5 -d 10" % PROG


def read_events(path: Path) -> List[Event]:
    """Stored events in ascending time order; [] when the store does not exist."""
    if not path.exists():
        return []
    if path.is_dir():
        raise StoreError("%s is a directory, not an event store" % path)
    try:
        with path.open("r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError as exc:
        raise StoreError("cannot read %s: %s" % (path, exc.strerror or exc))
    events: List[Event] = []
    for number, raw in enumerate(lines, start=1):
        if not raw.strip():
            raise StoreError(
                "%s: line %d is blank" % (path, number),
                "The store is corrupt; repair or remove that line and try again.",
            )
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise StoreError(
                "%s: line %d is not valid JSON (%s)" % (path, number, exc.msg),
                "The store is corrupt; repair or remove that line and try again.",
            )
        events.append(event_from_payload(payload, "%s: line %d" % (path, number)))
    # Canonical stamps are fixed-width ISO-8601 UTC, so lexicographic order is
    # chronological order -- no second parse needed on the hot read path.
    events.sort(key=lambda event: (event.time, event.id))
    return events


def append_event(path: Path, event: Event) -> None:
    """Append one flushed JSON line: an interrupted run cannot lose earlier events."""
    line = json.dumps(event.as_dict(), ensure_ascii=False)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise StoreError("cannot write %s: %s" % (path, exc.strerror or exc))


def next_event_id(events: Sequence[Event]) -> int:
    return max((event.id for event in events), default=0) + 1


# --------------------------------------------------------------------------- #
# Domain: plate boundaries (literal constant -- ships in the source, offline)
# --------------------------------------------------------------------------- #
EARTH_RADIUS_KM = 6371.0088

# Literal data table kept compact on purpose: one boundary per entry.
# fmt: off
BOUNDARIES: Tuple[Tuple[str, Tuple[Tuple[float, float], ...]], ...] = (
    ("Mid-Atlantic Ridge", (
        (65.0, -18.0), (55.0, -28.0), (45.0, -28.0), (35.0, -35.0), (25.0, -45.0),
        (10.0, -40.0), (0.0, -28.0), (-15.0, -15.0), (-30.0, -14.0), (-45.0, -15.0),
        (-55.0, -3.0),
    )),
    ("East Pacific Rise", (
        (32.0, -113.0), (20.0, -105.0), (10.0, -103.0), (0.0, -102.0),
        (-20.0, -112.0), (-35.0, -110.0), (-50.0, -115.0),
    )),
    ("San Andreas Fault", (
        (40.0, -124.0), (37.0, -122.0), (34.0, -118.0), (31.0, -115.0),
    )),
    ("Cascadia Subduction Zone", (
        (50.0, -130.0), (46.0, -125.0), (42.0, -124.0), (40.0, -124.0),
    )),
    ("Aleutian Arc", (
        (52.0, 175.0), (53.0, -170.0), (54.0, -165.0), (54.0, -160.0), (53.0, -155.0),
    )),
    ("Ryukyu Trench", (
        (31.0, 129.0), (28.0, 127.0), (25.0, 124.0), (23.0, 122.0), (21.0, 121.0),
    )),
    ("Japan Trench", (
        (45.0, 150.0), (40.0, 143.0), (36.0, 141.0), (32.0, 138.0), (28.0, 133.0),
    )),
    ("Mariana Trench", (
        (24.0, 143.0), (18.0, 145.0), (13.0, 145.0), (8.0, 143.0),
    )),
    ("Peru-Chile Trench", (
        (0.0, -81.0), (-10.0, -79.0), (-20.0, -71.0), (-30.0, -72.0), (-40.0, -75.0),
        (-50.0, -76.0),
    )),
    ("Himalayan Collision Zone", (
        (35.0, 72.0), (30.0, 80.0), (28.0, 88.0), (27.0, 95.0),
    )),
    ("East African Rift", (
        (12.0, 40.0), (5.0, 36.0), (-3.0, 36.0), (-10.0, 34.0), (-15.0, 35.0),
    )),
    ("Alpine Fault, New Zealand", (
        (-35.0, 173.0), (-40.0, 175.0), (-44.0, 170.0), (-46.0, 167.0),
    )),
    ("Southeast Indian Ridge", (
        (-25.0, 70.0), (-35.0, 78.0), (-45.0, 85.0), (-50.0, 90.0),
    )),
    ("Red Sea Rift", (
        (28.0, 34.0), (20.0, 38.0), (15.0, 41.0), (12.0, 43.0),
    )),
    ("Carlsberg Ridge", (
        (10.0, 57.0), (5.0, 62.0), (0.0, 66.0), (-5.0, 68.0),
    )),
)
# fmt: on


@dataclass(frozen=True)
class BoundaryHit:
    name: str
    distance_km: float
    vertex_lat: float
    vertex_lon: float


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres between two lat/lon points."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def nearest_boundary(lat: float, lon: float) -> BoundaryHit:
    """Nearest bundled boundary vertex across every polyline in BOUNDARIES."""
    best: Optional[BoundaryHit] = None
    for name, points in BOUNDARIES:
        for point_lat, point_lon in points:
            distance = haversine_km(lat, lon, point_lat, point_lon)
            if best is None or distance < best.distance_km:
                best = BoundaryHit(
                    name=name,
                    distance_km=distance,
                    vertex_lat=point_lat,
                    vertex_lon=point_lon,
                )
    if best is None:  # pragma: no cover - BOUNDARIES is a non-empty literal
        raise StoreError("the bundled plate boundary table is empty")
    return best


# --------------------------------------------------------------------------- #
# Validation helpers
# --------------------------------------------------------------------------- #
def parse_number(raw: Optional[str], label: str, example: str) -> float:
    if raw is None or raw == "":
        raise ValidationError(
            "%s is required" % label,
            "try: %s %s" % (PROG, example),
        )
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValidationError("%s must be a number (got %r)" % (label, raw))
    if not math.isfinite(value):
        raise ValidationError("%s must be a finite number (got %r)" % (label, raw))
    return value


def validate_magnitude(raw: Optional[str]) -> float:
    value = parse_number(raw, "magnitude", "record -m 4.5 -d 10")
    if not 0.0 <= value <= 10.0:
        raise ValidationError(
            "magnitude must be between 0 and 10 (got %s)" % _trim(value)
        )
    return value


def validate_depth(raw: Optional[str]) -> float:
    value = parse_number(raw, "depth", "record -m 4.5 -d 10")
    if value < 0.0:
        raise ValidationError(
            "depth must be non-negative kilometres (got %s)" % _trim(value)
        )
    return value


def validate_time(raw: Optional[str]) -> datetime:
    if raw is None or raw == "":
        return datetime.now(timezone.utc)
    try:
        return parse_iso_utc(raw)
    except ValueError:
        raise ValidationError(
            "time must be ISO-8601, e.g. 2024-03-01 or 2024-03-01T12:00:00Z (got %r)"
            % raw
        )


def validate_place(raw: Optional[str]) -> str:
    if raw is None:
        return "unknown"
    place = raw.strip()
    if not place:
        return "unknown"
    if len(place) > MAX_PLACE_CHARS:
        raise ValidationError(
            "place must be at most %d characters (got %d)"
            % (MAX_PLACE_CHARS, len(place))
        )
    return place


def validate_latitude(raw: Optional[str]) -> float:
    value = parse_number(raw, "latitude", "distance --lat 0 --lon 0")
    if not -90.0 <= value <= 90.0:
        raise ValidationError(
            "latitude must be between -90 and 90 (got %s)" % _trim(value)
        )
    return value


def validate_longitude(raw: Optional[str]) -> float:
    value = parse_number(raw, "longitude", "distance --lat 0 --lon 0")
    if not -180.0 <= value <= 180.0:
        raise ValidationError(
            "longitude must be between -180 and 180 (got %s)" % _trim(value)
        )
    return value


def _trim(value: float) -> str:
    text = ("%.1f" % value).rstrip("0").rstrip(".")
    return text if text not in ("", "-") else "0"


# --------------------------------------------------------------------------- #
# Rendering helpers
# --------------------------------------------------------------------------- #
def display_text(text: str) -> str:
    """Collapse control characters so one record can never wrap into two rows."""
    return " ".join(text.split())


def truncate(text: str, width: int) -> str:
    text = display_text(text)
    if len(text) <= width:
        return text
    if width <= len(GLYPHS.ellipsis):
        return text[:width]
    return text[: width - len(GLYPHS.ellipsis)] + GLYPHS.ellipsis


def render_table(
    headers: Sequence[str], rows: Sequence[Sequence[str]], aligns: Sequence[str]
) -> List[str]:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    lines = [
        GAP.join(
            header.ljust(width) if align == "l" else header.rjust(width)
            for header, width, align in zip(headers, widths, aligns)
        ).rstrip(),
        GAP.join(GLYPHS.rule * width for width in widths),
    ]
    for row in rows:
        rendered = GAP.join(
            cell.ljust(width) if align == "l" else cell.rjust(width)
            for cell, width, align in zip(row, widths, aligns)
        )
        lines.append(rendered.rstrip())
    return lines


def format_degrees(value: float, positive: str, negative: str) -> str:
    hemisphere = positive if value >= 0 else negative
    return "%.1f%s%s" % (abs(value), GLYPHS.degree, hemisphere)


def attribute_block(rows: Sequence[Tuple[str, str]]) -> List[str]:
    """Aligned key/value block -- the tool's one surface shape."""
    width = max(len(key) for key, _ in rows)
    return ["%s %s" % ((key + ":").ljust(width + 1), value) for key, value in rows]


def event_rows(events: Sequence[Event]) -> List[List[str]]:
    return [
        [
            str(event.id),
            event.time,
            "%.1f" % event.magnitude,
            "%.1f" % event.depth_km,
            truncate(event.place, PLACE_WIDTH),
        ]
        for event in events
    ]


def chart_bar(magnitude: float, lowest: float, highest: float) -> str:
    """Bar whose length maps magnitude onto the store's own range."""
    span = highest - lowest
    if span <= 0.0:
        length = BAR_WIDTH
    else:
        length = int(round((magnitude - lowest) / span * BAR_WIDTH))
        length = max(1, min(BAR_WIDTH, length))
    return GLYPHS.bar * length + GLYPHS.track * (BAR_WIDTH - length)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_record(args: argparse.Namespace, out: Any, err: Any, painter: Painter) -> int:
    if args.magnitude is None or args.depth is None:
        _write(err, RECORD_USAGE)
    magnitude = validate_magnitude(args.magnitude)
    depth_km = validate_depth(args.depth)
    place = validate_place(args.place)
    moment = validate_time(args.time)

    path = store_path()
    events = read_events(path)  # a corrupt store is reported before anything is written
    event = Event(
        id=next_event_id(events),
        time=format_iso_utc(moment),
        magnitude=magnitude,
        depth_km=depth_km,
        place=place,
    )
    append_event(path, event)
    if args.json:
        _write(
            out,
            json.dumps(
                {"stored": event.as_dict(), "store": str(path)}, ensure_ascii=False
            ),
        )
        return EXIT_OK

    _write(out, "Recorded event %s" % painter.accent("#%d" % event.id))
    _write(out, GLYPHS.rule * 20)
    for line in attribute_block(
        [
            ("id", str(event.id)),
            ("time", event.time),
            ("magnitude", "%.1f" % event.magnitude),
            ("depth km", "%.1f" % event.depth_km),
            ("place", display_text(event.place)),
        ]
    ):
        _write(out, "  " + line)
    _write(out, painter.dim("store: %s" % path))
    return EXIT_OK


def cmd_list(args: argparse.Namespace, out: Any, err: Any, painter: Painter) -> int:
    path = store_path()
    events = read_events(path)
    if args.json:
        _write(
            out,
            json.dumps(
                {
                    "store": str(path),
                    "count": len(events),
                    "events": [event.as_dict() for event in events],
                },
                ensure_ascii=False,
            ),
        )
        return EXIT_OK
    if not events:
        _write(out, empty_store_message())
        return EXIT_OK
    _write(
        out,
        "Events %d %s %s"
        % (len(events), GLYPHS.dash, painter.dim("oldest first, time ascending")),
    )
    for line in render_table(
        ("id", "time", "mag", "depth km", "place"),
        event_rows(events),
        ("r", "l", "r", "r", "l"),
    ):
        _write(out, line)
    return EXIT_OK


def cmd_distance(args: argparse.Namespace, out: Any, err: Any, painter: Painter) -> int:
    if args.lat is None or args.lon is None:
        _write(err, DISTANCE_USAGE)
    latitude = validate_latitude(args.lat)
    longitude = validate_longitude(args.lon)
    hit = nearest_boundary(latitude, longitude)
    if args.json:
        _write(
            out,
            json.dumps(
                {
                    "query": {"lat": latitude, "lon": longitude},
                    "boundary": hit.name,
                    "distance_km": round(hit.distance_km, 1),
                    "nearest_vertex": {"lat": hit.vertex_lat, "lon": hit.vertex_lon},
                },
                ensure_ascii=False,
            ),
        )
        return EXIT_OK
    for line in attribute_block(
        [
            ("Nearest boundary", painter.accent(hit.name)),
            ("Great-circle away", "%.1f km" % hit.distance_km),
            (
                "Nearest vertex",
                "%s %s"
                % (
                    format_degrees(hit.vertex_lat, "N", "S"),
                    format_degrees(hit.vertex_lon, "E", "W"),
                ),
            ),
            (
                "Query point",
                "%s %s"
                % (
                    format_degrees(latitude, "N", "S"),
                    format_degrees(longitude, "E", "W"),
                ),
            ),
        ]
    ):
        _write(out, line)
    _write(
        out,
        painter.dim(
            "bundled boundary table: %d named boundaries, nearest-vertex haversine"
            % len(BOUNDARIES)
        ),
    )
    return EXIT_OK


def cmd_chart(args: argparse.Namespace, out: Any, err: Any, painter: Painter) -> int:
    path = store_path()
    events = read_events(path)
    if not events:
        if args.json:
            _write(
                out,
                json.dumps(
                    {"store": str(path), "count": 0, "rows": []}, ensure_ascii=False
                ),
            )
            return EXIT_OK
        _write(out, "No events to chart yet. Try: %s record -m 4.5 -d 10" % PROG)
        return EXIT_OK

    lowest = min(event.magnitude for event in events)
    highest = max(event.magnitude for event in events)
    if args.json:
        _write(
            out,
            json.dumps(
                {
                    "store": str(path),
                    "count": len(events),
                    "min_magnitude": lowest,
                    "max_magnitude": highest,
                    "rows": [
                        {
                            "id": event.id,
                            "date": event.time[:10],
                            "magnitude": event.magnitude,
                            "depth_km": event.depth_km,
                            "place": event.place,
                            "bar": chart_bar(event.magnitude, lowest, highest).rstrip(
                                GLYPHS.track
                            ),
                        }
                        for event in events
                    ],
                },
                ensure_ascii=False,
            ),
        )
        return EXIT_OK

    if len(events) == 1:
        title = "Magnitude history %s 1 event at %.1f" % (GLYPHS.dash, highest)
    else:
        title = "Magnitude history %s %d events, %.1f to %.1f" % (
            GLYPHS.dash,
            len(events),
            lowest,
            highest,
        )
    _write(out, title)

    rows = [
        [
            str(event.id),
            event.time[:10],
            "%.1f" % event.magnitude,
            truncate(event.place, CHART_PLACE_WIDTH),
            chart_bar(event.magnitude, lowest, highest),
        ]
        for event in events
    ]
    for line in render_table(
        ("id", "date", "mag", "place", "bar"), rows, ("r", "l", "r", "l", "l")
    ):
        _write(out, line)

    if len(events) == 1:
        _write(out, "axis  %.1f (collapsed %s one event)" % (highest, GLYPHS.dash))
    else:
        _write(
            out,
            "axis  %.1f %s%s  %.1f"
            % (lowest, GLYPHS.rule * BAR_WIDTH, GLYPHS.arrow, highest),
        )
    _write(
        out,
        painter.dim(
            "bar length is proportional to magnitude, scaled to this store's range"
        ),
    )
    return EXIT_OK


# Literal data table kept compact on purpose: one sample event per line.
# fmt: off
SAMPLE_EVENTS: Tuple[Tuple[str, float, float, str], ...] = (
    ("2024-08-06T12:09:00Z", 5.2, 3.0, "Bakersfield, California"),
    ("2024-04-05T14:23:00Z", 4.8, 5.0, "Whitehouse Station, New Jersey"),
    ("2023-02-06T01:17:00Z", 7.8, 10.0, "Kahramanmaras, Turkey"),
    ("2022-09-19T18:05:00Z", 7.6, 25.0, "Michoacan, Mexico"),
    ("2021-02-13T14:07:00Z", 7.1, 44.0, "Fukushima, Japan"),
    ("2020-01-24T03:55:00Z", 5.0, 6.4, "Elazig, Turkey"),
    ("2019-11-26T10:54:00Z", 6.4, 20.0, "Durres, Albania"),
    ("2019-07-04T17:33:00Z", 6.4, 10.7, "Ridgecrest, California"),
    ("2018-09-28T10:02:00Z", 7.5, 20.0, "Palu, Indonesia"),
    ("2017-09-19T18:14:00Z", 7.1, 48.0, "Puebla, Mexico"),
    ("2016-11-13T11:02:00Z", 7.8, 15.1, "Kaikoura, New Zealand"),
    ("2015-04-25T06:11:00Z", 7.8, 8.2, "Gorkha, Nepal"),
    ("2014-04-01T23:46:00Z", 8.2, 25.0, "Iquique, Chile"),
    ("2011-03-11T05:46:00Z", 9.1, 29.0, "Tohoku, Japan"),
    ("2010-01-12T21:53:00Z", 7.0, 13.0, "Port-au-Prince, Haiti"),
    ("2004-12-26T00:58:00Z", 9.1, 30.0, "Sumatra, Indonesia"),
    ("2025-01-07T01:05:00Z", 7.1, 10.0, "Tibet, China"),
    ("2025-03-28T06:20:00Z", 7.7, 10.0, "Mandalay, Myanmar"),
)
# fmt: on


def cmd_seed(args: argparse.Namespace, out: Any, err: Any, painter: Painter) -> int:
    """Load the bundled sample sequence; idempotent unless --force is passed."""
    path = store_path()
    events = read_events(path)
    if events and not args.force:
        _write(
            out,
            "Store already holds %d event%s; nothing seeded."
            % (len(events), "" if len(events) == 1 else "s"),
        )
        _write(out, painter.dim("run with --force to append the sample sequence again"))
        return EXIT_OK
    if events:
        _write(
            out,
            painter.dim(
                "store already holds %d events; appending the sample sequence"
                % len(events)
            ),
        )

    next_id = next_event_id(events)
    for moment, magnitude, depth_km, place in SAMPLE_EVENTS:
        event = Event(
            id=next_id,
            time=format_iso_utc(parse_iso_utc(moment)),
            magnitude=magnitude,
            depth_km=depth_km,
            place=place,
        )
        append_event(path, event)
        next_id += 1

    stored = read_events(path)
    _write(
        out,
        "Seeded %d sample events %s %d event%s now stored"
        % (
            len(SAMPLE_EVENTS),
            GLYPHS.dash,
            len(stored),
            "" if len(stored) == 1 else "s",
        ),
    )
    _write(out, "store: %s" % path)
    _write(
        out,
        painter.dim(
            "next: %s list   |   %s chart   |   %s distance --lat 0 --lon -30"
            % (PROG, PROG, PROG)
        ),
    )
    return EXIT_OK


# --------------------------------------------------------------------------- #
# Command line
# --------------------------------------------------------------------------- #
EXAMPLES = """\
examples:
  %(prog)s record -m 5.2 -d 3.0 -p "Bakersfield, California"
  %(prog)s record -m 4.5 -d 10 --time 2024-03-01T12:00:00Z
  %(prog)s list
  %(prog)s distance --lat 0 --lon -30
  %(prog)s chart

The store lives at $HOME/.seismolog/events.jsonl and is created on first record;
set $SEISMOLOG_STORE to keep it somewhere else. No network, no data files.
""" % {"prog": PROG}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Record seismic events, measure distance to tectonic plate "
        "boundaries and compare magnitudes over time.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version="%s %s" % (PROG, __version__)
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="plain output (also honoured via the NO_COLOR environment variable)",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    record = subparsers.add_parser(
        "record",
        help="record one seismic event",
        description="Validate and append one event, then echo exactly what was stored.",
        epilog='example: %s record -m 4.5 -d 10 -p "Test Ridge"' % PROG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    record.add_argument(
        "-m",
        "--magnitude",
        metavar="M",
        default=None,
        help="required; magnitude between 0.0 and 10.0",
    )
    record.add_argument(
        "-d",
        "--depth",
        metavar="KM",
        default=None,
        help="required; hypocentre depth in kilometres (>= 0)",
    )
    record.add_argument(
        "-p",
        "--place",
        metavar="PLACE",
        default=None,
        help="optional place description (default: unknown)",
    )
    record.add_argument(
        "--time",
        metavar="ISO8601",
        default=None,
        help="optional event time (default: the current UTC time)",
    )
    record.add_argument(
        "--json", action="store_true", help="machine-readable result on stdout"
    )
    record.set_defaults(handler=cmd_record)

    listing = subparsers.add_parser(
        "list",
        help="print every stored event, oldest first",
        description="Print one row per stored event with its id, ordered by time.",
    )
    listing.add_argument(
        "--json", action="store_true", help="machine-readable result on stdout"
    )
    listing.set_defaults(handler=cmd_list)

    distance = subparsers.add_parser(
        "distance",
        help="distance from a point to the nearest plate boundary",
        description="Great-circle distance to the nearest bundled plate boundary vertex.",
        epilog="example: %s distance --lat 37.8 --lon -122.4" % PROG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    distance.add_argument(
        "--lat",
        metavar="DEG",
        default=None,
        help="required; latitude between -90 and 90",
    )
    distance.add_argument(
        "--lon",
        metavar="DEG",
        default=None,
        help="required; longitude between -180 and 180",
    )
    distance.add_argument(
        "--json", action="store_true", help="machine-readable result on stdout"
    )
    distance.set_defaults(handler=cmd_distance)

    chart = subparsers.add_parser(
        "chart",
        help="ASCII magnitude comparison of the stored events",
        description="One bar per event, scaled to the store's own magnitude range.",
    )
    chart.add_argument(
        "--json", action="store_true", help="machine-readable result on stdout"
    )
    chart.set_defaults(handler=cmd_chart)

    seed = subparsers.add_parser(
        "seed",
        help="load the bundled sample event sequence (idempotent)",
        description="Append a realistic sample sequence so every view has content.",
    )
    seed.add_argument(
        "--force",
        action="store_true",
        help="append the sample sequence even when the store already holds events",
    )
    seed.set_defaults(handler=cmd_seed)
    return parser


def _write(stream: Any, text: str) -> None:
    stream.write(text + "\n")


def report_error(err: Any, painter: Painter, error: SeismologError) -> int:
    _write(err, "%s %s" % (painter.accent("error:"), error.message))
    if error.hint:
        _write(err, "hint: %s" % error.hint)
    return error.exit_code


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    painter = Painter(
        enabled=(
            not args.no_color
            and not os.getenv("NO_COLOR")
            and getattr(sys.stdout, "isatty", lambda: False)()
        )
    )
    handler = getattr(args, "handler", None)
    if handler is None:
        # A bare invocation is a request for the usage screen, not an error.
        parser.print_help(sys.stdout)
        return EXIT_OK
    try:
        return handler(args, sys.stdout, sys.stderr, painter)
    except SeismologError as error:
        return report_error(sys.stderr, painter, error)
    except BrokenPipeError:  # `seismolog list | head` -- exit quietly, no traceback
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except OSError:
            pass
        return EXIT_OK
    except KeyboardInterrupt:
        _write(sys.stderr, "interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
