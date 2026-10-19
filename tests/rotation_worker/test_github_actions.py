"""The rotation App's writer and reports, against a simulated GitHub. Nothing reaches GitHub."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from nacl import public

from rotation_worker.contract import ContractError
from rotation_worker.github_actions import (
    READ,
    WRITE,
    ActionsSecrets,
    FingerprintReports,
    GitHubUnavailable,
    ReportMissing,
    RotationApp,
)
from rotation_worker.values import Value

REPO = "AlobarQuest/orchestrator"
SECRET = "FACTORY_PR_TOKEN"
APP_ID = "5383095"
NOW = datetime(2026, 10, 19, 16, 0, tzinfo=UTC)
LOG_URL = "https://productionresultssa3.blob.core.windows.net/logs/1?sig=signed"
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = _KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.TraditionalOpenSSL,
    serialization.NoEncryption(),
).decode()


class GitHub:
    """One installation on one repository: mints, its secrets, and its report runs."""

    def __init__(self) -> None:
        self.repo_key = public.PrivateKey.generate()
        self.secrets: dict[str, tuple[str, str]] = {}
        self.requests: list[httpx.Request] = []
        self.granted: dict[str, str] | None = None
        self.minted_repositories: list[str] | None = None
        self.record_writes = True
        # GETs after a PUT that still answer the secret's earlier `updated_at`.
        self.stale_reads = 0
        self._previous: str | None = None
        self.runs: list[dict[str, Any]] = [
            {"id": 7, "run_started_at": (NOW - timedelta(minutes=20)).isoformat()}
        ]
        self.log = (
            "2026-10-19T15:40:01.0Z ##[group]Run for name in FACTORY_PR_TOKEN\n"
            '2026-10-19T15:40:01.1Z echo "fingerprint $name $digest ${#value}"\n'
            "2026-10-19T15:40:02.0Z fingerprint FACTORY_PR_TOKEN 1a2b3c4d 93\n"
            "2026-10-19T15:40:02.1Z fingerprint FACTORY_RUNNER_TOKEN 5e6f7a8b 79\n"
        )
        self.log_location = LOG_URL
        self.clock = NOW
        self.token = "ghs_" + "t" * 120
        self.jobs: list[dict[str, Any]] = [{"id": 99}]

    def api(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path, method = request.url.path, request.method
        if path == f"/repos/{REPO}/installation":
            claims = jwt.decode(
                request.headers["Authorization"].removeprefix("Bearer "),
                _KEY.public_key(),
                algorithms=["RS256"],
                # The App's clock is the test's, not the machine's: only the signature counts.
                options={"verify_iat": False, "verify_exp": False, "verify_nbf": False},
            )
            assert claims["iss"] == APP_ID
            return httpx.Response(200, json={"id": 173083952})
        if path.endswith("/installation"):
            return httpx.Response(404)
        if path == "/app/installations/173083952/access_tokens":
            body = json.loads(request.content)
            granted = self.granted if self.granted is not None else body["permissions"]
            names = self.minted_repositories or [f"AlobarQuest/{r}" for r in body["repositories"]]
            return httpx.Response(
                201,
                json={
                    "token": self.token,
                    "permissions": granted,
                    "repositories": [{"full_name": n} for n in names],
                },
            )
        if path.endswith("/actions/secrets/public-key"):
            key = base64.b64encode(bytes(self.repo_key.public_key)).decode()
            return httpx.Response(200, json={"key": key, "key_id": "kid"})
        if "/actions/secrets/" in path:
            name = path.rsplit("/", 1)[1]
            if method == "PUT":
                body = json.loads(request.content)
                sealed = base64.b64decode(body["encrypted_value"])
                plain = public.SealedBox(self.repo_key).decrypt(sealed).decode()
                if self.record_writes:
                    self._previous = self.secrets.get(name, (None, None))[1]
                    self.clock += timedelta(seconds=5)
                    self.secrets[name] = (plain, self.clock.isoformat())
                return httpx.Response(201)
            if name not in self.secrets:
                return httpx.Response(404)
            if self.stale_reads and self._previous is not None:
                self.stale_reads -= 1
                return httpx.Response(200, json={"name": name, "updated_at": self._previous})
            return httpx.Response(200, json={"name": name, "updated_at": self.secrets[name][1]})
        if path.endswith("/secret-fingerprint.yml/runs"):
            return httpx.Response(200, json={"workflow_runs": self.runs[:1]})
        if path.endswith("/jobs"):
            return httpx.Response(200, json={"jobs": self.jobs})
        if path.endswith("/logs"):
            return httpx.Response(302, headers={"location": self.log_location})
        return httpx.Response(418)

    def blob(self, request: httpx.Request) -> httpx.Response:
        assert "authorization" not in request.headers
        return httpx.Response(200, text=self.log)


@pytest.fixture
def gh() -> GitHub:
    return GitHub()


def _app(gh: GitHub) -> RotationApp:
    return RotationApp(
        APP_ID, lambda: Value(PEM.strip()), httpx.MockTransport(gh.api), clock=lambda: NOW
    )


def _writer(gh: GitHub) -> ActionsSecrets:
    return ActionsSecrets(_app(gh), httpx.MockTransport(gh.api), sleep=lambda _seconds: None)


def _reports(gh: GitHub) -> FingerprintReports:
    return FingerprintReports(
        _app(gh),
        httpx.MockTransport(gh.api),
        httpx.MockTransport(gh.blob),
        clock=lambda: NOW,
    )


def test_a_write_seals_the_value_for_the_named_secret_alone(gh: GitHub) -> None:
    _writer(gh).write(REPO, SECRET, Value("new-pat"))
    assert gh.secrets[SECRET][0] == "new-pat"
    mint = next(r for r in gh.requests if r.url.path.endswith("/access_tokens"))
    assert json.loads(mint.content) == {"repositories": ["orchestrator"], "permissions": WRITE}
    for request in gh.requests:
        assert "new-pat" not in request.content.decode(errors="ignore")


def test_a_write_that_github_didn_t_record_is_refused(gh: GitHub) -> None:
    gh.record_writes = False
    with pytest.raises(ContractError, match="didn't record the write"):
        _writer(gh).write(REPO, SECRET, Value("an-actions-secret-for-tests"))


def test_an_overwrite_counts_only_once_updated_at_moves(gh: GitHub) -> None:
    gh.secrets[SECRET] = ("old", (NOW + timedelta(hours=1)).isoformat())
    with pytest.raises(ContractError, match="didn't record the write"):
        _writer(gh).write(REPO, SECRET, Value("an-actions-secret-for-tests"))


def test_a_token_minted_with_more_than_asked_is_refused(gh: GitHub) -> None:
    gh.granted = {**WRITE, "actions": "write"}
    with pytest.raises(ContractError, match="other permissions"):
        _app(gh).token(REPO, WRITE)


def test_a_token_reaching_another_repository_is_refused(gh: GitHub) -> None:
    gh.minted_repositories = [REPO, "AlobarQuest/brain"]
    with pytest.raises(ContractError, match="other repositories"):
        _app(gh).token(REPO, WRITE)


def test_a_repository_the_app_isn_t_installed_on_is_refused(gh: GitHub) -> None:
    with pytest.raises(ContractError, match="HTTP 404"):
        _app(gh).token("AlobarQuest/factory-runner", WRITE)


@pytest.mark.parametrize(
    ("repository", "name"),
    [("AlobarQuest/orchestrator/../brain", SECRET), (REPO, "factory_pr_token"), (REPO, "A/B")],
)
def test_a_malformed_repository_or_name_is_refused_before_any_request(
    gh: GitHub, repository: str, name: str
) -> None:
    with pytest.raises(ContractError):
        _writer(gh).write(repository, name, Value("an-actions-secret-for-tests"))
    assert gh.requests == []


def test_the_write_client_permits_only_the_approved_secret(gh: GitHub) -> None:
    writer = _writer(gh)
    writer.write(REPO, SECRET, Value("an-actions-secret-for-tests"))
    paths = {(r.method, r.url.path) for r in gh.requests if "/actions/" in r.url.path}
    assert paths == {
        ("GET", f"/repos/{REPO}/actions/secrets/public-key"),
        ("GET", f"/repos/{REPO}/actions/secrets/{SECRET}"),
        ("PUT", f"/repos/{REPO}/actions/secrets/{SECRET}"),
    }


def test_a_report_gives_each_secret_s_fingerprint_and_the_run_start(gh: GitHub) -> None:
    report = _reports(gh).latest(REPO, SECRET)
    assert report.fingerprint is not None
    assert (report.fingerprint.sha256_prefix, report.fingerprint.length) == ("1a2b3c4d", 93)
    assert report.started == NOW - timedelta(minutes=20)
    mint = next(r for r in gh.requests if r.url.path.endswith("/access_tokens"))
    assert json.loads(mint.content)["permissions"] == READ


def test_a_report_that_doesn_t_name_the_secret_answers_none(gh: GitHub) -> None:
    assert _reports(gh).latest(REPO, "OTHER_SECRET").fingerprint is None


def test_a_stale_report_is_missing_not_awaited(gh: GitHub) -> None:
    gh.runs = [{"id": 7, "run_started_at": (NOW - timedelta(hours=4)).isoformat()}]
    with pytest.raises(ReportMissing, match="older than 3 hours"):
        _reports(gh).latest(REPO, SECRET)


def test_no_report_at_all_is_missing(gh: GitHub) -> None:
    gh.runs = []
    with pytest.raises(ReportMissing, match="no successful"):
        _reports(gh).latest(REPO, SECRET)


def test_a_log_redirect_to_another_host_is_refused(gh: GitHub) -> None:
    gh.log_location = "https://attacker.example/logs/1"
    with pytest.raises(ContractError, match="somewhere unexpected"):
        _reports(gh).latest(REPO, SECRET)


def test_a_report_naming_a_secret_twice_is_refused(gh: GitHub) -> None:
    gh.log += "2026-10-19T15:40:03.0Z fingerprint FACTORY_PR_TOKEN 99999999 93\n"
    with pytest.raises(ContractError, match="twice"):
        _reports(gh).latest(REPO, SECRET)


def test_github_s_5xx_is_unavailable_not_a_refusal() -> None:
    def down(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    app = RotationApp(APP_ID, lambda: Value(PEM.strip()), httpx.MockTransport(down))
    with pytest.raises(GitHubUnavailable):
        app.token(REPO, READ)


def test_a_mint_with_no_token_is_refused(gh: GitHub) -> None:
    gh.token = ""
    with pytest.raises(ContractError, match="returned no token"):
        _app(gh).token(REPO, WRITE)


def test_a_report_run_with_more_than_one_job_is_missing(gh: GitHub) -> None:
    gh.jobs = [{"id": 99}, {"id": 100}]
    with pytest.raises(ReportMissing, match="no single job"):
        _reports(gh).latest(REPO, SECRET)


def test_a_write_whose_updated_at_lags_is_asked_again(gh: GitHub) -> None:
    gh.secrets[SECRET] = ("old", (NOW - timedelta(days=1)).isoformat())
    gh.stale_reads = 2
    _writer(gh).write(REPO, SECRET, Value("an-actions-secret-for-tests"))
    assert gh.secrets[SECRET][0] == "an-actions-secret-for-tests"


def test_a_repository_spelled_in_another_case_is_the_same_repository(gh: GitHub) -> None:
    gh.minted_repositories = [REPO]

    def lower(request: httpx.Request) -> httpx.Response:
        path = request.url.path.replace("alobarquest/orchestrator", REPO)
        return gh.api(
            httpx.Request(
                request.method,
                f"https://api.github.com{path}",
                headers=request.headers,
                content=request.content,
            )
        )

    app = RotationApp(APP_ID, lambda: Value(PEM.strip()), httpx.MockTransport(lower))
    assert app.token("alobarquest/orchestrator", WRITE).reveal().startswith("ghs_")
