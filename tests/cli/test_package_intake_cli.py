from pathlib import Path

import pytest

from orchestrator.package_sources import (
    PackageSourceError,
    canonical_package_hash,
    load_package_intake_payload,
)


def test_package_source_reader_builds_intake_payload() -> None:
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

