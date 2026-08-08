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
# Merged by a machine with no gate run to account for it. Never fabricate a basis: say so.
BASIS_UNATTRIBUTED = "unattributed"

NO_BASIS_REASON = "pushed to the branch with no pull request; nothing recorded a permission"
UNATTRIBUTED_REASON = "landed by a machine with no gate run observed on the pull request"


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
    if basis == BASIS_HUMAN or landing.rule is None:
        return record
    record["decision"] = DECISION
    record["rule_path"] = landing.rule.path
    record["rule_revision"] = landing.rule.revision
    record["rule_run"] = landing.rule.run
    record["rule_outcome"] = landing.rule.outcome
    if landing.update is not None:
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
