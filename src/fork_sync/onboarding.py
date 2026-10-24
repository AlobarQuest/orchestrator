"""`fork-sync onboard`: the runbook's procedure as one resumable command (ADR-0056 amendment 5).

Each step reads where the fork stands, does its act when that read says it isn't done, and says
which of four things happened: `done` (nothing to do), `did now`, `waiting` (something it started
or depends on did not finish before the deadline) or `needs you`. The run stops at the first step
that is waiting or needs a person, printing what to do, so rerunning continues from there.

What stays a person's: installing the App, accepting the first review's findings (`onboard`
prints the `fork-sync ack` dry run and never approves), and App Brain's landing record, which
only an agent session can write. A step that writes outside the fork (factory-runner's registry,
change-manager's policy) refuses unless the fork's declaration is committed and the App is
installed on it. Every GitHub call goes through the injected runner, every wait pauses between
polls and has a deadline, and no secret value is printed or passed as an argument.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import sys
import tempfile
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final

from fork_sync import acknowledge, bws, checks, onboard
from fork_sync.estate import SYNC_APP_ID, EstateReadError
from fork_sync.fork_sync_review import ACKS_PATH, CONFIG_PATH, ECOSYSTEMS, Config, parse_config
from fork_sync.gh import CommandFailed, Runner, gh_api
from fork_sync.onboard import (
    REQUIRED_CHECKS,
    REVIEW_SCRIPT,
    SCAFFOLD_BRANCH,
    SECRETS,
    Refused,
    render,
)

DONE: Final = "done"
DID: Final = "did now"
WAITING: Final = "waiting"
NEEDS_YOU: Final = "needs you"

REGISTRY_REPO: Final = "AlobarQuest/factory-runner"
REGISTRY_PATH: Final = ".cred-consumers.toml"
# Every fork adds an Actions-secret consumer to both entries; APP_ID is the App's id, not a
# credential, so it is registered nowhere.
REGISTRATIONS: Final = (
    ("fork-sync-app-private-key", "APP_PRIVATE_KEY"),
    ("anthropic-api-key", "ANTHROPIC_API_KEY"),
)
POLICY_REPO: Final = "AlobarQuest/change-manager"
RELEASE_SCRIPT: Final = "scripts/release_inert_repository.py"
RELEASED: Final = "released"
ALREADY_PRESENT: Final = "already_present"
POLICY_FILES: Final = frozenset(  # not-a-vocabulary: the generator's output files, read as paths
    {
        "app/deploy_policy.py",
        "app/deploy_policy_history.py",
        "contracts/ratified_rollout_policy.json",
    }
)
"""Every file the release script may change. A version bump always moves both `app/` files (the
outgoing version goes to the history), so those two are required; the contract is moved only when
the rollout terms are."""
POLICY_REQUIRED: Final = frozenset({"app/deploy_policy.py", "app/deploy_policy_history.py"})
POLICY_SOURCE: Final = "app/deploy_policy.py"
MAX_PAGES: Final = 10
"""How many pages of a GitHub listing a step reads before refusing as implausible."""
REASON_RUN: Final = 80
"""The longest unbroken run in a reason the release script is given; it refuses one over ~89."""
CONFIRM: Final = "fork-sync check"
"""The last step's name: a run that ends on it, done, has onboarded the fork."""
POLL_SECONDS: Final = 30
DEADLINE_SECONDS: Final = 45 * 60
"""How long one wait may take before the run stops as `waiting`; rerunning resumes it."""

sleep = time.sleep
monotonic = time.monotonic
"""Looked up at call time, so tests replace them; nothing waits for real in a test."""


@dataclass(frozen=True)
class Outcome:
    step: str
    status: str
    detail: str = ""
    blocking: bool = True
    """Whether a `waiting` or `needs you` stops the run. Only a wait the later steps don't depend
    on is not: they run, and the run still ends unfinished."""


class Waiting(RuntimeError):
    """Something did not finish before the deadline; nothing is wrong yet."""


@dataclass(frozen=True)
class Estate:
    """What onboarding reads outside GitHub's CLI. Tests replace all of it."""

    secret: bws.SecretReader
    installed: Callable[[str], bool]
    """Whether the sync App's installation covers the fork."""
    policy: Callable[[], dict[str, Any]]
    """change-manager's whole landing policy document."""
    revision: Callable[[], str]
    """The commit change-manager's running container was built from."""
    landing: checks.LandingReader | None
    """App Brain's landing for a repository, or `None` without its read key."""


@dataclass(frozen=True)
class Context:
    run: Runner
    fork: str
    """GitHub's own spelling of the fork."""
    upstream: str
    text: str
    """The declaration as the operator wrote it."""
    config: Config
    estate: Estate
    config_path: Path
    baseline_tag: str | None
    discard_fork_commits: bool
    reset_secrets: bool
    today: date


def wait_for[T](what: str, probe: Callable[[], T | None]) -> T:
    deadline = monotonic() + DEADLINE_SECONDS
    while (value := probe()) is None:
        if monotonic() >= deadline:
            raise Waiting(f"{what} did not finish in {DEADLINE_SECONDS // 60} minutes; rerun")
        sleep(POLL_SECONDS)
    return value


def _number(url: str) -> int:
    match = re.search(r"/pull/(\d+)", url)
    if match is None:
        raise Refused(f"gh pr create printed no pull request URL: {url.strip()[:200]}")
    return int(match.group(1))


def _open_pull(run: Runner, repo: str, branch: str) -> dict[str, Any] | None:
    owner = repo.split("/")[0]
    pulls = gh_api(run, f"repos/{repo}/pulls?state=open&head={owner}:{branch}") or []
    return pulls[0] if pulls else None


