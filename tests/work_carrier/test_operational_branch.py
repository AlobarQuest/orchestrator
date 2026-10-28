"""A standing operational package is carried without a repository (ADR-0054 increment 2).

Its credential opts in through two reviewed records (ADR-0055 amendment 15, increment 4): its
registry row hands it to the SDS, and the member record grants `credential_rotation` on the
repository whose registry holds that row.

The branch keys on the PROFILE, and every check answers YES or NO -- never UNKNOWN, which
would hold the record under exit 2 forever, the behaviour this branch replaces. Each refusal
is driven from the positive control by changing exactly one input, so a refusal that fired
for some other reason would fail the control rather than pass for free.
"""

from __future__ import annotations

import io
import shutil
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from orchestrator.reach_vocabulary import REACH_VOCABULARY
from rotation_worker.registry import RegistryEntry, RegistryError
from tests.work_carrier.conftest import FIXTURE_PACKAGE
from tests.work_carrier.test_workability import (
    GRANTED,
    _emitting,
    _record,
    _Registrar,
    _run_registering,
    grants,
    member,
    portfolio,
    project,
)
from work_carrier.cli import EXIT_FINDINGS, EXIT_OK, EXIT_UNUSABLE
from work_carrier.prepare import Prepared, emit_key
from work_carrier.workability import (
    NO,
    NOT_WORKABLE,
    OPERATIONAL,
    OPERATIONAL_REACH,
    OPERATIONAL_RESIDUAL,
    PAT_SCOPE_RESIDUAL,
    UNDECIDED,
    UNKNOWN,
    YES,
    assess_operational,
    judge,
)

CREDENTIAL = "openrouter-generic"
STANDING = f"rotation-{CREDENTIAL}"
REGISTRY = "AlobarQuest/infraops-mcp-server"
# The registry hands the credential to the SDS, and the record grants rotation on its registry.
ROTATED = {CREDENTIAL: REGISTRY}
ROTATION_GRANTS = {**GRANTED, **grants(REGISTRY, service="credential_rotation")}


def operational(
    package_id: str = STANDING,
    *,
    standing: object = True,
    reach: object = ("external_system", "operator_machine"),
    profile: str = "non-software-operational",
    credential: str = CREDENTIAL,
) -> dict:
    """An intake payload shaped as `package_sources.py` emits it: `profile` beside the snapshot."""
    fields: dict = {
        "owner": "devon",
        "operating_procedure": "make before break",
        "credential_id": credential,
    }
    if standing is not None:
        fields["standing"] = standing
    snapshot: dict = {"profile_fields": fields}
    if reach is not None:
        snapshot["reach"] = list(reach) if isinstance(reach, tuple) else reach
    return {"package_id": package_id, "profile": profile, "enforcement_snapshot": snapshot}


def _verdicts(
    payload: dict, rotated: dict = ROTATED, granted: dict = ROTATION_GRANTS
) -> dict[str, str]:
    return {c.name: c.verdict for c in assess_operational(payload, rotated, granted).constraints}


def test_a_standing_opted_in_package_with_operational_reach_is_operational() -> None:
    verdict = assess_operational(operational(), ROTATED, ROTATION_GRANTS)

    assert verdict.decision == OPERATIONAL
    assert set(_verdicts(operational()).values()) == {YES}


@pytest.mark.parametrize(
    ("rotated", "granted", "why"),
    [
        ({}, ROTATION_GRANTS, "no registry row hands"),
        ({CREDENTIAL: "AlobarQuest/elsewhere"}, ROTATION_GRANTS, "does not grant"),
        (ROTATED, GRANTED, "does not grant credential_rotation"),
    ],
)
def test_a_credential_opts_in_only_through_the_registry_and_the_member_record(
    rotated: dict, granted: dict, why: str
) -> None:
    verdict = assess_operational(operational(), rotated, granted)
    opted = next(c for c in verdict.constraints if c.name == "opts in")

    assert verdict.decision == NOT_WORKABLE
    assert opted.verdict == NO and why in opted.detail
    assert [c.name for c in verdict.constraints if c.verdict != YES] == ["opts in"]


def test_the_registry_repository_is_matched_without_case() -> None:
    assert _verdicts(operational(), {CREDENTIAL: REGISTRY.upper()})["opts in"] == YES


