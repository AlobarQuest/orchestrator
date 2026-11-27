"""The GitHub App private-key adapter, driven through the contract against a simulated GitHub.

ADR-0055 amendment 35 and D-84. A person generates a key pair and pastes it (a human act), so each
test stages the new key by hand, then runs the machine units. The simulated GitHub verifies each
JWT against the public halves of the keys it issued, as GitHub does; GitHub documents no status
for a key it never issued or one deleted, so it answers both 401 unless a test names another
status (D-84 item 14 A: the first real rotation measures them). Every key is generated at run
time, and none is committed.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import tomllib
from dataclasses import dataclass, field, replace
from functools import cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
)

from rotation_worker import worker
from rotation_worker.adapters import ADAPTERS, github_app_key
from rotation_worker.contract import ContractError, Provider, Rotation, ScopeChecked, UnitRun
from rotation_worker.inbox import ConfirmsIdentity, NamesItsCredential, bind
from rotation_worker.plans import derive
from rotation_worker.types import CLASS_TYPES, TYPES
from rotation_worker.values import Value
from tests.rotation_worker.stores import MemoryStore
from tests.rotation_worker.test_inbox import Provides

WORK = "work-project"
APP = 5684758
OTHER_APP = 5684778
INSTALLATION = 180257215
PROOF = "AlobarQuest/intent-packages"
KEEPER_ID = "314a966c-5654-4e3c-8358-b4fd01268775"


@cache
def _pem(slot: int) -> str:
    """A fresh RSA key per slot, generated once per run: slot 0 is the control's."""
    del slot
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode("ascii")


def _stored(pem: str) -> Value:
    """The stored form: base64 of the PEM, on one line as macOS `base64` writes it."""
    return Value(base64.b64encode(pem.encode()).decode("ascii"))


def _public(pem: str) -> bytes:
    key = load_pem_private_key(pem.encode(), password=None)
    return key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)


def _public_pem(pem: str) -> str:
    key = load_pem_private_key(pem.encode(), password=None)
    return key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode()


def _row(**changes: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": "lanes-read-app-private-key",
        "class": "github-app-private-key",
        "provider": "github",
        "app_id": APP,
        "installation_id": INSTALLATION,
        "proof_repository": PROOF,
        "consumer": [
            {"kind": "bws-secret", "uuid": KEEPER_ID},
            {
                "kind": "runtime-fetch",
                "repo": "orchestrator",
                "ref": "scripts/run-landing-ledger.sh",
                "healthchecks_check": "sds-landing-ledger",
            },
        ],
    }
    row.update(changes)
    return row


