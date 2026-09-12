"""Put the script's source directory on sys.path so tests can import `zulip_build_report`,
and provide helpers that build `lake build --json` lines the way Lake emits them."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import pytest

_SCRIPT_DIR = Path(__file__).resolve().parents[2] / "scripts" / "reporting"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

sys.path.insert(0, str(_SCRIPT_DIR))


def entry(
    level: str,
    file: Optional[str] = None,
    line: Optional[int] = None,
    col: Optional[int] = None,
    text: str = "",
    kind: Optional[str] = None,
    target: str = "Mathlib.A",
) -> str:
    """One line of `lake build --json` output.

    With `file`, the entry is shaped like a Lean message: `fileName`, `pos`, `endPos`,
    `data` (the body), and `message` (the body with the position prefix). Without it,
    the entry is one Lake produced itself and carries only `target`, `level`, `message`.
    """
    obj = {"target": target, "level": level}
    if file is not None:
        obj["fileName"] = file
        obj["pos"] = {"line": line, "column": col}
        obj["endPos"] = {"line": line, "column": col + 1}
        obj["data"] = text
        obj["message"] = f"{file}:{line}:{col}: {text}"
    else:
        obj["message"] = text
    if kind is not None:
        obj["kind"] = kind
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def ndjson(*entries: str) -> str:
    """Join entries into the newline-delimited stream `main` reads."""
    return "".join(e + "\n" for e in entries)


@pytest.fixture
def mathlib_log() -> Path:
    """The messages of mathlib4 weekly run 33358987484, as `--json` entries: 6 warnings, 2 info.

    Covers docStringVerso notes (tagged with `kind`), single- and multi-line `grind?`
    failures (untagged), `Try this` info blocks, and a message containing `|`.
    """
    return FIXTURES / "mathlib_weekly.ndjson"


@pytest.fixture
def cslib_log() -> Path:
    """The messages of cslib weekly run 33359064105 as `--json` entries (3 info messages)."""
    return FIXTURES / "cslib_weekly.ndjson"


@pytest.fixture
def failed_log() -> Path:
    """Synthetic failed build: a failed job's trace-level replay, errors (one a named
    error with a non-linter `kind`), Lake's `Lean exited` entry, and a stderr panic block."""
    return FIXTURES / "failed_build.ndjson"
