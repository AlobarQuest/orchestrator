"""Architecture guard: one test states what the Release layer reaches and reads.

* The routes `image_release` asks production to serve, held to the routes `create_app()` serves.
* What each of `image_release`'s three identities (SYSTEM, VERIFIER, anonymous) may send.
* What the activation sweep's two clients may send, and the bearers its launcher fetches.
* The three programs that read `GET /api/v1/observations` with a filter, and no others.
* The one public document `attest_exit_criteria.py` reads, with no credential.

Each row is held to the code (the client's own predicate, the launcher's text, the module's own
constants) and each control shows the comparison reporting a deliberate departure.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType

import pytest

from activation_sweep import binding_client, orchestrator_client
from image_release import client as release_client
from image_release.release import REQUIRED_ROUTES
from orchestrator.main import create_app
from tests.architecture.edge_table import (
    DEADMAN,
    OBSERVER_BEARER,
    SYSTEM_BEARER,
    Edge,
    Permits,
    launcher_bearers,
    near_misses,
    route_disagreements,
)

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD")
ID = "0b5bd8e1-6d0c-4e8a-9a0d-1f0b4d8f6a11"
BINDING = "7c1f0f64-3a0e-4b8b-8a3e-2f5f2d9c0a55"

# ---- The routes image_release requires ------------------------------------------------------


def served_templates() -> frozenset[str]:
    """The path templates `create_app()` serves, as the OpenAPI document `image_release` reads."""
    return frozenset(create_app().openapi()["paths"])


def unserved(required: tuple[str, ...], served: frozenset[str]) -> list[str]:
    return sorted(path for path in required if path not in served)


def test_every_route_image_release_requires_is_served() -> None:
    assert unserved(REQUIRED_ROUTES, served_templates()) == []


def test_control_a_renamed_route_fails_the_required_check() -> None:
    victim = "/api/v1/work-units/{unit_id}/release-artifacts"
    assert victim in REQUIRED_ROUTES
    assert unserved(REQUIRED_ROUTES, served_templates() - {victim}) == [victim]


# ---- What image_release's three identities may send -----------------------------------------

BIND = f"/api/v1/work-units/{ID}/release-artifacts"
OBSERVE = f"/api/v1/release-artifacts/{BINDING}/deployment-observations"
VERIFY = f"/api/v1/work-units/{ID}/verify"
CANDIDATES = "/api/v1/machine-activation-candidates"
DEAD_LETTER = "/api/v1/dead-letter"
RELEASE_ROWS: dict[str, frozenset[tuple[str, str]]] = {
    "system": frozenset(
        {
            ("GET", CANDIDATES),
            ("GET", DEAD_LETTER),
            ("GET", OBSERVE),
            ("POST", BIND),
            ("POST", OBSERVE),
        }
    ),
    "verifier": frozenset({("POST", VERIFY)}),
    "anonymous": frozenset(
        {
            ("GET", "/health/live"),
            ("GET", "/health/ready"),
            ("GET", "/openapi.json"),
            ("GET", DEAD_LETTER),
        }
    ),
}
RELEASE_PROBES = frozenset(
    {
        f"{BIND}/extra",
        f"/api/v1/work-units/{ID}/adjudications",
        f"/api/v1/work-units/{ID}",
        f"/api/v1/release-artifacts/{BINDING}",
        "/api/v1/work-units/not-a-uuid/verify",
        "/api/v1/observations",
        "/api/v1/members",
        "/api/v1/dead-letter/x",
        "/docs",
    }
)


def _release_permits() -> dict[str, Permits]:
    return {
        "system": lambda m, p: release_client.system_permits(m, p),
        "verifier": lambda m, p: release_client.verifier_permits(m, p),
        "anonymous": lambda m, p: release_client.anonymous_permits(m, p),
    }


def _orchestrator_only(
    routes: frozenset[tuple[str, str]],
    permits: Permits,
    corpus: frozenset[str],
    methods: tuple[str, ...] = METHODS,
) -> frozenset[tuple[str, str, str, bool]]:
    edge = Edge(
        change_manager=frozenset(),
        orchestrator=routes,
        github=frozenset(),
        alert_hub=frozenset(),
        bearers=frozenset(),
    )
    table: dict[str, Permits | None] = {
        "change_manager": None,
        "orchestrator": permits,
        "github": None,
        "alert_hub": None,
    }
    return route_disagreements(edge, table, corpus, methods)


def release_disagreements() -> dict[str, frozenset[tuple[str, str, str, bool]]]:
    permits = _release_permits()
    all_paths = {path for rows in RELEASE_ROWS.values() for _, path in rows}
    corpus = near_misses(all_paths) | RELEASE_PROBES
    return {
        identity: _orchestrator_only(rows, permits[identity], corpus)
        for identity, rows in RELEASE_ROWS.items()
    }


def test_image_release_accepts_exactly_its_routes_among_the_corpus() -> None:
    assert {k: sorted(v) for k, v in release_disagreements().items()} == {
        "system": [],
        "verifier": [],
        "anonymous": [],
    }


@pytest.mark.parametrize(
    ("predicate", "extra"),
    [
        ("system_permits", f"/api/v1/work-units/{ID}/adjudications"),
        ("verifier_permits", f"/api/v1/work-units/{ID}"),
        ("anonymous_permits", "/api/v1/members"),
    ],
)
def test_control_a_widened_release_predicate_fails_the_table(
    monkeypatch: pytest.MonkeyPatch, predicate: str, extra: str
) -> None:
    original = getattr(release_client, predicate)
    monkeypatch.setattr(release_client, predicate, lambda m, p: p == extra or original(m, p))
    identity = predicate.removesuffix("_permits")

    assert ("orchestrator", "POST", extra, True) in release_disagreements()[identity]


# ---- The activation sweep -------------------------------------------------------------------

SWEEP_LAUNCHER = Path("scripts/run-activation-sweep.sh")
OBSERVATIONS = "/api/v1/observations"
SWEEP_SYSTEM = frozenset({("GET", CANDIDATES), ("POST", BIND), ("POST", OBSERVE)})
SWEEP_OBSERVER = frozenset({("POST", OBSERVATIONS)})
SWEEP_METHODS = ("GET", "POST")
SWEEP_PROBES = RELEASE_PROBES | {DEAD_LETTER, "/health/live", VERIFY}


def sweep_disagreements() -> dict[str, frozenset[tuple[str, str, str, bool]]]:
    """Held over GET and POST: any other method goes down the predicates' write branch."""
    corpus = near_misses({p for _, p in SWEEP_SYSTEM | SWEEP_OBSERVER}) | SWEEP_PROBES
    return {
        "system": _orchestrator_only(
            SWEEP_SYSTEM, lambda m, p: binding_client._permits(m, p), corpus, SWEEP_METHODS
        ),
        "observer": _orchestrator_only(
            SWEEP_OBSERVER, lambda m, p: orchestrator_client._permits(m, p), corpus, SWEEP_METHODS
        ),
    }