@dataclass
class GitHub:
    """GitHub's App endpoints the adapter and the mint reach, verifying each JWT by public key."""

    # App id -> the public halves of its key pairs that GitHub holds.
    keys: dict[int, list[str]] = field(default_factory=dict)
    installations: dict[str, int] = field(default_factory=lambda: {PROOF.casefold(): INSTALLATION})
    tokens: dict[str, list[str]] = field(default_factory=dict)
    revoked: list[str] = field(default_factory=list)
    requests: list[httpx.Request] = field(default_factory=list)
    # How GitHub answers a JWT no key it holds verifies: undocumented, measured at the first real
    # rotation (amendment 35 item 14 A).
    refused: int = 401
    down: bool = False
    reach: list[str] | None = None
    revoke_status: int = 204
    answered_app: int | None = None

    def issue(self, app: int = APP) -> str:
        pem = _pem(len([k for keys in self.keys.values() for k in keys]) + 1)
        self.keys.setdefault(app, []).append(_public_pem(pem))
        return pem

    def delete(self, pem: str, app: int = APP) -> None:
        self.keys[app].remove(_public_pem(pem))

    def _app_of(self, assertion: str) -> int | None:
        claims = jwt.decode(assertion, options={"verify_signature": False})
        app = int(claims["iss"])
        for public in self.keys.get(app, []):
            try:
                jwt.decode(assertion, public, algorithms=["RS256"])
            except jwt.PyJWTError:
                continue
            return app
        return None

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("no route", request=request)
        bearer = request.headers["Authorization"].removeprefix("Bearer ")
        path, method = request.url.path, request.method
        if path in (github_app_key.REPOSITORIES_PATH, github_app_key.TOKEN_PATH):
            repositories = self.tokens.get(bearer)
            if repositories is None:
                return httpx.Response(401, json={"message": "Bad credentials"})
            if method == "DELETE":
                if self.revoke_status == 204:
                    del self.tokens[bearer]
                    self.revoked.append(bearer)
                return httpx.Response(self.revoke_status)
            listed = self.reach if self.reach is not None else repositories
            return httpx.Response(
                200,
                json={
                    "total_count": len(listed),
                    "repositories": [{"full_name": name} for name in listed],
                },
            )
        app = self._app_of(bearer)
        if app is None:
            return httpx.Response(self.refused, json={"message": "A JSON web token could not be"})
        if path == "/app":
            return httpx.Response(200, json={"id": self.answered_app or app, "slug": "lanes"})
        if path.endswith("/installation"):
            repository = path.removeprefix("/repos/").removesuffix("/installation")
            return httpx.Response(200, json={"id": self.installations[repository.casefold()]})
        body = json.loads(request.content)
        token = f"ghs_{secrets.token_urlsafe(27)}"
        self.tokens[token] = [PROOF]
        return httpx.Response(
            201,
            json={
                "token": token,
                "permissions": body["permissions"],
                "repositories": [{"full_name": PROOF}],
            },
        )


class Rotating:
    """One rotation of the read App's key, with the old key in its keeper."""

    def __init__(self, github: GitHub, old: str, row: dict[str, Any] | None = None) -> None:
        self.github = github
        self.store = MemoryStore()
        self.keeper = self.store.put(WORK, "lanes-read-app-private-key", _stored(old))
        self.rotation = Rotation(
            "lanes-read-app-private-key",
            "2026-11-27-requested",
            self.keeper,
            WORK,
            (f'bws-secret uuid="{self.keeper}"', 'provider name="github"'),
        )
        self.provider = github_app_key.build_provider(
            rotation=self.rotation,
            credential=row or _row(),
            store=self.store,
            transport=httpx.MockTransport(github.handle),
            control_key=lambda: _pem(0),
        )
        self.filed: list[tuple[Any, Any]] = []

    def stage(self, new: Value) -> None:
        self.store.put(WORK, self.rotation.staging_key, new)

    def run(self, unit: str) -> None:
        UnitRun(
            rotation=self.rotation,
            store=self.store,
            provider=self.provider,
            attestations=(),
            record=lambda step, evidence: self.filed.append((step, evidence)),
            before_step=lambda: None,
            filed=frozenset(step.kind for step, _ in self.filed),
        ).run(github_app_key.PLAN.unit(unit))

    def evidence(self, kind: str) -> list[Any]:
        return [evidence for step, evidence in self.filed if step.kind == kind]


def _rotating(github: GitHub | None = None) -> tuple[Rotating, str, str]:
    github = github or GitHub()
    old, new = github.issue(), github.issue()
    rotating = Rotating(github, old)
    rotating.stage(_stored(new))
    return rotating, old, new


# ---- the type -------------------------------------------------------------------------------


def test_the_type_is_bitbuckets_shape_a_person_mints_and_deletes() -> None:
    assert CLASS_TYPES["github-app-private-key"] == github_app_key.NAME
    assert TYPES[github_app_key.NAME].capabilities == TYPES["bitbucket"].capabilities
    assert github_app_key.PLAN == derive(TYPES["bitbucket"].capabilities, (), "", "bws-secret")
    assert [segment.name for segment in github_app_key.PLAN.segments] == [
        "mint",
        "store",
        "verify",
        "revoke",
        "confirm",
    ]
    adapter = ADAPTERS[github_app_key.NAME]
    assert adapter.provider_name == "github"
    assert adapter.revoke_may_come_first
    assert adapter.check_row is github_app_key.check_row
    provider = _rotating()[0].provider
    assert isinstance(provider, ScopeChecked)
    assert isinstance(provider, ConfirmsIdentity)
    assert isinstance(provider, NamesItsCredential)


