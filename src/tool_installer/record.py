"""The installation record: what one binary on this machine may honestly assert about itself.

TWO RULES SHAPE EVERY LINE BELOW, AND THEY ARE THE LANDING LEDGER'S, BY WAY OF THE ACTIVATION
SWEEP AND THE PIN WATCHER.

**A record must not assert a condition nobody checked.** This lane reads cargo's own record of
what it built, asks GitHub where the fork's branch is, and -- when it acts -- runs the tool. It
does not know whether any session is using the binary, so nothing here says so.

**Re-running over unchanged reality must change nothing.** `observed_at` is therefore the
COMMITTER DATE OF THE INSTALLED REVISION, never the moment the pass ran. The orchestrator's replay
check hashes the whole command, `observed_at` included
(`services/observations.py::_fact_identity`), so a wall clock would give unchanged reality a new
fact hash every pass -- and because the source reference would be the same, that reaches the
same-source/different-facts branch and raises `observation_conflict`, permanently, from the second
pass onward. A clock that is a function of the facts is the only one that replays. When the pass
ran is recorded anyway: the orchestrator stamps `received_at` itself, which is a better answer
than anything this program could assert about its own clock.

When nothing is installed, or the installed revision has been force-pushed out of the fork's
history, there is no such date and the AVAILABLE revision's committer date stands in. That is
equally a function of the facts and moves only when the fork's head moves, which is the property
the rule actually requires. The pin watcher makes the same substitution for the same reason.

**THE DIGEST COVERS THE WHOLE COMPOSED RECORD, NOT JUST `facts`.** Content-addressing `facts`
alone is the obvious reading of "content-address the reference" and is a strict subset of what the
orchestrator compares: because the reference is also the idempotency key, the server's first
lookup is by that key, and on a hit it compares the ENTIRE stored command -- `summary`, `status`,
`severity`, `source_url`, `trust_classification`, `subject_type`, `observation_type`, every one
producer-derived and none of them in `facts`
(`services/observations.py::_validate_idempotent_replay`, `::_command_payload`). Rewording one
clause of `summary_of` would otherwise make the next pass an `idempotency_conflict` for a machine
whose tool had not moved, which for a healthy machine is every pass. Two independent adversarial
reviews found this in the activation sweep, and no test could: every test generates both sides
from one version of the producer. Digesting the whole body makes any producer change APPEND,
which is always safe.

**The residual is named rather than implied:** `actor_id` and `actor_role` are in the compared
payload and are derived server-side from the credential, so this program cannot cover them.
Recording under a different credential actor would conflict in the same way, and would need the
same treatment as any other supersession problem -- there is none.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from tool_installer.install import (
    ACTION_INSTALL_FAILED,
    ACTION_INSTALLED,
    ACTION_NOT_PERMITTED,
    ACTION_ROLLED_BACK,
    ProbeResult,
)
from tool_installer.tools import Tool

# ADR-0042's lane, named once for the whole lane rather than once per tool -- the precedent is
# `drift_digest`, `recovery_floor`, `machine_activation` and `pin_watcher`: `source_system` names
# the producing LANE and `subject_reference` names the individual subject. `machine_activation` is
# the near miss and is wrong: it asserts what a working copy WILL EXECUTE at its next start, where
# this asserts what a binary on the machine IS.
SOURCE_SYSTEM = "tool_installer"

# A separate observation type for the reason migrations 0031 and 0032 both gave: none of the
# existing members fits, and reusing a near-miss writes false provenance into rows that have no
# supersession model and no delete route. `activation` is the closest and asserts something
# different; `inventory` asserts only that something was enumerated. This asserts something
# specific and falsifiable -- which revision of a named tool the operator machine is running, and
# whether that is the one the fork's branch holds.
OBSERVATION_TYPE = "tool_revision"

TRUST_CLASSIFICATION = "delivery_system"

# `repo`, keyed by `owner/name`, which is what the landing ledger, the activation sweep and the
# pin watcher already write. None of the four can collide: uniqueness is on
# `(source_system, source_reference)` and the source systems differ.
SUBJECT_TYPE = "repo"

# The machine is running the fork's head, or a pass installed it and proved it. Anything else is
# DEGRADED rather than FAILED while the artifact still works, and FAILED once an install or a
# proof has actually gone wrong. A tool that is merely behind is an ordinary, recoverable state --
# the machine is not running what was merged, which is worth a person's attention and is not a
# failure of anything.
STATUS_CURRENT = "passed"
STATUS_BEHIND = "degraded"
STATUS_FAILED = "failed"
SEVERITY_CURRENT = "info"
SEVERITY_BEHIND = "warning"
SEVERITY_FAILED = "critical"

STATE_CURRENT = "current"
STATE_BEHIND = "behind"
STATE_ABSENT = "absent"

MAX_SUMMARY = 512
# The orchestrator's own bound on the encoded facts (`services/observations.py`).
MAX_FACT_BYTES = 4096

# An action that means something went wrong with the artifact itself, as opposed to one that means
# nothing was attempted. Spelled as a set of the FAILING actions rather than of the healthy ones so
# that an action added later is a finding by default -- the other arrangement silently exempts it,
# which is the direction that fails open.
FAILING_ACTIONS = frozenset({ACTION_ROLLED_BACK, ACTION_INSTALL_FAILED})


@dataclass(frozen=True)
class Installation:
    """One tool's state on this machine, and what this pass did about it."""

    tool: Tool
    install_root: str
    installed_revision: str | None
    installed_version: str | None
    installed_committed_at: str | None
    available_revision: str
    available_committed_at: str
    action: str
    probes: tuple[ProbeResult, ...] = ()
    detail: str = ""

    @property
    def state(self) -> str:
        if self.installed_revision is None:
            return STATE_ABSENT
        return STATE_CURRENT if self.installed_revision == self.available_revision else STATE_BEHIND

    @property
    def is_finding(self) -> bool:
        """Anything a person would want to know about: not current, or something went wrong.

        `absent` counts, and deliberately: a first install that has not happened yet is exactly
        the state this lane exists to end.
        """
        return self.state != STATE_CURRENT or self.action in FAILING_ACTIONS


