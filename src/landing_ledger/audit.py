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

**A has TWO subjects now, because there are two ways a landing gets permission without a person.**
The gate-permitted landing above is re-evaluated against a transcribed rule. A FACTORY landing
(ADR-0020) is re-evaluated against the ORCHESTRATOR'S OWN DURABLE RECORD, because everything the
ledger can see about it from GitHub -- the commit trailer naming a work unit, the pull-request body
naming the same -- is written by the runner, i.e. by the thing whose compliance is the question.
Same shape, different source of truth, and the same rule that neither is taken on its own word.

**The factory half asks the DURABLE record, never the live one.** `…/pr-merge-admission` composes
exactly the answer wanted and is deliberately not used: it evaluates whether the landing may happen
NOW, and that legitimately diverges from whether it was permitted THEN -- a unit's version moves,
a repository's landing classification is re-determined, an approval is superseded. An audit built
on the live answer manufactures findings out of ordinary change, and a reporting control that
cries wolf is one that gets ignored. What it reads instead does not drift: the unit completed, its
adjudications name the verifier as decider, the evidence was observed, and the orchestrator's own
account of its own act names this repository and this pull request.

One clause is checked TRANSITIVELY and it is worth naming rather than leaving implicit: that
landing on the repository changes nothing already serving. That is a live classification the estate
re-determines, so re-asking it here is precisely the divergence above. The orchestrator writes its
merge record ONLY after every admission term passed, and that term is one of them -- so the record
existing is the durable trace of the classification as it stood at the landing.

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
from typing import Any, Protocol

from landing_ledger.model import PendingUpdate
from landing_ledger.record import BASIS_FACTORY, BASIS_RULE
from landing_ledger.rules import GATE_PATH, Rule, rule_for

# `BASIS_RULE` and `BASIS_FACTORY` are IMPORTED, not restated. Until WS-P3.7 Increment 5 this
# module defined `BASIS_RULE = "auto_merge_rule"` independently of `record.py`, with nothing
# coupling the two -- the recorder writes the value and the detector selects on it, so a rename on
# one side would have made the detector silently select nothing, which reads as a clean estate.
# There is one definition now, and the import is the pin.

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

# Detector A, factory half (ADR-0020).
FACTORY_CLAIM_UNREADABLE = "factory_claim_unreadable"
FACTORY_UNIT_UNKNOWN = "factory_unit_unknown"
FACTORY_UNIT_NOT_COMPLETED = "factory_unit_not_completed"
FACTORY_NOT_VERIFIER_DECIDED = "factory_not_verifier_decided"
FACTORY_HUMAN_ADJUDICATION = "factory_human_adjudication"
FACTORY_LANDING_UNBOUND = "factory_landing_unbound"
FACTORY_FINGERPRINT_MISMATCH = "factory_fingerprint_mismatch"

# Detector B.
STALL_ELIGIBLE_NOT_ARMED = "eligible_green_and_not_armed"
STALL_ARMED_NOT_LANDED = "armed_green_and_still_open"
STALL_METADATA_UNREADABLE = "update_metadata_unreadable"
STALL_RULE_UNKNOWN = "current_rule_revision_unknown"

# A caveat qualifies the audit's own evidence; it is not an assertion that anything is wrong, and
# it does not drive the exit status. It is still recorded, so it cannot be lost by being quiet.
CAVEAT_RULE_SELF_MODIFIED = "rule_pinned_after_this_landing_changed_it"
CAVEAT_NO_RULE_INSTALLED = "no_rule_installed"
CAVEAT_MERGE_COMMIT_UNRECORDED = "merge_commit_not_recorded_by_the_orchestrator"

# The verifier's role as the adjudication rows spell it. ADR-0020 permits the factory to land only
# a unit no person adjudicated, so any other decider -- including a NULL one, which is the
# historical rows' value and is never read as consent -- disqualifies the landing.
VERIFIER_ROLE = "verifier"

# The record statuses that mean the orchestrator's landing call reached GitHub and the pull request
# is landed. `already_merged` is here on purpose: a landing is not idempotent and a lost response
# looks exactly like a refusal when GitHub is asked again, so the retry that finds the pull request
# already landed records this rather than overwriting something that happened. Requiring `merged`
# alone would turn a lost response into a finding.
LANDED_MERGE_STATUSES = frozenset({"merged", "already_merged"})

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
    factory_landings: int = 0
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


