"""change-manager's bearer setting, orchestrator side (ADR-0055 amendment 9, decision 6).

change-manager holds each machine bearer in this repository's `ORCHESTRATOR_M2M_CREDENTIALS` entry
form, keyed by scope and without `agent_id`, and the rotation executor will write it.
`tests/fixtures/m2m_bearer_setting.json` is byte-identical to change-manager's copy under the same
name, and CONTRACT_SHA256 is the same constant on both sides, so a one-sided edit is loud. The hash
proves only that the file is unchanged; the other tests recompute every verdict with this
repository's parser, so the fixture can't hold a rule the orchestrator doesn't.
"""

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from orchestrator.identity.auth import (
    MAX_PREVIOUS_OVERLAP,
    M2MCredential,
    _hash_accepted,
    previous_hash_valid,
)
from orchestrator.main import _previous_hash

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "m2m_bearer_setting.json"
CONTRACT_SHA256 = "caa7e5a797b3756a72f4e8013a59f1d0b81cbd0e9565ff1f054df8e7fd826238"


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _cases(section: str) -> list[dict[str, Any]]:
    return _fixture()[section]["cases"]


def test_the_fixture_is_unchanged() -> None:
    """A one-sided edit here means change-manager's copy has silently drifted."""
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == CONTRACT_SHA256


def test_the_overlap_bound_is_this_repositorys() -> None:
    assert _fixture()["max_previous_overlap_seconds"] == MAX_PREVIOUS_OVERLAP.total_seconds()


@pytest.mark.parametrize("case", _cases("previous_cases"), ids=lambda case: case["name"])
def test_each_previous_verdict_is_this_parsers(case: dict[str, Any]) -> None:
    section = _fixture()["previous_cases"]
    current = section["current_hash"]
    try:
        credential = M2MCredential(
            agent_id="any",
            token_hash=current,
            previous=_previous_hash({"previous": case["previous"]}),
        )
        valid = previous_hash_valid(
            credential,
            {current, section["other_current_hash"]},
            datetime.fromisoformat(_fixture()["now"]),
        )
    except RuntimeError, TypeError, ValueError:
        valid = False
    assert valid is case["valid"]


@pytest.mark.parametrize("case", _cases("authentication_cases"), ids=lambda case: case["name"])
def test_each_authentication_verdict_is_this_parsers(case: dict[str, Any]) -> None:
    entry = _fixture()["authentication_cases"]["entry"]
    credential = M2MCredential(
        agent_id="any", token_hash=entry["token_hash"], previous=_previous_hash(entry)
    )
    presented = hashlib.sha256(case["presented"].encode()).hexdigest()
    accepted = _hash_accepted(credential, presented, datetime.fromisoformat(case["at"]))
    assert accepted is case["accepted"]


def test_the_example_is_one_entry_this_parser_accepts() -> None:
    example = _fixture()["example"]
    scope = _fixture()["variables"][example["variable"]]
    document = json.loads(example["value"])
    assert set(document) == {scope}
    entry = document[scope]
    previous = _previous_hash(entry)
    assert previous is not None
    credential = M2MCredential(agent_id="any", token_hash=entry["token_hash"], previous=previous)
    now = previous.until - timedelta(days=7)
    assert previous_hash_valid(credential, {entry["token_hash"]}, now)
