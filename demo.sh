#!/usr/bin/env bash
# Seismolog end-to-end demo: non-interactive, offline, safe to re-run.
#
# It works in a throwaway store under $TMPDIR so your real event log at
# $HOME/.seismolog/events.jsonl is never touched, and every command is invoked
# through the interpreter (no reliance on executable bits or on $PATH).
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
SEIS=("$PY" seismolog.py)

DEMO_DIR="$(mktemp -d "${TMPDIR:-/tmp}/seismolog-demo.XXXXXX")"
trap 'rm -rf "$DEMO_DIR"' EXIT
export SEISMOLOG_STORE="$DEMO_DIR/events.jsonl"
export NO_COLOR=1

step() { printf '\n$'; printf ' %q' "$@"; printf '\n'; }
run() { step "$*"; "$@"; }
fail() {
  step "$*"
  set +e
  "$@"
  local code=$?
  set -e
  printf '[exit %d]\n' "$code"
}

printf 'Seismolog demo\n'
printf 'store: %s (temporary; removed when the demo ends)\n' "$SEISMOLOG_STORE"

run "${SEIS[@]}" --version
run "${SEIS[@]}" --help

printf '\n--- 1. an empty store invites the first record -----------------------\n'
run "${SEIS[@]}" list
run "${SEIS[@]}" chart

printf '\n--- 2. load the bundled sample sequence -----------------------------\n'
run "${SEIS[@]}" seed
run "${SEIS[@]}" seed

printf '\n--- 3. record a new event (id, time, magnitude, depth, place) -------\n'
run "${SEIS[@]}" record -m 6.1 -d 14.5 -p "Hualien, Taiwan" --time 2024-04-02T23:58:00Z
run "${SEIS[@]}" record -m 4.9 -d 8
run "${SEIS[@]}" record --json -m 3.4 -d 2.1 -p "Micro event, Oklahoma"

printf '\n--- 4. list every event, oldest first -------------------------------\n'
run "${SEIS[@]}" list

printf '\n--- 5. compare magnitudes over time --------------------------------\n'
run "${SEIS[@]}" chart

printf '\n--- 6. how far from the nearest plate boundary? --------------------\n'
run "${SEIS[@]}" distance --lat 23.99 --lon 121.61
run "${SEIS[@]}" distance --lat 0 --lon -30
run "${SEIS[@]}" distance --lat 40 --lon -100
run "${SEIS[@]}" distance --lat 0 --lon 0

printf '\n--- 7. validation failures exit non-zero and write nothing ---------\n'
fail "${SEIS[@]}" record -m 12 -d 10
fail "${SEIS[@]}" record -m 5 -d -3
fail "${SEIS[@]}" record -m 5 -d 10 --time yesterday
fail "${SEIS[@]}" record
fail "${SEIS[@]}" distance --lat 95 --lon 0
fail "${SEIS[@]}" distance --lat 0 --lon 200

printf '\n--- 8. a corrupt store is reported, never silently skipped ---------\n'
export SEISMOLOG_STORE="$DEMO_DIR/corrupt.jsonl"
printf '{"id": 1, "time": "2024-01-01T00:00:00Z", "magnitude": 4.0, "depth_km": 5.0, "place": "intact"}\n{"id": 2, "time": "2024-02-0\n' > "$SEISMOLOG_STORE"
fail "${SEIS[@]}" list
fail "${SEIS[@]}" chart
export SEISMOLOG_STORE="$DEMO_DIR/events.jsonl"

printf '\n--- 9. scripting: --json on stdout, errors on stderr -----------------\n'
run "${SEIS[@]}" list --json
run "${SEIS[@]}" distance --lat 35.68 --lon 139.69 --json

printf '\nDemo complete: record, list, distance and chart all ran from one file.\n'