# ---- a rotation -----------------------------------------------------------------------------


def test_a_rotation_proves_stores_and_waits_for_the_person_s_deletion() -> None:
    rotating, old, new = _rotating()
    rotating.run("store")
    [verified] = rotating.evidence("verify_new")
    assert verified.note == (
        f"working proof: installation {INSTALLATION} on {PROOF} minted a metadata-read token "
        "reaching that repository alone, revoked with 204"
    )
    # D-84 item 14 A: the never-issued control's status is recorded before anything is stored.
    control, answer = verified.probes
    assert (control.subject, control.http_status, control.outcome) == ("known_bad", 401, "dead")
    assert (answer.http_status, answer.outcome) == (200, "live")
    assert rotating.github.tokens == {}
    assert len(rotating.github.revoked) == 1
    rotating.run("verify")
    # Before the person deletes the old key pair, confirm-dead finds it alive and stops.
    with pytest.raises(ContractError, match="probed live"):
        rotating.run("confirm")
    rotating.github.delete(old)
    rotating.run("confirm")
    # The old key's status is recorded at confirm.
    [*_, confirmed] = rotating.evidence("confirm_dead")
    assert [(p.subject, p.http_status) for p in confirmed.probes] == [
        ("known_bad", 401),
        ("old", 401),
        ("known_bad", 401),
        ("new", 200),
    ]
    assert rotating.store.secrets.keys() == {rotating.keeper}
    assert rotating.store.read(rotating.keeper).same_as(_stored(new))


@pytest.mark.parametrize("status", [403, 404, 422])
def test_a_control_github_doesn_t_refuse_with_401_fails_verify_new_by_its_status(
    status: int,
) -> None:
    github = GitHub(refused=status)
    rotating, old, _ = _rotating(github)
    with pytest.raises(ContractError, match=f"didn't refuse a known-bad value \\({status}\\)"):
        rotating.run("store")
    assert rotating.store.read(rotating.keeper).same_as(_stored(old))
    assert github.tokens == {} and github.revoked == []


@pytest.mark.parametrize("status", [403, 404])
def test_a_deleted_key_github_doesn_t_refuse_with_401_fails_confirm_by_its_status(
    status: int,
) -> None:
    rotating, old, _ = _rotating()
    rotating.run("store")
    rotating.run("verify")
    rotating.github.delete(old)
    rotating.github.refused = status
    with pytest.raises(ContractError, match=f"\\({status}\\)"):
        rotating.run("confirm")
    # Nothing is deleted by machine: the quarantine still holds the old key.
    assert rotating.store.find(WORK, rotating.rotation.quarantine_key) is not None


def test_a_key_for_another_app_is_refused_by_name_before_anything_is_stored() -> None:
    github = GitHub()
    old, other = github.issue(), github.issue(OTHER_APP)
    rotating = Rotating(github, old)
    rotating.stage(_stored(other))
    # GitHub checks the JWT against the row's App's keys, which this key isn't among.
    with pytest.raises(ContractError, match="the new value probed dead \\(401\\)"):
        rotating.run("store")
    github.answered_app = OTHER_APP
    with pytest.raises(ContractError, match=f"for App {OTHER_APP}, not the row's App {APP}"):
        rotating.provider.probe(_stored(old))
    assert rotating.store.read(rotating.keeper).same_as(_stored(old))


