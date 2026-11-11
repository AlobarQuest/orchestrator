"""Architecture guard: one test states what the Lane runtime layer's components reach.

* `alert_hub_client`: the one route its client's predicate permits (`POST /v1/alerts`).
* `estate_clients.confined`: one `httpx.Client`, redirects off unless a caller asks.
* `scripts/deploy_orchestrator.py`: the two HTTP hosts it names (production and the lane phases'
  Healthchecks listing), the requests and commands it
  makes when its three phases run, and the programs it can start.
* The shell helpers `sds-deadman.sh`, `sds-install.sh`, `sds-bws.sh` and `sds-token.sh`: the
  external commands, literal hosts and sourced scripts each one names, and where `launchctl`
  appears.
* `standing_identity`, `compute_image_tags`, `sds_scope`, `build_registry_bundle` and
  `shape_registry_context`: what network and processes each can reach.

Each row is held to the code, not to a second statement of it: the client's own predicate, the
requests and commands a recording `World` sees, the text and tree of each script. Each has a control
that shows the row reporting a deliberate widening.

What this cannot see, stated so a green run is not read as more than it is:

* The shell reader (`edge_table.read_shell`) names the head of each simple command as the text
  spells it. A command assembled in a variable, run through a wrapper other than `env`, or through
  `eval`, and anything inside a here-document body, is not seen. Backticks and `$((` fail loudly.
  The row compares the spelling, so `"$_SDS_BWS_PYTHON"` is a command here, not its program.
* The deploy command's `gh` and `git` argv is built at run time; the row compares what the recorded
  paths ran. A branch no test of the command drives is not recorded, so the static program set
  (`gh`, `git`, `bws`, `/usr/bin/security`, the binder) bounds the programs and the recorded set
  bounds the subcommands.
* A route outside the corpus (each listed route, its near misses, and the named probes) is not
  probed in the alert-hub row, as in the other edge tables.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

import alert_hub_client
from estate_clients import confined
from scripts import deploy_orchestrator as deploy
from tests.architecture.edge_table import (
    ALERT_HUB_PERMITS,
    Permits,
    ShellUnplaced,
    captured_permits,
    literal_hosts,
    near_misses,
    read_shell,
    script_network,
    script_processes,
    sendable,
)
from tests.scripts import test_deploy_orchestrator as deploy_tests

# The harness the deploy command's own tests use: a real throwaway git origin, a fake production
# and a fake `gh` and binder. Re-exported by name so pytest finds the fixtures here.
repo = deploy_tests.repo
h = deploy_tests.h

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
SCRIPTS = Path("scripts")
HEALTHCHECKS = "https://healthcheck.alobar.net"


# ---- alert_hub_client ---------------------------------------------------------------------------

ALERT_CORPUS = near_misses(
    {"/v1/alerts", "/v1/alerts/abc", "/v1/alerts/abc/ack", "/v1/settings", "/v1/rules", "/"}
)


def alert_routes(permits: Permits) -> frozenset[tuple[str, str]]:
    return frozenset(
        (method, path)
        for method in METHODS
        for path in ALERT_CORPUS
        if sendable(permits, method, path)
    )


def test_the_alert_client_sends_one_route_to_one_host() -> None:
    assert alert_hub_client.BASE_URL == "https://alerts.alobar.net"
    assert alert_routes(ALERT_HUB_PERMITS) == {("POST", "/v1/alerts")}


FIRE_ONCE_PERMITS = captured_permits(
    alert_hub_client,
    lambda: alert_hub_client.fire_once(
        "token",
        "edges:key",
        alert_hub_client.Alert("t"),
        user_agent="edges/1",
        timeout=1.0,
    ),
)


def test_fire_once_sends_the_same_one_route() -> None:
    assert alert_routes(FIRE_ONCE_PERMITS) == {("POST", "/v1/alerts")}


def test_control_a_widened_fire_once_predicate_fails_the_row() -> None:
    def wider(method: str, path: str) -> bool:
        return FIRE_ONCE_PERMITS(method, path) or method == "GET"

    assert ("GET", "/v1/alerts") in alert_routes(wider)


def test_control_a_widened_alert_predicate_fails_the_row() -> None:
    def wider(method: str, path: str) -> bool:
        return ALERT_HUB_PERMITS(method, path) or method == "GET"

    assert ("GET", "/v1/alerts") in alert_routes(wider)


def test_control_a_moved_alert_route_fails_the_row(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(alert_hub_client, "ALERTS", "/v1/alerts/abc")
    assert alert_routes(ALERT_HUB_PERMITS) != {("POST", "/v1/alerts")}


# ---- estate_clients.confined --------------------------------------------------------------------

CONFINED = Path("src/estate_clients/confined.py")


def calls(text: str, dotted: str) -> int:
    return sum(
        isinstance(node, ast.Call) and ast.unparse(node.func) == dotted
        for node in ast.walk(ast.parse(text))
    )


def redirect_default(text: str) -> object:
    """The default of `ConfinedClient.__init__`'s `follow_redirects`."""
    [init] = [
        node
        for cls in ast.walk(ast.parse(text))
        if isinstance(cls, ast.ClassDef) and cls.name == "ConfinedClient"
        for node in cls.body
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    ]
    names = [argument.arg for argument in init.args.kwonlyargs]
    defaults = [d.value if isinstance(d, ast.Constant) else d for d in init.args.kw_defaults]
    return dict(zip(names, defaults, strict=True))["follow_redirects"]


