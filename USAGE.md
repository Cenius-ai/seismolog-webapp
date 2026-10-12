# Using Seismolog

Every command below is copy-pasteable from the project root. `python3 seismolog.py`
can be shortened to `seismolog` once `install.sh` has run.

## 1. Record an event

```bash
python3 seismolog.py record -m 6.1 -d 14.5 -p "Hualien, Taiwan" --time 2024-04-02T23:58:00Z
```

```
Recorded event #3
────────────────────
  id:        3
  time:      2024-04-02T23:58:00Z
  magnitude: 6.1
  depth km:  14.5
  place:     Hualien, Taiwan
store: /home/you/.seismolog/events.jsonl
```

* `-m/--magnitude` is required, on the 0.0–10.0 scale.
* `-d/--depth` is required, in kilometres, and must not be negative.
* `-p/--place` is optional and stored verbatim; omitted, it becomes `unknown`.
* `--time` is optional ISO-8601. A bare date (`2024-04-02`), a `Z` stamp and an
  offset stamp (`2024-04-02T23:58:00+08:00`) are all accepted and normalised to UTC.
  Omitted, the event gets the current UTC time.
* The id is assigned at write time: one greater than the highest id already stored.

The echoed block always matches the line written to the store. Any validation
failure prints one line to stderr, writes nothing and exits `2`:

```bash
$ python3 seismolog.py record -m 12 -d 10
error: magnitude must be between 0 and 10 (got 12)
$ echo $?
2
```

### Scripting it

```bash
python3 seismolog.py record --json -m 4.9 -d 8
{"stored": {"id": 4, "time": "2026-10-12T21:19:51Z", "magnitude": 4.9, "depth_km": 8.0, "place": "unknown"}, "store": "/home/you/.seismolog/events.jsonl"}
```

## 2. List what you have recorded

```bash
python3 seismolog.py list
```

```
Events 3 — oldest first, time ascending
id  time                  mag  depth km  place
──  ────────────────────  ───  ────────  ──────────────────────
 2  2019-07-04T17:33:00Z  6.9       8.0  Ridgecrest, California
 3  2023-11-12T07:41:00Z  5.4      11.0  Zagreb, Croatia
 1  2024-03-01T09:12:00Z  4.3       6.0  Ridgecrest, California
```

These three were recorded in the order 2024-03-01, 2019-07-04, 2023-11-12, so the
ids read 1, 2, 3 down the recording order while the rows come back oldest first.

Rows are ordered by event time, **not** by the order you recorded them — that is
why the id column is independent of the row order. Each row stays inside 80
columns no matter how long the place is (long places are truncated with `…`).

An empty store is a normal state, not an error:

```
$ python3 seismolog.py list
No events recorded yet. Try: seismolog record -m 4.5 -d 10
$ echo $?
0
```

## 3. Distance to the nearest plate boundary

```bash
python3 seismolog.py distance --lat 0 --lon -30
```

```
Nearest boundary:  Mid-Atlantic Ridge
Great-circle away: 222.4 km
Nearest vertex:    0.0°N 28.0°W
Query point:       0.0°N 30.0°W
bundled boundary table: 15 named boundaries, nearest-vertex haversine
```

The boundary table ships inside `seismolog.py` (15 named boundaries — Mid-Atlantic
Ridge, East Pacific Rise, San Andreas, Cascadia, Aleutian Arc, Japan Trench, Ryukyu
Trench, Mariana, Peru-Chile, Himalayan Collision Zone, East African Rift, Alpine
Fault, Southeast Indian Ridge, Red Sea Rift, Carlsberg Ridge), so this works with no data file and
no network. The distance is a haversine great-circle distance to the nearest vertex,
which is the coarse-but-honest answer for an MVP.

Latitude must be −90…90 and longitude −180…180; anything else (or a non-numeric
value) prints a specific message and exits `2`.

```bash
$ python3 seismolog.py distance --lat 95 --lon 0
error: latitude must be between -90 and 90 (got 95)
```

## 4. Compare magnitudes over time

```bash
python3 seismolog.py chart
```

```
Magnitude history — 5 events, 3.0 to 7.2
id  date        mag  place            bar
──  ──────────  ───  ───────────────  ────────────────────────────────
 1  2020-01-02  3.0  Micro, Oklahoma  █░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
 2  2020-02-03  4.6  Felt, Italy      ████████████░░░░░░░░░░░░░░░░░░░░
 3  2020-03-04  5.4  Moderate, Chile  ██████████████████░░░░░░░░░░░░░░
 4  2020-04-05  6.1  Strong, Japan    ████████████████████████░░░░░░░░
 5  2020-05-06  7.2  Major, Alaska    ████████████████████████████████
axis  3.0 ────────────────────────────────►  7.2
bar length is proportional to magnitude, scaled to this store's range
```

The axis labels come from the store's own minimum and maximum, so the chart is
useful for any dataset. One stored event renders one full-length bar with a
collapsed axis; an empty store prints an invitation.

## 5. Sample data

```bash
python3 seismolog.py seed           # idempotent: adds 18 real events once
python3 seismolog.py seed --force   # append the sequence again
```

## Recipes

```bash
# Keep a fieldwork log next to the data you are processing
SEISMOLOG_STORE=./fieldwork/events.jsonl python3 seismolog.py record -m 3.2 -d 6 -p "Site B"

# Everything recorded on 2024-04-02 (JSON, read with jq)
python3 seismolog.py list --json | jq '.events[] | select(.time | startswith("2024-04-02"))'

# Which boundary is nearest to each stored event? (events carry no coordinates,
# so pass the point you care about explicitly)
python3 seismolog.py distance --lat 35.68 --lon 139.69 --json

# Plain output for logs and captured sessions
NO_COLOR=1 python3 seismolog.py chart > history.txt
```

## Exit codes and streams

| Code | Meaning |
| --- | --- |
| `0` | success (including the empty-store invitations) |
| `2` | bad input: out-of-range or non-numeric argument, unparseable `--time`, missing required flag |
| `3` | store problem: unreadable, corrupt, or unwritable store path |

Results go to **stdout**; errors and hints go to **stderr**. `--json` on `record`,
`list`, `distance` and `chart` prints one stable JSON document on stdout.
