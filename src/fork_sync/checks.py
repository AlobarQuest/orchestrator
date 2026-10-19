"""`fork-sync check`: every onboarding step, read-only, as ok / missing / human / info.

`missing` is something an agent or a `fork-sync` command can fix and fails the check; `human` is
a step this program cannot verify (no credential, or only a person can see it); `info` never
fails.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fork_sync.fork_sync_review import CONFIG_PATH, Config, ConfigError, parse_config
from fork_sync.gh import Runner, gh_api
from fork_sync.onboard import (
    GITHUB_ACTIONS_APP_ID,
    OWN_WORKFLOWS,
    REPO_SETTINGS,
    REQUIRED_CHECKS,
    SECRETS,
    SYNC_AUTHOR,
    render,
)
from tool_installer.tools import TOOLS

OK = "ok"
MISSING = "missing"
HUMAN = "human"
INFO = "info"


@dataclass(frozen=True)
class Step:
    name: str
    status: str
    detail: str = ""


def _step(name: str, ok: bool, detail: str = "") -> Step:
    return Step(name, OK if ok else MISSING, detail)


PolicyReader = Callable[[], dict[str, Any]]
"""Returns change-manager's `inert_landing` block, or raises."""
LandingReader = Callable[[str], str]
"""Returns App Brain's landing for a repository."""


def _repository_steps(repo: dict[str, Any], upstream: str | None) -> list[Step]:
    parent = (repo.get("parent") or {}).get("full_name", "")
    steps = [
        _step(
            "is a fork of the declared upstream",
            # With no readable declaration, that step is the one reported missing.
            bool(repo.get("fork")) and (upstream is None or parent.lower() == upstream.lower()),
            f"parent {parent or 'none'}, declared {upstream or 'unreadable'}",
        )
    ]
    wrong = {key: repo.get(key) for key, want in REPO_SETTINGS.items() if repo.get(key) != want}
    wrong.pop("squash_merge_commit_title", None)  # set alongside the message; not a condition
    steps.append(_step("repository settings", not wrong, f"differs: {wrong}" if wrong else ""))
    return steps


def _file_steps(run: Runner, fork: str, config: Config) -> list[Step]:
    steps = []
    for path, expected in render(config.ecosystem).items():
        actual = gh_api(run, f"repos/{fork}/contents/{path}?ref=main", raw=True)
        if actual is None:
            steps.append(Step(f"{path} on main", MISSING, "absent"))
        else:
            same = actual.encode() == expected
            steps.append(_step(f"{path} on main", same, "" if same else "differs from scaffold"))
    return steps


def _names(items: list[dict[str, Any]] | None) -> set[str]:
    return {item["name"] for item in items or []}


def _workflow_steps(run: Runner, fork: str, upstream: str) -> list[Step]:
    ours = {
        item["name"]
        for item in gh_api(run, f"repos/{fork}/contents/.github/workflows?ref=main") or []
    }
    theirs = {
        item["name"] for item in gh_api(run, f"repos/{upstream}/contents/.github/workflows") or []
    }
    leaked = sorted((ours & theirs) - OWN_WORKFLOWS)
    actions = gh_api(run, f"repos/{fork}/actions/permissions") or {}
    return [
        _step("upstream workflows absent from main", not leaked, ", ".join(leaked)),
        _step("Actions enabled", bool(actions.get("enabled"))),
    ]


def _protection_step(run: Runner, fork: str) -> Step:
    protection = gh_api(run, f"repos/{fork}/branches/main/protection")
    if protection is None:
        return Step("branch protection on main", MISSING, "main is not protected")
    checks = protection.get("required_status_checks") or {}
    pinned = {(c.get("context"), c.get("app_id")) for c in checks.get("checks") or []}
    problems = []
    if pinned != {(name, GITHUB_ACTIONS_APP_ID) for name in REQUIRED_CHECKS}:
        problems.append(f"required checks {sorted(pinned, key=str)}")
    if checks.get("strict"):
        problems.append("strict is on")
    if protection.get("required_pull_request_reviews"):
        problems.append("reviews required")
    if (protection.get("enforce_admins") or {}).get("enabled"):
        problems.append("admins enforced")
    return _step("branch protection on main", not problems, "; ".join(problems))


