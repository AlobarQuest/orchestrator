"""Architecture guard: one test states what the Membership layer reaches and with which credential.

* `membership-check`: the orchestrator and change-manager routes, the GitHub reads, the `gh` and
  `git` commands it runs, and the credentials its launcher fetches.
* `sds-members`: the one read route (observer) and the one stage route (SYSTEM).

Each row is held to the code: the client's own predicate where it has one, the recorded requests
and commands where it does not, and the launcher's text. Each comparison has a control that shows it
reporting a deliberate widening. As in the other edge tables, a widening to a route outside the
corpus (each listed route, its near misses, and the named probes) is not seen here.

Two facts the table states and does not change:

* `pin_watcher.github.GitHubReader` permits any `GET`; the lane's use of it (`repositories`, one
  route) bounds it, so that use is recorded rather than the predicate.
* `membership_check.declaration` and `.auto_merge` hold plain `httpx` clients with a path pattern
  of their own, so the comparison runs that pattern and a source scan holds them to `GET`.
"""

from __future__ import annotations

import ast
import re
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from types import ModuleType

import httpx
import pytest

from bump_proposer import landing_policy
from fork_sync import watch as fork_watch
from membership_check import auto_merge, declaration
from membership_check import cli as check_cli
from pin_watcher import github as pin_github
from rotation_worker import registry as rotation_registry
from rotation_worker.git_mirror import GITHUB, RepoMirror
from sds_members import cli as members_cli
from sds_members import client as members_client
from tests.architecture.edge_table import (
    DEADMAN,
    GH_AUTH_TOKEN,
    OBSERVER_BEARER,
    READ_BEARER,
    Edge,
    Permits,
    built_clients,
    captured_permits,
    launcher_bearers,
    launcher_text,
    near_misses,
    route_disagreements,
)

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
LAUNCHER = Path("scripts/run-membership-check.sh")
CHECK_SOURCES = sorted(Path("src/membership_check").glob("*.py"))

MEMBERS = "/api/v1/members"
PROPOSALS = "/api/v1/member-proposals"
LANDING_POLICY = "/api/landing-policy"
FACTORY_TARGET = "/repos/o/r/contents/factory-target.toml"
CRED_CONSUMERS = "/repos/o/r/contents/.cred-consumers.toml"
REPOSITORY = "/repos/o/r"
WORKFLOWS = "/repos/o/r/contents/.github/workflows"
WORKFLOW = "/repos/o/r/contents/.github/workflows/ci.yml"

# ---- membership-check: the services it asks directly ----------------------------------------

CHECK_EDGE = Edge(
    change_manager=frozenset({("GET", LANDING_POLICY)}),
    orchestrator=frozenset({("GET", MEMBERS)}),
    # `declaration` (the two contents files, and the repository its absence probe reads) and
    # `auto_merge` (the repository, the workflows directory and each workflow file). The listing
    # (the members and their accounts' public repositories) goes by the pin reader, and the
    # registry files by `gh` and `git`: see below.
    github=frozenset(
        {
            ("GET", FACTORY_TARGET),
            ("GET", CRED_CONSUMERS),
            ("GET", REPOSITORY),
            ("GET", WORKFLOWS),
            ("GET", WORKFLOW),
        }
    ),
    alert_hub=frozenset(),
    # Its own bearers: the observer and change-manager READ credentials, the operator's `gh`
    # login, and the dead-man check's API key and ping (`sds-deadman.sh`).
    bearers=DEADMAN | {OBSERVER_BEARER, READ_BEARER, GH_AUTH_TOKEN},
)

