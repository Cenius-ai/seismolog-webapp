#!/usr/bin/env bash
# Seismolog setup: project-level dependencies only, then it EXITS.
#
# It never uses sudo, never touches system packages and never starts a server --
# Seismolog is a command-line tool, so there is nothing to serve. Run the tool
# itself afterwards with `python3 seismolog.py <command>` (or `bash demo.sh`).
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"

echo "==> upgrading pip, setuptools and wheel (old pip breaks editable installs)"
"$PYTHON" -m pip install --upgrade pip setuptools wheel

echo "==> installing the declared dependencies (pytest, for the test suite)"
"$PYTHON" -m pip install -r requirements.txt

echo "==> installing the 'seismolog' console entry point"
if "$PYTHON" -m pip install -e . ; then
  echo "    entry point installed: run 'seismolog --help'"
else
  echo "    WARNING: the editable install did not complete." >&2
  echo "    The tool is unaffected: use 'python3 seismolog.py' or 'python3 -m seismolog'." >&2
fi

echo "==> checking that the module imports cleanly"
"$PYTHON" -c "import seismolog; print('    seismolog', seismolog.__version__)"

echo "==> loading the sample event sequence into the store (idempotent)"
"$PYTHON" seismolog.py seed

echo
echo "Setup complete -- nothing is running."
echo "  store : ${SEISMOLOG_STORE:-$HOME/.seismolog/events.jsonl}"
echo "Next:"
echo "  \$ $PYTHON seismolog.py list"
echo "  \$ $PYTHON seismolog.py chart"
echo "  \$ bash demo.sh          # recorded end-to-end walkthrough"
echo "  \$ $PYTHON -m pytest     # test suite"
