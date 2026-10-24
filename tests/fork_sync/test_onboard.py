"""`fork-sync onboard` through its real entrypoint and its step functions, against a fake estate.

`World` models what the steps read and change: the fork (its main, the scaffold and sync pull
requests, their CI runs, its secrets and protection), factory-runner's registry, and
change-manager's policy and served revision. Writes move that state the way GitHub would, so a
run is shown stopping where a person is needed and a rerun shown continuing from there. Every
argv and stdin is recorded, so no test needs to trust a step's own account of what it did.
"""

from __future__ import annotations

import base64
import json
import sys
import tomllib
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from fork_sync import bws, cli, onboarding
from fork_sync.fork_sync_review import ACKS_PATH, CONFIG_PATH, Finding, Report
from fork_sync.gh import CommandFailed
from fork_sync.onboard import EMPTY_ACKS, SCAFFOLD_BRANCH, SYNC_AUTHOR, render

FORK = "AlobarQuest/rea"
UPSTREAM = "morluto/rea"
TAG = "rea-agents-6.6.0"
CONFIG = f"""upstream = "{UPSTREAM}"
ecosystem = "npm"
release_tag_pattern = '^rea-agents-(\\d+\\.\\d+\\.\\d+)$'
"""
LOCKED_CONFIG = CONFIG + "generate_lockfile = true\n"
SCAFFOLD_SHA = "c" * 40
LOCKED_SHA = "d" * 40
SYNC_SHA = "5" * 40
REGISTRY_SHA = "e" * 40
POLICY_SHA = "f" * 40
PEM = "-----BEGIN RSA PRIVATE KEY-----\nMIIEfake\n-----END RSA PRIVATE KEY-----\n"
ANTHROPIC = "sk-ant-api03-not-a-real-key"
VALUES = {
    bws.APP_PRIVATE_KEY_UUID: base64.b64encode(PEM.encode()).decode(),
    bws.ANTHROPIC_API_KEY_UUID: ANTHROPIC,
}
SCAFFOLD_MERGED_AT = "2026-10-24T10:00:00Z"
DECLARED_AT = "2026-10-24T09:30:00Z"
"""When the scaffold commit itself was made: before its pull request merged it into main."""
DECLARED_SHA = "a" * 40
SYNC_RUN = {
    "status": "completed",
    "conclusion": "success",
    "id": 70,
    "created_at": "2026-10-24T11:00:00Z",
}
FINDING = Finding("binary", "x.node", "binary added", "d")
CI = ".github/workflows/ci.yml"
REGISTRY = """# The fork-sync lane's credentials.
version = 1

[[credential]]
id = "factory-pr-token"
class = "github-pat"

  [[credential.consumer]]
  kind = "gh-actions-secret"
  repo = "AlobarQuest/orchestrator"
  name = "FACTORY_PR_TOKEN"

# The private key of GitHub App fork-secure-upstream-sync.
[[credential]]
id = "fork-sync-app-private-key"
class = "github-app-private-key"

  [[credential.consumer]]
  kind = "gh-actions-secret"
  repo = "AlobarQuest/rtk"
  name = "APP_PRIVATE_KEY"

# INCOMPLETE BY DESIGN: only the fork-sync lane's copies.
[[credential]]
id = "anthropic-api-key"
class = "anthropic-api-key"

  [[credential.consumer]]
  kind = "gh-actions-secret"
  repo = "AlobarQuest/rtk"
  name = "ANTHROPIC_API_KEY"
"""


def _job(name: str, conclusion: str) -> dict[str, Any]:
    return {"name": name, "status": "completed", "conclusion": conclusion}


def _ci(review: str) -> list[dict[str, Any]]:
    """A fork's CI run: `build and test` passes, and `security review` concludes `review`."""
    return [_job("build and test", "success"), _job("security review", review)]


