"""A bearer's previous hash during a rotation's overlap (ADR-0055 decision 7).

Boot accepts an optional `previous` per identity and refuses an `until` more than eight days
ahead; authentication accepts the previous hash only before `until`, and the current hash always.
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from orchestrator.identity.auth import (
    MAX_PREVIOUS_OVERLAP,
    AuthenticationError,
    M2MCredential,
    PreviousHash,
    authenticate_m2m,
)
from orchestrator.main import load_auth_config
from tests.identity.test_boot_auth_config import AUTH_VARIABLES, MESSAGE, VALID
from tests.identity.test_m2m_auth import registry

CURRENT = hashlib.sha256(b"current-token").hexdigest()
OLD = hashlib.sha256(b"old-token").hexdigest()
UNTIL = datetime(2026, 10, 17, 4, 0, tzinfo=UTC)


def _boot(monkeypatch: pytest.MonkeyPatch, entry: dict[str, Any]) -> M2MCredential:
    for name in AUTH_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    environment = {**VALID, "ORCHESTRATOR_M2M_CREDENTIALS": json.dumps({"worker-key": entry})}
    for name, value in environment.items():
        if value is not None:
            monkeypatch.setenv(name, value)
    config = load_auth_config()
    assert config is not None
    return config.m2m_credentials["worker-key"]


def _entry(**previous: object) -> dict[str, Any]:
    return {"agent_id": "worker", "token_hash": CURRENT, "previous": previous}


def _in(delta: timedelta) -> str:
    return (datetime.now(UTC) + delta).isoformat()


def test_boot_without_previous_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    credential = _boot(monkeypatch, {"agent_id": "worker", "token_hash": CURRENT})
    assert credential == M2MCredential(agent_id="worker", token_hash=CURRENT)


@pytest.mark.parametrize("suffix", ["+00:00", "Z"])
def test_boot_accepts_a_previous_hash_within_the_cap(
    monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    until = (datetime.now(UTC) + timedelta(days=7)).replace(microsecond=0)
    text = until.isoformat().replace("+00:00", suffix)
    credential = _boot(monkeypatch, _entry(token_hash=OLD, until=text))
    assert credential.previous == PreviousHash(token_hash=OLD, until=until)


def test_boot_accepts_an_until_already_past(monkeypatch: pytest.MonkeyPatch) -> None:
    """A `previous` left behind after its backstop authenticates nothing; it must not brick boot."""
    credential = _boot(monkeypatch, _entry(token_hash=OLD, until=_in(-timedelta(days=30))))
    assert credential.previous is not None


@pytest.mark.parametrize(
    "previous",
    [
        pytest.param({"token_hash": OLD, "until": "later"}, id="until_not_an_instant"),
        pytest.param({"token_hash": OLD, "until": "2026-10-17T04:00:00"}, id="until_naive"),
        pytest.param({"token_hash": OLD, "until": "2026-10-17T04:00:00+02:00"}, id="until_not_utc"),
        pytest.param({"token_hash": OLD, "until": 1760673600}, id="until_not_a_string"),
        pytest.param({"token_hash": OLD}, id="until_missing"),
        pytest.param({"until": "2026-10-17T04:00:00Z"}, id="hash_missing"),
        pytest.param({"token_hash": "A" * 64, "until": "2026-10-17T04:00:00Z"}, id="hash_shape"),
        pytest.param(
            {"token_hash": CURRENT, "until": "2026-10-17T04:00:00Z"}, id="hash_is_current"
        ),
        pytest.param(
            {"token_hash": OLD, "until": "2026-10-17T04:00:00Z", "note": "x"}, id="extra_key"
        ),
        pytest.param("not-an-object", id="not_an_object"),
    ],
)
def test_boot_refuses_a_malformed_previous(
    monkeypatch: pytest.MonkeyPatch, previous: object
) -> None:
    entry = {"agent_id": "worker", "token_hash": CURRENT, "previous": previous}
    with pytest.raises(RuntimeError, match=MESSAGE):
        _boot(monkeypatch, entry)


def test_boot_refuses_an_until_more_than_eight_days_ahead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(RuntimeError, match=MESSAGE):
        _boot(monkeypatch, _entry(token_hash=OLD, until=_in(timedelta(days=8, minutes=5))))


def test_boot_accepts_an_until_just_inside_eight_days(monkeypatch: pytest.MonkeyPatch) -> None:
    credential = _boot(
        monkeypatch, _entry(token_hash=OLD, until=_in(timedelta(days=8, minutes=-5)))
    )
    assert credential.previous is not None


def test_the_cap_is_eight_days() -> None:
    assert MAX_PREVIOUS_OVERLAP == timedelta(days=8)


def _authenticate(token: bytes, now: datetime, previous: PreviousHash | None) -> str:
    credential = M2MCredential(agent_id="worker", token_hash=CURRENT, previous=previous)
    identity = authenticate_m2m(
        bearer_token=token.decode(),
        credential_key_id="worker-key",
        credentials={"worker-key": credential},
        registry=registry(),
        now=now,
    )
    return identity.actor_id


OVERLAP = PreviousHash(token_hash=OLD, until=UNTIL)


def test_the_previous_hash_authenticates_before_until() -> None:
    assert _authenticate(b"old-token", UNTIL - timedelta(seconds=1), OVERLAP) == "worker"


@pytest.mark.parametrize("after", [timedelta(0), timedelta(seconds=1)], ids=["at", "after"])
def test_the_previous_hash_is_refused_from_until_on(after: timedelta) -> None:
    with pytest.raises(AuthenticationError):
        _authenticate(b"old-token", UNTIL + after, OVERLAP)


@pytest.mark.parametrize("offset", [-timedelta(days=1), timedelta(days=1)], ids=["before", "after"])
def test_the_current_hash_authenticates_either_side_of_until(offset: timedelta) -> None:
    assert _authenticate(b"current-token", UNTIL + offset, OVERLAP) == "worker"


def test_an_unrelated_token_is_refused_during_the_overlap() -> None:
    with pytest.raises(AuthenticationError):
        _authenticate(b"other-token", UNTIL - timedelta(days=1), OVERLAP)


def test_without_previous_the_old_token_is_refused() -> None:
    with pytest.raises(AuthenticationError):
        _authenticate(b"old-token", UNTIL - timedelta(days=1), None)


@pytest.mark.parametrize(
    "previous",
    [
        pytest.param(PreviousHash(token_hash="x", until=UNTIL), id="hash_shape"),
        pytest.param(PreviousHash(token_hash=CURRENT, until=UNTIL), id="hash_is_current"),
        pytest.param(PreviousHash(token_hash=OLD, until=UNTIL.replace(tzinfo=None)), id="naive"),
        pytest.param(
            PreviousHash(token_hash=OLD, until=cast(datetime, "2026-10-17")),
            id="until_not_a_datetime",
        ),
        pytest.param(
            PreviousHash(token_hash=OLD, until=UNTIL + timedelta(days=30)), id="until_past_cap"
        ),
    ],
)
def test_a_malformed_previous_fails_closed_at_request_time(previous: PreviousHash) -> None:
    with pytest.raises(AuthenticationError, match="configuration"):
        _authenticate(b"current-token", UNTIL - timedelta(days=1), previous)


def test_a_previous_hash_may_not_be_another_identitys_current_hash() -> None:
    """Otherwise the other identity's bearer would authenticate as this one."""
    other = hashlib.sha256(b"other-token").hexdigest()
    credentials = {
        "worker-key": M2MCredential(
            agent_id="worker", token_hash=CURRENT, previous=PreviousHash(other, UNTIL)
        ),
        "other-key": M2MCredential(agent_id="other", token_hash=other),
    }
    with pytest.raises(AuthenticationError, match="configuration"):
        authenticate_m2m(
            bearer_token="other-token",
            credential_key_id="worker-key",
            credentials=credentials,
            registry=registry(),
            now=UNTIL - timedelta(days=1),
        )


