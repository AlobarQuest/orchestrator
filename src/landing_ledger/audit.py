"""The three detectors that replace the human gate on a routine dependency update.

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
cries wolf is one that gets ignored.

**Be exact about which half of what it reads is actually immutable, because two of the four things
below are and two are recomputed.** The unit id, the merge event and the authority fingerprint are
rows that were written once and are never rewritten. `verifier_decided_completion` is NOT a stored
verdict: the evidence pack recomputes it per read, over stored adjudication and evidence rows but
against constants in the RUNNING orchestrator -- so a future release that narrows which evidence
types count as observed would flip already-audited landings to a finding. That residual is real and
is accepted rather than papered over: it is much narrower than admission's (which consults the
estate, the current approval and the live envelope gates on every call), and the alternative --
re-deriving the verdict here from the adjudication rows -- cannot see which criteria the revision
REQUIRED, so it would be a second copy of a judgment with a worse view than the original.

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

**Detector C -- is the default branch green NOW?** A and B both ask about ONE update at a time,
and neither can see the failure ADR-0034 newly creates: several updates, each green on its own
head, landing in one evening and breaking `main` in combination. Nothing verifies the combination.
It is the hazard `strict: true` would prevent and which this estate deliberately declined, because
making branches strict serialises every merge behind rebase cycles -- so the answer is to WATCH
the combination rather than to forbid it.

**C is not a second copy of `DRIFT_CHECK_NOT_GREEN`.** That one asks whether a landing went in
with a failing check on its OWN head, which A already files daily across all eight repositories.
C asks about the branch afterwards, where every landing's checks may have passed individually.

**The whole of C is the three-state distinction, and collapsing it makes the control useless.**
A run in flight, or a tip nothing has decided on yet, IS NOT A FINDING. Under the current arming
identity a landing fires no `push` run at all -- a `GITHUB_TOKEN`-armed auto-merge triggers none
-- so an unverified tip is the ordinary state for hours at a time, and reporting it would red the
control permanently. This estate has now paid twice for collapsing check states: `mergeable_state:
blocked` covers four different causes, and the landing lane held three clean bumps for four days
on runs GitHub had cancelled when the Actions quota ran out. The quiet answers are still RECORDED,
in the observation's own facts, because which quiet answer it is is worth reading -- they are just
not assertions that anything is wrong.

**What a rule arm cannot be evidence of.** A landing's own gate run says the gate EXECUTED; it
never says the change is sound. So neither detector counts the gate's run as a check, and B's
notion of green excludes it. C excludes it too, and for C the omission is sharper: the gate runs
only on pull requests, so a gate run at a branch tip would be a surprise rather than a signal.

**THREE CATEGORIES, and only one of them drives the exit status.** A FINDING asserts that
something is wrong and that a person can act on it. A CAVEAT qualifies the audit's own evidence.
An EXCEPTION is a subject current policy can never decide, for a reason that is a fact about the
subject rather than a defect -- it is recorded, printed, and quiet, because a control that is
permanently non-zero on conditions that will never clear is one nobody reads. The categories are
kept apart rather than folded: WHICH one a line is IS the information, and a single quiet set
would say "quiet" about all of them while losing which is which.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from landing_ledger.model import (
    BRANCH_FAILING,
    BRANCH_IN_FLIGHT,
    BRANCH_PASSING,
    BRANCH_UNVERIFIED,
    BranchStatus,
    PendingUpdate,
    WorkflowRun,
    is_work_unit_id,
)

# What the orchestrator reads raise when they cannot answer. The name predates the read surface --
# the client had only a write surface when it was written -- and is left alone rather than churned
# through two modules and their tests for a word.
from landing_ledger.orchestrator_client import LedgerWriteError as LedgerReadError
from landing_ledger.record import (
    BASIS_FACTORY,
    BASIS_RULE,
    is_known_defective_metadata_landing,
)
from landing_ledger.rules import GATE_PATH, Rule, rule_for
from landing_ledger.titles import bump_of

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

# A conclusion that is NOT A VERDICT ABOUT THE CHANGE. A cancelled run was stopped, a stale one
# superseded, a skipped one never asked -- none of them says the change is bad, and the estate has
# already paid for reading them as though they did: the landing lane held three clean bumps for
# four days on the strength of runs GitHub cancelled when the Actions quota ran out.
#
# A DELIBERATE MIRROR of `orchestrator.services.estate_landing_admission.NO_VERDICT_CONCLUSIONS`,
# which this program may not import -- the isolation test says so -- and pinned to it by a test
# that imports both, the same arrangement `titles.py` uses. Its polarity is the safety: anything
# NOT named here is read as a verdict, so a conclusion the platform has not yet invented fails
# toward refusing rather than toward calling itself absent.
NO_VERDICT_CONCLUSIONS = frozenset({"cancelled", "skipped", "stale"})

# The subset of the above that a CONSUMER MAY ACT ON. `FAILING_CONCLUSIONS` is deliberately wider
# because it feeds `is_green`, where over-counting is conservative: a reporting predicate that
# calls a cancelled run not-green merely stays quiet. A predicate that drives an irreversible act
# needs the narrower question -- did the checks decide AGAINST this change -- and the two must not
# share a name, because the same word would let one be tuned for the other's cost.
REFUSING_CONCLUSIONS = FAILING_CONCLUSIONS - NO_VERDICT_CONCLUSIONS

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
FACTORY_LANDING_UNCLAIMED = "factory_landing_not_claimed_by_the_orchestrator"
FACTORY_FINGERPRINT_MISMATCH = "factory_fingerprint_mismatch"

# Detector C.
BRANCH_NOT_GREEN = "default_branch_not_green"

# Detector B.
STALL_ELIGIBLE_NOT_ARMED = "eligible_green_and_not_armed"
STALL_ARMED_NOT_LANDED = "armed_green_and_still_open"
STALL_METADATA_UNREADABLE = "update_metadata_unreadable"
STALL_RULE_UNKNOWN = "current_rule_revision_unknown"

# An EXCEPTION is a subject that current policy can never decide, for a reason that is a property
# of the subject rather than a defect anywhere. It is recorded and printed, and it drives no exit
# status: a reader cannot act on it and no pass will ever clear it.
#
# NOT A CAVEAT, and the distinction is the whole of this category. A caveat is a doubt about the
# AUDIT'S OWN EVIDENCE -- something the audit is unsure of. An exception is a certainty about the
# SUBJECT. Filing one as the other would put a fact where doubts go to be ignored, and this module
# already says in as many words that a caveat is where a doubt goes to be ignored.
#
# THE LANDING LANE ALREADY CLASSIFIES THESE SUBJECTS THIS WAY, and this is the same ruling reaching
# the second control that looks at them: `estate_lander._EXCEPTION` holds the lander's refusal code
# for the identical condition. The two vocabularies are NOT pinned to each other and must not be --
# neither program reads the other's codes, and the thing they genuinely share is the classifier
# (`titles.bump_of`), which IS pinned. What agrees is the answer, not the spelling.
EXCEPTION_UPDATE_TYPE_UNPARSEABLE = "update_type_unparseable"

# Detector A's exception, and the only one it has. A landing recorded during the window in which
# the reader discarded a requirement range's own trailer: the row is immutable and content-
# addressed, so the three update keys it lacks can never arrive and the finding it would otherwise
# raise can never clear. A certainty about the subject, which is what separates it from a caveat.
EXCEPTION_METADATA_UNREADABLE_AT_RECORDING = "update_metadata_unreadable_at_recording"

# A caveat qualifies the audit's own evidence; it is not an assertion that anything is wrong, and
# it does not drive the exit status. It is still recorded, so it cannot be lost by being quiet.
CAVEAT_RULE_SELF_MODIFIED = "rule_pinned_after_this_landing_changed_it"
CAVEAT_NO_RULE_INSTALLED = "no_rule_installed"
# A caveat qualifies the audit's own evidence; it is not an assertion that anything is wrong, and
# it does not drive the exit status. NOTHING about a factory landing is reported this way, and that
# is a decision: a caveat is where a doubt goes to be ignored, and every doubt about an irreversible
# act this estate cannot undo deserves the lane a person actually reads.

# The verifier's role as the adjudication rows spell it. ADR-0020 permits the factory to land only
# a unit no person adjudicated, so any other decider -- including a NULL one, which is the
# historical rows' value and is never read as consent -- disqualifies the landing.
VERIFIER_ROLE = "verifier"

# What the orchestrator's own record of its own act can say. All three are RECOGNISED, because the
# row names this repository and this pull request whatever it concluded -- but only ONE of them
# asserts that the orchestrator made this landing, and the other two are findings.
#
# READ ALL FIVE WRITERS IN `pr_merge.py` BEFORE CHANGING THIS. Neither of the other two statuses
# means what its name suggests, and a first draft of this module widened the binding on a premise
# that two adversarial reviews falsified from opposite directions:
#
#  * `already_merged` has two writers. One is the lost-response retry, which IS the factory's own
#    act reconciled after the fact. The other fires BEFORE the merge call, when the pull request
#    was already landed -- and its own comment says it records "somebody else's act, never as
#    ours". They are indistinguishable in the row, so the status cannot assert authorship.
#  * `refused` has two writers as well. One is "the one genuinely ambiguous outcome", where the
#    call failed and the reconciling read failed too, and `pr_merge.py` says in as many words that
#    the ledger observes the landing independently and can settle it. The other is a CONFIRMED
#    non-landing: the remote answered 200 and said it had not merged.
#
# So a landing whose only record is either of those is reported, never excused. Settling an
# ambiguity is exactly what a finding does -- it puts the thing in front of a person -- while a
# caveat drives no exit code and would settle it by making it quiet.
MERGE_RECORD_STATUSES = frozenset({"merged", "already_merged", "refused"})
LANDED_MERGE_STATUS = "merged"

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
    # Detector C's answer, or None when the branch could not be asked -- which is a measurement
    # that did not happen and reaches `unavailable`, never a pass.
    branch: BranchStatus | None = None
    findings: tuple[Finding, ...] = ()
    caveats: tuple[Finding, ...] = ()
    exceptions: tuple[Finding, ...] = ()
    unavailable: bool = False

    @property
    def severity(self) -> str:
        """Exceptions alone leave a repository `info`, exactly as caveats do.

        They are recorded facts about subjects nothing can decide, not assertions that something
        is wrong -- so a repository whose whole open queue is unclassifiable is quiet here, and
        says so in its own list rather than in its severity.
        """
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


def audit_landing(
    facts: Any,
) -> tuple[tuple[Finding, ...], tuple[Finding, ...], tuple[Finding, ...]]:
    """Re-evaluate one recorded landing against the rule that was pinned to it.

    Returns (findings, caveats, exceptions). A landing whose basis is not the rule is not this
    detector's subject and yields none of the three -- the caller counts it, so "nothing found"
    always arrives with a denominator.

    The third slot exists for exactly one subject and arrived with it: the six rows recorded while
    the reader could not read a requirement range's trailer. It is an EXCEPTION rather than a
    caveat because a caveat is a doubt about this audit's own evidence, and this is a certainty
    about the subject -- the row is immutable, the metadata it lacks can never arrive, and the
    finding it would otherwise raise can never clear.
    """
    permitted = _permitted_by(facts)
    if permitted is None or permitted.get("basis") != BASIS_RULE:
        return (), (), ()
    changed = _what_changed(facts)
    subject = f"{changed.get('repository')}@{str(changed.get('commit'))[:12]}"
    findings: list[Finding] = []
    caveats: list[Finding] = []
    exceptions: list[Finding] = []

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
        return tuple(findings), tuple(caveats), tuple(exceptions)

    rule = rule_for(str(revision))
    if rule is None:
        findings.append(
            Finding(
                DRIFT_RULE_UNKNOWN,
                subject,
                f"rule revision {revision} is not transcribed, so what it permitted is unknown",
            )
        )
        return tuple(findings), tuple(caveats), tuple(exceptions)

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
    # ABSENT, NOT NULL. `permitted_by` writes the three update keys together or not at all, so
    # the key's presence answers "was the trailer readable?" while its VALUE answers "what did
    # the update bot declare?". Testing the value would report every no-delta landing revision
    # 3457db3c permits as metadata this program failed to read -- a finding about the ledger,
    # raised against the landings the new rule exists to make. One key is tested because all
    # three arrive together; `test_record.py` pins that they do.
    if rule.requires_upstream_author and "update_type" not in permitted:
        # THE SIX ROWS OF THE KNOWN-DEFECTIVE WINDOW ARE EXEMPT, BY IDENTITY, AND ONLY HERE. The
        # exemption withholds this one finding and nothing else: every other check above and below
        # still runs against these rows, so a rule that did not succeed, or a check that did not
        # pass, is still reported for them. Silencing the class outright would be worse than the
        # defect -- a genuine absence on any other landing must still be a finding.
        if is_known_defective_metadata_landing(changed.get("repository"), changed.get("commit")):
            exceptions.append(
                Finding(
                    EXCEPTION_METADATA_UNREADABLE_AT_RECORDING,
                    subject,
                    "recorded while the reader could not read a requirement range's trailer; the "
                    "row is immutable, so the metadata it lacks can never arrive",
                )
            )
        else:
            findings.append(
                Finding(
                    DRIFT_METADATA_MISSING,
                    subject,
                    "no update metadata was recorded, so the rule's own condition cannot be "
                    "re-read",
                )
            )
    elif not rule.permits(update_type, ecosystem):
        findings.append(
            Finding(
                DRIFT_NOT_SATISFIED,
                subject,
                f"{update_type or 'an update stating no version delta'} in the {ecosystem} "
                f"ecosystem is outside rule {revision[:12]}",
            )
        )

    failing = _failing(permitted.get("checks"))
    if failing:
        findings.append(
            Finding(DRIFT_CHECK_NOT_GREEN, subject, "; ".join(sorted(failing)[:MAX_LIST]))
        )
    return tuple(findings), tuple(caveats), tuple(exceptions)


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
            payload.get("status") in MERGE_RECORD_STATUSES
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
    # The SHAPE is validated here, not only at the client. These facts are read back out of the
    # orchestrator rather than constructed by this module, so a value that cannot name a unit must
    # become a finding about the landing -- and the client refuses an unreadable path with an error
    # that `audit_pass` catches as UNAVAILABLE, which would report the whole repository as
    # unmeasured on the strength of one malformed row. Two lanes, and this picks the right one.
    unit_id = permitted.get("work_unit")
    if not is_work_unit_id(unit_id):
        return (
            Finding(
                FACTORY_CLAIM_UNREADABLE,
                subject,
                f"recorded as a factory landing naming {unit_id!r}, which cannot name a work unit",
            ),
        ), ()
    assert isinstance(unit_id, str)

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
    others = deciders - {VERIFIER_ROLE}
    if others:
        findings.append(
            Finding(
                FACTORY_HUMAN_ADJUDICATION,
                subject,
                "a current adjudication was decided outside the verifier role: "
                # NULL is the historical rows' value and reads as `unrecorded`, never as consent --
                # the same word the evidence pack's own markdown prints for it, so a reader does
                # not meet two spellings of one absence.
                + ", ".join(sorted("unrecorded" if role is None else str(role) for role in others)),
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

    status = payload.get("status")
    if status != LANDED_MERGE_STATUS:
        findings.append(
            Finding(
                FACTORY_LANDING_UNCLAIMED,
                subject,
                f"the orchestrator's record of this pull request is {status!r}, which does not "
                "assert that it made this landing",
            )
        )

    # BOTH heads, and they answer different questions. `merge_commit_sha` is what the orchestrator
    # believes it produced; `head_sha` is the head it NAMED in the call, which the remote refused
    # anything else for. Comparing only the first leaves the binding resting on a pull-request
    # NUMBER whenever a status carries no commit -- and a number says nothing about content.
    landed_commit = payload.get("merge_commit_sha")
    if landed_commit is not None and landed_commit != commit:
        findings.append(
            Finding(
                FACTORY_LANDING_UNBOUND,
                subject,
                f"the orchestrator recorded landing {str(landed_commit)[:12]} for this pull "
                "request, which is not the commit that reached the branch",
            )
        )
    observed_head = changed.get("head_commit")
    if observed_head is not None and payload.get("head_sha") != observed_head:
        findings.append(
            Finding(
                FACTORY_LANDING_UNBOUND,
                subject,
                f"the orchestrator asked to land head {str(payload.get('head_sha'))[:12]}, which "
                f"is not the head {str(observed_head)[:12]} that this pull request landed",
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
    return tuple(findings), ()


def is_green(pending: PendingUpdate) -> bool:
    """At least one job concluded at the head, and none of them failed."""
    if not pending.checks:
        return False
    return not any(check.conclusion in FAILING_CONCLUSIONS for check in pending.checks)


def audit_pending(
    pending: PendingUpdate, rule: Rule, now: datetime, settle_seconds: int = SETTLE_SECONDS
) -> tuple[tuple[Finding, ...], tuple[Finding, ...]]:
    """Classify one open update against the rule currently installed.

    Returns (findings, exceptions). Three outcomes are HEALTHY and produce neither: ineligible and
    unarmed (the rule declining to act, which is the rule working), eligible but not green (the
    required checks doing their job), and eligible, green, armed and freshly settled (a landing
    about to happen).

    A FOURTH outcome is neither healthy nor a finding, and separating it out is the whole of this
    function's 2026-08-23 change. When the head commit carries no update metadata, two very
    different subjects arrive at the same place: one the gate SHOULD be able to decide and this
    audit cannot, which is a finding; and one nothing here can decide, because neither the
    trailer nor the title states anything a rule could be applied to. Reporting the second kind
    as findings made this detector exit non-zero every night on seven subjects that would never
    clear -- which is how a control stops being read, and how a real stall arrives as an eighth
    line in a report already known to be noise.

    ITS SUBJECT SHRANK ON 2026-08-28 AND THE BRANCH IS KEPT ANYWAY. Requirement ranges were the
    bulk of it, and they no longer arrive here at all: the reader keeps the ecosystem a range
    states even though it states no delta, and revision 3457db3c classifies one on that alone.
    What still reaches this branch is a head commit whose trailer could not be read, which is a
    genuine and fail-closed state rather than a residue -- it just stopped being the common one.

    THE TITLE IS CONSULTED ONLY WHERE THE METADATA IS ABSENT, and that bound is deliberate. The
    gate itself reads the metadata trailer, never the title, so a subject whose trailer IS
    readable is decided in the gate's own terms -- which is what makes this detector's answer a
    statement about the gate rather than a second opinion about the pull request.
    """
    subject = f"{pending.repository}#{pending.number}"
    green = is_green(pending)
    if pending.update is None:
        if bump_of(pending.title) is None:
            return (), (
                Finding(
                    EXCEPTION_UPDATE_TYPE_UNPARSEABLE,
                    subject,
                    "no update metadata on the head commit and no single version delta in the "
                    "title, so there is nothing here for any rule to be applied to",
                ),
            )
        return (
            Finding(
                STALL_METADATA_UNREADABLE,
                subject,
                "no update metadata on the head commit, so eligibility cannot be decided",
            ),
        ), ()
    if not rule.permits(pending.update.update_type, pending.update.ecosystem):
        return (), ()
    if not green:
        return (), ()
    if not pending.armed:
        # The declared intent may legitimately be absent -- a requirement range states none, and
        # revision 3457db3c permits one on its ecosystem alone -- so this says so rather than
        # interpolating `None` into a sentence a person has to read. It is the line this detector
        # emits for every already-open update after a gate edit, which fires no `pull_request`
        # event and so arms nothing that already exists.
        declared = pending.update.update_type or "no version delta stated"
        return (
            Finding(
                STALL_ELIGIBLE_NOT_ARMED,
                subject,
                f"{pending.update.dependency} ({declared}) is permitted by the installed rule "
                "and every concluded check passed, but nothing armed it",
            ),
        ), ()
    if pending.last_concluded_at is None:
        return (), ()
    if (now - pending.last_concluded_at).total_seconds() < settle_seconds:
        return (), ()
    return (
        Finding(
            STALL_ARMED_NOT_LANDED,
            subject,
            f"armed and green since {pending.last_concluded_at.isoformat()}, still open",
        ),
    ), ()


def branch_status(commit: str, runs: tuple[WorkflowRun, ...]) -> BranchStatus:
    """Reduce every workflow run at a commit to one of the four branch states.

    PER WORKFLOW, THEN ACROSS WORKFLOWS, and that order is the substance. "The newest concluded
    run at the tip" reads as one run, and one run is the wrong unit: a repository runs several
    workflows over one commit, so a newer green run of workflow B would hide a red workflow A and
    report a broken branch as healthy. Each workflow's own newest decided run is its current
    answer, and the branch is failing if ANY of them decided against the commit.

    THE SKIP IS THE MECHANISM, not the choice of set, and the two are easy to confuse. A
    no-verdict conclusion is skipped BEFORE the newest-run comparison, so a cancelled re-run
    cannot become a workflow's current answer and bury the verdict it superseded -- which is the
    behaviour that held three clean bumps for four days when the Actions quota ran out. Delete
    that skip and a cancelled run newer than a failure reports the branch as fine.

    `REFUSING_CONCLUSIONS` rather than `FAILING_CONCLUSIONS` is then a NAMING choice with no
    behavioural difference: the skip has already removed everything the two sets disagree about
    (`REFUSING == FAILING - NO_VERDICT` by construction), so no test can tell them apart here.
    It is named this way because it is the safer one to inherit -- if the skip is ever removed,
    `REFUSING` still declines to call a cancelled run a failure and `FAILING` would not.

    A run whose conclusion is unknown to BOTH vocabularies -- something the platform has not yet
    invented -- lands in `passing` and accuses nobody. That is the fail-toward-quiet direction,
    and it is the right one for a control whose findings a person acts on. Note this is the
    OPPOSITE polarity from `NO_VERDICT_CONCLUSIONS`, deliberately: there an unknown word must
    read as a verdict so a landing lane refuses; here an unknown word must not manufacture a
    finding about somebody's branch.
    """
    newest: dict[str, WorkflowRun] = {}
    in_flight: set[str] = set()
    for run in runs:
        if run.status != "completed":
            in_flight.add(run.path)
            continue
        if run.conclusion is None or run.conclusion in NO_VERDICT_CONCLUSIONS:
            continue
        current = newest.get(run.path)
        if current is None or run.updated_at > current.updated_at:
            newest[run.path] = run
    failing = tuple(
        sorted(path for path, run in newest.items() if run.conclusion in REFUSING_CONCLUSIONS)
    )
    passing = tuple(
        sorted(path for path, run in newest.items() if run.conclusion not in REFUSING_CONCLUSIONS)
    )
    if failing:
        state = BRANCH_FAILING
    elif passing:
        state = BRANCH_PASSING
    elif in_flight:
        state = BRANCH_IN_FLIGHT
    else:
        state = BRANCH_UNVERIFIED
    return BranchStatus(
        commit=commit,
        state=state,
        failing=failing,
        passing=passing,
        in_flight=tuple(sorted(in_flight)),
    )


def audit_branch(repository: str, branch: BranchStatus) -> tuple[Finding, ...]:
    """Detector C's verdict: one finding when the tip is failing, and nothing otherwise.

    The quiet states produce no line here on purpose. They are carried on the observation instead
    (`audit_observation`), where a reader can see WHICH quiet answer a repository gave without the
    control having to exit non-zero about it every night for eight repositories.
    """
    if branch.state != BRANCH_FAILING:
        return ()
    return (
        Finding(
            BRANCH_NOT_GREEN,
            f"{repository}@{branch.commit[:12]}",
            "the default branch tip is red: "
            + "; ".join(branch.failing[:MAX_LIST])
            + " decided against it",
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
    branch: BranchStatus | None,
    settle_seconds: int = SETTLE_SECONDS,
) -> RepoAudit:
    """All three detectors over one repository. Never raises on a shape it did not expect.

    It DOES propagate an orchestrator that could not be read, which is not the same thing: the
    caller turns that into the incomplete exit code, where a swallowed one would report a
    repository as clean on the strength of a question nobody managed to ask.

    `branch` HAS NO DEFAULT, deliberately, and it is the only parameter here without one. `None`
    is a real answer -- the branch could not be asked -- and it is the answer that reaches
    `unavailable`. A default would let a caller omit the read entirely and get the same value,
    so the fail-closed case and the forgotten case would be spelled identically. Requiring it
    makes every caller say which one it means.
    """
    findings: list[Finding] = []
    caveats: list[Finding] = []
    exceptions: list[Finding] = []
    permitted = 0
    factory = 0
    # A branch nobody could ask about is a missing answer, exactly as an unreadable orchestrator
    # is. It reaches the incomplete exit code rather than a clean one, so a repository whose tip
    # went unread is never reported as green.
    unreadable = branch is None
    if branch is not None:
        findings.extend(audit_branch(repository, branch))
    for facts in landings:
        landing_findings, landing_caveats, landing_exceptions = audit_landing(facts)
        try:
            factory_findings, factory_caveats = audit_factory_landing(facts, units)
        except LedgerReadError:
            # The orchestrator could not be asked about THIS landing. Caught here rather than left
            # to the caller's blanket catch, which discards the rule and stall findings the same
            # pass already computed without asking anything -- and those are the ones that need no
            # orchestrator at all. The repository's answer is still missing, so `unavailable`
            # carries it to the incomplete exit code; what is not lost is everything else measured.
            unreadable, factory_findings, factory_caveats = True, (), ()
        record = _permitted_by(facts)
        basis = record.get("basis") if record is not None else None
        # Two subjects, two denominators, kept apart. Folding them would put factory landings
        # behind a key whose name says `rule`, and a count nobody can attribute is not a
        # denominator -- it is a number.
        permitted += basis == BASIS_RULE
        factory += basis == BASIS_FACTORY
        findings.extend(landing_findings + factory_findings)
        caveats.extend(landing_caveats + factory_caveats)
        exceptions.extend(landing_exceptions)

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
            pending_findings, pending_exceptions = audit_pending(
                candidate, rule, now, settle_seconds
            )
            findings.extend(pending_findings)
            exceptions.extend(pending_exceptions)

    return RepoAudit(
        repository=repository,
        rule_revision=rule_revision,
        landings_audited=len(landings),
        permitted_landings=permitted,
        factory_landings=factory,
        pending_audited=len(pending),
        branch=branch,
        findings=tuple(findings),
        caveats=tuple(caveats),
        exceptions=tuple(exceptions),
        unavailable=unreadable,
    )


def _fit(facts: dict[str, Any]) -> dict[str, Any]:
    """Trim the three variable-length lists until the record fits the orchestrator's byte bound.

    LEAST URGENT FIRST. The branch's workflow-path lists go before anything else: the `state`
    beside them is the verdict and is a scalar that always survives, so the paths are detail in a
    way an exception is not. Then an exception, a permanent property of a subject that says the
    same thing tomorrow. Then a caveat, which qualifies evidence. A finding asserts a violation,
    and the violation is the thing that must survive. All the entry lists keep their true counts
    beside them, so a trim is visible rather than silent.

    THE BRANCH LISTS ARE IN THE LOOP RATHER THAN OUTSIDE IT, and that is what keeps this
    function's guarantee. A fixed block added to `facts` that this loop cannot reach would be
    evicting findings on its behalf -- the exact inversion the paragraph above forbids -- and,
    once the three entry lists emptied, would fall out of the `else` still over the bound.
    """
    branch = facts.get("default_branch") or {}
    while len(json.dumps(facts, sort_keys=True, separators=(",", ":"))) > MAX_FACT_BYTES:
        for entries in (
            branch.get("passing") or [],
            branch.get("in_flight") or [],
            branch.get("failing") or [],
            facts["exceptions"],
            facts["caveats"],
            facts["findings"],
        ):
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
            # DETECTOR C'S ANSWER, RECORDED WHATEVER IT IS -- including the three answers that are
            # not findings. That is the point of putting it here rather than in `caveats`: a
            # caveat prints a line every night for every repository whose tip nothing has decided
            # on yet, which under the current arming identity is the ordinary state, and a report
            # known to be noise is one a real finding arrives inside. `null` means the branch
            # could not be asked, which `unavailable` beside it already says is not a pass.
            "default_branch": (
                None
                if audit.branch is None
                else {
                    "commit": audit.branch.commit,
                    "state": audit.branch.state,
                    "failing": list(audit.branch.failing[:MAX_LIST]),
                    "passing": list(audit.branch.passing[:MAX_LIST]),
                    "in_flight": list(audit.branch.in_flight[:MAX_LIST]),
                }
            ),
            "landings_audited": audit.landings_audited,
            "rule_permitted_landings": audit.permitted_landings,
            "factory_landings": audit.factory_landings,
            "pending_audited": audit.pending_audited,
            "findings_found": len(audit.findings),
            "caveats_found": len(audit.caveats),
            "exceptions_found": len(audit.exceptions),
            "findings": [finding.as_fact() for finding in audit.findings[:MAX_LIST]],
            "caveats": [caveat.as_fact() for caveat in audit.caveats[:MAX_LIST]],
            "exceptions": [entry.as_fact() for entry in audit.exceptions[:MAX_LIST]],
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
            f"{audit.repository}: {len(audit.findings)} finding(s) and "
            f"{len(audit.exceptions)} exception(s) over "
            f"{audit.permitted_landings} rule-permitted landing(s), "
            f"{audit.factory_landings} factory landing(s) and "
            f"{audit.pending_audited} open update(s)"
            # The branch state belongs in the SUMMARY as well as the facts, because the summary is
            # what a person reads in a listing. `unread` rather than an omission: a missing clause
            # would read as a state nobody thought to print.
            + f"; default branch {audit.branch.state if audit.branch else 'unread'}"
            + (" [UNAVAILABLE]" if audit.unavailable else "")
        )[:512],
        "facts": facts,
        "payload_digest": None,
    }
