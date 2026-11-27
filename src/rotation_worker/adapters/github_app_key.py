"""The GitHub App private-key adapter: a person mints and deletes, the executor proves (ADR-0055
amendment 35, D-84).

GitHub has no API that creates, lists or deletes an App's key pairs: a person generates one on the
App's settings page and deletes the others there. So a person pastes the new key into the inbox
(base64 of the PEM, the form the mint's `decode_stored_key` reads), the executor proves and
stores it, waits unclaimed until every scheduled reader has run since the keeper's revision, and,
after the person deletes the App's other key pairs, proves the old key dead.

* **Probe** is `GET /app` with a JWT the key signs (`iss` the row's `app_id`). Live is exactly 200
  naming the row's App id; a 200 naming another App refuses by name. The known-bad control is a
  JWT signed by an RSA key generated in memory and discarded, which GitHub never issued. GitHub
  documents no status for either refusal, so `dead_statuses` is `{401}` and the first rotation is
  the measurement (D-84 item 14 A): any other answer fails the step with its status.
* **Identity** holds when both keys answer `GET /app` for the row's App, and their public halves
  differ: a key re-encoded differently passes every probe while rotating nothing. Each App has its
  own id, so a key pasted for another App never binds, and the inbox binds a key naming its own
  waiting rotation while others wait (`inbox.NamesItsCredential`, amendment 35 item 6 B).
* **The working proof** (`check_scope`, run by verify_new once the key answers live): the proof
  repository's installation is the row's, a mint for that repository alone with `metadata: read`
  alone echoes exactly that, the token reaches that one repository, and `DELETE
  /installation/token` answers 204, whatever the reach check found. The token lives only in memory
  for those calls.
* **The revoke alert** names the new key pair by the first 8 characters of GitHub's own SHA-256
  fingerprint of its public half: a hash of a public key, never written into evidence.
* **Mint, host, consumer_loaded and revoke** are never the executor's.

Nothing here logs, keeps or raises with the PEM, a JWT or a token, and no response body is kept:
a refusal names the request, the status, or the failure's type.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Final

import httpx

from estate_clients.confined import ConfinedClient, TransportFailure
from estate_clients.github_app_mint import (
    REPOSITORY,
    MintRefused,
    MintUnavailable,
    decode_stored_key,
    mint,
    sign_app_jwt,
)
from rotation_worker.contract import ContractError, Rotation
from rotation_worker.plans import derive
from rotation_worker.secret_store import SecretStore
from rotation_worker.types import TYPES
from rotation_worker.values import Value

NAME: Final = "github-app-key"
# The rows' `provider`: every registry row of this type names it.
PROVIDER: Final = "github"
CLASS: Final = "github-app-private-key"
API: Final = "https://api.github.com"
USER_AGENT: Final = "rotation-worker/1 (+AlobarQuest/orchestrator)"
HEADERS: Final = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
TIMEOUT_SECONDS: Final = 30.0
APP_ID_FIELD: Final = "app_id"
INSTALLATION_FIELD: Final = "installation_id"
PROOF_FIELD: Final = "proof_repository"
APP_PATH: Final = "/app"
REPOSITORIES_PATH: Final = "/installation/repositories"
TOKEN_PATH: Final = "/installation/token"
# The narrowest token GitHub issues: it grants `metadata: read` with every token.
PROOF_PERMISSIONS: Final = {"metadata": "read"}
# The consumer kinds a row of this type may list (amendment 35 item 11).
KEEPER_KIND: Final = "bws-secret"
READER_KIND: Final = "runtime-fetch"
# The length of the fingerprint prefix the revoke alert names, below the evidence scan's run.
FINGERPRINT_SHOWN: Final = 8
CONTROL_KEY_BITS: Final = 2048
# This type has no packages from before derivation, so its one candidate is the derived plan of a
# credential with no copies to distribute.
PLAN: Final = derive(TYPES[NAME].capabilities, (), "", "bws-secret")


def _declared_id(credential: Mapping[str, Any], field: str) -> int:
    value = credential.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ContractError(f"a {CLASS} row declares its {field} as a positive integer")
    return value


def row_fields(credential: Mapping[str, Any]) -> tuple[int, int, str]:
    """The row's App id, installation id and proof repository, or a refusal."""
    app_id = _declared_id(credential, APP_ID_FIELD)
    installation_id = _declared_id(credential, INSTALLATION_FIELD)
    proof = credential.get(PROOF_FIELD)
    if not isinstance(proof, str) or not REPOSITORY.fullmatch(proof):
        raise ContractError(f"a {CLASS} row's {PROOF_FIELD} is one owner/name repository")
    if proof.split("/")[1] in {".", ".."}:
        raise ContractError(f"a {CLASS} row's {PROOF_FIELD} is one owner/name repository")
    return app_id, installation_id, proof