def _secrets_and_run_steps(run: Runner, fork: str) -> list[Step]:
    present = {
        s["name"] for s in (gh_api(run, f"repos/{fork}/actions/secrets") or {}).get("secrets", [])
    }
    absent = [name for name in SECRETS if name not in present]
    runs = gh_api(run, f"repos/{fork}/actions/workflows/upstream-sync.yml/runs?per_page=1") or {}
    latest = (runs.get("workflow_runs") or [None])[0]
    if latest is None:
        run_step = Step("latest upstream-sync run", MISSING, "never ran")
    else:
        outcome = latest.get("conclusion") or latest.get("status")
        run_step = _step(
            "latest upstream-sync run",
            outcome in ("success", "in_progress", "queued"),
            f"{outcome} {latest.get('html_url', '')}",
        )
    return [
        _step("Actions secrets", not absent, f"absent: {', '.join(absent)}" if absent else ""),
        run_step,
    ]


def _policy_step(fork: str, read_policy: PolicyReader | None) -> Step:
    name = "change-manager inert landing policy"
    if read_policy is None:
        return Step(name, HUMAN, "not checked: set FORK_SYNC_CHANGE_MANAGER_TOKEN")
    try:
        block = read_policy()
    except Exception as error:  # an unreadable policy is reported, never assumed
        return Step(name, MISSING, f"unreadable: {error}")
    problems = []
    if fork.lower() not in {r.lower() for r in block.get("repositories", [])}:
        problems.append("repository not listed")
    for key in ("permitted_authors", "non_ecosystem_authors"):
        if SYNC_AUTHOR not in block.get(key, []):
            problems.append(f"{SYNC_AUTHOR} not in {key}")
    return _step(name, not problems, "; ".join(problems))


def _landing_step(fork: str, read_landing: LandingReader | None) -> Step:
    name = "App Brain landing record"
    if read_landing is None:
        return Step(
            name,
            HUMAN,
            f'not checked: call record_default_branch_landing(github_repo="{fork}", '
            'landing="inert", evidence=...) or set FORK_SYNC_APP_BRAIN_URL/_KEY',
        )
    answer = read_landing(fork)
    return _step(name, answer == "inert", f"App Brain answers {answer}")


def _tool_step(fork: str) -> Step:
    rows = [tool.name for tool in TOOLS if tool.repository.lower() == fork.lower()]
    if rows:
        return Step("tool_installer row", OK, ", ".join(rows))
    return Step("tool_installer row", INFO, "not an installed tool")


def check(
    run: Runner,
    fork: str,
    read_policy: PolicyReader | None = None,
    read_landing: LandingReader | None = None,
) -> list[Step]:
    repo = gh_api(run, f"repos/{fork}")
    if repo is None:
        return [Step("repository exists", MISSING, f"{fork} is not visible")]
    text = gh_api(run, f"repos/{fork}/contents/{CONFIG_PATH}?ref=main", raw=True)
    config: Config | None = None
    if text is None:
        steps = [Step(f"{CONFIG_PATH} on main", MISSING, "absent")]
    else:
        try:
            config = parse_config(text)
            steps = [Step(f"{CONFIG_PATH} on main", OK)]
        except ConfigError as error:
            steps = [Step(f"{CONFIG_PATH} on main", MISSING, f"invalid: {error}")]
    upstream = config.upstream if config else (repo.get("parent") or {}).get("full_name")
    steps += _repository_steps(repo, config.upstream if config else None)
    if config is not None:
        steps += _file_steps(run, fork, config)
    if upstream:
        steps += _workflow_steps(run, fork, upstream)
    steps.append(_protection_step(run, fork))
    steps += _secrets_and_run_steps(run, fork)
    steps.append(_policy_step(fork, read_policy))
    steps.append(_landing_step(fork, read_landing))
    steps.append(_tool_step(fork))
    return steps
