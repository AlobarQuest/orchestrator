"""Mint a GitHub App installation token narrowed to a repository list and a permission set.

ADR-0059 increment 3. A read lane mints a narrow read token from the read-only App every pass, and
the rotation executor mints one per repository and purpose (`rotation_worker.github_actions`).
Both go through `mint`, so one mint and one echo check exist.

* **One installation per mint.** The installation comes from the requested repositories, never
  from a configured id or an owner name written here: every repository is looked up with
  `GET /repos/{owner}/{name}/installation`, and a mint whose repositories span more than one owner,
  or whose lookups resolve more than one installation, is refused before anything is minted.
* **The answer must echo the request.** GitHub's answer names the permissions and repositories it
  granted. The mint refuses unless the permissions equal the request and the casefolded repository
  set equals the request, so an App granted more can't hand its caller more.
* **The key's two forms.** In-process, `mint` takes the PEM text. The stored form, in BWS and on
  the console script's stdin, is base64 of the PEM: one line as macOS `base64` writes it, or
  wrapped as `openssl base64` and GNU `base64` write it, the same form the dispatch App
  (`orchestrator.services.github_app`) and the fork-sync App store. Raw PEM on stdin is refused,
  because it means the keeper holds something other than the stored form.
* **Nothing carries the key, the JWT or the token.** A failure names its cause and, for a request
  that got no answer, the exception's type; an error body is never kept, because it may echo the
  request. The token leaves only as the return value, and the console script prints it alone.

`sds-github-mint` is the console script. It reads the key on stdin (never argv, never an
environment variable), prints only the token on stdout, and writes `FATAL: <cause>` on stderr for
every failure. It accepts only `read` levels unless `--allow-write` is given, so a read lane's
launcher can't ask for write by a typo; `mint` itself accepts the levels its caller passes.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Final, TextIO

import httpx

from estate_clients.confined import ConfinedClient, TransportFailure

API: Final = "https://api.github.com"
USER_AGENT: Final = "sds-github-mint/1 (+AlobarQuest/orchestrator)"
TIMEOUT_SECONDS: Final = 30.0
HEADERS: Final = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
JWT_LIFETIME: Final = timedelta(minutes=9)
# GitHub's own bound on the `repositories` list of one mint.
MAX_REPOSITORIES: Final = 500
REPOSITORY: Final = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")
PERMISSION_NAME: Final = re.compile(r"^[a-z_]+$")
APP_ID: Final = re.compile(r"^[0-9]+$")
_MINT_PATH: Final = re.compile(r"^/app/installations/\d+/access_tokens$")
_PEM_HEADER: Final = "-----BEGIN"

EXIT_FAILED: Final = 1


class MintRefused(Exception):
    """The request, or GitHub's answer to it, isn't what a narrowed mint allows."""


class MintUnavailable(Exception):
    """GitHub couldn't be asked this time: no answer, or a 5xx."""


