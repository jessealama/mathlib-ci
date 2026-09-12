"""Parse a `lake build --json` log into messages and attribute them to linters.

With `--json`, Lake writes one JSON object per log entry to stdout (progress goes to
stderr). Every object has `target`, `level`, and `message`; entries that came from a
Lean message also carry `kind`, `fileName`, `pos`, `endPos`, and `data` (the body
without the `file:line:col:` prefix). `parse_build_log` turns that stream into
`Message` records; `classify` fills in `Message.linter` from `kind`, which for a linter
message is the linter's option name. Messages without a `linter.*` kind are attributed
to `UNATTRIBUTED`.

This is the only module that knows Lake's output format.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

UNATTRIBUTED = "(not attributed to a linter)"

PANIC_PREFIX = "PANIC at "
LINTER_KIND_PREFIX = "linter."


@dataclass
class Message:
    severity: str
    file: Optional[str]
    line: Optional[int]
    col: Optional[int]
    text: str
    linter: Optional[str] = None

    @property
    def first_line(self) -> str:
        return self.text.split("\n", 1)[0]

    @property
    def panic_lines(self) -> List[str]:
        """The `PANIC at ...` lines in this message.

        A panic during `#eval`-style command evaluation is a positioned info message
        whose body starts with the panic line. A panic that reaches Lean's stderr is
        relayed by Lake as one unpositioned `stderr:` info entry with the panic lines
        after it, so one message can carry several panics.
        """
        if self.severity != "info":
            return []
        return [line for line in self.text.split("\n") if line.startswith(PANIC_PREFIX)]

    @property
    def is_panic(self) -> bool:
        return bool(self.panic_lines)


def parse_build_log(lines: Iterable[str]) -> List[Message]:
    """Read the newline-delimited JSON of `lake build --json` into messages.

    Blank lines are skipped and `trace` entries (Lake's own command lines, replayed
    for failed jobs) are dropped; anything else that is not a JSON object is an error,
    since it means the log was not produced with `--json`.
    """
    messages: List[Message] = []
    for lineno, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError as e:
            raise ValueError(f"line {lineno} is not a JSON log entry (was `lake build --json` used?): {line[:80]}") from e
        if not isinstance(obj, dict) or "level" not in obj or "message" not in obj:
            raise ValueError(f"line {lineno} is not a JSON log entry (was `lake build --json` used?): {line[:80]}")
        if obj["level"] == "trace":
            continue
        pos = obj.get("pos") or {}
        messages.append(Message(
            severity=obj["level"],
            file=obj.get("fileName"),
            line=pos.get("line"),
            col=pos.get("column"),
            # Entries Lake creates itself (stderr relays, `Lean exited with code N`)
            # have no `data`; their `message` has no position prefix to strip.
            text=obj.get("data", obj["message"]),
            linter=obj.get("kind"),
        ))
    return messages


def classify(messages: List[Message]) -> List[Message]:
    """Attribute each message to a linter via its `kind`.

    `kind` is also set for named errors (`lean.unknownIdentifier._namedError`), so only
    a `linter.` kind counts; everything else is `UNATTRIBUTED`.
    """
    for msg in messages:
        if not msg.linter or not msg.linter.startswith(LINTER_KIND_PREFIX):
            msg.linter = UNATTRIBUTED
    return messages


def severity_counts(messages: List[Message]) -> "OrderedDict[str, int]":
    """Counts per severity, in the order the shell script printed them.

    Panics are info messages, but are counted under "Panics" only.
    """
    counts = OrderedDict()  # type: OrderedDict[str, int]
    panics = sum(len(m.panic_lines) for m in messages)
    errors = sum(1 for m in messages if m.severity == "error")
    warnings = sum(1 for m in messages if m.severity == "warning")
    infos = sum(1 for m in messages if m.severity == "info" and not m.is_panic)
    for label, n in (("Panics", panics), ("Errors", errors), ("Warnings", warnings), ("Info messages", infos)):
        if n:
            counts[label] = n
    return counts


def linter_table(messages: List[Message], show_info: bool) -> List[Tuple[str, int, int]]:
    """Per-linter (warnings, infos) counts, largest first, unattributed always last.

    Errors and panics are not linter output and are left out. This ordering is used both
    for the Zulip table and for the order of sections in the job summary.
    """
    counts: Dict[str, List[int]] = {}
    for m in messages:
        if m.severity == "error" or m.is_panic:
            continue
        if m.severity == "info" and not show_info:
            continue
        row = counts.setdefault(m.linter or UNATTRIBUTED, [0, 0])
        row[0 if m.severity == "warning" else 1] += 1
    named = sorted(
        ((name, w, i) for name, (w, i) in counts.items() if name != UNATTRIBUTED),
        key=lambda r: (-(r[1] + r[2]), r[0]),
    )
    unattributed = counts.get(UNATTRIBUTED, [0, 0])
    return named + [(UNATTRIBUTED, unattributed[0], unattributed[1])]
