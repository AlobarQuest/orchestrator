"""The broker token's project-reach bind (ADR-0055 amendment 34 item 5, outcome C).

A project grant's secrets are listed beside the project (measured 2026-11-27), so a row naming
projects binds when the listed projects equal its declared ones exactly and every declared secret
is listed. A row naming none (every lane's) still binds only on an exact match. Each test fails
when the check it names is removed or loosened.
"""

from __future__ import annotations

import contextlib
import io
import json
import secrets
import sys
import types
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from rotation_worker.adapters import bws_lane_token as lane
from rotation_worker.adapters import bws_machine_token as bws
from rotation_worker.contract import Rotation
from rotation_worker.git_mirror import RepoMirror
from rotation_worker.registry import REGISTRY_PATH, REGISTRY_REPOSITORIES, provider_entries
from rotation_worker.values import Value
from tests.rotation_worker.conftest import Origins

ROOT = Path(__file__).resolve().parents[2]
BROKER = "secrets-broker-bws-token"
SERVICE = "lanesvc0000000000000000"
KEEPER = f'bws-secret uuid="{uuid.uuid4()}"'
VARIABLE = (
    f'coolify-env instance="prod" resource_type="service" uuid="{SERVICE}" '
    'key="SECRETS_BROKER_BWS_ACCESS_TOKEN" redeploy=false'
)
PROJECTS = frozenset({str(uuid.uuid4()), str(uuid.uuid4())})
DECLARED = frozenset({str(uuid.uuid4()), str(uuid.uuid4())})
# The projects' own secrets, which a project grant lists and no row declares.
PROJECT_SECRETS = frozenset({str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())})
# Built at run time: a placeholder, no BWS token's shape.
TOKEN = Value(f"placeholder-{secrets.token_hex(8)}")


class Login(bws.BwsMachineTokenProvider):
    """A fresh login whose listings are a table, never a child process. `reach` answers the
    digest of the same listing, so either bind path reads one truth."""

    def __init__(self, listed: tuple[frozenset[str], frozenset[str]] | None) -> None:
        super().__init__(organization_id="org")
        self.listed = listed
        self.asked: list[str] = []

    def probe(self, value: Value) -> int | None:
        return 200 if value.same_as(TOKEN) else 400

    def known_bad(self) -> Value:
        return Value(f"known-bad-{secrets.token_hex(8)}")

    def reach(self, value: Value) -> str | None:
        self.asked.append("reach")
        return None if self.listed is None else bws.reach_digest(*self.listed)

    def reach_sets(self, value: Value) -> tuple[frozenset[str], frozenset[str]] | None:
        self.asked.append("reach_sets")
        return self.listed


def _provider(
    login: Login, projects: frozenset[str], declared: frozenset[str]
) -> lane.BwsLaneTokenProvider:
    return lane.BwsLaneTokenProvider(
        login,
        bws.reach_digest(projects, declared),
        lane.lane_variable((KEEPER, VARIABLE), KEEPER),
        lambda: pytest.fail("a bind never builds a Coolify client"),
        lambda: [],
        project_reach=(projects, declared) if projects else None,
    )


def _refusal(listed: tuple[frozenset[str], frozenset[str]] | None) -> str | None:
    return _provider(Login(listed), PROJECTS, DECLARED).bind_refusal(TOKEN, Value("old"))


def test_a_project_reach_token_seeing_its_projects_own_secrets_binds() -> None:
    assert _refusal((PROJECTS, DECLARED | PROJECT_SECRETS)) is None
    assert _refusal((PROJECTS, DECLARED)) is None


@pytest.mark.parametrize(
    "listed",
    [
        (PROJECTS | {str(uuid.uuid4())}, DECLARED | PROJECT_SECRETS),
        (PROJECTS - {min(PROJECTS)}, DECLARED | PROJECT_SECRETS),
        (PROJECTS, (DECLARED - {min(DECLARED)}) | PROJECT_SECRETS),
    ],
    ids=["one-more-project", "one-fewer-project", "a-declared-secret-missing"],
)
def test_a_project_reach_token_seeing_other_than_its_row_declares_refuses(
    listed: tuple[frozenset[str], frozenset[str]],
) -> None:
    assert _refusal(listed) == "what it can see isn't exactly the reach its registry row declares"


def test_a_failed_listing_refuses() -> None:
    reason = _refusal(None)
    assert reason is not None and "declares" in reason


