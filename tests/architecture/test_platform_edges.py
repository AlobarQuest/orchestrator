"""Architecture guard: one test states what the Core platform layer reaches and reads.

* The GitHub App mint: its one host, its one request, and that the request carries no body, so the
  token it returns has the whole installation's reach.
* Every call that mints that token or resolves its credentials, each with the grant or allowlist
  check that bounds it, or the reason it needs none.
* The CLI's one client and the one path prefix it sends.
* The configuration surface: the `Settings` fields, which are secrets, and every raw environment
  read in the layer.
* The files the layer reads, and the routes that answer without an actor.
* The exception handlers, the statuses they map, and the one alert the 500 handler may send.
* The layer's population, so a new file fails until it is placed.

Each row is held to the code (the module's constants, the real `fire_once` predicate captured as
the lanes' are, the app's own route tree, the AST of the source) and each has a `test_control_*`
that shows the comparison reporting a deliberate widening.

What this cannot see, stated so a green run is not read as more than it is:

* A URL assembled at run time is read only as far as the AST shows it: the mint's f-string and the
  CLI's literal or f-string paths. A path passed on as a variable is reported by the CLI row, not
  followed, and `_post_data` is the one place the CLI does that, so its callers are scanned too.
* A call made through an object, an alias (`from x import token_provider_for as mint`), a
  `functools.partial` or `import_module` is not a call this scan names. The scans read the
  callee's spelling, as the other edge tables do.
* The environment and file scans see the spelling `os.environ`, `os.getenv`, `read_text`,
  `read_bytes`, `open`, `Jinja2Templates`, `Config` and `fileConfig`. A read through `pathlib`
  methods not named, or `os.environ` passed as a whole to a callee, is seen only as the whole
  `os.environ` (reported under the key `*`). Writes are not scanned.
* The bound each token caller names is checked to be a call that still exists in the module that
  holds it, not that the call is reached on every path or that its result is honoured. Those are
  the landing, execution and verification edge tests' and the services' own tests.
* Handlers added with `add_middleware`, or by a router's own handlers, are not handlers here;
  `add_middleware` appears nowhere in the layer today and is not scanned for.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Final, get_args

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.routing import APIRoute
from pydantic import SecretStr
from starlette.routing import Route

import alert_hub_client
from orchestrator.api import error_shape
from orchestrator.api import server_errors as server_errors_module
from orchestrator.api.dependencies import APIAuthenticationError, get_actor
from orchestrator.api.health import router as health_router
from orchestrator.config import Settings
from orchestrator.errors import DomainError
from orchestrator.main import API_ROUTERS, create_app
from orchestrator.services import github_app
from orchestrator.web import router as web_router
from scripts.layers import load
from tests.architecture.edge_table import Permits, built_clients, near_misses, sendable
from tests.architecture.import_scan import file_imports

SOURCE = Path("src/orchestrator")
GITHUB_APP = "src/orchestrator/services/github_app.py"
CLI = "src/orchestrator/cli.py"
EXECUTION_ROUTES = "src/orchestrator/api/routes/execution.py"
LANDING_ROUTES = "src/orchestrator/api/routes/landing.py"
VERIFIER_ROUTES = "src/orchestrator/api/routes/verifier.py"
COMMON_ROUTES = "src/orchestrator/api/routes/common.py"


def _parse(text: str) -> ast.Module:
    return ast.parse(text)


def _callee(node: ast.Call) -> str:
    """The name a call is spelled with: `f` for `f(...)`, `f` for `x.f(...)`."""
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _dotted(node: ast.expr) -> str:
    return ast.unparse(node)


def _sources(root: Path = SOURCE) -> dict[str, str]:
    return {path.as_posix(): path.read_text() for path in sorted(root.rglob("*.py"))}


# ---- 1. The mint ---------------------------------------------------------------------------------

BODY_KEYWORDS: Final = frozenset({"json", "data", "content", "files", "params"})
MINT_URL_SHAPE: Final = "{}/app/installations/{}/access_tokens"


def mint_requests(source: str) -> list[ast.Call]:
    """Every call to an attribute of `httpx` in the source, whatever it is called."""
    return [
        node
        for node in ast.walk(_parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "httpx"
    ]


def _url_shape(call: ast.Call, tree: ast.Module) -> tuple[str, str]:
    """The first argument as an f-string with each field blanked, and its first field's text."""
    target = call.args[0]
    if isinstance(target, ast.Name):
        assigned = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == target.id for t in node.targets)
        ]
        assert len(assigned) == 1, f"the url is assigned {len(assigned)} times"
        target = assigned[0]
    assert isinstance(target, ast.JoinedStr), "the mint's url is not an f-string"
    shape = "".join(
        str(part.value) if isinstance(part, ast.Constant) else "{}" for part in target.values
    )
    first = next(p for p in target.values if isinstance(p, ast.FormattedValue))
    return shape, _dotted(first.value)


