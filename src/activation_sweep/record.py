"""The activation record: what one working copy may honestly assert, and nothing more.

TWO RULES SHAPE EVERY LINE BELOW, and they are the landing ledger's.

**A record must not assert a condition nobody checked.** The sweep reads the disk. It does not
know what any process is executing, so nothing here says so -- ADR-0030's weak form is a bound on
the claim, not a limitation to be worked around.

**Re-running over unchanged reality must change nothing.** That is why `observed_at` is HEAD's
COMMITTER DATE and not the moment the sweep ran. The orchestrator's replay check hashes the whole
command, `observed_at` included (`services/observations.py::_fact_identity`), so a wall clock
would give unchanged reality a new fact hash on every pass -- and because the source reference is
the same, that reaches the same-source/different-facts branch and raises `observation_conflict`,
permanently, from the second sweep onward. A clock that is a function of the facts is the only
one that replays. When the sweep ran is recorded anyway: the orchestrator stamps `received_at`
itself, which is a better answer than anything this program could assert about its own clock.

**The reference carries a FACT DIGEST, and that is the decision the landing ledger's shape would
have got wrong here.** A landing is immutable, so the ledger keys on the landing's own identity
and treats a changed fact as the loud failure it is. A working copy is not immutable: a tree can
go dirty and clean again with HEAD never moving, so `(repository, head)` alone is one key with
two sets of facts, which wedges the producer forever. Content-addressing the reference makes a
changed condition APPEND and an unchanged sweep REPLAY, which is the behaviour a daily control
needs. change-manager's `observation_key` is the precedent.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

from activation_sweep.checkout import BEHIND, DIRTY, Checkout, conditions_of

# ADR-0030's lane, named once for the whole lane rather than once per producer -- the precedent is
# written inside `OBSERVATION_SOURCE_SYSTEMS` itself: `source_system` names the producing LANE and
# `subject_reference` names the individual run's subject. It is `machine_activation` and not the
# more obvious compound because `persistence/models.py` is inside the guarded tree and the ws32
# and ws34 word guards forbid the bare token the compound would tokenize to.
SOURCE_SYSTEM = "machine_activation"

# A separate observation type for the same reason migration 0027 gave for `backup`: none of the
# existing members fits, and reusing a near-miss writes false provenance into rows that have no
# supersession model and no delete route. `drift` is the closest and is the infrastructure drift
# digest's; `inventory` asserts nothing. This one asserts something specific -- what a named
# working copy on the operator machine will execute at the next start.
OBSERVATION_TYPE = "activation"

TRUST_CLASSIFICATION = "delivery_system"
# `repo`, keyed by `owner/name`, which is what the landing ledger already writes. The two cannot
# collide: uniqueness is on `(source_system, source_reference)` and the source systems differ.
SUBJECT_TYPE = "repo"

# A measurement that found nothing PASSED; one that found a condition is DEGRADED rather than
# FAILED. Behind and dirty are both ordinary, recoverable states of a working copy -- the machine
# is not running what was merged, which is worth a person's attention and is not a failure of
# anything.
STATUS_CURRENT = "passed"
STATUS_CONDITION = "degraded"
SEVERITY_CURRENT = "info"
SEVERITY_CONDITION = "warning"

MAX_SUMMARY = 512


def _measured(checkout: Checkout) -> dict[str, Any]:
    """The raw numbers the conditions were classified FROM, kept beside them.

    `ahead_by` is here and in no condition, deliberately -- see `checkout`'s module docstring.
    """
    return {
        "behind_by": checkout.behind_by,
        "ahead_by": checkout.ahead_by,
        "tracked_modifications": checkout.tracked_modifications,
    }


def activation_facts(checkout: Checkout) -> dict[str, Any]:
    """Everything the record says, bounded by construction.

    No key here contains a fragment the orchestrator's secret detector reads as metadata
    (`services/observations.py::SECRET_KEY_PARTS` -- `token`, `credential`, `log` and six more
    are matched as SUBSTRINGS of a key name, so `commit_log` would be refused on its name alone).

    Nothing is variable-length beyond `missing`, which is capped at ten entries of at most two
    hundred characters, so the encoded facts cannot approach the orchestrator's 4096-byte bound
    and there is no trimming loop to get wrong.
    """
    facts: dict[str, Any] = {
        "checkout": {
            "path": checkout.path,
            "repository": checkout.repository,
            "branch": checkout.branch,
            "upstream": checkout.upstream,
        },
        "head": {
            "commit": checkout.head,
            "committed_at": checkout.head_committed_at.isoformat(),
        },
        "conditions": list(conditions_of(checkout)),
        "measured": _measured(checkout),
    }
    if checkout.missing:
        facts["missing"] = [asdict(commit) for commit in checkout.missing]
    return facts


def summary_of(checkout: Checkout) -> str:
    """One sentence, computed from the same classifier the facts carry."""
    found = conditions_of(checkout)
    head = checkout.head[:12]
    if not found:
        return f"{checkout.repository} at {head} is current with {checkout.upstream}"
    # Each clause carries its own verb, so any subset of them reads as a sentence. A shared verb
    # works for whichever clause was written first and is wrong for the other.
    clauses = []
    if BEHIND in found:
        clauses.append(f"is {checkout.behind_by} behind {checkout.upstream}")
    if DIRTY in found:
        count = checkout.tracked_modifications
        clauses.append(f"has {count} modified tracked file{'' if count == 1 else 's'}")
    return f"{checkout.repository} at {head} {' and '.join(clauses)}"[:MAX_SUMMARY]


def fact_digest(facts: dict[str, Any]) -> str:
    canonical = json.dumps(facts, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def reference_for(checkout: Checkout, facts: dict[str, Any]) -> str:
    """The row's identity: the repository, the head it is at, and a digest of everything else.

    The head is in there for a reader rather than for uniqueness -- the digest already covers it.
    """
    return f"activation:{checkout.repository}@{checkout.head}:{fact_digest(facts)}"


def activation_observation(checkout: Checkout) -> dict[str, Any]:
    facts = activation_facts(checkout)
    reference = reference_for(checkout, facts)
    conditions = conditions_of(checkout)
    return {
        # The reference is ALREADY content-addressed, so the idempotency key is the same string.
        # The landing ledger keeps them distinct because its reference is an immutable identity
        # and its key is the content; here section 5.2 of the spec decided the identity IS the
        # content, and spelling two different strings for one concept would be a second copy.
        "idempotency_key": reference,
        "expected_version": 0,
        "source_system": SOURCE_SYSTEM,
        "source_reference": reference,
        "source_url": f"https://github.com/{checkout.repository}/commit/{checkout.head}",
        "trust_classification": TRUST_CLASSIFICATION,
        "subject_type": SUBJECT_TYPE,
        "subject_reference": checkout.repository,
        "environment": None,
        "observation_type": OBSERVATION_TYPE,
        "status": STATUS_CONDITION if conditions else STATUS_CURRENT,
        "severity": SEVERITY_CONDITION if conditions else SEVERITY_CURRENT,
        # HEAD's clock, never the pass's. See the module docstring: any wall-clock value here
        # makes the second sweep over unchanged reality an `observation_conflict`.
        "observed_at": checkout.head_committed_at.isoformat(),
        "summary": summary_of(checkout),
        "facts": facts,
        "payload_digest": None,
    }