def test_the_old_key_re_encoded_is_refused_as_the_same_key_pair() -> None:
    github = GitHub()
    old = github.issue()
    rotating = Rotating(github, old)
    # The same key, wrapped as `openssl base64` writes it: other bytes, the same key pair.
    wrapped = base64.encodebytes(old.encode()).decode("ascii").strip()
    rotating.stage(Value(wrapped))
    with pytest.raises(ContractError, match="the new key is the old key pair"):
        rotating.run("store")
    assert not rotating.provider.same_identity(Value(wrapped), _stored(old))
    assert github.tokens == {} and github.revoked == []


def test_an_installation_not_the_row_s_is_refused_before_anything_is_minted() -> None:
    github = GitHub(installations={PROOF.casefold(): INSTALLATION + 1})
    rotating, old, _ = _rotating(github)
    with pytest.raises(ContractError, match=f"is {INSTALLATION + 1}, not the row's {INSTALLATION}"):
        rotating.run("store")
    assert not any(r.url.path.endswith("/access_tokens") for r in github.requests)
    assert rotating.store.read(rotating.keeper).same_as(_stored(old))


def test_a_proof_token_reaching_more_is_refused_and_still_revoked() -> None:
    github = GitHub(reach=[PROOF, "AlobarQuest/orchestrator"])
    rotating, _, _ = _rotating(github)
    with pytest.raises(ContractError, match=f"doesn't reach {PROOF} alone"):
        rotating.run("store")
    assert github.tokens == {} and len(github.revoked) == 1


def test_a_proof_token_reaching_one_other_repository_is_refused() -> None:
    github = GitHub(reach=["AlobarQuest/orchestrator"])
    rotating, _, _ = _rotating(github)
    with pytest.raises(ContractError, match=f"doesn't reach {PROOF} alone"):
        rotating.run("store")
    assert github.tokens == {} and len(github.revoked) == 1


@pytest.mark.parametrize("status", [404, 500])
def test_a_proof_token_not_revoked_fails_the_step_by_its_status(status: int) -> None:
    github = GitHub(revoke_status=status)
    rotating, _, _ = _rotating(github)
    with pytest.raises(ContractError, match=f"answered HTTP {status}: the token may live"):
        rotating.run("store")


def test_no_answer_is_indeterminate_and_names_only_the_failure_s_type() -> None:
    rotating, _, new = _rotating()
    assert rotating.provider.probe(_stored(new)) == 200
    rotating.github.down = True
    assert rotating.provider.probe(_stored(new)) is None
    with pytest.raises(ContractError, match="installation got no answer \\(ConnectError\\)$"):
        rotating.provider.check_scope(_stored(new))


def test_a_value_that_isn_t_the_stored_form_is_refused_without_its_text() -> None:
    rotating, _, new = _rotating()
    for text in (new.strip(), "not-base64!", base64.b64encode(b"plain").decode()):
        with pytest.raises(ContractError, match="isn't the stored form") as refused:
            rotating.provider.probe(Value(text))
        assert text not in str(refused.value)


def test_no_evidence_refusal_or_log_line_carries_a_key_a_jwt_or_a_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    rotating, old, new = _rotating()
    rotating.run("store")
    rotating.github.revoke_status = 500
    refusals = []
    for unit in ("store", "verify", "confirm"):
        try:
            rotating.run(unit)
        except ContractError as error:
            refusals.append(str(error))
    sent = [r.headers["Authorization"].removeprefix("Bearer ") for r in rotating.github.requests]
    forms = {
        old,
        new,
        _stored(old).reveal(),
        _stored(new).reveal(),
        *sent,
        *rotating.github.revoked,
    }
    text = " ".join([*(repr(evidence) for _, evidence in rotating.filed), *refusals, caplog.text])
    for form in forms:
        for line in form.splitlines():
            if len(line) > 12:
                assert line not in text


# ---- identity, the inbox and the revoke alert -----------------------------------------------