def test_the_activation_sweep_binds_through_exactly_its_routes() -> None:
    assert {k: sorted(v) for k, v in sweep_disagreements().items()} == {
        "system": [],
        "observer": [],
    }
    assert launcher_bearers(SWEEP_LAUNCHER) == DEADMAN | {SYSTEM_BEARER, OBSERVER_BEARER}


@pytest.mark.parametrize(
    ("module", "identity", "extra"),
    [
        (binding_client, "system", f"/api/v1/work-units/{ID}/adjudications"),
        (orchestrator_client, "observer", "/api/v1/members"),
    ],
)
def test_control_a_widened_sweep_predicate_fails_the_table(
    monkeypatch: pytest.MonkeyPatch, module: ModuleType, identity: str, extra: str
) -> None:
    original = getattr(module, "_permits")
    monkeypatch.setattr(module, "_permits", lambda m, p: p == extra or original(m, p))

    assert ("orchestrator", "POST", extra, True) in sweep_disagreements()[identity]


# ---- Who reads the observations list --------------------------------------------------------

READERS = {
    "src/rotation_worker/orchestrator_client.py": True,
    "scripts/replay_deploy_signal.py": True,
    "src/landing_ledger/orchestrator_client.py": True,
}


def _bound_names(tree: ast.Module) -> set[str]:
    """Module-level names bound to the observations path."""
    return {
        target.id
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and isinstance(node.value, ast.Constant)
        and node.value.value == OBSERVATIONS
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        if isinstance(target, ast.Name)
    }


