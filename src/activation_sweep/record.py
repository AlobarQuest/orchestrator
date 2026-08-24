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

**The reference carries a DIGEST, and that is the decision the landing ledger's shape would have
got wrong here.** A landing is immutable, so the ledger keys on the landing's own identity and
treats a changed fact as the loud failure it is. A working copy is not immutable: a tree can go
dirty and clean again with HEAD never moving, so `(repository, head)` alone is one key with two
sets of facts, which wedges the producer forever. Content-addressing the reference makes a changed
condition APPEND and an unchanged sweep REPLAY. change-manager's `observation_key` is the
precedent.

**THE DIGEST COVERS THE WHOLE COMPOSED RECORD, NOT JUST `facts`, AND THE DIFFERENCE IS THE WHOLE
POINT.** A first version digested `facts` alone, which is the obvious reading of "content-address
the reference" and is a strict subset of what the orchestrator compares. Because the reference is
also the idempotency key, the server's FIRST lookup is by that key, and on a hit it compares the
entire stored command -- `summary`, `status`, `severity`, `source_url`, `trust_classification`,
`subject_type`, `observation_type`, every one of them producer-derived and none of them in `facts`
(`services/observations.py::_validate_idempotent_replay`, `::_command_payload`). So rewording one
clause of `summary_of` would have made the next sweep an `idempotency_conflict` for every checkout
whose git state had not moved since its last row -- the section 5.2 defect exactly, one field over,
self-healing only when that repository's HEAD or tree next moves, which for `email-capture` or
`~/.claude` can be weeks. Two independent adversarial reviews found it; no test could, because
every test generates both sides from one version of the producer.

Digesting the whole body makes any producer change APPEND, which is always safe. **The residual is
named rather than implied: `actor_id` and `actor_role` are in the compared payload and are derived
server-side from the credential, so this program cannot cover them.** Recording an activation
observation under a different credential actor would conflict in the same way and would need the
same treatment as any other supersession problem -- there is none.
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
# The orchestrator's own bound on the encoded facts (`services/observations.py`).
MAX_FACT_BYTES = 4096


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

    `missing` is the only variable-length member, and `_fit` trims it -- deterministically, longest
    entry first -- until the encoded record is inside the orchestrator's 4096-byte bound. A first
    version argued no trimming was needed because ten entries of at most two hundred CHARACTERS
    could not approach the bound. That arithmetic was done in ASCII: the bound is on UTF-8 bytes
    of `json.dumps` output, which escapes a non-ASCII code point to six bytes and an astral one to
    twelve, so about fifty-two CJK characters per subject is enough to exceed it -- on exactly the
    checkout that is ten or more commits behind, which is the case the control exists to report.
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


def _encoded_size(value: Any) -> int:
    return len(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _fit(facts: dict[str, Any]) -> dict[str, Any]:
    """Drop missing commits, longest entry first, until the facts fit the orchestrator's bound.

    Deterministic, so the same checkout always encodes identically and a re-run replays rather
    than conflicting. `missing` is the only variable-length member and `behind_by` stays beside
    it, so a trim is visible rather than silent. It terminates BELOW the bound rather than merely
    trying: with every entry gone the remaining members are a few hundred bytes.
    """
    while _encoded_size(facts) > MAX_FACT_BYTES:
        entries = facts.get("missing")
        if not entries:
            return facts
        entries.remove(max(entries, key=_encoded_size))
    return facts


def record_digest(record: dict[str, Any]) -> str:
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def reference_for(checkout: Checkout, record: dict[str, Any]) -> str:
    """The row's identity: the repository, the head it is at, and a digest of everything else.

    The head is in there for a reader rather than for uniqueness -- the digest already covers it.
    `record` is the whole composed observation minus the two self-referential fields; see the
    module docstring for why digesting `facts` alone is the defect this shape exists to avoid.
    """
    return f"activation:{checkout.repository}@{checkout.head}:{record_digest(record)}"


def activation_observation(checkout: Checkout) -> dict[str, Any]:
    conditions = conditions_of(checkout)
    record = {
        "expected_version": 0,
        "source_system": SOURCE_SYSTEM,
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
        "facts": _fit(activation_facts(checkout)),
        "payload_digest": None,
    }
    reference = reference_for(checkout, record)
    # The reference is ALREADY content-addressed over everything above, so the idempotency key is
    # the same string. The landing ledger keeps them distinct because its reference is an
    # immutable identity and its key is the content; here section 5.2 of the spec decided the
    # identity IS the content, and spelling two strings for one concept would be a second copy.
    return {"idempotency_key": reference, "source_reference": reference, **record}