def _on(ctx: Context, path: str, ref: str = "main") -> str | None:
    return gh_api(ctx.run, f"repos/{ctx.fork}/contents/{path}?ref={ref}", raw=True)


def _latest_jobs(run: Runner, repo: str, sha: str) -> dict[str, dict[str, Any]]:
    """Every Actions job at `sha` by name, the newest of each name (a re-run replaces a job)."""
    runs = (gh_api(run, f"repos/{repo}/actions/runs?head_sha={sha}&per_page=100") or {}).get(
        "workflow_runs", []
    )
    jobs: dict[str, dict[str, Any]] = {}
    for workflow_run in runs:
        listed = gh_api(run, f"repos/{repo}/actions/runs/{workflow_run['id']}/jobs") or {}
        for job in listed.get("jobs", []):
            if job["name"] not in jobs or job["id"] > jobs[job["name"]]["id"]:
                jobs[job["name"]] = job
    return jobs


def land_pull(run: Runner, repo: str, number: int, sha: str) -> str:
    """Squash-merge a pull request this command opened once every check `main`'s protection
    requires has passed at the commit it pushed. Returns the merge commit."""
    protection = gh_api(run, f"repos/{repo}/branches/main/protection")
    required = [
        check["context"]
        for check in ((protection or {}).get("required_status_checks") or {}).get("checks") or []
    ]
    if not required:
        raise Refused(f"{repo} main requires no check, so nothing says #{number} may merge")

    def finished() -> dict[str, dict[str, Any]] | None:
        jobs = _latest_jobs(run, repo, sha)
        done = all(jobs.get(name, {}).get("status") == "completed" for name in required)
        return jobs if done else None

    jobs = wait_for(f"the checks of {repo}#{number}", finished)
    failed = {name: jobs[name].get("conclusion") for name in required}
    failed = {name: conclusion for name, conclusion in failed.items() if conclusion != "success"}
    if failed:
        raise Refused(f"{repo}#{number} is open and unmerged: its checks concluded {failed}")
    pull = gh_api(run, f"repos/{repo}/pulls/{number}") or {}
    if pull.get("head", {}).get("sha") != sha:
        raise Refused(f"{repo}#{number}'s head is no longer the commit this command pushed")
    if not pull.get("merged"):
        merge = ["gh", "pr", "merge", str(number), "--repo", repo, "--squash"]
        run([*merge, "--match-head-commit", sha], None, None)
        pull = gh_api(run, f"repos/{repo}/pulls/{number}") or {}
    if not pull.get("merged") or not pull.get("merge_commit_sha"):
        raise Refused(f"{repo}#{number} did not merge")
    return pull["merge_commit_sha"]


def _in_place(ctx: Context) -> None:
    """A step that writes outside the fork runs only for a fork that has committed this
    declaration (on main, or on its open scaffold pull request) and has the App installed."""
    committed = _on(ctx, CONFIG_PATH) or _on(ctx, CONFIG_PATH, SCAFFOLD_BRANCH)
    if committed is None:
        raise Refused(f"{ctx.fork} has no committed {CONFIG_PATH}; nothing outside it is changed")
    if parse_config(committed).upstream.lower() != ctx.upstream.lower():
        raise Refused(f"{ctx.fork}'s committed {CONFIG_PATH} names another upstream")
    if not ctx.estate.installed(ctx.fork):
        raise Refused(f"{onboard.SYNC_APP} is not installed on {ctx.fork}")


# ---- 1. the App (a person's) ------------------------------------------------------------------


def _workflows(run: Runner, path: str) -> set[str]:
    return {item["name"] for item in gh_api(run, path) or []}


def app_installed(ctx: Context) -> Outcome:
    name = "App installed on the fork"
    if ctx.estate.installed(ctx.fork):
        return Outcome(name, DONE)
    return Outcome(name, NEEDS_YOU, onboard.install_steps(ctx.fork))


# ---- 2. baseline -------------------------------------------------------------------------------


def baseline(ctx: Context) -> Outcome:
    name = "baseline main on an upstream tag"
    if _on(ctx, CONFIG_PATH) is not None:
        return Outcome(name, DONE, f"{CONFIG_PATH} is on main")
    ours = _workflows(ctx.run, f"repos/{ctx.fork}/contents/.github/workflows?ref=main")
    theirs = _workflows(ctx.run, f"repos/{ctx.upstream}/contents/.github/workflows")
    inherited = sorted(ours & theirs)
    if not inherited:
        return Outcome(name, DONE, "main carries none of upstream's workflows")
    if ctx.baseline_tag is None:
        return Outcome(
            name,
            NEEDS_YOU,
            f"main carries upstream's {', '.join(inherited)}; rerun with --baseline-tag <the "
            "upstream tag main's manifest version matches>",
        )
    if gh_api(ctx.run, f"repos/{ctx.fork}/branches/main/protection") is not None:
        return Outcome(
            name,
            NEEDS_YOU,
            f"main is protected and a baseline force-pushes it: save the protection (gh api "
            f"repos/{ctx.fork}/branches/main/protection), remove it, and rerun",
        )
    head = onboard.baseline(
        ctx.run, ctx.fork, ctx.baseline_tag, discard_fork_commits=ctx.discard_fork_commits
    )
    return Outcome(name, DID, f"main is now {head[:12]} ({ctx.baseline_tag})")


# ---- 3. scaffold -------------------------------------------------------------------------------


