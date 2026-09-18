"""The durable fact this producer files before it proposes (G1+G2, ADR-0026 decision 2).

**THE OBSERVATION IS THE FACT AND THE RECORD IS A DECISION ABOUT IT**, and keeping those apart is
the whole reason this module exists rather than the proposal simply carrying more fields. What is
observed here is that an open dependency update exists on a repository and names two versions.
Whether the estate lands it by itself, and whether it therefore becomes work, are judgments read
from a policy version that moves -- they belong to `_reasoning` on the change record, which a
person approves, and they are deliberately absent from every string below.

That is not a stylistic preference; it is what lets the row stay frozen. The judgment is re-taken
on every pass, and a fact that carried it would change under a stable reference the first time the
declaration moved.

**THE REFERENCE IS THE BUMP'S OWN IDENTITY, NOT A CONTENT DIGEST**, which is the landing ledger's
arrangement rather than the revision watcher's, and the difference is what the subject does. What
an application SERVES is expected to move, so keying on the application alone would make the pass
after any change a conflict; a bump at a fixed (repository, pull request, dependency, from, to) is
immutable in the same way a commit on a branch is, so a changed fact about it means something is
wrong and raising is the point. The update bot rewrites a pull request IN PLACE when a newer
version appears, so BOTH versions are in the reference: that rewrite is a different bump and takes
a different row, rather than colliding on this one.

**THE RESIDUAL, because it is real and the estate has paid for it once.** `normalized_fact_hash`
is computed over the whole stored command -- `summary` and `status` included, not `facts` alone --
so rewording `summary_of` re-derives a fact that is already frozen, and every currently-open bump
would then reach the same-source/different-facts branch and refuse. There is no lookback here to
shrink: the pass sees every open update. So a change to the prose below is a migration-shaped act,
not an edit, and the fix for a wrong cause is a new bump rather than a correction.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from landing_ledger.model import PendingUpdate
from landing_ledger.titles import Bump

SOURCE_SYSTEM = "bump_proposer"
OBSERVATION_TYPE = "dependency_update"
TRUST_CLASSIFICATION = "delivery_system"

# `repo`, spelled the way the landing ledger, the activation sweep and the pin watcher spell it,
# so one repository is ONE subject across all four producers. A second spelling would make it two
# and no query would join them.
SUBJECT_TYPE = "repo"

# `observed`, and this lane is exactly what that member is for: it asserts that something is the
# case, not that a check passed or failed. `passed` would claim a verdict nothing here took, and
# every other member of the vocabulary is a health word about a running thing.
STATUS = "observed"
SEVERITY = "info"

MAX_SUMMARY = 512

# `CommandBase.idempotency_key` is `max_length=200`. It is mirrored here because exceeding it is a
# FastAPI 422 raised BEFORE any service code runs -- so no named error and no service test can
# reach it, which is the exact shape that refused twelve candidate rows on the binding lane's
# first live pass. Held to the real model by `tests/bump_proposer/test_observation.py`.
MAX_IDEMPOTENCY_KEY = 200


class ObservationUncomposable(ValueError):
    """This bump cannot be stated as an observation, so nothing about it may be proposed.

    A `ValueError` because it is a fact about the subject rather than a broken tool, and named
    because the alternative is a 422 whose body names a field location and not a pull request.
    """


def bump_facts(pending: PendingUpdate, bump: Bump) -> dict[str, Any]:
    """Everything that stays true of this bump for as long as it is this bump.

    NOTHING THAT MOVES GOES IN HERE. `head_commit` moves on a rebase, `armed` moves when the
    estate arms or disarms the update, and the check conclusions move whenever a job is re-run --
    each of them would re-derive a frozen fact and refuse. What is left is the delta itself, which
    is also what the reference names, so a conflict here can only ever be a reworded summary.
    """
    if pending.update is None:  # pragma: no cover - `_consider` refuses these before this runs
        raise ObservationUncomposable(
            f"{pending.repository}#{pending.number} carries no dependency metadata, "
            "so there is no bump to state"
        )
    return {
        "repository": pending.repository,
        "pull_request": pending.number,
        "dependency": pending.update.dependency,
        "ecosystem": pending.update.ecosystem,
        "from_version": bump.from_version,
        "to_version": bump.to_version,
        "kind": bump.kind,
    }


def fact_digest(facts: dict[str, Any]) -> str:
    canonical = json.dumps(facts, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def reference_for(pending: PendingUpdate, bump: Bump) -> str:
    """The bump's own identity: where it is open, and which delta it names.

    BOTH versions, not just the target. The bot rewrites in place, and while a newer target is the
    common case, a moved base under a stable target is the one that would otherwise refuse this
    pull request forever with no repair route.
    """
    dependency = pending.update.dependency if pending.update is not None else "unknown"
    return (
        f"dependency-update:{pending.repository}#{pending.number}:"
        f"{dependency}@{bump.from_version}->{bump.to_version}"
    )


def summary_of(pending: PendingUpdate, bump: Bump) -> str:
    """What is true, and nothing about what should happen to it. See the module docstring."""
    dependency = pending.update.dependency if pending.update is not None else "unknown"
    return (
        f"{pending.repository} carries an open dependency update of {dependency} "
        f"from {bump.from_version} to {bump.to_version} (pull request {pending.number})"
    )[:MAX_SUMMARY]


def bump_observation(pending: PendingUpdate, bump: Bump) -> dict[str, Any]:
    facts = bump_facts(pending, bump)
    reference = reference_for(pending, bump)
    key = f"{reference}:{fact_digest(facts)}"
    if len(key) > MAX_IDEMPOTENCY_KEY:
        raise ObservationUncomposable(
            f"the observation key for {pending.repository}#{pending.number} is {len(key)} "
            f"characters, over the {MAX_IDEMPOTENCY_KEY} the route accepts"
        )
    return {
        # Content-addressed over the facts, so an unchanged re-run replays and a CHANGED fact
        # reaches the orchestrator's same-source/different-facts branch instead of opening a quiet
        # second row. The reference itself is NOT content-addressed -- see the module docstring.
        "idempotency_key": key,
        "expected_version": 0,
        "source_system": SOURCE_SYSTEM,
        "source_reference": reference,
        "source_url": f"https://github.com/{pending.repository}/pull/{pending.number}",
        "trust_classification": TRUST_CLASSIFICATION,
        "subject_type": SUBJECT_TYPE,
        "subject_reference": pending.repository,
        "environment": None,
        "observation_type": OBSERVATION_TYPE,
        "status": STATUS,
        "severity": SEVERITY,
        # WHEN THE BUMP APPEARED, never when the pass ran. The orchestrator hashes the whole
        # stored command, `observed_at` included, so a wall clock would give unchanged reality a
        # new fact hash every pass and refuse from the second one on. `opened_at` is the only
        # timestamp this reader carries that does not move: `last_concluded_at` advances whenever
        # a job re-runs. It is imprecise in one direction and the imprecision is stated rather
        # than hidden -- a bump the bot rewrote in place appeared later than its pull request did,
        # and the row for that rewrite carries the pull request's opening.
        "observed_at": pending.opened_at.isoformat(),
        "summary": summary_of(pending, bump),
        "facts": facts,
        "payload_digest": None,
    }
