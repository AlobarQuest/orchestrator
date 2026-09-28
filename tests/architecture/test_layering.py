"""The inner layers import nothing from the layers that sit on top of them.

`kernel` holds the state machine and the value types every other layer speaks; `persistence`
maps them to tables; `services` owns the business rules and the models it returns. The HTTP
surface (`api`), the HTML surface (`web`), process wiring (`main`, `cli`) and configuration
(`config`, `db`, `identity`) are built ON those layers and hand them plain values.

Until the Tier-2 layering change, three services imported their response models from
`api.schemas`, `github_app` read `Settings` itself, and the two-field `ActorContext` every
service passes around lived in `services/lifecycle`, so modules needing only that value type
imported the lifecycle service to get it (it is now in `kernel.states`; the auth module's own,
larger class of the same name is `AuthenticatedIdentity`). Each was a dependency pointing the
wrong way, and nothing said so. This module is what says so now.

Two shapes of rule, deliberately different:

* `kernel` and `persistence` are ALLOWLISTS - they are small and foundational, so any new
  internal dependency is a decision worth making in this file.
* `services` is a DENYLIST of the layers above it - services legitimately reach many sibling
  top-level modules (`factory_policy`, `reach_vocabulary`, ...), and the invariant is only that
  none of them is a consumer of services.

Function-local imports and `TYPE_CHECKING` blocks count: `ast.walk` sees both, and a deferred
import is still a dependency. A literal `import_module("orchestrator....")` counts too.
"""

import ast
from pathlib import Path

import pytest

SOURCE_ROOT = Path("src/orchestrator")

KERNEL_MAY_IMPORT = frozenset({"errors", "kernel"})
PERSISTENCE_MAY_IMPORT = frozenset({"kernel", "persistence"})
SERVICES_MUST_NOT_IMPORT = frozenset({"api", "web", "main", "cli", "config", "db", "identity"})


def _internal_imports(path: Path) -> set[str]:
    """Return the second component of every `orchestrator.<x>` a file imports.

    A relative import is refused outright rather than resolved: the source tree uses none, and a
    first one would otherwise be a way past this check.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    targets: set[str] = set()
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"{path}:{node.lineno} uses a relative import"
            if node.module == "orchestrator":
                # `from orchestrator import config` names the layer in the imported name.
                modules = [f"orchestrator.{alias.name}" for alias in node.names]
            else:
                modules = [node.module or ""]
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name | ast.Attribute)
            and (node.func.id if isinstance(node.func, ast.Name) else node.func.attr)
            == "import_module"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            modules = [node.args[0].value]
        for module in modules:
            parts = module.split(".")
            if parts[0] != "orchestrator":
                continue
            # A bare `import orchestrator` reaches every layer by attribute access, which no
            # import scan can attribute to one; the source tree uses none, so refuse it.
            assert len(parts) > 1, f"{path}:{node.lineno} imports the bare orchestrator package"
            targets.add(parts[1])
    return targets


def _layer_imports(layer: str) -> dict[str, set[str]]:
    files = sorted((SOURCE_ROOT / layer).rglob("*.py"))
    assert files, f"no source files under {SOURCE_ROOT / layer} - the layer moved"
    return {str(path): _internal_imports(path) for path in files}


@pytest.mark.parametrize(
    ("layer", "allowed"),
    [("kernel", KERNEL_MAY_IMPORT), ("persistence", PERSISTENCE_MAY_IMPORT)],
)
def test_a_foundational_layer_imports_only_what_its_allowlist_names(
    layer: str, allowed: frozenset[str]
) -> None:
    offenders = {
        path: sorted(targets - allowed)
        for path, targets in _layer_imports(layer).items()
        if targets - allowed
    }

    assert offenders == {}


def test_services_import_nothing_from_the_layers_built_on_them() -> None:
    offenders = {
        path: sorted(targets & SERVICES_MUST_NOT_IMPORT)
        for path, targets in _layer_imports("services").items()
        if targets & SERVICES_MUST_NOT_IMPORT
    }

    assert offenders == {}


def test_every_denied_name_is_a_real_module() -> None:
    """A deny entry naming nothing would deny nothing; keep the list honest as modules move."""
    present = {path.stem for path in SOURCE_ROOT.glob("*.py")} | {
        path.name for path in SOURCE_ROOT.iterdir() if (path / "__init__.py").exists()
    }

    assert SERVICES_MUST_NOT_IMPORT <= present
    assert KERNEL_MAY_IMPORT <= present
    assert PERSISTENCE_MAY_IMPORT <= present


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("from orchestrator.api.schemas import X\n", {"api"}),
        ("import orchestrator.config\n", {"config"}),
        ("def f():\n    from orchestrator.web import app\n", {"web"}),
        (
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n    from orchestrator.api import routes\n",
            {"api"},
        ),
        ("import importlib\nimportlib.import_module('orchestrator.main')\n", {"main"}),
        ("from importlib import import_module\nimport_module('orchestrator.cli')\n", {"cli"}),
        ("import httpx\nfrom orchestrator.kernel.states import ActorRole\n", {"kernel"}),
        ("from orchestrator import config\n", {"config"}),
        ("from orchestrator import api, errors\n", {"api", "errors"}),
    ],
)
def test_the_scanner_sees_every_import_shape(
    tmp_path: Path, source: str, expected: set[str]
) -> None:
    path = tmp_path / "module.py"
    path.write_text(source)

    assert _internal_imports(path) == expected


@pytest.mark.parametrize(
    ("source", "refusal"),
    [
        ("from ..api import schemas\n", "relative import"),
        ("import orchestrator\n", "bare orchestrator package"),
    ],
)
def test_the_scanner_refuses_an_import_it_cannot_attribute(
    tmp_path: Path, source: str, refusal: str
) -> None:
    path = tmp_path / "module.py"
    path.write_text(source)

    with pytest.raises(AssertionError, match=refusal):
        _internal_imports(path)