def test_identity_is_the_row_s_app_and_a_different_key_pair() -> None:
    rotating, old, new = _rotating()
    assert rotating.provider.same_identity(_stored(new), _stored(old))
    assert rotating.provider.declared == str(APP)
    assert rotating.provider.identity_of(_stored(new)) == str(APP)
    # A value that isn't an App key, or one GitHub doesn't accept, names nothing.
    assert rotating.provider.identity_of(Value(f"1|{secrets.token_urlsafe(30)}")) is None
    assert rotating.provider.identity_of(_stored(_pem(0))) is None
    assert rotating.provider.declared_by(_row()) == str(APP)
    assert rotating.provider.declared_by({"class": "coolify-api-token", "app_id": APP}) is None
    with pytest.raises(ContractError, match="declares its app_id"):
        rotating.provider.declared_by(_row(app_id="5684758"))


def _unstage(rotating: Rotating) -> None:
    """Take back the value the helper staged, so the inbox's bind stages it."""
    staged = rotating.store.find(WORK, rotating.rotation.staging_key)
    assert staged is not None
    rotating.store.delete(staged.id)


def test_the_inbox_binds_a_key_naming_its_own_rotation_while_another_app_s_waits() -> None:
    rotating, _, new = _rotating()
    store, rotation = rotating.store, rotating.rotation
    _unstage(rotating)
    reason = bind(
        _stored(new),
        rotation=rotation,
        store=store,
        provider=cast(Provider, rotating.provider),
        others_waiting=["push-key-store"],
        other_keepers=list,
        others_declare=lambda: {"push-key-store": str(OTHER_APP)},
    )
    assert reason is None
    staged = store.find(WORK, rotation.staging_key)
    assert staged is not None and store.read(staged.id).same_as(_stored(new))


def test_the_inbox_refuses_a_key_when_two_waiting_rotations_declare_its_app() -> None:
    rotating, _, new = _rotating()
    store, rotation = rotating.store, rotating.rotation
    _unstage(rotating)
    reason = bind(
        _stored(new),
        rotation=rotation,
        store=store,
        provider=cast(Provider, rotating.provider),
        others_waiting=["twin-store"],
        other_keepers=list,
        others_declare=lambda: {"twin-store": str(APP)},
    )
    assert reason == (
        f"another waiting rotation declares the same identity ({APP}), so the inbox can't tell "
        "which it is for: twin-store"
    )
    assert store.find(WORK, rotation.staging_key) is None


def test_the_revoke_alert_names_the_key_pair_to_keep_by_github_s_fingerprint() -> None:
    rotating, _, new = _rotating()
    guide = rotating.provider.revoke_guide(_stored(new))
    # GitHub's: `openssl rsa -pubout -outform DER | openssl sha256 -binary | openssl base64`.
    fingerprint = base64.b64encode(hashlib.sha256(_public(new)).digest()).decode("ascii")
    assert f"begins {fingerprint[:8]}." in guide
    assert fingerprint[:9] not in guide
    assert "a pair made by hand and never pasted" in guide


def test_mint_host_consumer_loaded_and_revoke_are_a_person_s() -> None:
    provider = _rotating()[0].provider
    value = _stored(_pem(1))
    for call in (
        provider.mint,
        lambda: provider.host(value, value),
        lambda: provider.consumer_loaded({}, value),
        lambda: provider.revoke(value),
    ):
        with pytest.raises(ContractError):
            call()


def test_the_client_permits_only_the_four_requests() -> None:
    rotating, _, _ = _rotating()
    rotating.run("store")
    sent = {(r.method, r.url.path) for r in rotating.github.requests}
    assert sent == {
        ("GET", "/app"),
        ("GET", f"/repos/{PROOF}/installation"),
        (
            "POST",
            f"/app/installations/{INSTALLATION}/access_tokens",
        ),
        ("GET", "/installation/repositories"),
        ("DELETE", "/installation/token"),
    }
    for method, path in (("GET", "/user"), ("DELETE", "/app"), ("GET", "/repos/o/r/installation")):
        with pytest.raises(ContractError, match=f"may not {method} {path}"):
            rotating.provider._send(method, path, "x")


