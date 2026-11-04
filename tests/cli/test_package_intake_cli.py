import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

import orchestrator.package_sources as package_sources
from orchestrator.cli import app
from orchestrator.package_sources import (
    PackageSourceError,
    VerifiedApproval,
    canonical_package_hash,
    load_package_intake_payload,
    load_protocol_fixture_intake_payload,
)


def _verified_approval() -> VerifiedApproval:
    return VerifiedApproval(
        approved_by="devon",
        approved_at="2026-07-05T00:02:00Z",
        approval_event_id="22222222-2222-2222-2222-222222222222",
        approval_ledger_commit="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )


def test_the_payload_carries_the_change_record_when_one_caused_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0026. The one field here that is NOT derived from the package.

    A package cannot know what caused the work, so this value is supplied by whoever invokes the
    emitter -- the carry, from the approved change-manager record it is carrying. Without this
    the whole join is emitted by nothing: every other test of the join builds a
    `PackageIntakeCommand` directly and never runs the emitter.
    """
    monkeypatch.setattr(
        package_sources,
        "_verify_current_approval",
        lambda *args: _verified_approval(),
        raising=False,
    )
    monkeypatch.setattr(package_sources, "_git_head", lambda path: "deadbeef", raising=False)
    payload = load_package_intake_payload(
        Path("tests/fixtures/intent-packages/ws32-approved-software"),
        source_repository="AlobarQuest/intent-packages",
        change_record_id=4321,
    )
    assert payload["change_record_id"] == 4321


def test_the_payload_OMITS_the_key_when_nothing_caused_the_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Absent, not null -- and the difference is pinned here or it is pinned nowhere.

    Absent means nobody named a cause, which is every payload emitted before ADR-0026. Emitting
    `null` would work at the schema (the field defaults to None) and would change the bytes of
    every payload this command has ever produced, which is a larger claim than the one intended.
    """
    monkeypatch.setattr(
        package_sources,
        "_verify_current_approval",
        lambda *args: _verified_approval(),
        raising=False,
    )
    monkeypatch.setattr(package_sources, "_git_head", lambda path: "deadbeef", raising=False)
    payload = load_package_intake_payload(
        Path("tests/fixtures/intent-packages/ws32-approved-software"),
        source_repository="AlobarQuest/intent-packages",
    )
    assert "change_record_id" not in payload


def test_the_payload_carries_the_originating_observation_when_one_is_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0026 amendment 1, arriving the same way the change record does and from the same
    record. Relayed as the STRING the carry holds: whether it names a row is a question only the
    orchestrator's own database can answer, and it answers it at intake."""
    monkeypatch.setattr(
        package_sources,
        "_verify_current_approval",
        lambda *args: _verified_approval(),
        raising=False,
    )
    monkeypatch.setattr(package_sources, "_git_head", lambda path: "deadbeef", raising=False)
    payload = load_package_intake_payload(
        Path("tests/fixtures/intent-packages/ws32-approved-software"),
        source_repository="AlobarQuest/intent-packages",
        originating_observation_id="7a1f0f3e-2b6d-4a0e-9c2f-8d1b5a4c6e70",
    )
    assert payload["originating_observation_id"] == "7a1f0f3e-2b6d-4a0e-9c2f-8d1b5a4c6e70"


def test_the_payload_OMITS_the_observation_key_when_no_signal_is_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Absent, not null, on the same terms as the change record: every payload emitted before
    this key lacked it, and the intake replay's legacy exemption keys on the key being MISSING
    from the stored event."""
    monkeypatch.setattr(
        package_sources,
        "_verify_current_approval",
        lambda *args: _verified_approval(),
        raising=False,
    )
    monkeypatch.setattr(package_sources, "_git_head", lambda path: "deadbeef", raising=False)
    payload = load_package_intake_payload(
        Path("tests/fixtures/intent-packages/ws32-approved-software"),
        source_repository="AlobarQuest/intent-packages",
    )
    assert "originating_observation_id" not in payload


def test_package_source_reader_builds_intake_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        package_sources,
        "_verify_current_approval",
        lambda *args: _verified_approval(),
        raising=False,
    )
    monkeypatch.setattr(package_sources, "_git_head", lambda path: "deadbeef", raising=False)
    payload = load_package_intake_payload(
        Path("tests/fixtures/intent-packages/ws32-approved-software"),
        source_repository="AlobarQuest/intent-packages",
    )

    assert payload["package_id"] == "ws32-approved-software"
    assert payload["revision"] == 1
    assert payload["status_at_intake"] == "approved"
    assert payload["verification_mode"] == "caller_attested_cli_verified"
    assert payload["source_repository"] == "AlobarQuest/intent-packages"
    assert payload["approved_by"] == "devon"
    assert payload["approved_at"] == "2026-07-05T00:02:00Z"
    assert payload["approval_event_id"] == "22222222-2222-2222-2222-222222222222"
    assert payload["approval_ledger_commit"] == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert payload["acceptance_criteria"] == [
        {
            "ac_id": "AC-001",
            "condition": "The change is tested.",
            "evidence_type": "automated_test",
            "evidence": "gate: focused tests pass",
            "approver": "policy",
        }
    ]


def _refuse_approval(*args: object) -> VerifiedApproval:
    raise PackageSourceError("approval verification failed: stubbed cause")


def test_package_source_reader_requires_verified_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        package_sources,
        "_verify_current_approval",
        _refuse_approval,
    )

    with pytest.raises(PackageSourceError, match="approval verification failed"):
        load_package_intake_payload(
            Path("tests/fixtures/intent-packages/ws32-approved-software"),
            source_repository="AlobarQuest/intent-packages",
        )


def test_package_source_reader_raises_when_git_provenance_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        package_sources,
        "_verify_current_approval",
        lambda *args: _verified_approval(),
    )
    monkeypatch.setattr(
        package_sources,
        "_git_head",
        lambda path: (_ for _ in ()).throw(PackageSourceError("git provenance unavailable")),
    )

    with pytest.raises(PackageSourceError, match="git provenance unavailable"):
        load_package_intake_payload(
            Path("tests/fixtures/intent-packages/ws32-approved-software"),
            source_repository="AlobarQuest/intent-packages",
        )


def test_package_source_reader_normalizes_source_path_with_resolved_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        package_sources,
        "_verify_current_approval",
        lambda *args: _verified_approval(),
    )
    monkeypatch.setattr(package_sources, "_git_head", lambda path: "deadbeef")
    fixture_dir = Path("tests/fixtures/intent-packages/ws32-approved-software").resolve()
    symlink_dir = tmp_path / "fixture-link"
    symlink_dir.symlink_to(fixture_dir, target_is_directory=True)

    payload = load_package_intake_payload(
        symlink_dir,
        source_repository="AlobarQuest/intent-packages",
    )

    assert payload["source_path"] == str(fixture_dir)


def test_package_source_reader_rejects_ambiguous_matching_approvals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        package_sources,
        "_verify_current_approval",
        lambda *args: _verified_approval(),
    )
    monkeypatch.setattr(package_sources, "_git_head", lambda path: "deadbeef")
    package_dir = tmp_path / "ws32-approved-software"
    shutil.copytree("tests/fixtures/intent-packages/ws32-approved-software", package_dir)
    lineage_path = package_dir / "lineage.yaml"
    original = lineage_path.read_text(encoding="utf-8")
    lineage_path.write_text(
        original.replace(
            "approvals:\n"
            "  - revision: 1\n"
            "    approved_hash: bfcf35c540a540efcac4eb4095b9dbf33529e39361a03a21d43b64c96dd054b2\n"
            "    approver: devon\n"
            '    approved_at: "2026-07-05T00:02:00Z"\n'
            '    commit: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"\n'
            '    event_id: "22222222-2222-2222-2222-222222222222"\n',
            "approvals:\n"
            "  - revision: 1\n"
            "    approved_hash: bfcf35c540a540efcac4eb4095b9dbf33529e39361a03a21d43b64c96dd054b2\n"
            "    approver: mallory\n"
            '    approved_at: "2026-07-04T23:59:00Z"\n'
            '    commit: "ffffffffffffffffffffffffffffffffffffffff"\n'
            '    event_id: "99999999-9999-9999-9999-999999999999"\n'
            "  - revision: 1\n"
            "    approved_hash: bfcf35c540a540efcac4eb4095b9dbf33529e39361a03a21d43b64c96dd054b2\n"
            "    approver: devon\n"
            '    approved_at: "2026-07-05T00:02:00Z"\n'
            '    commit: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"\n'
            '    event_id: "22222222-2222-2222-2222-222222222222"\n',
        ),
        encoding="utf-8",
    )

    with pytest.raises(PackageSourceError, match="ambiguous matching approvals"):
        load_package_intake_payload(
            package_dir,
            source_repository="AlobarQuest/intent-packages",
        )


def test_package_source_reader_rejects_unapproved_package() -> None:
    with pytest.raises(PackageSourceError, match="not approved for intake"):
        load_package_intake_payload(
            Path("tests/fixtures/intent-packages/ws32-draft-software"),
            source_repository="AlobarQuest/intent-packages",
        )


def test_package_source_reader_rejects_unapproved_current_state_with_matching_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        package_sources,
        "_verify_current_approval",
        lambda *args: _verified_approval(),
    )
    package_dir = tmp_path / "ws32-approved-software"
    shutil.copytree("tests/fixtures/intent-packages/ws32-approved-software", package_dir)
    package_path = package_dir / "package.yaml"
    lineage_path = package_dir / "lineage.yaml"
    package_path.write_text(
        package_path.read_text(encoding="utf-8").replace("status: approved", "status: draft"),
        encoding="utf-8",
    )
    lineage_path.write_text(
        lineage_path.read_text(encoding="utf-8").replace(
            "current_state: approved",
            "current_state: draft",
        ),
        encoding="utf-8",
    )

    with pytest.raises(PackageSourceError, match="not approved for intake"):
        load_package_intake_payload(
            package_dir,
            source_repository="AlobarQuest/intent-packages",
        )


def test_protocol_fixture_reader_accepts_closed_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        package_sources,
        "_verify_current_approval",
        lambda *args: _verified_approval(),
    )
    monkeypatch.setattr(package_sources, "_git_head", lambda path: "deadbeef")
    package_dir = tmp_path / "ws32-closed-software"
    shutil.copytree("tests/fixtures/intent-packages/ws32-approved-software", package_dir)
    (package_dir / "package.yaml").write_text(
        (package_dir / "package.yaml")
        .read_text(encoding="utf-8")
        .replace("status: approved", "status: closed"),
        encoding="utf-8",
    )
    (package_dir / "lineage.yaml").write_text(
        (package_dir / "lineage.yaml")
        .read_text(encoding="utf-8")
        .replace("current_state: approved", "current_state: closed"),
        encoding="utf-8",
    )

    payload = load_protocol_fixture_intake_payload(
        package_dir,
        source_repository="AlobarQuest/intent-packages",
    )

    assert payload["status_at_intake"] == "closed"
    assert payload["intake_purpose"] == "protocol_fixture"


def test_package_source_reader_requires_cli_verifier_for_cli_verified_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FACTORY_EVENTS_HOME", str(tmp_path))
    monkeypatch.setattr(package_sources, "_verify_with_intent_packages_cli", lambda path: None)
    monkeypatch.setattr(package_sources, "_verify_factory_chain", lambda: None)
    monkeypatch.setattr(package_sources, "_is_human_operator", lambda agent_id: agent_id == "devon")

    events_file = tmp_path / "events.jsonl"
    events_file.write_text(
        '{"event":{"action":"package.approved","event_id":"22222222-2222-2222-2222-222222222222",'
        '"timestamp":"2026-07-05T00:02:00Z","source":{"ref":"ws32-approved-software"},'
        '"evidence":[{"approved_hash":"bfcf35c540a540efcac4eb4095b9dbf33529e39361a03a21d43b64c96dd054b2",'
        '"revision":1,"approver":"devon","commit":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}]}}\n',
        encoding="utf-8",
    )

    with pytest.raises(PackageSourceError, match="approval verification failed"):
        load_package_intake_payload(
            Path("tests/fixtures/intent-packages/ws32-approved-software"),
            source_repository="AlobarQuest/intent-packages",
        )


def test_package_source_reader_rejects_false_cli_verifier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FACTORY_EVENTS_HOME", str(tmp_path))
    monkeypatch.setattr(package_sources, "_verify_with_intent_packages_cli", lambda path: False)
    monkeypatch.setattr(package_sources, "_verify_factory_chain", lambda: None)
    monkeypatch.setattr(package_sources, "_is_human_operator", lambda agent_id: agent_id == "devon")

    events_file = tmp_path / "events.jsonl"
    events_file.write_text(
        '{"event":{"action":"package.approved","event_id":"22222222-2222-2222-2222-222222222222",'
        '"timestamp":"2026-07-05T00:02:00Z","source":{"ref":"ws32-approved-software"},'
        '"evidence":[{"approved_hash":"bfcf35c540a540efcac4eb4095b9dbf33529e39361a03a21d43b64c96dd054b2",'
        '"revision":1,"approver":"devon","commit":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}]}}\n',
        encoding="utf-8",
    )

    with pytest.raises(PackageSourceError, match="approval verification failed"):
        load_package_intake_payload(
            Path("tests/fixtures/intent-packages/ws32-approved-software"),
            source_repository="AlobarQuest/intent-packages",
        )


@pytest.mark.parametrize(
    ("filename", "replacement", "error"),
    [
        ("package.yaml", "package_id: duplicate\npackage_id: duplicate\n", "duplicate key"),
        ("package.yaml", "---\npackage_id: one\n---\npackage_id: two\n", "exactly one"),
        ("package.yaml", "- not\n- a\n- mapping\n", "must contain a mapping"),
        ("lineage.yaml", "package_id: duplicate\npackage_id: duplicate\n", "duplicate key"),
        ("lineage.yaml", "---\npackage_id: one\n---\npackage_id: two\n", "exactly one"),
        ("lineage.yaml", "- not\n- a\n- mapping\n", "must contain a mapping"),
    ],
)
def test_package_source_reader_rejects_fail_open_yaml_shapes(
    tmp_path: Path,
    filename: str,
    replacement: str,
    error: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        package_sources,
        "_verify_current_approval",
        lambda *args: _verified_approval(),
    )
    package_dir = tmp_path / "ws32-approved-software"
    shutil.copytree("tests/fixtures/intent-packages/ws32-approved-software", package_dir)
    (package_dir / filename).write_text(replacement, encoding="utf-8")

    with pytest.raises(PackageSourceError, match=error):
        load_package_intake_payload(
            package_dir,
            source_repository="AlobarQuest/intent-packages",
        )


def test_package_source_reader_rejects_non_mapping_approval_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        package_sources,
        "_verify_current_approval",
        lambda *args: _verified_approval(),
    )
    package_dir = tmp_path / "ws32-approved-software"
    shutil.copytree("tests/fixtures/intent-packages/ws32-approved-software", package_dir)
    lineage_path = package_dir / "lineage.yaml"
    lineage_path.write_text(
        lineage_path.read_text(encoding="utf-8").replace(
            "approvals:\n  - revision: 1\n",
            "approvals:\n  - malformed\n  - revision: 1\n",
        ),
        encoding="utf-8",
    )

    with pytest.raises(PackageSourceError, match="approval entries must be mappings"):
        load_package_intake_payload(
            package_dir,
            source_repository="AlobarQuest/intent-packages",
        )


def test_package_source_reader_rejects_malformed_approval_mapping_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        package_sources,
        "_verify_current_approval",
        lambda *args: _verified_approval(),
    )
    package_dir = tmp_path / "ws32-approved-software"
    shutil.copytree("tests/fixtures/intent-packages/ws32-approved-software", package_dir)
    lineage_path = package_dir / "lineage.yaml"
    lineage_path.write_text(
        lineage_path.read_text(encoding="utf-8").replace(
            "approvals:\n  - revision: 1\n",
            "approvals:\n  - revision: 999\n    approved_hash: irrelevant\n  - revision: 1\n",
        ),
        encoding="utf-8",
    )

    with pytest.raises(PackageSourceError, match="approval entry missing approver"):
        load_package_intake_payload(
            package_dir,
            source_repository="AlobarQuest/intent-packages",
        )


def test_package_source_reader_rejects_non_mapping_acceptance_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        package_sources,
        "_verify_current_approval",
        lambda *args: _verified_approval(),
    )
    package_dir = tmp_path / "ws32-approved-software"
    shutil.copytree("tests/fixtures/intent-packages/ws32-approved-software", package_dir)
    package_path = package_dir / "package.yaml"
    package_path.write_text(
        package_path.read_text(encoding="utf-8").replace(
            "acceptance:\n  - id: AC-001\n",
            "acceptance:\n  - malformed\n  - id: AC-001\n",
        ),
        encoding="utf-8",
    )

    with pytest.raises(PackageSourceError, match="acceptance entries must be mappings"):
        load_package_intake_payload(
            package_dir,
            source_repository="AlobarQuest/intent-packages",
        )


def test_package_source_reader_rejects_missing_acceptance_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        package_sources,
        "_verify_current_approval",
        lambda *args: _verified_approval(),
    )
    package_dir = tmp_path / "ws32-approved-software"
    shutil.copytree("tests/fixtures/intent-packages/ws32-approved-software", package_dir)
    package_path = package_dir / "package.yaml"
    package_path.write_text(
        package_path.read_text(encoding="utf-8").replace(
            "    approver: policy\n",
            "",
        ),
        encoding="utf-8",
    )

    with pytest.raises(PackageSourceError, match="acceptance entry missing approver"):
        load_package_intake_payload(
            package_dir,
            source_repository="AlobarQuest/intent-packages",
        )


def test_package_source_reader_binds_payload_to_verified_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def verify(
        path: Path,
        package_id: str,
        approved_hash: str,
        revision: object,
        approval: dict[str, object],
    ) -> VerifiedApproval | None:
        assert approval["approver"] == "devon"
        return VerifiedApproval(
            approved_by="devon",
            approved_at="2026-07-05T00:02:00Z",
            approval_event_id="evt-verified",
            approval_ledger_commit="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        )

    monkeypatch.setattr(package_sources, "_verify_current_approval", verify)
    monkeypatch.setattr(package_sources, "_git_head", lambda path: "deadbeef")

    payload = load_package_intake_payload(
        Path("tests/fixtures/intent-packages/ws32-approved-software"),
        source_repository="AlobarQuest/intent-packages",
    )

    assert payload["approval_event_id"] == "evt-verified"
    assert payload["approval_ledger_commit"] == "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def test_package_source_reader_exact_event_verification_rejects_forged_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FACTORY_EVENTS_HOME", str(tmp_path))
    monkeypatch.setattr(package_sources, "_verify_with_intent_packages_cli", lambda path: True)
    monkeypatch.setattr(package_sources, "_verify_factory_chain", lambda: None)
    monkeypatch.setattr(package_sources, "_is_human_operator", lambda agent_id: agent_id == "devon")

    events_file = tmp_path / "events.jsonl"
    events_file.write_text(
        '{"event":{"action":"package.approved","event_id":"evt-real",'
        '"timestamp":"2026-07-05T00:02:00Z","source":{"ref":"ws32-approved-software"},'
        '"evidence":[{"approved_hash":"bfcf35c540a540efcac4eb4095b9dbf33529e39361a03a21d43b64c96dd054b2",'
        '"revision":1,"approver":"devon","commit":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}]}}\n',
        encoding="utf-8",
    )

    package_dir = tmp_path / "ws32-approved-software"
    shutil.copytree("tests/fixtures/intent-packages/ws32-approved-software", package_dir)
    lineage_path = package_dir / "lineage.yaml"
    lineage_path.write_text(
        lineage_path.read_text(encoding="utf-8").replace(
            'event_id: "22222222-2222-2222-2222-222222222222"',
            'event_id: "evt-forged"',
        ),
        encoding="utf-8",
    )

    with pytest.raises(PackageSourceError, match="approval verification failed"):
        load_package_intake_payload(
            package_dir,
            source_repository="AlobarQuest/intent-packages",
        )


def test_canonical_package_hash_uses_intent_package_ordering_rules() -> None:
    package = {"status": "draft", "\ue000": 2, "😀": 1}

    assert (
        canonical_package_hash(package)
        == "04208f6cdb854e2ab1b07dd3633a39dec854344fe72824cf7f2fdb4e2e33129e"
    )


def test_intake_package_reads_approved_fixture_and_posts_expected_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    monkeypatch.setattr(
        package_sources,
        "_verify_current_approval",
        lambda *args: _verified_approval(),
    )
    monkeypatch.setattr(package_sources, "_git_head", lambda path: "deadbeef")

    def fake_request(method: str, path: str, payload=None):
        observed.update(method=method, path=path, payload=payload)
        return {"id": "revision-1", "revision": 1}

    monkeypatch.setattr("orchestrator.cli.request", fake_request)
    result = CliRunner().invoke(
        app,
        [
            "intake-package",
            "tests/fixtures/intent-packages/ws32-approved-software",
            "--source-repository",
            "AlobarQuest/intent-packages",
            "--idempotency-key",
            "package-intake-1",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"id": "revision-1", "revision": 1}
    assert observed["method"] == "POST"
    assert observed["path"] == "/api/v1/package-intakes"
    payload = observed["payload"]
    assert isinstance(payload, dict)
    assert payload["idempotency_key"] == "package-intake-1"
    assert payload["expected_version"] == 0
    assert payload["package_id"] == "ws32-approved-software"
    assert payload["source_repository"] == "AlobarQuest/intent-packages"
    assert payload["source_commit"] == "deadbeef"
    assert payload["verification_mode"] == "caller_attested_cli_verified"


def test_intake_protocol_fixture_reads_closed_fixture_and_posts_expected_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    monkeypatch.setattr(
        package_sources,
        "_verify_current_approval",
        lambda *args: _verified_approval(),
    )
    monkeypatch.setattr(package_sources, "_git_head", lambda path: "deadbeef")
    package_dir = tmp_path / "ws32-closed-software"
    shutil.copytree("tests/fixtures/intent-packages/ws32-approved-software", package_dir)
    (package_dir / "package.yaml").write_text(
        (package_dir / "package.yaml")
        .read_text(encoding="utf-8")
        .replace("status: approved", "status: closed"),
        encoding="utf-8",
    )
    (package_dir / "lineage.yaml").write_text(
        (package_dir / "lineage.yaml")
        .read_text(encoding="utf-8")
        .replace("current_state: approved", "current_state: closed"),
        encoding="utf-8",
    )

    def fake_request(method: str, path: str, payload=None):
        observed.update(method=method, path=path, payload=payload)
        return {"id": "revision-1", "revision": 1}

    monkeypatch.setattr("orchestrator.cli.request", fake_request)
    result = CliRunner().invoke(
        app,
        [
            "intake-protocol-fixture",
            str(package_dir),
            "--source-repository",
            "AlobarQuest/intent-packages",
            "--idempotency-key",
            "fixture-intake-1",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert observed["method"] == "POST"
    assert observed["path"] == "/api/v1/package-intakes"
    payload = observed["payload"]
    assert isinstance(payload, dict)
    assert payload["idempotency_key"] == "fixture-intake-1"
    assert payload["expected_version"] == 0
    assert payload["status_at_intake"] == "closed"
    assert payload["intake_purpose"] == "protocol_fixture"


_GOOD_APPROVAL = {"approver": "devon", "commit": "abc123", "event_id": "evt-1"}


def _stub_every_check_passing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(package_sources, "_is_recognised_approver", lambda approver: True)
    monkeypatch.setattr(package_sources, "_verify_with_intent_packages_cli", lambda path: True)
    monkeypatch.setattr(package_sources, "_verify_factory_chain", lambda: None)
    monkeypatch.setattr(package_sources, "_iter_package_approval_events", lambda: [])


def _verify_with(
    approval: dict[str, object] | None = None, revision: object = 1
) -> VerifiedApproval:
    return package_sources._verify_current_approval(
        Path("tests/fixtures/intent-packages/ws32-approved-software"),
        "ws32-approved-software",
        "hash",
        revision,
        _GOOD_APPROVAL if approval is None else approval,
    )


@pytest.mark.parametrize(
    ("approval", "revision", "cause"),
    [
        (None, "1", "revision is not an integer"),
        ({**_GOOD_APPROVAL, "approver": "nobody"}, 1, "approver is not recognised"),
        ({**_GOOD_APPROVAL, "commit": ""}, 1, "approval commit is empty"),
        ({**_GOOD_APPROVAL, "event_id": ""}, 1, "approval event id is empty"),
    ],
)
def test_a_malformed_approval_names_the_field_that_is_wrong(
    monkeypatch: pytest.MonkeyPatch,
    approval: dict[str, object] | None,
    revision: object,
    cause: str,
) -> None:
    _stub_every_check_passing(monkeypatch)
    monkeypatch.setattr(package_sources, "_is_recognised_approver", lambda a: a == "devon")

    with pytest.raises(PackageSourceError, match=f"^approval verification failed: {cause}$"):
        _verify_with(approval, revision)


def test_a_missing_intent_packages_checkout_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_every_check_passing(monkeypatch)
    monkeypatch.setattr(package_sources, "_verify_with_intent_packages_cli", lambda path: None)

    with pytest.raises(
        PackageSourceError,
        match="^approval verification failed: intent-packages checkout is unavailable$",
    ):
        _verify_with()


def test_a_failing_verify_approval_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_every_check_passing(monkeypatch)
    monkeypatch.setattr(package_sources, "_verify_with_intent_packages_cli", lambda path: False)

    with pytest.raises(
        PackageSourceError,
        match="^approval verification failed: intent-packages verify-approval failed$",
    ):
        _verify_with()


def _chain_with(monkeypatch: pytest.MonkeyPatch, *, run: object) -> None:
    monkeypatch.setattr(package_sources, "_is_recognised_approver", lambda approver: True)
    monkeypatch.setattr(package_sources, "_verify_with_intent_packages_cli", lambda path: True)
    monkeypatch.setattr(package_sources, "_security_standards_dir", lambda: Path("."))
    monkeypatch.setattr(package_sources.subprocess, "run", run)


def test_a_missing_security_standards_checkout_is_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _chain_with(monkeypatch, run=None)
    monkeypatch.setattr(package_sources, "_security_standards_dir", lambda: None)

    with pytest.raises(
        PackageSourceError,
        match="^approval verification failed: security-standards checkout is unavailable$",
    ):
        _verify_with()


def test_a_chain_verify_that_exits_non_zero_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    _chain_with(monkeypatch, run=lambda *a, **k: subprocess.CompletedProcess(a, 1))

    with pytest.raises(
        PackageSourceError,
        match="^approval verification failed: factory event chain did not verify$",
    ):
        _verify_with()


@pytest.mark.parametrize(
    "error", [OSError("no python"), subprocess.TimeoutExpired(cmd="x", timeout=1)]
)
def test_a_chain_verify_that_cannot_run_is_named(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    def run(*a: object, **k: object) -> None:
        raise error

    _chain_with(monkeypatch, run=run)

    with pytest.raises(
        PackageSourceError,
        match="^approval verification failed: factory event chain verification could not run$",
    ):
        _verify_with()


def test_an_unreadable_event_log_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_every_check_passing(monkeypatch)
    monkeypatch.setattr(package_sources, "_iter_package_approval_events", lambda: None)

    with pytest.raises(
        PackageSourceError,
        match="^approval verification failed: factory event log is unreadable$",
    ):
        _verify_with()


def test_no_matching_approval_event_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_every_check_passing(monkeypatch)

    with pytest.raises(
        PackageSourceError,
        match="^approval verification failed: no matching package.approved event$",
    ):
        _verify_with()


# ---- The event that proves an approval: each field it must carry ------------------------------

_EVENT = {
    "action": "package.approved",
    "event_id": "evt-1",
    "timestamp": "2026-07-05T00:02:00Z",
    "source": {"ref": "pkg-1"},
    "evidence": [{"approved_hash": "h1", "revision": 1, "approver": "devon", "commit": "c1"}],
}


def _verify_event_log() -> VerifiedApproval:
    return package_sources._verified_approval_from_events(
        package_id="pkg-1",
        approved_hash="h1",
        revision=1,
        approver="devon",
        commit="c1",
        event_id="evt-1",
    )


def _event_with(**changes: object) -> dict[str, object]:
    return {**_EVENT, **changes}


def _evidence_with(**changes: object) -> list[dict[str, object]]:
    return [{**_EVENT["evidence"][0], **changes}]


def _log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, events: list[object]) -> None:
    monkeypatch.setenv("FACTORY_EVENTS_HOME", str(tmp_path))
    (tmp_path / "events.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )


def test_an_event_carrying_every_claimed_field_verifies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _log(tmp_path, monkeypatch, [{"event": _EVENT}])

    verified = _verify_event_log()

    assert verified == VerifiedApproval("devon", "2026-07-05T00:02:00Z", "evt-1", "c1")


@pytest.mark.parametrize(
    "event",
    [
        _event_with(event_id="evt-other"),
        _event_with(source={"ref": "pkg-other"}),
        _event_with(source="pkg-1"),
        _event_with(source=None),
        _event_with(evidence=_evidence_with(approved_hash="h-other")),
        _event_with(evidence=_evidence_with(revision=2)),
        _event_with(evidence=_evidence_with(approver="someone")),
        _event_with(evidence=_evidence_with(commit="c-other")),
        _event_with(evidence=None),
        _event_with(timestamp=""),
        _event_with(timestamp=None),
        _event_with(action="package.rejected"),
    ],
    ids=[
        "event-id",
        "source-ref",
        "source-not-a-mapping",
        "source-null",
        "hash",
        "revision",
        "approver",
        "commit",
        "evidence-null",
        "timestamp-empty",
        "timestamp-null",
        "other-action",
    ],
)
def test_an_event_deviating_in_one_field_does_not_verify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, event: dict[str, object]
) -> None:
    _log(tmp_path, monkeypatch, [{"event": event}])

    with pytest.raises(PackageSourceError, match="no matching package.approved event"):
        _verify_event_log()


def test_evidence_that_is_not_a_list_matches_nothing() -> None:
    assert not package_sources._matching_event_evidence(
        tuple(_evidence_with()),
        approved_hash="h1",
        revision=1,
        approver="devon",
        commit="c1",
    )


def test_the_log_skips_other_actions_and_blank_or_broken_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _log(tmp_path, monkeypatch, [{"event": _event_with(action="package.proposed")}, _EVENT])
    with (tmp_path / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("\n not json\n")

    events = package_sources._iter_package_approval_events()

    assert events == [_EVENT]


# ---- The sibling CLI's answer, and who counts as an approver ------------------------------------


@pytest.mark.parametrize(("code", "answer"), [(0, True), (1, False), (2, False)])
def test_the_sibling_cli_answer_is_its_exit_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int, answer: bool
) -> None:
    monkeypatch.setattr(package_sources, "_intent_packages_src", lambda: tmp_path)
    monkeypatch.setattr(
        package_sources.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, code)
    )

    assert package_sources._verify_with_intent_packages_cli(tmp_path) is answer


def _registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **agents: str) -> None:
    (tmp_path / "registry" / "agents").mkdir(parents=True)
    for name, text in agents.items():
        (tmp_path / "registry" / "agents" / f"{name}.yaml").write_text(text, encoding="utf-8")
    monkeypatch.setenv("SECURITY_STANDARDS_DIR", str(tmp_path))


def test_only_an_agent_with_the_human_operator_profile_is_a_human_operator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _registry(
        tmp_path,
        monkeypatch,
        devon="authority_profile: human-operator-v1\n",
        robot="authority_profile: coding-agent-v1\n",
        bare="agent_id: bare\n",
    )

    assert package_sources._is_human_operator("devon") is True
    assert package_sources._is_human_operator("robot") is False
    assert package_sources._is_human_operator("bare") is False
    assert package_sources._is_human_operator("absent") is False


@pytest.mark.parametrize(
    ("approver", "recognised"),
    [
        ("policy:dependency-update@v1", True),
        ("policy:dependency-update@v0", False),
        ("policy:x@v10", True),
        ("policy:x@v01", False),
        ("policy:Bad@v1", False),
        ("policy:-x@v1", False),
        ("policy:@v1", False),
        ("other:x@v1", False),
        ("policy:dependency-update", False),
        ("nobody", False),
    ],
)
def test_a_policy_shaped_approver_is_recognised_without_the_registry(
    monkeypatch: pytest.MonkeyPatch, approver: str, recognised: bool
) -> None:
    monkeypatch.setattr(package_sources, "_is_human_operator", lambda agent_id: False)

    assert package_sources._is_recognised_approver(approver) is recognised


def test_a_float_is_refused_in_the_canonical_hash() -> None:
    with pytest.raises(PackageSourceError, match="floats are not allowed"):
        canonical_package_hash({"budget": 1.5})
