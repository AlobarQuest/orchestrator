"""A package's content hash, recomputed exactly as intent-packages computes it.

ADR-0055 decision 1: the executor checks the revision it works against intent-packages' git, so
it recomputes the content hash the orchestrator holds rather than trusting it (T3). This is a copy
of `intent_packages/canonical.py`: RFC 8785 canonical JSON of the package without `status`, then
sha256. A copy rather than a dependency, so the executor installs nothing from the repository it
is checking. `tests/rotation_worker/test_checks.py` pins it to `rotation-openrouter-generic`
revision 1's approved hash; if intent-packages changes its algorithm, that test fails.
"""

from __future__ import annotations

import hashlib
from typing import Any

_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


class CanonicalError(ValueError):
    """A package holds something intent-packages refuses to hash."""


def package_hash(package: dict[str, Any]) -> str:
    core = {key: value for key, value in package.items() if key != "status"}
    return hashlib.sha256(_canon(core).encode("utf-8")).hexdigest()


def _canon(value: object) -> str:
    if value is None or value is True or value is False:
        return {None: "null", True: "true", False: "false"}[value]
    if isinstance(value, float):
        raise CanonicalError("floats are not allowed in intent packages")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, list):
        return "[" + ",".join(_canon(item) for item in value) + "]"
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise CanonicalError("a mapping key is not a string")
        # RFC 8785 orders members by UTF-16 code units, which differs from code points outside
        # the BMP.
        items = sorted(value.items(), key=lambda item: item[0].encode("utf-16-be"))
        return "{" + ",".join(f"{_string(key)}:{_canon(item)}" for key, item in items) + "}"
    raise CanonicalError(f"unhashable type in package: {type(value).__name__}")


def _string(text: str) -> str:
    out = ['"']
    for character in text:
        if character in _ESCAPES:
            out.append(_ESCAPES[character])
        elif ord(character) < 0x20:
            out.append(f"\\u{ord(character):04x}")
        else:
            out.append(character)
    out.append('"')
    return "".join(out)