def check_row(credential: Mapping[str, Any]) -> None:
    """Refuse, before a claim, a row this adapter couldn't act on: one without its declared ids
    and proof repository, or whose consumers aren't one keeper and `runtime-fetch` readers, each
    read by its Healthchecks check."""
    row_fields(credential)
    consumers = credential.get("consumer")
    if not isinstance(consumers, list) or not all(isinstance(c, Mapping) for c in consumers):
        raise ContractError(f"a {CLASS} row lists its consumers")
    kinds = [consumer.get("kind") for consumer in consumers]
    if kinds.count(KEEPER_KIND) != 1:
        raise ContractError(f"a {CLASS} row has exactly one {KEEPER_KIND} keeper")
    others = sorted({str(kind) for kind in kinds} - {KEEPER_KIND, READER_KIND})
    if others:
        raise ContractError(f"a {CLASS} row may not list a {', '.join(others)} consumer")
    readers = [consumer for consumer in consumers if consumer.get("kind") == READER_KIND]
    if not readers:
        raise ContractError(f"a {CLASS} row lists at least one {READER_KIND} reader")
    for reader in readers:
        check = reader.get("healthchecks_check")
        if not isinstance(check, str) or not check:
            raise ContractError(f"each {READER_KIND} reader of a {CLASS} row names its check")


def declared_app(credential: Mapping[str, Any]) -> str | None:
    """The App id a registry row of this type declares, or `None` for a row of another class.
    A row of this class whose `app_id` can't be read raises `ContractError`."""
    if credential.get("class") != CLASS:
        return None
    return str(_declared_id(credential, APP_ID_FIELD))


def build_provider(
    *,
    rotation: Rotation | None,
    credential: Mapping[str, Any] | None,
    store: SecretStore,
    transport: httpx.BaseTransport | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    control_key: Callable[[], str] | None = None,
) -> GitHubAppKeyProvider:
    """`control_key` returns a PEM for the known-bad control, a fresh RSA key unless a test pins
    one; `transport` reaches GitHub for the probe and the proof's mint alike."""
    if rotation is None or credential is None:
        raise ContractError("the github-app-key adapter needs its rotation and registry row")
    app_id, installation_id, proof = row_fields(credential)

    def old() -> Value:
        # The value the new one replaces, read as the contract reads it for a rotation: the
        # quarantine once store has moved it there, the keeper before.
        quarantine = store.find(rotation.work_project_id, rotation.quarantine_key)
        return store.read(quarantine.id if quarantine is not None else rotation.keeper_id)

    installation_path = f"/repos/{proof}/installation"
    permitted = {
        ("GET", APP_PATH),
        ("GET", installation_path),
        ("GET", REPOSITORIES_PATH),
        ("DELETE", TOKEN_PATH),
    }
    try:
        client = ConfinedClient(
            base_url=API,
            user_agent=USER_AGENT,
            headers=HEADERS,
            timeout=TIMEOUT_SECONDS,
            permits=lambda method, path: (method, path) in permitted,
            refuse=lambda method, path: ContractError(f"the probe may not {method} {path}"),
            transport=transport,
        )
    except TransportFailure as failure:
        raise ContractError(f"GitHub's client couldn't be built: {failure.error_type}") from None
    return GitHubAppKeyProvider(
        client,
        app_id=app_id,
        installation_id=installation_id,
        proof_repository=proof,
        old=old,
        transport=transport,
        clock=clock,
        control_key=control_key or _new_rsa_pem,
    )


