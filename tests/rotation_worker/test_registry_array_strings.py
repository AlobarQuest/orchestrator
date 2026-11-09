"""The registry's array strings hold no comma, because infraops' reader splits arrays on one.

infraops reads `.cred-consumers.toml` with its own TOML subset (`parseValue` in
`src/security-drift/cred-consumers.ts`), which splits an array's body on every `,` before it
checks for quotes. A comma inside a quoted string makes the whole registry unreadable: the 03:00
scan then posts one `cred.registry-error` and nothing else from any registry, and `cred-findings`
throws, so the rotation proposer exits 2.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from rotation_worker.registry import REGISTRY_PATH

ROOT = Path(__file__).resolve().parents[2]


def _array_strings(node: Any, path: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _array_strings(value, f"{path}.{key}" if path else key)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            if isinstance(item, str):
                yield f"{path}[{index}]", item
            else:
                yield from _array_strings(item, f"{path}[{index}]")


def commas_in_array_strings(text: str) -> list[str]:
    return [path for path, item in _array_strings(tomllib.loads(text)) if "," in item]


def test_no_array_string_in_the_registry_holds_a_comma() -> None:
    assert commas_in_array_strings((ROOT / REGISTRY_PATH).read_text()) == []


def test_the_check_finds_a_comma_inside_an_array_string() -> None:
    text = 'version = 1\n[[credential]]\nid = "x"\nrotation_preconditions = ["a, b", "c"]\n'
    assert commas_in_array_strings(text) == ["credential[0].rotation_preconditions[0]"]
