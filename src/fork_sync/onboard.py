"""The acting half: render the fork-side files, and the three onboarding steps that write.

Each step takes an injectable runner, works in a temporary clone, and refuses before it writes
when its precondition does not hold.
"""

from __future__ import annotations

import re
import tempfile
from importlib import resources
from pathlib import Path

from fork_sync.fork_sync_review import ACKS_PATH, CONFIG_PATH, Config, parse_config
from fork_sync.gh import Runner, gh_api, gh_write

SYNC_WORKFLOW = ".github/workflows/upstream-sync.yml"
CI_WORKFLOW = ".github/workflows/ci.yml"
AUDIT_WORKFLOW = ".github/workflows/fork-sync-audit.yml"
REVIEW_SCRIPT = ".github/scripts/fork_sync_review.py"
OWN_WORKFLOWS = frozenset({"upstream-sync.yml", "ci.yml", "fork-sync-audit.yml"})
REQUIRED_CHECKS = ("build and test", "security review")
GITHUB_ACTIONS_APP_ID = 15368
"""The GitHub Actions app: a required check must come from a workflow, not any status poster."""
SYNC_APP = "fork-secure-upstream-sync"
SYNC_AUTHOR = f"{SYNC_APP}[bot]"
SECRETS = ("APP_ID", "APP_PRIVATE_KEY", "ANTHROPIC_API_KEY")
SCAFFOLD_BRANCH = "fork-sync-scaffold"
SCAFFOLD_TITLE = "fork-sync: scaffold the upstream sync lane"
REPO_SETTINGS = {
    "allow_merge_commit": True,
    "squash_merge_commit_title": "COMMIT_OR_PR_TITLE",
    "squash_merge_commit_message": "COMMIT_MESSAGES",
    "delete_branch_on_merge": True,
    # The alert is an issue, and a fork starts with issues off.
    "has_issues": True,
}


EMPTY_ACKS = b"""# Findings a person has read and accepted, by fingerprint (fork-sync review).
# One [[ack]] per finding: fingerprint, by, date, reason, and for an `ai` finding severity
# (the highest accepted: low, medium or high). See onboarding-a-fork.md.
"""


class Refused(RuntimeError):
    """A precondition does not hold; nothing was written."""


def render(ecosystem: str) -> dict[str, bytes]:
    """The fork-side files, exactly as `check` expects to find them on main."""
    package = resources.files("fork_sync")
    return {
        SYNC_WORKFLOW: package.joinpath("templates/upstream-sync.yml").read_bytes(),
        CI_WORKFLOW: package.joinpath(f"templates/ci-{ecosystem}.yml").read_bytes(),
        AUDIT_WORKFLOW: package.joinpath(f"templates/audit-{ecosystem}.yml").read_bytes(),
        REVIEW_SCRIPT: package.joinpath("fork_sync_review.py").read_bytes(),
    }


def parent_of(run: Runner, fork: str) -> str:
    repo = gh_api(run, f"repos/{fork}")
    if repo is None:
        raise Refused(f"{fork} does not exist or is not visible")
    parent = (repo.get("parent") or {}).get("full_name")
    if not repo.get("fork") or not parent:
        raise Refused(f"{fork} is not a fork")
    return parent


def clone_fork(run: Runner, fork: str, into: Path, *extra: str) -> Path:
    target = into / "fork"
    run(
        ["git", "clone", "--quiet", *extra, f"https://github.com/{fork}.git", str(target)],
        None,
        None,
    )
    return target


def scaffold(run: Runner, fork: str, config_path: Path) -> int:
    """Open a pull request adding the declaration and the rendered files. Returns its number."""
    text = config_path.read_text(encoding="utf-8")
    config: Config = parse_config(text)
    parent = parent_of(run, fork)
    if parent.lower() != config.upstream.lower():
        raise Refused(f"{fork} is a fork of {parent}, but the declaration names {config.upstream}")
    with tempfile.TemporaryDirectory() as scratch:
        clone = clone_fork(run, fork, Path(scratch), "--depth", "1")
        run(["git", "checkout", "--quiet", "-b", SCAFFOLD_BRANCH], None, clone)
        files = {CONFIG_PATH: text.encode(), **render(config.ecosystem)}
        # Re-scaffolding an onboarded fork keeps what a person has acknowledged.
        if not (clone / ACKS_PATH).exists():
            files[ACKS_PATH] = EMPTY_ACKS
        for path, content in files.items():
            (clone / path).parent.mkdir(parents=True, exist_ok=True)
            (clone / path).write_bytes(content)
        run(["git", "add", *files], None, clone)
        run(
            ["git", "commit", "--quiet", "-m", "fork-sync: scaffold the upstream sync lane"],
            None,
            clone,
        )
        run(["git", "push", "--quiet", "origin", SCAFFOLD_BRANCH], None, clone)
        url = run(
            [
                "gh", "pr", "create", "--repo", fork, "--base", "main", "--head", SCAFFOLD_BRANCH,
                "--title", SCAFFOLD_TITLE,
                "--body", f"Adds `{CONFIG_PATH}` and the files `fork-sync scaffold` renders. "
                f"Acknowledged findings go in `{ACKS_PATH}`.",
            ],
            None,
            clone,
        )  # fmt: skip
    match = re.search(r"/pull/(\d+)", url)
    if match is None:
        raise Refused(f"gh pr create printed no pull request URL: {url.strip()[:200]}")
    return int(match.group(1))


