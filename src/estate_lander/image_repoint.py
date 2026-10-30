"""Pointing a repository's applications at another image: the hosting platform and the registry.

Landing Part 3b, Task 4. When a landed bump in a repository that redeploys is judged bad, the
estate lander points each of that repository's applications at the image of the bump's parent,
and after the revert lands points them back at the moving tag. These two clients are all it uses
to do so.

## The hosting platform's client is confined to one repository's applications

A Coolify token names actions, not resources: any token that can change an application's image can
change every application, and delete servers besides. So the bound is this client. It may list the
applications, and -- for the applications the member record lists for ONE repository -- read one,
change its image tag (the PATCH body is that one field), and start a deploy of it (`/start`, which
pulls the image; `/restart` reuses the running one). Nothing else: `permits` refuses every other
path and verb before the transport sees a byte, and the surface test lists all of it (review focus
4).

**The allowlist is derived, never hand-kept.** `confine` matches each member-record application's
health host to the one Coolify application whose `fqdn` names that host. An application that no
Coolify application serves, or that two serve, refuses the whole re-point: production is never
pointed back by halves on a guess. `confine` runs once per client, so a client cannot be widened to
a second repository.

The token is sent only over TLS or to a tailnet address: production Coolify's API has no TLS and is
reached over the tailnet (ADR-0055 amendment 9).

## The registry is read for a digest and nothing else

`Registry.digest` asks GHCR for a pull token for one of this estate's images and then for the
manifest of one tag, answering its digest, or None when the registry does not hold that tag. A
GitHub token with package read authenticates the token request (`gh auth token`, as the landers'
other GitHub reads); no new credential.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlsplit

import httpx

from estate_clients.confined import ConfinedClient, TransportFailure, tailnet_or_https_problem

USER_AGENT: Final = "estate-lander/1 (+AlobarQuest/orchestrator)"
TIMEOUT_SECONDS: Final = 30.0
# Production Coolify on the tailnet (`ovh-vps1`), as the rotation executor reaches it.
DEFAULT_COOLIFY_URL: Final = "http://100.92.138.19:8000"

TAG_FIELD: Final = "docker_registry_image_tag"
_IMAGE_FIELD: Final = "docker_registry_image_name"
_APPLICATIONS: Final = "/api/v1/applications"
# One application, or its deploy. A Coolify uuid is lower-case letters and digits.
_ONE: Final = re.compile(r"/api/v1/applications/([a-z0-9]+)(/start)?")

GHCR: Final = "https://ghcr.io"
# This estate's images, and only them: `ghcr.io/alobarquest/<name>`.
_OWNER: Final = "alobarquest"
_IMAGE: Final = re.compile(r"ghcr\.io/alobarquest/([a-z0-9][a-z0-9._-]*)")
_TAG: Final = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")
# The manifest kinds a build here pushes: brain's moving tag is an OCI index.
_MANIFESTS: Final = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)


class RepointRefused(Exception):
    """The platform could not be read or written, or would have been reached beyond the bound.

    Its message names the cause by kind -- a status, an exception type, an application name --
    and never a body, a header or the token.
    """


class RegistryUnreadable(Exception):
    """The registry could not say whether it holds the tag. Never read as "it does not"."""


@dataclass(frozen=True)
class AppSpec:
    """One application the member record lists for the repository: its name and health route."""

    name: str
    health_url: str


@dataclass(frozen=True)
class Application:
    """One Coolify application, matched to a member-record application by host."""

    uuid: str
    name: str
    health_url: str
    image: str
    tag: str


def _permits(allowed: frozenset[str], method: str, path: str) -> bool:
    """The whole surface. The listing always; for an allowed uuid, GET and PATCH of the
    application and POST of its deploy; nothing else."""
    if path == _APPLICATIONS:
        return method == "GET"
    match = _ONE.fullmatch(path)
    if match is None or match.group(1) not in allowed:
        return False
    if match.group(2):
        return method == "POST"
    return method in ("GET", "PATCH")


def _hosts(fqdn: object) -> set[str]:
    """Every host a Coolify `fqdn` names: a comma-separated list of URLs, case-folded."""
    if not isinstance(fqdn, str):
        return set()
    return {host.lower() for part in fqdn.split(",") if (host := urlsplit(part.strip()).hostname)}


def _string(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise RepointRefused(f"the platform's application carries no {key}")
    return value


class CoolifyImages:
    """The hosting platform, confined to one repository's applications once `confine` has run."""

    def __init__(
        self, base_url: str, token: str, *, transport: httpx.BaseTransport | None = None
    ) -> None:
        problem = tailnet_or_https_problem(base_url)
        if problem is not None:
            raise RepointRefused(f"the Coolify URL {problem}")
        self._allowed: frozenset[str] = frozenset()
        self._confined = False
        try:
            self._client = ConfinedClient(
                base_url=base_url,
                user_agent=USER_AGENT,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                timeout=TIMEOUT_SECONDS,
                transport=transport,
                permits=self.permits,
                refuse=lambda method, path: RepointRefused(f"Coolify may not {method} {path}"),
            )
        except TransportFailure as failure:
            raise RepointRefused(f"the Coolify URL is unusable: {failure.error_type}") from None

    def permits(self, method: str, path: str) -> bool:
        return _permits(self._allowed, method, path)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> CoolifyImages:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def confine(self, apps: Sequence[AppSpec]) -> tuple[Application, ...]:
        """Match each application to the one Coolify application serving its host, and allow
        exactly those. Refused when any matches none or several, or when nothing is listed."""
        if self._confined:
            raise RepointRefused("this client is already confined to a repository")
        if not apps:
            raise RepointRefused("the member record lists no application for the repository")
        listing = self._send("GET", _APPLICATIONS).json()
        if not isinstance(listing, list) or not all(isinstance(row, dict) for row in listing):
            raise RepointRefused("the platform's application listing did not read")
        matched: list[Application] = []
        for app in apps:
            host = (urlsplit(app.health_url).hostname or "").lower()
            serving = [row for row in listing if host and host in _hosts(row.get("fqdn"))]
            if len(serving) != 1:
                count = "no application" if not serving else f"{len(serving)} applications"
                raise RepointRefused(f"{app.name}: {count} on the platform serve {host or '?'}")
            matched.append(self._application(serving[0], app))
        self._allowed = frozenset(application.uuid for application in matched)
        self._confined = True
        return tuple(matched)

    def application(self, uuid: str) -> Application:
        """One allowed application as the platform has it now."""
        row = self._send("GET", f"{_APPLICATIONS}/{uuid}").json()
        if not isinstance(row, dict):
            raise RepointRefused("the platform's application did not read")
        return self._application(row, AppSpec(str(row.get("name") or uuid), ""))

    def set_tag(self, uuid: str, tag: str) -> None:
        """Change one allowed application's image tag. The body is that one field."""
        self._send("PATCH", f"{_APPLICATIONS}/{uuid}", json={TAG_FIELD: tag})

    def start(self, uuid: str) -> None:
        """Start a deploy of one allowed application, which pulls its image tag."""
        self._send("POST", f"{_APPLICATIONS}/{uuid}/start")

    @staticmethod
    def _application(row: dict[str, Any], app: AppSpec) -> Application:
        uuid = _string(row, "uuid")
        if not re.fullmatch(r"[a-z0-9]+", uuid):
            raise RepointRefused("the platform's application uuid did not read")
        return Application(
            uuid=uuid,
            name=app.name,
            health_url=app.health_url,
            image=_string(row, _IMAGE_FIELD),
            tag=_string(row, TAG_FIELD),
        )

    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._client.request(method, path, **kwargs)
        except TransportFailure as failure:
            raise RepointRefused(f"Coolify {method} got no answer: {failure.error_type}") from None
        if not 200 <= response.status_code < 300:
            # The body may echo the request, so only the status is kept.
            raise RepointRefused(f"Coolify {method} answered HTTP {response.status_code}")
        return response


