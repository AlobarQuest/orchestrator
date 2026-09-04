"""AC-011: the four invariants WS-P2.1 must not have broken, scanned repo-wide.

The repo already guards these per workstream -- WS-3.2 pins the kernel's vocabulary, WS-5.3 pins
the post-deploy routes, WS-6.1 pins observation ingest. Each of those asks "did THIS path stay
clean?". None of them asks "did the WHOLE repo stay clean?", and a workstream that adds a
reconciliation runner, four recovery actions and a detect-pass is exactly the kind of change that
can satisfy every local guard while quietly violating a global one.

So this is a SCAN, not another scope guard:

1. Nothing merges. Not CI, not a script, not a service.
2. Nothing calls out. The orchestrator is push-only: reality arrives as pushed observations, and
   only four files are allowed to speak HTTP at all.
3. Nothing loops. No background thread, no scheduler, no poller (ADR-0002).
4. No worker can complete a unit, and no secret is tracked.
"""

import ast
import re
from pathlib import Path

import pytest

from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.kernel.transitions import EDGE_ROLES

SRC = Path("src")
SCRIPTS = Path("scripts")
PYTHON_SOURCES = sorted(SRC.rglob("*.py"))
SCRIPT_PYTHON_SOURCES = sorted(SCRIPTS.glob("*.py"))
SHELL_SOURCES = sorted(SCRIPTS.glob("*.sh"))

# The merge scan covers `scripts/*.py` as well; the egress and secret scans deliberately do not.
#
# Those scripts have always been outside every merge guard: the string check reads `src/**.py` plus
# `scripts/*.sh`, so a `scripts/land_pr.py` running `gh pr merge` fired nothing while the identical
# code under `src/` reddened. `scripts/` is a plausible home for exactly the ADR-0020 landing code,
# so shipping a residual-gap fix that inherited the same blind spot would have closed one hole
# under a comment claiming both were closed.
#
# It is a SEPARATE list rather than a wider `PYTHON_SOURCES` because four of these scripts import
# `urllib.request`. Widening the shared list would red the outbound scan below and force four new
# OUTBOUND_ALLOWLIST entries -- weakening the structural chokepoint in order to strengthen the
# merge guard, which is a trade in the wrong direction and one this increment is not allowed to
# make. (The secret scan's identical blind spot is left alone here; it is not this change's
# subject.)
MERGE_SCAN_SOURCES = [*PYTHON_SOURCES, *SCRIPT_PYTHON_SOURCES]


# ---------------------------------------------------------------------------------------------
# 1. Nothing merges.
#
# `.github/workflows` is covered by test_no_automatic_merge.py, which also bans workflow-specific
# things (deploy, workflow_dispatch) that are meaningless in source. This is the other half: the
# SOURCE and SCRIPTS must not merge either. Devon's merge gate is the last human checkpoint in the
# factory, and a merge performed by code is a gate that has been removed.
# ---------------------------------------------------------------------------------------------

MERGE_ACTIONS = (
    "gh pr merge",
    "git push origin main",
    "/merge",  # the GitHub REST merge endpoints: PUT /pulls/{n}/merge, POST /merges
)

