"""What one activation record asserts, and why an unchanged sweep must re-encode identically."""

from __future__ import annotations

import ast
import json
from pathlib import Path

from activation_sweep.checkout import BEHIND, DIRTY, conditions_of, read_checkout
from activation_sweep.record import (
    OBSERVATION_TYPE,
    SEVERITY_CONDITION,
    SEVERITY_CURRENT,
    SOURCE_SYSTEM,
    STATUS_CONDITION,
    STATUS_CURRENT,
    activation_observation,
    summary_of,
)
from orchestrator.api.schemas import ObservationCommandModel
from orchestrator.persistence.models import (
    OBSERVATION_SEVERITIES,
    OBSERVATION_SOURCE_SYSTEMS,
    OBSERVATION_STATUSES,
    OBSERVATION_SUBJECT_TYPES,
    OBSERVATION_TRUST_CLASSIFICATIONS,
    OBSERVATION_TYPES,
)
from orchestrator.services.observations import (
    MAX_FACT_BYTES,
    MAX_SUMMARY,
    SECRET_KEY_PARTS,
)
from tests.activation_sweep.conftest import Estate


def _keys(value: object) -> set[str]:
    if isinstance(value, dict):
        found = set(value)
        for child in value.values():
            found |= _keys(child)
        return found
    if isinstance(value, list):
        found: set[str] = set()
        for child in value:
            found |= _keys(child)
        return found
    return set()


def test_every_vocabulary_value_the_record_uses_is_one_the_orchestrator_admits(
    estate: Estate,
) -> None:
    """The record composes six enum values, and every one is validated server-side. A member
    added here and not to `models.py` is refused at write time -- at 07:10, in a log."""
    body = activation_observation(read_checkout(estate.local))

    assert body["source_system"] in OBSERVATION_SOURCE_SYSTEMS
    assert body["observation_type"] in OBSERVATION_TYPES
    assert body["subject_type"] in OBSERVATION_SUBJECT_TYPES
    assert body["trust_classification"] in OBSERVATION_TRUST_CLASSIFICATIONS
    assert body["status"] in OBSERVATION_STATUSES
    assert body["severity"] in OBSERVATION_SEVERITIES
    assert SOURCE_SYSTEM in OBSERVATION_SOURCE_SYSTEMS
    assert OBSERVATION_TYPE in OBSERVATION_TYPES


def test_no_fact_key_reads_as_secret_metadata(estate: Estate) -> None:
    """`SECRET_KEY_PARTS` are matched as SUBSTRINGS of a key name, so a key merely CALLED
    `commit_log` is refused whatever its value -- `log` is a member. The failure is a rejected
    write on a lane nobody is watching that morning, so it is pinned here."""
    estate.land_upstream()
    body = activation_observation(read_checkout(estate.local))

    offenders = {
        key for key in _keys(body["facts"]) if any(part in key.lower() for part in SECRET_KEY_PARTS)
    }
    assert offenders == set()


def test_the_facts_and_summary_stay_inside_the_orchestrators_bounds(estate: Estate) -> None:
    """No trimming loop exists, so the bound has to hold by construction."""
    estate.land_upstream("x" * 4000)
    for _ in range(20):
        estate.land_upstream("y" * 400)
    estate.modify_tracked()
    body = activation_observation(read_checkout(estate.local))

    encoded = json.dumps(body["facts"], sort_keys=True, separators=(",", ":"))
    assert len(encoded.encode("utf-8")) < MAX_FACT_BYTES
    assert len(body["summary"]) <= MAX_SUMMARY
    assert len(body["facts"]["missing"]) <= 30
    # The true count travels beside the trimmed list, so a reader can tell a trim from a smaller
    # gap -- the shape the landing ledger uses for `files_changed`.
    assert body["facts"]["measured"]["behind_by"] == 21
    assert len(body["facts"]["missing"]) == 10


def test_the_idempotency_key_fits_the_route_that_will_carry_it(estate: Estate) -> None:
    """The API model caps it at 200 characters, and the sweep's key is the longest thing it
    composes: eleven characters, the repository, a forty-character head and a sha256 digest. The
    bound is read off the model rather than restated, so a narrowed cap reds here instead of at
    07:10 as a 422 against a lane nobody is watching.
    """
    estate.land_upstream()
    body = activation_observation(read_checkout(estate.local))
    cap = max(
        item.max_length
        for item in ObservationCommandModel.model_fields["idempotency_key"].metadata
        if getattr(item, "max_length", None) is not None
    )

    assert len(body["idempotency_key"]) <= cap
    # The longest enrolled repository name, measured rather than assumed to be the example's.
    longest = len("AlobarQuest/infraops-mcp-server") - len("AlobarQuest/example")
    assert len(body["idempotency_key"]) + longest <= cap


def test_a_current_checkout_records_a_passing_observation(estate: Estate) -> None:
    body = activation_observation(read_checkout(estate.local))

    assert body["status"] == STATUS_CURRENT
    assert body["severity"] == SEVERITY_CURRENT
    assert body["facts"]["conditions"] == []
    assert "missing" not in body["facts"]
    assert body["subject_reference"] == "AlobarQuest/example"
    assert "is current with origin/main" in body["summary"]


