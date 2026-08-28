"""The landing record: what a landing may honestly assert, and nothing more.

TWO RULES SHAPE EVERY LINE BELOW.

**A record must not assert a condition nobody checked at the time.** Most of this estate's history
predates the gate workflow entirely, so the honest basis for those landings is *a person decided*
-- not a reconstructed set of conditions. Two values that look reconstructible are deliberately
absent: the branch's required-check list (GitHub keeps no history of it, so "required at the time"
is unknowable) and the repository's App Brain landing classification (nothing at landing time
consults it -- it is why the repository has a gate at all, not a condition of any one landing --
and an external re-determination would change these facts and break the re-run).

**Re-running over the same history must change nothing.** So `source_reference` is the landing's
own immutable identity and every value in `facts` comes from upstream: the landing time is
GitHub's, never the pass's. The `idempotency_key` is content-addressed over the facts, which
routes a changed fact to the orchestrator's same-source/different-facts branch -- a loud
`observation_conflict` -- rather than to the idempotency-replay branch. Facts that drift are
supposed to be loud.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

from landing_ledger.model import Landing

SOURCE_SYSTEM = "github"
TRUST_CLASSIFICATION = "delivery_system"
SUBJECT_TYPE = "repo"
STATUS = "observed"
SEVERITY = "info"
DECISION = "ADR-0016"

# The orchestrator bounds `facts` at 4096 encoded bytes, 512-character strings, 30-item lists and
# 64-character keys (`services/observations.py`). Staying under the byte bound is structural here,
# not hoped-for: `_fit` drops file paths until the record fits, deterministically, so the same
# landing always encodes to the same bytes.
MAX_FACT_BYTES = 4096
MAX_LIST = 30
MAX_TITLE = 240

# A landing that reached the branch through a pull request the gate workflow permitted.
BASIS_RULE = "auto_merge_rule"
# A landing a person decided to make. Every pre-gate merge in this estate is one of these.
BASIS_HUMAN = "human"
# Pushed straight at the branch. There is no permission basis, and that is the finding.
BASIS_NONE = "none"
# The factory landing its own pull request, whose acceptance criteria were resolved from evidence
# the orchestrator observed and where landing changes nothing already serving (ADR-0020). Devon's
# name, and the name carries the constraint: it cannot describe a landing that deploys without
# saying something false.
BASIS_FACTORY = "factory-approved-no-deploy"
# ADR-0019 increment 5b. A landing the orchestrator made into a repository where landing changes
# something already serving, permitted by a change record the estate approved by conformance to a
# policy version a human pinned. Named for what it rests on rather than for who performed it,
# because the record and the version are the parts that can be looked up years later.
BASIS_CHANGE_RECORD = "change-record-policy"
# Merged by a machine with no gate run to account for it. Never fabricate a basis: say so.
BASIS_UNATTRIBUTED = "unattributed"

# Every basis `basis_of` may return. Held here so a branch added to the cascade without a name --
# or a name added without a branch -- is a test failure rather than a value nobody can interpret.
#
# THIS PACKAGE IS DELIBERATELY LEFT OUTSIDE `test_cross_boundary_vocabulary`, and the reason is
# that extending it would not have caught the defect it is being proposed for. That guard finds
# module-level string COLLECTIONS used in a membership test; the thing that had silently duplicated
# here was a SCALAR (`BASIS_RULE`, defined independently in `audit.py` until WS-P3.7 Increment 5),
# which its predicate cannot see in any root. Widening the root to catch it would be a check that
# is correct about the wrong noun -- the shape this estate keeps finding -- and the only collection
# it would then discover is `audit.FAILING_CONCLUSIONS`, whose producer is GitHub's own API. That
# is the guard's own documented blind spot, so its registry entry would be prose rather than a pin.
# What the scalar needed was ONE definition, which it now has: `audit.py` imports these names, and
# an import cannot drift. What the vocabulary needed was a total-coverage assertion against the
# cascade, which `tests/landing_ledger/test_record.py` now carries.
BASES = (
    BASIS_NONE,
    BASIS_RULE,
    BASIS_HUMAN,
    BASIS_FACTORY,
    BASIS_CHANGE_RECORD,
    BASIS_UNATTRIBUTED,
)

NO_BASIS_REASON = "pushed to the branch with no pull request; nothing recorded a permission"
UNATTRIBUTED_REASON = "landed by a machine with no gate run observed on the pull request"
# EVERY STRING THAT REACHES `facts` IS FROZEN AT THE FIRST LANDING THAT CARRIES IT. Correcting one
# afterwards re-encodes the row, which is an `observation_conflict` on a landing where nothing
# actually changed -- so this says only what stays true. In particular it does NOT name where the
# claim was read from: a first draft said "from the landing commit", and the fall-back added hours
# later can take it from the pull request's head instead.
FACTORY_REASON = (
    "landed by the factory, claiming a work unit whose criteria it says were met; the claim is the "
    "runner's own and is checked against the orchestrator by the audit"
)
# Says only what stays true, for the reason above it: nothing dated, nothing counted, and no claim
# that the record has been verified -- because it has not been. The honest sentence is what the
# landing asserts plus who could check it, and the second half is deliberately future-tense-free.
CHANGE_RECORD_REASON = (
    "landed by the orchestrator against a change record the estate approved by conformance to a "
    "pinned policy version; the record and the version are named so the approval can be looked up "
    "in change-manager, and no detector re-evaluates it here"
)


def is_machine(login: str | None) -> bool:
    return login is not None and login.endswith("[bot]")


def basis_of(landing: Landing) -> str:
    """Which permission basis this landing has -- fail-closed in every direction.

    The rule basis needs BOTH halves: the gate workflow ran and succeeded on the pull request,
    AND a machine performed the landing. Either half alone is satisfiable by something else --
    the gate runs on every Dependabot pull request including the major bumps it deliberately
    leaves to a person, and a machine identity could land something by some route this adapter
    cannot see. Anything that satisfies neither `human` nor `rule` is `unattributed`; it is never
    rounded down to the nearest basis that fits.

    THE FACTORY BRANCH MUST SIT AFTER `auto_merge_rule`, AND THAT ORDER IS THE ONLY ONE THAT IS
    LOAD-BEARING -- measured by mutation rather than asserted, because a first draft of this note
    named the wrong pair. A landing the gate actually permitted has a rule to be re-evaluated
    against, which is a stronger answer than a claim, and a landing can carry both. Moving the
    factory branch above the rule branch reclassifies it, and a test reds.

    Its position relative to `human` is NOT load-bearing: the two are mutually exclusive by
    `is_machine`, so swapping them changes nothing and no test can tell. What actually keeps every
    factory pull request before 2026-08-10 recorded as `human` -- they were opened by the runner,
    so they carry the claim, and Devon merged them -- is that conjunct, not the order.

    It sits before `unattributed` because a claim plus a machine merger is strictly more than
    `unattributed` can say.

    IT IS KEYED ON THE CLAIM AND A MACHINE MERGER, NOT ON THE FACTORY APP'S OWN LOGIN, and the
    choice is about which way a mistake fails. An exact-login literal that stopped matching -- a
    renamed App, a second identity -- would drop the landing to `unattributed`, where the audit
    returns early and says nothing. Keying on the claim means a landing that claims a unit it has
    no right to reaches the audit and becomes a FINDING. Prefer the loud failure: the point of this
    basis is that it is checked.

    THAT REMOVES ONE LITERAL AND NOT ALL OF THEM, and the residual is stated rather than implied.
    `is_machine` is itself a login literal -- the `[bot]` suffix -- so a machine that lands without
    it is read as a person and recorded `human`, which asserts something false and which no
    detector will ever look at. GitHub does answer this properly: `merged_by.type` is `Bot`, and
    this adapter reads only the login. Closing it means carrying that field through the record,
    which is a change to what every landing asserts and belongs with a reason to make it.
    """
    if landing.pull_request is None:
        return BASIS_NONE
    if (
        landing.rule is not None
        and landing.rule.outcome == "success"
        and is_machine(landing.landed_by)
    ):
        return BASIS_RULE
    if landing.landed_by is not None and not is_machine(landing.landed_by):
        return BASIS_HUMAN
    if landing.claim is not None and is_machine(landing.landed_by):
        return BASIS_FACTORY
    if landing.policy is not None and is_machine(landing.landed_by):
        return BASIS_CHANGE_RECORD
    return BASIS_UNATTRIBUTED


def permitted_by(landing: Landing) -> dict[str, Any]:
    basis = basis_of(landing)
    if basis == BASIS_NONE:
        return {"basis": basis, "reason": NO_BASIS_REASON}
    record: dict[str, Any] = {
        "basis": basis,
        "landed_by": landing.landed_by,
        # The true count, kept beside the list the way `files_changed` is, so a consumer that
        # sees fewer entries than this knows the list was trimmed rather than that the landing
        # ran fewer checks.
        "checks_observed": len(landing.checks),
        "checks": [asdict(check) for check in landing.checks[:MAX_LIST]],
    }
    if basis == BASIS_UNATTRIBUTED:
        record["reason"] = UNATTRIBUTED_REASON
        return record
    if basis == BASIS_FACTORY:
        # ADR-0020 point 4 says this basis carries "the unit id, the package revision, the criteria
        # and how each resolved". The first two are here; the last two deliberately are NOT, and
        # this is a considered divergence from the ADR's letter rather than an omission. Recording
        # how each criterion resolved would mean either asking the orchestrator while RECORDING --
        # which makes the facts a function of orchestrator state, so an unchanged reality re-encodes
        # differently on a later pass and conflicts -- or copying the runner's own account of its
        # own compliance out of the pull request, which is the assertion this basis exists to stop
        # re-recording. So the record carries the CLAIM, and `audit.audit_factory_landing` resolves
        # it against the orchestrator's durable rows, where a mismatch is a finding.
        assert landing.claim is not None
        record["reason"] = FACTORY_REASON
        record["work_unit"] = landing.claim.work_unit
        record["package_revision"] = landing.claim.package_revision
        return record
    if basis == BASIS_CHANGE_RECORD:
        assert landing.policy is not None
        record["reason"] = CHANGE_RECORD_REASON
        record["change_record"] = landing.policy.change_record
        record["policy_version"] = landing.policy.policy_version
        return record
    if basis == BASIS_HUMAN or landing.rule is None:
        return record
    record["decision"] = DECISION
    record["rule_path"] = landing.rule.path
    record["rule_revision"] = landing.rule.revision
    record["rule_run"] = landing.rule.run
    record["rule_outcome"] = landing.rule.outcome
    if landing.update is not None:
        # THE KEY BEING PRESENT AND NULL IS A DIFFERENT ASSERTION FROM THE KEY BEING ABSENT, and
        # `audit_landing` keys on exactly that difference. Present with `None` says the update
        # bot declared no version delta -- which is the ordinary shape of a requirement range,
        # and which revision 3457db3c permits deliberately. All three keys ABSENT says this
        # program could not read the trailer at all, so no rule's condition can be re-read and
        # the landing is a finding. Before 2026-08-28 the reader collapsed the two, and every
        # no-delta landing arrived looking unreadable.
        record["dependency"] = landing.update.dependency
        record["ecosystem"] = landing.update.ecosystem
        record["update_type"] = landing.update.update_type
    return record


def what_changed(landing: Landing) -> dict[str, Any]:
    """The mechanical half, read from the API rather than parsed out of a title.

    Versions are deliberately absent. The only API-sourced version is Dependabot's own
    `dependency-version` trailer, and it is demonstrably wrong -- intent-packages #50 carries
    `dependency-version: 0.16.0` in the same commit message whose subject says 0.16.1. The
    landed content is the diff, so `commit` and `files` are what answer "what changed", and
    `title` carries Dependabot's own words verbatim.
    """
    return {
        "repository": landing.repository,
        "base_ref": landing.base_ref,
        "commit": landing.commit,
        "head_commit": landing.head_commit,
        "pull_request": landing.pull_request,
        "title": landing.title.strip()[:MAX_TITLE],
        "files_changed": landing.files_changed,
        "files": list(landing.files[:MAX_LIST]),
    }


def _encoded_size(facts: dict[str, Any]) -> int:
    return len(json.dumps(facts, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _fit(facts: dict[str, Any]) -> dict[str, Any]:
    """Trim the two variable-length lists, longest entry first, until the record fits.

    Deterministic, so the same landing always encodes identically and a re-run dedups rather than
    conflicting. File paths go before checks because the diff detail is the least load-bearing
    thing here and the checks are the permission evidence. Both counts stay beside their lists
    (`files_changed`, `checks_observed`), so a trim is visible rather than silent.

    Only these two are variable-length -- every other field is bounded by its own cap -- so the
    loop terminates, and it terminates BELOW the bound rather than merely trying: a landing with
    thirty maximal checks and a maximal title encodes to 4241 bytes with no files left to drop,
    which is how this was found.
    """
    while _encoded_size(facts) > MAX_FACT_BYTES:
        for entries in (facts["what_changed"]["files"], facts["permitted_by"].get("checks") or []):
            if entries:
                entries.remove(max(entries, key=lambda entry: len(json.dumps(entry))))
                break
        else:
            return facts
    return facts


def fact_digest(facts: dict[str, Any]) -> str:
    canonical = json.dumps(facts, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# One type for one kind of event. Every row this adapter writes says the same thing -- a commit
# reached the default branch -- and the ROUTE it took is `permitted_by`, not a second type.
#
# The earlier split (`github_pr` with a pull request, `inventory` without) was the honest version
# of a missing vocabulary member, and it was wrong in both directions once one existed. `github_pr`
# already means "a fact about a pull request bound to a work unit" in the reconciliation lane
# (`reconciliation_runner/facts.py` writes it, `services/reconciliation_detection.py` reads it to
# decide which observation is current); those rows are subject_type `work_unit` keyed by unit id
# and a landing is subject_type `repo` keyed by `owner/name`, so the two never actually collide --
# but relying on two namespaces staying disjoint is a coincidence, not a design. `inventory`
# meanwhile asserted nothing at all.
#
# `landing` was added to OBSERVATION_TYPES by migration 0022 (Devon, 2026-08-07).
OBSERVATION_TYPE = "landing"


def observation_type_for(landing: Landing) -> str:
    """Every landing is a landing, whatever route it took to get there."""
    return OBSERVATION_TYPE


def landing_observation(landing: Landing) -> dict[str, Any]:
    facts = _fit(
        {
            "what_changed": what_changed(landing),
            "permitted_by": permitted_by(landing),
        }
    )
    reference = f"landing:{landing.repository}@{landing.commit}"
    return {
        # Content-addressed, so an unchanged re-run replays and a CHANGED fact reaches the
        # orchestrator's same-source/different-facts branch instead of its replay branch.
        "idempotency_key": f"{reference}:{fact_digest(facts)}",
        "expected_version": 0,
        "source_system": SOURCE_SYSTEM,
        # The landing's own identity, never content-addressed: a commit on a branch is immutable,
        # so one landing is one row forever. Content-addressing it would let drifted facts open a
        # quiet second row instead of raising.
        "source_reference": reference,
        "source_url": f"https://github.com/{landing.repository}/commit/{landing.commit}",
        "trust_classification": TRUST_CLASSIFICATION,
        "subject_type": SUBJECT_TYPE,
        "subject_reference": landing.repository,
        "environment": None,
        "observation_type": observation_type_for(landing),
        "status": STATUS,
        "severity": SEVERITY,
        # Upstream's clock, never the pass's: with a wall-clock timestamp every pass would
        # recompute a different fact hash for unchanged reality and conflict, forever.
        "observed_at": landing.landed_at.isoformat(),
        "summary": (
            f"{landing.commit[:12]} landed on {landing.base_ref} of {landing.repository} "
            f"({basis_of(landing)})"
        )[:512],
        "facts": facts,
        "payload_digest": None,
    }
