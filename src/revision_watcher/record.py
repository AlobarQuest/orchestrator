"""The durable record: what one pass may honestly assert about one application.

THE TWO RULES ARE THE PIN WATCHER'S, WHICH TOOK THEM FROM THE ACTIVATION SWEEP AND THE LEDGER.

**A record must not assert a condition nobody checked.** This lane asks an application what commit
it is serving and asks GitHub what a branch names. It does not know WHY they differ -- whether a
deployment failed, an image never built, a secret was rotated, or nobody performed a manual swap --
and nothing here says. Cause-independence is the property that makes the lane worth having; a
record that guessed at a cause would trade it away.

**Re-running over unchanged reality must change nothing.** `observed_at` is the committer date of
the commit the record is about, never the moment the pass ran -- see `census.read_subject`, where
it is derived. The orchestrator hashes the whole stored command, `observed_at` included, so a wall
clock would give unchanged reality a new fact hash every pass; at a stable reference that is the
same-source/different-facts branch and `observation_conflict` forever, from the second pass on.

**THE DIGEST COVERS THE WHOLE COMPOSED RECORD, NOT JUST `facts`.** The reference is also the
idempotency key, so the server's first lookup is by that key and on a hit it compares the entire
stored command -- `summary`, `status`, `severity` and five more, every one producer-derived and
none of them in `facts`. Rewording one clause of `summary_of` would otherwise make the next pass an
`idempotency_conflict` for every application that had not moved, which for a healthy estate is all
of them. The activation sweep paid for this discovery twice; this lane inherits the answer.

**AND THE REFERENCE IS CONTENT-ADDRESSED RATHER THAN PER-APPLICATION**, which is the one place this
differs from the landing ledger and the difference is deliberate. A landing is immutable -- a
commit on a branch cannot change -- so the ledger keys on the landing and a changed fact means
something is wrong. What an application SERVES is not immutable; it is expected to move, several
times a day. Keying on the application alone would make the second pass after any deploy an
`observation_conflict`, permanently, which is the exact trap ADR-0022's first draft fell into by
copying the ledger's rule onto a subject that re-runs.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from revision_watcher.census import BEHIND, CURRENT, DIVERGED, UNSTAMPED, Reading

SOURCE_SYSTEM = "revision_watcher"
OBSERVATION_TYPE = "production_revision"
TRUST_CLASSIFICATION = "delivery_system"

# `app`, because the subject is one deployed application rather than a repository -- four of the
# six subjects share one repository and would collide under a `repo` reference. Uniqueness is on
# `(source_system, source_reference)`, so this cannot collide with a sibling lane's rows either.
SUBJECT_TYPE = "service"

# A current application PASSED. Behind or diverged is DEGRADED rather than FAILED: production
# serving an older build is an ordinary, recoverable state that wants a person's attention, and
# nothing here has established that anything FAILED -- which is the cause-independence the module
# docstring keeps. `unstamped` is INFO: an application that never claimed to be askable has not
# failed a check, it has declined to take one.
_STATUS = {CURRENT: "passed", BEHIND: "degraded", DIVERGED: "degraded", UNSTAMPED: "passed"}
_SEVERITY = {CURRENT: "info", BEHIND: "warning", DIVERGED: "warning", UNSTAMPED: "info"}

MAX_SUMMARY = 512


def summary_of(reading: Reading) -> str:
    served = (reading.served or "<none>")[:12]
    expected = (reading.expected or "<unknown>")[:12]
    if reading.state == CURRENT:
        body = f"is serving {served}, which is what {reading.subject.branch} names"
    elif reading.state == UNSTAMPED:
        body = (
            f"answers health and names no commit, so what it is serving cannot be compared "
            f"with {reading.subject.branch} at {expected}"
        )
    elif reading.state == BEHIND:
        body = f"is serving {served}, behind {reading.subject.branch} at {expected}"
    else:
        body = f"is serving {served}, which is not on {reading.subject.branch} at {expected}"
    return f"{reading.subject.name} {body}"[:MAX_SUMMARY]


def revision_facts(reading: Reading) -> dict[str, Any]:
    return {
        "application": reading.subject.name,
        "repository": reading.subject.repository,
        "branch": reading.subject.branch,
        "state": reading.state,
        # Present and null rather than absent when an application names no commit. A consumer must
        # tell "this application does not say" from "this application is current", and an absent
        # key says the first to a reader who is looking and the second to one calling `.get()`.
        "serving": reading.served,
        "branch_names": reading.expected,
        "health_url": reading.subject.health_url,
    }


def record_digest(record: dict[str, Any]) -> str:
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def reference_for(reading: Reading, record: dict[str, Any]) -> str:
    """The row's identity: the application, what it serves, and a digest of everything else.

    The served commit is spelled out for a reader rather than for uniqueness -- the digest already
    covers it. See the module docstring for why this is content-addressed where the ledger's is not.
    """
    served = (reading.served or "unstamped")[:12]
    return f"production-revision:{reading.subject.name}@{served}:{record_digest(record)}"


def revision_observation(reading: Reading) -> dict[str, Any]:
    if reading.observed_at is None:
        raise ValueError(
            "no fact-derived clock is available for "
            f"{reading.subject.name}; a wall clock here would wedge the producer"
        )
    record = {
        "expected_version": 0,
        "source_system": SOURCE_SYSTEM,
        "source_url": reading.subject.health_url,
        "trust_classification": TRUST_CLASSIFICATION,
        "subject_type": SUBJECT_TYPE,
        "subject_reference": reading.subject.name,
        "environment": "production",
        "observation_type": OBSERVATION_TYPE,
        "status": _STATUS[reading.state],
        "severity": _SEVERITY[reading.state],
        "observed_at": reading.observed_at,
        "summary": summary_of(reading),
        "facts": revision_facts(reading),
        "payload_digest": None,
    }
    reference = reference_for(reading, record)
    # Already content-addressed over everything above, so the idempotency key is the same string.
    # Spelling two strings for one concept would be a second copy of it.
    return {"idempotency_key": reference, "source_reference": reference, **record}
