"""The one secret-shape detector every metadata ingress asks.

Until it was consolidated there were four copies with three different key lists, so a key one
ingress refused another accepted. These tests pin the union, each branch of the walk, and the one
ingress-specific option (a string length bound), and the structural test at the end pins that no
service keeps a copy of its own.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from orchestrator.kernel.secret_metadata import SECRET_KEY_PARTS, secret_metadata_path

SERVICES = Path(__file__).resolve().parents[2] / "src" / "orchestrator" / "services"

# A literal, not derived from the constant: the list IS the judgment, and a test built by
# iterating the constant would shrink with it.
EXPECTED_PARTS = (
    "api_key",
    "authorization",
    "bearer",
    "body",
    "credential",
    "instruction",
    "log",
    "password",
    "response",
    "secret",
    "token",
)


def test_the_key_list_is_the_union_of_every_former_copy() -> None:
    assert SECRET_KEY_PARTS == EXPECTED_PARTS


@pytest.mark.parametrize("part", EXPECTED_PARTS)
def test_a_key_containing_any_part_is_refused_at_its_path(part: str) -> None:
    key = f"x_{part.upper()}_y"

    assert secret_metadata_path({"outer": {key: "value"}}) == f"$.outer.{key}"


def test_a_clean_payload_has_no_secret_path() -> None:
    payload = {
        "repository": "AlobarQuest/orchestrator",
        "key_id": "orchestrator-system",
        "facts": [{"head": "abc", "count": 3, "ok": True, "none": None}],
    }

    assert secret_metadata_path(payload) is None


def test_a_list_is_walked_with_its_index() -> None:
    assert secret_metadata_path({"items": [{"a": 1}, {"password": "x"}]}) == "$.items[1].password"


def test_a_bearer_header_value_is_refused() -> None:
    assert secret_metadata_path({"url": "x AUTHORIZATION: Bearer abc"}) == "$.url"


def test_a_bws_shaped_value_is_refused() -> None:
    # Assembled at runtime so no token-shaped literal sits in the tree.
    shaped = "0." + "0123abcd-0123-4567-89ab-0123456789ab" + "." + "notrealfixture"

    assert secret_metadata_path({"nested": [{"value": shaped}]}) == "$.nested[0].value"


def test_a_top_level_string_is_inspected() -> None:
    assert secret_metadata_path("authorization: bearer abc") == "$"


def test_a_string_over_the_bound_is_refused_only_when_a_bound_is_given() -> None:
    long_value = "a" * 513

    assert secret_metadata_path({"v": long_value}) is None
    assert secret_metadata_path({"v": long_value}, max_string=512) == "$.v"
    assert secret_metadata_path({"v": "a" * 512}, max_string=512) is None


def test_the_string_bound_reaches_strings_inside_lists() -> None:
    assert secret_metadata_path({"v": ["ok", "a" * 513]}, max_string=512) == "$.v[1]"


def test_no_service_keeps_a_copy_of_the_detector() -> None:
    """A second copy is how the four lists drifted apart; the kernel module is the only one."""
    copies = []
    for path in sorted(SERVICES.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Assign):
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            elif isinstance(node, ast.FunctionDef):
                names = [node.name]
            for name in names:
                if name in {"SECRET_KEY_PARTS", "BWS_TOKEN_SHAPE", "_secret_metadata_path"}:
                    copies.append(f"{path.name}:{name}")

    assert copies == []
