"""The Mac's conformance facts in the lane's container (ADR-0059 Amendment 13, D-62 C).

`factory decompose` derives a bump unit's conformance claim from project-standards' compliance
matrix, which reads three inputs only the Mac has. The container gets code-standards from a clone
(the launcher's), and the other two from the newest `mac_conformance.reported` event in the
factory-events chain: the infra-drift report's ids and statuses, and the Mac's LaunchAgent and
Claude hook wiring, present or absent.

The launcher walks the chain (`orchestrator.factory_chain_read`, which picks the newest event by
its `timestamp`, never its `seq`) and hands the pass one file, `--conformance-facts`: whether the
walk verified, its summary, and the selection. This module judges it at the pass's time and
writes the facts where the matrix reads them, under the pass's `HOME`, so project-standards runs
unchanged:

- `$HOME/infra-drift/reports/<generated_at's UTC date>.json`, the report: each instance's `ok` and
  each proposal's id and target UUID and name;
- `$HOME/Library/LaunchAgents/<label>.plist`, an empty marker for each present LaunchAgent;
- `$HOME/.claude/settings.json`, one `Stop` hook command per present hook name.

A member's claim reads the event when it declares the `infra` standard, or a `launchagent:` or
`hook:` required check, in its mirror's `PROJECT.md`. Such a member's bump is held, proposing no
decomposition, when the facts are unusable: the chain didn't verify, there is no usable event, the
report was unreadable, an instance isn't ok, or the report's `generated_at` is older than
`MAX_REPORT_AGE_HOURS`. It is also held when it declares a wiring entry the event doesn't list,
which the container would otherwise read as not wired.

On the Mac the launcher passes no file, so this module reads, holds and writes nothing there.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import yaml

# project-standards' `INFRA_MAX_AGE_H`: the matrix calls a report older than this stale.
MATRIX_MAX_AGE_HOURS: Final = 36
# The driver's bound, an hour inside the matrix's. The matrix measures the report's age again when
# the generator runs, after this check, so a report at the matrix's exact bound could pass here
# and read stale there, giving a claim that isn't green. A plain int, with no off value.
MAX_REPORT_AGE_HOURS: Final = 35
WIRING_KINDS: Final = ("launchagent", "hook")
PRESENT: Final = "present"
_FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)


@dataclass(frozen=True)
class Declared:
    """What a member's claim reads from the event: the `infra` cell, and its wiring entries."""

    infra: bool
    wiring: tuple[str, ...]

    @property
    def reads_event(self) -> bool:
        return self.infra or bool(self.wiring)


def declared(checkout: Path) -> Declared:
    """Read from the mirror's `PROJECT.md` frontmatter as project-standards' contract reads it. A
    member with no readable declaration reads nothing from the event: its claim isn't green
    whatever the event says, on the Mac as here."""
    try:
        match = _FRONTMATTER.match((checkout / "PROJECT.md").read_text(encoding="utf-8"))
        front = yaml.safe_load(match.group(1)) if match else None
    except OSError, UnicodeDecodeError, yaml.YAMLError:
        # Deliberate: the matrix reads such a member's every cell as `unknown`, on the Mac as
        # here, whatever the event says, so holding it for the event would change nothing. The
        # generator refuses or proposes it as it would on the Mac.
        return Declared(False, ())
    if not isinstance(front, dict):
        return Declared(False, ())
    standards = front.get("applicable_standards")
    infra = isinstance(standards, (list, dict)) and "infra" in standards
    checks = front.get("required_checks")
    wiring = tuple(
        entry["executor"]
        for entry in (checks if isinstance(checks, list) else [])
        if isinstance(entry, dict)
        and isinstance(entry.get("executor"), str)
        and entry["executor"].partition(":")[0] in WIRING_KINDS
        and entry["executor"].partition(":")[2]
    )
    return Declared(infra, wiring)


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


