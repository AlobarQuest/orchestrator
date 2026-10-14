"""The credentials setting rewritten for one entry, refusing what the orchestrator's boot refuses.

`tests/api/test_m2m_identities_api.py` loads a rewrite through the orchestrator's own parser.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from rotation_worker.contract import ContractError
from rotation_worker.m2m_setting import OVERLAP, Change, build

NOW = datetime(2026, 10, 15, 7, 0, tzinfo=UTC)
ENTRY = "orchestrator-operator"


def _hash(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


OLD, NEW = _hash("old"), _hash("new")


def _served(**entry: Any) -> dict[str, Any]:
    return {
        "orchestrator-system": {"agent_id": "sds-system", "token_hash": _hash("system")},
        ENTRY: {"agent_id": "claude-code-interactive", "token_hash": OLD, **entry},
        "rotation-executor-1": {"agent_id": "rotation-executor", "token_hash": _hash("executor")},
    }


def _host(served: dict[str, Any], now: datetime = NOW) -> dict[str, Any] | None:
    text = build(served, ENTRY, Change.HOST, NEW, OLD, now).text
    return None if text is None else json.loads(text)


def test_host_sets_the_new_hash_and_keeps_the_old_as_previous() -> None:
    written = _host(_served())
    assert written is not None
    assert written[ENTRY] == {
        "agent_id": "claude-code-interactive",
        "token_hash": NEW,
        "previous": {"token_hash": OLD, "until": (NOW + OVERLAP).isoformat()},
    }
    # Every other entry, the executor's own included, is written back as served.
    assert {k: v for k, v in written.items() if k != ENTRY} == {
        k: v for k, v in _served().items() if k != ENTRY
    }


def test_host_is_a_no_op_when_the_service_already_holds_both() -> None:
    served = _served(previous={"token_hash": OLD, "until": NOW.isoformat()})
    served[ENTRY]["token_hash"] = NEW
    rewrite = build(served, ENTRY, Change.HOST, NEW, OLD, NOW)
    assert rewrite.text is None and rewrite.fingerprint is None


def test_host_refuses_an_entry_holding_neither_value() -> None:
    served = _served()
    served[ENTRY]["token_hash"] = _hash("someone else's")
    with pytest.raises(ContractError, match="neither"):
        build(served, ENTRY, Change.HOST, NEW, OLD, NOW)


def test_host_refuses_an_entry_with_the_new_hash_but_not_the_old_as_previous() -> None:
    served = _served()
    served[ENTRY]["token_hash"] = NEW
    with pytest.raises(ContractError, match="neither"):
        build(served, ENTRY, Change.HOST, NEW, OLD, NOW)


def test_an_unknown_entry_is_refused() -> None:
    with pytest.raises(ContractError, match="no entry"):
        build(_served(), "orchestrator-nobody", Change.HOST, NEW, OLD, NOW)


def test_host_refuses_an_old_hash_another_entry_still_holds() -> None:
    """The observer and drift-reporter case: boot refuses a previous equal to a current hash."""
    served = _served()
    served["orchestrator-system"]["token_hash"] = OLD
    with pytest.raises(ContractError, match="still current"):
        build(served, ENTRY, Change.HOST, NEW, OLD, NOW)


def test_host_drops_an_expired_overlap_elsewhere_and_keeps_a_live_one() -> None:
    served = _served()
    served["orchestrator-system"]["previous"] = {
        "token_hash": _hash("long gone"),
        "until": (NOW - timedelta(days=1)).isoformat(),
    }
    live = {"token_hash": _hash("recent"), "until": (NOW + timedelta(days=2)).isoformat()}
    served["rotation-executor-1"]["previous"] = live
    written = _host(served)
    assert written is not None
    assert "previous" not in written["orchestrator-system"]
    assert written["rotation-executor-1"]["previous"] == live


def test_a_served_until_too_far_ahead_is_refused_before_writing() -> None:
    served = _served()
    served["orchestrator-system"]["previous"] = {
        "token_hash": _hash("x"),
        "until": (NOW + timedelta(days=9)).isoformat(),
    }
    with pytest.raises(ContractError, match="eight days"):
        build(served, ENTRY, Change.HOST, NEW, OLD, NOW)


def test_a_served_until_not_in_utc_is_refused() -> None:
    served = _served()
    served["orchestrator-system"]["previous"] = {
        "token_hash": _hash("x"),
        "until": "2026-10-16T07:00:00+02:00",
    }
    with pytest.raises(ContractError, match="UTC"):
        build(served, ENTRY, Change.HOST, NEW, OLD, NOW)


def test_a_malformed_served_hash_is_refused() -> None:
    served = _served()
    served["orchestrator-system"]["token_hash"] = "not-a-hash"
    with pytest.raises(ContractError, match="sha256"):
        build(served, ENTRY, Change.HOST, NEW, OLD, NOW)


def test_retire_removes_the_previous_hash() -> None:
    served = _served(previous={"token_hash": OLD, "until": (NOW + OVERLAP).isoformat()})
    served[ENTRY]["token_hash"] = NEW
    text = build(served, ENTRY, Change.RETIRE, NEW, OLD, NOW).text
    assert text is not None
    assert json.loads(text)[ENTRY] == {"agent_id": "claude-code-interactive", "token_hash": NEW}


def test_retire_is_a_no_op_once_the_previous_hash_is_gone() -> None:
    served = _served()
    served[ENTRY]["token_hash"] = NEW
    assert build(served, ENTRY, Change.RETIRE, NEW, OLD, NOW).text is None


def test_retire_refuses_a_previous_hash_that_isn_t_the_old_value_s() -> None:
    served = _served(previous={"token_hash": _hash("other"), "until": NOW.isoformat()})
    served[ENTRY]["token_hash"] = NEW
    with pytest.raises(ContractError, match="isn't the old value's"):
        build(served, ENTRY, Change.RETIRE, NEW, OLD, NOW)


def test_retire_refuses_an_entry_without_the_new_hash() -> None:
    with pytest.raises(ContractError, match="doesn't hold the new hash"):
        build(_served(), ENTRY, Change.RETIRE, NEW, OLD, NOW)


def test_the_fingerprint_is_short_and_matches_the_text() -> None:
    rewrite = build(_served(), ENTRY, Change.HOST, NEW, OLD, NOW)
    assert rewrite.text is not None and rewrite.fingerprint is not None
    assert len(rewrite.fingerprint) == 8
    assert hashlib.sha256(rewrite.text.encode()).hexdigest().startswith(rewrite.fingerprint)
