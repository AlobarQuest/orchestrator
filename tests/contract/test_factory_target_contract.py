"""`factory-target.toml` has three readers in two repositories, held to one case table.

The orchestrator's admission reader and the work carrier's reader live here; project-standards'
`portfolio.factory_target.parse_declaration` is the third, and the conformance kit's
`runner.caller` and `portfolio lint` read through it. None can import another across the
repository boundary, so each repository carries a byte-identical
`tests/fixtures/factory_target_declarations.json` and pins it with the same `CONTRACT_SHA256`.
A one-sided edit to the table reds the repository that was not updated; a parser that stops
agreeing with the table reds its own repository.

The table answers what a file's OWN BYTES say. Absence of the file, and a repository that does
not answer, are properties of the read rather than the bytes, and are covered by
`tests/services/test_factory_target.py`.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from orchestrator.services.execution.factory_target import FILENAME, GitHubFactoryTargetSource
from work_carrier.declaration import parse as carrier_parse

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "factory_target_declarations.json"
CONTRACT_SHA256 = "64d31a44f56c9a34b6bb5f24a143b046fb459a678f66b8e5c263a96ed3ae6469"
REPOSITORY = "AlobarQuest/intent-packages"


def golden_cases() -> list[dict[str, Any]]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


def _admission_answer(text: str) -> bool | None:
    """Through the real client: the admission parser is private to its module."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/repos/{REPOSITORY}/contents/{FILENAME}"
        return httpx.Response(200, text=text)

    source = GitHubFactoryTargetSource(
        lambda: "installation-token", transport=httpx.MockTransport(handler)
    )
    return source.declaration_for(REPOSITORY).target


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
def test_the_admission_reader_answers_what_the_table_says(case: dict[str, Any]) -> None:
    assert _admission_answer(case["text"]) is case["target"]


@pytest.mark.parametrize("case", golden_cases(), ids=lambda case: case["name"])
def test_the_work_carriers_reader_answers_what_the_table_says(case: dict[str, Any]) -> None:
    assert carrier_parse(case["text"]).target is case["target"]