# ADR-0020 lifts the prohibition for a bounded class, and says it must be lifted OPENLY -- by
# amending this guard with a named exception, never by finding a verb it does not cover. This is
# that exception, and it ships EMPTY: nothing in the repository may land a pull request today, and
# the mechanism is built now, while nothing is entitled to use it, so that its first entry arrives
# into a door already shown to open and to close.
#
# Keyed by exact relative path, the shape OUTBOUND_ALLOWLIST and ws32's WS42_DISPATCH_PATHS
# already use. Every entry carries a reason, and the rot check below refuses one that no longer
# needs the exemption.
MERGE_EXEMPT_PATHS: set[Path] = {
    # ADR-0020's bounded exception, and the FIRST entry this door has ever carried. The module
    # spells the REST endpoint `…/pulls/{n}/merge`, which contains the substring this guard scans
    # for -- it is here because it genuinely lands a pull request, not because a string resembles
    # one. Everything that makes that defensible is outside this file: the criteria were resolved
    # from evidence the orchestrator OBSERVED, with no human adjudication; a human approved the
    # envelope that grants the capability; and the estate says landing on that repository's
    # default branch changes nothing already serving.
    Path("src/orchestrator/services/pr_merge.py"),
    # ADR-0019 Increment 5b, and the SECOND entry -- deliberately its own, because what makes the
    # first defensible does not carry over. There is no work unit here, so no criteria the
    # orchestrator resolved from evidence and no envelope a human approved; and the estate says
    # landing on this repository's default branch DOES change something already serving, which is
    # the opposite of the first entry's last clause.
    #
    # What stands in its place is a change record approved by conformance to a policy version a
    # human pinned, re-checked against the version in force at the moment of the act; the hours
    # that policy declares for changing something already serving; the update bot's own identity;
    # a head current with its base; a permitted version delta; the rollout workflow still being
    # the bytes the record's criteria describe; one landing per repository per window; and an
    # environment switch that defaults to refusing.
    Path("src/orchestrator/services/estate_pr_merge.py"),
    # ADR-0033, the THIRD entry and the first that is not about landing a pull request -- so
    # neither justification above carries over, and the difference is larger than between the
    # two of them. There is no work unit here, so no criteria the orchestrator resolved from
    # evidence and no envelope a human approved; and there is no change record either, because
    # this act happens on the FAR side of the one this producer writes. It publishes a commit
    # directly to a default branch.
    #
    # What stands in their place is that the commit can only ever be one this program wrote:
    # a revision of a package whose author declared `standing = true` -- a declaration only a
    # human author can make -- targeting a repository named in the grant of the
    # `approval-policy.toml` that approved the revision, whose shape that same policy checks
    # exactly. The producer cannot approve the change record it writes (its change-manager
    # bearer is propose-scoped by construction), cannot create work, and cannot dispatch. The
    # estate reports that repository's default branch as `inert`, so nothing already serving
    # changes; and its protection takes a direct push and reports the required checks
    # afterwards whoever performs it, so publishing OBTAINS a verdict rather than skipping one.
    #
    # The producer refuses to begin on a checkout carrying a commit it could not publish, which
    # is what keeps this entry to the one act it names rather than to whatever else has
    # accumulated on that branch.
    Path("src/bump_proposer/standing.py"),
    # ADR-0038 part 2, the FOURTH entry, and it is the second that lands a pull request into a
    # repository where landing changes nothing already serving -- so the first entry's last clause
    # is the only one of its three that carries over, and the second entry's justification does
    # not carry over at all. There is no work unit here, so no criteria the orchestrator resolved
    # from evidence and no envelope a human approved; and there is no change record either, and
    # there cannot be one -- a record exists to carry acceptance criteria and a rollback plan for
    # a rollout, and a repository where landing deploys nothing has no subject for any of the
    # three. So the change window, the pace rule and every record term are absent by decision.
    #
    # What stands in their place is ADR-0038 part 2: a population a human pinned into a versioned
    # policy document held by another service, CONFIRMED against the estate's own answer about
    # what landing on that repository does, with a disagreement refusing in both directions; an
    # author condition read from that same document rather than written here, which is the only
    # thing bounding which pull requests this lane sees at all; a head current with its base; an
    # ecosystem the required checks do exercise; every required check green, told apart from a
    # check that reported nothing and from one still running; and an environment switch of its own
    # that defaults to refusing. What it replaces is a GitHub Actions workflow that armed the
    # platform's own automatic landing across the same six repositories with no freshness
    # condition, no policy version, and no record of the permission in the artifact.
    #
    # **This entry is only real because the module NAMES ITS ACT `merge`.** The scans above find a
    # landing by a REST path spelled in the file or by an attribute call named `merge`, and a
    # landing performed through an injected gateway spells neither -- so a module in this shape
    # could land pull requests and be invisible to the one control that lists every file that
    # does. The gateway method is named for the spelling the guard reads, deliberately, so that
    # the exemption is taken openly rather than avoided by a verb the scanner does not cover.
    Path("src/orchestrator/services/inert_pr_merge.py"),
}


@pytest.mark.parametrize("source", [*MERGE_SCAN_SOURCES, *SHELL_SOURCES], ids=lambda p: str(p))
def test_nothing_in_the_repo_merges_a_pull_request(source: Path) -> None:
    if source in MERGE_EXEMPT_PATHS:
        return
    text = source.read_text(encoding="utf-8").lower()
    for action in MERGE_ACTIONS:
        assert action not in text, (
            f"{source} performs a merge ({action!r}). Merging is Devon's gate; "
            "code that merges has removed it. If this file is the bounded exception ADR-0020 "
            "allows, add it to MERGE_EXEMPT_PATHS with a reason -- openly, never by rewording."
        )