def scaffold(ctx: Context) -> Outcome:
    name = "scaffold pull request"
    if _on(ctx, CONFIG_PATH) is not None:
        return Outcome(name, DONE, f"{CONFIG_PATH} is on main")
    did: list[str] = []
    pull = _open_pull(ctx.run, ctx.fork, SCAFFOLD_BRANCH)
    if pull is None:
        did.append(f"opened #{onboard.scaffold(ctx.run, ctx.fork, ctx.config_path)}")
    lockfile = ECOSYSTEMS[ctx.config.ecosystem].lockfile
    if ctx.config.generate_lockfile and lockfile and _on(ctx, lockfile, SCAFFOLD_BRANCH) is None:
        if ctx.baseline_tag is None:
            return Outcome(
                name,
                NEEDS_YOU,
                f"the declaration generates {lockfile}, which the scaffold branch lacks; rerun "
                "with --baseline-tag <the tag main was baselined to>",
            )
        bootstrap_lockfile(ctx, ctx.baseline_tag, lockfile)
        did.append(f"committed {lockfile} for {ctx.baseline_tag}")
    if did:
        return Outcome(name, DID, "; ".join(did))
    return Outcome(name, DONE, f"#{pull['number'] if pull else '?'} is open")


def _resolve_lockfile(ctx: Context, clone: Path, tag: str, lockfile: str) -> bytes:
    """Resolve `lockfile` for `tag` in a clone of the scaffold branch with the review script's own
    `lock`, exactly as the sync will, and return its bytes (staged in the clone). The script run is
    the one this tool rendered, checked byte for byte. `lock` pins resolution to the tag's commit
    date, so the same tag resolves to the same bytes."""
    script = clone / REVIEW_SCRIPT
    if not script.is_file() or script.read_bytes() != render(ctx.config.ecosystem)[REVIEW_SCRIPT]:
        raise Refused(f"the scaffold branch's {REVIEW_SCRIPT} is not the one scaffold renders")
    (clone / lockfile).unlink(missing_ok=True)  # `lock` refuses to overwrite one
    source = f"https://github.com/{ctx.upstream}.git"
    refspec = f"refs/tags/{tag}:refs/tags/{tag}"
    ctx.run(["git", "fetch", "--quiet", "--no-tags", source, refspec], None, clone)
    ctx.run([sys.executable, REVIEW_SCRIPT, "lock", tag], None, clone)
    return (clone / lockfile).read_bytes()


def bootstrap_lockfile(ctx: Context, tag: str, lockfile: str) -> None:
    """main has no lockfile yet, so `build and test` can't install: resolve one for the baseline
    tag and push it to the scaffold branch."""
    with tempfile.TemporaryDirectory() as scratch:
        clone = onboard.clone_fork(ctx.run, ctx.fork, Path(scratch), "--branch", SCAFFOLD_BRANCH)
        _resolve_lockfile(ctx, clone, tag, lockfile)
        message = f"fork-sync: resolve {lockfile} for {tag}"
        ctx.run(["git", "commit", "--quiet", "-m", message], None, clone)
        ctx.run(["git", "push", "--quiet", "origin", SCAFFOLD_BRANCH], None, clone)


def lockfile_refusal(ctx: Context, sha: str, lockfile: str) -> str:
    """Why the scaffold head's lockfile must not be admin-merged, or "" when it may: its bytes are
    the ones this command resolves for the baseline tag. A lockfile names what gets installed, so
    one anybody pushed to the branch is not trusted for being the right file name."""
    if ctx.baseline_tag is None:
        return f"{lockfile} can be checked only against the baseline tag; rerun with --baseline-tag"
    with tempfile.TemporaryDirectory() as scratch:
        clone = onboard.clone_fork(ctx.run, ctx.fork, Path(scratch), "--branch", SCAFFOLD_BRANCH)
        ctx.run(["git", "checkout", "--quiet", "--detach", sha], None, clone)
        resolved = _resolve_lockfile(ctx, clone, ctx.baseline_tag, lockfile)
    if (_on(ctx, lockfile, sha) or "").encode() != resolved:
        return f"{lockfile} at its head is not what {ctx.baseline_tag} resolves to"
    return ""


# ---- 4. secrets --------------------------------------------------------------------------------


def secrets(ctx: Context) -> Outcome:
    name = "Actions secrets"
    listed = ctx.run(["gh", "secret", "list", "--repo", ctx.fork, "--json", "name"], None, None)
    present = {item["name"] for item in json.loads(listed or "[]")}
    wanted = [secret for secret in SECRETS if ctx.reset_secrets or secret not in present]
    if not wanted:
        return Outcome(name, DONE, ", ".join(SECRETS))
    sources: dict[str, Callable[[], str]] = {
        "APP_ID": lambda: SYNC_APP_ID,
        "APP_PRIVATE_KEY": lambda: bws.pem(ctx.estate.secret(bws.APP_PRIVATE_KEY_UUID)),
        "ANTHROPIC_API_KEY": lambda: ctx.estate.secret(bws.ANTHROPIC_API_KEY_UUID),
    }
    # Every value is read before any is set, so a BWS failure leaves the fork as it was.
    values = {secret: sources[secret]() for secret in wanted}
    for secret, value in values.items():
        # The value goes on stdin. `--body -` would store a literal dash.
        ctx.run(["gh", "secret", "set", secret, "--repo", ctx.fork], value, None)
    return Outcome(name, DID, f"set {', '.join(wanted)} from BWS")


# ---- 5. registry -------------------------------------------------------------------------------


def missing_registrations(text: str, fork: str) -> list[tuple[str, str]]:
    """The (credential id, secret name) pairs whose consumer list lacks the fork's copy."""
    credentials = {c["id"]: c for c in tomllib.loads(text).get("credential", [])}
    missing = []
    for credential, secret in REGISTRATIONS:
        if credential not in credentials:
            raise Refused(f"{REGISTRY_PATH} has no {credential} entry")
        consumers = credentials[credential].get("consumer", [])
        if not any(
            c.get("kind") == "gh-actions-secret"
            and str(c.get("repo", "")).lower() == fork.lower()
            and c.get("name") == secret
            for c in consumers
        ):
            missing.append((credential, secret))
    return missing