def mint(
    app_id: str,
    private_key_pem: str,
    repositories: Sequence[str],
    permissions: Mapping[str, str],
    *,
    transport: httpx.BaseTransport | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> str:
    """An installation token for exactly `repositories` (`owner/name`) with exactly `permissions`.

    Raises `MintRefused` for a request or answer outside that, and `MintUnavailable` when GitHub
    couldn't be asked.
    """
    wanted = _check_request(app_id, repositories, permissions)
    label = ", ".join(repositories)
    client = _client(_jwt(app_id, private_key_pem, clock), wanted, transport)
    try:
        installations = {_installation(client, repository) for repository in wanted.values()}
        if len(installations) != 1:
            raise MintRefused(f"{label} resolve to more than one installation; mint once for each")
        (installation_id,) = installations
        minted = _json(
            _send(
                client,
                "POST",
                f"/app/installations/{installation_id}/access_tokens",
                201,
                json={
                    "repositories": [r.split("/")[1] for r in wanted.values()],
                    "permissions": dict(permissions),
                },
            )
        )
    finally:
        client.close()
    if minted.get("permissions") != dict(permissions):
        raise MintRefused(f"the token for {label} was minted with other permissions")
    # GitHub resolves a repository's name in any case and answers with its own spelling.
    echoed = [str(r.get("full_name")).casefold() for r in minted.get("repositories") or []]
    if len(echoed) != len(wanted) or set(echoed) != set(wanted):
        raise MintRefused(f"the token for {label} reaches other repositories")
    token = minted.get("token")
    if not isinstance(token, str) or not token:
        raise MintRefused(f"the mint for {label} returned no token")
    return token


def _check_request(
    app_id: str, repositories: Sequence[str], permissions: Mapping[str, str]
) -> dict[str, str]:
    """The requested repositories keyed by casefolded name, once the request is well formed."""
    if not APP_ID.fullmatch(app_id):
        raise MintRefused("the App id isn't a number")
    wanted = _check_repositories(repositories)
    if not permissions:
        # GitHub grants every permission the App holds when none is named.
        raise MintRefused("a mint names at least one permission")
    for name, level in permissions.items():
        if not PERMISSION_NAME.fullmatch(name) or not isinstance(level, str) or not level:
            raise MintRefused(f"{name!r} isn't a permission name and level")
    return wanted


def _check_repositories(repositories: Sequence[str]) -> dict[str, str]:
    if not repositories:
        raise MintRefused("a mint names at least one repository")
    if len(repositories) > MAX_REPOSITORIES:
        raise MintRefused(f"a mint names at most {MAX_REPOSITORIES} repositories")
    wanted: dict[str, str] = {}
    for repository in repositories:
        name = repository.split("/")[-1]
        if not REPOSITORY.fullmatch(repository) or name in {".", ".."}:
            raise MintRefused(f"{repository!r} isn't an owner/name repository")
        if repository.casefold() in wanted:
            raise MintRefused(f"{repository} is named twice")
        wanted[repository.casefold()] = repository
    if len({key.split("/")[0] for key in wanted}) != 1:
        raise MintRefused(
            "the repositories belong to more than one owner; mint once for each installation"
        )
    return wanted


def _jwt(app_id: str, private_key_pem: str, clock: Callable[[], datetime]) -> str:
    import jwt
    from cryptography.exceptions import UnsupportedAlgorithm
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    try:
        key = load_pem_private_key(private_key_pem.encode(), password=None)
    except (ValueError, TypeError, UnsupportedAlgorithm) as error:
        raise MintRefused(f"the App's key doesn't load: {type(error).__name__}") from None
    if not isinstance(key, RSAPrivateKey):
        raise MintRefused("the App's key isn't an RSA private key")
    now = clock()
    claims = {
        # A minute back, for the clocks GitHub and this machine disagree by.
        "iat": int((now - timedelta(minutes=1)).timestamp()),
        "exp": int((now + JWT_LIFETIME).timestamp()),
        "iss": app_id,
    }
    try:
        return jwt.encode(claims, key, algorithm="RS256")
    except (ValueError, TypeError, jwt.PyJWTError) as error:
        raise MintRefused(f"the App's JWT couldn't be signed: {type(error).__name__}") from None


def _client(
    assertion: str, wanted: Mapping[str, str], transport: httpx.BaseTransport | None
) -> ConfinedClient:
    lookups = {("GET", f"/repos/{repository}/installation") for repository in wanted.values()}
    try:
        return ConfinedClient(
            base_url=API,
            user_agent=USER_AGENT,
            headers={**HEADERS, "Authorization": f"Bearer {assertion}"},
            timeout=TIMEOUT_SECONDS,
            permits=lambda m, p: (
                (m, p) in lookups or (m == "POST" and bool(_MINT_PATH.fullmatch(p)))
            ),
            refuse=lambda method, path: MintRefused(f"the mint may not {method} {path}"),
            transport=transport,
        )
    except TransportFailure as failure:
        raise MintUnavailable(f"GitHub's client couldn't be built: {failure.error_type}") from None


def _installation(client: ConfinedClient, repository: str) -> int:
    found = _json(_send(client, "GET", f"/repos/{repository}/installation", 200))
    installation_id = found.get("id")
    if not isinstance(installation_id, int) or isinstance(installation_id, bool):
        raise MintRefused(f"the App isn't installed on {repository}")
    return installation_id


def _send(
    client: ConfinedClient, method: str, path: str, expected: int, **kwargs: Any
) -> httpx.Response:
    try:
        response = client.request(method, path, **kwargs)
    except TransportFailure as failure:
        raise MintUnavailable(f"GitHub {method} got no answer: {failure.error_type}") from None
    if response.status_code >= 500:
        raise MintUnavailable(f"GitHub {method} answered HTTP {response.status_code}")
    if response.status_code != expected:
        # The body may echo the request, so only the status is kept.
        raise MintRefused(f"GitHub {method} {path} answered HTTP {response.status_code}")
    return response


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        raise MintRefused("GitHub answered with something other than JSON") from None
    if not isinstance(payload, dict):
        raise MintRefused("GitHub answered with something other than an object")
    return payload


def decode_stored_key(stored: str) -> str:
    """The PEM text inside the stored form: base64 of the PEM, wrapped or on one line."""
    text = stored.strip()
    if not text:
        raise MintRefused("no key arrived on stdin")
    if text.startswith(_PEM_HEADER):
        raise MintRefused("the key arrived as PEM; the stored form is base64 of the PEM")
    try:
        pem = base64.b64decode("".join(text.split()), validate=True).decode("ascii")
    except binascii.Error, ValueError:
        raise MintRefused(
            "the key on stdin isn't base64; the stored form is base64 of the PEM"
        ) from None
    if not pem.lstrip().startswith(_PEM_HEADER):
        raise MintRefused(
            "the key on stdin doesn't decode to a PEM; the stored form is base64 of the PEM"
        )
    return pem


def _permission(text: str) -> tuple[str, str]:
    name, separator, level = text.partition("=")
    if not separator or not PERMISSION_NAME.fullmatch(name) or not level:
        raise MintRefused(f"--permission {text!r} isn't name=level")
    return name, level


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sds-github-mint",
        description=(
            "Mint a GitHub App installation token for exactly the named repositories and "
            "permissions. Reads the App's key, base64 of the PEM, on stdin; prints only the token."
        ),
    )
    parser.add_argument("--app-id", required=True)
    parser.add_argument("--repository", action="append", required=True, metavar="OWNER/NAME")
    parser.add_argument("--permission", action="append", required=True, metavar="NAME=LEVEL")
    parser.add_argument(
        "--allow-write",
        action="store_true",
        help="accept write levels; without it every level must be read",
    )
    return parser


def run(
    argv: Sequence[str] | None,
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
    transport: httpx.BaseTransport | None = None,
) -> int:
    """`main` with its streams and transport passed in, so tests drive the real invocation."""
    arguments = _parser().parse_args(argv)
    try:
        permissions: dict[str, str] = {}
        for text in arguments.permission:
            name, level = _permission(text)
            if name in permissions:
                raise MintRefused(f"--permission {name} is named twice")
            allowed = {"read", "write"} if arguments.allow_write else {"read"}
            if level not in allowed:
                raise MintRefused(
                    f"--permission {name}={level} isn't accepted"
                    + ("" if arguments.allow_write else "; only read levels without --allow-write")
                )
            permissions[name] = level
        pem = decode_stored_key(stdin.read())
        token = mint(arguments.app_id, pem, arguments.repository, permissions, transport=transport)
    except (MintRefused, MintUnavailable) as error:
        print(f"FATAL: {error}", file=stderr)
        return EXIT_FAILED
    stdout.write(token)
    stdout.flush()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return run(argv, sys.stdin, sys.stdout, sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