class GitHubAppKeyProvider:
    name = PROVIDER
    probe_target = "GitHub's GET /app, with a JWT the App's key signs"
    revoke_probe_target = probe_target
    dead_statuses = frozenset({401})
    hosts_destinations = False

    def __init__(
        self,
        client: ConfinedClient,
        *,
        app_id: int,
        installation_id: int,
        proof_repository: str,
        old: Callable[[], Value],
        transport: httpx.BaseTransport | None,
        clock: Callable[[], datetime],
        control_key: Callable[[], str],
    ) -> None:
        self._client = client
        self._app_id = app_id
        self._installation_id = installation_id
        self._proof = proof_repository
        self._old = old
        self._transport = transport
        self._clock = clock
        self._control_key = control_key

    # ---- the probe and the control ------------------------------------------------------------

    def mint(self) -> Value:
        raise ContractError("a person generates a GitHub App key pair, on the App's settings page")

    def known_bad(self) -> Value:
        return Value(base64.b64encode(self._control_key().encode()).decode("ascii"))

    def probe(self, value: Value) -> int | None:
        status, answered = self._app(value)
        if status == 200 and answered != self._app_id:
            raise ContractError(
                "GET /app answered 200 "
                + ("with no App id" if answered is None else f"for App {answered}")
                + f", not the row's App {self._app_id}"
            )
        return status

    def probe_before_revoke(self, value: Value) -> int | None:
        return self.probe(value)

    def _app(self, value: Value) -> tuple[int | None, int | None]:
        """`GET /app`'s status with `value`'s JWT and, on 200, the App id it names."""
        try:
            response = self._send("GET", APP_PATH, self._jwt(value))
        except TransportFailure:
            return None, None
        if response.status_code != 200:
            return response.status_code, None
        return 200, _int_field(_object(response), "id")

    # ---- identity ----------------------------------------------------------------------------

    def same_identity(self, new: Value, old: Value) -> bool:
        """Whether both keys answer `GET /app` for the row's App and are different key pairs."""
        return (
            self.probe(new) == 200
            and self.probe(old) == 200
            and _public_der(new) != _public_der(old)
        )

    @property
    def declared(self) -> str:
        return str(self._app_id)

    def declared_by(self, credential: Mapping[str, Any]) -> str | None:
        return declared_app(credential)

    def identity_of(self, new: Value) -> str | None:
        """The App `new` signs for, by `GET /app`; `None` for a value that isn't an App key or
        that GitHub doesn't accept."""
        try:
            status, answered = self._app(new)
        except ContractError:
            return None
        return str(answered) if status == 200 and answered is not None else None

    # ---- the working proof (amendment 35 items 3 and 4) ---------------------------------------

    def check_scope(self, new: Value) -> str:
        """Refuse a key that is the old key re-encoded, or that can't do what every reader does:
        mint a token on the row's installation. Returns the note verify_new files."""
        if _public_der(new) == _public_der(self._old()):
            raise ContractError("the new key is the old key pair: its public half is the same")
        assertion = self._jwt(new)
        installation = self._installation(assertion)
        if installation != self._installation_id:
            raise ContractError(
                f"{self._proof}'s installation is {installation}, "
                f"not the row's {self._installation_id}"
            )
        token = self._proof_token(new)
        try:
            problem = self._reach_problem(token)
        finally:
            revoked = self._revoke_token(token)
        problems = [found for found in (problem, revoked) if found is not None]
        if problems:
            raise ContractError("; ".join(problems))
        return (
            f"working proof: installation {self._installation_id} on {self._proof} minted a "
            "metadata-read token reaching that repository alone, revoked with 204"
        )

    def _installation(self, assertion: str) -> int | None:
        path = f"/repos/{self._proof}/installation"
        try:
            response = self._send("GET", path, assertion)
        except TransportFailure as failure:
            raise ContractError(f"GET {path} got no answer ({failure.error_type})") from None
        if response.status_code != 200:
            raise ContractError(f"GET {path} answered HTTP {response.status_code}")
        return _int_field(_object(response), "id")

    def _proof_token(self, new: Value) -> Value:
        try:
            token = mint(
                str(self._app_id),
                _pem(new),
                [self._proof],
                PROOF_PERMISSIONS,
                transport=self._transport,
                clock=self._clock,
                user_agent=USER_AGENT,
            )
        except (MintRefused, MintUnavailable) as error:
            raise ContractError(f"the proof mint failed: {error}") from None
        return Value(token)

    def _reach_problem(self, token: Value) -> str | None:
        """Why the token doesn't reach exactly the proof repository, or `None`. Never raises, so
        the token is always revoked after it."""
        try:
            response = self._send("GET", REPOSITORIES_PATH, token.reveal())
        except TransportFailure as failure:
            return f"GET {REPOSITORIES_PATH} got no answer ({failure.error_type})"
        except ContractError as refusal:
            return str(refusal)
        if response.status_code != 200:
            return f"GET {REPOSITORIES_PATH} answered HTTP {response.status_code}"
        try:
            listed = _object(response)
        except ContractError as refusal:
            return str(refusal)
        names = [
            entry.get("full_name") if isinstance(entry, dict) else None
            for entry in listed.get("repositories") or []
        ]
        if (
            listed.get("total_count") != 1
            or len(names) != 1
            or not isinstance(names[0], str)
            or names[0].casefold() != self._proof.casefold()
        ):
            return f"the proof token doesn't reach {self._proof} alone"
        return None

    def _revoke_token(self, token: Value) -> str | None:
        """Why the proof token isn't revoked, or `None` once GitHub answered 204. Never raises."""
        try:
            response = self._send("DELETE", TOKEN_PATH, token.reveal())
        except TransportFailure as failure:
            return f"DELETE {TOKEN_PATH} got no answer ({failure.error_type}): the token may live"
        if response.status_code != 204:
            return f"DELETE {TOKEN_PATH} answered HTTP {response.status_code}: the token may live"
        return None

    # ---- the person's acts ----------------------------------------------------------------------

    def revoke_guide(self, new: Value) -> str:
        """The revoke alert's text, naming the key pair to keep by its fingerprint."""
        digest = base64.b64encode(hashlib.sha256(_public_der(new)).digest()).decode("ascii")
        return (
            "Every reader has minted with the new key. On the App's settings page (Edit, "
            "Credentials, Key pairs), delete every key pair except the one whose SHA256 "
            f"fingerprint begins {digest[:FINGERPRINT_SHOWN]}. The executor can't list key pairs, "
            "so it can't see a pair made by hand and never pasted: delete that too. There is "
            "nothing to record; the executor proves the old key refused."
        )

    def host(self, new: Value, old: Value) -> str:
        raise ContractError("a GitHub App key has no hosted service to host it")

    def consumer_loaded(self, consumer: Mapping[str, Any], new: Value) -> bool:
        raise ContractError("a GitHub App key has no hosted consumer")

    def revoke(self, value: Value) -> str:
        raise ContractError("a person deletes a GitHub App key pair, on the App's settings page")

    # ---- plumbing ------------------------------------------------------------------------------

    def _jwt(self, value: Value) -> str:
        try:
            return sign_app_jwt(str(self._app_id), _pem(value), self._clock)
        except MintRefused as error:
            raise ContractError(str(error)) from None

    def _send(self, method: str, path: str, bearer: str) -> httpx.Response:
        return self._client.request(method, path, headers={"Authorization": f"Bearer {bearer}"})


