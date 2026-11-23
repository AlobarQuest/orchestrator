"""The proof a lane token's verify_consumers note names is accepted where today's note is
(#789 follow-up 4, option A: no new key, so no contract change and no deploy first).

Each note the executor files for both proofs, the issue's deletion record included, goes through
the orchestrator's real ingest check and the real verifier, and gets the same answer as the same
note without its proof phrase. The longest note any proof can make fits both length limits.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from orchestrator.persistence.models import WorkPackageRevision, WorkUnit
from orchestrator.services.verifier.rotation_evidence import (
    LONG_RUN,
    MAX_TEXT,
    check_rotation_evidence,
)
from orchestrator.services.verifier.rotation_verification import rotation_step_verdict
from rotation_worker import worker
from rotation_worker.contract import EMPTY, PROOF_PHRASES, ConsumerProof
from rotation_worker.evidence import MAX_NOTE, StepEvidence
from rotation_worker.values import Value
from tests.rotation_worker.test_lane_credentials import KEEPER, LANE_ISSUE, NEW_TOKEN, _row
from tests.rotation_worker.test_lane_token_loaded import PRINT_KEY, TARGET, Helper, Unit, printed

CRITERION = (
    "external: the verify_consumers step's rotation-step/1 evidence, "
    "filed by the rotation executor."
)
ROTATION_UNIT = SimpleNamespace(required_capability="operational_action")
ROTATION_REVISION = SimpleNamespace(
    profile="non-software-operational",
    enforcement_snapshot={
        "profile_fields": {"standing": True, "credential_id": "execution-watch-bws-token"}
    },
)
PHRASE = re.compile("|".join(re.escape(f"; {p}") for p in PROOF_PHRASES.values()))


def _reissue_loaded(unit: Unit) -> None:
    unit.row = _row(helper_target=TARGET)
    unit.helper = Helper({PRINT_KEY: printed(NEW_TOKEN)})


def _reissue_patch(unit: Unit) -> None:
    unit.row = _row()
    unit.helper = None


def _issue(unit: Unit) -> None:
    unit.plan, unit.name = LANE_ISSUE, "store"
    unit.store.secrets[KEEPER] = ("keepers", "keeper", Value(EMPTY))
    unit.drop_quarantine()
    unit.helper = Helper(AssertionError("an issue never reads loaded"))


@pytest.mark.parametrize(
    ("arrange", "proof", "records"),
    [(_reissue_loaded, "loaded", 1), (_reissue_patch, "patch", 1), (_issue, "patch", 2)],
    ids=["reissue-loaded", "reissue-patch", "issue-patch-and-deletion"],
)
def test_each_proof_note_is_accepted_and_judged_as_today_s_note(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    arrange: Any,
    proof: ConsumerProof,
    records: int,
) -> None:
    unit = Unit(tmp_path, monkeypatch)
    arrange(unit)
    unit.take()
    payloads = [
        b["payload"]
        for b in unit.client.evidence_bodies
        if b["payload"]["step"] == "verify_consumers"
    ]
    assert len(payloads) == records
    for payload in payloads:
        assert PROOF_PHRASES[proof] in payload["note"]
        check_rotation_evidence(
            cast(WorkUnit, ROTATION_UNIT),
            cast(WorkPackageRevision, ROTATION_REVISION),
            None,
            payload,
            worker.EVIDENCE_TYPE,
            "rotation-worker@0123456789ab",
        )
        today = {**payload, "note": PHRASE.sub("", payload["note"])}
        assert today["note"] != payload["note"]
        verdicts = [
            rotation_step_verdict(
                "external_attestation", CRITERION, unit.name, worker.EVIDENCE_TYPE, body
            )
            for body in (payload, today)
        ]
        # Both decided by the verifier, with no person: the phrase changes no answer.
        assert verdicts == [None, None]


def test_the_longest_proof_note_fits_both_length_limits() -> None:
    """Counts far past any registry row, the longest before-revoke outcome, every proof phrase
    at once, and the issue's deletion suffix with a full BWS id."""
    note = "999999 of 999999 consumers hold or attest the new value"
    note += "; the old value is indeterminate before the revoke"
    note += "".join(f"; {phrase}" for phrase in PROOF_PHRASES.values())
    note += "; deleted staging 00000000-0000-4000-8000-000000000000"
    assert len(note) <= MAX_NOTE and len(note) <= MAX_TEXT
    StepEvidence("verify_consumers", note=note)


def test_no_proof_phrase_could_turn_on_the_before_revoke_rule_or_form_a_long_run() -> None:
    for phrase in PROOF_PHRASES.values():
        assert " before the revoke" not in phrase
        assert LONG_RUN.search(phrase) is None
