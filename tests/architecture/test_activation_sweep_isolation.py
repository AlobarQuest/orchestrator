"""The sweep shares no import path with the orchestrator, writes to one endpoint, never pulls."""

import ast
from pathlib import Path
from typing import Any

import httpx
import pytest

from activation_sweep.binding_client import (
    is_allowed_read as binding_is_allowed_read,
)
from activation_sweep.binding_client import (
    is_allowed_write as binding_is_allowed_write,
)
from activation_sweep.checkout import READ_ONLY, ForbiddenCommandError, run_git
from activation_sweep.orchestrator_client import (
    ForbiddenEndpointError,
    OrchestratorClient,
    SweepWriteError,
    UnusableEndpointError,
    is_allowed_write,
    open_client,
)

SWEEP = Path("src/activation_sweep")
ORCHESTRATOR = Path("src/orchestrator")
ALLOWED_TOP_LEVEL = {
    "httpx",
    "typer",
    "activation_sweep",
    "dataclasses",
    "datetime",
    "hashlib",
    "json",
    "os",
    "pathlib",
    "re",
    # `shutil.which` alone, to find `uv` on PATH before the activation check runs it. The
    # fallback to its standard install location is what keeps a scheduled pass measuring when
    # the plist's PATH does not carry it.
    "shutil",
    "subprocess",
    # `tomllib` reads `[project.scripts]` from a working copy's own manifest, which is what the
    # console-entry-point fact is measured against. Standard library since 3.11; it is here
    # because this guard confines THIRD-PARTY dependencies and lists every top-level name.
    "tomllib",
    "typing",
    # `urllib.parse` only, for the base-URL shape check. `urllib.request` is an HTTP client and
    # is in the scan's own `HTTP_CLIENTS` set, so it could never arrive here unnoticed.
    "urllib",
    "__future__",
}

# The subcommands that would make this program act on the machine rather than read it. Named HERE
# rather than in the source, because a second copy of the allowlist beside the allowlist is a
# thing that can drift; what the source holds is the one list of what is permitted, and this is
# the control proving the complement is refused.
NEVER = ("pull", "merge", "reset", "checkout", "clean", "commit", "push", "rebase", "stash")


def _client(handler: Any) -> OrchestratorClient:
    return OrchestratorClient(
        base_url="https://x",
        credential_key_id="orchestrator-observer",
        token="t",
        transport=httpx.MockTransport(handler),
    )


def _file_imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _imports(root: Path) -> set[str]:
    names: set[str] = set()
    for path in root.rglob("*.py"):
        names |= _file_imports(path)
    return names


def test_the_sweep_imports_nothing_from_the_orchestrator() -> None:
    assert {name for name in _imports(SWEEP) if name.split(".")[0] == "orchestrator"} == set()


def test_the_orchestrator_imports_nothing_from_the_sweep() -> None:
    assert {
        name for name in _imports(ORCHESTRATOR) if name.split(".")[0] == "activation_sweep"
    } == set()


def test_the_sweeps_third_party_deps_are_confined() -> None:
    assert {name.split(".")[0] for name in _imports(SWEEP)} - ALLOWED_TOP_LEVEL == set()


def test_the_write_surface_is_the_observer_roles_whole_write_surface_and_no_more() -> None:
    assert is_allowed_write("/api/v1/observations")
    assert not is_allowed_write("/api/v1/observations/")
    assert not is_allowed_write("/api/v1/deployment-observations")
    unit = "/api/v1/work-units/00000000-0000-0000-0000-000000000000"
    assert not is_allowed_write(f"{unit}/commands/ready")
    assert not is_allowed_write(f"{unit}/evidence")
    assert not is_allowed_write(f"{unit}/dispatch")
    assert not is_allowed_write(f"{unit}/pr-merge")


def test_only_the_client_module_can_speak_http() -> None:
    """The program's whole entry in the repository's outbound allowlist is ONE file, and this is
    what keeps it there. Both halves of the malformed-URL guard live in that module -- `httpx`
    refuses some URLs at the constructor and others at request time -- so the CLI needs no import
    of an HTTP client to translate either, and a future one would red here first.
    """
    speaks = {
        str(path.relative_to(SWEEP))
        for path in SWEEP.rglob("*.py")
        if {"httpx", "requests", "urllib.request", "http.client", "aiohttp"} & _file_imports(path)
    }

    # TWO modules, one per lane, and the split is the property rather than an exception to it.
    # `sweep` writes observations as OBSERVER and reads nothing; `bind` writes a release artifact
    # as SYSTEM and must read first. A single module serving both would hold both credentials'
    # reach behind one guard, so the surfaces are separated where the credentials are.
    assert speaks == {"orchestrator_client.py", "binding_client.py"}


def test_the_sweep_has_no_read_surface_at_all() -> None:
    """It needs nothing back, so there is no reader to bound -- and none to widen by accident.

    Written as an assertion about the module rather than as prose, so adding a read means
    deleting a test that says reads do not exist, which is a decision somebody has to make.
    """
    import activation_sweep.orchestrator_client as client_module

    assert not hasattr(client_module, "is_allowed_read")
    assert not any(
        hasattr(OrchestratorClient, name) for name in ("get", "read_observations", "_read")
    )


