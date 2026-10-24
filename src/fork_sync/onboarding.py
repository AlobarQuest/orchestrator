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
from datetime import date
from pathlib import Path
from typing import Any, Final

from fork_sync import acknowledge, bws, checks, onboard
from fork_sync.estate import SYNC_APP_ID, EstateReadError
from fork_sync.fork_sync_review import (
    ACKS_PATH,
    CONFIG_PATH,
    ECOSYSTEMS,
    Config,
    ConfigError,
    parse_config,
)
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
RELEASE_ALREADY_PRESENT: Final = 2
"""The release script's exit when the repository is already in the current version."""
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


def bootstrap_lockfile(ctx: Context, tag: str, lockfile: str) -> None:
    """main has no lockfile yet, so `build and test` can't install: resolve one for the baseline
    tag with the review script's own `lock`, exactly as the sync will, and push it to the
    scaffold branch. The script run is the one this tool rendered, checked byte for byte."""
    with tempfile.TemporaryDirectory() as scratch:
        clone = onboard.clone_fork(ctx.run, ctx.fork, Path(scratch), "--branch", SCAFFOLD_BRANCH)
        script = clone / REVIEW_SCRIPT
        if (
            not script.is_file()
            or script.read_bytes() != render(ctx.config.ecosystem)[REVIEW_SCRIPT]
        ):
            raise Refused(f"the scaffold branch's {REVIEW_SCRIPT} is not the one scaffold renders")
        source = f"https://github.com/{ctx.upstream}.git"
        refspec = f"refs/tags/{tag}:refs/tags/{tag}"
        ctx.run(["git", "fetch", "--quiet", "--no-tags", source, refspec], None, clone)
        ctx.run([sys.executable, REVIEW_SCRIPT, "lock", tag], None, clone)
        message = f"fork-sync: resolve {lockfile} for {tag}"
        ctx.run(["git", "commit", "--quiet", "-m", message], None, clone)
        ctx.run(["git", "push", "--quiet", "origin", SCAFFOLD_BRANCH], None, clone)


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


def _latest_sync_run(ctx: Context) -> dict[str, Any] | None:
    path = f"repos/{ctx.fork}/actions/workflows/{acknowledge.SYNC_WORKFLOW}/runs?per_page=1"
    return ((gh_api(ctx.run, path) or {}).get("workflow_runs") or [None])[0]


