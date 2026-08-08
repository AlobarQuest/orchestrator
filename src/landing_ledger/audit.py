"""The two detectors that replace the human gate on a routine dependency update.

They REPORT. Neither of them changes anything, anywhere -- not a pull request, not a repository
setting, not a work unit. That is this estate's established shape for anything that watches
(ADR-0002/0003): a separate program that reads reality and files what it saw, so a wrong detector
costs a wrong record rather than a wrong action.

**Detector A -- did a permitted landing actually satisfy the rule that was in force?** The ledger
records the update's own metadata as values and pins the rule by its blob sha at the landing
commit, so the question is answerable by re-evaluation rather than by trusting the gate's own
report of itself. `rules.py` holds the transcribed rules; an unrecognised revision is a finding,
never a pass.

**Detector B -- is anything eligible, green, and simply not landing?** The ledger cannot see this
at all: a pull request that never lands leaves no landing record, so the failure is an ABSENCE in
the very thing that would report it. B therefore reads the open pull requests from GitHub. It
covers two of the three known ways this happens -- a rule that stopped arming (the hyphenated
literal of 2026-08-07), and the estate's habit of disarming the siblings of whichever pull request
lands first -- because both present identically: eligible, green, unarmed.

**The third way is NOT here, deliberately.** A recorder that silently covers less than it claims
is a property of the recorder, not of landing, and it is answered where it happens: `record` now
reports a pass that could not read everything as a distinct, non-zero outcome. Folding it in here
would put "the producer under-reported" behind a detector whose input is what the producer
produced -- which is the shape of a check that consumes the thing it is meant to detect.

**What a rule arm cannot be evidence of.** A landing's own gate run says the gate EXECUTED; it
never says the change is sound. So neither detector counts the gate's run as a check, and B's
notion of green excludes it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from landing_ledger.model import PendingUpdate
from landing_ledger.rules import GATE_PATH, Rule, rule_for

# The basis the ledger records for a landing the gate permitted. Only these are Detector A's
# subject: a landing a person decided needs no rule to have been satisfied.
BASIS_RULE = "auto_merge_rule"

# A conclusion that means the job did not pass. `skipped` and `neutral` are neither pass nor
# failure and are deliberately absent -- a conditional job that did not run is not a red check,
# and treating it as one would make every repository with a conditional job a permanent finding.
FAILING_CONCLUSIONS = frozenset(
    {"failure", "timed_out", "cancelled", "action_required", "startup_failure", "stale"}
)

# How long a pull request must have been green before "armed and still open" is worth reporting.
# GitHub lands an armed pull request within seconds of its last required check, so anything above
# a few minutes is generous -- but a pass that ran inside that window would report a race as a
# defect. A plain int with a real default and NO off value: a reporting obligation that can be
# switched off is one that will be.
SETTLE_SECONDS = 3600

# Detector A.
DRIFT_RULE_UNKNOWN = "rule_revision_unknown"
DRIFT_RULE_MISSING = "rule_revision_missing"
DRIFT_METADATA_MISSING = "update_metadata_missing"
DRIFT_NOT_SATISFIED = "rule_not_satisfied"
DRIFT_RULE_DID_NOT_SUCCEED = "rule_run_did_not_succeed"
DRIFT_CHECK_NOT_GREEN = "check_did_not_pass"

# Detector B.
STALL_ELIGIBLE_NOT_ARMED = "eligible_green_and_not_armed"
STALL_ARMED_NOT_LANDED = "armed_green_and_still_open"
STALL_METADATA_UNREADABLE = "update_metadata_unreadable"
STALL_RULE_UNKNOWN = "current_rule_revision_unknown"

# A caveat qualifies the audit's own evidence; it is not an assertion that anything is wrong, and
# it does not drive the exit status. It is still recorded, so it cannot be lost by being quiet.
CAVEAT_RULE_SELF_MODIFIED = "rule_pinned_after_this_landing_changed_it"
CAVEAT_NO_RULE_INSTALLED = "no_rule_installed"

MAX_LIST = 20
MAX_DETAIL = 240
MAX_FACT_BYTES = 4096

OBSERVATION_TYPE = "landing_audit"
SOURCE_SYSTEM = "github"
TRUST_CLASSIFICATION = "delivery_system"
SUBJECT_TYPE = "repo"
STATUS = "observed"


@dataclass(frozen=True)
class Finding:
    kind: str
    subject: str
    detail: str

    def as_fact(self) -> dict[str, str]:
        return {"kind": self.kind, "subject": self.subject, "detail": self.detail[:MAX_DETAIL]}


@dataclass(frozen=True)
class RepoAudit:
    """One repository's answer. `unavailable` means the answer is missing, not that it is clean."""

    repository: str
    rule_revision: str | None
    landings_audited: int
    permitted_landings: int
    pending_audited: int
    findings: tuple[Finding, ...] = ()
    caveats: tuple[Finding, ...] = ()
    unavailable: bool = False

    @property
    def severity(self) -> str:
        return "warning" if self.findings or self.unavailable else "info"