class World:
    """The estate one onboarding touches, in a fresh fork's state unless told otherwise."""

    def __init__(self, *, installed: bool = True, config: str = CONFIG) -> None:
        self.installed = installed
        self.config = config
        self.main: dict[str, str] = {"package.json": "{}"}
        self.main_workflows = {"ci.yml", "cd.yml"}  # upstream's, before a baseline
        self.settings: dict[str, Any] = {"allow_merge_commit": False, "has_issues": False}
        self.protection: dict[str, Any] | None = None
        self.secrets: set[str] = set()
        self.branches: dict[str, dict[str, str]] = {}
        self.pulls: dict[tuple[str, int], dict[str, Any]] = {}
        self.runs: dict[str, dict[str, Any]] = {}
        self.sync_runs: list[dict[str, Any]] = []
        self.review: list[Finding] = [FINDING]
        self.registry = REGISTRY
        self.registry_checks = "success"
        self.policy_checks = "success"
        self.policy: dict[str, Any] = {
            "version": 12,
            "inert_landing": {
                "repositories": ["alobarquest/rtk"],
                "permitted_authors": [SYNC_AUTHOR],
                "non_ecosystem_authors": [SYNC_AUTHOR],
            },
        }
        self.revision = "0" * 40
        self.release_exit = 0
        self.release_stdout: str | None = None
        # change-manager's source version when it is ahead of the served one (merged, undeployed).
        self.source_version: int | None = None
        # What the release script changes in change-manager, as git status and the pull request
        # both report it.
        self.policy_changes = ["app/deploy_policy.py", "app/deploy_policy_history.py"]
        self.landing: str | None = None
        self.calls: list[tuple[list[str], str | None]] = []
        self.clones: dict[Path, str] = {}

    # ---- the states a fork reaches -------------------------------------------------------------

    def onboarded(self) -> World:
        """Every step done: what a finished onboarding leaves."""
        self.main.update({path: content.decode() for path, content in render("npm").items()})
        self.main[CONFIG_PATH] = self.config
        self.main[ACKS_PATH] = EMPTY_ACKS.decode()
        self.main_workflows = {"ci.yml", "upstream-sync.yml", "fork-sync-audit.yml"}
        self.settings = {
            "allow_merge_commit": True,
            "squash_merge_commit_message": "COMMIT_MESSAGES",
            "delete_branch_on_merge": True,
            "has_issues": True,
        }
        self.protection = {
            "required_status_checks": {
                "strict": False,
                "checks": [
                    {"context": "build and test", "app_id": 15368},
                    {"context": "security review", "app_id": 15368},
                ],
            },
            "enforce_admins": {"enabled": False},
        }
        self.secrets = {"APP_ID", "APP_PRIVATE_KEY", "ANTHROPIC_API_KEY"}
        self.registry = registered_registry()
        self.sync_runs = [SYNC_RUN]
        self.pulls[(FORK, 12)] = {
            "number": 12,
            "branch": SCAFFOLD_BRANCH,
            "head": {"sha": SCAFFOLD_SHA},
            "state": "closed",
            "merged": True,
            "merged_at": SCAFFOLD_MERGED_AT,
        }
        self.policy["version"] = 13
        self.policy["inert_landing"]["repositories"].append(FORK.lower())
        self.landing = "inert"
        return self

    # ---- the estate's reads outside gh ---------------------------------------------------------

    def estate(self) -> onboarding.Estate:
        def landing(repository: str) -> str:
            assert self.landing is not None
            return self.landing

        return onboarding.Estate(
            secret=VALUES.__getitem__,
            installed=lambda fork: self.installed,
            policy=lambda: json.loads(json.dumps(self.policy)),
            revision=lambda: self.revision,
            landing=landing if self.landing is not None else None,
        )

    # ---- gh and git ----------------------------------------------------------------------------

    def __call__(self, argv: Sequence[str], stdin: str | None, cwd: Path | None) -> str:
        argv = list(argv)
        self.calls.append((argv, stdin))
        if argv[:2] == ["gh", "api"]:
            if argv[2] == "-X":
                return self._write(argv[3], argv[4], json.loads(stdin or "{}"))
            answer = self._get(argv[-1])
            if answer is None:
                raise CommandFailed(argv, 1, "gh: Not Found (HTTP 404)")
            return answer if isinstance(answer, str) else json.dumps(answer)
        if argv[:2] == ["git", "clone"]:
            return self._clone(argv)
        if argv[:2] == ["git", "push"]:
            return self._push(argv, cwd)
        if argv[:3] == ["git", "rev-parse", "HEAD"]:
            repo = self.clones.get(cwd) if cwd else None
            return {"AlobarQuest/factory-runner": REGISTRY_SHA}.get(
                repo or "", POLICY_SHA if repo == "AlobarQuest/change-manager" else "b" * 40
            )
        if argv[:3] == ["git", "rev-parse", "origin/main"]:
            return "old\n"
        if argv == ["git", "status", "--porcelain"]:
            return "".join(f" M {path}\n" for path in self.policy_changes)
        if argv[:2] == [sys.executable, ".github/scripts/fork_sync_review.py"] and cwd:
            (cwd / "package-lock.json").write_text('{"lockfileVersion": 3}')
            return ""
        if argv[1:3] == ["run", "python"]:
            if self.release_exit:
                raise CommandFailed(argv, self.release_exit, "release refused")
            if self.release_stdout is not None:
                return self.release_stdout
            line = {"status": "released", "version": self.source() + 1}
            return json.dumps({**line, "repository": FORK.lower()}) + "\n"
        if argv[:3] == ["gh", "secret", "list"]:
            return json.dumps([{"name": name} for name in sorted(self.secrets)])
        if argv[:3] == ["gh", "secret", "set"]:
            self.secrets.add(argv[3])
            return ""
        if argv[:3] == ["gh", "pr", "create"]:
            return self._open(argv)
        if argv[:3] == ["gh", "pr", "merge"]:
            return self._merge(argv)
        if argv[:3] == ["gh", "workflow", "run"]:
            self._sync()
            return ""
        return ""

    def source(self) -> int:
        """The version in force in change-manager's source on main."""
        return self.policy["version"] if self.source_version is None else self.source_version

    def _clone(self, argv: list[str]) -> str:
        target = Path(argv[-1])
        target.mkdir(parents=True)
        repo = argv[-2].removeprefix("https://github.com/").removesuffix(".git")
        self.clones[target] = repo
        files: dict[str, str] = {}
        if repo == FORK:
            branch = argv[argv.index("--branch") + 1] if "--branch" in argv else None
            files = self.branches[branch] if branch else self.main
        elif repo == "AlobarQuest/factory-runner":
            files = {".cred-consumers.toml": self.registry}
        elif repo == "AlobarQuest/change-manager":
            files = {"app/deploy_policy.py": f"CURRENT_VERSION: Final = {self.source()}\n"}
        for path, content in files.items():
            (target / path).parent.mkdir(parents=True, exist_ok=True)
            (target / path).write_text(content)
        return ""

    def _push(self, argv: list[str], cwd: Path | None) -> str:
        assert cwd is not None
        repo, branch = self.clones[cwd], argv[-1]
        if branch == "HEAD:main":  # a baseline: upstream's .github is gone
            self.main_workflows = set()
            return ""
        tree = {
            str(path.relative_to(cwd)): path.read_text()
            for path in cwd.rglob("*")
            if path.is_file() and ".git" not in path.parts
        }
        self.branches[branch] = tree
        if repo == FORK and branch == SCAFFOLD_BRANCH and (FORK, 12) in self.pulls:
            self._head(12, LOCKED_SHA)
        return ""

    def _head(self, number: int, sha: str) -> None:
        self.pulls[(FORK, number)]["head"]["sha"] = sha
        self.runs[sha] = {"path": CI, "jobs": _ci("failure")}

    def _open(self, argv: list[str]) -> str:
        repo = argv[argv.index("--repo") + 1]
        branch = argv[argv.index("--head") + 1]
        number, sha, checks = {
            FORK: (12, SCAFFOLD_SHA, None),
            "AlobarQuest/factory-runner": (104, REGISTRY_SHA, ("Quality", self.registry_checks)),
            "AlobarQuest/change-manager": (
                132,
                POLICY_SHA,
                ("Lint, type-check, and test", self.policy_checks),
            ),
        }[repo]
        self.pulls[(repo, number)] = {
            "number": number,
            "branch": branch,
            "head": {"sha": sha, "ref": branch, "repo": {"full_name": repo}},
            "state": "open",
            "merged": False,
        }
        if checks is None:
            self._head(number, sha)
        else:
            self.runs[sha] = {"path": ".github/workflows/x.yml", "jobs": [_job(*checks)]}
        return f"https://github.com/{repo}/pull/{number}\n"

    def _merge(self, argv: list[str]) -> str:
        repo, number = argv[argv.index("--repo") + 1], int(argv[3])
        pull = self.pulls[(repo, number)]
        pull.update(state="closed", merged=True, merge_commit_sha=f"{number:040d}")
        pull["merged_at"] = SCAFFOLD_MERGED_AT
        if repo == FORK:
            self.main.update(self.branches[pull["branch"]])
            self.main_workflows = {"ci.yml", "upstream-sync.yml", "fork-sync-audit.yml"}
        elif repo == "AlobarQuest/factory-runner":
            self.registry = self.branches[pull["branch"]][".cred-consumers.toml"]
        else:  # change-manager deploys the merge, then serves the version it adds
            self.revision = pull["merge_commit_sha"]
            self.policy["version"] = self.source() + 1
            self.source_version = None
            self.policy["inert_landing"]["repositories"].append(FORK.lower())
        return ""

    def _sync(self) -> None:
        self.sync_runs.insert(0, SYNC_RUN)
        self.pulls[(FORK, 13)] = {
            "number": 13,
            "branch": "upstream-sync",
            "title": "upstream-sync: rea-agents-6.7.0",
            "head": {"sha": SYNC_SHA, "ref": "upstream-sync", "repo": {"full_name": FORK}},
            "state": "open",
            "merged": False,
        }
        self.held(bool(self.review))

    def held(self, holds: bool) -> None:
        """The sync pull request's review: holding on `FINDING`, or passing."""
        self.review = [FINDING] if holds else []
        verdict = "failure" if holds else "success"
        self.runs[SYNC_SHA] = {"path": CI, "jobs": _ci(verdict)}

    def _write(self, method: str, path: str, body: dict[str, Any]) -> str:
        if method == "PATCH" and path == f"repos/{FORK}":
            self.settings.update(body)
        elif method == "PUT" and path == f"repos/{FORK}/branches/main/protection":
            checks = body["required_status_checks"]
            self.protection = {
                "required_status_checks": checks,
                "enforce_admins": {"enabled": body["enforce_admins"]},
            }
        return ""

    def _open_pulls(self, repo: str, branch: str | None = None) -> list[dict[str, Any]]:
        return [
            pull
            for (r, _), pull in self.pulls.items()
            if r == repo and pull["state"] == "open" and branch in (None, pull["branch"])
        ]

    def _get(self, path: str) -> Any:
        fork, base, query = f"repos/{FORK}", path.split("?")[0], path.partition("?")[2]
        ref = query.removeprefix("ref=") if query.startswith("ref=") else None
        if path == fork:
            return {
                "fork": True,
                "parent": {"full_name": UPSTREAM},
                "full_name": FORK,
                **self.settings,
            }
        if base == f"{fork}/contents/.github/workflows":
            return [{"name": name} for name in sorted(self.main_workflows)]
        if path == f"repos/{UPSTREAM}/contents/.github/workflows":
            return [{"name": "ci.yml"}, {"name": "cd.yml"}]
        if base.startswith(f"{fork}/contents/") and ref is not None:
            name = base.removeprefix(f"{fork}/contents/")
            files = self.main if ref == "main" else self.branches.get(ref)
            if files is None:  # a commit: the scaffold branch is the only one with a pull head
                files = self.branches.get(SCAFFOLD_BRANCH, {})
            return files.get(name)
        if path == f"{fork}/branches/main/protection":
            return self.protection
        if path == f"{fork}/actions/permissions":
            return {"enabled": True}
        if path == f"{fork}/actions/secrets":
            return {"secrets": [{"name": name} for name in sorted(self.secrets)]}
        if path == f"{fork}/actions/workflows/upstream-sync.yml/runs?per_page=1":
            return {"workflow_runs": self.sync_runs[:1]}
        if path.startswith(f"{fork}/actions/workflows/upstream-sync.yml/runs?per_page=100&page="):
            return {"workflow_runs": self.sync_runs if path.endswith("page=1") else []}
        if path.startswith(f"{fork}/commits?sha=main&path={CONFIG_PATH}&per_page=100&page="):
            declared = CONFIG_PATH in self.main and path.endswith("page=1")
            commit = {"sha": DECLARED_SHA, "commit": {"committer": {"date": DECLARED_AT}}}
            return [commit] if declared else []
        if path == f"{fork}/commits/{DECLARED_SHA}/pulls":
            return [p for (r, _), p in self.pulls.items() if r == FORK and p.get("merged_at")]
        if path == "repos/AlobarQuest/change-manager/contents/app/deploy_policy.py?ref=main":
            return f"CURRENT_VERSION: Final = {self.source()}\n"
        if (
            path
            == f"repos/AlobarQuest/change-manager/contents/app/deploy_policy.py?ref={POLICY_SHA}"
        ):
            return f"CURRENT_VERSION: Final = {self.source() + 1}\n"
        if path == f"{fork}/pulls?state=open&per_page=100":
            return self._open_pulls(FORK)
        if path == f"{fork}/issues/13/comments?per_page=100":
            body = Report(SYNC_SHA, self.review).markdown({})
            return [{"user": {"login": "github-actions[bot]", "type": "Bot"}, "body": body}]
        if path == "repos/AlobarQuest/factory-runner/contents/.cred-consumers.toml?ref=main":
            return self.registry
        for repo, context in (
            ("AlobarQuest/factory-runner", "Quality"),
            ("AlobarQuest/change-manager", "Lint, type-check, and test"),
            (FORK, None),
        ):
            if path.startswith(f"repos/{repo}/pulls?state=open&head="):
                return self._open_pulls(repo, path.split(":", 1)[1])
            if path.startswith(f"repos/{repo}/pulls?state=closed&head="):
                branch = path.split(":", 1)[1].split("&")[0]
                return [
                    pull
                    for (r, _), pull in self.pulls.items()
                    if r == repo and pull["state"] == "closed" and pull["branch"] == branch
                ]
            if path == f"repos/{repo}/branches/main/protection" and context:
                return {"required_status_checks": {"checks": [{"context": context}]}}
            if path.startswith(f"repos/{repo}/actions/runs?head_sha="):
                sha = path.split("=")[1].split("&")[0]
                if sha not in self.runs:
                    return {"workflow_runs": []}
                run = {"id": 1000 + list(self.runs).index(sha), "path": self.runs[sha]["path"]}
                return {"workflow_runs": [{**run, "status": "completed", "created_at": "t"}]}
            if path.startswith(f"repos/{repo}/actions/runs/") and path.endswith("/jobs"):
                sha = list(self.runs)[int(path.split("/")[-2]) - 1000]
                jobs = self.runs[sha]["jobs"]
                return {"jobs": [{**job, "id": 1 + i} for i, job in enumerate(jobs)]}
            if path.startswith(f"repos/{repo}/pulls/") and path.endswith("/files?per_page=100"):
                if repo == "AlobarQuest/change-manager":
                    return [
                        {"filename": name, "status": "modified"} for name in self.policy_changes
                    ]
                files = self.branches.get(SCAFFOLD_BRANCH, {})
                changed = [name for name in files if self.main.get(name) != files[name]]
                return [{"filename": name, "status": "added"} for name in changed]
            if path.startswith(f"repos/{repo}/pulls/"):
                return self.pulls.get((repo, int(path.split("/")[-1])))
        return None

    # ---- what the tests read -------------------------------------------------------------------

    def writes(self) -> list[str]:
        """Every call that changes something: neither a read through `gh api` nor a clone."""
        reads = (["gh", "api"], ["gh", "secret", "list"], ["git", "clone"])
        return [
            " ".join(argv)
            for argv, _ in self.calls
            if not any(argv[: len(r)] == r for r in reads) or "-X" in argv
        ]

    def ran(self, prefix: str) -> bool:
        return any(" ".join(argv).startswith(prefix) for argv, _ in self.calls)