def test_a_lane_token_with_one_extra_secret_still_refuses() -> None:
    """Exact match only: a row naming no project never takes the subset form."""
    login = Login((frozenset(), DECLARED | {str(uuid.uuid4())}))
    reason = _provider(login, frozenset(), DECLARED).bind_refusal(TOKEN, Value("old"))
    assert reason is not None and "declares" in reason
    assert login.asked == ["reach"]
    login.listed = (frozenset(), DECLARED)
    assert _provider(login, frozenset(), DECLARED).bind_refusal(TOKEN, Value("old")) is None


@contextlib.contextmanager
def _sdk(projects: list[str], secret_ids: list[str] | None) -> Iterator[None]:
    """A stand-in `bitwarden_sdk` for the child script; `secret_ids` `None` fails the listing."""

    def listing(ids: list[str]) -> Any:
        data = [types.SimpleNamespace(id=i) for i in ids]
        return types.SimpleNamespace(data=types.SimpleNamespace(data=data))

    def secret_list(organization: str) -> Any:
        if secret_ids is None:
            raise RuntimeError("listing failed")
        return listing(secret_ids)

    class Client:
        def auth(self) -> Any:
            return types.SimpleNamespace(login_access_token=lambda token: None)

        def projects(self) -> Any:
            return types.SimpleNamespace(list=lambda organization: listing(projects))

        def secrets(self) -> Any:
            return types.SimpleNamespace(list=secret_list)

    module = types.ModuleType(bws.CHILD_SDK)
    setattr(module, "BitwardenClient", Client)
    saved = sys.modules.get(bws.CHILD_SDK)
    sys.modules[bws.CHILD_SDK] = module
    try:
        yield
    finally:
        if saved is None:
            del sys.modules[bws.CHILD_SDK]
        else:
            sys.modules[bws.CHILD_SDK] = saved


def _run_reach_ids(
    monkeypatch: pytest.MonkeyPatch, projects: list[str], secret_ids: list[str] | None
) -> str:
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO(f"org\n{TOKEN.reveal()}"))
    with _sdk(projects, secret_ids), contextlib.redirect_stdout(out):
        exec(compile(bws._REACH_IDS, "<_REACH_IDS>", "exec"), {})
    return out.getvalue().strip()


def test_the_child_prints_sorted_ids_only_and_reach_sets_reads_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answer = _run_reach_ids(monkeypatch, ["p2", "p1"], ["s2", "s1"])
    assert json.loads(answer) == {"projects": ["p1", "p2"], "secrets": ["s1", "s2"]}
    assert TOKEN.reveal() not in answer
    assert _run_reach_ids(monkeypatch, ["p1"], None) == "unreadable"

    login = bws.BwsMachineTokenProvider(organization_id="org")
    for printed, expected in [
        (answer, (frozenset({"p1", "p2"}), frozenset({"s1", "s2"}))),
        ("unreadable", None),
        (None, None),
        ('{"projects": ["p1"]}', None),
        ('["p1"]', None),
    ]:
        monkeypatch.setattr(login, "_fresh", lambda script, stdin, printed=printed: printed)
        assert login.reach_sets(TOKEN) == expected


def test_the_real_broker_row_takes_the_project_path_and_every_lane_row_the_exact_one(
    mirror: RepoMirror, origins: Origins, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = (ROOT / REGISTRY_PATH).read_text()
    for repository in REGISTRY_REPOSITORIES:
        text = registry if repository == "AlobarQuest/orchestrator" else "version = 1\n"
        origins.commit(repository, {REGISTRY_PATH: text})
    rows = provider_entries(mirror, lane.PROVIDER)
    assert BROKER in {entry.credential["id"] for entry in rows} and len(rows) > 1

    for entry in rows:
        projects, declared = lane.declared_reach(entry.credential)
        broker = entry.credential["id"] == BROKER
        # The broker's live listing holds its projects' own secrets too, which no row declares.
        login = Login((projects, declared | PROJECT_SECRETS if broker else declared))
        monkeypatch.setattr(
            lane, "BwsMachineTokenProvider", lambda organization_id, login=login: login
        )
        provider = lane.build_provider(
            rotation=Rotation(
                entry.credential["id"], "2026-11-27-requested", "k", "w", tuple(entry.destinations)
            ),
            credential=entry.credential,
            store_read=lambda _: pytest.fail("a bind reads no store"),
            coolify_token_id="coolify-token",
            coolify_url="https://coolify.example.test",
            organization_id="org",
            siblings=lambda: [],
            pg_helper=lambda: pytest.fail("a bind asks no helper"),
            issue=False,
        )
        assert provider.bind_refusal(TOKEN, Value("old")) is None, entry.credential["id"]
        expected = "reach_sets" if broker else "reach"
        assert login.asked == [expected], entry.credential["id"]
