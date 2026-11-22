"""The unit verifier's launcher."""

from __future__ import annotations

from pathlib import Path

NEW = Path("scripts/run-unit-verifier.sh")


def test_it_arms_the_new_check_and_keeps_the_identity_and_the_program() -> None:
    text = NEW.read_text()
    assert 'sds_deadman_arm sds-unit-verifier --finding 2 "$@"' in text
    assert "sds-rotation-verifier" not in text
    assert 'ORCHESTRATOR_API_CREDENTIAL_KEY_ID="orchestrator-verifier"' in text
    assert '"$REPO_ROOT/.venv/bin/unit-verifier" "$@"' in text
