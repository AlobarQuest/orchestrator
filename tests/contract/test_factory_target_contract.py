"""`factory-target.toml` is read by the work carrier and project-standards, held to one case table.

Admission no longer reads the file (ADR-0058): the member record's grant is its authority. The
carrier's reader and project-standards'
`portfolio.factory_target.parse_declaration` (which the conformance kit's `runner.caller` and
`portfolio lint` read through) remain. Neither can import the other across the repository
boundary, so each repository carries a byte-identical
`tests/fixtures/factory_target_declarations.json` and pins it with the same `CONTRACT_SHA256`.
A one-sided edit to the table reds the repository that was not updated; a parser that stops
agreeing with the table reds its own repository.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from work_carrier.declaration import parse as carrier_parse

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "factory_target_declarations.json"
CONTRACT_SHA256 = "64d31a44f56c9a34b6bb5f24a143b046fb459a678f66b8e5c263a96ed3ae6469"


def golden_cases() -> list[dict[str, Any]]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


def test_the_case_table_is_unchanged() -> None:
    """A one-sided edit here means project-standards' copy has silently drifted."""
    canonical = json.dumps(
        json.loads(FIXTURE.read_text(encoding="utf-8")), sort_keys=True, separators=(",", ":")
    )
    assert hashlib.sha256(canonical.encode()).hexdigest() == CONTRACT_SHA256


def test_the_case_table_covers_all_three_answers() -> None:
    """Without this, a table of only well-formed files would agree trivially."""
    assert {case["target"] for case in golden_cases()} == {True, False, None}


@pytest.mark.parametrize("case", golden_cases(), ids=lambda case: case["name"])
def test_the_work_carriers_reader_answers_what_the_table_says(case: dict[str, Any]) -> None:
    assert carrier_parse(case["text"]).target is case["target"]
