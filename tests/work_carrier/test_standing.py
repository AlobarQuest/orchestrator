"""The carrier's standing-approval check (ADR-0055 amendment 22 item 3).

A rotation revision a policy approved is carried only when the carry form refuses nothing and the
lineage's approval is the one the emitter verified in the chain; the carrier then records its check,
under its own identity, before it posts the intake. Each refusal below is driven from the passing
control by changing one input. The package is the one `generate.render` writes for the landing
ledger's real lane-token row, approved by the standing approval's policy approver.
"""

from __future__ import annotations

import ast
import io
import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from rotation_worker.canonical import package_hash
from rotation_worker.registry import RegistryEntry, RegistryError
from tests.rotation_proposer.test_standing_approval import REGISTRY, _generated, _real_row
from tests.work_carrier.test_workability import _Source, member, portfolio, project
from work_carrier import cli as cli_module
from work_carrier import standing
from work_carrier.change_manager import WorkRecord
from work_carrier.cli import EXIT_FINDINGS, EXIT_OK, run
from work_carrier.orchestrator_client import OrchestratorError
from work_carrier.prepare import Prepared, Refused, emit_key
from work_carrier.prepare import standing_approval as standing_gate
from work_carrier.workability import judge

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "landing-ledger-bws-token"
PACKAGE_ID = f"rotation-{TOKEN}"
APPROVER = "policy:non-software-operational@v4"
APPROVAL_COMMIT = "a" * 40
REGISTRY_COMMIT = "b" * 40
EVENT_ID = "evt-standing-1"
APPROVED_AT = "2026-11-11T12:00:00+00:00"
# Planted where a careless report could reach it; no line may ever carry it.
SENTINEL = "placeholder-secret-value-not-a-token"


def _package(**overrides: Any) -> dict[str, Any]:
    return {**_generated(_real_row(TOKEN)), "status": "approved", **overrides}


def _lineage(package: dict[str, Any], **approval: Any) -> dict[str, Any]:
    entry = {
        "revision": 1,
        "approved_hash": package_hash(package),
        "approver": APPROVER,
        "approved_at": APPROVED_AT,
        "commit": APPROVAL_COMMIT,
        "event_id": EVENT_ID,
        **approval,
    }
    return {
        "package_id": package["package_id"],
        "current_state": "approved",
        "revisions": [{"revision": 1, "hash": package_hash(package)}],
        "approvals": [entry],
    }


def _payload(package: dict[str, Any], rec: WorkRecord, **overrides: Any) -> dict[str, Any]:
    """What the emitter returns once it has verified the approval in the chain."""
    return {
        "package_id": package["package_id"],
        "revision": package["revision"],
        "content_hash": package_hash(package),
        "approved_by": APPROVER,
        "approved_at": APPROVED_AT,
        "approval_event_id": EVENT_ID,
        "approval_ledger_commit": APPROVAL_COMMIT,
        "profile": package["profile"],
        "source_repository": rec.package_source_repository,
        "change_record_id": rec.change_record_id,
        "idempotency_key": emit_key(rec),
        "expected_version": 0,
        "enforcement_snapshot": {
            "profile_fields": package["profile_fields"],
            "reach": package["reach"],
        },
        **overrides,
    }


def _write(directory: Path, package: dict[str, Any], lineage: dict[str, Any]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "package.yaml").write_text(yaml.safe_dump(package, sort_keys=False))
    (directory / "lineage.yaml").write_text(yaml.safe_dump(lineage, sort_keys=False))
    return directory


def _entry(row: dict[str, Any] | None = None) -> Callable[[str], RegistryEntry]:
    found = RegistryEntry(REGISTRY, REGISTRY_COMMIT, row if row is not None else _real_row(TOKEN))
    return lambda credential_id: found


def _record() -> WorkRecord:
    return WorkRecord(
        change_record_id=91,
        package_id=PACKAGE_ID,
        package_revision=1,
        package_source_repository="AlobarQuest/intent-packages",
        reasoning="a lane credential's first issue",
        decided_by="work-policy",
    )