def first_sync(ctx: Context) -> Outcome:
    name = "first sync"
    latest = _latest_sync_run(ctx)
    status = DONE
    if latest is None:
        ctx.run(
            ["gh", "workflow", "run", acknowledge.SYNC_WORKFLOW, "--repo", ctx.fork], None, None
        )
        status = DID

    def finished() -> dict[str, Any] | None:
        newest = _latest_sync_run(ctx)
        return newest if newest is not None and newest.get("status") == "completed" else None

    finished_run = wait_for(f"{acknowledge.SYNC_WORKFLOW} on {ctx.fork}", finished)
    if finished_run.get("conclusion") != "success":
        return Outcome(
            name,
            NEEDS_YOU,
            f"the sync concluded {finished_run.get('conclusion')} "
            f"({finished_run.get('html_url', '')}); fix it, run gh workflow run "
            f"{acknowledge.SYNC_WORKFLOW} --repo {ctx.fork}, and rerun",
        )
    pull = _open_pull(ctx.run, ctx.fork, acknowledge.SYNC_BRANCH)
    detail = f"sync pull request #{pull['number']}" if pull else "no sync pull request open"
    return Outcome(name, status, detail)


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
        return Outcome(
            name,
            NEEDS_YOU,
            "no sync pull request is open, so neither required check can be seen reporting "
            "(the fork may already be at upstream's newest release); protect after the next "
            "sync opens one",
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


def _served(ctx: Context, version: int | None) -> dict[str, Any] | None:
    document = ctx.estate.policy()
    listed = ctx.fork.lower() in {
        r.lower() for r in document["inert_landing"].get("repositories", [])
    }
    return document if listed and (version is None or document["version"] >= version) else None


def _while_deploying[T](probe: Callable[[], T | None]) -> Callable[[], T | None]:
    """A probe of change-manager that reads "not yet" while its container is being replaced, when
    it may not answer at all."""

    def tolerant() -> T | None:
        try:
            return probe()
        except EstateReadError:
            return None

    return tolerant


def _release(ctx: Context, clone: Path) -> str:
    """Run change-manager's release script for the fork. Returns what it printed on success,
    or "" when the repository is already in the current version."""
    uv = shutil.which("uv") or str(Path.home() / ".local/bin/uv")
    reason = (
        f"{ctx.fork} joins the fork-sync lane (orchestrator ADR-0056), released by "
        f"`fork-sync onboard` on {ctx.today.isoformat()}"
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
        return ctx.run(argv, None, clone).strip()
    except CommandFailed as failure:
        if failure.returncode == RELEASE_ALREADY_PRESENT:
            return ""
        raise Refused(f"{RELEASE_SCRIPT} refused: {failure.stderr.strip()[:300]}") from None


def landing_policy(ctx: Context) -> Outcome:
    name = "change-manager inert landing policy"
    document = ctx.estate.policy()
    step = checks.policy_step(ctx.fork, lambda: document["inert_landing"])
    if step.status == checks.OK:
        return Outcome(name, DONE, f"version {document['version']}")
    if _served(ctx, None) is not None:
        return Outcome(name, NEEDS_YOU, f"the fork is listed, but {step.detail}")
    _in_place(ctx)
    expected = document["version"] + 1
    branch = f"fork-sync-inert-{ctx.fork.split('/')[1].lower()}"
    pull = _open_pull(ctx.run, POLICY_REPO, branch)
    if pull is not None:
        number, sha = pull["number"], pull["head"]["sha"]
    else:
        with tempfile.TemporaryDirectory() as scratch:
            clone = onboard.clone_fork(ctx.run, POLICY_REPO, Path(scratch), "--depth", "1")
            released = _release(ctx, clone)
            if not released:
                # Already in change-manager's source: merged earlier, perhaps not yet deployed.
                serving = _while_deploying(lambda: _served(ctx, None))
                wait_for(f"change-manager serving {ctx.fork}", serving)
                return Outcome(name, DONE, f"{ctx.fork.lower()} was already released")
            if released.split() != [str(expected), ctx.fork.lower()]:
                raise Refused(f"{RELEASE_SCRIPT} printed {released[:200]!r}, not {expected}")
            title = f"Landing policy v{expected}: admit {ctx.fork} to the inert lane"
            # The script edits tracked files only; anything untracked is debris, not policy.
            status = ctx.run(["git", "status", "--porcelain"], None, clone)
            untracked = [line[3:] for line in status.splitlines() if line.startswith("??")]
            if untracked:
                raise Refused(f"{RELEASE_SCRIPT} left untracked files: {untracked[:5]}")
            ctx.run(["git", "checkout", "--quiet", "-b", branch], None, clone)
            ctx.run(["git", "add", "--update"], None, clone)
            ctx.run(["git", "commit", "--quiet", "-m", title], None, clone)
            ctx.run(["git", "push", "--quiet", "origin", branch], None, clone)
            sha = ctx.run(["git", "rev-parse", "HEAD"], None, clone).strip()
            body = (
                f"Generated by `fork-sync onboard` with `{RELEASE_SCRIPT}`: version {expected} "
                f"lists {ctx.fork.lower()} in `inert_landing.repositories` (orchestrator ADR-0056 "
                "amendment 5)."
            )
            argv = ["gh", "pr", "create", "--repo", POLICY_REPO, "--base", "main", "--head", branch]
            url = ctx.run([*argv, "--title", title, "--body", body], None, clone)
        number = _number(url)
    merged = land_pull(ctx.run, POLICY_REPO, number, sha)
    running = _while_deploying(lambda: ctx.estate.revision() == merged or None)
    wait_for(f"change-manager running {merged[:12]}", running)
    serving = _while_deploying(lambda: _served(ctx, expected))
    served = wait_for(f"change-manager serving version {expected}", serving)
    return Outcome(name, DID, f"{POLICY_REPO}#{number} merged; version {served['version']} served")


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
        if outcome.status in (WAITING, NEEDS_YOU):
            break
    return outcomes


def attempt(step: Step, ctx: Context) -> Outcome:
    name = getattr(step, "__name__", "step").replace("_", " ")
    try:
        return step(ctx)
    except Waiting as waiting:
        return Outcome(name, WAITING, str(waiting))
    # Each refusal is a RuntimeError (`Refused`, `CommandFailed`, `bws.Unavailable`,
    # `EstateReadError`); a declaration or registry that doesn't parse is a ValueError of its own.
    except (RuntimeError, ConfigError, tomllib.TOMLDecodeError) as error:
        return Outcome(name, NEEDS_YOU, f"refused: {error}")
