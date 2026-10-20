"""The no-dependency ecosystem, the weekly audit of main, and the alert issue.

The alert is what makes a held sync reach the maintainer: before it, a failed review was visible
only in the landing lane's log. The workflow tests pin what the rendered jobs may hold, because
the alert job runs beside the job that holds `ANTHROPIC_API_KEY`.
"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from fork_sync import fork_sync_review as review
from fork_sync.onboard import REQUIRED_CHECKS, render

NO_DEPS = """
upstream = "nyldn/claude-octopus"
ecosystem = "npm-no-deps"
release_tag_pattern = '^v(\\d+\\.\\d+\\.\\d+)$'
test_commands = ["make test"]
"""
CARGO = """
upstream = "rtk-ai/rtk"
ecosystem = "cargo"
release_tag_pattern = '^v(\\d+\\.\\d+\\.\\d+)$'
"""


def _never(argv: list[str], cwd: Path, env: dict[str, str]) -> tuple[int, bytes]:
    raise AssertionError(f"no audit runs for this ecosystem: {argv}")


# ---- the no-dependency ecosystem ------------------------------------------------------------


def test_the_no_dependency_ecosystem_reads_package_json_and_builds_nothing() -> None:
    config = review.parse_config(NO_DEPS)
    assert config.build_commands == ()
    assert config.test_commands == ("make test",)
    assert review.manifest_version("npm-no-deps", '{"version": "11.14.1"}') == "11.14.1"


@pytest.mark.parametrize("ecosystem", sorted(review.ECOSYSTEMS))
def test_every_ecosystem_has_path_rules_and_an_audit(ecosystem: str) -> None:
    assert ecosystem in review.PATH_RULES
    if review.ECOSYSTEMS[ecosystem].lockfile is not None:
        assert ecosystem in review.ADVISORY_READERS


def test_a_manifest_with_no_dependencies_has_nothing_to_audit() -> None:
    head = {"package.json": b'{"name": "x", "version": "1.0.0", "dependencies": {}}'}
    assert review.audit_findings("npm-no-deps", head.get, _never) == []


@pytest.mark.parametrize("key", review.NPM_DEPENDENCY_KEYS)
def test_a_declared_dependency_is_a_finding(key: str) -> None:
    head = {"package.json": json.dumps({key: {"left-pad": "1.3.0"}}).encode()}
    [finding] = review.audit_findings("npm-no-deps", head.get, _never)
    assert finding.kind == "dependencies-declared"
    assert key in finding.summary


def test_the_dependency_finding_is_bound_to_the_declared_tables() -> None:
    def finding(manifest: dict[str, Any]) -> review.Finding:
        [found] = review.declared_dependencies(json.dumps(manifest).encode())
        return found

    pinned = finding({"version": "1.0.0", "dependencies": {"a": "1.0.0"}})
    renamed = finding({"version": "2.0.0", "dependencies": {"a": "1.0.0"}})
    bumped = finding({"version": "1.0.0", "dependencies": {"a": "1.0.1"}})
    assert pinned.fingerprint() == renamed.fingerprint()
    assert pinned.fingerprint() != bumped.fingerprint()


def test_an_unreadable_manifest_is_a_finding_not_a_pass() -> None:
    [finding] = review.declared_dependencies(b"{not json")
    assert finding.kind == "manifest-unreadable"


def test_the_no_dependency_ecosystem_keeps_the_package_json_rules(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """An install-time script appearing is still a finding: the ecosystem drops only the audit."""
    monkeypatch.chdir(tmp_path)
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, {"package.json": '{"version": "1.0.0"}\n'}, "base")
    head = _commit(
        tmp_path, {"package.json": '{"version": "1.0.0", "scripts": {"postinstall": "x"}}\n'}, "h"
    )
    model = _opener([{"content": [{"type": "text", "text": '{"findings": []}'}]}], [])
    report = review.build_report(review.parse_config(NO_DEPS), base, head, "key", model, _never)
    assert report.unavailable is None
    assert [f.kind for f in report.findings] == ["npm-lifecycle"]


# ---- the weekly audit of main ---------------------------------------------------------------


CARGO_REPORT = json.dumps(
    {
        "vulnerabilities": {
            "list": [
                {
                    "advisory": {"id": "RUSTSEC-2026-0001", "title": "bad", "cvss": None},
                    "package": {"name": "dep", "version": "1.0.0"},
                    "versions": {"patched": [">=1.0.1"]},
                }
            ]
        }
    }
).encode()


def _vulnerable(argv: list[str], cwd: Path, env: dict[str, str]) -> tuple[int, bytes]:
    return 1, CARGO_REPORT


def _main_repo(tmp_path: Path, monkeypatch: Any, acks: str = "") -> None:
    monkeypatch.chdir(tmp_path)
    _git(tmp_path, "init", "-q", "-b", "main")
    files = {
        ".github/fork-sync.toml": CARGO,
        "Cargo.toml": '[package]\nversion = "0.1.0"\n',
        "Cargo.lock": "version = 3\n",
    }
    if acks:
        files[".github/fork-sync-acks.toml"] = acks
    _commit(tmp_path, files, "main")


def test_the_weekly_audit_fails_on_an_unacknowledged_advisory(
    tmp_path: Path, monkeypatch: Any
) -> None:
    _main_repo(tmp_path, monkeypatch)
    assert review.audit_main({}, _vulnerable) == 1


def test_the_weekly_audit_reads_main_s_acknowledgements(tmp_path: Path, monkeypatch: Any) -> None:
    """The same fingerprint a sync pull request's review printed, so one acknowledgement serves
    both. Control: without it, the same main fails (preceding test)."""
    [finding] = review.advisory_findings("cargo", 1, CARGO_REPORT, b"", "Cargo.lock")
    ack = (
        f'[[ack]]\nfingerprint = "{finding.fingerprint()}"\nby = "devon"\n'
        'date = "2026-10-20"\nreason = "read"\n'
    )
    _main_repo(tmp_path, monkeypatch, ack)
    assert review.audit_main({}, _vulnerable) == 0


def test_the_weekly_audit_reads_git_not_the_working_tree(tmp_path: Path, monkeypatch: Any) -> None:
    """An uncommitted acknowledgement in the checkout is not main's."""
    _main_repo(tmp_path, monkeypatch)
    [finding] = review.advisory_findings("cargo", 1, CARGO_REPORT, b"", "Cargo.lock")
    (tmp_path / ".github/fork-sync-acks.toml").write_text(
        f'[[ack]]\nfingerprint = "{finding.fingerprint()}"\nby = "x"\ndate = "d"\nreason = "r"\n'
    )
    assert review.audit_main({}, _vulnerable) == 1