# The residual gap WS-P3.7 measured: a merge performed as a METHOD CALL on a client object --
# `pull_request.merge(...)` -- spells none of the strings above and none of the token sequences the
# ws32/ws33/ws34 scanners look for. Measured 2026-08-08 against all five guard files individually:
# caught by nothing. Structural rather than textual, because the shape has no characteristic
# spelling to match.
#
# SQLAlchemy's `Session.merge()` would fire this too. That is intended and is not a false positive
# to be special-cased: the exemption above is the answer if such a call is ever genuinely needed,
# and today `src/` contains no `.merge(` call of any kind.
def _merge_method_calls(source: Path) -> list[str]:
    tree = ast.parse(source.read_text(encoding="utf-8"))
    return [
        f"{source}:{node.lineno}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "merge"
    ]


@pytest.mark.parametrize("source", MERGE_SCAN_SOURCES, ids=lambda p: str(p))
def test_nothing_in_the_repo_calls_a_merge_method(source: Path) -> None:
    if source in MERGE_EXEMPT_PATHS:
        return
    calls = _merge_method_calls(source)
    assert not calls, (
        f"{calls} calls a .merge() method. Merging is Devon's gate; code that merges has "
        "removed it. If this file is the bounded exception ADR-0020 allows, add it to "
        "MERGE_EXEMPT_PATHS with a reason."
    )


def test_the_merge_exemption_names_only_files_that_need_it() -> None:
    """An exemption nobody needs is an exemption nobody is watching -- the same rot check the
    outbound allowlist carries, and the reason this one can ship empty without going stale."""
    missing = [str(path) for path in MERGE_EXEMPT_PATHS if not path.exists()]
    assert not missing, f"the merge exemption names files that no longer exist: {missing}"

    unused = [
        str(path)
        for path in MERGE_EXEMPT_PATHS
        if not any(action in path.read_text(encoding="utf-8").lower() for action in MERGE_ACTIONS)
        and not (path.suffix == ".py" and _merge_method_calls(path))
    ]
    assert not unused, (
        f"these files are exempt from the merge guard but no longer merge anything: {unused}. "
        "Remove them -- an exemption nobody needs is an exemption nobody is watching."
    )


# ---------------------------------------------------------------------------------------------
# 2. Nothing calls out.
#
# The orchestrator is PUSH-ONLY. External reality arrives as observations POSTed to it; it does
# not go and look. That is what makes the observation, release, post-deploy and reconciliation
# paths auditable -- there is no hidden second source of truth being fetched behind them.
#
# Four files legitimately speak HTTP, and each is a deliberate outbound EGRESS, not an ingest:
# the CLI (an operator's client), dispatch (fires the runner workflow), the GitHub App (mints its
# own installation token), and knowledge promotion (pushes an approved proposal to a brain).
# Anything else that imports an HTTP client has invented a fetch path.
# ---------------------------------------------------------------------------------------------

# The capability is FETCHING, not the package name. `urllib.parse` is string manipulation --
# `urlsplit`/`urlunsplit`, which the observation services use to normalise a URL they were GIVEN --
# and banning the top-level `urllib` flagged both of them for doing no such thing. A guard that
# fires on the wrong thing gets an exemption bolted on, and the exemption is what actually rots.
HTTP_CLIENTS = {
    "httpx",
    "requests",
    "urllib3",
    "aiohttp",
    "urllib.request",
    "urllib.error",
    "http.client",
}

