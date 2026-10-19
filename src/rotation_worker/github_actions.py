"""The rotation App: it writes one repository's Actions secret, and reads that repository's report.

ADR-0055 amendment 1 (question 6) and the increment 7 amendment. Actions secrets are write-only,
so the executor writes them through a dedicated GitHub App, `alobar-sds-rotation`, installed only
on the repositories that hold a registered copy, and verifies them by reading what a workflow in
the same repository reports about them.

* **One token per repository and purpose.** Each write mints an installation token for the
  destination's one repository with exactly `{secrets: write, metadata: read}`; each read mints
  one with exactly `{actions: read, metadata: read}`. The mint refuses unless GitHub's answer
  names that repository alone and exactly those permissions, so an App granted more can't hand
  the executor more. Nothing assumes a token's length: GitHub is lengthening them.
* **A write reaches only what the approved destination names.** The client is built for one
  repository and one secret name, and permits only that secret's public key, read and write.
* **A write is sealed with the repository's public key** (libsodium's sealed box, through
  PyNaCl), and counts only once the secret's `updated_at` has moved.
* **The report** is `.github/workflows/secret-fingerprint.yml`, hourly in each holding repository
  with no permissions. Its log carries `fingerprint <NAME> <sha256 prefix> <length>` per secret,
  hashed as `Value.fingerprint` hashes. GitHub answers a job's log with a redirect to a signed
  URL on Azure blob storage, so that one hop is made by a second client that permits only that
  host and sends no credential.
"""

from __future__ import annotations

import base64
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import urlsplit

import httpx

from estate_clients.confined import ConfinedClient, TransportFailure
from rotation_worker.contract import ContractError
from rotation_worker.values import Fingerprint, Value

API: Final = "https://api.github.com"
USER_AGENT: Final = "rotation-worker/1 (+AlobarQuest/orchestrator)"
TIMEOUT_SECONDS: Final = 30.0
WRITE: Final[Mapping[str, str]] = {"secrets": "write", "metadata": "read"}
READ: Final[Mapping[str, str]] = {"actions": "read", "metadata": "read"}
HEADERS: Final = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
REPORT_WORKFLOW: Final = "secret-fingerprint.yml"
# A report older than this means the workflow stopped (GitHub disables a schedule after 60 days
# without repository activity), which a person must see rather than wait on.
REPORT_MAX_AGE: Final = timedelta(hours=3)
# Where GitHub's job-log redirect points (measured 2026-10-19: productionresultssa3).
LOG_HOST: Final = re.compile(r"^productionresultssa\d+\.blob\.core\.windows\.net$")
REPOSITORY: Final = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")
SECRET_NAME: Final = re.compile(r"^[A-Z_][A-Z0-9_]*$")
REPORT_LINE: Final = re.compile(r"(?:^|\s)fingerprint ([A-Z_][A-Z0-9_]*) ([0-9a-f]{8}) (\d+)$")
JWT_LIFETIME: Final = timedelta(minutes=9)
UPDATE_CHECKS: Final = 3
UPDATE_RECHECK_SECONDS: Final = 2.0


class GitHubUnavailable(RuntimeError):
    """GitHub couldn't be asked this time. The message never carries a token."""


class ReportMissing(ContractError):
    """A holding repository has no recent report: its workflow stopped or never ran."""


@dataclass(frozen=True)
class Report:
    """What a repository's latest successful report says one secret holds, and when it ran."""

    fingerprint: Fingerprint | None
    started: datetime


def _refuse(method: str, path: str) -> ContractError:
    return ContractError(f"the rotation App may not {method} {path}")


def _check_repository(repository: str) -> None:
    if not REPOSITORY.fullmatch(repository):
        raise ContractError(f"{repository!r} isn't an owner/name repository")


