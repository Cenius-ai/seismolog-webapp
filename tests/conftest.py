"""Shared fixtures: exercise the real command line against a throwaway store.

Every test runs the shipped ``seismolog.py`` in a subprocess with $SEISMOLOG_STORE
and $HOME pointed at a temporary directory, so no test can read or write the
developer's own event log and no test needs network access.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional, Sequence

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "seismolog.py"


class CliResult:
    """Captured outcome of one CLI invocation."""

    def __init__(self, completed: subprocess.CompletedProcess) -> None:
        self.code = completed.returncode
        self.out = completed.stdout
        self.err = completed.stderr

    def __repr__(self) -> str:  # pragma: no cover - test diagnostics only
        return "CliResult(code=%r, out=%r, err=%r)" % (self.code, self.out, self.err)


@pytest.fixture()
def store(tmp_path: Path) -> Path:
    """The event store every test writes to unless it asks for another one."""
    return tmp_path / "store" / "events.jsonl"


@pytest.fixture()
def home(tmp_path: Path) -> Path:
    """A throwaway $HOME so the default store path is never the developer's."""
    path = tmp_path / "home"
    path.mkdir(exist_ok=True)
    return path


@pytest.fixture()
def run(store: Path, home: Path):
    """Call the CLI: ``run("list")`` -> CliResult."""

    def _run(
        *args: str,
        store_path: Optional[Path] = None,
        home_dir: Optional[Path] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> CliResult:
        environment = os.environ.copy()
        environment.pop("SEISMOLOG_STORE", None)
        target = store if store_path is None else store_path
        if target is not None:
            environment["SEISMOLOG_STORE"] = str(target)
        environment["HOME"] = str(home if home_dir is None else home_dir)
        environment["NO_COLOR"] = "1"
        environment["PYTHONIOENCODING"] = "utf-8"
        environment.update(env or {})
        completed = subprocess.run(
            [sys.executable, str(CLI), *args],
            capture_output=True,
            text=True,
            env=environment,
            timeout=120,
        )
        return CliResult(completed)

    return _run


def record_args(
    magnitude: float,
    depth: float,
    place: Optional[str] = None,
    moment: Optional[str] = None,
) -> Sequence[str]:
    """Build a ``record`` command line the same way a user would type it."""
    args = ["record", "-m", str(magnitude), "-d", str(depth)]
    if place is not None:
        args += ["-p", place]
    if moment is not None:
        args += ["--time", moment]
    return args
