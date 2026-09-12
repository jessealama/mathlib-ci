"""Tests for parse_build_log / classify in build_report/lake_log.py."""

from __future__ import annotations

import pytest
from conftest import entry, ndjson

from build_report.lake_log import UNATTRIBUTED, Message, classify, parse_build_log


def _parse(*entries):
    return parse_build_log(ndjson(*entries).splitlines())


def test_lean_message_fields():
    msgs = _parse(entry("warning", "Mathlib/A.lean", 12, 3, "something odd", kind="linter.foo"))
    assert msgs == [Message("warning", "Mathlib/A.lean", 12, 3, "something odd", "linter.foo")]


def test_body_comes_from_data_not_message():
    # `message` carries the `file:line:col:` prefix; `data` is the body alone.
    (m,) = _parse(entry("warning", "Mathlib/A.lean", 12, 3, "x"))
    assert m.text == "x"
    assert m.first_line == "x"


def test_lake_entry_without_data_uses_message():
    (m,) = _parse(entry("error", text="Lean exited with code 1"))
    assert m == Message("error", None, None, None, "Lean exited with code 1", None)


def test_trace_entries_are_dropped():
    msgs = _parse(
        entry("trace", text=".> LEAN_PATH=... lean Mathlib/A.lean --json"),
        entry("warning", "Mathlib/A.lean", 1, 0, "w"),
    )
    assert [m.text for m in msgs] == ["w"]


def test_blank_and_crlf_lines():
    # Feed raw lines (as `main` does after `split("\n")`), so `\r` reaches the parser.
    msgs = parse_build_log([entry("warning", "A.lean", 1, 0, "hi") + "\r", "", "\r"])
    assert [m.text for m in msgs] == ["hi"]


def test_multiline_body_is_preserved():
    body = "`grind?` suggestion failed: `grind only [= a,\n  = b]` did not close the goal\n\nNote: x"
    (m,) = _parse(entry("warning", "A.lean", 5, 6, body))
    assert m.text == body
    assert m.first_line == "`grind?` suggestion failed: `grind only [= a,"


def test_non_json_line_is_an_error():
    # Text output on stdout means the workflow ran `lake build` without `--json`.
    with pytest.raises(ValueError, match="line 2"):
        _parse(entry("warning", "A.lean", 1, 0, "w"), "warning: A.lean:1:0: w")


def test_panic_detection():
    (m,) = _parse(entry("info", "Mathlib/A.lean", 1, 0, "PANIC at Lean.Expr.foo Lean/Expr.lean:12:3: boom"))
    assert m.is_panic
    (n,) = _parse(entry("info", "Mathlib/A.lean", 1, 0, "'simp; grind' can be replaced with 'grind'"))
    assert not n.is_panic


def test_classify_uses_kind():
    msgs = classify(_parse(
        entry("warning", "A.lean", 1, 0, "x", kind="linter.style.docStringVerso"),
        entry("info", "A.lean", 2, 0, "y"),
        entry("error", "A.lean", 3, 0, "z", kind="linter.foo"),
    ))
    assert [m.linter for m in msgs] == ["linter.style.docStringVerso", UNATTRIBUTED, "linter.foo"]


def test_named_error_kind_is_not_a_linter():
    (m,) = classify(_parse(entry("error", "A.lean", 1, 0, "Unknown identifier `foo`", kind="lean.unknownIdentifier._namedError")))
    assert m.linter == UNATTRIBUTED


def test_disable_note_alone_does_not_attribute():
    # Attribution comes from `kind`, never from matching the note in the body.
    (m,) = classify(_parse(entry("warning", "A.lean", 1, 0, "x\n\nNote: This linter can be disabled with `set_option linter.x false`")))
    assert m.linter == UNATTRIBUTED


def test_mathlib_fixture_counts(mathlib_log):
    with open(mathlib_log, encoding="utf-8") as f:
        msgs = classify(parse_build_log(f))
    by_sev = {}
    for m in msgs:
        by_sev[m.severity] = by_sev.get(m.severity, 0) + 1
    assert by_sev == {"warning": 6, "info": 2}
    verso = [m for m in msgs if m.linter == "linter.style.docStringVerso"]
    assert len(verso) == 3
    assert all(m.severity == "warning" for m in verso)
    # verifyGrindOnly messages are untagged in this log (pre mathlib4#43399)
    grind = [m for m in msgs if m.first_line.startswith("`grind?` suggestion failed")]
    assert len(grind) == 6 - 3
    assert all(m.linter == UNATTRIBUTED for m in grind)
    assert any(m.text.endswith("\n  = mem_filter]` did not close the goal") for m in grind)
    assert all(m.linter == UNATTRIBUTED for m in msgs if m.severity == "info")


def test_cslib_fixture_counts(cslib_log):
    with open(cslib_log, encoding="utf-8") as f:
        msgs = classify(parse_build_log(f))
    assert [m.severity for m in msgs] == ["info"] * 3
    assert msgs[0].file == "Cslib/Foundations/Semantics/LTS/MapHom.lean"
    assert msgs[0].line == 51


def test_failed_build_fixture(failed_log):
    with open(failed_log, encoding="utf-8") as f:
        msgs = classify(parse_build_log(f))
    # The failed job's trace-level replay of the `lean` command line is not a message.
    assert not any(".> LEAN_PATH" in m.text for m in msgs)
    assert [m.severity for m in msgs].count("error") == 3
    assert [m.text for m in msgs if m.file is None and m.severity == "error"] == ["Lean exited with code 1"]


def test_panic_lines_extracted_from_stderr_block(failed_log):
    with open(failed_log, encoding="utf-8") as f:
        msgs = classify(parse_build_log(f))
    (stderr_msg,) = [m for m in msgs if m.is_panic]
    assert stderr_msg.file is None
    assert stderr_msg.panic_lines == [
        "PANIC at Lean.Expr.bindingBody! Lean.Expr:1247:14: binding expected",
        "PANIC at Lean.Meta.whnf Lean.Meta.WHNF:80:2: unreachable",
    ]


def test_classify_fixture_linters(failed_log):
    with open(failed_log, encoding="utf-8") as f:
        msgs = classify(parse_build_log(f))
    assert [m.linter for m in msgs if m.severity == "warning"] == [
        "linter.tacticAnalysis.verifyGrindOnly",
        "linter.haveLet",
    ]
    # Mathlib's `logLint0Disable` variant used to need a `(?:false|0)` alternation
    # in a regex; with `kind` on the entry the note's wording no longer matters.
    assert [m.linter for m in msgs if m.severity == "error"] == [UNATTRIBUTED] * 3