def registered_registry() -> str:
    missing = onboarding.missing_registrations(REGISTRY, FORK)
    return onboarding.registered(REGISTRY, FORK, missing)


@pytest.fixture
def waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """A clock that moves only when a step sleeps, failing a wait that outlives twice the
    deadline instead of hanging the suite."""
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        if sum(slept) > 2 * onboarding.DEADLINE_SECONDS:
            raise AssertionError("waited past the deadline")
        slept.append(seconds)

    monkeypatch.setattr(onboarding, "sleep", sleep)
    monkeypatch.setattr(onboarding, "monotonic", lambda: sum(slept))
    return slept


def _onboard(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, world: World, *extra: str) -> Any:
    config = tmp_path / "fork-sync.toml"
    config.write_text(world.config)
    monkeypatch.setattr(cli, "run_command", world)
    monkeypatch.setattr(cli, "onboarding_estate", world.estate)
    return CliRunner().invoke(cli.app, ["onboard", FORK, "--config", str(config), *extra])


def _statuses(output: str) -> list[tuple[str, str]]:
    known = (onboarding.DONE, onboarding.DID, onboarding.WAITING, onboarding.NEEDS_YOU)
    lines = [line for line in output.splitlines() if not line.startswith(" ")]
    return [
        (status, line[10:])
        for line in lines
        for status in known
        if line.startswith(f"{status} ") or line.startswith(f"{status:9} ")
    ]


