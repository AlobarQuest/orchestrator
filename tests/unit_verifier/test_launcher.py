"""The unit verifier's launcher header agrees with its arming line. The container behaviour is
tested in `tests/scripts/test_run_unit_verifier.py`."""

from __future__ import annotations

from pathlib import Path

LAUNCHER = Path("scripts/run-unit-verifier.sh")


def test_finding_code_and_header_agree() -> None:
    text = LAUNCHER.read_text()
    assert 'sds_deadman_arm sds-unit-verifier --finding 2 "$@"' in text
    header = text[: text.index("set -uo pipefail")]
    assert "#   2  something was found: a unit went to a person or an alert names it." in header
    assert "#   3  the queue could not be read, or alert-hub could not be told." in header
