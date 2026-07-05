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