def _check(
    tmp_path: Path,
    *,
    package: dict[str, Any] | None = None,
    lineage: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    find_entry: Callable[[str], RegistryEntry] | None = None,
) -> standing.Checked | str:
    package = package if package is not None else _package()
    lineage = lineage if lineage is not None else _lineage(package)
    directory = _write(tmp_path / PACKAGE_ID, package, lineage)
    payload = payload if payload is not None else _payload(package, _record())
    return standing.check(payload, directory, find_entry or _entry())


# ---- the check ------------------------------------------------------------------------------


def test_a_policy_approved_generated_revision_1_passes_and_composes_its_record(
    tmp_path: Path,
) -> None:
    checked = _check(tmp_path)

    assert isinstance(checked, standing.Checked), checked
    observation = checked.observation
    assert observation["source_system"] == "work_carrier"
    assert observation["observation_type"] == "standing_approval_check"
    assert observation["subject_type"] == "package_revision"
    assert observation["subject_reference"] == f"{PACKAGE_ID}@r1"
    assert observation["observed_at"] == APPROVED_AT
    assert observation["facts"] == {
        "package_id": PACKAGE_ID,
        "revision": 1,
        "content_hash": package_hash(_package()),
        "package_commit": APPROVAL_COMMIT,
        "approver": APPROVER,
        "registry_entry": TOKEN,
        "registry_repository": REGISTRY,
        "registry_commit": REGISTRY_COMMIT,
        "refused": [],
    }
    # The reference names the revision and the registry commit, so a moved registry is a new row.
    assert f"{PACKAGE_ID}@r1" in observation["source_reference"]
    assert f"{REGISTRY}@{REGISTRY_COMMIT}" in observation["source_reference"]
    assert len(observation["idempotency_key"]) <= standing.MAX_IDEMPOTENCY_KEY


def test_two_passes_over_one_revision_and_registry_compose_the_same_record(tmp_path: Path) -> None:
    first = _check(tmp_path / "one")
    second = _check(tmp_path / "two")
    assert isinstance(first, standing.Checked) and isinstance(second, standing.Checked)
    assert first.observation == second.observation


def test_a_registry_that_moved_is_a_new_reference(tmp_path: Path) -> None:
    first = _check(tmp_path / "one")
    moved = RegistryEntry(REGISTRY, "c" * 40, _real_row(TOKEN))
    second = _check(tmp_path / "two", find_entry=lambda credential_id: moved)
    assert isinstance(first, standing.Checked) and isinstance(second, standing.Checked)
    assert first.observation["source_reference"] != second.observation["source_reference"]
    assert first.observation["idempotency_key"] != second.observation["idempotency_key"]


def test_a_lineage_approval_the_emitter_didn_t_verify_refuses(tmp_path: Path) -> None:
    """The chain event the emitter matched is the payload's; a lineage naming another approver,
    commit or event id is not the approval the chain holds."""
    package = _package()
    for field, value in (
        ("approver", "policy:non-software-operational@v5"),
        ("commit", "d" * 40),
        ("event_id", "evt-another"),
    ):
        refused = _check(tmp_path / field, lineage=_lineage(package, **{field: value}))
        assert isinstance(refused, str)
        assert "isn't the one the emitter verified" in refused and field in refused


def test_a_checkout_that_no_longer_hashes_to_the_payload_refuses(tmp_path: Path) -> None:
    package = _package()
    payload = _payload(package, _record(), content_hash="0" * 64)
    refused = _check(tmp_path, package=package, payload=payload)
    assert refused == "the checkout no longer hashes to the payload's content_hash"


