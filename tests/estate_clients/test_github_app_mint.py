"""The narrowed App mint and its console script, against a simulated GitHub. Nothing reaches GitHub.

The key is generated here, at test time; no key, JWT or token is committed.
"""

from __future__ import annotations

import base64
import io
import json
import tomllib
from collections.abc import Callable
from pathlib import Path

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from estate_clients import github_app_mint
from estate_clients.github_app_mint import (
    MintRefused,
    MintUnavailable,
    decode_stored_key,
    mint,
    run,
)

ROOT = Path(__file__).resolve().parents[2]
APP_ID = "5684758"
INSTALLATION = 180257215
OWNER = "AlobarQuest"
REPOS = [f"{OWNER}/brain", f"{OWNER}/orchestrator"]
READ = {"contents": "read", "metadata": "read"}
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = _KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.TraditionalOpenSSL,
    serialization.NoEncryption(),
).decode()
# The stored form: one line, as macOS `base64` writes it.
STORED = base64.b64encode(PEM.encode()).decode()
TOKEN = "ghs_" + "m" * 100


class GitHub:
    """One App installation: answers installation lookups and mints, and records every request."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.installations: dict[str, int] = {r.casefold(): INSTALLATION for r in REPOS}
        self.granted: dict[str, str] | None = None
        self.echoed: list[str] | None = None
        self.token = TOKEN
        self.assertions: list[str] = []

    def api(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        assertion = request.headers["Authorization"].removeprefix("Bearer ")
        self.assertions.append(assertion)
        claims = jwt.decode(
            assertion,
            _KEY.public_key(),
            algorithms=["RS256"],
            options={"verify_iat": False, "verify_exp": False, "verify_nbf": False},
        )
        assert claims["iss"] == APP_ID
        if path.endswith("/installation"):
            repository = path.removeprefix("/repos/").removesuffix("/installation")
            found = self.installations.get(repository.casefold())
            if found is None:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json={"id": found})
        if path == f"/app/installations/{INSTALLATION}/access_tokens":
            body = json.loads(request.content)
            names = self.echoed or [f"{OWNER}/{name}" for name in body["repositories"]]
            return httpx.Response(
                201,
                json={
                    "token": self.token,
                    "permissions": self.granted
                    if self.granted is not None
                    else body["permissions"],
                    "repositories": [{"full_name": n} for n in names],
                },
            )
        return httpx.Response(418)

    @property
    def mints(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "POST"]


@pytest.fixture
def gh() -> GitHub:
    return GitHub()


def _argv(
    *extra: str, repositories: list[str] = REPOS, permissions: dict[str, str] = READ
) -> list[str]:
    argv = ["--app-id", APP_ID]
    for repository in repositories:
        argv += ["--repository", repository]
    for name, level in permissions.items():
        argv += ["--permission", f"{name}={level}"]
    return [*argv, *extra]


def _run(
    argv: list[str], key: str = STORED, transport: httpx.BaseTransport | None = None
) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    code = run(argv, io.StringIO(key), stdout, stderr, transport=transport)
    return code, stdout.getvalue(), stderr.getvalue()


def _mint(gh: GitHub, repositories: list[str] = REPOS, permissions: dict[str, str] = READ) -> str:
    return mint(APP_ID, PEM, repositories, permissions, transport=httpx.MockTransport(gh.api))


def test_the_script_prints_only_the_token_narrowed_to_the_request(gh: GitHub) -> None:
    code, out, err = _run(_argv(), transport=httpx.MockTransport(gh.api))
    assert (code, out, err) == (0, TOKEN, "")
    (posted,) = gh.mints
    assert json.loads(posted.content) == {
        "repositories": ["brain", "orchestrator"],
        "permissions": READ,
    }
    lookups = [r.url.path for r in gh.requests if r.method == "GET"]
    assert lookups == [f"/repos/{r}/installation" for r in REPOS]


def test_a_wrapped_key_is_the_same_stored_form(gh: GitHub) -> None:
    wrapped = "\n".join(STORED[i : i + 64] for i in range(0, len(STORED), 64)) + "\n"
    code, out, _ = _run(_argv(), key=wrapped, transport=httpx.MockTransport(gh.api))
    assert (code, out) == (0, TOKEN)


def test_a_permission_echo_that_differs_is_refused(gh: GitHub) -> None:
    gh.granted = {**READ, "administration": "read"}
    code, out, err = _run(_argv(), transport=httpx.MockTransport(gh.api))
    assert (code, out) == (1, "")
    assert "other permissions" in err


@pytest.mark.parametrize(
    "echoed",
    [
        pytest.param([*REPOS, f"{OWNER}/rtk"], id="extra"),
        pytest.param(REPOS[:1], id="missing"),
        pytest.param([REPOS[0], REPOS[0]], id="one-twice"),
    ],
)
def test_a_repository_echo_that_differs_is_refused(gh: GitHub, echoed: list[str]) -> None:
    gh.echoed = echoed
    code, out, err = _run(_argv(), transport=httpx.MockTransport(gh.api))
    assert (code, out) == (1, "")
    assert "reaches other repositories" in err


def test_github_s_own_spelling_of_a_repository_is_the_same_repository(gh: GitHub) -> None:
    gh.echoed = [r.lower() for r in REPOS]
    assert _mint(gh) == TOKEN


def test_repositories_of_more_than_one_owner_are_refused_before_any_request(gh: GitHub) -> None:
    code, out, err = _run(
        _argv(repositories=[REPOS[0], "someone-else/brain"]), transport=httpx.MockTransport(gh.api)
    )
    assert (code, out) == (1, "")
    assert "more than one owner" in err
    assert gh.requests == []


def test_lookups_that_resolve_two_installations_are_refused_before_a_mint(gh: GitHub) -> None:
    gh.installations[REPOS[1].casefold()] = INSTALLATION + 1
    code, _, err = _run(_argv(), transport=httpx.MockTransport(gh.api))
    assert code == 1
    assert "more than one installation" in err
    assert gh.mints == []


def test_a_repository_the_app_isn_t_installed_on_is_refused(gh: GitHub) -> None:
    del gh.installations[REPOS[1].casefold()]
    code, _, err = _run(_argv(), transport=httpx.MockTransport(gh.api))
    assert code == 1
    assert "HTTP 404" in err
    assert gh.mints == []


def test_a_transport_error_is_unavailable_and_names_only_its_type() -> None:
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(MintUnavailable, match="got no answer: ConnectError"):
        mint(APP_ID, PEM, REPOS, READ, transport=httpx.MockTransport(unreachable))
    code, out, err = _run(_argv(), transport=httpx.MockTransport(unreachable))
    assert (code, out, err) == (1, "", "FATAL: GitHub GET got no answer: ConnectError\n")


def test_github_s_5xx_is_unavailable_not_a_refusal() -> None:
    with pytest.raises(MintUnavailable, match="HTTP 503"):
        mint(
            APP_ID, PEM, REPOS, READ, transport=httpx.MockTransport(lambda _r: httpx.Response(503))
        )


def test_a_mint_with_no_token_is_refused(gh: GitHub) -> None:
    gh.token = ""
    with pytest.raises(MintRefused, match="returned no token"):
        _mint(gh)


@pytest.mark.parametrize(
    ("key", "cause"),
    [
        pytest.param(PEM, "arrived as PEM", id="raw-pem"),
        pytest.param("not base64 at all!", "isn't base64", id="not-base64"),
        pytest.param(
            base64.b64encode(b"hello").decode(), "doesn't decode to a PEM", id="not-a-pem"
        ),
        pytest.param("", "no key arrived", id="empty"),
        pytest.param(
            base64.b64encode(b"-----BEGIN RSA PRIVATE KEY-----\nAAAA\n").decode(),
            "key doesn't load",
            id="broken-pem",
        ),
    ],
)
def test_a_bad_key_form_is_refused_before_any_request(gh: GitHub, key: str, cause: str) -> None:
    code, out, err = _run(_argv(), key=key, transport=httpx.MockTransport(gh.api))
    assert (code, out) == (1, "")
    assert cause in err
    assert gh.requests == []


def test_a_key_that_isn_t_rsa_is_refused(gh: GitHub) -> None:
    other = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    code, _, err = _run(
        _argv(), key=base64.b64encode(other).decode(), transport=httpx.MockTransport(gh.api)
    )
    assert code == 1
    assert "isn't an RSA private key" in err
    assert gh.requests == []


def test_a_write_level_without_allow_write_is_refused_before_any_request(gh: GitHub) -> None:
    code, out, err = _run(
        _argv(permissions={"contents": "write", "metadata": "read"}),
        transport=httpx.MockTransport(gh.api),
    )
    assert (code, out) == (1, "")
    assert "only read levels without --allow-write" in err
    assert gh.requests == []


def test_allow_write_accepts_a_write_level(gh: GitHub) -> None:
    write = {"contents": "write", "metadata": "read"}
    code, out, _ = _run(
        _argv("--allow-write", permissions=write), transport=httpx.MockTransport(gh.api)
    )
    assert (code, out) == (0, TOKEN)
    assert json.loads(gh.mints[0].content)["permissions"] == write


def test_admin_is_refused_even_with_allow_write(gh: GitHub) -> None:
    code, _, err = _run(
        _argv("--allow-write", permissions={"administration": "admin"}),
        transport=httpx.MockTransport(gh.api),
    )
    assert code == 1
    assert "administration=admin isn't accepted" in err
    assert gh.requests == []


@pytest.mark.parametrize(
    ("repositories", "permissions", "cause"),
    [
        pytest.param([], READ, "at least one repository", id="no-repository"),
        pytest.param([f"{OWNER}/.."], READ, "isn't an owner/name", id="dot-dot"),
        pytest.param([f"{OWNER}/a/../b"], READ, "isn't an owner/name", id="traversal"),
        pytest.param([REPOS[0], REPOS[0].lower()], READ, "named twice", id="twice"),
        pytest.param(REPOS, {}, "at least one permission", id="no-permission"),
    ],
)
def test_a_malformed_request_is_refused_before_any_request(
    gh: GitHub, repositories: list[str], permissions: dict[str, str], cause: str
) -> None:
    with pytest.raises(MintRefused, match=cause):
        _mint(gh, repositories, permissions)
    assert gh.requests == []


def test_a_malformed_permission_argument_is_refused(gh: GitHub) -> None:
    argv = ["--app-id", APP_ID, "--repository", REPOS[0], "--permission", "contents"]
    code, _, err = _run(argv, transport=httpx.MockTransport(gh.api))
    assert code == 1
    assert "isn't name=level" in err


def _failures(gh: GitHub) -> list[Callable[[], tuple[int, str, str]]]:
    """Every failure the script can report once the key has been read."""
    transport = httpx.MockTransport(gh.api)

    def with_github(change: Callable[[GitHub], object]) -> Callable[[], tuple[int, str, str]]:
        def failure() -> tuple[int, str, str]:
            fresh = GitHub()
            change(fresh)
            return _run(_argv(), transport=httpx.MockTransport(fresh.api))

        return failure

    def raising(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    return [
        with_github(lambda g: setattr(g, "granted", {"contents": "write"})),
        with_github(lambda g: setattr(g, "echoed", [*REPOS, f"{OWNER}/rtk"])),
        with_github(lambda g: setattr(g, "token", "")),
        with_github(lambda g: g.installations.pop(REPOS[1].casefold())),
        lambda: _run(_argv(), transport=httpx.MockTransport(raising)),
        lambda: _run(_argv(), transport=httpx.MockTransport(lambda _r: httpx.Response(500))),
        lambda: _run(_argv(), key=PEM, transport=transport),
        lambda: _run(_argv(permissions={"contents": "write"}), transport=transport),
    ]


def test_no_failure_carries_the_key_the_jwt_or_the_token(
    gh: GitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    pem_body = "".join(PEM.splitlines()[1:-1])
    secrets = [pem_body, pem_body[:40], STORED, STORED[:40], TOKEN, "Bearer", "eyJ"]
    for failure in _failures(gh):
        code, out, err = failure()
        assert code == 1
        assert out == ""
        assert err.startswith("FATAL: ")
        for secret in secrets:
            assert secret not in err
    captured = capsys.readouterr()
    for secret in secrets:
        assert secret not in captured.out + captured.err


def test_no_raised_refusal_carries_the_key_or_the_token(gh: GitHub) -> None:
    gh.granted = {"contents": "write"}
    with pytest.raises(MintRefused) as raised:
        _mint(gh)
    text = f"{raised.value}{raised.value!r}"
    # Nothing chained, so a traceback can't print what an inner exception held.
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None or raised.value.__suppress_context__
    for secret in (PEM, STORED, TOKEN, *gh.assertions):
        assert secret not in text


def test_decode_reads_the_stored_form_back_to_the_pem() -> None:
    assert decode_stored_key(STORED + "\n") == PEM


def test_the_console_script_is_main_and_main_reads_stdin(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]
    assert scripts["sds-github-mint"] == "estate_clients.github_app_mint:main"
    # A refusal before any request: the real entry point, with nothing sent anywhere.
    monkeypatch.setattr("sys.stdin", io.StringIO(PEM))
    assert github_app_mint.main(_argv()) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "the stored form is base64" in captured.err