def _context(world: World, tmp_path: Path, **changes: Any) -> onboarding.Context:
    config = tmp_path / "fork-sync.toml"
    config.write_text(world.config)
    from fork_sync.fork_sync_review import parse_config

    fields: dict[str, Any] = {
        "run": world,
        "fork": FORK,
        "upstream": UPSTREAM,
        "text": world.config,
        "config": parse_config(world.config),
        "estate": world.estate(),
        "config_path": config,
        "baseline_tag": None,
        "discard_fork_commits": False,
        "reset_secrets": False,
        "today": date(2026, 10, 24),
    }
    return onboarding.Context(**(fields | changes))


# ---- the whole run -----------------------------------------------------------------------------


def test_a_fork_with_nothing_done_stops_at_the_app_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    world = World(installed=False)
    result = _onboard(monkeypatch, tmp_path, world, "--baseline-tag", TAG)
    assert result.exit_code == 1, result.output
    assert _statuses(result.output) == [("needs you", "App installed on the fork")]
    assert "settings/apps/fork-secure-upstream-sync/installations" in result.output
    assert bws.APP_PRIVATE_KEY_UUID in result.output
    assert world.writes() == []


def test_with_the_app_installed_it_runs_through_to_the_first_review(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, waits: list[float]
) -> None:
    world = World()
    result = _onboard(monkeypatch, tmp_path, world, "--baseline-tag", TAG)
    assert result.exit_code == 1, result.output
    assert _statuses(result.output) == [
        ("done", "App installed on the fork"),
        ("did now", "baseline main on an upstream tag"),
        ("did now", "scaffold pull request"),
        ("did now", "Actions secrets"),
        ("did now", "registered in AlobarQuest/factory-runner's .cred-consumers.toml"),
        ("did now", "scaffold merged"),
        ("did now", "first sync"),
        ("did now", "main protected"),
        ("needs you", "first review"),
    ]
    assert "fork-sync ack AlobarQuest/rea 13 --by '<who>' --reason '<why>'" in result.output
    # It never approves: nothing touches the acknowledgements or merges the held sync.
    assert "--yes" not in result.output
    assert not world.ran("gh pr merge 13")
    assert ACKS_PATH not in world.branches.get("fork-sync-ack-13-555555555555", {})
    assert world.main[ACKS_PATH] == EMPTY_ACKS.decode()
    assert world.ran("git push --quiet --force-with-lease=main:old origin HEAD:main")
    assert world.ran("gh pr merge 12 --repo AlobarQuest/rea --merge --admin")
    assert world.ran("gh pr merge 104 --repo AlobarQuest/factory-runner --squash")
    assert world.registry == registered_registry()
    assert not world.ran("gh pr create --repo AlobarQuest/change-manager")
    assert world.protection is not None and world.settings["has_issues"] is True


def test_rerunning_after_the_review_is_acknowledged_continues_to_the_policy_and_app_brain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, waits: list[float]
) -> None:
    world = World()
    _onboard(monkeypatch, tmp_path, world, "--baseline-tag", TAG)
    world.held(False)  # acknowledged, re-synced, and the review now passes
    world.calls.clear()

    result = _onboard(monkeypatch, tmp_path, world)
    assert result.exit_code == 1, result.output
    assert _statuses(result.output)[-3:] == [
        ("done", "first review"),
        ("did now", "change-manager inert landing policy"),
        ("needs you", "App Brain landing record"),
    ]
    assert all(status == "done" for status, _ in _statuses(result.output)[:-2])
    assert world.ran("gh pr merge 132 --repo AlobarQuest/change-manager --squash")
    [release] = [argv for argv, _ in world.calls if argv[1:3] == ["run", "python"]]
    assert release[3:6] == ["scripts/release_inert_repository.py", "--repository", FORK.lower()]
    assert "AlobarQuest/rea" in release[-1] and "fork-sync onboard" in release[-1]
    assert world.policy["version"] == 13 and world.revision == f"{132:040d}"
    assert 'record_default_branch_landing(github_repo="AlobarQuest/rea", landing="inert"' in (
        result.output
    )

    world.landing = "inert"
    world.calls.clear()
    finished = _onboard(monkeypatch, tmp_path, world)
    assert finished.exit_code == 0, finished.output
    assert all(status == "done" for status, _ in _statuses(finished.output))
    assert "missing" not in finished.output
    assert world.writes() == []


def test_a_fully_onboarded_fork_is_left_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, waits: list[float]
) -> None:
    """Resumability: every step reads done and changes nothing. Even --baseline-tag, which
    force-pushes main, does nothing to a fork that has its declaration."""
    world = World().onboarded()
    result = _onboard(monkeypatch, tmp_path, world, "--baseline-tag", TAG)
    assert result.exit_code == 0, result.output
    statuses = _statuses(result.output)
    assert len(statuses) == 13 and all(status == "done" for status, _ in statuses)
    assert world.writes() == []
    assert not world.ran("git clone")


