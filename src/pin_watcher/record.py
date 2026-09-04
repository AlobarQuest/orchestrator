"""The pin record: what one caller may honestly assert about the runner it would execute.

TWO RULES, AND THEY ARE THE ACTIVATION SWEEP'S, WHICH TOOK THEM FROM THE LANDING LEDGER.

**A record must not assert a condition nobody checked.** This lane reads a workflow file and asks
GitHub where its revision sits. It does not know whether a dispatch has happened, would succeed, or
would produce a different result -- so nothing here says so. A stale caller is reported as a stale
caller, never as a run that went wrong.

**Re-running over unchanged reality must change nothing.** `observed_at` is therefore the committer
date of the PINNED revision, never the moment the pass ran. The orchestrator's replay check hashes
the whole command, `observed_at` included (`services/observations.py::_fact_identity`), so a wall
clock would give unchanged reality a new fact hash every pass -- and because the source reference
would be the same, that reaches the same-source/different-facts branch and raises
`observation_conflict` permanently, from the second pass onward. A clock that is a function of the
facts is the only one that replays. When the pass ran is recorded anyway: the orchestrator stamps
`received_at`, which is a better answer than anything this program could assert about its own clock.

An `unpinned` or `unresolvable` caller has no pinned revision and therefore no date of its own, so
it falls back to the RECOMMENDATION's committer date. That is equally a function of the facts --
the recommendation is in them -- and it moves only when the recommendation moves, which is the
property the rule above actually requires.

**THE DIGEST COVERS THE WHOLE COMPOSED RECORD, NOT JUST `facts`.** Content-addressing `facts` alone
is the obvious reading and is a strict subset of what the orchestrator compares: because the
reference is also the idempotency key, the server's first lookup is by that key and on a hit it
compares the entire stored command -- `summary`, `status`, `severity`, `source_url` and five more,
every one producer-derived and none of them in `facts`. Rewording one clause of `summary_of` would
otherwise make the next pass an `idempotency_conflict` for every caller that had not moved, which
for a healthy chain is all of them. The activation sweep paid for this discovery; this lane simply
inherits the answer.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pin_watcher.compare import Caller
from pin_watcher.github import CALLER_PATH, RECOMMENDATION_PATH, RUNNER_REPOSITORY

SOURCE_SYSTEM = "pin_watcher"
OBSERVATION_TYPE = "caller_pin"
TRUST_CLASSIFICATION = "delivery_system"

# `repo`, keyed by `owner/name`, which is what the landing ledger and the activation sweep already
# write. The three cannot collide: uniqueness is on `(source_system, source_reference)`, and the
# source systems differ.
SUBJECT_TYPE = "repo"

# A caller at the recommendation PASSED. Any other state is DEGRADED rather than FAILED: a stale
# pin is an ordinary, recoverable state of a repository -- the factory would run a runner nobody
# chose, which is worth a person's attention and is not a failure of anything.
STATUS_CURRENT = "passed"
STATUS_FINDING = "degraded"
SEVERITY_CURRENT = "info"
SEVERITY_FINDING = "warning"


def pin_facts(caller: Caller, recommended: str) -> dict[str, Any]:
    """Everything the record says, bounded by construction.

    Every value is a repository name, a path, a revision, a state name or a count, so the record
    is small by shape rather than by trimming -- there is no variable-length member to fit. No key
    contains a fragment the orchestrator's secret detector reads as metadata
    (`services/observations.py::SECRET_KEY_PARTS` matches nine substrings against key NAMES).
    """
    facts: dict[str, Any] = {
        "caller": {
            "repository": caller.repository,
            "workflow_path": CALLER_PATH,
            "pin": caller.pin,
        },
        "recommendation": {
            "repository": RUNNER_REPOSITORY,
            "path": RECOMMENDATION_PATH,
            "revision": recommended,
        },
        "state": caller.state,
    }
    # Present only when a comparison was actually made, so their absence is a statement rather
    # than a zero that would read as "no distance".
    if caller.behind_by is not None or caller.ahead_by is not None:
        facts["measured"] = {"behind_by": caller.behind_by, "ahead_by": caller.ahead_by}
    return facts


def summary_of(caller: Caller, recommended: str) -> str:
    """One sentence, computed from the same state the facts carry.

    A caller is described as current only when its state IS current -- the guard is structural
    rather than a clause that remembers to check.
    """
    short = recommended[:7]
    if not caller.is_finding:
        return (
            f"{caller.repository} pins the runner at the recommended {short}, "
            f"so a dispatch would execute the revision the estate chose."
        )
    if caller.state == "unpinned":
        return (
            f"{caller.repository} names '{caller.pin}' rather than a revision, so what a dispatch "
            f"executes is whatever that ref holds at the moment it fires."
        )
    if caller.state == "unresolvable":
        return (
            f"{caller.repository} pins {caller.pin[:7]}, which {RUNNER_REPOSITORY} does not have."
        )
    if caller.state == "diverged":
        return (
            f"{caller.repository} pins {caller.pin[:7]}, which is not on the runner's default "
            f"branch and is neither ahead of nor behind the recommended {short}."
        )
    if caller.state == "ahead":
        count = caller.ahead_by or 0
        return (
            f"{caller.repository} pins {caller.pin[:7]}, {count} commits AHEAD of the recommended "
            f"{short}; the recommendation is the stale half."
        )
    count = caller.behind_by or 0
    return (
        f"{caller.repository} pins {caller.pin[:7]}, {count} commits behind the recommended "
        f"{short}, so a dispatch would execute a runner missing those changes."
    )


def record_digest(record: dict[str, Any]) -> str:
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def reference_for(caller: Caller, record: dict[str, Any]) -> str:
    """The row's identity: the repository, the pin it names, and a digest of everything else.

    The pin is in there for a reader rather than for uniqueness -- the digest already covers it.
    `record` is the whole composed observation minus the two self-referential fields; see the
    module docstring for why digesting `facts` alone is the defect this shape avoids.
    """
    return f"caller-pin:{caller.repository}@{caller.pin}:{record_digest(record)}"


def pin_observation(caller: Caller, recommended: str, recommended_at: str | None) -> dict[str, Any]:
    observed_at = caller.pinned_at or recommended_at
    if observed_at is None:
        raise ValueError(
            "no fact-derived clock is available for "
            f"{caller.repository}; a wall clock here would wedge the producer"
        )
    record = {
        "expected_version": 0,
        "source_system": SOURCE_SYSTEM,
        "source_url": f"https://github.com/{caller.repository}/blob/HEAD/{CALLER_PATH}",
        "trust_classification": TRUST_CLASSIFICATION,
        "subject_type": SUBJECT_TYPE,
        "subject_reference": caller.repository,
        "environment": None,
        "observation_type": OBSERVATION_TYPE,
        "status": STATUS_FINDING if caller.is_finding else STATUS_CURRENT,
        "severity": SEVERITY_FINDING if caller.is_finding else SEVERITY_CURRENT,
        # The pinned revision's clock, never the pass's. See the module docstring: any wall-clock
        # value here makes the second pass over unchanged reality an `observation_conflict`.
        "observed_at": observed_at,
        "summary": summary_of(caller, recommended),
        "facts": pin_facts(caller, recommended),
        "payload_digest": None,
    }
    reference = reference_for(caller, record)
    # The reference is ALREADY content-addressed over everything above, so the idempotency key is
    # the same string. Spelling two strings for one concept would be a second copy of it.
    return {"idempotency_key": reference, "source_reference": reference, **record}