def _pem(value: Value) -> str:
    try:
        return decode_stored_key(value.reveal())
    except MintRefused:
        raise ContractError(
            "the value isn't the stored form, base64 of the App key's PEM"
        ) from None


def _public_der(value: Value) -> bytes:
    """The key's public half, DER `SubjectPublicKeyInfo`: what GitHub's fingerprint hashes."""
    from cryptography.exceptions import UnsupportedAlgorithm
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        PublicFormat,
        load_pem_private_key,
    )

    try:
        key = load_pem_private_key(_pem(value).encode(), password=None)
    except (ValueError, TypeError, UnsupportedAlgorithm) as error:
        raise ContractError(f"the App's key doesn't load: {type(error).__name__}") from None
    return key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)


def _new_rsa_pem() -> str:
    """A fresh RSA private key, as PEM: GitHub never issued it, so a JWT it signs is refused."""
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
    )

    key = rsa.generate_private_key(public_exponent=65537, key_size=CONTROL_KEY_BITS)
    return key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode("ascii")


def _object(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        raise ContractError("GitHub answered with something other than JSON") from None
    if not isinstance(payload, dict):
        raise ContractError("GitHub answered with something other than an object")
    return payload


def _int_field(payload: Mapping[str, Any], field: str) -> int | None:
    value = payload.get(field)
    return value if isinstance(value, int) and not isinstance(value, bool) else None