def mint_findings(source: str) -> list[str]:
    """What is wrong with a module's HTTP calls as the mint's row states them; empty when right."""
    tree = _parse(source)
    calls = mint_requests(source)
    found: list[str] = []
    if [_dotted(call.func) for call in calls] != ["httpx.post"]:
        found.append(f"calls: {[_dotted(call.func) for call in calls]}")
        return found
    [call] = calls
    shape, first = _url_shape(call, tree)
    if shape != MINT_URL_SHAPE or first != "GITHUB_API_URL":
        found.append(f"url: {shape!r} from {first}")
    body = sorted(BODY_KEYWORDS & {kw.arg for kw in call.keywords if kw.arg})
    if body or any(kw.arg is None for kw in call.keywords):
        found.append(f"body keywords: {body}")
    return found


def test_the_mint_reaches_one_host_with_one_bodyless_request() -> None:
    assert github_app.GITHUB_API_URL == "https://api.github.com"
    assert mint_findings(Path(GITHUB_APP).read_text()) == []


MINT_OK = """
import httpx
GITHUB_API_URL = "x"
def mint(credentials):
    url = f"{GITHUB_API_URL}/app/installations/{credentials.installation_id}/access_tokens"
    return httpx.post(url, headers={}, timeout=1)
"""


def test_control_the_mint_row_passes_a_source_of_its_shape() -> None:
    assert mint_findings(MINT_OK) == []


@pytest.mark.parametrize(
    ("edit", "mentions"),
    [
        (("timeout=1)", "timeout=1, json={'repositories': ['x']})"), "body keywords"),
        (("timeout=1)", "timeout=1, data=b'x')"), "body keywords"),
        (("timeout=1)", "timeout=1, content=b'x')"), "body keywords"),
        (("timeout=1)", "timeout=1, **extra)"), "body keywords"),
        (
            (
                "    return httpx.post",
                "    httpx.get('https://example.test')\n    return httpx.post",
            ),
            "calls",
        ),
        (("/access_tokens", "/other"), "url"),
    ],
)
def test_control_a_body_a_second_call_or_another_route_is_reported(
    edit: tuple[str, str], mentions: str
) -> None:
    old, new = edit
    assert old in MINT_OK
    findings = mint_findings(MINT_OK.replace(old, new))
    assert findings and mentions in findings[0]


# ---- 2. Who mints the token ----------------------------------------------------------------------

PROVIDER = "token_provider_for"
CREDENTIALS = "github_app_credentials_for"
DISPATCH = "src/orchestrator/services/execution/dispatch.py"
ESTATE_ADMISSION = "src/orchestrator/services/landing/estate_landing_admission.py"
INERT_ADMISSION = "src/orchestrator/services/landing/inert_landing_admission.py"
PR_MERGE_ADMISSION = "src/orchestrator/services/landing/pr_merge_admission.py"
REVERT_ACT = "src/orchestrator/services/landing/revert_act.py"
NAMED_CHECK = "src/orchestrator/services/verifier/verifier_evidence.py"


@dataclass(frozen=True)
class Caller:
    """One function that mints or resolves, how often, and what bounds it.

    `anchors` are the calls (module, callee) that carry the bound; each must still exist.
    """

    provider: int
    credentials: int
    bound: str
    anchors: tuple[tuple[str, str], ...] = ()


_GRANT_AT_ADMISSION = "the admission asks the member record's grant before any read or write"
_REVERT = (
    "the lane gateways are built here and handed to revert_act, whose watch and acts ask the "
    "dependency_updates grant per repository before any read (`_refuse_unless_permitted`)"
)

TOKEN_CALLERS: Final[dict[tuple[str, str], Caller]] = {
    (EXECUTION_ROUTES, "dispatch_route"): Caller(
        1,
        1,
        "dispatch_work_unit is handed the member record as its grant source and refuses a "
        "repository it does not grant (`target_repository_not_granted`); the gateway's own "
        "predicate allows only the one workflow-dispatch route",
        ((DISPATCH, "is_granted"),),
    ),
    (LANDING_ROUTES, "estate_landing_admission_route"): Caller(
        1, 1, f"{_GRANT_AT_ADMISSION} (estate)", ((ESTATE_ADMISSION, "grant_term"),)
    ),
    (LANDING_ROUTES, "estate_pr_merge_route"): Caller(
        1, 1, f"{_GRANT_AT_ADMISSION}; the merge needs it met", ((ESTATE_ADMISSION, "grant_term"),)
    ),
    (LANDING_ROUTES, "estate_pr_branch_update_route"): Caller(
        1, 1, f"{_GRANT_AT_ADMISSION}; the update needs it met", ((ESTATE_ADMISSION, "grant_term"),)
    ),
    (LANDING_ROUTES, "inert_landing_admission_route"): Caller(
        1, 1, f"{_GRANT_AT_ADMISSION} (inert)", ((INERT_ADMISSION, "grant_term"),)
    ),
    (LANDING_ROUTES, "inert_pr_merge_route"): Caller(
        1, 1, f"{_GRANT_AT_ADMISSION}; the merge needs it met", ((INERT_ADMISSION, "grant_term"),)
    ),
    (LANDING_ROUTES, "inert_pr_branch_update_route"): Caller(
        1, 1, f"{_GRANT_AT_ADMISSION}; the update needs it met", ((INERT_ADMISSION, "grant_term"),)
    ),
    (LANDING_ROUTES, "pr_merge_route"): Caller(
        1,
        1,
        "the factory merge asks the grant on the unit's own envelope target "
        "(`merge_repository_not_granted`)",
        ((PR_MERGE_ADMISSION, "grant_term"),),
    ),
    (LANDING_ROUTES, "held_pr_close_route"): Caller(
        2,
        1,
        "the named lane's admission is composed again in the act, grant term included",
        ((ESTATE_ADMISSION, "grant_term"), (INERT_ADMISSION, "grant_term")),
    ),
    (LANDING_ROUTES, "_revert_lanes"): Caller(1, 0, _REVERT, ((REVERT_ACT, "grant_term"),)),
    (LANDING_ROUTES, "landing_watch_route"): Caller(0, 1, _REVERT, ((REVERT_ACT, "grant_term"),)),
    (LANDING_ROUTES, "landing_revert_route"): Caller(0, 1, _REVERT, ((REVERT_ACT, "grant_term"),)),
    (LANDING_ROUTES, "landing_repoint_route"): Caller(0, 1, _REVERT, ((REVERT_ACT, "grant_term"),)),
    (LANDING_ROUTES, "landing_pinned_route"): Caller(0, 1, _REVERT, ((REVERT_ACT, "grant_term"),)),
    (LANDING_ROUTES, "landing_restored_route"): Caller(
        0, 1, _REVERT, ((REVERT_ACT, "grant_term"),)
    ),
    (VERIFIER_ROUTES, "get_check_observer"): Caller(
        1,
        1,
        "named reason, not a grant: the VERIFIER role's named-check read observes one job of the "
        "unit's own repository, for work already dispatched, so a grant withdrawn since can still "
        "be observed; the repository is the unit's envelope target and the same as its dispatch "
        "target, and every landing asks the grant again",
        ((NAMED_CHECK, "_target_repository"),),
    ),
}