def _permitted_by(facts: Any) -> dict[str, Any] | None:
    if not isinstance(facts, dict):
        return None
    record = facts.get("permitted_by")
    return record if isinstance(record, dict) else None


def _what_changed(facts: Any) -> dict[str, Any]:
    if not isinstance(facts, dict):
        return {}
    record = facts.get("what_changed")
    return record if isinstance(record, dict) else {}


def _failing(checks: Any) -> list[str]:
    if not isinstance(checks, list):
        return []
    return [
        f"{check.get('name')}={check.get('conclusion')}"
        for check in checks
        if isinstance(check, dict) and check.get("conclusion") in FAILING_CONCLUSIONS
    ]


def audit_landing(facts: Any) -> tuple[tuple[Finding, ...], tuple[Finding, ...]]:
    """Re-evaluate one recorded landing against the rule that was pinned to it.

    Returns (findings, caveats). A landing whose basis is not the rule is not this detector's
    subject and yields neither -- the caller counts it, so "nothing found" always arrives with a
    denominator.
    """
    permitted = _permitted_by(facts)
    if permitted is None or permitted.get("basis") != BASIS_RULE:
        return (), ()
    changed = _what_changed(facts)
    subject = f"{changed.get('repository')}@{str(changed.get('commit'))[:12]}"
    findings: list[Finding] = []
    caveats: list[Finding] = []

    if GATE_PATH in (changed.get("files") or []):
        caveats.append(
            Finding(
                CAVEAT_RULE_SELF_MODIFIED,
                subject,
                "the pinned revision is this landing's OWN new rule, because the ledger reads the "
                "gate at the landing commit; the rule that armed it was the previous revision",
            )
        )

    revision = permitted.get("rule_revision")
    if not revision:
        findings.append(
            Finding(DRIFT_RULE_MISSING, subject, "recorded as rule-permitted with no rule pinned")
        )
        return tuple(findings), tuple(caveats)

    rule = rule_for(str(revision))
    if rule is None:
        findings.append(
            Finding(
                DRIFT_RULE_UNKNOWN,
                subject,
                f"rule revision {revision} is not transcribed, so what it permitted is unknown",
            )
        )
        return tuple(findings), tuple(caveats)

    if permitted.get("rule_outcome") != "success":
        findings.append(
            Finding(
                DRIFT_RULE_DID_NOT_SUCCEED,
                subject,
                f"the rule's own run concluded {permitted.get('rule_outcome')!r}",
            )
        )

    update_type = permitted.get("update_type")
    ecosystem = permitted.get("ecosystem")
    if rule.requires_upstream_author and update_type is None:
        findings.append(
            Finding(
                DRIFT_METADATA_MISSING,
                subject,
                "no update metadata was recorded, so the rule's own condition cannot be re-read",
            )
        )
    elif not rule.permits(update_type, ecosystem):
        findings.append(
            Finding(
                DRIFT_NOT_SATISFIED,
                subject,
                f"{update_type} of a {ecosystem} dependency is outside rule {revision[:12]}",
            )
        )

    failing = _failing(permitted.get("checks"))
    if failing:
        findings.append(
            Finding(DRIFT_CHECK_NOT_GREEN, subject, "; ".join(sorted(failing)[:MAX_LIST]))
        )
    return tuple(findings), tuple(caveats)


def is_green(pending: PendingUpdate) -> bool:
    """At least one job concluded at the head, and none of them failed."""
    if not pending.checks:
        return False
    return not any(check.conclusion in FAILING_CONCLUSIONS for check in pending.checks)


def audit_pending(
    pending: PendingUpdate, rule: Rule, now: datetime, settle_seconds: int = SETTLE_SECONDS
) -> tuple[Finding, ...]:
    """Classify one open update against the rule currently installed.

    Three outcomes are HEALTHY and produce nothing: ineligible and unarmed (the rule declining to
    act, which is the rule working), eligible but not green (the required checks doing their job),
    and eligible, green, armed and freshly settled (a landing about to happen).
    """
    subject = f"{pending.repository}#{pending.number}"
    green = is_green(pending)
    if pending.update is None:
        return (
            Finding(
                STALL_METADATA_UNREADABLE,
                subject,
                "no update metadata on the head commit, so eligibility cannot be decided",
            ),
        )
    if not rule.permits(pending.update.update_type, pending.update.ecosystem):
        return ()
    if not green:
        return ()
    if not pending.armed:
        return (
            Finding(
                STALL_ELIGIBLE_NOT_ARMED,
                subject,
                f"{pending.update.update_type} of {pending.update.dependency} is permitted by the "
                f"installed rule and every concluded check passed, but nothing armed it",
            ),
        )
    if pending.last_concluded_at is None:
        return ()
    if (now - pending.last_concluded_at).total_seconds() < settle_seconds:
        return ()
    return (
        Finding(
            STALL_ARMED_NOT_LANDED,
            subject,
            f"armed and green since {pending.last_concluded_at.isoformat()}, still open",
        ),
    )