def test_the_credential_is_sent_on_every_request() -> None:
    """Nothing else asserts the two headers, and a client that authenticated with neither would
    fail identically to an expired bearer -- 401 on every checkout, once a morning."""
    seen: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers)
        return httpx.Response(201, json={})

    _client(handler).record_observation({})

    assert seen[0]["authorization"] == "Bearer t"
    assert seen[0]["x-credential-key-id"] == "orchestrator-observer"


def test_a_rejected_write_carries_the_status_and_nothing_else() -> None:
    """The rejection body echoes the command back, so a diagnostic that prints what it was given
    is how a value that should not be in a transcript gets into one."""
    body = {"error": {"code": "observation_invalid", "command": {"idempotency_key": "secret-ish"}}}
    client = _client(lambda request: httpx.Response(422, json=body))

    with pytest.raises(SweepWriteError) as error:
        client.record_observation({})

    assert str(error.value) == "orchestrator rejected POST /api/v1/observations: 422"


def test_a_success_that_is_not_json_is_this_clients_error_rather_than_a_bare_ValueError() -> None:
    """A 204, or a proxy's page. Left unguarded it escapes as `ValueError` and the CLI reports it
    as though a working copy had been unreadable."""
    client = _client(lambda request: httpx.Response(200, text="<html>nginx</html>"))

    with pytest.raises(SweepWriteError):
        client.record_observation({})


@pytest.mark.parametrize(
    "unusable",
    [
        # Refused at the CONSTRUCTOR by httpx.
        "https://ho\x00st",
        # Constructed cleanly by httpx and refused at REQUEST time by IDNA encoding -- a
        # `UnicodeError`, which is neither `HTTPError` nor `InvalidURL`. Both shapes are ordinary
        # environment-variable typos, and both are caught here instead so that the tool refusing
        # is one clear exit rather than nine per-checkout write failures.
        "https://sds..alobar.net",
        "https://" + "a" * 71 + ".net",
        "http://sds.alobar.net",
        "https://",
    ],
)
def test_an_unusable_orchestrator_url_is_refused_before_a_client_exists(unusable: str) -> None:
    with pytest.raises(UnusableEndpointError):
        open_client(base_url=unusable, credential_key_id="orchestrator-observer", token="t")


def test_a_usable_orchestrator_url_still_opens() -> None:
    """A refusal that refused everything would look identical on the test above."""
    with open_client(
        base_url="https://sds.alobar.net", credential_key_id="orchestrator-observer", token="t"
    ) as client:
        assert client is not None


def test_a_forbidden_write_never_reaches_the_transport() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={})

    client = _client(handler)
    with pytest.raises(ForbiddenEndpointError):
        client.post("/api/v1/work-units/00000000-0000-0000-0000-000000000000/dispatch", {})
    assert seen == []
    assert client.record_observation({}) == {}
    assert seen == ["/api/v1/observations"]


def test_the_git_surface_can_read_and_fetch_and_nothing_else(tmp_path: Path) -> None:
    """ADR-0030 stops at recording. `pull` is UNREACHABLE here, not merely unused.

    `fetch` is the one member that writes anything, and what it writes is remote-tracking refs --
    never HEAD, never the index, never a tracked file.
    """
    assert "fetch" in READ_ONLY
    for subcommand in NEVER:
        assert subcommand not in READ_ONLY
        with pytest.raises(ForbiddenCommandError):
            run_git(tmp_path, subcommand)
    with pytest.raises(ForbiddenCommandError):
        run_git(tmp_path)


def test_the_binding_lanes_surface_is_two_paths_and_no_more() -> None:
    """One read and one write, both anchored. Controls in both directions.

    The unit-id shape is spelled into the write pattern so a prefix, a trailing slash, or
    `.../{id}/anything-else` does not match. Without that the pattern would admit every per-unit
    command route -- `dispatch`, `commands/ready`, `pr-merge` -- which is the reach this lane's
    SYSTEM credential actually has and the reason the bound is written in code.
    """
    unit = "00000000-0000-0000-0000-000000000000"
    assert binding_is_allowed_write(f"/api/v1/work-units/{unit}/release-artifacts")
    assert not binding_is_allowed_write(f"/api/v1/work-units/{unit}/release-artifacts/")
    assert not binding_is_allowed_write(f"/api/v1/work-units/{unit}/commands/ready")
    assert not binding_is_allowed_write(f"/api/v1/work-units/{unit}/dispatch")
    assert not binding_is_allowed_write(f"/api/v1/work-units/{unit}/pr-merge")
    assert not binding_is_allowed_write("/api/v1/observations")
    assert not binding_is_allowed_write("/api/v1/machine-activation-candidates")

    assert binding_is_allowed_read("/api/v1/machine-activation-candidates")
    assert binding_is_allowed_read("/api/v1/machine-activation-candidates?repository=a/b")
    assert not binding_is_allowed_read("/api/v1/machine-activation-candidates/")
    assert not binding_is_allowed_read(f"/api/v1/work-units/{unit}/evidence-pack")
    assert not binding_is_allowed_read("/api/v1/observations")