def token_call_sites(sources: Mapping[str, str]) -> dict[tuple[str, str], tuple[int, int]]:
    """(file, enclosing function) -> (provider calls, credentials calls)."""
    found: dict[tuple[str, str], list[int]] = {}
    for path, text in sources.items():
        tree = _parse(text)
        for function in ast.walk(tree):
            if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            names = [
                _callee(node)
                for node in _own_nodes(function)
                if isinstance(node, ast.Call) and _callee(node) in (PROVIDER, CREDENTIALS)
            ]
            if names:
                found[(path, function.name)] = [names.count(PROVIDER), names.count(CREDENTIALS)]
        for node in tree.body:  # a call at module level has no function to name
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                continue
            for call in ast.walk(node):
                if isinstance(call, ast.Call) and _callee(call) in (PROVIDER, CREDENTIALS):
                    found.setdefault((path, "<module>"), [0, 0])[
                        0 if _callee(call) == PROVIDER else 1
                    ] += 1
    return {key: (counts[0], counts[1]) for key, counts in found.items()}


def _own_nodes(function: ast.AST) -> Iterable[ast.AST]:
    """The function's own body: a nested function is its own site."""
    stack = list(ast.iter_child_nodes(function))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            stack.extend(ast.iter_child_nodes(node))


def caller_disagreements(
    sites: Mapping[tuple[str, str], tuple[int, int]],
    table: Mapping[tuple[str, str], Caller],
) -> dict[str, object]:
    expected = {key: (row.provider, row.credentials) for key, row in table.items()}
    return {
        f"{key}": {"found": sites.get(key), "listed": expected.get(key)}
        for key in sites.keys() | expected.keys()
        if sites.get(key) != expected.get(key)
    }


def test_every_call_that_mints_or_resolves_the_token_is_listed_with_its_bound() -> None:
    assert caller_disagreements(token_call_sites(_sources()), TOKEN_CALLERS) == {}


def test_the_per_file_counts_are_the_ones_the_design_recorded() -> None:
    per_file: dict[str, list[int]] = {}
    for (path, _), (provider, credentials) in token_call_sites(_sources()).items():
        counts = per_file.setdefault(path, [0, 0])
        counts[0] += provider
        counts[1] += credentials
    assert per_file == {
        EXECUTION_ROUTES: [1, 1],
        LANDING_ROUTES: [10, 13],
        VERIFIER_ROUTES: [1, 1],
    }


def test_every_listed_bound_is_a_call_that_still_exists() -> None:
    callees: dict[str, set[str]] = {}
    for row in TOKEN_CALLERS.values():
        assert row.bound.strip()
        for module, name in row.anchors:
            if module not in callees:
                callees[module] = {
                    _callee(node)
                    for node in ast.walk(_parse(Path(module).read_text()))
                    if isinstance(node, ast.Call)
                }
            assert name in callees[module], f"{module} no longer calls {name}"
    assert not [
        key
        for key, row in TOKEN_CALLERS.items()
        if not row.anchors and not row.bound.startswith("named reason")
    ]


def test_the_dispatch_route_passes_the_member_record_as_its_grant_source() -> None:
    [route] = [
        node
        for node in ast.walk(_parse(Path(EXECUTION_ROUTES).read_text()))
        if isinstance(node, ast.FunctionDef) and node.name == "dispatch_route"
    ]
    grants = [
        keyword.value
        for node in ast.walk(route)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg == "grant_source"
    ]
    assert [_dotted(value) for value in grants] == ["MemberRecordGrants(session)"]


