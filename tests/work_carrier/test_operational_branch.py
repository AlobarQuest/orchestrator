"""A standing operational package is carried without a repository (ADR-0054 increment 2).

The branch keys on the PROFILE, and every check answers YES or NO -- never UNKNOWN, which
would hold the record under exit 2 forever, the behaviour this branch replaces. Each refusal
is driven from the positive control by changing exactly one input, so a refusal that fired
for some other reason would fail the control rather than pass for free.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from orchestrator.reach_vocabulary import REACH_VOCABULARY
from tests.work_carrier.test_workability import (
    DECLARED_TRUE,
    _Declares,
    _emitting,
    _record,
    _Registrar,
    _run_registering,
    portfolio,
    project,
)
from work_carrier import workability
from work_carrier.change_manager import WorkRecord
from work_carrier.cli import EXIT_FINDINGS, EXIT_OK, EXIT_UNUSABLE
from work_carrier.prepare import emit_key
from work_carrier.workability import (
    NO,
    NOT_WORKABLE,
    OPERATIONAL,
    OPERATIONAL_REACH,
    OPERATIONAL_RESIDUAL,
    PAT_SCOPE_RESIDUAL,
    STANDING_OPERATIONAL_PACKAGES,
    UNDECIDED,
    UNKNOWN,
    YES,
    assess_operational,
    judge,
)

STANDING = "rotation-openrouter-generic"


def operational(
    package_id: str = STANDING,
    *,
    standing: object = True,
    reach: object = ("external_system", "operator_machine"),
    profile: str = "non-software-operational",
) -> dict:
    """An intake payload shaped as `package_sources.py` emits it: `profile` beside the snapshot."""
    fields: dict = {"owner": "devon", "operating_procedure": "make before break"}
    if standing is not None:
        fields["standing"] = standing
    snapshot: dict = {"profile_fields": fields}
    if reach is not None:
        snapshot["reach"] = list(reach) if isinstance(reach, tuple) else reach
    return {"package_id": package_id, "profile": profile, "enforcement_snapshot": snapshot}


def _verdicts(payload: dict) -> dict[str, str]:
    return {c.name: c.verdict for c in assess_operational(payload).constraints}


def test_a_standing_allowlisted_package_with_operational_reach_is_operational() -> None:
    verdict = assess_operational(operational())

    assert verdict.decision == OPERATIONAL
    assert set(_verdicts(operational()).values()) == {YES}


@pytest.mark.parametrize(
    ("payload", "refused"),
    [
        (operational(standing=None), "standing"),
        (operational(standing=False), "standing"),
        # `is True`, not truthiness: a string or a 1 is not a declaration.
        (operational(standing="true"), "standing"),
        (operational(standing=1), "standing"),
        (operational("rotation-not-declared"), "opts in"),
        (operational(reach=None), "reach"),
        (operational(reach=[]), "reach"),
        (operational(reach=["external_system", "source_repository"]), "reach"),
        (operational(reach=["external_system", "somewhere_new"]), "reach"),
        (operational(reach=["external_system", ["nested"]]), "reach"),
        (operational(reach="external_system"), "reach"),
    ],
)
def test_each_refusal_is_a_definite_no_on_its_own_check(payload: dict, refused: str) -> None:
    verdicts = _verdicts(payload)

    assert assess_operational(payload).decision == NOT_WORKABLE
    assert verdicts[refused] == NO
    assert [name for name, v in verdicts.items() if v != YES] == [refused]
    assert UNKNOWN not in verdicts.values()


def test_the_allowlist_and_reach_set_agree_with_the_orchestrators_vocabulary() -> None:
    """Two copies of the reach vocabulary; this keeps the carrier's from drifting."""
    assert OPERATIONAL_REACH <= set(REACH_VOCABULARY)
    assert "source_repository" not in OPERATIONAL_REACH
    assert STANDING in STANDING_OPERATIONAL_PACKAGES


def _judge(payload: dict) -> tuple[str, str, _Declares]:
    source = _Declares(DECLARED_TRUE)
    out = io.StringIO()
    (decision,) = judge([("subject", payload)], out, source=source, portfolio=portfolio(project()))
    return decision, out.getvalue(), source


