"""Architecture guard: every /review handler is gated by `_human`, every POST by `_require_form`.

ADR-0006's human gate is `_human(actor)` and the CSRF-plus-confirmation gate is `_require_form`.
A handler written without either is open to a worker credential or to a forged form, and nothing
else would notice, so one scan of `web.py` pins both:

- every function decorated `@router.get(...)` or `@router.post(...)` calls `_human(actor)` as a
  direct statement of its body (a call inside a branch is not the gate);
- every `@router.post` function calls `_require_form(...)` as a direct statement of its body too
  (a call inside an `if`, an `except` or a nested `def` does not run for every request);
- the decorated set equals the router's registered endpoints, so a route registered any other way
  is not missed.
"""

import ast
from pathlib import Path

from fastapi.routing import APIRoute

import orchestrator.web as web

_SOURCE = Path(web.__file__).read_text()


def _decorated(tree: ast.Module) -> dict[str, tuple[str, ast.FunctionDef]]:
    """Function name -> (HTTP method, node) for each `@router.get/post` function."""
    found: dict[str, tuple[str, ast.FunctionDef]] = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "router"
                and target.attr in {"get", "post"}
            ):
                found[node.name] = (target.attr, node)
    return found


def _call_name(call: ast.Call) -> str | None:
    return call.func.id if isinstance(call.func, ast.Name) else None


def _has_direct_call(function: ast.FunctionDef, name: str, *, argument: str | None = None) -> bool:
    """Whether a top-level statement of the body is a bare call of `name` (on `argument`)."""
    for statement in function.body:
        if (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Call)
            and _call_name(statement.value) == name
        ):
            args = statement.value.args
            if argument is None or (
                args and isinstance(args[0], ast.Name) and args[0].id == argument
            ):
                return True
    return False


def violations(source: str) -> list[str]:
    """Every handler in `source` missing its `_human` or (for a POST) `_require_form` call."""
    problems: list[str] = []
    for name, (method, node) in sorted(_decorated(ast.parse(source)).items()):
        if not _has_direct_call(node, "_human", argument="actor"):
            problems.append(f"{name}: no direct _human(actor) statement")
        if method == "post" and not _has_direct_call(node, "_require_form"):
            problems.append(f"{name}: POST without a direct _require_form statement")
    return problems


def test_every_review_handler_is_gated() -> None:
    assert violations(_SOURCE) == []


def test_the_scan_sees_every_registered_route() -> None:
    decorated = set(_decorated(ast.parse(_SOURCE)))
    registered = {
        route.endpoint.__name__ for route in web.router.routes if isinstance(route, APIRoute)
    }
    assert decorated == registered
    assert len(decorated) == 26
    assert sum(1 for method, _ in _decorated(ast.parse(_SOURCE)).values() if method == "post") == 17


_GOOD_POST = """
@router.post("/x")
def handler(actor):
    _human(actor)
    _require_form(1)
"""


def test_the_scan_accepts_a_gated_post() -> None:
    assert violations(_GOOD_POST) == []


def test_the_scan_reports_a_post_without_require_form() -> None:
    source = _GOOD_POST.replace("    _require_form(1)\n", "")
    assert violations(source) == ["handler: POST without a direct _require_form statement"]


def test_the_scan_reports_a_get_without_human() -> None:
    source = '@router.get("/x")\ndef handler(actor):\n    return 1\n'
    assert violations(source) == ["handler: no direct _human(actor) statement"]


def test_the_scan_reports_a_human_call_hidden_in_a_branch() -> None:
    source = '@router.get("/x")\ndef handler(actor):\n    if actor:\n        _human(actor)\n'
    assert violations(source) == ["handler: no direct _human(actor) statement"]


def test_the_scan_reports_a_human_call_on_something_other_than_actor() -> None:
    source = _GOOD_POST.replace("_human(actor)", "_human(other)")
    assert violations(source) == ["handler: no direct _human(actor) statement"]


def test_the_scan_reports_a_require_form_only_in_a_branch() -> None:
    source = _GOOD_POST.replace(
        "    _require_form(1)\n", "    if actor:\n        _require_form(1)\n"
    )
    assert violations(source) == ["handler: POST without a direct _require_form statement"]


def test_the_scan_reports_a_require_form_only_in_an_except_clause() -> None:
    source = _GOOD_POST.replace(
        "    _require_form(1)\n",
        "    try:\n        pass\n    except ValueError:\n        _require_form(1)\n",
    )
    assert violations(source) == ["handler: POST without a direct _require_form statement"]


def test_the_scan_reports_a_require_form_only_in_a_nested_def() -> None:
    source = _GOOD_POST.replace(
        "    _require_form(1)\n", "    def never_runs():\n        _require_form(1)\n"
    )
    assert violations(source) == ["handler: POST without a direct _require_form statement"]