@dataclass(frozen=True)
class Facts:
    """One pass's reading of the chain. `event` is the newest event's ids and statuses (the
    selection's `event`), or `None` with `reason` saying why there is none."""

    event: dict[str, Any] | None
    reason: str
    clock: Callable[[], datetime]

    def unusable(self) -> str | None:
        """Why no bump that reads the event may go on, judged now; `None` when it may."""
        event = self.event
        if event is None:
            return self.reason
        if event["unreadable"] is not None:
            return f"the Mac's newest infra-drift report was unreadable ({event['unreadable']})"
        failed = sorted(name for name, ok in event["instances"].items() if not ok)
        if failed or event["result"] != "success":
            names = ", ".join(failed) or "the result"
            return f"the Mac's infra-drift audit is incomplete: {names} not ok"
        age = self.clock() - _utc(event["generated_at"])
        if age > timedelta(hours=MAX_REPORT_AGE_HOURS):
            return (
                f"the Mac's infra-drift report ({event['generated_at']}) is "
                f"{age.total_seconds() / 3600:.1f} hours old, past {MAX_REPORT_AGE_HOURS}"
            )
        return None

    def unlisted(self, wiring: tuple[str, ...]) -> list[str]:
        """The declared wiring entries the event doesn't list (only asked of a usable event)."""
        listed = self.event["wiring"] if self.event is not None else {}
        return sorted(set(wiring) - set(listed))

    def summary(self) -> str:
        problem = self.unusable()
        if problem is not None or self.event is None:
            return f"unusable: {problem}"
        return f"usable: seq {self.event['seq']}, report {self.event['generated_at']}"


def load(path: Path, clock: Callable[[], datetime]) -> Facts:
    """The launcher's file. A file that can't be read, or a walk that didn't verify, is no event:
    every bump that reads one is held, never passed."""
    try:
        feed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        # Not swallowed: it becomes the hold's reason, which the pass reports and alerts.
        return Facts(None, f"the chain's facts file is unreadable ({type(error).__name__})", clock)
    if not isinstance(feed, dict) or feed.get("verified") is not True:
        summary = feed.get("summary") if isinstance(feed, dict) else None
        return Facts(
            None, f"the factory-events chain did not verify ({summary or 'no record'})", clock
        )
    selection = feed.get("mac_conformance")
    if not isinstance(selection, dict):
        return Facts(None, "the verified walk carried no mac_conformance selection", clock)
    event = selection.get("event")
    if not isinstance(event, dict):
        return Facts(None, str(selection.get("reason") or "no usable event"), clock)
    return Facts(event, "", clock)


def materialize(event: dict[str, Any], home: Path) -> None:
    """Write a usable event's facts where the matrix reads them; the caller asks `unusable` first.
    Each file is created, never overwritten: `HOME` is the pass's new directory, and a file already
    there means it isn't, which raises `FileExistsError`."""
    by_instance: dict[str, list[dict[str, Any]]] = {name: [] for name in event["instances"]}
    for proposal in event["proposals"]:
        by_instance[proposal["instance"]].append(
            {"id": proposal["id"], "target": proposal["target"]}
        )
    report = {
        "generated_at": event["generated_at"],
        "instances": {
            name: {"ok": True, "proposals": proposals} for name, proposals in by_instance.items()
        },
    }
    reports = home / "infra-drift" / "reports"
    _create(reports / f"{_utc(event['generated_at']).date().isoformat()}.json", json.dumps(report))
    present = [name for name, status in event["wiring"].items() if status == PRESENT]
    agents = home / "Library" / "LaunchAgents"
    agents.mkdir(parents=True, exist_ok=True)
    hooks: list[str] = []
    for executor in present:
        kind, _, name = executor.partition(":")
        if kind == "launchagent":
            _create(agents / f"{name}.plist", "")
        else:
            hooks.append(name)
    commands = [{"type": "command", "command": name} for name in hooks]
    settings = {"hooks": {"Stop": [{"hooks": commands}]}}
    _create(home / ".claude" / "settings.json", json.dumps(settings))


def _create(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(text)
