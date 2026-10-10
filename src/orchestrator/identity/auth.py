import hashlib
import re
import secrets
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from orchestrator.identity.registry import RegistryAdapter, RegistryValidationError
from orchestrator.kernel.states import ActorRole

TOKEN_HASH_PATTERN = re.compile(r"[0-9a-f]{64}")

# ADR-0055 decision 7. A rotation sets a previous hash's `until` seven days out as a backstop; the
# overlap ends when retire-old removes it. Boot refuses anything further ahead, so a mistyped
# expiry cannot keep a retired bearer valid indefinitely.
MAX_PREVIOUS_OVERLAP = timedelta(days=8)


class AuthenticationError(PermissionError):
    """Authentication failed without disclosing credential details."""


@dataclass(frozen=True)
class AuthenticatedIdentity:
    """What authentication resolved about a caller, before the API narrows it to an actor."""

    actor_id: str
    role: ActorRole
    authority_profile: str
    credential_key_id: str | None
    registry_version: int


@dataclass(frozen=True)
class PreviousHash:
    """The hash an identity's bearer had before a rotation, accepted only before `until`."""

    token_hash: str
    until: datetime


@dataclass(frozen=True)
class M2MCredential:
    agent_id: str
    token_hash: str
    previous: PreviousHash | None = None


def authenticate_m2m(
    *,
    bearer_token: str,
    credential_key_id: str,
    credentials: Mapping[str, M2MCredential],
    registry: RegistryAdapter,
    now: datetime | None = None,
) -> AuthenticatedIdentity:
    now = now or datetime.now(UTC)
    _validate_m2m_credentials(credentials, now)
    credential = credentials.get(credential_key_id)
    if credential is None:
        raise AuthenticationError("invalid machine credential")
    presented_hash = hashlib.sha256(bearer_token.encode()).hexdigest()
    if not bearer_token or not _hash_accepted(credential, presented_hash, now):
        raise AuthenticationError("invalid machine credential")
    try:
        actor = registry.resolve(credential.agent_id)
    except RegistryValidationError as error:
        raise AuthenticationError("invalid machine identity") from error
    if actor.runtime == "human" or actor.authority_profile == "human-operator-v1":
        raise AuthenticationError("human identities cannot use machine credentials")
    return AuthenticatedIdentity(
        actor_id=actor.agent_id,
        role=ActorRole.WORKER,
        authority_profile=actor.authority_profile,
        credential_key_id=credential_key_id,
        registry_version=actor.version,
    )


def authenticate_human(
    *,
    headers: Sequence[tuple[str, str]],
    peer_ip: str,
    trusted_proxy_ips: Set[str],
    proxy_marker_header: str,
    proxy_marker: str,
    email_header: str,
    email_to_actor: Mapping[str, str],
    registry: RegistryAdapter,
) -> AuthenticatedIdentity:
    marker_values = _header_values(headers, proxy_marker_header)
    email_values = _header_values(headers, email_header)
    has_forward_auth = bool(marker_values) or bool(email_values)
    if peer_ip not in trusted_proxy_ips:
        message = (
            "forward-auth headers received from untrusted peer"
            if has_forward_auth
            else "human authentication requires trusted proxy"
        )
        raise AuthenticationError(message)
    if len(marker_values) != 1 or len(email_values) != 1:
        raise AuthenticationError("forward-auth requires exactly one identity header")
    marker = marker_values[0]
    if not proxy_marker or not secrets.compare_digest(marker, proxy_marker):
        raise AuthenticationError("invalid trusted proxy marker")
    email = email_values[0].strip().lower()
    actor_id = email_to_actor.get(email)
    if actor_id is None:
        raise AuthenticationError("unknown human identity")
    try:
        actor = registry.resolve(actor_id)
    except RegistryValidationError as error:
        raise AuthenticationError("invalid human identity") from error
    if actor.runtime != "human" or actor.authority_profile != "human-operator-v1":
        raise AuthenticationError("registry identity is not a human operator")
    return AuthenticatedIdentity(
        actor_id=actor.agent_id,
        role=ActorRole.HUMAN,
        authority_profile=actor.authority_profile,
        credential_key_id=None,
        registry_version=actor.version,
    )


def _hash_accepted(credential: M2MCredential, presented_hash: str, now: datetime) -> bool:
    """The current hash always; the previous hash only before its `until` (ADR-0055 decision 7)."""
    current = secrets.compare_digest(presented_hash, credential.token_hash)
    previous = credential.previous
    if previous is None:
        return current
    overlapping = secrets.compare_digest(presented_hash, previous.token_hash)
    return current or (overlapping and now < previous.until)


def _header_values(headers: Sequence[tuple[str, str]], target: str) -> list[str]:
    normalized_target = target.lower()
    return [value for name, value in headers if name.lower() == normalized_target]


def _validate_m2m_credentials(
    credentials: Mapping[str, M2MCredential], now: datetime | None = None
) -> None:
    agent_ids: set[str] = set()
    for key_id, credential in credentials.items():
        if (
            not key_id
            or not isinstance(credential, M2MCredential)
            or not credential.agent_id
            or TOKEN_HASH_PATTERN.fullmatch(credential.token_hash) is None
        ):
            raise AuthenticationError("invalid machine credential configuration")
        if credential.agent_id in agent_ids:
            raise AuthenticationError("machine credentials must map one-to-one")
        agent_ids.add(credential.agent_id)
    current_hashes = {credential.token_hash for credential in credentials.values()}
    if not all(
        previous_hash_valid(credential, current_hashes, now or datetime.now(UTC))
        for credential in credentials.values()
    ):
        raise AuthenticationError("invalid machine credential configuration")


def previous_hash_valid(credential: M2MCredential, current_hashes: Set[str], now: datetime) -> bool:
    """Whether a credential's previous hash may be configured; boot and every request ask this.

    A previous hash must not be ANY identity's current hash, its own included: a retired value is
    never live, and one equal to another identity's current hash would let that identity's bearer
    authenticate as this one. Its `until` is a UTC instant at most eight days after `now`, which
    at boot is the boot clock (ADR-0055 decision 7).
    """
    previous = credential.previous
    return previous is None or (
        isinstance(previous, PreviousHash)
        and TOKEN_HASH_PATTERN.fullmatch(previous.token_hash) is not None
        and previous.token_hash not in current_hashes
        and isinstance(previous.until, datetime)
        and previous.until.utcoffset() == timedelta(0)
        and previous.until <= now + MAX_PREVIOUS_OVERLAP
    )
