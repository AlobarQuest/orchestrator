"""Is change-window's infraops pin behind? One digest comparison, read from two public files.

ADR-0059 Amendment 18 bakes infraops-mcp-server's `dist/bundle/change-mgr-cli.js` into the lane
image at `INFRAOPS_COMMIT`, with its sha256 beside it in `lanes/Dockerfile`. The pin is STALE when
the bundle at infraops' `main` differs from that digest. It is judged on the digest, never on the
commit: almost every infraops commit leaves the bundle unchanged, so a commit check would report
every one of them.

* **Both sides are read at `main`.** The Dockerfile is orchestrator `main`'s, over the public raw
  URL, never this image's baked copy: the question is whether the pin the next release bakes is
  current, not the one this container runs.
* **The bundle's URL is the Dockerfile's.** The `ADD` line that fetches the bundle at
  `${INFRAOPS_COMMIT}` is read, and the commit swapped for `main`, so infraops' owner, repository
  and path are written once, in the Dockerfile.
* **No credential.** Both files are public; the client sends no `Authorization` header, and only
  GETs on the raw host.
* **Unread is not equal.** A failed fetch, a non-200 or a Dockerfile that doesn't parse raises
  `BundleUnreadable`, which the pass treats as unread: the key it had stays as it was, never
  resolved, and the pass exits incomplete.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlsplit

import httpx

from alert_hub_client import Alert, alert_key
from estate_clients.confined import ConfinedClient, TransportFailure

RAW_HOST: Final = "raw.githubusercontent.com"
RAW: Final = f"https://{RAW_HOST}"
# The one place this check names the orchestrator's own repository, as the rotation worker's
# `BEHIND_REPOSITORY` does. Everything about infraops is read from the file this names.
DOCKERFILE_PATH: Final = "/AlobarQuest/orchestrator/main/lanes/Dockerfile"
USER_AGENT: Final = "pin-watcher/1 (+AlobarQuest/orchestrator)"
KEY: Final = alert_key("infraops-pin-behind", "change-window")

_COMMIT_PLACEHOLDER: Final = "${INFRAOPS_COMMIT}"
_COMMIT = re.compile(r"^ARG INFRAOPS_COMMIT=([0-9a-f]{40})$", re.MULTILINE)
_ADD = re.compile(r"^ADD --checksum=sha256:([0-9a-f]{64})\s+(\S+)\s+change-mgr-cli\.js$")


class BundleUnreadable(Exception):
    """One side of the comparison couldn't be read. Never a statement about the pin."""


@dataclass(frozen=True)
class Pin:
    """What orchestrator `main`'s Dockerfile pins: the commit, the digest, and `main`'s bundle."""

    commit: str
    digest: str
    main_path: str


@dataclass(frozen=True)
class Reading:
    pin: Pin
    main_digest: str

    @property
    def stale(self) -> bool:
        return self.main_digest != self.pin.digest


def _instructions(text: str) -> list[str]:
    """The Dockerfile's instructions, each continuation joined onto one line."""
    joined = re.sub(r"\\\n\s*", " ", text)
    return [line.strip() for line in joined.splitlines()]


def parse_dockerfile(text: str) -> Pin:
    """The pin, as `lanes/Dockerfile` writes it. Anything but exactly one of each is unreadable."""
    commits = _COMMIT.findall(text)
    if len(commits) != 1:
        raise BundleUnreadable(f"lanes/Dockerfile has {len(commits)} INFRAOPS_COMMIT pins, not 1")
    adds = [
        match
        for line in _instructions(text)
        if (match := _ADD.match(line)) and _COMMIT_PLACEHOLDER in match.group(2)
    ]
    if len(adds) != 1:
        raise BundleUnreadable(f"lanes/Dockerfile has {len(adds)} pinned bundle ADDs, not 1")
    digest, url = adds[0].groups()
    parsed = urlsplit(url.replace(_COMMIT_PLACEHOLDER, "main"))
    if parsed.scheme != "https" or parsed.hostname != RAW_HOST or parsed.query or parsed.fragment:
        raise BundleUnreadable(f"the pinned bundle's URL is not on {RAW_HOST}: {url}")
    return Pin(commit=commits[0], digest=digest, main_path=parsed.path)


def _refuse(method: str, path: str) -> BundleUnreadable:
    return BundleUnreadable(f"the bundle check may not {method} {path}")


def _get(client: ConfinedClient, path: str) -> bytes:
    try:
        response = client.request("GET", path)
    except TransportFailure as failure:
        raise BundleUnreadable(f"{RAW}{path} is unreachable: {failure.error_type}") from None
    if response.status_code != 200:
        raise BundleUnreadable(f"{RAW}{path} answered {response.status_code}")
    return response.content


def read(transport: httpx.BaseTransport | None = None) -> Reading:
    """Both digests, read now. Raises `BundleUnreadable` for anything short of both."""
    try:
        client = ConfinedClient(
            base_url=RAW,
            user_agent=USER_AGENT,
            headers={},
            timeout=30.0,
            transport=transport,
            permits=lambda method, path: method == "GET" and path.startswith("/"),
            refuse=_refuse,
        )
    except TransportFailure as failure:
        raise BundleUnreadable(f"the raw client is unusable: {failure.error_type}") from None
    try:
        try:
            dockerfile = _get(client, DOCKERFILE_PATH).decode()
        except UnicodeDecodeError:
            raise BundleUnreadable(f"{RAW}{DOCKERFILE_PATH} is not UTF-8") from None
        pin = parse_dockerfile(dockerfile)
        main_digest = hashlib.sha256(_get(client, pin.main_path)).hexdigest()
    finally:
        client.close()
    return Reading(pin=pin, main_digest=main_digest)


def describe(reading: Reading) -> str:
    """The pass's one log line: both digests, short."""
    verdict = "behind" if reading.stale else "current"
    return (
        f"infraops pin {verdict}: pinned {reading.pin.digest[:12]} "
        f"(commit {reading.pin.commit[:12]}), main {reading.main_digest[:12]}"
    )


def alert(reading: Reading) -> Alert:
    return Alert(
        "change-window's infraops pin is behind infraops main",
        f"lanes/Dockerfile pins bundle {reading.pin.digest[:12]} at commit "
        f"{reading.pin.commit[:12]}; infraops main's bundle is {reading.main_digest[:12]}. "
        'Bump it: docs/operations/deploy.md, "Bump change-window\'s infraops pin".',
    )


def classify(reading: Reading | None) -> tuple[dict[str, Alert], set[str]]:
    """The firing alerts and unread keys for one sync. `None` is a reading that failed."""
    if reading is None:
        return {}, {KEY}
    if reading.stale:
        return {KEY: alert(reading)}, set()
    return {}, set()