def test_the_operational_branch_asks_github_nothing_and_says_no_runner() -> None:
    """A `target_repo` is planted (the profile's closed schema forbids one) so that a branch
    which still read the declaration would be seen asking; with none, nothing would be asked
    either way and the assertion could not discriminate."""
    payload = operational()
    payload["enforcement_snapshot"]["profile_fields"]["target_repo"] = "AlobarQuest/somewhere"

    decision, text, source = _judge(payload)

    assert decision == OPERATIONAL
    assert source.asked == [], "an operational package has no repository to read a declaration of"
    assert OPERATIONAL_RESIDUAL in text
    assert PAT_SCOPE_RESIDUAL not in text


def test_the_branch_keys_on_the_profile_not_on_the_missing_repository() -> None:
    """A dependency-update package that names no target_repo is still held, standing or not."""
    payload = operational(profile="dependency-update")

    decision, text, _ = _judge(payload)

    assert decision == UNDECIDED
    assert OPERATIONAL_RESIDUAL not in text


def test_the_profile_is_read_beside_the_snapshot_not_inside_it() -> None:
    """`profile` inside the snapshot is not where the emitter puts it, and must not count.

    Deliberately a pin on the emitter's current shape (`package_sources.py` writes `profile`
    beside `enforcement_snapshot`); if the emitter ever moves it, this test moves with it.
    """
    payload = operational()
    payload["enforcement_snapshot"]["profile"] = payload.pop("profile")

    decision, _, _ = _judge(payload)

    assert decision == UNDECIDED


# ---- through the carry: what reaches the writer, and the exit code -----------------------


def _carry(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict,
    *,
    allowlist: frozenset[str],
) -> tuple[int, str, list[dict]]:
    import work_carrier.cli as cli_module
    import work_carrier.prepare as prepare_module

    rec = _record()
    emitted = {
        **payload,
        "package_id": rec.package_id,
        "revision": rec.package_revision,
        "source_repository": rec.package_source_repository,
        "change_record_id": rec.change_record_id,
        "idempotency_key": emit_key(rec),
        "expected_version": 0,
    }
    monkeypatch.setattr(workability, "STANDING_OPERATIONAL_PACKAGES", allowlist)
    monkeypatch.setattr(
        cli_module,
        "judge_workability",
        lambda subjects, out: judge(
            subjects, out, source=_Declares(DECLARED_TRUE), portfolio=portfolio(project())
        ),
    )
    original = prepare_module.prepare
    monkeypatch.setattr(
        cli_module,
        "prepare",
        lambda r, **kw: original(r, **kw, runner=_emitting(emitted)),
    )
    writer = _Registrar()
    code, text = _run_registering(root, rec, writer)
    return code, text, writer.payloads


def _fixture_id() -> frozenset[str]:
    rec: WorkRecord = _record()
    return frozenset({rec.package_id})


def test_an_operational_record_is_registered_and_exits_clean(
    checkout_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, text, payloads = _carry(
        checkout_root, monkeypatch, operational(), allowlist=_fixture_id()
    )

    assert code == EXIT_OK
    assert len(payloads) == 1
    assert payloads[0]["change_record_id"] == _record().change_record_id, "ADR-0027"
    assert OPERATIONAL in text
    assert "1 carried" in text


def test_an_operational_record_not_on_the_allowlist_is_a_finding(
    checkout_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, text, payloads = _carry(checkout_root, monkeypatch, operational(), allowlist=frozenset())

    assert payloads == []
    assert code == EXIT_FINDINGS
    assert NOT_WORKABLE in text


def test_a_dependency_update_record_with_no_repository_is_still_held_under_exit_2(
    checkout_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, text, payloads = _carry(
        checkout_root,
        monkeypatch,
        operational(profile="dependency-update"),
        allowlist=_fixture_id(),
    )

    assert payloads == []
    assert code == EXIT_UNUSABLE
    assert UNDECIDED in text