def test_an_unreadable_declaration_refuses_before_anything(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    world = World()
    world.config = CONFIG.replace(UPSTREAM, "someone/else")
    result = _onboard(monkeypatch, tmp_path, world)
    assert result.exit_code == 1
    assert "onboard refused" in result.output
    assert world.writes() == []


# ---- steps -------------------------------------------------------------------------------------


def test_a_baseline_waits_for_its_tag(tmp_path: Path) -> None:
    world = World()
    outcome = onboarding.baseline(_context(world, tmp_path))
    assert outcome.status == onboarding.NEEDS_YOU and "--baseline-tag" in outcome.detail
    assert world.writes() == []


def test_a_baseline_refuses_a_protected_main(tmp_path: Path) -> None:
    world = World()
    world.protection = {"required_status_checks": None}
    outcome = onboarding.baseline(_context(world, tmp_path, baseline_tag=TAG))
    assert outcome.status == onboarding.NEEDS_YOU and "is protected" in outcome.detail
    assert world.writes() == []


def test_the_secrets_go_on_stdin_and_never_on_argv(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    world = World()
    world.secrets = {"APP_ID"}  # already set: skipped
    outcome = onboarding.secrets(_context(world, tmp_path))
    assert outcome.status == onboarding.DID
    sets = [(argv, stdin) for argv, stdin in world.calls if argv[:3] == ["gh", "secret", "set"]]
    assert sets == [
        (["gh", "secret", "set", "APP_PRIVATE_KEY", "--repo", FORK], PEM),
        (["gh", "secret", "set", "ANTHROPIC_API_KEY", "--repo", FORK], ANTHROPIC),
    ]
    every_argv = " ".join(" ".join(argv) for argv, _ in world.calls)
    for value in (PEM, ANTHROPIC, VALUES[bws.APP_PRIVATE_KEY_UUID], "MIIEfake"):
        assert value not in every_argv
        assert value not in outcome.detail
    assert "--body" not in every_argv
    assert "MIIEfake" not in capsys.readouterr().out


def test_the_secrets_are_all_skipped_when_present_unless_reset(tmp_path: Path) -> None:
    world = World()
    world.secrets = {"APP_ID", "APP_PRIVATE_KEY", "ANTHROPIC_API_KEY"}
    assert onboarding.secrets(_context(world, tmp_path)).status == onboarding.DONE
    assert not world.ran("gh secret set")
    onboarding.secrets(_context(world, tmp_path, reset_secrets=True))
    sets = [argv[3] for argv, _ in world.calls if argv[:3] == ["gh", "secret", "set"]]
    assert sets == ["APP_ID", "APP_PRIVATE_KEY", "ANTHROPIC_API_KEY"]
    assert [stdin for argv, stdin in world.calls if argv[3:4] == ["APP_ID"]] == ["4094707"]


def test_a_bws_failure_sets_no_secret(tmp_path: Path) -> None:
    world = World()

    def unreadable(uuid: str) -> str:
        if uuid == bws.ANTHROPIC_API_KEY_UUID:
            raise bws.Unavailable(f"BWS secret {uuid} could not be read (bws exit 1)")
        return VALUES[uuid]

    estate = onboarding.Estate(unreadable, lambda f: True, dict, str, None)
    with pytest.raises(bws.Unavailable):
        onboarding.secrets(_context(world, tmp_path, estate=estate))
    assert not world.ran("gh secret set")


def test_the_registry_is_opened_only_when_the_fork_is_missing(tmp_path: Path) -> None:
    world = World()
    world.registry = registered_registry()
    outcome = onboarding.registry(_context(world, tmp_path))
    assert outcome.status == onboarding.DONE
    assert not world.ran("git clone") and world.writes() == []


def test_the_registry_adds_exactly_the_two_consumers() -> None:
    before = tomllib.loads(REGISTRY)["credential"]
    after = tomllib.loads(registered_registry())["credential"]
    added = {
        c["id"]: [x for x in c["consumer"] if x not in b["consumer"]]
        for c, b in zip(after, before, strict=True)
    }
    assert added == {
        "factory-pr-token": [],
        "fork-sync-app-private-key": [
            {"kind": "gh-actions-secret", "repo": FORK, "name": "APP_PRIVATE_KEY"}
        ],
        "anthropic-api-key": [
            {"kind": "gh-actions-secret", "repo": FORK, "name": "ANTHROPIC_API_KEY"}
        ],
    }
    # Each consumer lands in its own entry, above the comment that opens the next one.
    text = registered_registry()
    assert text.index('repo = "AlobarQuest/rea"') < text.index("# INCOMPLETE BY DESIGN")


def test_the_registry_pull_request_is_not_merged_when_its_checks_fail(
    tmp_path: Path, waits: list[float]
) -> None:
    world = World()
    world.main[CONFIG_PATH] = CONFIG
    world.registry_checks = "failure"
    with pytest.raises(onboarding.Refused, match="concluded"):
        onboarding.registry(_context(world, tmp_path))
    assert world.ran("gh pr create --repo AlobarQuest/factory-runner")
    assert not world.ran("gh pr merge")


def test_an_off_fork_step_refuses_without_the_declaration_or_the_app(tmp_path: Path) -> None:
    world = World()
    with pytest.raises(onboarding.Refused, match="no committed"):
        onboarding.registry(_context(world, tmp_path))
    world.main[CONFIG_PATH] = CONFIG
    world.installed = False
    with pytest.raises(onboarding.Refused, match="not installed"):
        onboarding.registry(_context(world, tmp_path))
    assert not world.ran("git clone") and world.writes() == []


def _scaffolded(world: World, tmp_path: Path, waits: list[float]) -> onboarding.Context:
    world.main_workflows = set()
    ctx = _context(world, tmp_path)
    assert onboarding.scaffold(ctx).status == onboarding.DID
    return ctx


def test_the_scaffold_merge_refuses_a_diff_with_an_unexpected_file(
    tmp_path: Path, waits: list[float]
) -> None:
    world = World()
    ctx = _scaffolded(world, tmp_path, waits)
    world.branches[SCAFFOLD_BRANCH]["src/index.js"] = "planted"
    outcome = onboarding.merge_scaffold(ctx)
    assert outcome.status == onboarding.NEEDS_YOU
    assert "['src/index.js']" in outcome.detail
    assert not world.ran("gh pr merge")


def test_the_scaffold_merge_refuses_an_edited_lane_file(tmp_path: Path, waits: list[float]) -> None:
    world = World()
    ctx = _scaffolded(world, tmp_path, waits)
    world.branches[SCAFFOLD_BRANCH][CI] += "# edited\n"
    outcome = onboarding.merge_scaffold(ctx)
    assert outcome.status == onboarding.NEEDS_YOU and "not what scaffold renders" in outcome.detail
    assert not world.ran("gh pr merge")


def test_the_scaffold_merge_waits_for_build_and_test(tmp_path: Path, waits: list[float]) -> None:
    world = World()
    ctx = _scaffolded(world, tmp_path, waits)
    world.runs[SCAFFOLD_SHA]["jobs"][0] = _job("build and test", "failure")
    outcome = onboarding.merge_scaffold(ctx)
    assert outcome.status == onboarding.NEEDS_YOU and "concluded failure" in outcome.detail
    assert not world.ran("gh pr merge")
    world.runs[SCAFFOLD_SHA]["jobs"][0] = _job("build and test", "success")
    assert onboarding.merge_scaffold(ctx).status == onboarding.DID
    assert world.ran("gh pr merge 12 --repo AlobarQuest/rea --merge --admin --match-head-commit")


def test_a_declared_lockfile_is_resolved_on_the_scaffold_branch(
    tmp_path: Path, waits: list[float]
) -> None:
    world = World(config=LOCKED_CONFIG)
    world.main_workflows = set()
    outcome = onboarding.scaffold(_context(world, tmp_path))
    assert outcome.status == onboarding.NEEDS_YOU and "--baseline-tag" in outcome.detail
    outcome = onboarding.scaffold(_context(world, tmp_path, baseline_tag=TAG))
    assert outcome.status == onboarding.DID and "package-lock.json" in outcome.detail
    assert world.ran(f"{sys.executable} .github/scripts/fork_sync_review.py lock {TAG}")
    assert world.ran(f"git fetch --quiet --no-tags https://github.com/{UPSTREAM}.git")
    assert "package-lock.json" in world.branches[SCAFFOLD_BRANCH]
    # The merge accepts the lockfile, and only because the declaration generates one.
    ctx = _context(world, tmp_path, baseline_tag=TAG)
    assert onboarding.scaffold_diff_refusal(ctx, 12, LOCKED_SHA) == ""
    plain = _context(world, tmp_path, config=_context(World(), tmp_path).config)
    assert "package-lock.json" in onboarding.scaffold_diff_refusal(plain, 12, LOCKED_SHA)
    # Its bytes are checked against the tag's resolution, which needs the tag.
    untagged = _context(world, tmp_path)
    assert "--baseline-tag" in onboarding.scaffold_diff_refusal(untagged, 12, LOCKED_SHA)


def test_a_tampered_lockfile_is_not_admin_merged(tmp_path: Path, waits: list[float]) -> None:
    """A lockfile names what gets installed: anything pushed to the branch other than what the
    baseline tag resolves to refuses the merge, whatever its name."""
    world = World(config=LOCKED_CONFIG)
    world.main_workflows = set()
    onboarding.scaffold(_context(world, tmp_path, baseline_tag=TAG))
    world.branches[SCAFFOLD_BRANCH]["package-lock.json"] = '{"lockfileVersion": 3, "evil": 1}'
    outcome = onboarding.merge_scaffold(_context(world, tmp_path, baseline_tag=TAG))
    assert outcome.status == onboarding.NEEDS_YOU
    assert f"is not what {TAG} resolves to" in outcome.detail
    assert world.ran(f"{sys.executable} .github/scripts/fork_sync_review.py lock {TAG}")
    assert not world.ran("gh pr merge")


@pytest.mark.parametrize("code", [1, 2])
def test_every_nonzero_release_exit_refuses(tmp_path: Path, waits: list[float], code: int) -> None:
    """2 included: it is uv's and Python's own usage exit, so it can never mean "present"."""
    world = World().onboarded()
    world.policy["inert_landing"]["repositories"].remove(FORK.lower())
    world.policy["version"] = 12
    world.release_exit = code
    with pytest.raises(onboarding.Refused, match=f"exited {code}"):
        onboarding.landing_policy(_context(world, tmp_path))
    assert not world.ran("git commit") and not world.ran("gh pr create")


@pytest.mark.parametrize(
    "stdout",
    [
        "13 alobarquest/rea\n",  # the retired interface
        "",
        '{"status": "released", "version": 13, "repository": "alobarquest/rea"}\nmore\n',
        '{"status": "released", "version": 14, "repository": "alobarquest/rea"}\n',
        '{"status": "released", "version": "13", "repository": "alobarquest/rea"}\n',
        '{"status": "released", "version": 13, "repository": "alobarquest/rtk"}\n',
        '{"status": "done", "version": 13, "repository": "alobarquest/rea"}\n',
        '{"status": "released", "version": 13, "repository": "alobarquest/rea", "x": 1}\n',
    ],
)
def test_a_malformed_release_line_refuses(tmp_path: Path, waits: list[float], stdout: str) -> None:
    world = World().onboarded()
    world.policy["inert_landing"]["repositories"].remove(FORK.lower())
    world.policy["version"] = 12
    world.release_stdout = stdout
    with pytest.raises(onboarding.Refused):
        onboarding.landing_policy(_context(world, tmp_path))
    assert not world.ran("git commit") and not world.ran("gh pr create")


def test_an_already_present_release_is_done_once_it_is_served(
    tmp_path: Path, waits: list[float]
) -> None:
    world = World().onboarded()
    world.policy["inert_landing"]["repositories"].remove(FORK.lower())
    world.policy["version"] = 12
    line = {"status": "already_present", "version": 12, "repository": FORK.lower()}
    world.release_stdout = json.dumps(line) + "\n"
    reads = iter([False, False, False, True])
    policy = world.policy

    def served() -> dict[str, Any]:
        listed = next(reads, True)
        repositories = [*policy["inert_landing"]["repositories"], *([FORK.lower()] * listed)]
        return {
            **policy,
            "inert_landing": {**policy["inert_landing"], "repositories": repositories},
        }

    estate = onboarding.Estate(VALUES.__getitem__, lambda f: True, served, str, None)
    outcome = onboarding.landing_policy(_context(world, tmp_path, estate=estate))
    assert outcome.status == onboarding.DONE and "already released" in outcome.detail
    assert waits and not world.ran("git commit") and not world.ran("gh pr create")


def test_the_policy_pull_request_is_not_merged_when_its_checks_fail(
    tmp_path: Path, waits: list[float]
) -> None:
    world = World().onboarded()
    world.policy["inert_landing"]["repositories"].remove(FORK.lower())
    world.policy_checks = "failure"
    with pytest.raises(onboarding.Refused, match="concluded"):
        onboarding.landing_policy(_context(world, tmp_path))
    assert world.ran("gh pr create --repo AlobarQuest/change-manager")
    assert not world.ran("gh pr merge")


def test_the_policy_change_refuses_untracked_files(tmp_path: Path, waits: list[float]) -> None:
    world = World().onboarded()
    world.policy["inert_landing"]["repositories"].remove(FORK.lower())

    def debris(argv: Sequence[str], stdin: str | None, cwd: Path | None) -> str:
        if list(argv) == ["git", "status", "--porcelain"]:
            return " M app/deploy_policy.py\n M app/deploy_policy_history.py\n?? stray.txt\n"
        return world(argv, stdin, cwd)

    with pytest.raises(onboarding.Refused, match="stray.txt"):
        onboarding.landing_policy(_context(world, tmp_path, run=debris))
    assert not world.ran("git commit") and not world.ran("gh pr create")


@pytest.mark.parametrize(
    "changes",
    [
        ["app/deploy_policy.py", "app/deploy_policy_history.py", "app/main.py"],
        ["app/deploy_policy.py"],  # a version bump always moves the history too
        ["contracts/ratified_rollout_policy.json"],
    ],
)
def test_the_policy_change_is_committed_only_when_it_is_the_generators_files(
    tmp_path: Path, waits: list[float], changes: list[str]
) -> None:
    world = World().onboarded()
    world.policy["inert_landing"]["repositories"].remove(FORK.lower())
    world.policy_changes = changes
    with pytest.raises(onboarding.Refused, match="is not committed"):
        onboarding.landing_policy(_context(world, tmp_path))
    assert not world.ran("git commit") and not world.ran("gh pr create")


def test_the_policy_pull_request_is_merged_only_when_its_diff_is_the_generators_files(
    tmp_path: Path, waits: list[float]
) -> None:
    """The second check, on the opened pull request: a push to its branch after this command
    pushed (here, an existing pull request found on rerun) cannot widen what lands."""
    world = World().onboarded()
    world.policy["inert_landing"]["repositories"].remove(FORK.lower())
    world.pulls[("AlobarQuest/change-manager", 132)] = {
        "number": 132,
        "branch": "fork-sync-inert-rea",
        "head": {"sha": POLICY_SHA},
        "state": "open",
        "merged": False,
    }
    world.runs[POLICY_SHA] = {"path": "x", "jobs": [_job("Lint, type-check, and test", "success")]}
    world.policy_changes = [*world.policy_changes, ".github/workflows/deploy.yml"]
    with pytest.raises(onboarding.Refused, match="open and unmerged"):
        onboarding.landing_policy(_context(world, tmp_path))
    assert not world.ran("gh pr merge")
    world.policy_changes = world.policy_changes[:2]
    assert onboarding.landing_policy(_context(world, tmp_path)).status == onboarding.DID
    assert world.ran("gh pr merge 132 --repo AlobarQuest/change-manager --squash")


def test_the_release_reason_meets_the_generators_constraints() -> None:
    nasty = 'a "quoted"\\ reason\twith\x1b[31m control\n and ' + "x" * 200 + "  end"
    reason = onboarding.release_reason(nasty)
    assert not set(reason) & {'"', "\\", "\t", "\n", "\x1b"}
    assert all(c.isprintable() for c in reason)
    assert max(len(word) for word in reason.split()) <= onboarding.REASON_RUN
    assert "  " not in reason and reason.endswith("end")
    plain = "AlobarQuest/rea joins the fork-sync lane (orchestrator ADR-0056)"
    assert onboarding.release_reason(plain) == plain


def test_a_sync_from_before_the_scaffold_merged_does_not_count(
    tmp_path: Path, waits: list[float]
) -> None:
    """An rtk-shaped fork ran some older sync before the lane's scaffold; that run proves nothing
    about the scaffolded main, so the first sync is dispatched anyway."""
    world = World().onboarded()
    world.sync_runs = [{**SYNC_RUN, "created_at": "2026-10-23T09:00:00Z"}]
    outcome = onboarding.first_sync(_context(world, tmp_path))
    assert outcome.status == onboarding.DID
    assert world.ran("gh workflow run upstream-sync.yml --repo AlobarQuest/rea")
    world.calls.clear()
    assert onboarding.first_sync(_context(world, tmp_path)).status == onboarding.DONE
    assert world.writes() == []


def test_the_scaffold_is_dated_by_its_pull_request_or_by_its_commit(tmp_path: Path) -> None:
    """Merged through a pull request, main received the declaration when that merged (later
    than the commit); pushed straight to main, at the commit's own date."""
    world = World().onboarded()
    assert onboarding.scaffolded_at(_context(world, tmp_path)).isoformat() == (
        "2026-10-24T10:00:00+00:00"
    )
    world.pulls.clear()  # scaffolded another way: no pull request carried it
    assert onboarding.scaffolded_at(_context(world, tmp_path)).isoformat() == (
        "2026-10-24T09:30:00+00:00"
    )


def test_a_fork_scaffolded_without_a_pull_request_still_passes_the_first_sync(
    tmp_path: Path, waits: list[float]
) -> None:
    world = World().onboarded()
    world.pulls.clear()
    world.sync_runs = [{**SYNC_RUN, "created_at": "2026-10-24T09:45:00Z"}]
    assert onboarding.first_sync(_context(world, tmp_path)).status == onboarding.DONE
    assert world.writes() == []
    world.sync_runs = [{**SYNC_RUN, "created_at": "2026-10-24T09:00:00Z"}]  # before the commit
    assert onboarding.first_sync(_context(world, tmp_path)).status == onboarding.DID
    assert world.ran("gh workflow run upstream-sync.yml")


def test_a_later_failed_sync_does_not_undo_a_finished_first_sync(
    tmp_path: Path, waits: list[float]
) -> None:
    world = World().onboarded()
    failed = {**SYNC_RUN, "conclusion": "failure", "created_at": "2026-10-25T06:00:00Z"}
    world.sync_runs = [failed, SYNC_RUN]
    outcome = onboarding.first_sync(_context(world, tmp_path))
    assert outcome.status == onboarding.DONE
    assert world.writes() == []


def test_only_failed_syncs_since_the_scaffold_need_you(tmp_path: Path, waits: list[float]) -> None:
    world = World().onboarded()
    world.sync_runs = [{**SYNC_RUN, "conclusion": "failure", "html_url": "https://run/9"}]
    outcome = onboarding.first_sync(_context(world, tmp_path))
    assert outcome.status == onboarding.NEEDS_YOU and "https://run/9" in outcome.detail
    assert world.writes() == []


def test_the_policy_is_not_released_for_a_listed_fork_missing_an_author(tmp_path: Path) -> None:
    world = World().onboarded()
    world.policy["inert_landing"]["non_ecosystem_authors"] = []
    outcome = onboarding.landing_policy(_context(world, tmp_path))
    assert outcome.status == onboarding.NEEDS_YOU and "non_ecosystem_authors" in outcome.detail
    assert not world.ran("git clone")


def test_app_brain_other_than_inert_needs_the_agent(tmp_path: Path) -> None:
    world = World().onboarded()
    world.landing = "redeploys"
    outcome = onboarding.app_brain(_context(world, tmp_path))
    assert outcome.status == onboarding.NEEDS_YOU
    assert "App Brain answers redeploys" in outcome.detail
    assert "ci.yml, fork-sync-audit.yml, upstream-sync.yml" in outcome.detail


def test_a_sync_that_opened_no_pull_request_waits_to_protect_without_blocking(
    tmp_path: Path, waits: list[float]
) -> None:
    world = World()
    outcome = onboarding.protect(_context(world, tmp_path))
    assert outcome.status == onboarding.WAITING and not outcome.blocking
    assert "waiting for the next upstream release" in outcome.detail
    assert world.writes() == []


def test_with_nothing_newer_upstream_the_policy_and_app_brain_still_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, waits: list[float]
) -> None:
    """The fork is at upstream's newest release, so the first sync opens no pull request and
    protection waits for weeks; the steps that don't depend on it run meanwhile, and the run
    ends unfinished, saying why."""
    world = World().onboarded()
    world.protection = None
    world.policy["inert_landing"]["repositories"].remove(FORK.lower())
    world.policy["version"] = 12
    result = _onboard(monkeypatch, tmp_path, world)
    assert result.exit_code == 1, result.output
    statuses = dict((step, status) for status, step in _statuses(result.output))
    assert statuses["main protected"] == "waiting"
    assert statuses["change-manager inert landing policy"] == "did now"
    assert statuses["App Brain landing record"] == "done"
    assert statuses["fork-sync check"] == "needs you"  # it reports main unprotected
    assert "not finished: main protected, fork-sync check" in result.output
    assert world.protection is None and not world.ran("gh api -X")


def test_a_wait_stops_as_waiting_at_its_deadline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, waits: list[float]
) -> None:
    world = World()
    world.main_workflows = set()
    ctx = _context(world, tmp_path)
    onboarding.scaffold(ctx)
    del world.runs[SCAFFOLD_SHA]  # CI never reports
    outcome = onboarding.attempt(onboarding.merge_scaffold, ctx)
    assert outcome.status == onboarding.WAITING
    assert sum(waits) >= onboarding.DEADLINE_SECONDS
    assert all(seconds == onboarding.POLL_SECONDS for seconds in waits)
    assert not world.ran("gh pr merge")