def installation_facts(installation: Installation) -> dict[str, Any]:
    """Everything the record says, bounded by construction.

    Every value is a repository name, a path, a revision, a version, a state name, an action name
    or a bounded probe line, so the record is small by shape rather than by trimming. No key
    contains a fragment the orchestrator's secret detector reads as metadata
    (`services/observations.py::SECRET_KEY_PARTS` matches nine substrings against key NAMES, so
    a key merely CALLED something like `install_log` would be refused on its name alone).
    """
    facts: dict[str, Any] = {
        "tool": {
            "name": installation.tool.name,
            "repository": installation.tool.repository,
            "branch": installation.tool.branch,
            "install_root": installation.install_root,
        },
        "installed": {
            "revision": installation.installed_revision,
            "version": installation.installed_version,
            # `null` on a first install, and on a revision the fork's history no longer has.
            # Emitted rather than omitted so the key set is one shape for every row and a reader
            # who finds the key can tell "not measured" from "not reported".
            "committed_at": installation.installed_committed_at,
        },
        "available": {
            "revision": installation.available_revision,
            "committed_at": installation.available_committed_at,
        },
        "state": installation.state,
        "action": installation.action,
    }
    if installation.probes:
        facts["probes"] = [result.as_facts() for result in installation.probes]
    if installation.detail:
        facts["detail"] = installation.detail[:MAX_SUMMARY]
    return facts


def summary_of(installation: Installation) -> str:
    """One sentence, computed from the same state and action the facts carry.

    A tool is described as current only when its state IS current -- the guard is structural
    rather than a clause that remembers to check.
    """
    name = installation.tool.name
    short = installation.available_revision[:7]
    if installation.action == ACTION_INSTALLED:
        return (
            f"{name} {installation.installed_version} was installed at {short} on the operator "
            f"machine and passed every probe."
        )
    if installation.action == ACTION_ROLLED_BACK:
        return (
            f"{name} built at {short} but failed its probe, so the previous artifact was "
            f"restored; the machine is running {installation.installed_revision or 'nothing'}."
        )
    if installation.action == ACTION_INSTALL_FAILED:
        return (
            f"{name} could not be built at {short}; the working artifact was left in place, "
            f"because cargo replaces a binary only on success."
        )
    if installation.state == STATE_ABSENT:
        return f"{name} is not installed on the operator machine; {short} is available."
    if installation.state == STATE_CURRENT:
        installed = installation.installed_revision or ""
        return (
            f"{name} {installation.installed_version} on the operator machine is built from "
            f"{installed[:7]}, which is {installation.tool.branch}'s head."
        )
    permitted = (
        "; this pass was not permitted to act"
        if (installation.action == ACTION_NOT_PERMITTED)
        else ""
    )
    installed = installation.installed_revision or ""
    return (
        f"{name} on the operator machine is built from {installed[:7]}, which is not "
        f"{installation.tool.branch}'s head {short}{permitted}."
    )[:MAX_SUMMARY]


def status_of(installation: Installation) -> tuple[str, str]:
    if installation.action in FAILING_ACTIONS:
        return STATUS_FAILED, SEVERITY_FAILED
    if installation.is_finding:
        return STATUS_BEHIND, SEVERITY_BEHIND
    return STATUS_CURRENT, SEVERITY_CURRENT


def record_digest(record: dict[str, Any]) -> str:
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def reference_for(installation: Installation, record: dict[str, Any]) -> str:
    """The row's identity: the tool, the revision it is at, and a digest of everything else.

    The revision is in there for a reader rather than for uniqueness -- the digest already covers
    it. `record` is the whole composed observation minus the two self-referential fields; see the
    module docstring for why digesting `facts` alone is the defect this shape exists to avoid.
    """
    at = installation.installed_revision or "absent"
    return f"tool-revision:{installation.tool.repository}@{at}:{record_digest(record)}"


def observed_at_for(installation: Installation) -> str:
    """The installed revision's clock, falling back to the available one. Never a wall clock."""
    return installation.installed_committed_at or installation.available_committed_at


def installation_observation(installation: Installation) -> dict[str, Any]:
    status, severity = status_of(installation)
    revision = installation.installed_revision or installation.available_revision
    record = {
        "expected_version": 0,
        "source_system": SOURCE_SYSTEM,
        "source_url": (f"https://github.com/{installation.tool.repository}/commit/{revision}"),
        "trust_classification": TRUST_CLASSIFICATION,
        "subject_type": SUBJECT_TYPE,
        "subject_reference": installation.tool.repository,
        "environment": None,
        "observation_type": OBSERVATION_TYPE,
        "status": status,
        "severity": severity,
        # A revision's clock, never the pass's. See the module docstring: any wall-clock value
        # here makes the second pass over unchanged reality an `observation_conflict`.
        "observed_at": observed_at_for(installation),
        "summary": summary_of(installation),
        "facts": installation_facts(installation),
        "payload_digest": None,
    }
    reference = reference_for(installation, record)
    # The reference is ALREADY content-addressed over everything above, so the idempotency key is
    # the same string. Spelling two strings for one concept would be a second copy of it.
    return {"idempotency_key": reference, "source_reference": reference, **record}