OUTBOUND_ALLOWLIST = {
    Path("src/orchestrator/cli.py"),
    Path("src/orchestrator/services/dispatch.py"),
    Path("src/orchestrator/services/github_app.py"),
    # WS-P2.20. Named-check evidence is only worth anything if the orchestrator saw the result
    # itself, so this file READS one thing from GitHub -- how a named job concluded on a PR head
    # -- and writes nothing. It is not a new outbound capability: it borrows github_app.py's
    # installation token, and the alternative (an out-of-process poller, ADR-0002's shape) would
    # put the observation outside the transaction that records it.
    Path("src/orchestrator/services/github_checks.py"),
    Path("src/orchestrator/services/knowledge_promotions.py"),
    # WS-P2.28. Admission asks App Brain one question about the unit's target repository -- does
    # landing on its default branch change something already serving -- and writes nothing. Same
    # justification as github_checks.py above: a declaration is only worth checking if the
    # orchestrator saw the estate's own answer, and the out-of-process alternative (ADR-0002's
    # shape) would put that answer outside the transaction that records the admission decision.
    # The credential is READ-ONLY and App Brain scopes it to two read paths.
    Path("src/orchestrator/services/estate_landing.py"),
    # ADR-0019 Increment 3. Admission asks change-manager one question about the pull request it
    # would land -- has this change been routed through the estate's record, and did somebody
    # approve it -- and writes nothing. Same justification as the two above: the answer decides an
    # admission term, so it must be inside the transaction that records the decision, and the
    # out-of-process alternative (ADR-0002's shape) would put it outside. It reaches exactly one
    # listing route and holds a bearer that can read change records; the fact that the same shared
    # secret could also approve one is change-manager's to narrow, and is recorded in ADR-0019
    # rather than implied here.
    Path("src/orchestrator/services/change_record.py"),
    # ADR-0020 Increment 4b. The one genuinely MUTATING egress this repository has: it reads one
    # pull request and asks for it to be landed, naming the head the criteria were adjudicated at
    # so the remote refuses any other. It borrows the same App installation token the workflow
    # trigger and the named-check observer use, and speaks to nothing else.
    Path("src/orchestrator/services/pr_merge.py"),
    # ADR-0019 Increment 5b. The SECOND mutating egress, and the more consequential one: it lands
    # into a repository where landing changes something already serving. Four calls -- the pull
    # request, how far its head is behind its base, the object name of the rollout workflow at
    # that base, and the landing itself -- of which one changes anything, and every one names the
    # head the terms were evaluated against so the remote refuses any other. Same App installation
    # token as the three readers above. The reads are here rather than in an out-of-process poller
    # for the reason the readers above give: every one of them decides an admission term, and an
    # answer obtained outside the transaction that records the decision is an answer about a
    # moment that has passed.
    Path("src/orchestrator/services/estate_pr_merge.py"),
    # ADR-0038 part 2. It reads the policy naming which repositories a person declared landable
    # unattended -- ONE request, to the same service and with the same bearer as the change-record
    # reader above, whose own entry states why an admission term's read belongs inside the
    # transaction that records the decision. It writes nothing and reaches one route.
    Path("src/orchestrator/services/inert_landing_policy.py"),
    # ADR-0026. `work_carrier` is a SEPARATE program (ADR-0002's shape), out of process and on a
    # schedule, so this is not the orchestrator speaking HTTP. It makes ONE request here -- a
    # listing of the work proposals a human approved in change-manager -- and holds no write
    # path to that service at all, which is what keeps a carry from approving the proposal it is
    # carrying.
    Path("src/work_carrier/change_manager.py"),
    # ADR-0028. `bump_proposer` is likewise a SEPARATE, scheduled program. It makes exactly two
    # kinds of request here -- a listing of the work records it has already made, and one
    # proposal -- and its own allowlist refuses every other path, including every route that
    # could move a record's status. That bound is what keeps the producer on the far side of
    # the human decision it exists to prompt.
    Path("src/bump_proposer/change_manager.py"),
    # ADR-0038. The other half of the same program, and a SEPARATE file because it is a separate
    # credential. It makes ONE request -- change-manager's landing policy, the declaration of
    # which repositories land unattended and on what terms -- with a READ-scoped bearer, on every
    # pass including a dry one. It takes no path argument at all, so unlike the module above it
    # needs no allowlist to be confined to one route. The split is what preserves the launcher's
    # own property: reading the rule a dry run reports against does not touch the credential that
    # could write.
    Path("src/bump_proposer/landing_policy.py"),
    # ADR-0027. The other half of the same program: the intake registration that completes the
    # carry. ONE write, `POST /api/v1/package-intakes`, enforced in code by `is_allowed_write`
    # and in tests by test_work_carrier_isolation.py. It composes no decision -- every rule about
    # what may be registered is evaluated inside the orchestrator, in the transaction that
    # records it -- and the payload it sends is the emitter's own bytes, unedited.
    Path("src/work_carrier/orchestrator_client.py"),
    # ADR-0029. `work_watcher` is the work lane's watcher, a SEPARATE program that shares the
    # carry's invocation and runs before it. TWO files, one route each. The change-manager
    # one is the only MUTATING egress either work-lane program has, and it is one-directional
    # by construction: its single route can reach `resolved` and no other status, so a bug
    # here stops work a person approved and cannot cause any. Its scope permits more than its
    # allowlist does -- `POST /api/deploy-changes` among it -- and
    # test_work_watcher_isolation.py is the control for that gap.
    Path("src/work_watcher/change_manager.py"),
    # The other half: the read that establishes the fact. The completion rule is derived
    # inside the orchestrator (ADR-0029) and relayed here, so this program composes nothing
    # and writes nothing to the system that owns the work.
    Path("src/work_watcher/orchestrator_client.py"),
    # ADR-0019 Increment 5b. `estate_lander` is a SEPARATE program (ADR-0002's shape), and its
    # egress is not the orchestrator's. It reads which changes the estate routed, asks the
    # orchestrator whether each may be landed, and relays the answer -- composing nothing, because
    # every term is evaluated inside the orchestrator in the transaction that records the act.
    # Its whole surface is two routes, enforced in code by `is_allowed_read`/`is_allowed_write`
    # and in tests by test_estate_lander_isolation.py.
    Path("src/estate_lander/orchestrator_client.py"),
    # ADR-0038 part 2a. `inert_lander` is the SIBLING separate program, and its egress is not
    # the orchestrator's either. It reads which repositories a person declared ones where landing
    # on the default branch changes nothing already serving, asks the orchestrator whether each
    # open update-bot pull request may be landed, and relays the answer -- composing nothing.
    # Its whole surface is three routes, enforced in code by `is_allowed_read`/`is_allowed_write`
    # and in tests by test_inert_lander_isolation.py, and it deliberately cannot reach the
    # estate lane's, whose population lands into something already serving.
    Path("src/inert_lander/orchestrator_client.py"),
    # The reconciliation runner is a SEPARATE program (ADR-0002). Polling GitHub is its entire
    # job, and it may only push what it finds back through two endpoints -- enforced in code by
    # ALLOWED_WRITE_ENDPOINTS and in tests by test_reconciliation_runner_isolation.py. It is not
    # the orchestrator, and its egress is not the orchestrator's.
    Path("src/reconciliation_runner/client.py"),
    # The WS-P2.7 tracker projection adapter is a SEPARATE program (ADR-0003), the same
    # report-only-runner shape as ADR-0002. It reads canonical state and projects it onto Todoist;
    # projecting is its entire job. It shares no import path with src/orchestrator/, and its write
    # surface is the two-endpoint, both-report-only allowlist enforced in code by
    # _is_allowed_write in orchestrator_client.py and in tests by
    # test_tracker_projection_adapter_isolation.py. Its egress is not the orchestrator's.
    Path("src/tracker_projection_adapter/orchestrator_client.py"),
    Path("src/tracker_projection_adapter/tracker.py"),
    # WS-P3.6 Increment 2. The landing ledger is a SEPARATE program, the same report-only shape as
    # ADR-0002. It reads GitHub -- which commits reached a default branch, and what can be observed
    # about how each got there -- and records one observation per landing. Its GitHub half refuses
    # any method but GET, and its orchestrator half may write to exactly one endpoint, the OBSERVER
    # role's whole write surface; both are enforced in code and pinned by
    # test_landing_ledger_isolation.py. Its egress is not the orchestrator's.
    Path("src/landing_ledger/github.py"),
    Path("src/landing_ledger/orchestrator_client.py"),
    # ADR-0019 increment 2. The rollout watcher is a SEPARATE program, the same report-only shape
    # as ADR-0002 -- it reads GitHub for the workflow run a landing caused and appends one
    # observation to the change record change-manager holds. Its GitHub half refuses any method
    # but GET; its change-manager half may write to exactly one route and read exactly two, and
    # reaches neither the execution lifecycle nor the decision routes.
    # ADR-0022 ADDED A THIRD EGRESS FILE, and with it the orchestrator itself -- which this entry
    # used to say the watcher did not speak to at all. A rollout it observes may belong to a WORK
    # UNIT, and the traceability chain's observation hop is unit-scoped, so the watcher is the one
    # producer positioned to fill it. That half writes to exactly one endpoint (the OBSERVER role's
    # whole write surface) and reads exactly one path (the unit history that CONFIRMS the claim a
    # commit trailer makes). All three are enforced in code and pinned by
    # test_deploy_watcher_isolation.py. Its egress is not the orchestrator's -- it is a client of
    # it, from outside the process, holding a credential that can do nothing else.
    Path("src/deploy_watcher/change_manager.py"),
    Path("src/deploy_watcher/github.py"),
    Path("src/deploy_watcher/orchestrator.py"),
    # ADR-0019 increment 4. The change PRODUCER is a SEPARATE program again, and the narrowest
    # one yet: it reads GitHub for the open pull requests that would land on a repository where
    # landing redeploys, and writes to exactly ONE change-manager route -- the proposal ingress.
    # It cannot approve, claim, post an outcome, hand off, sync, or move any record's status, and
    # that bound is enforced twice over: by the credential's scope at change-manager, and here in
    # code before a request is built. Its egress is not the orchestrator's.
    Path("src/change_proposer/change_manager.py"),
    # ADR-0030. The machine-activation sweep is a SEPARATE program (ADR-0002's shape), out of
    # process and on a clock. Its subject is local git on the operator machine, which it reads
    # through a subcommand allowlist that makes `pull` unreachable rather than merely unused --
    # so the ONLY thing it speaks HTTP to is the orchestrator, and only to file what it found.
    # One endpoint, the OBSERVER role's whole write surface, and no read surface at all;
    # enforced in code by `is_allowed_write` and pinned by test_activation_sweep_isolation.py.
    # Its egress is not the orchestrator's.
    Path("src/activation_sweep/orchestrator_client.py"),
    # ADR-0030's OTHER lane, in the same program and deliberately not in the same module. Binding
    # a release artifact for a unit this machine has activated needs the SYSTEM credential and a
    # read, where the sweep above needs OBSERVER and no read at all -- so a second confined
    # surface rather than a widening of the first, which is what keeps either from quietly
    # acquiring the other's reach. Two paths, one GET and one POST, enforced in code by
    # `is_allowed_read` / `is_allowed_write`. Its egress is not the orchestrator's.
    Path("src/activation_sweep/binding_client.py"),
    # The pin watcher READS GitHub, and that is the point rather than an incidental dependency:
    # a caller's pin is a fact about a remote repository's workflow file, so no local surface can
    # answer it. Read-only by construction -- the client's single entry point refuses anything but
    # GET -- and it addresses only factory-runner and repositories it found by asking GitHub which
    # ones carry a caller. Its egress is not the orchestrator's.
    Path("src/pin_watcher/github.py"),
    # The pin watcher's write half, and the activation sweep's client deliberately copied rather
    # than imported: lanes share DOMAIN knowledge, never plumbing, so a sibling's refactor cannot
    # break this lane's schedule. One endpoint, the OBSERVER role's whole write surface, no read
    # surface at all; enforced by `is_allowed_write` and pinned by test_pin_watcher_isolation.py.
    # Its egress is not the orchestrator's.
    Path("src/pin_watcher/orchestrator_client.py"),
}