PLANTED_MINT = (
    "def stray_route(settings):\n    return token_provider_for(credentials_of(settings))\n"
)
PLANTED_RESOLVE = "def stray(settings):\n    return github_app_credentials_for(settings)\n"


def test_control_a_planted_call_in_a_fake_module_is_reported() -> None:
    sources = {**_sources(), "src/orchestrator/api/routes/stray.py": PLANTED_MINT}
    assert caller_disagreements(token_call_sites(sources), TOKEN_CALLERS) == {
        "('src/orchestrator/api/routes/stray.py', 'stray_route')": {
            "found": (1, 0),
            "listed": None,
        }
    }
    sources = {**_sources(), "src/orchestrator/stray.py": PLANTED_RESOLVE}
    assert len(caller_disagreements(token_call_sites(sources), TOKEN_CALLERS)) == 1


def test_control_a_second_mint_in_a_listed_function_is_reported() -> None:
    sources = _sources()
    key = (EXECUTION_ROUTES, "dispatch_route")
    sources[EXECUTION_ROUTES] = sources[EXECUTION_ROUTES].replace(
        "    token_provider = token_provider_for(credentials)\n",
        "    token_provider = token_provider_for(credentials)\n"
        "    token_provider_for(credentials)\n",
    )
    assert list(caller_disagreements(token_call_sites(sources), TOKEN_CALLERS)) == [str(key)]


def test_control_a_listed_function_that_stops_calling_is_reported() -> None:
    sources = _sources()
    sources[VERIFIER_ROUTES] = sources[VERIFIER_ROUTES].replace(
        "token_provider_for(github_app_credentials_for(settings))", "None"
    )
    assert list(caller_disagreements(token_call_sites(sources), TOKEN_CALLERS)) == [
        str((VERIFIER_ROUTES, "get_check_observer"))
    ]


# ---- 3. The CLI ----------------------------------------------------------------------------------

API_PREFIX: Final = "/api/v1/"
CLI_PATH_ARGUMENT: Final = {"request": 1, "_post_data": 0}


def cli_findings(source: str) -> list[str]:
    """What the CLI's source departs from: one `httpx.Client` on `settings.api_url`, `/api/v1/`."""
    tree = _parse(source)
    found: list[str] = []
    clients = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _dotted(node.func) == "httpx.Client"
    ]
    if len(clients) != 1:
        found.append(f"{len(clients)} httpx.Client calls")
    else:
        base = [kw.value for kw in clients[0].keywords if kw.arg == "base_url"]
        if [_dotted(value) for value in base] != ["settings.api_url"]:
            found.append(f"base_url {[_dotted(v) for v in base]}")
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        index = CLI_PATH_ARGUMENT.get(node.func.id)
        if index is None or len(node.args) <= index:
            continue
        path = node.args[index]
        if isinstance(path, ast.Constant) and isinstance(path.value, str):
            literal = path.value
        elif (
            isinstance(path, ast.JoinedStr)
            and path.values
            and isinstance(path.values[0], ast.Constant)
        ):
            literal = str(path.values[0].value)
        elif isinstance(path, ast.Name) and _enclosing_is_post_data(tree, node):
            continue  # the body of `_post_data`, whose callers are scanned in their own right
        else:
            found.append(f"line {node.lineno}: path not a literal: {_dotted(path)}")
            continue
        if not literal.startswith(API_PREFIX):
            found.append(f"line {node.lineno}: {literal!r}")
    return found


def _enclosing_is_post_data(tree: ast.Module, call: ast.Call) -> bool:
    return any(
        isinstance(function, ast.FunctionDef)
        and function.name == "_post_data"
        and call in ast.walk(function)
        for function in ast.walk(tree)
    )


def test_the_cli_builds_one_client_and_sends_only_api_v1_paths() -> None:
    assert cli_findings(Path(CLI).read_text()) == []


def test_the_cli_takes_its_base_url_from_cli_settings() -> None:
    [url] = [
        node
        for node in ast.walk(_parse(Path(CLI).read_text()))
        if isinstance(node, ast.Call)
        and _dotted(node.func) == "os.getenv"
        and node.args
        and ast.literal_eval(node.args[0]) == "ORCHESTRATOR_API_URL"
    ]
    assert ast.literal_eval(url.args[1]) == "http://127.0.0.1:8000"


@pytest.mark.parametrize(
    ("edit", "mentions"),
    [
        (
            'request("GET", "/api/v1/dead-letter")',
            'request("GET", "https://api.github.com/x")',
        ),
        ('request("GET", "/api/v1/dead-letter")', 'request("GET", "/health/live")'),
        ('request("GET", "/api/v1/dead-letter")', 'request("GET", where)'),
        ('request("GET", "/api/v1/dead-letter")', 'request("GET", f"{host}/api/v1/x")'),
        ("base_url=settings.api_url", 'base_url="https://api.github.com"'),
    ],
)
def test_control_a_path_off_api_v1_or_a_second_base_is_reported(edit: str, mentions: str) -> None:
    text = Path(CLI).read_text()
    old, new = edit, mentions
    assert old in text
    assert cli_findings(text.replace(old, new, 1))
    extra = text + "\n\ndef planted():\n    " + mentions + "\n"
    assert cli_findings(extra) or old.startswith("base_url")


