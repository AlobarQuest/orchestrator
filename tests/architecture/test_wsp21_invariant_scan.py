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
SHELL_SOURCES = sorted(SCRIPTS.glob("*.sh"))


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


@pytest.mark.parametrize("source", [*PYTHON_SOURCES, *SHELL_SOURCES], ids=lambda p: str(p))
def test_nothing_in_the_repo_merges_a_pull_request(source: Path) -> None:
    text = source.read_text(encoding="utf-8").lower()
    for action in MERGE_ACTIONS:
        assert action not in text, (
            f"{source} performs a merge ({action!r}). Merging is Devon's gate; "
            "code that merges has removed it."
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
    Path("src/orchestrator/services/knowledge_promotions.py"),
    # The reconciliation runner is a SEPARATE program (ADR-0002). Polling GitHub is its entire
    # job, and it may only push what it finds back through two endpoints -- enforced in code by
    # ALLOWED_WRITE_ENDPOINTS and in tests by test_reconciliation_runner_isolation.py. It is not
    # the orchestrator, and its egress is not the orchestrator's.
    Path("src/reconciliation_runner/client.py"),
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