def test_a_carry_form_refusal_names_its_conditions(tmp_path: Path) -> None:
    """A registry that moved since the approval makes the re-render differ."""
    row = _real_row(TOKEN)
    for consumer in row["consumer"]:
        if consumer["kind"] == "coolify-env":
            consumer["key"] = "LANDING_LEDGER_MOVED_VARIABLE"
    refused = _check(tmp_path, find_entry=_entry(row))
    assert isinstance(refused, str)
    assert refused.startswith("the carry form refused: ")
    assert "proposer_generated" in refused and "destinations" in refused


@pytest.mark.parametrize(
    ("error", "named"),
    [
        (RegistryError(f"{TOKEN}: 0 registry entries, expected exactly 1"), True),
        (RuntimeError(SENTINEL), False),
    ],
)
def test_a_read_that_fails_refuses_and_names_only_what_it_composed(
    tmp_path: Path, error: Exception, named: bool
) -> None:
    def find(credential_id: str) -> RegistryEntry:
        raise error

    refused = _check(tmp_path, find_entry=find)
    assert isinstance(refused, str)
    assert type(error).__name__ in refused
    assert (str(error) in refused) is named
    assert SENTINEL not in refused


def test_a_missing_lineage_refuses(tmp_path: Path) -> None:
    directory = _write(tmp_path / PACKAGE_ID, _package(), _lineage(_package()))
    (directory / "lineage.yaml").unlink()
    refused = standing.check(_payload(_package(), _record()), directory, _entry())
    assert refused == "the carry form could not be run: FileNotFoundError"


# ---- which records the check applies to -----------------------------------------------------


def _prepared(tmp_path: Path, payload: dict[str, Any]) -> Prepared:
    package = _package()
    directory = _write(tmp_path / PACKAGE_ID, package, _lineage(package))
    return Prepared(record=_record(), package_path=directory, payload=payload)


def _unreachable(credential_id: str) -> RegistryEntry:
    raise AssertionError("the registry must not be read for this record")


def test_a_person_approved_rotation_revision_is_carried_as_before(tmp_path: Path) -> None:
    prepared = _prepared(tmp_path, _payload(_package(), _record(), approved_by="devon"))
    assert standing_gate(prepared, _unreachable, identified=False) is prepared


def test_a_policy_approved_bump_is_carried_as_before(tmp_path: Path) -> None:
    payload = _payload(
        _package(),
        _record(),
        approved_by="policy:dependency-update@v3",
        profile="dependency-update",
    )
    prepared = _prepared(tmp_path, payload)
    assert standing_gate(prepared, _unreachable, identified=False) is prepared


def test_the_check_keys_on_the_profile_not_the_record_s_change_class(tmp_path: Path) -> None:
    """A hand-proposed record has no `change_class`; the package's profile is what decides."""
    prepared = _prepared(tmp_path, _payload(_package(), _record()))
    assert prepared.record.change_class is None
    outcome = standing_gate(prepared, _entry(), identified=True)
    assert isinstance(outcome, Prepared) and outcome.standing_observation is not None


def test_no_identity_of_its_own_refuses_before_any_read(tmp_path: Path) -> None:
    prepared = _prepared(tmp_path, _payload(_package(), _record()))
    outcome = standing_gate(prepared, _unreachable, identified=False)
    assert isinstance(outcome, Refused)
    assert outcome.reason == "standing_approval_refused"
    assert "identity" in outcome.detail


# ---- the identity seam ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key_id", "token", "identified"),
    [
        ("work-carrier-observer", "t", True),
        ("", "t", False),
        ("work-carrier-observer", "", False),
        # Never a fallback to the shared SYSTEM key, which any SYSTEM caller holds.
        ("orchestrator-system", "t", False),
        ("  orchestrator-system  ", "t", False),
    ],
)
def test_the_observation_identity_is_never_the_system_key(
    monkeypatch: pytest.MonkeyPatch, key_id: str, token: str, identified: bool
) -> None:
    monkeypatch.setenv("WORK_CARRIER_OBSERVATION_KEY_ID", key_id)
    monkeypatch.setenv("WORK_CARRIER_OBSERVATION_TOKEN", token)
    assert (cli_module._observation_identity() is not None) is identified