def test_an_audit_that_cannot_run_fails_the_weekly_audit(tmp_path: Path, monkeypatch: Any) -> None:
    _main_repo(tmp_path, monkeypatch)
    assert review.audit_main({}, lambda argv, cwd, env: (1, b"network down")) == 2


def test_a_main_without_a_declaration_fails_the_weekly_audit(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.chdir(tmp_path)
    _git(tmp_path, "init", "-q", "-b", "main")
    _commit(tmp_path, {"README": "x\n"}, "main")
    assert review.audit_main({}, _never) == 2


# ---- the alert issue ------------------------------------------------------------------------

MARKER = "<!-- fork-sync-alert:review -->"
ENV = {
    "ALERT_KIND": "review",
    "GITHUB_TOKEN": "t",
    "GITHUB_API_URL": "https://api.github.com",
    "GITHUB_SERVER_URL": "https://github.com",
    "GITHUB_REPOSITORY": "AlobarQuest/rtk",
    "GITHUB_REPOSITORY_OWNER": "AlobarQuest",
    "PR_NUMBER": "10",
    "HEAD_SHA": "a" * 40,
    "RUN_URL": "https://github.com/AlobarQuest/rtk/actions/runs/1",
}
ISSUES = "https://api.github.com/repos/AlobarQuest/rtk/issues"
LISTING = f"{ISSUES}?state=open&creator=github-actions%5Bbot%5D&per_page=100"


class _Response(io.BytesIO):
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_: object) -> None:
        return None