def _imported_aliases(tree: ast.Module, bound: set[str]) -> set[str]:
    """Names this module imports from another under the path's constant's name, with aliases."""
    return {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name in bound
    }


def _mentions(node: ast.AST, names: set[str]) -> bool:
    """Whether the expression is the path: a bound name, or a literal or f-string holding it."""
    return any(
        (isinstance(part, ast.Name) and part.id in names)
        or (
            isinstance(part, ast.Constant)
            and isinstance(part.value, str)
            and OBSERVATIONS in part.value
        )
        for part in ast.walk(node)
    )


def _call_name(call: ast.Call) -> str:
    if isinstance(call.func, ast.Name):
        return call.func.id
    return call.func.attr if isinstance(call.func, ast.Attribute) else ""


def _is_filter(node: ast.AST, scope: ast.AST) -> bool:
    """A non-empty dict literal, directly or through a name assigned one in the same scope."""
    if isinstance(node, ast.Name):
        return any(
            isinstance(assigned, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == node.id for t in assigned.targets)
            and _is_filter(assigned.value, scope)
            for assigned in ast.walk(scope)
        )
    return isinstance(node, ast.Dict) and bool(node.keys)


def observation_reads(sources: Mapping[str, str]) -> dict[str, bool]:
    """Each Python source that sends the observations path as a GET, and whether it filters.

    A GET is a call with a literal "GET" among its arguments, or a call to a function whose name
    ends in `get`, that names the path. A filter is a `params=` dict, or a query string after the
    path. A shell script that names the path outside a comment is reported as unfiltered.

    The path may be a literal, an f-string, or a name bound to it in any scanned module (and
    imported, with or without an alias), passed positionally or by keyword. A class attribute
    (`self.PATH`) is out of scope.
    """
    parsed = {name: ast.parse(text) for name, text in sources.items() if name.endswith(".py")}
    bound = set().union(*(_bound_names(tree) for tree in parsed.values()))
    found: dict[str, bool] = {}
    for name, text in sources.items():
        if not name.endswith(".py"):
            if any(
                OBSERVATIONS in line and not line.lstrip().startswith("#")
                for line in text.splitlines()
            ):
                found[name] = False
            continue
        tree = parsed[name]
        names = bound | _imported_aliases(tree, bound)
        for scope in ast.walk(tree):
            if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for call in (n for n in ast.walk(scope) if isinstance(n, ast.Call)):
                values = [*call.args, *(k.value for k in call.keywords)]
                literal_get = any(isinstance(a, ast.Constant) and a.value == "GET" for a in values)
                if not (literal_get or _call_name(call).lower().endswith("get")):
                    continue
                if not any(_mentions(a, names) for a in values):
                    continue
                params = [k.value for k in call.keywords if k.arg == "params"]
                named = any(
                    k.arg not in {None, "params", "headers", "timeout", "method", "url", "path"}
                    for k in call.keywords
                )
                query = any(
                    isinstance(part, ast.Constant)
                    and isinstance(part.value, str)
                    and f"{OBSERVATIONS}?" in part.value
                    for a in values
                    for part in ast.walk(a)
                )
                found[name] = (
                    found.get(name, False)
                    or query
                    or named
                    or any(_is_filter(p, scope) for p in params)
                )
    return found


def _repository_sources() -> dict[str, str]:
    files = [
        *Path("src").rglob("*.py"),
        *Path("scripts").rglob("*.py"),
        *Path("scripts").rglob("*.sh"),
    ]
    return {path.as_posix(): path.read_text(encoding="utf-8") for path in files}


def test_the_observations_read_has_exactly_three_callers() -> None:
    assert observation_reads(_repository_sources()) == READERS