# ---- through the carry ----------------------------------------------------------------------


class _Orchestrator:
    """The two clients' doubles, writing to one ordered log so the test sees which came first."""

    def __init__(self, log: list[str], *, fail: bool = False) -> None:
        self.log = log
        self.fail = fail
        self.observations: list[dict] = []
        self.payloads: list[dict] = []

    def carried_revisions(self, change_record_id: int) -> list[str]:
        return []

    def record_observation(self, observation: dict) -> dict:
        self.log.append("observation")
        if self.fail:
            raise OrchestratorError("the orchestrator answered 403 for /api/v1/observations")
        self.observations.append(observation)
        return {"id": "22222222-2222-2222-2222-222222222222"}

    def register_intake(self, payload: dict) -> dict:
        self.log.append("intake")
        self.payloads.append(payload)
        return {"id": "11111111-1111-1111-1111-111111111111"}


def _carry(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    payload_overrides: dict[str, Any] | None = None,
    lineage_overrides: dict[str, Any] | None = None,
    emitter_fails: bool = False,
    recorder: _Orchestrator | None = None,
    argv: tuple[str, ...] = ("--register",),
) -> tuple[int, str, list[str], _Orchestrator, _Orchestrator | None]:
    rec = _record()
    package = _package()
    _write(
        root / "intent-packages" / "packages" / PACKAGE_ID,
        package,
        _lineage(package, **(lineage_overrides or {})),
    )
    emitted = _payload(package, rec, **(payload_overrides or {}))

    def emitter(command, **kwargs):
        if emitter_fails:
            # The emitter refuses an approval with no matching `package.approved` chain event.
            return subprocess.CompletedProcess(
                command, 1, stdout="", stderr="approval verification failed: no matching event"
            )
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(emitted), stderr="")

    import work_carrier.prepare as prepare_module

    original = prepare_module.prepare
    monkeypatch.setattr(cli_module, "prepare", lambda r, **kw: original(r, **kw, runner=emitter))
    monkeypatch.setattr(
        cli_module,
        "judge_workability",
        lambda subjects, out, grants, rotated: judge(
            subjects, out, grants=grants, rotated=rotated, portfolio=portfolio(project())
        ),
    )
    log: list[str] = []
    writer = _Orchestrator(log)
    if recorder is not None:
        recorder.log = log
    code, text = _run(root, rec, writer, recorder, argv)
    return code, text, log, writer, recorder


def _run(root: Path, rec: WorkRecord, writer, recorder, argv: tuple[str, ...]) -> tuple[int, str]:
    """The `run` call, behind unannotated doubles, as `test_workability._run_registering` does."""
    out = io.StringIO()
    code = run(
        ["--checkout-root", str(root), *argv],
        source=_Source([rec]),
        registrar=writer,
        recorder=recorder,
        members=[member(REGISTRY, credential_rotation="granted")],
        rotated={TOKEN: REGISTRY},
        find_entry=_entry(),
        out=out,
    )
    return code, out.getvalue()


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    (tmp_path / "intent-packages" / "packages").mkdir(parents=True)
    return tmp_path