def fork_only_commits(run: Runner, clone: Path) -> list[str]:
    """Commits on the fork's main that no upstream branch or tag contains."""
    out = run(
        ["git", "log", "--oneline", "origin/main", "--not", "--remotes=upstream",
         "--glob=refs/upstream-tags/*"],
        None,
        clone,
    )  # fmt: skip
    return out.splitlines()


def baseline(run: Runner, fork: str, tag: str, *, discard_fork_commits: bool = False) -> str:
    """Reset the fork's main to upstream's tag minus its workflows. Returns the new head.

    Commits on main that upstream doesn't have are refused unless the caller has decided to drop
    them; they're printed either way, so the decision is made against the list.
    """
    upstream = parent_of(run, fork)
    with tempfile.TemporaryDirectory() as scratch:
        clone = clone_fork(run, fork, Path(scratch))
        run(["git", "remote", "add", "upstream", f"https://github.com/{upstream}.git"], None, clone)
        run(
            ["git", "fetch", "--quiet", "--no-tags", "upstream",
             "+refs/heads/*:refs/remotes/upstream/*", "+refs/tags/*:refs/upstream-tags/*"],
            None,
            clone,
        )  # fmt: skip
        target = f"refs/upstream-tags/{tag}"
        try:
            run(["git", "rev-parse", "--verify", "--quiet", f"{target}^{{commit}}"], None, clone)
        except RuntimeError:
            raise Refused(f"{upstream} has no tag {tag}") from None
        stranded = fork_only_commits(run, clone)
        if stranded and discard_fork_commits:
            print(f"discarding {len(stranded)} commit(s) upstream does not have:")
            print("\n".join(stranded))
        elif stranded:
            raise Refused(
                f"main has {len(stranded)} commit(s) upstream does not; a baseline would lose "
                "them (pass --discard-fork-commits to drop them):\n" + "\n".join(stranded)
            )
        old = run(["git", "rev-parse", "origin/main"], None, clone).strip()
        run(["git", "checkout", "--quiet", "-B", "main", target], None, clone)
        # All of upstream's `.github/`, not only its workflows: a planted acknowledgements file or
        # review script must not become the fork's gate.
        run(["git", "rm", "-r", "-q", "--ignore-unmatch", ".github"], None, clone)
        message = f"fork-sync baseline: {tag}, upstream .github removed"
        run(["git", "commit", "--quiet", "--allow-empty", "-m", message], None, clone)
        run(
            ["git", "push", "--quiet", f"--force-with-lease=main:{old}", "origin", "HEAD:main"],
            None,
            clone,
        )
        return run(["git", "rev-parse", "HEAD"], None, clone).strip()


def reported_checks(run: Runner, fork: str) -> dict[int, set[str]]:
    """Per open pull request, the Actions job names that reported on its head."""
    reported: dict[int, set[str]] = {}
    for pull in gh_api(run, f"repos/{fork}/pulls?state=open&per_page=100") or []:
        sha = pull["head"]["sha"]
        runs = gh_api(run, f"repos/{fork}/actions/runs?head_sha={sha}&per_page=100") or {}
        names: set[str] = set()
        for workflow_run in runs.get("workflow_runs", []):
            jobs = gh_api(run, f"repos/{fork}/actions/runs/{workflow_run['id']}/jobs") or {}
            names |= {job["name"] for job in jobs.get("jobs", [])}
        reported[pull["number"]] = names
    return reported


def protect(run: Runner, fork: str) -> int:
    """Apply the landing settings and branch protection. Returns the pull request that proved
    the required job names report."""
    proven = [n for n, names in reported_checks(run, fork).items() if set(REQUIRED_CHECKS) <= names]
    if not proven:
        raise Refused(
            f"no open pull request on {fork} has reported both {', '.join(REQUIRED_CHECKS)}; "
            "protecting main on names that never report would block every pull request"
        )
    gh_write(run, "PATCH", f"repos/{fork}", REPO_SETTINGS)
    gh_write(
        run,
        "PUT",
        f"repos/{fork}/branches/main/protection",
        {
            "required_status_checks": {
                "strict": False,
                "checks": [
                    {"context": name, "app_id": GITHUB_ACTIONS_APP_ID} for name in REQUIRED_CHECKS
                ],
            },
            "enforce_admins": False,
            "required_pull_request_reviews": None,
            "restrictions": None,
        },
    )
    return min(proven)


def human_steps(fork: str) -> str:
    return f"""Human steps (only a person can do these):
  1. Install the App {SYNC_APP} on {fork}:
     https://github.com/settings/apps/{SYNC_APP}/installations -> Configure ->
     "Only select repositories" -> add {fork}.
  2. Confirm BWS project "Ops / Platform" holds the App key
     fork-secure-upstream-sync-app-private-key-b64 (998b817e-6164-4b9f-9c52-b4e800e8270d).

Agent steps (docs/operations/onboarding-a-fork.md):
  3. Set Actions secrets {", ".join(SECRETS)} on {fork} from BWS, in-process, value on stdin
     (`gh secret set NAME --repo {fork}` with no --body); never `--body -`.
  4. Register each secret copy in factory-runner's `.cred-consumers.toml`.
  5. Release a change-manager landing policy listing {fork.lower()} in inert_landing.repositories
     and {SYNC_AUTHOR} in permitted_authors and non_ecosystem_authors.
  6. Record App Brain's landing for {fork}:
     record_default_branch_landing(github_repo="{fork}", landing="inert",
       evidence="<the workflows read: no deploy on push; installs happen by tool_installer>").
  7. If the fork is an installed tool, add its row to src/tool_installer/tools.py.
"""