def audit_repository(
    *,
    repository: str,
    landings: list[Any],
    pending: tuple[PendingUpdate, ...],
    rule_revision: str | None,
    now: datetime,
    settle_seconds: int = SETTLE_SECONDS,
) -> RepoAudit:
    """Both detectors over one repository. Never raises on a shape it did not expect."""
    findings: list[Finding] = []
    caveats: list[Finding] = []
    permitted = 0
    for facts in landings:
        landing_findings, landing_caveats = audit_landing(facts)
        record = _permitted_by(facts)
        if record is not None and record.get("basis") == BASIS_RULE:
            permitted += 1
        findings.extend(landing_findings)
        caveats.extend(landing_caveats)

    rule = rule_for(rule_revision)
    if rule_revision is None:
        # A repository with no gate cannot fail to arm one. Its open updates are still counted and
        # reported, because "nobody installed a rule here" is a fact worth reading -- but it is a
        # scope decision somebody made, not drift, and reporting it as a violation would make the
        # detector permanently red about something nobody intends to change.
        caveats.append(
            Finding(
                CAVEAT_NO_RULE_INSTALLED,
                repository,
                f"no rule at {GATE_PATH}; {sum(1 for p in pending if is_green(p))} of "
                f"{len(pending)} open updates are green and will not land unattended",
            )
        )
    elif rule is None:
        findings.append(
            Finding(
                STALL_RULE_UNKNOWN,
                repository,
                f"the installed rule {rule_revision[:12]} is not transcribed, so no open update "
                "here can be classified",
            )
        )
    else:
        for candidate in pending:
            findings.extend(audit_pending(candidate, rule, now, settle_seconds))

    return RepoAudit(
        repository=repository,
        rule_revision=rule_revision,
        landings_audited=len(landings),
        permitted_landings=permitted,
        pending_audited=len(pending),
        findings=tuple(findings),
        caveats=tuple(caveats),
    )


def _fit(facts: dict[str, Any]) -> dict[str, Any]:
    """Trim the two variable-length lists until the record fits the orchestrator's byte bound.

    Caveats go before findings -- a caveat qualifies evidence, a finding asserts a violation, and
    the violation is the thing that must survive. Both keep their true counts beside them, so a
    trim is visible rather than silent.
    """
    while len(json.dumps(facts, sort_keys=True, separators=(",", ":"))) > MAX_FACT_BYTES:
        for entries in (facts["caveats"], facts["findings"]):
            if entries:
                entries.pop()
                break
        else:
            return facts
    return facts


def audit_observation(audit: RepoAudit, pass_id: str, observed_at: datetime) -> dict[str, Any]:
    """One observation per repository per pass -- emitted whether or not anything was found.

    Unconditional on purpose. A detector that writes only when it finds something is
    indistinguishable from a detector that has stopped running, which is the failure this whole
    increment exists to catch. The row is the heartbeat; the findings are its content.
    """
    facts = _fit(
        {
            "pass_id": pass_id,
            "repository": audit.repository,
            "rule_revision": audit.rule_revision,
            "unavailable": audit.unavailable,
            "landings_audited": audit.landings_audited,
            "rule_permitted_landings": audit.permitted_landings,
            "pending_audited": audit.pending_audited,
            "findings_found": len(audit.findings),
            "caveats_found": len(audit.caveats),
            "findings": [finding.as_fact() for finding in audit.findings[:MAX_LIST]],
            "caveats": [caveat.as_fact() for caveat in audit.caveats[:MAX_LIST]],
        }
    )
    reference = f"landing-audit:{audit.repository}@{pass_id}"
    digest = hashlib.sha256(
        json.dumps(facts, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        # Content-addressed, so re-running one pass replays and a pass that answers its own id
        # differently reaches the orchestrator's same-source/different-facts branch and is loud.
        "idempotency_key": f"{reference}:{digest}",
        "expected_version": 0,
        "source_system": SOURCE_SYSTEM,
        "source_reference": reference,
        "source_url": None,
        "trust_classification": TRUST_CLASSIFICATION,
        "subject_type": SUBJECT_TYPE,
        "subject_reference": audit.repository,
        "environment": None,
        "observation_type": OBSERVATION_TYPE,
        "status": STATUS,
        "severity": audit.severity,
        "observed_at": observed_at.isoformat(),
        "summary": (
            f"{audit.repository}: {len(audit.findings)} finding(s) over "
            f"{audit.permitted_landings} rule-permitted landing(s) and "
            f"{audit.pending_audited} open update(s)"
            + (" [UNAVAILABLE]" if audit.unavailable else "")
        )[:512],
        "facts": facts,
        "payload_digest": None,
    }