ORCHESTRATOR_PROBES = frozenset(
    {
        PROPOSALS,
        f"{MEMBERS}/44",
        "/api/v1/observations",
        "/api/v1/work-units/44/claim",
        "/api/v1/work-units/44/pr-merge",
        "/api/v1/approvals",
    }
)
CHANGE_MANAGER_PROBES = frozenset(
    {
        "/api/deploy-policy",
        "/api/items",
        "/api/items/44/approve",
        "/api/work-changes",
    }
)
GITHUB_PROBES = frozenset(
    {
        "/user/repos",
        "/repos/o/r/pulls",
        "/repos/o/r/pulls/44/merge",
        "/repos/o/r/contents/other.toml",
        "/repos/o/r/contents/.github",
        "/repos/o/r/contents/.github/workflows/a/b",
        "/repos/o/r/git/refs",
        "/repos/o/r/actions/runs",
        "/repos/o/r/settings",
        "/repos/../..%2fcontents/factory-target.toml",
        "/repos/o/r/contents/factory-target.toml?ref=x",
    }
)
CHECK_ROUTES = frozenset(
    {LANDING_POLICY, MEMBERS, FACTORY_TARGET, CRED_CONSUMERS, REPOSITORY, WORKFLOWS, WORKFLOW}
)
# A repository name and a workflow file name are segments, so "x" appended to either is another
# member of the same route, not a near miss; those two are tried with the other three variants.
TEMPLATED = frozenset({REPOSITORY, WORKFLOW})
CHECK_CORPUS = (
    near_misses(CHECK_ROUTES - TEMPLATED)
    | {path + end for path in TEMPLATED for end in ("", "/", "\n")}
    | ORCHESTRATOR_PROBES
    | CHANGE_MANAGER_PROBES
    | GITHUB_PROBES
)


def _fresh(module: ModuleType, call: Callable[[], object]) -> Permits:
    """The predicate of the client `call` builds, built again on each question, so a patch of the
    module's helpers reaches it."""
    return lambda m, p: captured_permits(module, call)(m, p)


def _read_members() -> object:
    return members_client.read_members(token="t", credential_key_id="k")


def _github_permits(method: str, path: str) -> bool:
    """The two hand-built GitHub readers' own patterns, GET only (a source scan holds the verb)."""
    return method == "GET" and (
        any(pattern.fullmatch(path) for pattern in declaration._ROUTES.values())
        or auto_merge.is_allowed(path)
    )


def _read_landing_policy() -> object:
    return landing_policy.read_landing_policy("t")


CHECK_PERMITS: dict[str, Permits | None] = {
    "change_manager": _fresh(landing_policy, _read_landing_policy),
    "orchestrator": _fresh(members_client, _read_members),
    "github": _github_permits,
    "alert_hub": None,
}


def check_disagreements() -> frozenset[tuple[str, str, str, bool]]:
    return route_disagreements(CHECK_EDGE, CHECK_PERMITS, CHECK_CORPUS, METHODS)


def test_membership_check_accepts_exactly_its_routes_among_the_corpus() -> None:
    assert sorted(check_disagreements()) == []


def test_every_listed_route_is_in_the_corpus() -> None:
    listed = {path for column in CHECK_EDGE.columns().values() for _, path in column}
    assert listed == CHECK_ROUTES


def test_the_hosts_are_the_ones_the_table_names() -> None:
    [orchestrator] = _built(members_client, _read_members)
    [change_manager] = _built(landing_policy, _read_landing_policy)
    [github] = _built(pin_github, lambda: pin_github.GitHubReader(token="t").get("/users/o"))

    assert orchestrator == "https://sds.alobar.net"
    assert change_manager == "https://change-mgr.alobar.net"
    assert github == "https://api.github.com"
    assert declaration.API == auto_merge.API == "https://api.github.com"


def _built(module: ModuleType, call: Callable[[], object]) -> list[str]:
    return [client.base_url for client in built_clients(module, call)]


def test_control_a_widened_orchestrator_predicate_fails_the_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = members_client._permits
    monkeypatch.setattr(
        members_client,
        "_permits",
        lambda route: lambda m, p: (m, p) == ("POST", PROPOSALS) or original(route)(m, p),
    )
    assert ("orchestrator", "POST", PROPOSALS, True) in check_disagreements()


def test_control_a_widened_change_manager_predicate_fails_the_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reader's predicate is an inline lambda; a moved path constant is what a widening is."""
    monkeypatch.setattr(landing_policy, "_PATH", "/api/items")
    found = check_disagreements()
    assert ("change_manager", "GET", "/api/items", True) in found
    assert ("change_manager", "GET", LANDING_POLICY, False) in found