def test_a_checkout_with_a_condition_records_a_degraded_observation(estate: Estate) -> None:
    landed = estate.land_upstream("bump ruff from 0.16.2 to 0.16.3 (#76)")
    body = activation_observation(read_checkout(estate.local))

    assert body["status"] == STATUS_CONDITION
    assert body["severity"] == SEVERITY_CONDITION
    assert body["facts"]["conditions"] == [BEHIND]
    assert body["facts"]["measured"]["behind_by"] == 1
    assert body["facts"]["missing"] == [
        {"commit": landed, "subject": "bump ruff from 0.16.2 to 0.16.3 (#76)"}
    ]
    assert len(landed) == 40


def test_the_summary_names_every_condition_it_found(estate: Estate) -> None:
    """A summary that named only the first would report a behind-and-dirty checkout as behind."""
    estate.land_upstream()
    estate.modify_tracked()
    state = read_checkout(estate.local)

    assert conditions_of(state) == (BEHIND, DIRTY)
    assert summary_of(state).endswith("is 1 behind origin/main and has 1 modified tracked file")


def test_a_dirty_only_summary_reads_as_a_sentence(estate: Estate) -> None:
    """The clause that is not first is the one a shared verb breaks, and it is the common case:
    seven of the nine enrolled copies can only ever produce one clause at a time."""
    estate.modify_tracked()

    assert summary_of(read_checkout(estate.local)).endswith("has 1 modified tracked file")


def test_the_summary_covers_the_condition_vocabulary_totally(estate: Estate) -> None:
    """Every member reaches a clause. A member with no clause is a condition the record's own
    sentence silently drops, which is how a finding becomes invisible to the person reading it."""
    estate.land_upstream()
    estate.modify_tracked()
    sentence = summary_of(read_checkout(estate.local))
    estate.restore_tracked()
    clean = summary_of(read_checkout(estate.local))

    # Stated as the two clauses rather than the two words, because the sentence is prose: what
    # must be total is that each condition contributes something a reader can see, and that each
    # clause carries its own verb so any subset of them still reads as a sentence.
    assert "is 1 behind" in sentence and "has 1 modified tracked file" in sentence
    # And the dirty clause vanishes with the condition while the behind clause survives, which a
    # sentence built by string concatenation gets wrong in exactly one direction.
    assert clean.endswith("is 1 behind origin/main")


def test_an_unchanged_sweep_encodes_identically(estate: Estate) -> None:
    """The whole reason `observed_at` is HEAD's clock. Two passes over unchanged reality must
    produce the same bytes, or the second reaches the orchestrator's same-source/different-facts
    branch and raises `observation_conflict` -- permanently, from the second sweep onward."""
    first = activation_observation(read_checkout(estate.local))
    second = activation_observation(read_checkout(estate.local))

    assert first == second
    assert first["observed_at"] == read_checkout(estate.local).head_committed_at.isoformat()


def test_a_tree_that_goes_dirty_and_clean_again_returns_to_its_ORIGINAL_reference(
    estate: Estate,
) -> None:
    """SECTION 5.2, and it is the decision the landing ledger's shape would have got wrong.

    A landing is immutable, so keying on `(repository, commit)` is right there. A working copy is
    not: it can go dirty and clean again with HEAD never moving. Under that key the dirty pass
    and the clean pass are one reference carrying two sets of facts -- `observation_conflict`,
    no supersession model, and a producer wedged permanently on its next run.
    """
    clean = activation_observation(read_checkout(estate.local))
    estate.modify_tracked()
    dirty = activation_observation(read_checkout(estate.local))
    estate.restore_tracked()
    again = activation_observation(read_checkout(estate.local))

    assert dirty["source_reference"] != clean["source_reference"]
    assert again == clean
    # The head never moved, so a reference keyed on it alone would have been the same string for
    # all three -- which is the wedge, measured rather than described.
    assert clean["facts"]["head"]["commit"] == dirty["facts"]["head"]["commit"]


def test_the_reference_and_the_key_move_with_every_fact_that_moves(estate: Estate) -> None:
    before = activation_observation(read_checkout(estate.local))
    estate.land_upstream()
    after = activation_observation(read_checkout(estate.local))

    assert before["source_reference"] != after["source_reference"]
    assert before["idempotency_key"] == before["source_reference"]
    assert after["idempotency_key"] == after["source_reference"]


def test_the_record_module_cannot_read_a_clock_at_all() -> None:
    """A structural pin, because the failure is invisible in a passing test.

    Any wall-clock value in `observed_at`, the summary or the facts gives unchanged reality a new
    fact hash on every pass, and the orchestrator hashes the whole command. So the module is held
    to importing no clock: `datetime` and `time` are not available to it, which makes the mistake
    unwriteable rather than merely untested.
    """
    tree = ast.parse(Path("src/activation_sweep/record.py").read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert {"datetime", "time", "calendar"} & imported == set()