def test_control_a_second_client_is_reported() -> None:
    text = Path(CLI).read_text() + "\n\ndef planted():\n    return httpx.Client(base_url='x')\n"
    assert cli_findings(text)


# ---- 4. Configuration ----------------------------------------------------------------------------

SETTINGS_FIELDS: Final = frozenset(
    {
        "alert_hub_token",
        "app_brain_read_key",
        "app_brain_timeout_seconds",
        "app_brain_url",
        "bump_merge_enabled",
        "change_record_timeout_seconds",
        "change_record_token",
        "change_record_url",
        "credential_key_header",
        "csrf_secret",
        "database_url",
        "dead_letter_stalled_approval_seconds",
        "dead_letter_stalled_verification_seconds",
        "dispatch_enabled",
        "dispatch_failure_signature_threshold",
        "dispatch_orchestrator_url",
        "dispatch_workflow_id",
        "dispatch_workflow_ref",
        "email_header",
        "email_to_actor",
        "estate_landing_enabled",
        "execution_stall_grace_seconds",
        "follow_up_due_after_days",
        "github_app_id",
        "github_app_installation_id",
        "github_app_private_key_b64",
        "inert_landing_enabled",
        "m2m_credentials",
        "m2m_roles",
        "proxy_marker",
        "proxy_marker_header",
        "reconcile_split_brain_stall_seconds",
        "registry_bundle",
        "rotation_executor_agent_id",
        "rotation_keychain_executor_agent_id",
        "trusted_proxy_ips",
    }
)
SECRET_FIELDS: Final = frozenset(
    {
        "github_app_private_key_b64",
        "app_brain_read_key",
        "change_record_token",
        "proxy_marker",
        "csrf_secret",
        "alert_hub_token",
    }
)
ENVIRONMENT_READS: Final = {
    "src/orchestrator/api/health.py": frozenset({"ORCHESTRATOR_REVISION"}),
    CLI: frozenset(
        {"ORCHESTRATOR_API_URL", "ORCHESTRATOR_API_TOKEN", "ORCHESTRATOR_API_CREDENTIAL_KEY_ID"}
    ),
    "src/orchestrator/main.py": frozenset({"LOG_LEVEL"}),
    "migrations/env.py": frozenset({"ORCHESTRATOR_DATABASE_URL"}),
}


def _is_secret(annotation: object) -> bool:
    return annotation is SecretStr or SecretStr in get_args(annotation)


def settings_findings(settings: type[Settings]) -> dict[str, object]:
    fields = settings.model_fields
    return {
        name: value
        for name, value in {
            "fields": sorted(set(fields) ^ SETTINGS_FIELDS),
            "prefix": settings.model_config.get("env_prefix"),
            "secrets": sorted(
                {n for n, f in fields.items() if _is_secret(f.annotation)} ^ SECRET_FIELDS
            ),
        }.items()
        if value not in ([], "ORCHESTRATOR_")
    }


def test_settings_are_the_listed_fields_with_the_listed_secrets() -> None:
    assert len(SETTINGS_FIELDS) == 36
    assert settings_findings(Settings) == {}


