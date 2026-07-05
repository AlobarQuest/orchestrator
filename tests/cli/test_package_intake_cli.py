import shutil
from pathlib import Path

import pytest

import orchestrator.package_sources as package_sources
from orchestrator.package_sources import (
    PackageSourceError,
    canonical_package_hash,
    load_package_intake_payload,
)


def test_package_source_reader_builds_intake_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        package_sources,
        "_verify_current_approval",
        lambda *args: True,
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
        lambda path, approved_hash, revision, approver: False,
    )

    with pytest.raises(PackageSourceError, match="approval verification failed"):
        load_package_intake_payload(
            Path("tests/fixtures/intent-packages/ws32-approved-software"),
            source_repository="AlobarQuest/intent-packages",
        )


def test_package_source_reader_raises_when_git_provenance_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(package_sources, "_verify_current_approval", lambda *args: True)
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
    monkeypatch.setattr(package_sources, "_verify_current_approval", lambda *args: True)
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
    monkeypatch.setattr(package_sources, "_verify_current_approval", lambda *args: True)
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
    with pytest.raises(PackageSourceError, match="matching approval"):
        load_package_intake_payload(
            Path("tests/fixtures/intent-packages/ws32-draft-software"),
            source_repository="AlobarQuest/intent-packages",
        )


def test_canonical_package_hash_uses_intent_package_ordering_rules() -> None:
    package = {"status": "draft", "\ue000": 2, "😀": 1}

    assert (
        canonical_package_hash(package)
        == "04208f6cdb854e2ab1b07dd3633a39dec854344fe72824cf7f2fdb4e2e33129e"
    )