def test_control_a_widened_declaration_pattern_fails_the_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(declaration._ROUTES, "merge", re.compile("/repos/o/r/pulls/44/merge"))
    assert ("github", "GET", "/repos/o/r/pulls/44/merge", True) in check_disagreements()


def test_control_a_widened_auto_merge_pattern_fails_the_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = auto_merge.is_allowed
    monkeypatch.setattr(
        auto_merge, "is_allowed", lambda path: path == "/repos/o/r/pulls" or original(path)
    )
    assert ("github", "GET", "/repos/o/r/pulls", True) in check_disagreements()


# ---- The two hand-built GitHub readers send GET and nothing else ----------------------------

_WRITING_CALLS = frozenset({"post", "put", "patch", "delete", "request", "stream"})


def non_get_calls(source: str) -> list[str]:
    """Each `build_request` whose verb is not the literal "GET", and each writing method call."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr == "build_request":
            verb = node.args[0] if node.args else None
            if not (isinstance(verb, ast.Constant) and verb.value == "GET"):
                found.append(f"build_request at line {node.lineno}")
        elif node.func.attr in _WRITING_CALLS:
            found.append(f"{node.func.attr} at line {node.lineno}")
    return found


@pytest.mark.parametrize("module", [declaration, auto_merge])
def test_a_hand_built_github_reader_builds_only_get_requests(module: ModuleType) -> None:
    source = Path(str(module.__file__)).read_text(encoding="utf-8")
    assert "build_request" in source
    assert non_get_calls(source) == []


def test_control_a_non_get_request_is_reported() -> None:
    source = Path(str(auto_merge.__file__)).read_text(encoding="utf-8")
    post = source.replace('build_request("GET"', 'build_request("POST"', 1)
    patched = source.replace("self._client.send(request)", "self._client.post(path)", 1)

    assert len(non_get_calls(post)) == 1
    assert len(non_get_calls(patched)) == 1


# ---- What else the program runs: the program's own imports ----------------------------------

# Module -> the names `cli.py` takes from it. A new import is a new reach, stated here first.
CLI_IMPORTS = {
    "bump_proposer.landing_policy": {"LandingPolicyError", "read_landing_policy"},
    "bump_proposer.standing": {"StandingError", "discover"},
    "change_proposer.criteria": {"_ROLLBACKS"},
    "deploy_watcher.workflows": {"ROLLOUT_WORKFLOWS"},
    "fork_harden.rules": {"FORKS"},
    "fork_sync.gh": {"CommandFailed", "subprocess_runner"},
    "fork_sync.watch": {"Unreadable", "forks"},
    "membership_check.auto_merge": {"AutoMergeState", "GitHubAutoMergeSource"},
    "membership_check.consistency": {"Scope", "canonical", "check", "factory_targets"},
    "membership_check.declaration": {"GitHubDeclarationSource"},
    "pin_watcher.github": {"GitHubReader", "PinWatcherError", "repositories"},
    "rotation_worker.git_mirror": {"MirrorError", "RepoMirror", "default_mirror_root"},
    "rotation_worker.registry": {
        "REGISTRY_PATH",
        "REGISTRY_REPOSITORIES",
        "RegistryError",
        "rotated_by_sds",
    },
    "rotation_worker.types": {"of_a_type"},
    "sds_members.client": {"Member", "MembersUnreadable", "read_members"},
    "sds_members.populations": {"observed"},
    "tool_installer.tools": {"TOOLS"},
}
LIBRARY_ROOTS = ("collections", "typing", "os", "__future__", "typer")


def project_imports(source: str) -> dict[str, set[str]]:
    """The names each non-library module contributes to this source."""
    found: dict[str, set[str]] = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in LIBRARY_ROOTS:
                continue
            found.setdefault(node.module, set()).update(alias.name for alias in node.names)
    return found


def test_the_cli_takes_exactly_the_names_the_table_states() -> None:
    assert project_imports(Path("src/membership_check/cli.py").read_text("utf-8")) == CLI_IMPORTS


def test_control_a_new_import_changes_what_the_cli_reaches() -> None:
    source = Path("src/membership_check/cli.py").read_text("utf-8")
    added = source.replace(
        "from tool_installer.tools import TOOLS",
        "from tool_installer.tools import TOOLS\nfrom alert_hub_client import KeyedAlerts",
    )
    wider = source.replace(
        "from pin_watcher.github import GitHubReader,",
        "from pin_watcher.github import GitHubReader, comparison,",
    )

    assert project_imports(added)["alert_hub_client"] == {"KeyedAlerts"}
    assert project_imports(wider) != CLI_IMPORTS


_NETWORK_MODULES = frozenset({"httpx", "requests", "urllib", "socket", "subprocess", "http", "ssl"})


def network_importers(sources: dict[str, str]) -> set[str]:
    found: set[str] = set()
    for name, text in sources.items():
        for node in ast.walk(ast.parse(text)):
            modules = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            if any(module.split(".")[0] in _NETWORK_MODULES for module in modules):
                found.add(name)
    return found


def test_only_the_two_github_readers_open_connections_in_the_program() -> None:
    sources = {path.name: path.read_text("utf-8") for path in CHECK_SOURCES}
    assert network_importers(sources) == {"auto_merge.py", "declaration.py"}


def test_control_a_third_network_module_is_reported() -> None:
    sources = {path.name: path.read_text("utf-8") for path in CHECK_SOURCES}
    sources["extra.py"] = "import subprocess\n"
    assert network_importers(sources) == {"auto_merge.py", "declaration.py", "extra.py"}


def test_the_program_sends_no_alert() -> None:
    """The alert is the launcher's outcome alarm; the table's alert-hub column is empty."""
    text = "\n".join(path.read_text("utf-8") for path in CHECK_SOURCES)
    assert "alert_hub" not in text
    assert CHECK_EDGE.alert_hub == frozenset()
    # b415d03b-...: the alert-hub `sds-bridge` sender token
    assert launcher_bearers(LAUNCHER).isdisjoint({"b415d03b-f92d-46b1-8a9d-b4f10103df49"})


# ---- The listing: the pin reader, an account read and its public listing -----------------------


def pin_reader_requests(kind: str = "User") -> list[tuple[str, str]]:
    """The requests `repositories` makes, as the cli calls it, sent to a recording transport."""
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path == "/users/o":
            return httpx.Response(200, json={"login": "o", "type": kind})
        return httpx.Response(200, json=[])

    with pin_github.GitHubReader(token="t", transport=httpx.MockTransport(handler)) as reader:
        check_cli.repositories(reader, ["o/r"])
    return seen


@pytest.mark.parametrize(
    ("kind", "listing"), [("User", "/users/o/repos"), ("Organization", "/orgs/o/repos")]
)
def test_the_listing_reads_each_account_and_its_public_repositories(
    kind: str, listing: str
) -> None:
    assert check_cli.repositories is pin_github.repositories
    assert pin_reader_requests(kind) == [("GET", "/users/o"), ("GET", listing)]


def test_the_pin_readers_predicate_is_any_get_so_its_use_is_the_bound() -> None:
    permits = captured_permits(pin_github, lambda: pin_github.GitHubReader(token="t"))
    assert permits("GET", "/users/o/repos") and permits("GET", "/repos/o/r/pulls")
    assert not permits("POST", "/users/o/repos")


def reader_uses(source: str) -> list[str]:
    """Each way `cli.py` touches a `GitHubReader`: its constructor, and any method on `reader`."""
    uses: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "GitHubReader":
                uses.append("GitHubReader()")
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "reader":
                uses.append(f"reader.{node.attr}")
    return uses


def test_the_cli_uses_its_pin_reader_only_to_list_repositories() -> None:
    source = Path("src/membership_check/cli.py").read_text("utf-8")
    assert reader_uses(source) == ["GitHubReader()"]
    assert "repositories(reader, observed_members)" in source


def test_control_a_second_reader_use_is_reported() -> None:
    source = Path("src/membership_check/cli.py").read_text("utf-8")
    wider = source.replace(
        "owned = repositories(reader, observed_members)", 'owned = reader.get("/user")'
    )
    assert reader_uses(wider) == ["GitHubReader()", "reader.get"]


def test_control_a_widened_listing_changes_the_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    original = pin_github.repositories

    def wider(reader: pin_github.GitHubReader, members: list[str]) -> list[str]:
        reader.get("/user/orgs")
        return original(reader, members)

    monkeypatch.setattr(check_cli, "repositories", wider)
    assert pin_reader_requests() == [
        ("GET", "/user/orgs"),
        ("GET", "/users/o"),
        ("GET", "/users/o/repos"),
    ]


# ---- The fork list: `gh api` on one file ----------------------------------------------------

REGISTRY_REPO = "AlobarQuest/factory-runner"
FORK_LIST_COMMANDS = [
    [
        "gh",
        "api",
        "-H",
        "Accept: application/vnd.github.raw",
        f"repos/{REGISTRY_REPO}/contents/.cred-consumers.toml?ref=main",
    ]
]


def fork_list_commands() -> list[list[str]]:
    commands: list[list[str]] = []

    def run(argv: Sequence[str], stdin: str | None, cwd: Path | None) -> str:
        commands.append(list(argv))
        return f'[[credential]]\nid = "{fork_watch.REGISTRY_CREDENTIAL}"\n'

    check_cli.forks(run)
    return commands


def test_the_fork_list_is_one_gh_read_of_the_factory_runner_registry() -> None:
    assert check_cli.forks is fork_watch.forks
    assert check_cli.subprocess_runner.__module__ == "fork_sync.gh"
    assert fork_list_commands() == FORK_LIST_COMMANDS


def test_control_an_extra_gh_command_changes_the_fork_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = fork_watch.gh_api

    def wider(run: Callable[..., str], path: str, *, raw: bool = False) -> object:
        run(["gh", "api", "-X", "DELETE", "repos/o/r"], None, None)
        return original(run, path, raw=raw)

    monkeypatch.setattr(fork_watch, "gh_api", wider)
    assert fork_list_commands() != FORK_LIST_COMMANDS


# ---- The registry mirrors: `git` against four public repositories ---------------------------

GIT_SUBCOMMANDS = {"clone", "config", "fetch", "rev-parse", "show"}


def mirror_commands(tmp_path: Path) -> list[list[str]]:
    """Every command `rotated_by_sds` runs through a mirror, on a runner that sends nothing."""
    commands: list[list[str]] = []
    urls: dict[str, str] = {}

    def run(argv: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
        commands.append(list(argv))
        stdout = b""
        if "clone" in argv:
            Path(argv[-1]).mkdir(parents=True)
            urls[Path(argv[-1]).name.split(".partial")[0]] = argv[-2]
        elif "config" in argv:
            stdout = urls[Path(argv[2]).name].encode()
        elif "rev-parse" in argv:
            stdout = b"a" * 40
        return subprocess.CompletedProcess(argv, 0, stdout, b"")

    rotation_registry.rotated_by_sds(RepoMirror(tmp_path, run))
    return commands


def subcommand(argv: Sequence[str]) -> str:
    rest = list(argv[1:])
    return rest[2] if rest[:1] == ["-C"] else rest[0]


def test_the_registry_mirrors_clone_and_read_the_four_registry_repositories(
    tmp_path: Path,
) -> None:
    commands = mirror_commands(tmp_path)

    assert {command[0] for command in commands} == {"git"}
    assert {subcommand(command) for command in commands} == GIT_SUBCOMMANDS
    assert sorted(command[-2] for command in commands if "clone" in command) == sorted(
        f"{GITHUB}/{repository}.git" for repository in rotation_registry.REGISTRY_REPOSITORIES
    )
    assert GITHUB == "https://github.com"
    assert [c[-2:] for c in commands if "fetch" in c][0] == [
        "origin",
        "+refs/heads/main:refs/heads/main",
    ]


def test_control_another_repository_or_command_changes_the_mirror_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = mirror_commands(tmp_path / "a")
    monkeypatch.setattr(
        rotation_registry,
        "REGISTRY_REPOSITORIES",
        (*rotation_registry.REGISTRY_REPOSITORIES, "AlobarQuest/other"),
    )
    wider = mirror_commands(tmp_path / "b")
    clone_urls = {command[-2] for command in wider if "clone" in command}

    assert f"{GITHUB}/AlobarQuest/other.git" in clone_urls
    assert len(wider) > len(baseline)

    original = RepoMirror.sync

    def pushing(self: RepoMirror, repository: str) -> str:
        self._git(repository, self._path(repository), "push")
        return original(self, repository)

    monkeypatch.setattr(RepoMirror, "sync", pushing)
    assert "push" in {subcommand(command) for command in mirror_commands(tmp_path / "c")}


# ---- The launcher: which credentials it fetches, and into which variable --------------------


def test_the_launcher_fetches_exactly_its_bearers() -> None:
    assert launcher_bearers(LAUNCHER) == CHECK_EDGE.bearers


def test_each_credential_goes_into_the_variable_the_program_reads() -> None:
    text = LAUNCHER.read_text("utf-8")
    assert check_cli.OBSERVER_TOKEN_VARIABLE == "SDS_MEMBERS_OBSERVER_TOKEN"
    assert check_cli.CHANGE_MANAGER_TOKEN_VARIABLE == "SDS_MEMBERS_CHANGE_MANAGER_TOKEN"
    assert check_cli.GITHUB_TOKEN_VARIABLE == "SDS_MEMBERS_GITHUB_TOKEN"
    assert f'OBSERVER_BEARER_UUID="{OBSERVER_BEARER}"' in text
    assert f'CHANGE_MANAGER_READ_UUID="{READ_BEARER}"' in text
    assert re.search(
        r'SDS_MEMBERS_OBSERVER_TOKEN="\$\(sds_bws_value "\$OBSERVER_BEARER_UUID" '
        r'"\$NARROW_IDENTITY"\)"',
        text,
    )
    assert re.search(
        r'SDS_MEMBERS_CHANGE_MANAGER_TOKEN="\$\(sds_bws_value "\$CHANGE_MANAGER_READ_UUID" '
        r'"\$BROAD_IDENTITY"\)"',
        text,
    )
    assert "SDS_MEMBERS_GITHUB_TOKEN" in text and "gh auth token" in text
    assert "export SDS_MEMBERS_OBSERVER_TOKEN SDS_MEMBERS_CHANGE_MANAGER_TOKEN" in text


def test_the_launcher_reads_its_two_bws_identities_from_the_keychain() -> None:
    accounts = set(re.findall(r"-a '(BWS_ACCESS_TOKEN[A-Z_]*)'", launcher_text(LAUNCHER)))
    assert accounts == {"BWS_ACCESS_TOKEN_SDS", "BWS_ACCESS_TOKEN_VPS_BACKUP"}


def test_the_observer_key_id_is_the_one_the_member_reader_names() -> None:
    assert check_cli.OBSERVER_KEY_ID == members_client.OBSERVER_KEY_ID == "orchestrator-observer"


def test_control_an_extra_bearer_changes_what_the_launcher_fetches(tmp_path: Path) -> None:
    changed = tmp_path / "run-membership-check.sh"
    # 221a48d5-...: the orchestrator-system SYSTEM bearer
    changed.write_text(
        LAUNCHER.read_text("utf-8")
        + '\nsds_bws_value "221a48d5-3f29-4898-b300-b4820140c880" "$BROAD_IDENTITY"\n',
        encoding="utf-8",
    )
    # The sourced helpers are found beside the script, so copy what it sources.
    for sourced in ("sds-deadman.sh", "sds-bws.sh"):
        (tmp_path / sourced).write_text(Path("scripts", sourced).read_text("utf-8"), "utf-8")

    assert launcher_bearers(changed) - CHECK_EDGE.bearers == {
        "221a48d5-3f29-4898-b300-b4820140c880"
    }


# ---- sds-members: one read as the observer, one stage as SYSTEM -----------------------------

STAGE_EDGE = Edge(
    change_manager=frozenset(),
    orchestrator=frozenset({("POST", PROPOSALS)}),
    github=frozenset(),
    alert_hub=frozenset(),
    bearers=frozenset(),
)
READ_EDGE = Edge(
    change_manager=frozenset(),
    orchestrator=frozenset({("GET", MEMBERS)}),
    github=frozenset(),
    alert_hub=frozenset(),
    bearers=frozenset(),
)


def _stage_client() -> object:
    return members_client.stage(token="t", credential_key_id="k", body={}, idempotency_key="i")


MEMBERS_CORPUS = (
    near_misses({MEMBERS, PROPOSALS})
    | ORCHESTRATOR_PROBES
    | {
        f"{PROPOSALS}/44",
        "/review/member-proposals/44",
        "/api/v1/member-proposals/44/confirm",
    }
)


def _sds_members_disagreements(
    edge: Edge, call: Callable[[], object]
) -> frozenset[tuple[str, str, str, bool]]:
    permits: dict[str, Permits | None] = {
        "change_manager": None,
        "orchestrator": _fresh(members_client, call),
        "github": None,
        "alert_hub": None,
    }
    return route_disagreements(edge, permits, MEMBERS_CORPUS, METHODS)


def test_the_reader_sends_only_the_member_record_get() -> None:
    assert sorted(_sds_members_disagreements(READ_EDGE, _read_members)) == []


def test_the_stager_sends_only_the_proposal_post() -> None:
    assert sorted(_sds_members_disagreements(STAGE_EDGE, _stage_client)) == []


def test_the_reader_is_the_observer_and_the_stager_is_system() -> None:
    assert members_client.OBSERVER_KEY_ID == "orchestrator-observer"
    assert members_client.OBSERVER_TOKEN_ENV == "SDS_MEMBERS_OBSERVER_TOKEN"
    assert members_cli.KEY_ID == "orchestrator-system"
    assert members_cli.TOKEN_VARIABLE == "SDS_MEMBERS_SYSTEM_TOKEN"
    assert members_client.DEFAULT_BASE_URL == "https://sds.alobar.net"


def test_a_stage_route_and_a_read_route_are_two_clients() -> None:
    """Reading cannot stage: the predicates differ by route, not by one shared allowlist."""
    assert members_client._READ == ("GET", MEMBERS)
    assert members_client._STAGE == ("POST", PROPOSALS)


def stagers(sources: dict[str, str]) -> set[str]:
    """The modules that import `stage` from the member client."""
    return {
        name
        for name, text in sources.items()
        for node in ast.walk(ast.parse(text))
        if isinstance(node, ast.ImportFrom)
        and node.module == "sds_members.client"
        and any(alias.name == "stage" for alias in node.names)
    }


def _repository_sources() -> dict[str, str]:
    files = [*Path("src").rglob("*.py"), *Path("scripts").rglob("*.py")]
    return {path.as_posix(): path.read_text("utf-8") for path in files}


def test_only_the_staging_command_stages() -> None:
    assert stagers(_repository_sources()) == {"src/sds_members/cli.py"}


def test_control_another_stager_is_reported() -> None:
    sources = {
        **_repository_sources(),
        "src/other/x.py": "from sds_members.client import stage\n",
    }
    assert stagers(sources) == {"src/sds_members/cli.py", "src/other/x.py"}


@pytest.mark.parametrize(
    ("edge", "call", "extra"),
    [
        (READ_EDGE, _read_members, ("POST", PROPOSALS)),
        (STAGE_EDGE, _stage_client, ("GET", MEMBERS)),
    ],
)
def test_control_a_widened_member_predicate_fails_the_table(
    monkeypatch: pytest.MonkeyPatch,
    edge: Edge,
    call: Callable[[], object],
    extra: tuple[str, str],
) -> None:
    original = members_client._permits
    monkeypatch.setattr(
        members_client,
        "_permits",
        lambda route: lambda m, p: (m, p) == extra or original(route)(m, p),
    )
    assert ("orchestrator", *extra, True) in _sds_members_disagreements(edge, call)