def registered(text: str, fork: str, missing: list[tuple[str, str]]) -> str:
    """The registry with one consumer appended to each named entry, after its last line (the
    comments before the next entry are that entry's). Refused unless it parses to exactly the
    old registry plus those consumers."""
    lines = text.splitlines(keepends=True)
    for credential, secret in missing:
        at = [i for i, line in enumerate(lines) if line.strip() == f'id = "{credential}"']
        if len(at) != 1:
            raise Refused(f"{credential} is not one plain entry in {REGISTRY_PATH}")
        end = next(
            (i for i in range(at[0], len(lines)) if lines[i].startswith("[[credential]]")),
            len(lines),
        )
        while end > at[0] and (not lines[end - 1].strip() or lines[end - 1].startswith("#")):
            end -= 1
        block = (
            f'\n  [[credential.consumer]]\n  kind = "gh-actions-secret"\n'
            f'  repo = "{fork}"\n  name = "{secret}"\n'
        )
        lines.insert(end, block)
    new = "".join(lines)
    expected = tomllib.loads(text)
    for entry in expected["credential"]:
        for credential, secret in missing:
            if entry["id"] == credential:
                entry["consumer"].append(
                    {"kind": "gh-actions-secret", "repo": fork, "name": secret}
                )
    if tomllib.loads(new) != expected:
        raise Refused(f"the new {REGISTRY_PATH} does not read back as the old one plus the fork")
    return new


def registry(ctx: Context) -> Outcome:
    name = f"registered in {REGISTRY_REPO}'s {REGISTRY_PATH}"
    path = f"repos/{REGISTRY_REPO}/contents/{REGISTRY_PATH}?ref=main"
    text = gh_api(ctx.run, path, raw=True)
    if text is None:
        raise Refused(f"{REGISTRY_REPO} main has no {REGISTRY_PATH}")
    missing = missing_registrations(text, ctx.fork)
    if not missing:
        return Outcome(name, DONE)
    _in_place(ctx)
    branch = f"fork-sync-register-{ctx.fork.split('/')[1].lower()}"
    pull = _open_pull(ctx.run, REGISTRY_REPO, branch)
    if pull is not None:
        number, sha = pull["number"], pull["head"]["sha"]
    else:
        title = f"Registry: the {ctx.fork} fork's fork-sync secret copies"
        body = (
            f"Adds {ctx.fork}'s `APP_PRIVATE_KEY` and `ANTHROPIC_API_KEY` Actions-secret copies as "
            "consumers of `fork-sync-app-private-key` and `anthropic-api-key`, set from BWS by "
            "`fork-sync onboard` (orchestrator `docs/operations/onboarding-a-fork.md`, step 5)."
        )
        with tempfile.TemporaryDirectory() as scratch:
            clone = onboard.clone_fork(ctx.run, REGISTRY_REPO, Path(scratch), "--depth", "1")
            target = clone / REGISTRY_PATH
            if target.read_text(encoding="utf-8") != text:
                raise Refused(f"{REGISTRY_REPO}'s {REGISTRY_PATH} changed while it was read; rerun")
            target.write_text(registered(text, ctx.fork, missing), encoding="utf-8")
            sha = _push_branch(ctx.run, clone, branch, [REGISTRY_PATH], title)
            argv = ["gh", "pr", "create", "--repo", REGISTRY_REPO, "--base", "main"]
            url = ctx.run([*argv, "--head", branch, "--title", title, "--body", body], None, clone)
        number = _number(url)
    merged = land_pull(ctx.run, REGISTRY_REPO, number, sha)
    return Outcome(name, DID, f"{REGISTRY_REPO}#{number} merged as {merged[:12]}")


def _push_branch(run: Runner, clone: Path, branch: str, paths: list[str], message: str) -> str:
    run(["git", "checkout", "--quiet", "-b", branch], None, clone)
    run(["git", "add", "--", *paths], None, clone)
    run(["git", "commit", "--quiet", "-m", message], None, clone)
    run(["git", "push", "--quiet", "origin", branch], None, clone)
    return run(["git", "rev-parse", "HEAD"], None, clone).strip()


# ---- 6. merge the scaffold ---------------------------------------------------------------------


def scaffold_diff_refusal(ctx: Context, number: int, sha: str) -> str:
    """Why the scaffold pull request must not be admin-merged, or "" when it may: its diff is
    exactly the rendered lane files, this declaration and (when main has none) the empty
    acknowledgements, plus the declared lockfile, each added or changed, and every rendered file
    and the declaration carry exactly the bytes this command would write."""
    files = gh_api(ctx.run, f"repos/{ctx.fork}/pulls/{number}/files?per_page=100") or []
    changed = {item["filename"]: item.get("status") for item in files}
    expected = {CONFIG_PATH: ctx.text.encode(), **render(ctx.config.ecosystem)}
    if _on(ctx, ACKS_PATH) is None:
        expected[ACKS_PATH] = onboard.EMPTY_ACKS
    lockfile = ECOSYSTEMS[ctx.config.ecosystem].lockfile if ctx.config.generate_lockfile else None
    unexpected = sorted(set(changed) - set(expected) - {lockfile})
    absent = sorted(set(expected) - set(changed))
    if unexpected or absent:
        return f"its diff adds {unexpected or 'nothing unexpected'} and lacks {absent or 'nothing'}"
    removed = sorted(
        path for path, status in changed.items() if status not in ("added", "modified")
    )
    if removed:
        return f"its diff removes or renames {removed}"
    for path, content in expected.items():
        if (_on(ctx, path, sha) or "").encode() != content:
            return f"{path} at its head is not what scaffold renders"
    if lockfile is not None and lockfile in changed:
        return lockfile_refusal(ctx, sha, lockfile)
    return ""