@pytest.mark.parametrize(
    ("payload", "refused"),
    [
        (operational(standing=None), "standing"),
        (operational(standing=False), "standing"),
        # `is True`, not truthiness: a string or a 1 is not a declaration.
        (operational(standing="true"), "standing"),
        (operational(standing=1), "standing"),
        # A package must be named for the credential it carries.
        (operational("rotation-not-declared"), "opts in"),
        (operational(credential="another-credential"), "opts in"),
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

    assert assess_operational(payload, ROTATED, ROTATION_GRANTS).decision == NOT_WORKABLE
    assert verdicts[refused] == NO
    assert [name for name, v in verdicts.items() if v != YES] == [refused]
    assert UNKNOWN not in verdicts.values()


def test_the_reach_set_agrees_with_the_orchestrators_vocabulary() -> None:
    """Two copies of the reach vocabulary; this keeps the carrier's from drifting."""
    assert OPERATIONAL_REACH <= set(REACH_VOCABULARY)
    assert "source_repository" not in OPERATIONAL_REACH


class _Asked(dict):
    """The grant table, remembering whether anything looked a service up in it."""

    asked: list[str]

    def __init__(self, table: dict) -> None:
        super().__init__(table)
        self.asked = []

    def get(self, key, default=None):
        self.asked.append(key)
        return super().get(key, default)


def _judge(payload: dict) -> tuple[str, str, _Asked]:
    source = _Asked(ROTATION_GRANTS)
    out = io.StringIO()
    (decision,) = judge(
        [("subject", payload)], out, grants=source, rotated=ROTATED, portfolio=portfolio(project())
    )
    return decision, out.getvalue(), source


def test_the_operational_branch_asks_only_the_rotation_grant_and_says_no_runner() -> None:
    """A `target_repo` is planted (the profile's closed schema forbids one) so that a branch
    which still consulted that repository's grants would be seen asking."""
    payload = operational()
    payload["enforcement_snapshot"]["profile_fields"]["target_repo"] = "AlobarQuest/somewhere"

    decision, text, source = _judge(payload)

    assert decision == OPERATIONAL
    assert source.asked == ["credential_rotation"], "only the registry repository's grant"
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
    rotated: dict | None = None,
) -> tuple[int, str, list[dict]]:
    import work_carrier.cli as cli_module
    import work_carrier.prepare as prepare_module

    # The fixture record, named for the standing package it carries, which the checkout holds.
    rec = replace(_record(), package_id=STANDING)
    packages = root / "intent-packages" / "packages"
    shutil.copytree(packages / FIXTURE_PACKAGE, packages / STANDING)
    emitted = {
        **payload,
        "package_id": rec.package_id,
        "revision": rec.package_revision,
        "source_repository": rec.package_source_repository,
        "change_record_id": rec.change_record_id,
        "idempotency_key": emit_key(rec),
        "expected_version": 0,
    }
    registry_reads: list[object] = []

    def rotated_by_sds(mirror: object) -> dict:
        registry_reads.append(mirror)
        if rotated is None:
            raise RegistryError(f"{REGISTRY}: .cred-consumers.toml doesn't parse")
        # Rows of a type the SDS rotates, as the registry reader returns them.
        return {
            credential: RegistryEntry(repository, "c" * 40, {"class": "openrouter-key"})
            for credential, repository in rotated.items()
        }

    monkeypatch.setattr(cli_module, "rotated_by_sds", rotated_by_sds)
    monkeypatch.setattr(cli_module, "default_mirror_root", lambda program: root / program)
    monkeypatch.setattr(
        cli_module,
        "judge_workability",
        lambda subjects, out, grants, rotated: judge(
            subjects, out, grants=grants, rotated=rotated, portfolio=portfolio(project())
        ),
    )
    original = prepare_module.prepare
    monkeypatch.setattr(
        cli_module,
        "prepare",
        lambda r, **kw: original(r, **kw, runner=_emitting(emitted)),
    )
    writer = _Registrar()
    members = [member(REGISTRY, credential_rotation="granted")]
    code, text = _run_registering(root, rec, writer, members)
    return code, text, writer.payloads


def test_an_operational_record_is_registered_and_exits_clean(
    checkout_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, text, payloads = _carry(checkout_root, monkeypatch, operational(), rotated=ROTATED)

    assert code == EXIT_OK
    assert len(payloads) == 1
    assert payloads[0]["change_record_id"] == _record().change_record_id, "ADR-0027"
    assert OPERATIONAL in text
    assert "1 carried" in text


def test_an_operational_record_whose_credential_isn_t_handed_over_is_a_finding(
    checkout_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, text, payloads = _carry(checkout_root, monkeypatch, operational(), rotated={})

    assert payloads == []
    assert code == EXIT_FINDINGS
    assert NOT_WORKABLE in text


def test_an_unreadable_registry_carries_nothing_and_is_unusable(
    checkout_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, text, payloads = _carry(checkout_root, monkeypatch, operational(), rotated=None)

    assert payloads == []
    assert code == EXIT_UNUSABLE
    assert "the credential registry could not be read" in text


def test_a_dependency_update_record_with_no_repository_is_still_held_under_exit_2(
    checkout_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, text, payloads = _carry(
        checkout_root,
        monkeypatch,
        operational(profile="dependency-update"),
        rotated=ROTATED,
    )

    assert payloads == []
    assert code == EXIT_UNUSABLE
    assert UNDECIDED in text


def test_a_class_the_sds_doesn_t_rotate_is_never_handed_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The canary's row sets rotated_by_sds, but it is no type the SDS rotates."""
    import work_carrier.cli as cli_module

    rows = {
        "openrouter-generic": RegistryEntry(REGISTRY, "c", {"class": "openrouter-key"}),
        "rotation-canary": RegistryEntry(
            "AlobarQuest/orchestrator", "c", {"class": "rotation-canary"}
        ),
    }
    monkeypatch.setattr(cli_module, "rotated_by_sds", lambda mirror: rows)
    monkeypatch.setattr(cli_module, "default_mirror_root", lambda program: tmp_path / program)
    waiting = cast(list[Prepared], [SimpleNamespace(payload=operational())])
    assert cli_module._handed_over(waiting, io.StringIO()) == {"openrouter-generic": REGISTRY}


def test_an_unreadable_registry_holds_a_rotation_undecided_and_nothing_else() -> None:
    verdict = assess_operational(operational(), None, ROTATION_GRANTS)
    assert verdict.decision == UNDECIDED
    assert [c.verdict for c in verdict.constraints if c.name == "opts in"] == [UNKNOWN]