def test_a_pass_records_the_check_before_it_posts_the_intake(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _Orchestrator([])
    code, text, log, writer, _ = _carry(root, monkeypatch, recorder=recorder)

    assert code == EXIT_OK, text
    assert log == ["observation", "intake"]
    assert recorder.observations[0]["facts"]["content_hash"] == writer.payloads[0]["content_hash"]
    assert writer.observations == [], "the intake client never records the check"


def test_a_failed_record_posts_no_intake(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Orchestrator([], fail=True)
    code, text, log, writer, _ = _carry(root, monkeypatch, recorder=recorder)

    assert code == EXIT_FINDINGS
    assert log == ["observation"]
    assert writer.payloads == []


def test_a_refusal_records_nothing_and_posts_nothing(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _Orchestrator([])
    code, text, log, writer, _ = _carry(
        root, monkeypatch, recorder=recorder, lineage_overrides={"event_id": "evt-another"}
    )

    assert code == EXIT_FINDINGS
    assert "standing_approval_refused" in text
    assert log == []


def test_no_matching_chain_event_refuses_at_the_emitter(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _Orchestrator([])
    code, text, log, _, _ = _carry(root, monkeypatch, recorder=recorder, emitter_fails=True)

    assert code == EXIT_FINDINGS
    assert "package_not_intakeable" in text
    assert log == []


def test_an_unset_identity_refuses_and_posts_nothing(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, text, log, writer, _ = _carry(root, monkeypatch, recorder=None)

    assert code == EXIT_FINDINGS
    assert "standing_approval_refused" in text and "identity" in text
    assert log == []


def test_a_system_key_named_as_the_identity_refuses(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WORK_CARRIER_OBSERVATION_KEY_ID", "orchestrator-system")
    monkeypatch.setenv("WORK_CARRIER_OBSERVATION_TOKEN", SENTINEL)
    code, text, log, _, _ = _carry(root, monkeypatch, recorder=None)

    assert code == EXIT_FINDINGS
    assert "standing_approval_refused" in text
    assert log == []
    assert SENTINEL not in text


def test_a_person_approved_rotation_is_registered_with_no_record(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, text, log, writer, _ = _carry(
        root, monkeypatch, recorder=None, payload_overrides={"approved_by": "devon"}
    )

    assert code == EXIT_OK, text
    assert log == ["intake"]


def test_a_bare_pass_checks_and_writes_nothing(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Orchestrator([])
    code, text, log, _, _ = _carry(root, monkeypatch, recorder=recorder, argv=())

    assert code == EXIT_OK, text
    assert log == []


def test_no_report_line_carries_a_value(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Code Brain rule 5: neither the identity's token nor an error's own text reaches the report
    or the record; only ids, commits, conditions and error types do."""
    monkeypatch.setenv("WORK_CARRIER_OBSERVATION_KEY_ID", "work-carrier-observer")
    monkeypatch.setenv("WORK_CARRIER_OBSERVATION_TOKEN", SENTINEL)
    recorder = _Orchestrator([])
    code, text, _, _, _ = _carry(root, monkeypatch, recorder=recorder)

    assert code == EXIT_OK, text
    assert SENTINEL not in text
    assert SENTINEL not in json.dumps(recorder.observations)


def test_a_refusal_carries_no_error_text_it_didn_t_compose(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def leaking(credential_id: str) -> RegistryEntry:
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(cli_module, "_registry_reader", lambda: leaking)
    rec = _record()
    package = _package()
    _write(root / "intent-packages" / "packages" / PACKAGE_ID, package, _lineage(package))
    outcome = standing_gate(
        Prepared(rec, root / "intent-packages" / "packages" / PACKAGE_ID, _payload(package, rec)),
        leaking,
        identified=True,
    )
    assert isinstance(outcome, Refused)
    assert SENTINEL not in outcome.detail


# ---- the carrier imports one module of the proposer -----------------------------------------


def test_the_carrier_imports_only_the_proposer_s_checks() -> None:
    """Amendment 22 item 3: the same checks, not a copy, and never the proposer's CLI or pass."""
    imported = set()
    for path in (ROOT / "src" / "work_carrier").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
    proposer = {name for name in imported if name.split(".")[0] == "rotation_proposer"}
    assert proposer == {"rotation_proposer.standing_approval"}


def test_the_record_the_carrier_composes_is_the_one_the_gate_reads() -> None:
    from orchestrator.services.intake import standing_approval as gate

    assert (gate.CARRIER_SOURCE, gate.CARRIER_CHECK) == (
        standing.SOURCE_SYSTEM,
        standing.OBSERVATION_TYPE,
    )
    assert gate.subject_for(PACKAGE_ID, 1) == standing.subject_for(PACKAGE_ID, 1)
    assert gate.ROTATION_PROFILE == standing.ROTATION_PROFILE