def test_control_a_third_or_unfiltered_caller_is_reported() -> None:
    third = (
        "from typing import Final\n"
        'PATH: Final = "/api/v1/observations"\n'
        "class C:\n"
        "    def all(self):\n"
        '        return self._send("GET", PATH)\n'
        "    def some(self):\n"
        '        return self._send("GET", PATH, params={"source_system": "x"})\n'
    )
    fstring = (
        "def f(h):\n"
        '    return _get(f"{SDS}/api/v1/observations?{q}", h)\n'
        "def g(h):\n"
        '    return _get(f"{SDS}/api/v1/observations", h)\n'
    )
    post_only = (
        'PATH = "/api/v1/observations"\n'
        "class C:\n"
        "    def w(self):\n"
        "        return self._post(PATH, {})\n"
        "    def x(self):\n"
        '        return self.request("POST", PATH)\n'
    )
    sources = {
        **_repository_sources(),
        "src/third/client.py": third,
        "src/third/fstring.py": fstring,
        "src/third/post.py": post_only,
        "scripts/third.sh": "curl https://sds/api/v1/observations\n# /api/v1/observations\n",
    }

    found = observation_reads(sources)

    assert found["src/third/client.py"] is True  # one of its two reads filters
    assert found["scripts/third.sh"] is False
    assert found["src/third/fstring.py"] is True
    assert "src/third/post.py" not in found
    assert set(found) - set(READERS) == {
        "src/third/client.py",
        "src/third/fstring.py",
        "scripts/third.sh",
    }


def test_control_an_unfiltered_read_is_reported_unfiltered() -> None:
    bare = 'PATH = "/api/v1/observations"\ndef f(c):\n    return c._send("GET", PATH)\n'
    assert observation_reads({"src/x.py": bare}) == {"src/x.py": False}


# ---- The attestation reads one public document ----------------------------------------------

ATTEST = Path("scripts/attest_exit_criteria.py")
PUBLIC_DOCUMENT = "https://sds.alobar.net/openapi.json"
CREDENTIAL_WORDS = ("authorization", "bearer", "token", "credential", "x-api-key", "cookie")


def attest_reach(source: str) -> tuple[frozenset[str], int, frozenset[str]]:
    """The URL literals, the opens, and any credential-shaped words in the code (not docstrings)."""
    tree = ast.parse(source)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    strings = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]
    identifiers = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    opens = sum(
        1 for node in ast.walk(tree) if isinstance(node, ast.Call) and _call_name(node) == "urlopen"
    )
    words = frozenset(
        word
        for word in CREDENTIAL_WORDS
        for text in [*strings, *(i.lower() for i in identifiers)]
        if word in text.lower()
    )
    return frozenset(s for s in strings if s.startswith(("http://", "https://"))), opens, words


def test_attest_exit_criteria_reads_one_public_document() -> None:
    urls, opens, words = attest_reach(ATTEST.read_text(encoding="utf-8"))

    assert urls == {PUBLIC_DOCUMENT}
    assert opens == 1
    assert words == frozenset()


def test_control_a_second_url_or_a_bearer_changes_what_attest_reaches() -> None:
    source = ATTEST.read_text(encoding="utf-8")
    second = source.replace(
        "def main(", 'OTHER = "https://sds.alobar.net/api/v1/members"\n\n\ndef main(', 1
    )
    bearer = 'HEADERS = {"Authorization": "Bearer x"}\n' + source.replace(
        "urllib.request.urlopen(openapi_url, timeout=30)",
        "urllib.request.urlopen(urllib.request.Request(openapi_url, headers=HEADERS))",
    )

    assert attest_reach(second)[0] == {PUBLIC_DOCUMENT, "https://sds.alobar.net/api/v1/members"}
    assert attest_reach(bearer)[1] == 1
    assert {"authorization", "bearer"} <= attest_reach(bearer)[2]


def test_control_an_imported_constant_is_a_caller() -> None:
    sources = {
        "src/a/consts.py": 'OBSERVATIONS_ENDPOINT = "/api/v1/observations"\n',
        "src/b/client.py": (
            "from a.consts import OBSERVATIONS_ENDPOINT\n"
            "def f(c):\n"
            "    return c.get(OBSERVATIONS_ENDPOINT, source_system='x')\n"
        ),
        "src/c/client.py": (
            "from a.consts import OBSERVATIONS_ENDPOINT as P\n"
            "def f(c):\n"
            "    return c._send('GET', P)\n"
        ),
    }

    assert observation_reads(sources) == {"src/b/client.py": True, "src/c/client.py": False}


def test_control_a_path_passed_by_keyword_is_a_caller() -> None:
    sources = {
        "src/d/client.py": (
            'PATH = "/api/v1/observations"\n'
            "def f(c):\n"
            '    return c.request(method="GET", url=PATH, params={"a": 1})\n'
            "def g(c):\n"
            '    return c.request(method="POST", url=PATH)\n'
        ),
    }

    assert observation_reads(sources) == {"src/d/client.py": True}