def test_the_shared_transport_builds_one_client_with_redirects_off() -> None:
    text = CONFINED.read_text(encoding="utf-8")
    assert calls(text, "httpx.Client") == 1
    assert redirect_default(text) is False


def test_the_built_client_follows_no_redirect_unless_asked() -> None:
    plain = confined.ConfinedClient(
        base_url="https://example.invalid",
        user_agent="edges/1",
        headers={},
        timeout=1.0,
        permits=lambda method, path: False,
        refuse=lambda method, path: RuntimeError(path),
    )
    asked = confined.ConfinedClient(
        base_url="https://example.invalid",
        user_agent="edges/1",
        headers={},
        timeout=1.0,
        permits=lambda method, path: False,
        refuse=lambda method, path: RuntimeError(path),
        follow_redirects=True,
    )

    assert plain._client.follow_redirects is False
    assert asked._client.follow_redirects is True


def test_no_caller_asks_for_redirects() -> None:
    asking = [
        path.as_posix()
        for root in (Path("src"), SCRIPTS)
        for path in root.rglob("*.py")
        if "follow_redirects=True" in path.read_text(encoding="utf-8")
    ]
    assert asking == []


def test_control_a_second_client_or_a_redirecting_default_fails_the_row() -> None:
    text = CONFINED.read_text(encoding="utf-8")
    assert calls(text + "\nx = httpx.Client()\n", "httpx.Client") == 2
    widened = text.replace("follow_redirects: bool = False", "follow_redirects: bool = True")
    assert redirect_default(widened) is True


# ---- scripts/deploy_orchestrator.py -------------------------------------------------------------

DEPLOY = SCRIPTS / "deploy_orchestrator.py"
SDS = "https://sds.alobar.net"
# The lane phases' quiet-slot read (ADR-0059): the check listing, with the read-only key.
HEALTHCHECKS = "https://healthcheck.alobar.net"
BINDER = "image-release"

# The programs the code spells, as `process_scan` reads them; `str(world.binder)` is the binder.
DEPLOY_PROGRAMS = frozenset({"/usr/bin/security", "bws", "gh", "git", "str(world.binder)"})
# What the recorded paths ran. `gh` is held to its first two words, `git` to its subcommand.
DEPLOY_COMMANDS = frozenset(
    {
        "/usr/bin/security",
        "bws",
        BINDER,
        "gh run list",
        "gh run view",
        "gh workflow run",
        "git cat-file",
        "git diff",
        "git fetch",
        "git merge-base",
        "git rev-parse",
        "git show",
    }
)
# What the three phases ask production; the unit id in a history path is a placeholder.
DEPLOY_REQUESTS = frozenset(
    {
        ("GET", "/api/v1/status-ledger"),
        ("GET", "/api/v1/work-units/{id}/history"),
        ("GET", "/health/live"),
        ("GET", "/health/ready"),
        ("GET", "/openapi.json"),
    }
)


def command_head(argv: list[str], binder: Path | None = None) -> str:
    if binder is not None and argv[0] == str(binder):
        return BINDER
    if argv[0] == "gh":
        return " ".join(argv[:3])
    if argv[0] == "git":
        return f"git {argv[3]}"  # argv is ["git", "-C", <checkout>, <subcommand>, ...]
    return argv[0]


def normalized(method: str, path: str) -> tuple[str, str]:
    if path.startswith("/api/v1/work-units/") and path.endswith("/history"):
        return method, "/api/v1/work-units/{id}/history"
    return method, path