def test_boot_refuses_a_previous_hash_equal_to_another_identitys_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both entries name `worker`, the fixture bundle's one machine actor: boot resolves each
    entry's identity but leaves one-to-one to request time, so only the hash rule can refuse."""
    for name in AUTH_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    other = hashlib.sha256(b"other-token").hexdigest()
    document = {
        "worker-key": _entry(token_hash=other, until=_in(timedelta(days=7))),
        "second-key": {"agent_id": "worker", "token_hash": other},
    }
    for name, value in {**VALID, "ORCHESTRATOR_M2M_CREDENTIALS": json.dumps(document)}.items():
        if value is not None:
            monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match=MESSAGE):
        load_auth_config()


def test_two_identities_sharing_a_value_may_share_a_previous_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The observer and drift-reporter bearers share one value and rotate together (decision 6)."""
    for name in AUTH_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    until = _in(timedelta(days=7))
    document = {
        "worker-key": _entry(token_hash=OLD, until=until),
        "second-key": {
            "agent_id": "worker",
            "token_hash": CURRENT,
            "previous": {"token_hash": OLD, "until": until},
        },
    }
    for name, value in {**VALID, "ORCHESTRATOR_M2M_CREDENTIALS": json.dumps(document)}.items():
        if value is not None:
            monkeypatch.setenv(name, value)
    config = load_auth_config()
    assert config is not None and len(config.m2m_credentials) == 2