def merge_scaffold(ctx: Context) -> Outcome:
    name = "scaffold merged"
    if _on(ctx, CONFIG_PATH) is not None:
        return Outcome(name, DONE, f"{CONFIG_PATH} is on main")
    pull = _open_pull(ctx.run, ctx.fork, SCAFFOLD_BRANCH)
    if pull is None:
        return Outcome(name, NEEDS_YOU, f"no open {SCAFFOLD_BRANCH} pull request; rerun to open it")
    number, sha = pull["number"], pull["head"]["sha"]
    jobs = wait_for(
        f"{onboard.CI_WORKFLOW} on #{number}",
        lambda: acknowledge.ci_jobs(ctx.run, ctx.fork, sha),
    )
    # `security review` fails here by design: main has no review script for it to run yet.
    build = (jobs.get("build and test") or {}).get("conclusion")
    if build != "success":
        return Outcome(name, NEEDS_YOU, f"`build and test` on #{number} concluded {build}")
    refusal = scaffold_diff_refusal(ctx, number, sha)
    if refusal:
        return Outcome(
            name,
            NEEDS_YOU,
            f"#{number} is not merged: {refusal}. Read it, and merge by hand with gh pr merge "
            f"{number} --repo {ctx.fork} --merge --admin",
        )
    merge = ["gh", "pr", "merge", str(number), "--repo", ctx.fork, "--merge", "--admin"]
    ctx.run([*merge, "--match-head-commit", sha], None, None)
    merged = gh_api(ctx.run, f"repos/{ctx.fork}/pulls/{number}") or {}
    if not merged.get("merged"):
        raise Refused(f"#{number} did not merge")
    return Outcome(
        name, DID, f"#{number} admin-merged as {merged.get('merge_commit_sha', '')[:12]}"
    )


# ---- 7. first sync -----------------------------------------------------------------------------


def _sync_runs_since(ctx: Context, since: datetime) -> list[dict[str, Any]]:
    """Every `upstream-sync` run created after `since`, newest first."""
    workflow = f"repos/{ctx.fork}/actions/workflows/{acknowledge.SYNC_WORKFLOW}/runs?per_page=100"
    runs: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        listed = (gh_api(ctx.run, f"{workflow}&page={page}") or {}).get("workflow_runs") or []
        newer = [r for r in listed if _instant(r["created_at"]) > since]
        runs += newer
        if len(newer) < len(listed) or len(listed) < 100:
            return runs
    raise Refused(f"{ctx.fork} has more than {MAX_PAGES * 100} sync runs since its scaffold")


def scaffolded_at(ctx: Context) -> datetime:
    """When main first carried the declaration. A sync from before then ran some other lane.

    It is the first commit on main that touched `.github/fork-sync.toml` (the oldest the commits
    API lists for the path). A commit that reached main through a pull request reached it when
    that pull request merged, which is later than the commit itself; one pushed straight to main
    reached it at its own commit date."""
    path = f"repos/{ctx.fork}/commits?sha=main&path={CONFIG_PATH}&per_page=100"
    oldest: dict[str, Any] | None = None
    for page in range(1, MAX_PAGES + 1):
        listed = gh_api(ctx.run, f"{path}&page={page}") or []
        oldest = listed[-1] if listed else oldest
        if len(listed) < 100:
            break
    else:
        raise Refused(f"{CONFIG_PATH} has more than {MAX_PAGES * 100} commits on {ctx.fork} main")
    if oldest is None:
        raise Refused(f"no commit on {ctx.fork} main adds {CONFIG_PATH}")
    pulls = gh_api(ctx.run, f"repos/{ctx.fork}/commits/{oldest['sha']}/pulls") or []
    merged = [_instant(pull["merged_at"]) for pull in pulls if pull.get("merged_at")]
    return min(merged) if merged else _instant(oldest["commit"]["committer"]["date"])