def test_control_a_planted_field_or_secret_or_prefix_is_reported() -> None:
    class Wider(Settings):
        planted: str = ""
        planted_key: SecretStr | None = None

    assert settings_findings(Wider) == {
        "fields": ["planted", "planted_key"],
        "secrets": ["planted_key"],
    }

    class Quieter(Settings):
        model_config = {**Settings.model_config, "env_prefix": "X_"}

    assert settings_findings(Quieter) == {"prefix": "X_"}


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def environment_reads(sources: Mapping[str, str]) -> dict[str, frozenset[str]]:
    """file -> the variable names read through `os.environ` or `os.getenv`; `*` for the whole."""
    found: dict[str, set[str]] = {}

    def literal(call: ast.Call) -> str:
        first = call.args[0] if call.args else None
        ok = isinstance(first, ast.Constant) and isinstance(first.value, str)
        return str(first.value) if ok and isinstance(first, ast.Constant) else "*"

    for path, text in sources.items():
        tree = _parse(text)
        parents = _parents(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _dotted(node.func) == "os.getenv":
                found.setdefault(path, set()).add(literal(node))
            elif isinstance(node, ast.Attribute) and _dotted(node) == "os.environ":
                parent = parents[node]
                if isinstance(parent, ast.Subscript) and parent.value is node:
                    key = parent.slice
                    name = str(key.value) if isinstance(key, ast.Constant) else "*"
                elif (
                    isinstance(parent, ast.Attribute)
                    and parent.attr in ("get", "setdefault", "pop")
                    and isinstance(parents[parent], ast.Call)
                ):
                    name = literal(parents[parent])  # type: ignore[arg-type]
                else:
                    name = "*"
                found.setdefault(path, set()).add(name)
    return {path: frozenset(names) for path, names in found.items()}


def layer_sources() -> dict[str, str]:
    """The `.py` files the layers manifest gives the platform under src/, and migrations/env.py."""
    return {path: Path(path).read_text() for path in LAYER_FILES}


def _layer_files() -> tuple[str, ...]:
    from scripts.layers import assign, tracked_files

    placed = assign(tracked_files(), load()).layers["platform"]
    return tuple(
        sorted(
            path
            for path in placed
            if path.endswith(".py") and (path.startswith("src/") or path == "migrations/env.py")
        )
    )


LAYER_FILES: Final = _layer_files()


def test_the_raw_environment_readers_are_the_listed_ones() -> None:
    assert environment_reads(layer_sources()) == ENVIRONMENT_READS


def test_control_a_planted_environment_read_is_reported() -> None:
    for planted in (
        'import os\nX = os.environ["X"]\n',
        'import os\nX = os.environ.get("X")\n',
        'import os\nX = os.getenv("X")\n',
        "import os\nX = dict(os.environ)\n",
    ):
        sources = {**layer_sources(), "src/orchestrator/planted.py": planted}
        found = environment_reads(sources)
        assert found["src/orchestrator/planted.py"] in ({"X"}, {"*"})
        assert found != ENVIRONMENT_READS


# ---- 5. Filesystem reads -------------------------------------------------------------------------

FILE_CALLS: Final = frozenset(
    {"read_text", "read_bytes", "open", "Jinja2Templates", "Config", "fileConfig"}
)
FILESYSTEM_READS: Final = {
    # The registry bundle named by `ORCHESTRATOR_REGISTRY_BUNDLE`.
    "src/orchestrator/identity/registry.py": frozenset({"read_text"}),
    # The templates directory.
    "src/orchestrator/web.py": frozenset({"Jinja2Templates"}),
    # `--data @file` and `--context @file`.
    CLI: frozenset({"read_text"}),
    # `alembic.ini`, through alembic's `Config`, for the readiness check's script heads.
    "src/orchestrator/api/health.py": frozenset({"Config"}),
    # `alembic.ini`'s logging section, read when alembic runs a migration.
    "migrations/env.py": frozenset({"fileConfig"}),
}


def filesystem_reads(sources: Mapping[str, str]) -> dict[str, frozenset[str]]:
    found: dict[str, set[str]] = {}
    for path, text in sources.items():
        for node in ast.walk(_parse(text)):
            if isinstance(node, ast.Call) and _callee(node) in FILE_CALLS:
                found.setdefault(path, set()).add(_callee(node))
    return {path: frozenset(names) for path, names in found.items()}


def test_the_files_the_layer_reads_are_the_listed_ones() -> None:
    assert filesystem_reads(layer_sources()) == FILESYSTEM_READS


def test_control_a_planted_read_is_reported() -> None:
    for planted in (
        'from pathlib import Path\nX = Path("x").read_text()\n',
        'X = open("x")\n',
        'from pathlib import Path\nX = Path("x").read_bytes()\n',
    ):
        sources = {**layer_sources(), "src/orchestrator/planted.py": planted}
        assert filesystem_reads(sources) != FILESYSTEM_READS


# ---- 6. Routes that answer without an actor ------------------------------------------------------

UNAUTHENTICATED: Final = frozenset(
    {
        "GET /health/live",
        "GET /health/ready",
        "GET /openapi.json",
        "GET /docs",
        "GET /docs/oauth2-redirect",
        "GET /redoc",
    }
)


def _dependency_calls(dependant: object) -> set[Callable[..., object]]:
    found: set[Callable[..., object]] = set()
    stack = list(getattr(dependant, "dependencies", []))
    while stack:
        current = stack.pop()
        if current.call is not None:
            found.add(current.call)
        stack.extend(current.dependencies)
    return found


def routes_without_actor(routers: Iterable[APIRouter]) -> frozenset[str]:
    """`METHOD path` of every route whose dependant tree, walked recursively, lacks `get_actor`."""
    found: set[str] = set()
    for router in routers:
        for route in router.routes:
            if isinstance(route, APIRoute) and get_actor not in _dependency_calls(route.dependant):
                methods = ",".join(sorted(route.methods or ()))
                found.add(f"{methods} {route.path}")
    return frozenset(found)


def documentation_routes(application: FastAPI) -> frozenset[str]:
    """The routes FastAPI adds itself: plain Starlette routes on the app."""
    return frozenset(
        f"{','.join(sorted((route.methods or set()) - {'HEAD'}))} {route.path}"
        for route in application.routes
        if isinstance(route, Route) and not isinstance(route, APIRoute)
    )


def test_the_routes_that_answer_without_an_actor_are_the_listed_ones() -> None:
    application = create_app()
    routers = [*API_ROUTERS, health_router, web_router]
    assert routes_without_actor(routers) | documentation_routes(application) == UNAUTHENTICATED
    served = {
        route.path for router in routers for route in router.routes if isinstance(route, APIRoute)
    }
    assert set(application.openapi()["paths"]) <= served


def test_control_a_route_with_no_actor_dependency_is_reported() -> None:
    throwaway = APIRouter()

    @throwaway.get("/throwaway")
    def open_route() -> dict[str, str]:
        return {}

    def through() -> None: ...

    def asks(_: None = Depends(through), actor: object = Depends(get_actor)) -> None: ...

    @throwaway.get("/guarded")
    def guarded(_: None = Depends(asks)) -> dict[str, str]:
        return {}

    routers = [*API_ROUTERS, health_router, web_router, throwaway]
    assert routes_without_actor(routers) - routes_without_actor(
        [*API_ROUTERS, health_router, web_router]
    ) == {"GET /throwaway"}


# ---- 7. Handlers and the alert send --------------------------------------------------------------

HANDLERS: Final = frozenset({APIAuthenticationError, DomainError, Exception})
STATUS_TABLE: Final = {
    "csrf_unavailable": 503,
    "factory_policy_invalid": 503,
    "factory_policy_version_unsupported": 503,
    "named_check_observation_unavailable": 503,
    "role_forbidden": 403,
    "human_actor_required": 403,
    "csrf_rejected": 403,
    "intake_registrar_invalid": 403,
}
STATUS_PROBES: Final = {
    "thing_not_found": 404,
    "intake_change_record_required": 409,
    "anything_else": 409,
}
ALERT_ROUTES: Final = frozenset({("POST", "/v1/alerts")})
ALERT_BASE_URL: Final = "https://alerts.alobar.net"


def handler_set(application: FastAPI) -> frozenset[type[BaseException]]:
    return frozenset(application.exception_handlers) - frozenset(FastAPI().exception_handlers)


def status_disagreements(status_for: Callable[[str], int]) -> dict[str, tuple[int, int]]:
    expected = {**STATUS_TABLE, **STATUS_PROBES}
    return {
        code: (status_for(code), want)
        for code, want in expected.items()
        if status_for(code) != want
    }


def alert_permits() -> tuple[str, Permits]:
    """The base URL and predicate of the client `fire_once` really builds, captured."""
    alert = alert_hub_client.Alert(title="t", message="m")
    key = alert_hub_client.alert_key("server-error", "/x", "E")
    [client] = built_clients(
        alert_hub_client,
        lambda: alert_hub_client.fire_once("token", key, alert, user_agent="edges/1", timeout=1.0),
    )
    return client.base_url, client.permits


def permitted(permits: Permits) -> frozenset[tuple[str, str]]:
    corpus = near_misses({"/v1/alerts", "/v1/alerts/x", "/v1/alert", "/health/live", "/"})
    methods = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD")
    return frozenset(
        (method, path)
        for method, path in product(methods, corpus)
        if sendable(permits, method, path)
    )


def alert_importers(sources: Mapping[str, str]) -> frozenset[str]:
    return frozenset(
        path
        for path, text in sources.items()
        if any(
            _imported(node) == "alert_hub_client"
            for node in ast.walk(_parse(text))
            if isinstance(node, ast.Import | ast.ImportFrom)
        )
    )


def _imported(node: ast.Import | ast.ImportFrom) -> str:
    if isinstance(node, ast.ImportFrom):
        return (node.module or "").split(".")[0] if node.level == 0 else ""
    return node.names[0].name.split(".")[0]


SERVER_ERRORS = "src/orchestrator/api/server_errors.py"


def test_the_handlers_beyond_fastapis_defaults_are_the_listed_ones() -> None:
    assert handler_set(create_app()) == HANDLERS


def test_the_error_statuses_are_the_listed_ones() -> None:
    assert status_disagreements(error_shape.status_for) == {}
    assert {c: 403 for c in error_shape._FORBIDDEN} | {
        c: 503 for c in error_shape._UNAVAILABLE
    } == STATUS_TABLE


def test_the_one_alert_the_500_handler_sends_is_post_v1_alerts_at_alert_hub() -> None:
    base_url, permits = alert_permits()
    assert base_url == ALERT_BASE_URL
    assert permitted(permits) == ALERT_ROUTES


def test_server_errors_sends_only_through_fire_once() -> None:
    tree = _parse(Path(SERVER_ERRORS).read_text())
    sends = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _callee(node) == "fire_once"
    ]
    assert len(sends) == 1
    assert not [
        n for n in ast.walk(tree) if isinstance(n, ast.Call) and _callee(n) == "ConfinedClient"
    ]
    assert server_errors_module.fire_once is alert_hub_client.fire_once


