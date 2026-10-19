"""`fork-sync` through its real entrypoint, with every command answered by a fake runner.

The fake records every argv, so each acting command is shown both doing its writes when its
precondition holds and writing nothing when it does not.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from fork_sync import cli, fork_sync_review
from fork_sync.gh import CommandFailed
from fork_sync.onboard import ACKS_PATH, EMPTY_ACKS, REQUIRED_CHECKS, render

FORK = "AlobarQuest/rea"
UPSTREAM = "morluto/rea"
CONFIG = f"""upstream = "{UPSTREAM}"
ecosystem = "npm"
release_tag_pattern = '^rea-agents-(\\d+\\.\\d+\\.\\d+)$'
"""
PINNED = [{"context": name, "app_id": 15368} for name in REQUIRED_CHECKS]
NOT_FOUND = CommandFailed(["gh"], 1, "gh: Not Found (HTTP 404)")


class FakeRunner:
    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.calls: list[tuple[list[str], str | None]] = []

    def __call__(self, argv: Sequence[str], stdin: str | None, cwd: Path | None) -> str:
        argv = list(argv)
        self.calls.append((argv, stdin))
        if argv[:2] == ["git", "clone"]:
            Path(argv[-1]).mkdir(parents=True)
        key = argv[-1] if argv[:2] == ["gh", "api"] else " ".join(argv[:3])
        answer = self.answers.get(key, "")
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, str) else json.dumps(answer)

    def ran(self, prefix: str) -> bool:
        return any(" ".join(argv).startswith(prefix) for argv, _ in self.calls)


def _invoke(monkeypatch: pytest.MonkeyPatch, runner: FakeRunner, *args: str) -> Any:
    monkeypatch.setattr(cli, "run_command", runner)
    for name in ("FORK_SYNC_CHANGE_MANAGER_TOKEN", "FORK_SYNC_APP_BRAIN_URL"):
        monkeypatch.delenv(name, raising=False)
    return CliRunner().invoke(cli.app, list(args))


def _onboarded() -> dict[str, Any]:
    files = render("npm")
    answers: dict[str, Any] = {
        f"repos/{FORK}": {
            "fork": True,
            "parent": {"full_name": UPSTREAM},
            "allow_merge_commit": True,
            "squash_merge_commit_message": "COMMIT_MESSAGES",
            "delete_branch_on_merge": True,
        },
        f"repos/{FORK}/contents/.github/fork-sync.toml?ref=main": CONFIG,
        f"repos/{FORK}/contents/.github/workflows?ref=main": [
            {"name": "ci.yml"},
            {"name": "upstream-sync.yml"},
        ],
        f"repos/{UPSTREAM}/contents/.github/workflows": [{"name": "ci.yml"}, {"name": "cd.yml"}],
        f"repos/{FORK}/actions/permissions": {"enabled": True},
        f"repos/{FORK}/branches/main/protection": {
            "required_status_checks": {"strict": False, "checks": PINNED},
            "enforce_admins": {"enabled": False},
        },
        f"repos/{FORK}/actions/secrets": {
            "secrets": [{"name": n} for n in ("APP_ID", "APP_PRIVATE_KEY", "ANTHROPIC_API_KEY")]
        },
        f"repos/{FORK}/actions/workflows/upstream-sync.yml/runs?per_page=1": {
            "workflow_runs": [{"conclusion": "success"}]
        },
    }
    for path, content in files.items():
        answers[f"repos/{FORK}/contents/{path}?ref=main"] = content.decode()
    return answers


def test_check_reports_an_onboarded_fork_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _invoke(monkeypatch, FakeRunner(_onboarded()), "check", FORK)
    assert result.exit_code == 0, result.output
    assert "missing" not in result.output
    assert "human    change-manager inert landing policy" in result.output
    assert "ok       tool_installer row -- rea" in result.output


def test_check_reports_a_fork_with_no_installer_row(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fork_sync.checks.TOOLS", ())
    result = _invoke(monkeypatch, FakeRunner(_onboarded()), "check", FORK)
    assert result.exit_code == 0, result.output
    assert "info     tool_installer row -- not an installed tool" in result.output


@pytest.mark.parametrize(
    ("change", "step"),
    [
        (lambda a: a.update({f"repos/{FORK}/branches/main/protection": NOT_FOUND}), "protection"),
        (
            # Names alone: any app could post a status called `security review`.
            lambda a: a[f"repos/{FORK}/branches/main/protection"].update(
                {"required_status_checks": {"strict": False, "contexts": list(REQUIRED_CHECKS)}}
            ),
            "protection",
        ),
        (
            lambda a: a.update(
                {f"repos/{FORK}/contents/.github/workflows/ci.yml?ref=main": "edited"}
            ),
            ".github/workflows/ci.yml on main -- differs",
        ),
        (
            lambda a: a[f"repos/{FORK}/contents/.github/workflows?ref=main"].append(
                {"name": "cd.yml"}
            ),
            "upstream workflows absent from main -- cd.yml",
        ),
        (lambda a: a[f"repos/{FORK}"].update({"delete_branch_on_merge": False}), "settings"),
        (lambda a: a[f"repos/{FORK}/actions/secrets"]["secrets"].pop(), "ANTHROPIC_API_KEY"),
        (
            lambda a: a.update(
                {f"repos/{FORK}/contents/.github/fork-sync.toml?ref=main": CONFIG + "x = 1\n"}
            ),
            "invalid: unknown key",
        ),
    ],
)
def test_check_names_what_is_missing(
    monkeypatch: pytest.MonkeyPatch, change: Any, step: str
) -> None:
    answers = _onboarded()
    change(answers)
    result = _invoke(monkeypatch, FakeRunner(answers), "check", FORK)
    assert result.exit_code == 1
    missing = [line for line in result.output.splitlines() if line.startswith("missing")]
    assert len(missing) == 1 and step in missing[0], result.output


def test_check_reads_the_policy_when_given_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli.estate,
        "read_inert_landing",
        lambda token: {
            "repositories": [FORK.lower()],
            "permitted_authors": ["fork-secure-upstream-sync[bot]"],
            "non_ecosystem_authors": [],
        },
    )
    monkeypatch.setattr(cli, "run_command", FakeRunner(_onboarded()))
    monkeypatch.setenv("FORK_SYNC_CHANGE_MANAGER_TOKEN", "t")
    result = CliRunner().invoke(cli.app, ["check", FORK])
    assert result.exit_code == 1
    assert "not in non_ecosystem_authors" in result.output


def test_scaffold_opens_a_pull_request(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = tmp_path / "fork-sync.toml"
    config.write_text(CONFIG)
    runner = FakeRunner(
        {
            f"repos/{FORK}": {"fork": True, "parent": {"full_name": UPSTREAM}},
            "gh pr create": f"https://github.com/{FORK}/pull/12\n",
        }
    )
    result = _invoke(monkeypatch, runner, "scaffold", FORK, "--config", str(config))
    assert result.exit_code == 0, result.output
    assert "opened pull request #12" in result.output
    [add] = [argv for argv, _ in runner.calls if argv[:2] == ["git", "add"]]
    assert set(add[2:]) == {".github/fork-sync.toml", ACKS_PATH, *render("npm")}
    assert fork_sync_review.parse_acks(EMPTY_ACKS.decode()) == {}


def test_scaffold_refuses_a_declaration_for_another_upstream(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = tmp_path / "fork-sync.toml"
    config.write_text(CONFIG.replace(UPSTREAM, "someone/else"))
    runner = FakeRunner({f"repos/{FORK}": {"fork": True, "parent": {"full_name": UPSTREAM}}})
    result = _invoke(monkeypatch, runner, "scaffold", FORK, "--config", str(config))
    assert result.exit_code == 1
    assert not runner.ran("git")


def _baseline_runner(stranded: str) -> FakeRunner:
    return FakeRunner(
        {
            f"repos/{FORK}": {"fork": True, "parent": {"full_name": UPSTREAM}},
            "git log --oneline": stranded,
            "git rev-parse origin/main": "old\n",
        }
    )


def test_baseline_needs_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _baseline_runner("")
    result = _invoke(monkeypatch, runner, "baseline", FORK, "--tag", "rea-agents-6.7.0")
    assert result.exit_code == 1
    assert runner.calls == []


def test_baseline_refuses_to_lose_fork_commits(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _baseline_runner("abc123 fork-only work\n")
    result = _invoke(
        monkeypatch, runner, "baseline", FORK, "--tag", "rea-agents-6.7.0", "--confirm"
    )
    assert result.exit_code == 1
    assert "abc123 fork-only work" in result.output
    assert not runner.ran("git push")


def test_baseline_resets_main_with_a_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _baseline_runner("")
    result = _invoke(
        monkeypatch, runner, "baseline", FORK, "--tag", "rea-agents-6.7.0", "--confirm"
    )
    assert result.exit_code == 0, result.output
    assert runner.ran("git checkout --quiet -B main refs/upstream-tags/rea-agents-6.7.0")
    assert runner.ran("git rm -r -q --ignore-unmatch .github")
    assert not runner.ran("git rm -r -q --ignore-unmatch .github/workflows")
    assert runner.ran(
        "git commit --quiet --allow-empty -m fork-sync baseline: rea-agents-6.7.0, "
        "upstream .github removed"
    )
    assert runner.ran("git push --quiet --force-with-lease=main:old origin HEAD:main")


def _protect_runner(job_names: list[str]) -> FakeRunner:
    return FakeRunner(
        {
            f"repos/{FORK}/pulls?state=open&per_page=100": [{"number": 4, "head": {"sha": "s1"}}],
            f"repos/{FORK}/actions/runs?head_sha=s1&per_page=100": {"workflow_runs": [{"id": 9}]},
            f"repos/{FORK}/actions/runs/9/jobs": {"jobs": [{"name": n} for n in job_names]},
        }
    )


def test_protect_refuses_until_both_checks_have_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _protect_runner(["build and test"])
    result = _invoke(monkeypatch, runner, "protect", FORK)
    assert result.exit_code == 1
    assert not runner.ran("gh api -X")


def test_protect_applies_settings_and_protection(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _protect_runner(list(REQUIRED_CHECKS))
    result = _invoke(monkeypatch, runner, "protect", FORK)
    assert result.exit_code == 0, result.output
    writes = {argv[4]: json.loads(stdin or "{}") for argv, stdin in runner.calls if "-X" in argv}
    assert writes[f"repos/{FORK}"]["squash_merge_commit_message"] == "COMMIT_MESSAGES"
    protection = writes[f"repos/{FORK}/branches/main/protection"]
    assert protection["required_status_checks"] == {"strict": False, "checks": PINNED}
    assert protection["enforce_admins"] is False
    assert protection["required_pull_request_reviews"] is None


def test_human_steps_names_the_app_and_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _invoke(monkeypatch, FakeRunner({}), "human-steps", FORK)
    assert "settings/apps/fork-secure-upstream-sync/installations" in result.output
    assert "998b817e-6164-4b9f-9c52-b4e800e8270d" in result.output
    assert "never `--body -`" in result.output


def test_the_rendered_files_are_the_lane_contract() -> None:
    for ecosystem in ("cargo", "npm"):
        files = render(ecosystem)
        ci = files[".github/workflows/ci.yml"].decode()
        for name in REQUIRED_CHECKS:
            assert f"name: {name}\n" in ci
        assert "paths" not in ci  # a path filter would leave a required check pending forever
        sync = files[".github/workflows/upstream-sync.yml"].decode()
        # Upstream's tag reaches shell only through env, never interpolated into a script.
        run_blocks = sync.split("run: |")[1:]
        assert all("${{" not in block.split("\n      - ")[0] for block in run_blocks)
    assert "npm ci --ignore-scripts" in render("npm")[".github/workflows/ci.yml"].decode()
    script = Path(fork_sync_review.__file__).read_bytes()
    assert render("cargo")[".github/scripts/fork_sync_review.py"] == script


def _jobs(ecosystem: str) -> dict[str, Any]:
    return yaml.safe_load(render(ecosystem)[".github/workflows/ci.yml"])["jobs"]


@pytest.mark.parametrize("ecosystem", ["cargo", "npm"])
def test_the_review_job_never_checks_out_the_head(ecosystem: str) -> None:
    jobs = _jobs(ecosystem)
    review_steps = jobs["security-review"]["steps"]
    checkouts = [s for s in review_steps if s.get("uses", "").startswith("actions/checkout")]
    assert [s["with"] for s in checkouts] == [
        {
            "ref": "${{ github.event.pull_request.base.sha }}",
            "fetch-depth": 0,
            "persist-credentials": False,
        }
    ]
    runs = "\n".join(s.get("run", "") for s in review_steps)
    assert 'git fetch --no-tags origin "$HEAD_SHA"' in runs
    assert "checkout" not in runs and "HEAD_SHA" not in runs.split("git fetch")[0]
    assert jobs["security-review"]["env"] == {"GIT_NO_REPLACE_OBJECTS": "1"}
    build = jobs["build-and-test"]["steps"][0]
    assert build["with"] == {"persist-credentials": False}


def test_cargo_audit_is_pinned_and_installed_outside_the_checkout() -> None:
    [install] = [
        s
        for s in _jobs("cargo")["security-review"]["steps"]
        if s.get("name") == "Install cargo-audit"
    ]
    assert install["run"] == "cargo install cargo-audit --locked --version 0.22.2"
    assert install["working-directory"] == "${{ runner.temp }}"


def test_the_sync_keeps_the_forks_whole_github_and_fences_nothing() -> None:
    sync = render("npm")[".github/workflows/upstream-sync.yml"].decode()
    assert "rm -rf .github\n" in sync and "git checkout origin/main -- .github/\n" in sync
    # Commit subjects are upstream's text: an indented block has no fence for one to close.
    body = sync.split("Open or update the pull request")[1]
    assert "```" not in body and "| sed 's/^/    /'" in body