def _imported_modules(source: Path) -> set[str]:
    """Every module name a file imports, at full dotted depth AND at top level.

    Both, because the two guards below need different granularities: an HTTP client must be
    distinguished from its harmless sibling (`urllib.request` vs `urllib.parse`), while a
    scheduler is disqualifying whatever you import from it.
    """
    tree = ast.parse(source.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules | {module.split(".")[0] for module in modules}


def test_only_the_allowlisted_files_can_speak_http() -> None:
    """The repo-wide outbound scan. The FIRST one -- every existing guard is path-scoped."""
    offenders = {
        str(source): sorted(_imported_modules(source) & HTTP_CLIENTS)
        for source in PYTHON_SOURCES
        if source not in OUTBOUND_ALLOWLIST and _imported_modules(source) & HTTP_CLIENTS
    }
    assert not offenders, (
        f"these files import an HTTP client but are not allowed to call out: {offenders}. "
        "The orchestrator is push-only; reality arrives as pushed observations. If a new egress "
        "is genuinely needed, add it to OUTBOUND_ALLOWLIST deliberately and say why."
    )


def test_the_allowlist_names_only_files_that_exist() -> None:
    """An allowlist that has rotted is an allowlist that stopped guarding anything."""
    missing = [str(path) for path in OUTBOUND_ALLOWLIST if not path.exists()]
    assert not missing, f"the outbound allowlist names files that no longer exist: {missing}"

    unused = [
        str(path) for path in OUTBOUND_ALLOWLIST if not _imported_modules(path) & HTTP_CLIENTS
    ]
    assert not unused, (
        f"these files are allowed to call out but no longer do: {unused}. Remove them from the "
        "allowlist -- an exemption nobody needs is an exemption nobody is watching."
    )


# ---------------------------------------------------------------------------------------------
# 3. Nothing loops.
#
# ADR-0002 chose a separate report-only runner precisely so the orchestrator would stay loop-free:
# it answers requests and returns. A background thread or scheduler inside it would be a second,
# invisible actor mutating state on a timer -- unattributable in the ledger, and impossible to
# reason about during an incident.
# ---------------------------------------------------------------------------------------------

# Importing a scheduler is disqualifying on its own -- there is no innocent reason to have one.
SCHEDULERS = {"apscheduler", "celery", "schedule", "sched", "multiprocessing"}

# But `threading` is NOT: github_app.py uses threading.Lock to guard its cached installation
# token, which is a mutex, not a worker. What must never appear is something that STARTS a
# concurrent actor. So the check is on what is constructed, not on what is imported.
BACKGROUND_STARTERS = {"Thread", "Process", "Timer", "ThreadPoolExecutor", "ProcessPoolExecutor"}


def test_the_orchestrator_imports_no_scheduler() -> None:
    offenders = {
        str(source): sorted(_imported_modules(source) & SCHEDULERS)
        for source in SRC.rglob("*.py")
        if _imported_modules(source) & SCHEDULERS
    }
    assert not offenders, (
        f"these files import a scheduler: {offenders}. The orchestrator answers requests and "
        "returns; reconciliation runs as a separate program (ADR-0002)."
    )


def test_the_orchestrator_starts_no_background_worker() -> None:
    """A background actor would mutate state on a timer -- unattributable in the ledger, and
    impossible to reason about during an incident. A LOCK is fine; a THREAD is not."""
    offenders: list[str] = []
    for source in SRC.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else node.func.id
                if isinstance(node.func, ast.Name)
                else None
            )
            if name in BACKGROUND_STARTERS:
                offenders.append(f"{source}:{node.lineno} starts a {name}")
    assert not offenders, offenders


