# Installing Seismolog

Seismolog is one Python file plus a test suite. Nothing is compiled, nothing is
served and nothing runs in the background.

## Prerequisites (install these with your system package manager, not this project)

* CPython **3.9 or newer** — check with `python3 --version`.
* `pip` for that interpreter — check with `python3 -m pip --version`.
* No compilers, headers or system libraries are needed: the tool imports only
  `argparse`, `json`, `math`, `os` and `sys`, all from the standard library.

## Step 1 — run the setup script (deps → entry point → sample data, then exits)

```bash
bash install.sh
```

It runs, in order:

1. `python3 -m pip install --upgrade pip setuptools wheel`
2. `python3 -m pip install -r requirements.txt` (the only declared dependency is
   `pytest`, used by the test suite)
3. `python3 -m pip install -e .` — provides the `seismolog` command
4. `python3 -c "import seismolog"` — fails loudly if anything above was incomplete
5. `python3 seismolog.py seed` — idempotent sample data

It never calls `sudo`, never installs system packages, never starts a server, and
returns to your shell when it is done.

### Manual equivalent

```bash
python3 -m pip install --upgrade pip setuptools wheel
python3 -m pip install -r requirements.txt
python3 -m pip install -e .
python3 seismolog.py seed
```

The tool works without step 3 as well — `python3 seismolog.py <command>` and
`python3 -m seismolog <command>` both run the same code from the project root.

## Step 2 — load sample data (already done by install.sh; safe to re-run)

```bash
python3 seismolog.py seed          # no-op when the store already holds events
python3 seismolog.py seed --force  # append the sample sequence again
```

## Step 3 — run the tool

```bash
python3 seismolog.py list
python3 seismolog.py chart
python3 seismolog.py distance --lat 0 --lon -30
```

Nothing is started in the foreground and no port is bound: every command prints
its result and exits, so it is safe in scripts, cron and CI.

## Step 4 — run the test suite

```bash
python3 -m pytest
```

## Step 5 (optional) — the recorded walkthrough

```bash
bash demo.sh
```

It works in a throwaway store under `$TMPDIR` and removes it afterwards, so your
real `$HOME/.seismolog/events.jsonl` is untouched.

## Optional quality tooling (not required to run anything)

The code was built and checked with `ruff==0.14.4` (`ruff check .`) and
`black==26.10.1` (`black --check seismolog.py tests/`). Neither is imported by the
tool or the test suite, so neither is a declared dependency:

```bash
python3 -m pip install ruff==0.14.4 black==26.10.1   # optional
ruff check . && black --check seismolog.py tests/
```

## Configuration

Neither the tool nor the test suite requires any environment variable.
`.env.example` lists the two optional ones; export them in your shell if you want
them (Seismolog does not read `.env` files itself):

| Variable | Default | Purpose |
| --- | --- | --- |
| `SEISMOLOG_STORE` | `$HOME/.seismolog/events.jsonl` | store path; the parent directory is created on first write |
| `NO_COLOR` | unset | set to any non-empty value to force plain output |

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `error: /path/events.jsonl: line N is not valid JSON` | the store has a malformed line (usually an interrupted hand-edit). Fix or remove that line; earlier lines are intact and are never rewritten. |
| `No events recorded yet.` | the store does not exist yet — that is the normal first-run state. Run `python3 seismolog.py seed` or record an event. |
| `seismolog: command not found` | the editable install was skipped; use `python3 seismolog.py <command>` from the project root. |
| `ModuleNotFoundError: No module named 'pytest'` | `python3 -m pip install -r requirements.txt` — pytest is only needed for the test suite, not for the tool. |
| `error: magnitude must be between 0 and 10` | the recorded magnitude is outside the 0.0–10.0 scale Seismolog stores. Nothing was written. |