def _instant(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def first_sync(ctx: Context) -> Outcome:
    """Done once any sync that ran from the scaffolded main (created after the declaration
    reached it) succeeded; a later failure is the lane's to report, not onboarding's. With none,
    it waits for one already running, or dispatches one."""
    name = "first sync"
    since = scaffolded_at(ctx)

    def succeeded(runs: list[dict[str, Any]]) -> dict[str, Any] | None:
        return next((r for r in runs if r.get("conclusion") == "success"), None)

    runs = _sync_runs_since(ctx, since)
    if (run := succeeded(runs)) is not None:
        return Outcome(name, DONE, f"{run.get('html_url', 'a sync')} succeeded")
    status = DONE
    if not runs:
        ctx.run(
            ["gh", "workflow", "run", acknowledge.SYNC_WORKFLOW, "--repo", ctx.fork], None, None
        )
        status = DID
    elif all(r.get("status") == "completed" for r in runs):
        return _sync_failed(ctx, runs[0])

    def finished() -> list[dict[str, Any]] | None:
        current = _sync_runs_since(ctx, since)
        settled = current and all(r.get("status") == "completed" for r in current)
        return current if succeeded(current) or settled else None

    runs = wait_for(f"{acknowledge.SYNC_WORKFLOW} on {ctx.fork}", finished)
    if succeeded(runs) is None:
        return _sync_failed(ctx, runs[0])
    pull = _open_pull(ctx.run, ctx.fork, acknowledge.SYNC_BRANCH)
    detail = f"sync pull request #{pull['number']}" if pull else "no sync pull request open"
    return Outcome(name, status, detail)


def _sync_failed(ctx: Context, workflow_run: dict[str, Any]) -> Outcome:
    return Outcome(
        "first sync",
        NEEDS_YOU,
        f"the sync concluded {workflow_run.get('conclusion')} "
        f"({workflow_run.get('html_url', '')}); fix it, run gh workflow run "
        f"{acknowledge.SYNC_WORKFLOW} --repo {ctx.fork}, and rerun",
    )


# ---- 8. protect --------------------------------------------------------------------------------


def _protected(ctx: Context) -> bool:
    repo = gh_api(ctx.run, f"repos/{ctx.fork}") or {}
    steps = [*checks.repository_steps(repo, None)[1:], checks.protection_step(ctx.run, ctx.fork)]
    return all(step.status == checks.OK for step in steps)


def protect(ctx: Context) -> Outcome:
    name = "main protected"
    if _protected(ctx):
        return Outcome(name, DONE)
    pull = _open_pull(ctx.run, ctx.fork, acknowledge.SYNC_BRANCH)
    if pull is None:
        # The first sync succeeded and opened nothing: upstream has no newer release. The policy
        # and App Brain steps don't depend on protection, so they run meanwhile.
        return Outcome(
            name,
            WAITING,
            "waiting for the next upstream release; protection needs a real pull request to see "
            "both required checks report",
            blocking=False,
        )
    number, sha = pull["number"], pull["head"]["sha"]
    jobs = wait_for(
        f"{onboard.CI_WORKFLOW} on #{number}",
        lambda: acknowledge.ci_jobs(ctx.run, ctx.fork, sha),
    )
    unreported = [check for check in REQUIRED_CHECKS if check not in jobs]
    if unreported:
        return Outcome(name, NEEDS_YOU, f"#{number}'s CI run has no {', '.join(unreported)} job")
    proven = onboard.protect(ctx.run, ctx.fork)
    return Outcome(name, DID, f"both checks required (reported on #{proven})")


# ---- 9. the first review (a person's) ----------------------------------------------------------


def ack_command(fork: str, number: int) -> str:
    """The dry run a person reads before accepting anything; it changes nothing."""
    return shlex.join(["fork-sync", "ack", fork, str(number), "--by", "<who>", "--reason", "<why>"])


def first_review(ctx: Context) -> Outcome:
    """Onboarding never approves a finding: a held review stops here with the dry run to read."""
    name = "first review"
    pull = _open_pull(ctx.run, ctx.fork, acknowledge.SYNC_BRANCH)
    if pull is None:
        return Outcome(name, DONE, "no sync pull request is open")
    number, sha = pull["number"], pull["head"]["sha"]
    jobs = wait_for(
        f"{onboard.CI_WORKFLOW} on #{number}",
        lambda: acknowledge.ci_jobs(ctx.run, ctx.fork, sha),
    )
    conclusion = (jobs.get("security review") or {}).get("conclusion")
    if conclusion == "success":
        return Outcome(name, DONE, f"#{number} passed `security review`")
    review = acknowledge.read_review(ctx.run, ctx.fork, number)
    if review.head != sha[:12]:
        return Outcome(name, WAITING, f"#{number}'s review comment is not yet of its head")
    if review.unavailable:
        return Outcome(
            name, NEEDS_YOU, f"#{number}'s review could not run; re-run its `security review` job"
        )
    if not review.failing:
        return Outcome(name, NEEDS_YOU, f"`security review` on #{number} concluded {conclusion}")
    return Outcome(
        name,
        NEEDS_YOU,
        f"#{number}'s review holds {len(review.failing)} finding(s). Read them, and acknowledge "
        f"only what you accept:\n  {ack_command(ctx.fork, number)}",
    )


# ---- 10. the landing policy --------------------------------------------------------------------


def _serves(document: dict[str, Any], fork: str, version: int) -> bool:
    """Whether a policy document lists the fork, at `version` or later."""
    listed = {r.lower() for r in document["inert_landing"].get("repositories", [])}
    return fork.lower() in listed and document["version"] >= version


def _while_deploying[T](probe: Callable[[], T | None]) -> Callable[[], T | None]:
    """A probe of change-manager that reads "not yet" while its container is being replaced, when
    it may not answer at all."""

    def tolerant() -> T | None:
        try:
            return probe()
        except EstateReadError:
            return None

    return tolerant


def release_reason(text: str) -> str:
    """`text` as the release script accepts a reason: no quote, backslash or control character
    (each becomes a space), whitespace collapsed, and no run longer than `REASON_RUN` unbroken."""
    cleaned = "".join(" " if c in '"\\' or not c.isprintable() else c for c in text)
    words = [
        word[i : i + REASON_RUN]
        for word in cleaned.split()
        for i in range(0, len(word), REASON_RUN)
    ]
    return " ".join(words)


_CURRENT_VERSION = re.compile(r"^CURRENT_VERSION: Final = (\d+)$", re.MULTILINE)


def policy_version(source: str) -> int:
    """The version in force in change-manager's `app/deploy_policy.py`, read from its text: the
    module is never imported here."""
    found = _CURRENT_VERSION.findall(source)
    if len(found) != 1:
        raise Refused(f"{POLICY_SOURCE} does not declare exactly one CURRENT_VERSION")
    return int(found[0])


def policy_diff_refusal(changed: set[str]) -> str:
    """Why a policy change must not be committed or merged, or "" when it may: it changes only
    the files the release script writes, and both `app/` files among them."""
    if changed - POLICY_FILES or not POLICY_REQUIRED <= changed:
        return f"it changes {sorted(changed)}, not the release script's {sorted(POLICY_FILES)}"
    return ""


@dataclass(frozen=True)
class Release:
    status: str
    """`released` (a new version was written) or `already_present` (nothing was)."""
    version: int


def parse_release(stdout: str, repository: str, current: int) -> Release:
    """The release script's one JSON line, read strictly: exactly the three keys, a known status,
    the repository asked for, and the version: `current + 1` for `released`, `current` for
    `already_present`, where `current` is the version in force in the source it ran on."""
    lines = stdout.strip().splitlines()
    try:
        answer = json.loads(lines[0]) if len(lines) == 1 else None
    except ValueError:
        answer = None
    shown = repr(stdout.strip()[:200])
    if not isinstance(answer, dict) or set(answer) != {"status", "version", "repository"}:
        raise Refused(f"{RELEASE_SCRIPT} printed {shown}, not one release line")
    status, version, named = answer["status"], answer["version"], answer["repository"]
    if status not in (RELEASED, ALREADY_PRESENT) or type(version) is not int:
        raise Refused(f"{RELEASE_SCRIPT} printed {shown}, not a release it describes")
    # GitHub resolves a repository's name in any case.
    if not isinstance(named, str) or named.lower() != repository.lower():
        raise Refused(f"{RELEASE_SCRIPT} released {named!r}, not {repository}")
    expected = current + 1 if status == RELEASED else current
    if version != expected:
        raise Refused(f"{RELEASE_SCRIPT} answered {status} at version {version}, not {expected}")
    return Release(status, version)


def _release(ctx: Context, clone: Path, current: int) -> Release:
    """Run change-manager's release script for the fork. It exits 0 for both outcomes; any other
    exit, 2 included (uv's and Python's own usage exit), is a failure."""
    uv = shutil.which("uv") or str(Path.home() / ".local/bin/uv")
    reason = release_reason(
        f"{ctx.fork} joins the fork-sync lane (orchestrator ADR-0056), released by "
        f"fork-sync onboard on {ctx.today.isoformat()}"
    )
    argv = [
        uv,
        "run",
        "python",
        RELEASE_SCRIPT,
        "--repository",
        ctx.fork.lower(),
        "--reason",
        reason,
    ]
    try:
        stdout = ctx.run(argv, None, clone)
    except CommandFailed as failure:
        raise Refused(
            f"{RELEASE_SCRIPT} exited {failure.returncode}: {failure.stderr.strip()[:300]}"
        ) from None
    return parse_release(stdout, ctx.fork.lower(), current)


def _open_policy_pull(ctx: Context, branch: str) -> tuple[int, str, int] | None:
    """A pull request an earlier run opened: its number, head, and the version it releases, which
    must be the one after main's."""
    pull = _open_pull(ctx.run, POLICY_REPO, branch)
    if pull is None:
        return None
    number, sha = pull["number"], pull["head"]["sha"]
    at = f"repos/{POLICY_REPO}/contents/{POLICY_SOURCE}?ref="
    main, head = (gh_api(ctx.run, at + ref, raw=True) or "" for ref in ("main", sha))
    version, released = policy_version(main), policy_version(head)
    if released != version + 1:
        raise Refused(f"{POLICY_REPO}#{number} releases version {released}, not {version + 1}")
    return number, sha, released


def _release_pull(ctx: Context, branch: str) -> tuple[int, str, int] | Release:
    """Run the release script in a clone, check its change, and open its pull request; or, when
    the fork is already in the source's current version, that answer."""
    with tempfile.TemporaryDirectory() as scratch:
        clone = onboard.clone_fork(ctx.run, POLICY_REPO, Path(scratch), "--depth", "1")
        # The source can be ahead of what is served (merged, not yet deployed), so the version
        # the script must write is read from the source it runs on.
        current = policy_version((clone / POLICY_SOURCE).read_text(encoding="utf-8"))
        release = _release(ctx, clone, current)
        if release.status == ALREADY_PRESENT:
            return release
        # Untracked paths are listed too, so debris refuses as surely as a stray edit.
        status = ctx.run(["git", "status", "--porcelain"], None, clone)
        changed = {line[3:] for line in status.splitlines() if line}
        refusal = policy_diff_refusal(changed)
        if refusal:
            raise Refused(f"{RELEASE_SCRIPT}'s change is not committed: {refusal}")
        title = f"Landing policy v{release.version}: admit {ctx.fork} to the inert lane"
        sha = _push_branch(ctx.run, clone, branch, sorted(changed), title)
        body = (
            f"Generated by `fork-sync onboard` with `{RELEASE_SCRIPT}`: version {release.version} "
            f"lists {ctx.fork.lower()} in `inert_landing.repositories` (orchestrator ADR-0056 "
            "amendment 5)."
        )
        argv = ["gh", "pr", "create", "--repo", POLICY_REPO, "--base", "main", "--head", branch]
        url = ctx.run([*argv, "--title", title, "--body", body], None, clone)
    return _number(url), sha, release.version


def landing_policy(ctx: Context) -> Outcome:
    name = "change-manager inert landing policy"
    document = ctx.estate.policy()
    step = checks.policy_step(ctx.fork, lambda: document["inert_landing"])
    if step.status == checks.OK:
        return Outcome(name, DONE, f"version {document['version']}")
    if _serves(document, ctx.fork, 0):
        return Outcome(name, NEEDS_YOU, f"the fork is listed, but {step.detail}")
    _in_place(ctx)
    branch = f"fork-sync-inert-{ctx.fork.split('/')[1].lower()}"
    opened = _open_policy_pull(ctx, branch) or _release_pull(ctx, branch)
    if isinstance(opened, Release):
        # Already in change-manager's source: merged earlier, perhaps not yet deployed.
        _wait_served(ctx, opened.version)
        return Outcome(name, DONE, f"{ctx.fork.lower()} was already released (v{opened.version})")
    number, sha, version = opened
    files = gh_api(ctx.run, f"repos/{POLICY_REPO}/pulls/{number}/files?per_page=100") or []
    refusal = policy_diff_refusal({item["filename"] for item in files})
    if refusal:
        raise Refused(f"{POLICY_REPO}#{number} is open and unmerged: {refusal}")
    merged = land_pull(ctx.run, POLICY_REPO, number, sha)
    served = _wait_served(ctx, version)
    try:
        running = f"; running {ctx.estate.revision()[:12]}"
    except EstateReadError:
        running = ""
    return Outcome(
        name,
        DID,
        f"{POLICY_REPO}#{number} merged as {merged[:12]}; version {served} served{running}",
    )


def _wait_served(ctx: Context, version: int) -> int:
    """Wait until change-manager serves `version` or later with the fork, deciding each poll on
    the one document it read. Returns the version served."""

    def serving() -> int | None:
        document = ctx.estate.policy()
        return document["version"] if _serves(document, ctx.fork, version) else None

    return wait_for(f"change-manager serving version {version}", _while_deploying(serving))


# ---- 11. App Brain (an agent's) ----------------------------------------------------------------


def app_brain(ctx: Context) -> Outcome:
    name = "App Brain landing record"
    workflows = sorted(_workflows(ctx.run, f"repos/{ctx.fork}/contents/.github/workflows?ref=main"))
    evidence = (
        f"{ctx.fork} is on the fork-sync lane (orchestrator ADR-0056): main's workflows are "
        f"{', '.join(workflows)}, and none deploys on push: upstream-sync.yml opens a pull "
        "request, ci.yml builds, tests and reviews, fork-sync-audit.yml audits. Installs happen "
        "by tool_installer."
    )
    call = (
        f'record_default_branch_landing(github_repo="{ctx.fork}", landing="inert", '
        f"evidence={json.dumps(evidence)})"
    )
    if ctx.estate.landing is None:
        return Outcome(
            name,
            NEEDS_YOU,
            f"an agent session records it (no credential outside one):\n  {call}\n"
            "then rerun with FORK_SYNC_APP_BRAIN_URL and FORK_SYNC_APP_BRAIN_KEY set to confirm",
        )
    answer = ctx.estate.landing(ctx.fork)
    if answer == "inert":
        return Outcome(name, DONE)
    return Outcome(name, NEEDS_YOU, f"App Brain answers {answer}; an agent session runs:\n  {call}")


# ---- 12 and 13 ---------------------------------------------------------------------------------


def installer_row(ctx: Context) -> Outcome:
    step = checks.tool_step(ctx.fork)
    if step.status == checks.OK:
        return Outcome("tool_installer row", DONE, step.detail)
    return Outcome(
        "tool_installer row",
        DONE,
        "none; if this machine installs the fork as a tool, add its row in "
        "src/tool_installer/tools.py as its own change",
    )


def confirm(ctx: Context) -> Outcome:
    policy = ctx.estate.policy
    steps = checks.check(ctx.run, ctx.fork, lambda: policy()["inert_landing"], ctx.estate.landing)
    summary = "\n".join(
        f"  {step.status:8} {step.name}" + (f" -- {step.detail}" if step.detail else "")
        for step in steps
    )
    missing = [step for step in steps if step.status == checks.MISSING]
    return Outcome(CONFIRM, NEEDS_YOU if missing else DONE, summary)


# ---- the run -----------------------------------------------------------------------------------

Step = Callable[[Context], Outcome]


def onboard_fork(
    run: Runner,
    fork: str,
    config_path: Path,
    estate: Estate,
    *,
    baseline_tag: str | None = None,
    discard_fork_commits: bool = False,
    reset_secrets: bool = False,
    today: date | None = None,
    report: Callable[[Outcome], None] = lambda outcome: None,
) -> list[Outcome]:
    """Every step in the runbook's order, until one is waiting or needs a person."""
    text = config_path.read_text(encoding="utf-8")
    config = parse_config(text)
    repo = gh_api(run, f"repos/{fork}")
    if repo is None:
        raise Refused(f"{fork} does not exist or is not visible")
    parent = onboard.parent_of(run, fork)
    if parent.lower() != config.upstream.lower():
        raise Refused(f"{fork} is a fork of {parent}, but the declaration names {config.upstream}")
    ctx = Context(
        run,
        repo.get("full_name") or fork,
        parent,
        text,
        config,
        estate,
        config_path,
        baseline_tag,
        discard_fork_commits,
        reset_secrets,
        today or date.today(),
    )
    steps: list[Step] = [
        app_installed,
        baseline,
        scaffold,
        secrets,
        registry,
        merge_scaffold,
        first_sync,
        protect,
        first_review,
        landing_policy,
        app_brain,
        installer_row,
        confirm,
    ]
    outcomes: list[Outcome] = []
    for step in steps:
        outcome = attempt(step, ctx)
        outcomes.append(outcome)
        report(outcome)
        if outcome.status in (WAITING, NEEDS_YOU) and outcome.blocking:
            break
    return outcomes


def finished(outcomes: list[Outcome]) -> bool:
    """Whether a run onboarded the fork: it reached the last step and every step is done."""
    return outcomes[-1].step == CONFIRM and all(o.status in (DONE, DID) for o in outcomes)


def attempt(step: Step, ctx: Context) -> Outcome:
    name = getattr(step, "__name__", "step").replace("_", " ")
    try:
        return step(ctx)
    except Waiting as waiting:
        return Outcome(name, WAITING, str(waiting))
    # Each refusal is a RuntimeError (`Refused`, `CommandFailed`, `bws.Unavailable`,
    # `EstateReadError`), and says why.
    except RuntimeError as error:
        return Outcome(name, NEEDS_YOU, f"refused: {error}")
    # Output in a shape nothing here expected: JSON that doesn't parse, a key or type missing, a
    # declaration or registry that doesn't parse. Only the error's type is named: the message
    # could carry the payload, which is GitHub's or upstream's text.
    except (ValueError, KeyError, TypeError) as error:
        return Outcome(
            name, NEEDS_YOU, f"refused: {name} read output it can't use ({type(error).__name__})"
        )