def test_server_errors_is_the_one_platform_file_importing_alert_hub_client() -> None:
    assert alert_importers(layer_sources()) == {SERVER_ERRORS}


def test_control_an_added_handler_is_reported() -> None:
    application = create_app()

    @application.exception_handler(KeyError)
    async def planted(_request: object, _error: KeyError) -> None: ...

    assert handler_set(application) - HANDLERS == {KeyError}


def test_control_a_widened_status_map_is_reported() -> None:
    def widened(code: str) -> int:
        return 418 if code == "role_forbidden" else error_shape.status_for(code)

    assert status_disagreements(widened) == {"role_forbidden": (418, 403)}


def test_control_a_widened_alert_predicate_is_reported() -> None:
    def widened(method: str, path: str) -> bool:
        return path.startswith("/v1/alerts") or (method, path) == ("POST", "/v1/alerts")

    assert permitted(widened) != ALERT_ROUTES
    assert permitted(lambda method, path: (method, path) == ("POST", "/v1/alerts")) == ALERT_ROUTES


def test_control_a_second_importer_of_alert_hub_client_is_reported() -> None:
    sources = {
        **layer_sources(),
        "src/orchestrator/planted.py": "from alert_hub_client import fire_once\n",
    }
    assert alert_importers(sources) == {SERVER_ERRORS, "src/orchestrator/planted.py"}