def _unreleased(world: World) -> World:
    world.policy["inert_landing"]["repositories"].remove(FORK.lower())
    world.policy["version"] = 12
    return world


def test_the_release_version_comes_from_the_source_not_the_served_policy(
    tmp_path: Path, waits: list[float]
) -> None:
    """change-manager's main is at 13 (merged, undeployed) while 12 is served: the release must
    write 14, and the run waits for 14 or later to be served with the fork."""
    world = _unreleased(World().onboarded())
    world.source_version = 13
    outcome = onboarding.landing_policy(_context(world, tmp_path))
    assert outcome.status == onboarding.DID and "version 14 served" in outcome.detail
    world = _unreleased(World().onboarded())
    world.source_version = 13
    world.release_stdout = json.dumps(
        {"status": "released", "version": 13, "repository": FORK.lower()}
    )
    with pytest.raises(onboarding.Refused, match="released at version 13, not 14"):
        onboarding.landing_policy(_context(world, tmp_path))
    assert not world.ran("git commit")


def test_the_policy_wait_reads_the_served_policy_not_the_revision(
    tmp_path: Path, waits: list[float]
) -> None:
    """A revision that never matches the merge (another deploy, or a health route that lags)
    doesn't hold a policy change change-manager already serves; it is only reported."""
    world = _unreleased(World().onboarded())
    reads: list[int] = []
    estate = world.estate()

    def counted() -> dict[str, Any]:
        reads.append(1)
        return estate.policy()

    never = onboarding.Estate(estate.secret, estate.installed, counted, lambda: "e" * 40, None)
    outcome = onboarding.landing_policy(_context(world, tmp_path, estate=never))
    assert outcome.status == onboarding.DID and "running eeeeeeeeeeee" in outcome.detail
    assert len(reads) == 2  # one for the decision to release, one for the served wait