def string_hosts(text: str) -> frozenset[str]:
    """Every host in a string constant of the module, docstrings included: say less."""
    return frozenset(
        host
        for node in ast.walk(ast.parse(text))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        for host in literal_hosts(node.value)
    )


class Recorder:
    """The commands a `World` was asked to run, as `command_head` spells them."""

    def __init__(self) -> None:
        self.commands: set[str] = set()

    def wrap(self, world: deploy.World) -> deploy.World:
        inner = world.run

        def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            self.commands.add(command_head(argv, world.binder))
            return inner(argv, **kwargs)

        world.run = run
        return world


@pytest.fixture
def driven(
    h: deploy_tests.Harness, monkeypatch: pytest.MonkeyPatch
) -> tuple[Recorder, set[tuple[str, str]]]:
    """All three phases against the harness, then the two credential reads, every seam recording."""
    recorder = Recorder()
    make = h.world
    h.world = lambda: recorder.wrap(make())

    # A ready unit makes the command read its history.
    h.production.ledger = [deploy_tests._ledger_row("ready")]
    assert h.prepare(h.repo.migrated) == deploy_tests.EXIT_OK
    h.production.ready = deploy_tests.DRIFT_503
    assert deploy.main(["recheck", "--state", str(h.state_path)], h.world()) == deploy_tests.EXIT_OK
    h.production.ready = (200, {"status": "ok"})
    h.production.live = [h.repo.base, h.repo.base, h.repo.migrated]
    assert h.finish() == deploy_tests.EXIT_OK

    def credential_read(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        recorder.commands.add(command_head(argv))
        return subprocess.CompletedProcess(argv, 0, '{"value": "v"}', "")

    monkeypatch.delenv("BWS_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(deploy, "_run", credential_read)
    deploy._bws_value("00000000-0000-0000-0000-000000000000")

    requests = {normalized(r.method, r.url.path) for r in h.production.requests}
    assert {r.url.host for r in h.production.requests} == {"sds.alobar.net"}
    return recorder, requests


def test_the_deploy_command_names_two_hosts_and_one_client() -> None:
    text = DEPLOY.read_text(encoding="utf-8")
    assert string_hosts(text) == {SDS, HEALTHCHECKS}
    assert script_network(DEPLOY) == {"httpx"}
    assert deploy.API_BASE == SDS
    assert deploy.HEALTHCHECKS_LISTING == f"{HEALTHCHECKS}/api/v3/checks/"


def test_the_deploy_command_starts_exactly_its_programs() -> None:
    assert script_processes(DEPLOY) == DEPLOY_PROGRAMS


def test_the_deploy_command_runs_exactly_its_commands(
    driven: tuple[Recorder, set[tuple[str, str]]],
) -> None:
    recorder, _ = driven
    assert recorder.commands == DEPLOY_COMMANDS


def test_the_deploy_command_asks_production_exactly_its_requests(
    driven: tuple[Recorder, set[tuple[str, str]]],
) -> None:
    _, requests = driven
    assert requests == DEPLOY_REQUESTS


def test_the_keychain_read_is_the_security_binary_by_absolute_path() -> None:
    seen: list[list[str]] = []

    def run(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "token\n", "")

    original = deploy._run
    deploy._run = run
    try:
        assert deploy._keychain_access_token() == "token"
    finally:
        deploy._run = original
    assert [argv[0] for argv in seen] == ["/usr/bin/security"]


def test_control_a_merge_command_is_seen_by_the_recorder_and_fails_the_row() -> None:
    recorder = Recorder()
    world = recorder.wrap(deploy.World(run=lambda argv, **_: subprocess.CompletedProcess(argv, 0)))

    world.run(["gh", "pr", "merge", "12", "--squash"])

    assert recorder.commands - DEPLOY_COMMANDS == {"gh pr merge"}


def test_control_a_second_host_or_a_second_program_fails_the_row(tmp_path: Path) -> None:
    text = DEPLOY.read_text(encoding="utf-8")
    assert string_hosts(text + '\nOTHER = "https://other.example/x"\n') != {SDS, HEALTHCHECKS}
    widened = tmp_path / "deploy_orchestrator.py"
    widened.write_text(text + '\nimport requests\nsubprocess.run(["curl", "x"])\n')
    assert script_network(widened) != {"httpx"}
    assert script_processes(widened) != DEPLOY_PROGRAMS


# ---- the shell helpers --------------------------------------------------------------------------

PYTHON = '"$_SDS_BWS_PYTHON"'
SECURITY = "/usr/bin/security"

# command heads (functions the file defines and builtins excluded), literal hosts, sourced scripts.
SHELL_ROWS: dict[str, tuple[frozenset[str], frozenset[str], frozenset[str]]] = {
    # Reaches `bws` only through `sds_bws_value`, which `sds-bws.sh` defines.
    "sds-deadman.sh": (
        frozenset({PYTHON, SECURITY, "cat", "curl", "dirname", "mktemp", "rm", "sds_bws_value"})
        | {"tee"},
        frozenset({HEALTHCHECKS}),
        frozenset({"sds-bws.sh"}),
    ),
    "sds-install.sh": (
        frozenset({SECURITY, "curl", "dirname", "git", "sds_bws_value"}),
        frozenset({HEALTHCHECKS}),
        frozenset({"sds-bws.sh"}),
    ),
    "sds-bws.sh": (frozenset({PYTHON, "bws", "dirname", "env"}), frozenset(), frozenset()),
    "sds-token.sh": (frozenset({SECURITY}), frozenset(), frozenset()),
}


def shell_disagreement(name: str, text: str) -> list[str]:
    commands, hosts, sources = SHELL_ROWS[name]
    found = read_shell(text)
    return [
        f"{label}: {sorted(got ^ want)}"
        for label, got, want in (
            ("commands", found.commands, commands),
            ("hosts", found.hosts, hosts),
            ("sources", found.sources, sources),
        )
        if got != want
    ]


@pytest.mark.parametrize("name", sorted(SHELL_ROWS))
def test_each_shell_helper_names_exactly_its_commands_hosts_and_sources(name: str) -> None:
    assert shell_disagreement(name, (SCRIPTS / name).read_text(encoding="utf-8")) == []


# Each spelling of "one more command", so the reader is shown to see it wherever one can sit.
INJECTIONS = {
    "a line": "\nwget https://other.example/x\n",
    "a command substitution": '\nx="$(wget https://other.example/x)"\n',
    "an env wrapper": "\nenv -u A B=1 wget https://other.example/x\n",
    "a case arm": '\ncase "$1" in\n  a) wget https://other.example/x ;;\nesac\n',
    "a pipeline": "\necho hi | wget https://other.example/x\n",
    "an if branch": "\nif true; then wget https://other.example/x; fi\n",
    "a trap handler": "\ntrap 'wget https://other.example/x' EXIT\n",
    "after a here-document": "\ncat <<EOF\nbody\nEOF\nwget https://other.example/x\n",
    "a subshell": "\n(wget https://other.example/x)\n",
}


@pytest.mark.parametrize("name", sorted(SHELL_ROWS))
@pytest.mark.parametrize("where", sorted(INJECTIONS))
def test_control_an_added_command_fails_each_shell_row(name: str, where: str) -> None:
    text = (SCRIPTS / name).read_text(encoding="utf-8") + INJECTIONS[where]
    found = read_shell(text)
    assert "wget" in found.commands
    assert "https://other.example" in found.hosts
    assert shell_disagreement(name, text) != []


def test_control_an_added_source_fails_the_row() -> None:
    text = (SCRIPTS / "sds-token.sh").read_text(encoding="utf-8") + '\nsource "$D/sds-bws.sh"\n'
    assert shell_disagreement("sds-token.sh", text) == ["sources: ['sds-bws.sh']"]


def test_the_reader_ignores_what_is_not_a_command() -> None:
    quiet = (
        'echo "wget https://a.example"  # wget https://b.example\n'
        "printf '%s' 'wget' >/dev/null\n"
        "x=$(( 1 + 1 )) 2>/dev/null || true\n"
    )
    # Arithmetic is refused rather than guessed at, and the rest names nothing.
    with pytest.raises(ShellUnplaced):
        read_shell(quiet)
    found = read_shell(quiet.rsplit("x=", 1)[0])
    # A host spelled in a quoted argument is named; one in a comment is not.
    assert found.commands == frozenset() and found.hosts == {"https://a.example"}


@pytest.mark.parametrize("text", ["x=`wget y`", "echo $(( 1 ))"])
def test_the_reader_refuses_what_it_cannot_place(text: str) -> None:
    with pytest.raises(ShellUnplaced):
        read_shell(text)


def launchctl_scripts() -> frozenset[str]:
    return frozenset(
        path.name
        for path in SCRIPTS.glob("*.sh")
        if "launchctl" in read_shell(path.read_text(encoding="utf-8")).commands
    )


def test_launchctl_appears_in_the_installers_alone() -> None:
    installers = frozenset(path.name for path in SCRIPTS.glob("install-*.sh"))
    assert installers
    assert launchctl_scripts() == installers


def test_control_a_launcher_that_reaches_launchctl_is_seen(tmp_path: Path) -> None:
    text = (SCRIPTS / "run-membership-check.sh").read_text(encoding="utf-8")
    assert "launchctl" not in read_shell(text).commands
    assert "launchctl" in read_shell(text + "\nlaunchctl kickstart x\n").commands


# ---- the Python components with no, or one, outward reach ---------------------------------------


def package_files(*paths: str) -> list[Path]:
    return sorted(
        found
        for path in map(Path, paths)
        for found in (path.rglob("*.py") if path.is_dir() else [path])
    )


INERT = {
    "standing_identity": package_files("src/standing_identity"),
    "compute_image_tags": package_files("scripts/compute_image_tags.py"),
    "sds_scope": package_files("scripts/sds_scope.py"),
}


@pytest.mark.parametrize("component", sorted(INERT))
def test_each_inert_component_has_no_network_and_no_process(component: str) -> None:
    for path in INERT[component]:
        assert script_network(path) == frozenset(), path
        assert script_processes(path) == frozenset(), path


def test_control_a_network_import_or_a_process_fails_the_inert_rows(tmp_path: Path) -> None:
    widened = tmp_path / "widened.py"
    widened.write_text("import httpx\nimport subprocess\nsubprocess.run(['ls'])\n")
    assert script_network(widened) == {"httpx"}
    assert script_processes(widened) == {"ls"}


def first_party_imports(path: Path) -> dict[str, frozenset[str]]:
    """What a file takes from the estate's own packages, by module (not the standard library)."""
    own = {entry.name.removesuffix(".py") for entry in Path("src").iterdir()}
    found: dict[str, set[str]] = {}
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] in own:
            found.setdefault(node.module, set()).update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in own:
                    found.setdefault(alias.name, set()).add("*")
    return {module: frozenset(names) for module, names in found.items()}


SDS_SCOPE = SCRIPTS / "sds_scope.py"
SDS_SCOPE_BORROWS = {
    "sds_members.client": frozenset({"SERVICES", "MembersUnreadable", "read_members"}),
    "sds_members.populations": frozenset(
        {"app_repositories", "bindable", "granted", "observed", "working_copies"}
    ),
}


def test_sds_scope_reads_the_members_and_nothing_else() -> None:
    assert first_party_imports(SDS_SCOPE) == SDS_SCOPE_BORROWS


def test_control_a_wider_borrowing_fails_the_sds_scope_row(tmp_path: Path) -> None:
    widened = tmp_path / "sds_scope.py"
    widened.write_text(
        SDS_SCOPE.read_text(encoding="utf-8")
        + "\nfrom estate_clients.confined import ConfinedClient\n"
    )
    assert first_party_imports(widened) != SDS_SCOPE_BORROWS


GIT_ONLY = {
    "build_registry_bundle": (
        SCRIPTS / "build_registry_bundle.py",
        {"git", "subprocess.check_output"},
    ),
    "shape_registry_context": (SCRIPTS / "shape_registry_context.py", {"git"}),
}
SUBPROCESS_CALLS = frozenset({"run", "check_output", "check_call", "call", "Popen"})


def subprocess_programs(text: str) -> frozenset[str]:
    """The first word of each `subprocess` call's argv as a literal list; anything else is named."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(text)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and ast.unparse(node.func.value) == "subprocess"
            and node.func.attr in SUBPROCESS_CALLS
        ):
            argv = node.args[0] if node.args else None
            first = argv.elts[0] if isinstance(argv, ast.List) and argv.elts else None
            found.add(
                first.value
                if isinstance(first, ast.Constant) and isinstance(first.value, str)
                else f"<{ast.unparse(argv) if argv else 'no argv'}>"
            )
    return frozenset(found)


@pytest.mark.parametrize("component", sorted(GIT_ONLY))
def test_the_registry_scripts_start_git_alone_and_have_no_network(component: str) -> None:
    path, heads = GIT_ONLY[component]
    assert script_network(path) == frozenset()
    assert script_processes(path) == heads
    assert subprocess_programs(path.read_text(encoding="utf-8")) == {"git"}


@pytest.mark.parametrize(
    "line",
    ['subprocess.run(["curl", "x"])', "subprocess.check_output(command)", "subprocess.call([])"],
)
def test_control_a_program_other_than_git_fails_the_registry_rows(line: str) -> None:
    assert subprocess_programs(f"import subprocess\n{line}\n") != {"git"}