# ---- 8. The population ---------------------------------------------------------------------------

# The files above that name a row, and which row.
PLACED: Final = {
    GITHUB_APP: "the mint",
    COMMON_ROUTES: "the credential resolver the token callers use",
    CLI: "the CLI, the environment and file rows",
    "src/orchestrator/config.py": "the Settings fields",
    "src/orchestrator/main.py": "LOG_LEVEL, the handlers and the app",
    "src/orchestrator/api/health.py": "the revision, alembic.ini and the open health routes",
    "src/orchestrator/identity/registry.py": "the registry bundle",
    "src/orchestrator/web.py": "the templates and the review routes",
    "src/orchestrator/api/server_errors.py": "the handler and its one alert",
    "src/orchestrator/api/error_shape.py": "the status map",
    "src/orchestrator/api/dependencies.py": "get_actor, which the routes row looks for",
    "migrations/env.py": "ORCHESTRATOR_DATABASE_URL and alembic.ini's logging",
}
# The files that reach nothing outside the process and its database: types, the kernel's rules, the
# persistence models, the clock, the engine and the package markers. Each is held to that below.
REACHES_NOTHING: Final = frozenset(
    {
        "src/orchestrator/__init__.py",
        "src/orchestrator/api/__init__.py",
        "src/orchestrator/api/routes/__init__.py",
        "src/orchestrator/api/schemas/__init__.py",
        "src/orchestrator/api/schemas/common.py",
        "src/orchestrator/clock.py",
        "src/orchestrator/db.py",
        "src/orchestrator/errors.py",
        "src/orchestrator/identity/__init__.py",
        "src/orchestrator/identity/auth.py",
        "src/orchestrator/services/__init__.py",
    }
)
REACHES_NOTHING_PACKAGES: Final = ("src/orchestrator/kernel/", "src/orchestrator/persistence/")
OUTBOUND_MODULES: Final = frozenset(
    {
        "aiohttp",
        "alert_hub_client",
        "estate_clients",
        "http",
        "httpx",
        "requests",
        "smtplib",
        "socket",
        "subprocess",
        "urllib3",
    }
)
# The layer's files that import a way out, and which way.
OUTBOUND_IMPORTS: Final = {
    GITHUB_APP: frozenset({"httpx"}),
    CLI: frozenset({"httpx"}),
    SERVER_ERRORS: frozenset({"alert_hub_client", "httpx"}),
}


def unplaced(files: Iterable[str]) -> list[str]:
    return sorted(
        path
        for path in files
        if path not in PLACED
        and path not in REACHES_NOTHING
        and not path.startswith(REACHES_NOTHING_PACKAGES)
    )


def outbound_imports(sources: Mapping[str, str]) -> dict[str, frozenset[str]]:
    found: dict[str, set[str]] = {}
    for path, text in sources.items():
        for node in ast.walk(_parse(text)):
            if isinstance(node, ast.Import | ast.ImportFrom):
                top = _imported(node)
                if top in OUTBOUND_MODULES:
                    found.setdefault(path, set()).add(top)
    return {path: frozenset(names) for path, names in found.items()}


def test_every_file_the_layer_owns_is_placed_by_a_row_or_reaches_nothing() -> None:
    assert unplaced(LAYER_FILES) == []
    assert set(PLACED) <= set(LAYER_FILES)
    assert REACHES_NOTHING <= set(LAYER_FILES)
    assert not set(PLACED) & REACHES_NOTHING


def test_the_files_that_reach_nothing_import_no_way_out() -> None:
    nothing = {
        path: text
        for path, text in layer_sources().items()
        if path in REACHES_NOTHING or path.startswith(REACHES_NOTHING_PACKAGES)
    }
    assert nothing
    assert outbound_imports(nothing) == {}


def test_the_layers_outbound_imports_are_the_listed_ones() -> None:
    assert outbound_imports(layer_sources()) == OUTBOUND_IMPORTS
    assert set(OUTBOUND_IMPORTS) <= set(PLACED)


def test_the_layer_modules_the_rows_read_are_the_whole_import_of_the_http_client() -> None:
    importers = {
        path
        for path in LAYER_FILES
        if any(i.top in {"httpx", "requests", "urllib3"} for i in file_imports(Path(path)))
    }
    assert importers == set(OUTBOUND_IMPORTS)


def test_control_a_new_platform_file_is_unplaced() -> None:
    assert unplaced([*LAYER_FILES, "src/orchestrator/new_thing.py"]) == [
        "src/orchestrator/new_thing.py"
    ]


def test_control_a_way_out_in_a_file_that_reaches_nothing_is_reported() -> None:
    sources = {"src/orchestrator/clock.py": "import httpx\n"}
    assert outbound_imports(sources) == {"src/orchestrator/clock.py": frozenset({"httpx"})}


def test_the_manifest_places_this_file_in_the_platform_layer() -> None:
    assert "tests/architecture/test_platform_edges.py" in {
        pattern for pattern in load()["platform"].paths
    }