def test_the_orchestrator_runs_no_polling_loop() -> None:
    """`while True` is the shape a poller takes when someone did not want to import a scheduler."""
    offenders: list[str] = []
    for source in SRC.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.While) and (
                isinstance(node.test, ast.Constant) and node.test.value is True
            ):
                offenders.append(f"{source}:{node.lineno}")
    assert not offenders, f"unbounded loops found: {offenders}"


# ---------------------------------------------------------------------------------------------
# 4. No worker completes anything, and no secret is tracked.
# ---------------------------------------------------------------------------------------------


def test_no_worker_edge_reaches_completed() -> None:
    """The recovery actions added by WS-P2.1 must not have handed a worker a way to finish its own
    work. Completion is adjudicated and then gated by a human; a worker that could complete could
    mark its own homework."""
    worker_completions = [
        f"{source} -> {target}"
        for (source, target), roles in EDGE_ROLES.items()
        if target is WorkUnitState.COMPLETED and ActorRole.WORKER in roles
    ]
    assert not worker_completions, worker_completions


# A BWS token is `0.` + uuid + secret:key. Matched by SHAPE, never by example -- writing a literal
# one into a tracked file is exactly what this test exists to prevent, and the repo's write-guard
# hook would (correctly) refuse to save this file if it contained one.
SECRET_SHAPES = (
    re.compile(r"\b0\.[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\."),
    re.compile(r"\bghp_[A-Za-z0-9]{36}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b"),
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
)


@pytest.mark.parametrize("source", [*PYTHON_SOURCES, *SHELL_SOURCES], ids=lambda p: str(p))
def test_no_tracked_source_carries_a_secret(source: Path) -> None:
    text = source.read_text(encoding="utf-8")
    for shape in SECRET_SHAPES:
        assert not shape.search(text), (
            f"{source} contains something shaped like a live credential. If it is real, it is "
            "LEAKED -- rotate it; deleting it is not enough."
        )