class RotationApp:
    """The App's identity: it mints installation tokens, and nothing else."""

    def __init__(
        self,
        app_id: str,
        private_key: Callable[[], Value],
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._app_id = app_id
        self._private_key = private_key
        self._transport = transport
        self._clock = clock

    def token(self, repository: str, permissions: Mapping[str, str]) -> Value:
        """An installation token for `repository` alone, with exactly `permissions`."""
        _check_repository(repository)
        owner, name = repository.split("/")
        app = self._client({"Authorization": f"Bearer {self._jwt()}"}, repository)
        installation = _json(self._send(app, "GET", f"/repos/{repository}/installation", 200))
        installation_id = installation.get("id")
        if not isinstance(installation_id, int):
            raise ContractError(f"the rotation App isn't installed on {repository}")
        minted = _json(
            self._send(
                app,
                "POST",
                f"/app/installations/{installation_id}/access_tokens",
                201,
                json={"repositories": [name], "permissions": dict(permissions)},
            )
        )
        granted = minted.get("permissions")
        repositories = [r.get("full_name") for r in minted.get("repositories") or []]
        if granted != dict(permissions):
            raise ContractError(f"the token for {repository} was minted with other permissions")
        # GitHub resolves a repository's name in any case and answers with its own spelling.
        if [str(r).casefold() for r in repositories] != [f"{owner}/{name}".casefold()]:
            raise ContractError(f"the token for {repository} reaches other repositories")
        token = minted.get("token")
        if not isinstance(token, str) or not token:
            raise ContractError(f"the mint for {repository} returned no token")
        return Value(token)

    def _jwt(self) -> str:
        import jwt
        from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
        from cryptography.hazmat.primitives.serialization import load_pem_private_key

        key = load_pem_private_key(self._private_key().reveal().encode(), password=None)
        if not isinstance(key, RSAPrivateKey):
            raise ContractError("the rotation App's key isn't an RSA private key")
        now = self._clock()
        claims = {
            # A minute back, for the clocks GitHub and the VM disagree by.
            "iat": int((now - timedelta(minutes=1)).timestamp()),
            "exp": int((now + JWT_LIFETIME).timestamp()),
            "iss": self._app_id,
        }
        return jwt.encode(claims, key, algorithm="RS256")

    def _client(self, headers: Mapping[str, str], repository: str) -> ConfinedClient:
        mint = re.compile(r"^/app/installations/\d+/access_tokens$")
        allowed = {("GET", f"/repos/{repository}/installation")}
        return ConfinedClient(
            base_url=API,
            user_agent=USER_AGENT,
            headers={**HEADERS, **headers},
            timeout=TIMEOUT_SECONDS,
            permits=lambda m, p: (m, p) in allowed or (m == "POST" and bool(mint.fullmatch(p))),
            refuse=_refuse,
            transport=self._transport,
        )

    @staticmethod
    def _send(
        client: ConfinedClient, method: str, path: str, expected: int, **kwargs: Any
    ) -> httpx.Response:
        return send(client, method, path, (expected,), **kwargs)


def send(
    client: ConfinedClient, method: str, path: str, expected: tuple[int, ...], **kwargs: Any
) -> httpx.Response:
    try:
        response = client.request(method, path, **kwargs)
    except TransportFailure as failure:
        raise GitHubUnavailable(f"GitHub {method} got no answer: {failure.error_type}") from None
    if response.status_code >= 500:
        raise GitHubUnavailable(f"GitHub {method} answered HTTP {response.status_code}")
    if response.status_code not in expected:
        # The body may echo the request, so only the status is kept.
        raise ContractError(f"GitHub {method} {path} answered HTTP {response.status_code}")
    return response


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        raise ContractError("GitHub answered with something other than JSON") from None
    if not isinstance(payload, dict):
        raise ContractError("GitHub answered with something other than an object")
    return payload


class ActionsSecrets:
    """Writes Actions secrets, one approved repository and name at a time."""

    def __init__(
        self,
        app: RotationApp,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._app = app
        self._transport = transport
        self._sleep = sleep

    def write(self, repository: str, name: str, value: Value) -> None:
        """Write `value` to `repository`'s secret `name`, and confirm its `updated_at` moved."""
        if not SECRET_NAME.fullmatch(name):
            raise ContractError(f"{name!r} isn't an Actions secret name")
        token = self._app.token(repository, WRITE)
        key_path = f"/repos/{repository}/actions/secrets/public-key"
        secret_path = f"/repos/{repository}/actions/secrets/{name}"
        allowed = {("GET", key_path), ("GET", secret_path), ("PUT", secret_path)}
        client = ConfinedClient(
            base_url=API,
            user_agent=USER_AGENT,
            headers={**HEADERS, "Authorization": f"Bearer {token.reveal()}"},
            timeout=TIMEOUT_SECONDS,
            permits=lambda method, path: (method, path) in allowed,
            refuse=_refuse,
            transport=self._transport,
        )
        before = self._updated(client, secret_path)
        public_key = _json(send(client, "GET", key_path, (200,)))
        body = {
            "encrypted_value": _seal(value, str(public_key.get("key"))),
            "key_id": public_key.get("key_id"),
        }
        send(client, "PUT", secret_path, (201, 204), json=body)
        # `updated_at` has whole seconds and may lag the PUT, so a write that seems not to have
        # moved it is asked about again before it is refused.
        for _ in range(UPDATE_CHECKS):
            after = self._updated(client, secret_path)
            if after is not None and (before is None or after > before):
                return
            self._sleep(UPDATE_RECHECK_SECONDS)
        raise ContractError(f"{repository}'s {name} didn't record the write")

    @staticmethod
    def _updated(client: ConfinedClient, path: str) -> datetime | None:
        response = send(client, "GET", path, (200, 404))
        if response.status_code == 404:
            return None
        return _instant(_json(response).get("updated_at"))


def _seal(value: Value, public_key: str) -> str:
    from nacl import public

    try:
        key = public.PublicKey(base64.b64decode(public_key, validate=True))
    except ValueError, TypeError:
        raise ContractError("the repository's public key doesn't decode") from None
    sealed = public.SealedBox(key).encrypt(value.reveal().encode())
    return base64.b64encode(sealed).decode()


def _instant(text: object) -> datetime:
    try:
        moment = datetime.fromisoformat(str(text))
    except ValueError:
        raise ContractError("GitHub gave a time that doesn't parse") from None
    if moment.utcoffset() is None:
        raise ContractError("GitHub gave a time without a zone")
    return moment


class FingerprintReports:
    """Reads what each repository's latest successful report says its secrets hold."""

    def __init__(
        self,
        app: RotationApp,
        transport: httpx.BaseTransport | None = None,
        log_transport: httpx.BaseTransport | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._app = app
        self._transport = transport
        self._log_transport = log_transport
        self._clock = clock
        self._lines: dict[str, tuple[dict[str, Fingerprint], datetime]] = {}

    def latest(self, repository: str, name: str) -> Report:
        """The newest successful report's line for `name`. Raises `ReportMissing` when there is
        no recent report, and `GitHubUnavailable` when GitHub couldn't be asked."""
        if repository not in self._lines:
            self._lines[repository] = self._read(repository)
        prints, started = self._lines[repository]
        return Report(prints.get(name), started)

    def _read(self, repository: str) -> tuple[dict[str, Fingerprint], datetime]:
        token = self._app.token(repository, READ)
        runs = f"/repos/{repository}/actions/workflows/{REPORT_WORKFLOW}/runs"
        jobs = re.compile(rf"^/repos/{re.escape(repository)}/actions/runs/\d+/jobs$")
        logs = re.compile(rf"^/repos/{re.escape(repository)}/actions/jobs/\d+/logs$")
        client = ConfinedClient(
            base_url=API,
            user_agent=USER_AGENT,
            headers={**HEADERS, "Authorization": f"Bearer {token.reveal()}"},
            timeout=TIMEOUT_SECONDS,
            permits=lambda m, p: (
                m == "GET" and (p == runs or bool(jobs.fullmatch(p)) or bool(logs.fullmatch(p)))
            ),
            refuse=_refuse,
            transport=self._transport,
        )
        listing = _json(
            send(client, "GET", runs, (200,), params={"status": "success", "per_page": 1})
        )
        latest = (listing.get("workflow_runs") or [None])[0]
        if not isinstance(latest, dict):
            raise ReportMissing(f"{repository} has no successful {REPORT_WORKFLOW} run")
        started = _instant(latest.get("run_started_at"))
        if self._clock() - started > REPORT_MAX_AGE:
            raise ReportMissing(
                f"{repository}'s latest {REPORT_WORKFLOW} run is older than "
                f"{int(REPORT_MAX_AGE.total_seconds() // 3600)} hours"
            )
        listed = _json(
            send(client, "GET", f"/repos/{repository}/actions/runs/{latest['id']}/jobs", (200,))
        )
        job_ids = [job.get("id") for job in listed.get("jobs") or []]
        if len(job_ids) != 1 or not isinstance(job_ids[0], int):
            raise ReportMissing(f"{repository}'s {REPORT_WORKFLOW} run has no single job")
        redirect = send(
            client, "GET", f"/repos/{repository}/actions/jobs/{job_ids[0]}/logs", (302,)
        )
        return _parse(self._fetch_log(redirect.headers.get("location", ""))), started

    def _fetch_log(self, location: str) -> str:
        parts = urlsplit(location)
        if parts.scheme != "https" or not LOG_HOST.fullmatch(parts.hostname or ""):
            raise ContractError("GitHub's job log redirect points somewhere unexpected")
        target = parts.path + (f"?{parts.query}" if parts.query else "")
        client = ConfinedClient(
            base_url=f"https://{parts.hostname}",
            user_agent=USER_AGENT,
            headers={},
            timeout=TIMEOUT_SECONDS,
            permits=lambda method, path: method == "GET" and bool(path),
            refuse=_refuse,
            transport=self._log_transport,
        )
        return send(client, "GET", target, (200,)).text


def _parse(log: str) -> dict[str, Fingerprint]:
    prints: dict[str, Fingerprint] = {}
    for line in log.splitlines():
        found = REPORT_LINE.search(line.rstrip())
        if found is None:
            continue
        name, prefix, length = found.groups()
        if name in prints:
            raise ContractError(f"the report names {name} twice")
        prints[name] = Fingerprint(prefix, int(length))
    return prints