class FakeGitHub:
    def __init__(
        self, issues: list[dict[str, Any]], comments: list[dict[str, Any]] | None = None
    ) -> None:
        self.issues = issues
        self.comments = comments or []
        self.calls: list[tuple[str, str, Any]] = []

    def __call__(self, request: Any) -> _Response:
        payload = json.loads(request.data) if request.data else None
        self.calls.append((request.get_method(), request.full_url, payload))
        if request.full_url == LISTING:
            return _Response(json.dumps(self.issues).encode())
        if request.get_method() == "GET":
            return _Response(json.dumps(self.comments).encode())
        return _Response(b"{}")

    def writes(self) -> list[tuple[str, str, Any]]:
        return [call for call in self.calls if call[0] != "GET"]


def _open_issue(body: str = f"{MARKER}\nfirst") -> dict[str, Any]:
    return {"number": 3, "body": body}


def test_a_failed_review_opens_an_issue_that_mentions_the_owner() -> None:
    github = FakeGitHub([])
    assert review.notify(ENV | {"RESULT": "failure"}, github) == 0
    [(method, url, payload)] = github.writes()
    assert (method, url) == ("POST", ISSUES)
    assert payload["title"] == review.ALERT_TITLES["review"]
    assert payload["body"].startswith(MARKER)
    assert "@AlobarQuest" in payload["body"]
    assert "https://github.com/AlobarQuest/rtk/pull/10" in payload["body"]
    assert f"<!-- head:{'a' * 40} -->" in payload["body"]


def test_a_failure_at_a_new_head_comments_on_the_open_issue() -> None:
    github = FakeGitHub([_open_issue(f"{MARKER}\n<!-- head:{'b' * 40} -->")])
    assert review.notify(ENV | {"RESULT": "failure"}, github) == 0
    [(method, url, payload)] = github.writes()
    assert (method, url) == ("POST", f"{ISSUES}/3/comments")
    assert "@AlobarQuest" in payload["body"]


@pytest.mark.parametrize("where", ["body", "comment"])
def test_a_failure_at_a_head_already_reported_writes_nothing(where: str) -> None:
    seen = f"{MARKER}\n<!-- head:{'a' * 40} -->"
    if where == "body":
        github = FakeGitHub([_open_issue(seen)])
    else:
        github = FakeGitHub([_open_issue()], [{"body": f"x <!-- head:{'a' * 40} -->"}])
    assert review.notify(ENV | {"RESULT": "failure"}, github) == 0
    assert github.writes() == []


def test_a_pass_closes_the_open_issue() -> None:
    github = FakeGitHub([_open_issue()])
    assert review.notify(ENV | {"RESULT": "success"}, github) == 0
    assert [(m, u) for m, u, _ in github.writes()] == [
        ("POST", f"{ISSUES}/3/comments"),
        ("PATCH", f"{ISSUES}/3"),
    ]
    assert github.writes()[-1][2] == {"state": "closed"}


def test_a_pass_with_no_open_issue_writes_nothing() -> None:
    github = FakeGitHub([])
    assert review.notify(ENV | {"RESULT": "success"}, github) == 0
    assert github.writes() == []


def test_only_the_bot_s_issue_with_the_kind_s_marker_is_the_alert() -> None:
    """The listing asks for the workflow bot's issues; among them, a pull request or the other
    kind's issue is not this alert."""
    others = [
        {"number": 1, "body": MARKER, "pull_request": {}},
        {"number": 2, "body": "<!-- fork-sync-alert:audit -->"},
    ]
    github = FakeGitHub(others)
    assert review.notify(ENV | {"RESULT": "failure"}, github) == 0
    assert [(m, u) for m, u, _ in github.writes()] == [("POST", ISSUES)]
    assert github.calls[0][1] == LISTING