def test_the_proof_mint_asks_for_the_proof_repository_and_metadata_alone() -> None:
    rotating, _, _ = _rotating()
    rotating.run("store")
    [asked] = [r for r in rotating.github.requests if r.method == "POST"]
    assert json.loads(asked.content) == {
        "repositories": [PROOF.split("/")[1]],
        "permissions": {"metadata": "read"},
    }


# ---- the registry row -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "refusal"),
    [
        ({"app_id": None}, "declares its app_id"),
        ({"app_id": "5684758"}, "declares its app_id"),
        ({"app_id": True}, "declares its app_id"),
        ({"installation_id": 0}, "declares its installation_id"),
        ({"proof_repository": "intent-packages"}, "one owner/name repository"),
        ({"proof_repository": "AlobarQuest/.."}, "one owner/name repository"),
        ({"consumer": []}, "exactly one bws-secret keeper"),
        (
            {"consumer": [{"kind": "bws-secret", "uuid": KEEPER_ID}]},
            "at least one runtime-fetch reader",
        ),
        (
            {
                "consumer": [
                    {"kind": "bws-secret", "uuid": KEEPER_ID},
                    {"kind": "gh-actions-secret", "repo": "o/r", "name": "KEY"},
                    {"kind": "runtime-fetch", "ref": "r", "healthchecks_check": "c"},
                ]
            },
            "may not list a gh-actions-secret consumer",
        ),
        (
            {
                "consumer": [
                    {"kind": "bws-secret", "uuid": KEEPER_ID},
                    {"kind": "runtime-fetch", "ref": "r"},
                ]
            },
            "names its check",
        ),
    ],
)
def test_check_row_refuses_a_row_the_adapter_couldn_t_act_on(
    changes: dict[str, Any], refusal: str
) -> None:
    with pytest.raises(ContractError, match=refusal):
        github_app_key.check_row(_row(**changes))


def test_both_registry_rows_pass_check_row_and_stay_out_of_the_sds_s_hands() -> None:
    registry = tomllib.loads(
        (Path(__file__).resolve().parents[2] / ".cred-consumers.toml").read_text()
    )
    rows = {
        row["id"]: row
        for row in registry["credential"]
        if row.get("class") == "github-app-private-key"
    }
    assert set(rows) == {"lanes-read-app-private-key", "lanes-push-app-private-key"}
    declared = {}
    for credential_id, row in rows.items():
        github_app_key.check_row(row)
        # D-84: exit checks 2 and 3 and the keepers' move come first.
        assert row["rotated_by_sds"] is False
        assert "rotate_requested" not in row
        # The ids are the ones each row's `provider_identity` states.
        identity = row["provider_identity"]
        assert f"id {row['app_id']}, installation {row['installation_id']}" in identity
        declared[credential_id] = github_app_key.declared_app(row)
    assert declared == {
        "lanes-read-app-private-key": "5684758",
        "lanes-push-app-private-key": "5684778",
    }


def test_the_worker_s_revoke_alert_names_the_key_pair_or_says_it_can_t() -> None:
    rotating, _, new = _rotating()
    rotating.run("store")
    setup = cast(worker.Setup, SimpleNamespace(store=rotating.store))
    provider = cast(Provider, rotating.provider)
    guide = worker._revoke_guide(setup, provider, rotating.rotation, "u1")
    assert guide == rotating.provider.revoke_guide(_stored(new))
    unreadable = replace(rotating.rotation, keeper_id="no-such-secret")
    fallback = worker._revoke_guide(setup, provider, unreadable, "u1")
    assert fallback.startswith(worker._REVOKE_GUIDE)
    assert "The key to keep can't be named: no such secret." in fallback
    plain = cast(Provider, Provides([]))
    assert worker._revoke_guide(setup, plain, rotating.rotation, "u1") == worker._REVOKE_GUIDE
