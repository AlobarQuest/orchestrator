"""Every reader of the `credential_rotation` grant gives one answer over one served record.

The executor and the decomposer read the raw body, the proposer and the membership check read
parsed members, and none can import the others' rule from the orchestrator. This holds the four
equal on each grant state, each member state, and case.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from membership_check.consistency import _granted as check_granted
from orchestrator.persistence.models import GRANT_STATES
from rotation_proposer import cli as proposer
from rotation_proposer.findings import Due
from rotation_worker.grants import SERVICE, rotation_granted
from sds_members import populations
from sds_members.client import MEMBER_STATES, SERVICES, read_members

DUE = Due("example", "openrouter-key", "age", "2026-10-08", "2026-10-08", True)
CREDENTIALS = proposer._Credentials("", "", "https://sds.example", "token", "")


def _record(repository: str, grant: str = "granted", state: str = "member") -> dict[str, Any]:
    row = {
        "repository": repository,
        "owner": "devon",
        "state": state,
        "working_copy": None,
        "machine_local": False,
        "apps": [],
        "holds": [],
        "grants": {
            service: {"state": grant if service == SERVICE else "not_assessed"}
            for service in SERVICES
        },
    }
    return {"members": [row]}


def _answers(
    record: dict[str, Any], repository: str, monkeypatch: pytest.MonkeyPatch
) -> dict[str, bool]:
    members = read_members(
        token="token",
        credential_key_id="key",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=record)),
    )
    monkeypatch.setattr(proposer, "read_members", lambda **_: members)
    folded = {name.lower() for name in populations.granted(members, SERVICE)}
    held = proposer._granted_repositories(CREDENTIALS)
    return {
        "rotation_granted": rotation_granted(record, repository),
        "populations.granted": repository.lower() in folded,
        "membership_check._granted": repository.lower() in check_granted(members, SERVICE),
        "proposer": proposer._ungranted(DUE, repository, lambda: held) is None,
    }


@pytest.mark.parametrize("grant", GRANT_STATES)
@pytest.mark.parametrize("state", MEMBER_STATES)
def test_every_reader_agrees_on_each_state(
    grant: str, state: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = grant == "granted" and state != "retired"
    answers = _answers(_record("A/one", grant, state), "A/one", monkeypatch)
    assert set(answers.values()) == {expected}, answers


@pytest.mark.parametrize(
    ("spelled", "asked", "expected"),
    [
        ("A/One", "a/ONE", True),
        ("A/one", "A/other", False),
        # `str.lower` and `str.casefold` part here, and the orchestrator lowers.
        ("A/straße", "A/STRASSE", False),
    ],
)
def test_every_reader_folds_case_the_same_way(
    spelled: str, asked: str, expected: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers = _answers(_record(spelled), asked, monkeypatch)
    assert set(answers.values()) == {expected}, answers