@pytest.mark.parametrize("result", ["cancelled", "skipped", ""])
def test_a_result_that_is_neither_pass_nor_fail_does_nothing(result: str) -> None:
    github = FakeGitHub([_open_issue()])
    assert review.notify(ENV | {"RESULT": result}, github) == 0
    assert github.calls == []


def test_the_audit_alert_names_main_and_no_pull_request() -> None:
    body = review.alert_body("audit", ENV | {"ALERT_KIND": "audit"})
    assert body.startswith("<!-- fork-sync-alert:audit -->")
    assert "/pull/" not in body


def _failing(request: Any) -> _Response:
    raise OSError("GitHub is down")


def test_an_alert_that_cannot_be_raised_fails_the_job() -> None:
    assert review.notify(ENV | {"RESULT": "failure"}, _failing) == 1


def test_an_alert_that_cannot_be_closed_only_warns() -> None:
    """A red job would hold a pull request whose review passed, with no alert to say so."""
    assert review.notify(ENV | {"RESULT": "success"}, _failing) == 0


def test_an_unknown_alert_kind_is_refused() -> None:
    assert review.notify(ENV | {"ALERT_KIND": "other", "RESULT": "failure"}, _failing) == 2


# ---- the rendered workflows -----------------------------------------------------------------


def _ci(ecosystem: str) -> dict[str, Any]:
    return yaml.safe_load(render(ecosystem)[".github/workflows/ci.yml"])


@pytest.mark.parametrize("ecosystem", sorted(review.ECOSYSTEMS))
def test_every_ecosystem_renders_the_required_jobs_alert_and_audit(ecosystem: str) -> None:
    jobs = _ci(ecosystem)["jobs"]
    names = {job["name"] for job in jobs.values()}
    assert set(REQUIRED_CHECKS) <= names
    assert {"notify", "weekly audit"} <= names


@pytest.mark.parametrize("ecosystem", sorted(review.ECOSYSTEMS))
@pytest.mark.parametrize("job", ["notify", "audit"])
def test_the_alert_and_audit_jobs_hold_no_secret_and_write_only_issues(
    ecosystem: str, job: str
) -> None:
    definition = _ci(ecosystem)["jobs"][job]
    assert definition["permissions"] == {"contents": "read", "issues": "write"}
    assert "secrets." not in json.dumps(definition)


@pytest.mark.parametrize("ecosystem", sorted(review.ECOSYSTEMS))
def test_the_alert_runs_the_base_s_script(ecosystem: str) -> None:
    [checkout] = [s for s in _ci(ecosystem)["jobs"]["notify"]["steps"] if "checkout" in str(s)]
    assert checkout["with"]["ref"] == "${{ github.event.pull_request.base.sha }}"


@pytest.mark.parametrize("ecosystem", sorted(review.ECOSYSTEMS))
def test_the_schedule_runs_only_the_audit(ecosystem: str) -> None:
    """A scheduled run must not build the head or need the review's key."""
    jobs = _ci(ecosystem)["jobs"]
    assert "schedule" not in jobs["build-and-test"]["if"].replace("!= 'schedule'", "")
    assert "pull_request" in jobs["security-review"]["if"]
    assert "schedule" in jobs["audit"]["if"]
    [checkout] = [s for s in jobs["audit"]["steps"] if "checkout" in str(s)]
    assert checkout["with"]["ref"] == "main"


def test_the_no_dependency_template_installs_nothing() -> None:
    text = render("npm-no-deps")[".github/workflows/ci.yml"].decode()
    assert "npm ci" not in text
    assert "npm install" not in text


# ---- helpers --------------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit(repo: Path, files: dict[str, str], message: str) -> str:
    for path, content in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _opener(responses: list[Any], seen: list[Any]) -> Any:
    def opener(request: Any) -> _Response:
        seen.append(request)
        return _Response(json.dumps(responses[0]).encode())

    return opener