def test_the_policy_change_stages_exactly_the_checked_paths(
    tmp_path: Path, waits: list[float]
) -> None:
    world = _unreleased(World().onboarded())
    world.policy_changes = [*world.policy_changes, "contracts/ratified_rollout_policy.json"]
    onboarding.landing_policy(_context(world, tmp_path))
    [add] = [argv for argv, _ in world.calls if argv[:2] == ["git", "add"]]
    staged = (
        "app/deploy_policy.py app/deploy_policy_history.py contracts/ratified_rollout_policy.json"
    )
    assert " ".join(add) == f"git add -- {staged}"
    assert not world.ran("git add --update") and not world.ran("git add -A")


def test_an_open_policy_pull_request_must_release_the_next_version(
    tmp_path: Path, waits: list[float]
) -> None:
    world = _unreleased(World().onboarded())
    world.pulls[("AlobarQuest/change-manager", 132)] = {
        "number": 132,
        "branch": "fork-sync-inert-rea",
        "head": {"sha": "9" * 40},  # its source declares no version
        "state": "open",
        "merged": False,
    }
    with pytest.raises(onboarding.Refused, match="CURRENT_VERSION"):
        onboarding.landing_policy(_context(world, tmp_path))
    assert not world.ran("gh pr merge")


def test_output_nothing_expected_is_a_refusal_naming_the_step(tmp_path: Path) -> None:
    """GitHub answering with something that isn't JSON, or JSON missing what a step reads, is a
    refusal of that step, not a traceback, and the payload isn't echoed."""
    world = World()

    def garbled(argv: Sequence[str], stdin: str | None, cwd: Path | None) -> str:
        if list(argv[:3]) == ["gh", "secret", "list"]:
            return "<html>secret-looking payload</html>"
        return world(argv, stdin, cwd)

    outcome = onboarding.attempt(onboarding.secrets, _context(world, tmp_path, run=garbled))
    assert outcome.status == onboarding.NEEDS_YOU
    assert outcome.detail == "refused: secrets read output it can't use (JSONDecodeError)"

    def keyless(argv: Sequence[str], stdin: str | None, cwd: Path | None) -> str:
        if list(argv[:3]) == ["gh", "secret", "list"]:
            return '[{"nom": "APP_ID"}]'
        return world(argv, stdin, cwd)

    outcome = onboarding.attempt(onboarding.secrets, _context(world, tmp_path, run=keyless))
    assert outcome.detail == "refused: secrets read output it can't use (KeyError)"
    assert "nom" not in outcome.detail


def test_the_policy_wait_holds_until_the_released_version_is_served(
    tmp_path: Path, waits: list[float]
) -> None:
    """A document listing the fork at a version below the one released is not the release."""
    world = _unreleased(World().onboarded())
    estate = world.estate()
    listed = world.policy["inert_landing"]
    served = iter(
        [
            estate.policy(),  # the decision: not listed
            {**world.policy, "inert_landing": {**listed, "repositories": [FORK.lower()]}},
        ]
    )

    def policy() -> dict[str, Any]:
        return next(served, None) or estate.policy()

    slow = onboarding.Estate(estate.secret, estate.installed, policy, estate.revision, None)
    outcome = onboarding.landing_policy(_context(world, tmp_path, estate=slow))
    assert outcome.status == onboarding.DID and "version 13 served" in outcome.detail
    assert waits
