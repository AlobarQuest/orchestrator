"""The client reads the member record, stages proposals, and reaches nothing else."""

import functools

import httpx
import pytest

from sds_members.client import (
    SERVICES,
    Hold,
    MembersUnreadable,
    ProposalRefused,
    read_members,
    read_members_from_environment,
    stage,
)

WIRE = {
    "members": [
        {
            "repository": "A/one",
            "owner": "devon",
            "state": "member",
            "working_copy": "/w/one",
            "machine_local": True,
            "apps": [{"name": "crm", "health_url": "https://crm.example/h", "branch": "main"}],
            "grants": {
                service: {
                    "state": "granted",
                    "seeded": False,
                    "set_by": "x",
                    "set_at": None,
                    "reason": "r",
                }
                for service in SERVICES
            },
            "holds": [
                {
                    "ecosystem": "uv",
                    "directory": "/",
                    "dependency": "sqlalchemy",
                    "ceiling": "2.0.54",
                    "reason": "r",
                    "set_by": "devon",
                    "set_at": None,
                }
            ],
        }
    ]
}
_read = functools.partial(read_members, token="t", credential_key_id="orchestrator-observer")


def _transport(handler):
    return httpx.MockTransport(handler)


def test_reads_members():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["agent"] = request.headers["user-agent"]
        seen["key"] = request.headers["x-credential-key-id"]
        return httpx.Response(200, json=WIRE)

    [member] = _read(transport=_transport(handler))
    assert seen == {
        "path": "/api/v1/members",
        "agent": "sds-members/1 (+AlobarQuest/orchestrator)",
        "key": "orchestrator-observer",
    }
    assert member.repository == "A/one" and member.machine_local is True
    assert member.apps[0].name == "crm"
    assert member.grants["rollouts"] == "granted" and set(member.grants) == set(SERVICES)
    assert member.holds == (Hold("uv", "/", "sqlalchemy", "2.0.54"),)


def test_a_member_without_holds_is_unreadable():
    """Fail closed: a server that dropped the key must not read as "nothing is held"."""
    [wire] = WIRE["members"]
    member = {key: value for key, value in wire.items() if key != "holds"}
    with pytest.raises(MembersUnreadable):
        _read(transport=_transport(lambda r: httpx.Response(200, json={"members": [member]})))


@pytest.mark.parametrize(
    "holds", [None, "x", [{"ecosystem": "uv"}], [{**WIRE["members"][0]["holds"][0], "ceiling": 3}]]
)
def test_malformed_holds_are_unreadable(holds):
    member = {**WIRE["members"][0], "holds": holds}
    with pytest.raises(MembersUnreadable):
        _read(transport=_transport(lambda r: httpx.Response(200, json={"members": [member]})))


@pytest.mark.parametrize("status", [302, 401, 500])
def test_non_2xx_is_unreadable(status):
    with pytest.raises(MembersUnreadable, match=str(status)):
        _read(transport=_transport(lambda r: httpx.Response(status)))


@pytest.mark.parametrize(
    "body",
    [
        httpx.Response(200, text="<html>"),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={"members": "x"}),
        httpx.Response(200, json={"members": [{"repository": "A/one"}]}),
    ],
)
def test_malformed_body_is_unreadable(body):
    with pytest.raises(MembersUnreadable):
        _read(transport=_transport(lambda r: body))


def test_transport_error_is_unreadable():
    def handler(request):
        raise httpx.ConnectError("boom")

    with pytest.raises(MembersUnreadable, match="ConnectError"):
        _read(transport=_transport(handler))


def test_unusable_base_url_is_unreadable():
    # A doubled dot builds a client and fails IDNA encoding at request time, before any socket.
    with pytest.raises(MembersUnreadable, match="UnicodeEncodeError"):
        _read(base_url="https://a..b")


def test_refuses_any_other_route():
    from sds_members import client

    read, stage_route = client._READ, client._STAGE
    assert client._permits(read)("GET", "/api/v1/members")
    assert client._permits(stage_route)("POST", "/api/v1/member-proposals")
    # Reading cannot stage, and staging cannot read.
    assert not client._permits(read)(*stage_route)
    assert not client._permits(stage_route)(*read)
    for method, path in [
        ("POST", "/api/v1/members"),
        ("GET", "/api/v1/member-proposals"),
        ("DELETE", "/api/v1/members"),
        ("GET", "/api/v1/work-units"),
    ]:
        assert not client._permits(read)(method, path)
        assert not client._permits(stage_route)(method, path)
        assert isinstance(client._refuse(method, path), MembersUnreadable)


def test_stage_posts_body_with_version_and_key_in_the_body():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen["method"], seen["path"] = request.method, request.url.path
        seen["json"] = json.loads(request.content)
        return httpx.Response(201, json={"id": "abc", "state": "staged"})

    result = stage(
        token="t",
        credential_key_id="orchestrator-system",
        body={"summary": "s", "changes": []},
        idempotency_key="k",
        transport=_transport(handler),
    )
    assert result["id"] == "abc"
    assert seen["method"] == "POST" and seen["path"] == "/api/v1/member-proposals"
    assert seen["json"] == {
        "summary": "s",
        "changes": [],
        "expected_version": 0,
        "idempotency_key": "k",
    }


def test_stage_refusal_carries_status_and_message():
    def handler(request):
        return httpx.Response(409, json={"error": {"code": "x", "message": "moved"}})

    with pytest.raises(ProposalRefused, match="409.*moved"):
        stage(
            token="t",
            credential_key_id="k",
            body={},
            idempotency_key="k",
            transport=_transport(handler),
        )


def test_service_copy_equals_the_orchestrators():
    from orchestrator.persistence.models import MEMBER_SERVICES, MEMBER_STATES
    from sds_members import client

    assert tuple(MEMBER_SERVICES) == SERVICES
    assert tuple(MEMBER_STATES) == client.MEMBER_STATES


def test_read_members_from_environment_refuses_without_a_token(monkeypatch):
    monkeypatch.delenv("SDS_MEMBERS_OBSERVER_TOKEN", raising=False)
    with pytest.raises(MembersUnreadable, match="SDS_MEMBERS_OBSERVER_TOKEN"):
        read_members_from_environment(transport=_transport(lambda request: httpx.Response(500)))
    monkeypatch.setenv("SDS_MEMBERS_OBSERVER_TOKEN", "")
    with pytest.raises(MembersUnreadable):
        read_members_from_environment()


def test_read_members_from_environment_uses_the_observer_key_id(monkeypatch):
    monkeypatch.setenv("SDS_MEMBERS_OBSERVER_TOKEN", "bearer-value")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers["x-credential-key-id"]
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, json=WIRE)

    [member] = read_members_from_environment(transport=_transport(handler))
    assert seen == {"key": "orchestrator-observer", "auth": "Bearer bearer-value"}
    assert member.repository == "A/one"