def _mapping(value: Any) -> dict[str, Any]:
    """Whatever this is if it is an object, otherwise an empty one.

    The audit reads bodies it did not construct -- stored facts and orchestrator responses -- so
    every nested lookup has to survive a shape it did not expect. An absent key then reads as a
    missing value and reaches a finding, rather than raising and taking the whole pass down.
    """
    return value if isinstance(value, dict) else {}


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


class UnitRecordReader(Protocol):
    """The two orchestrator reads the factory half needs, structural so a test can pass a fake.

    Both answer None when the orchestrator has no such unit, and RAISE when it could not be asked.
    The caller depends on that difference: a landing claiming a unit that does not exist is a
    finding about the landing, while an unreachable orchestrator is a measurement that did not
    happen and must reach the incomplete exit code instead.
    """

    def read_evidence_pack(self, work_unit_id: str) -> dict[str, Any] | None: ...

    def read_unit_history(self, work_unit_id: str) -> list[dict[str, Any]] | None: ...


def _merge_event(history: list[dict[str, Any]], repository: str, pull_request: Any) -> Any:
    """The orchestrator's own record that it landed THIS pull request, or None.

    Matched on repository AND pull-request number, never on the action alone: the question is
    whether this landing is the unit's landing, and a unit that landed some other pull request
    would satisfy an action-only match while proving nothing about this commit.
    """
    for event in history:
        if not isinstance(event, dict) or not str(event.get("action", "")).startswith("pr_merge."):
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        if (
            payload.get("status") in LANDED_MERGE_STATUSES
            and payload.get("repository") == repository
            and payload.get("pr_number") == pull_request
        ):
            return payload
    return None


def audit_factory_landing(
    facts: Any, units: UnitRecordReader
) -> tuple[tuple[Finding, ...], tuple[Finding, ...]]:
    """Re-evaluate one landing the factory made against what the orchestrator durably holds.

    The recorded claim is a HINT: it selects which unit to ask about and is evidence of nothing.
    Everything that decides the verdict is read from the orchestrator -- and the checks below are
    two independent readings rather than one, on purpose. `verifier_decided_completion` is the
    orchestrator's own composed answer over the required criteria, which is the only place the
    REQUIRED set is known; the walk over the current adjudications is a necessary condition
    computed here from the primary rows, which catches the composed answer being wrong about
    something visible. Neither subsumes the other and a reimplementation of the first would be a
    second copy of a judgment that already has an owner.
    """
    permitted = _permitted_by(facts)
    if permitted is None or permitted.get("basis") != BASIS_FACTORY:
        return (), ()
    changed = _what_changed(facts)
    commit = str(changed.get("commit"))
    subject = f"{changed.get('repository')}@{commit[:12]}"
    unit_id = permitted.get("work_unit")
    if not isinstance(unit_id, str) or not unit_id:
        return (
            Finding(
                FACTORY_CLAIM_UNREADABLE,
                subject,
                "recorded as a factory landing with no work unit named, so nothing can be checked",
            ),
        ), ()

    pack = units.read_evidence_pack(unit_id)
    history = units.read_unit_history(unit_id) if pack is not None else None
    if pack is None or history is None:
        return (
            Finding(
                FACTORY_UNIT_UNKNOWN,
                subject,
                f"the landing names work unit {unit_id}, which the orchestrator does not hold",
            ),
        ), ()

    work_unit = _mapping(pack.get("work_unit"))
    binding_findings, caveats = _audit_landing_binding(
        history, work_unit, changed, commit, subject, unit_id
    )
    return _audit_unit_record(pack, work_unit, subject) + binding_findings, caveats


