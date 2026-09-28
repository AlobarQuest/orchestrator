"""The single detector for secret-shaped content in metadata an ingress is about to store.

Four ingresses stored caller-supplied metadata -- release artifacts, infra-lane links (deleted by
Tier 3 item 24a), observations and rollout observations -- and each used to carry its own copy
of this walk with its own key list: four parts in two of them, ten in a third, eleven in the
fourth. A key refused at one door was accepted at the next. There is now one list, the union of
the four, so tightening happened only at the two narrow ingresses and nothing any of them refused
before is accepted now.

Keys are matched by SUBSTRING, case-insensitively, because the thing being refused is a field
that names a credential or a raw transcript, and `api_token`, `AuthToken` and `token_hint` all
name one. Values are matched on two shapes only: an HTTP bearer header, and the bootstrap-token
shape. Anything else a value contains is the ingress's own bounds to judge.
"""

from __future__ import annotations

import re

# The bootstrap-token shape: `0.` then a UUID then a dot-separated secret. Written as a pattern,
# never as an example, so no token-shaped literal sits in the tree.
BWS_TOKEN_SHAPE = re.compile(r"\b0\.[0-9a-fA-F-]{36}\.[A-Za-z0-9_=-]{8,}")

# Iterated with `any(part in key ...)`, never tested for membership, so this is a list of
# substrings rather than a vocabulary another system must agree with.
SECRET_KEY_PARTS = (
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


def secret_metadata_path(
    value: object,
    path: str = "$",
    *,
    max_string: int | None = None,
) -> str | None:
    """Return the JSON path of the first secret-shaped key or value, or None if there is none.

    `max_string`, when given, also refuses any string longer than it: an unbounded string is
    where a pasted log or response body arrives, so the rollout observation ingress treats length
    as a secret shape. The other ingresses bound their strings elsewhere and pass nothing.
    """
    if isinstance(value, dict):
        return _dict_path(value, path, max_string)
    if isinstance(value, list):
        return _list_path(value, path, max_string)
    if isinstance(value, str):
        return _string_path(value, path, max_string)
    return None


def _dict_path(value: dict[object, object], path: str, max_string: int | None) -> str | None:
    for key, child in value.items():
        key_text = str(key)
        child_path = f"{path}.{key_text}"
        lowered = key_text.lower()
        if any(part in lowered for part in SECRET_KEY_PARTS):
            return child_path
        found = secret_metadata_path(child, child_path, max_string=max_string)
        if found is not None:
            return found
    return None


def _list_path(value: list[object], path: str, max_string: int | None) -> str | None:
    for index, child in enumerate(value):
        found = secret_metadata_path(child, f"{path}[{index}]", max_string=max_string)
        if found is not None:
            return found
    return None


def _string_path(value: str, path: str, max_string: int | None) -> str | None:
    if max_string is not None and len(value) > max_string:
        return path
    if "authorization: bearer " in value.lower() or BWS_TOKEN_SHAPE.search(value):
        return path
    return None
