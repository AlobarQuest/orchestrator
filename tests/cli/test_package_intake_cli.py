import shutil
from pathlib import Path

import pytest

import orchestrator.package_sources as package_sources
from orchestrator.package_sources import (
    PackageSourceError,
    VerifiedApproval,
    canonical_package_hash,
    load_package_intake_payload,
)


def _verified_approval() -> VerifiedApproval:
    return VerifiedApproval(
        approved_by="devon",
        approved_at="2026-07-05T00:02:00Z",
        approval_event_id="22222222-2222-2222-2222-222222222222",
        approval_ledger_commit="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )


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


def test_package_source_reader_requires_verified_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        package_sources,
        "_verify_current_approval",
        lambda *args: None,
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
    monkeypatch.setattr(package_sources, "_git_head", lambda path: (_ for _ in ()).throw(
        PackageSourceError("git provenance unavailable")
    ))

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


def test_package_source_reader_requires_cli_verifier_for_cli_verified_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FACTORY_EVENTS_HOME", str(tmp_path))
    monkeypatch.setattr(package_sources, "_verify_with_intent_packages_cli", lambda path: None)
    monkeypatch.setattr(package_sources, "_verify_factory_chain", lambda: True)
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
            "approvals:\n"
            "  - revision: 1\n",
            "approvals:\n"
            "  - malformed\n"
            "  - revision: 1\n",
        ),
        encoding="utf-8",
    )

    with pytest.raises(PackageSourceError, match="approval entries must be mappings"):
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
            "acceptance:\n"
            "  - id: AC-001\n",
            "acceptance:\n"
            "  - malformed\n"
            "  - id: AC-001\n",
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
    monkeypatch.setattr(package_sources, "_verify_factory_chain", lambda: True)
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