def _audit_unit_record(
    pack: dict[str, Any], work_unit: dict[str, Any], subject: str
) -> tuple[Finding, ...]:
    """Does the orchestrator hold this unit in the state ADR-0020 requires to have landed it?"""
    findings: list[Finding] = []
    state = work_unit.get("state")
    if state != "completed":
        findings.append(
            Finding(FACTORY_UNIT_NOT_COMPLETED, subject, f"the unit is {state!r}, not completed")
        )

    decided = _mapping(pack.get("verifier_decided_completion"))
    if not (decided.get("decided_by_verifier") and decided.get("evidence_observed")):
        refusals = decided.get("refusals")
        codes = [
            str(refusal.get("code"))
            for refusal in (refusals if isinstance(refusals, list) else [])
            if isinstance(refusal, dict)
        ]
        findings.append(
            Finding(
                FACTORY_NOT_VERIFIER_DECIDED,
                subject,
                "the orchestrator does not hold this unit as decided by the verifier from "
                f"observed evidence: {'; '.join(sorted(codes)[:MAX_LIST]) or 'no reason given'}",
            )
        )

    deciders = {
        row.get("decided_by_role")
        for row in (pack.get("adjudications") or [])
        if isinstance(row, dict) and row.get("current")
    }
    if deciders - {VERIFIER_ROLE}:
        findings.append(
            Finding(
                FACTORY_HUMAN_ADJUDICATION,
                subject,
                "a current adjudication was decided outside the verifier role: "
                + ", ".join(sorted(str(role) for role in deciders - {VERIFIER_ROLE})),
            )
        )
    return tuple(findings)


def _audit_landing_binding(
    history: list[dict[str, Any]],
    work_unit: dict[str, Any],
    changed: dict[str, Any],
    commit: str,
    subject: str,
    unit_id: str,
) -> tuple[tuple[Finding, ...], tuple[Finding, ...]]:
    """Is this landing THIS unit's landing? Everything else rests on the answer being yes.

    Without it the claim selects any completed, verifier-decided unit in the estate and the audit
    reports on that one instead -- a check that is correct about the wrong noun. The orchestrator's
    own record of its own act is what closes it.
    """
    findings: list[Finding] = []
    caveats: list[Finding] = []
    payload = _merge_event(history, str(changed.get("repository")), changed.get("pull_request"))
    if payload is None:
        return (
            Finding(
                FACTORY_LANDING_UNBOUND,
                subject,
                f"unit {unit_id} holds no record of landing pull request "
                f"{changed.get('pull_request')} in this repository",
            ),
        ), ()

    landed_commit = payload.get("merge_commit_sha")
    if landed_commit is None:
        caveats.append(
            Finding(
                CAVEAT_MERGE_COMMIT_UNRECORDED,
                subject,
                "the orchestrator recorded landing this pull request without a commit, which is "
                "what a retry that found it already landed records; the binding rests on the "
                "repository and pull request alone",
            )
        )
    elif landed_commit != commit:
        findings.append(
            Finding(
                FACTORY_LANDING_UNBOUND,
                subject,
                f"the orchestrator recorded landing {str(landed_commit)[:12]} for this pull "
                "request, which is not the commit that reached the branch",
            )
        )

    fingerprint = work_unit.get("authority_fingerprint")
    if payload.get("authority_fingerprint") != fingerprint:
        findings.append(
            Finding(
                FACTORY_FINGERPRINT_MISMATCH,
                subject,
                "the authority the landing was made under is not the authority the unit now "
                "carries",
            )
        )
    return tuple(findings), tuple(caveats)
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
    units: UnitRecordReader,
    now: datetime,
    settle_seconds: int = SETTLE_SECONDS,
) -> RepoAudit:
    """Both detectors over one repository. Never raises on a shape it did not expect.

    It DOES propagate an orchestrator that could not be read, which is not the same thing: the
    caller turns that into the incomplete exit code, where a swallowed one would report a
    repository as clean on the strength of a question nobody managed to ask.
    """
    findings: list[Finding] = []
    caveats: list[Finding] = []
    permitted = 0
    factory = 0
    for facts in landings:
        landing_findings, landing_caveats = audit_landing(facts)
        factory_findings, factory_caveats = audit_factory_landing(facts, units)
        record = _permitted_by(facts)
        basis = record.get("basis") if record is not None else None
        # Two subjects, two denominators, kept apart. Folding them would put factory landings
        # behind a key whose name says `rule`, and a count nobody can attribute is not a
        # denominator -- it is a number.
        permitted += basis == BASIS_RULE
        factory += basis == BASIS_FACTORY
        findings.extend(landing_findings + factory_findings)
        caveats.extend(landing_caveats + factory_caveats)

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
        factory_landings=factory,
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
            "factory_landings": audit.factory_landings,
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
            f"{audit.permitted_landings} rule-permitted landing(s), "
            f"{audit.factory_landings} factory landing(s) and "
            f"{audit.pending_audited} open update(s)"
            + (" [UNAVAILABLE]" if audit.unavailable else "")
        )[:512],
        "facts": facts,
        "payload_digest": None,
    }