def image_name(image: str) -> str | None:
    """The package name of one of this estate's GHCR images, or None for any other image."""
    match = _IMAGE.fullmatch(image.strip().lower())
    return None if match is None or ".." in match.group(1) else match.group(1)


def _registry_permits(method: str, path: str) -> bool:
    if path == "/token":
        return method == "GET"
    parts = path.split("/")
    return (
        method == "HEAD"
        and len(parts) == 6
        and parts[:3] == ["", "v2", _OWNER]
        and parts[4] == "manifests"
        and image_name(f"ghcr.io/{_OWNER}/{parts[3]}") == parts[3]
        and _TAG.fullmatch(parts[5]) is not None
    )


class Registry:
    """GHCR, read for the digest of one tag of one of this estate's images."""

    def __init__(self, github_token: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self._github_token = github_token
        try:
            self._client = ConfinedClient(
                base_url=GHCR,
                user_agent=USER_AGENT,
                headers={},
                timeout=TIMEOUT_SECONDS,
                transport=transport,
                permits=_registry_permits,
                refuse=lambda method, path: RegistryUnreadable(f"GHCR may not {method} {path}"),
            )
        except TransportFailure as failure:
            raise RegistryUnreadable(f"GHCR is unusable: {failure.error_type}") from None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Registry:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def digest(self, image: str, tag: str) -> str | None:
        """The digest `image:tag` names, or None when the registry holds no such tag."""
        name = image_name(image)
        if name is None or _TAG.fullmatch(tag) is None:
            raise RegistryUnreadable("only a tag of this estate's GHCR images is read")
        token = self._token(name)
        response = self._request(
            "HEAD",
            f"/v2/{_OWNER}/{name}/manifests/{tag}",
            headers={"Authorization": f"Bearer {token}", "Accept": _MANIFESTS},
        )
        if response.status_code == 404:
            return None
        digest = response.headers.get("Docker-Content-Digest", "")
        if response.status_code != 200 or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise RegistryUnreadable(f"GHCR answered HTTP {response.status_code} for the tag")
        return digest

    def _token(self, name: str) -> str:
        response = self._request(
            "GET",
            "/token",
            params={"scope": f"repository:{_OWNER}/{name}:pull", "service": "ghcr.io"},
            auth=(_OWNER, self._github_token),
        )
        try:
            token = response.json().get("token") if response.status_code == 200 else None
        except ValueError:
            token = None
        if not isinstance(token, str) or not token:
            raise RegistryUnreadable(f"GHCR issued no pull token (HTTP {response.status_code})")
        return token

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return self._client.request(method, path, **kwargs)
        except TransportFailure as failure:
            raise RegistryUnreadable(f"GHCR got no answer: {failure.error_type}") from None
