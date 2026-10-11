"""The destination string: one registry consumer, written as the package and the envelope carry it.

ADR-0055 amendment 2 puts a credential's full destination list in three places: the package
revision's `profile_fields.destinations` (Devon reads its diff), each rotation unit's envelope
`constraints` (Devon reads it on the authenticated `/review` page), and the credential's registry
entry (`.cred-consumers.toml`, which merges without review). The executor acts only when all three
are equal, so the three must be written in one format, and that format is defined here.

**ONE FLAT STRING PER DESTINATION**: the credential's provider, then each consumer. A string is a
`kind`, then each of the kind's identity fields as
`name=<JSON value>`, separated by single spaces, in the fixed order `IDENTITY_FIELDS` gives:

    bws-secret uuid="9661da8f-ac66-4e97-a31f-b42500bb849c"
    coolify-env instance="prod" resource_type="application" uuid="abc" key="FOO" redeploy=true

Values are JSON because the registry's free-text fields hold spaces and commas (a `runtime-fetch`
`ref` is a sentence). `note` is not identity and never appears. `redeploy` does: it decides whether
the executor restarts a hosted service, so a registry edit to it is a change Devon must approve,
not one that passes the comparison silently.

`tests/fixtures/rotation_destination_format.json` pins the table and examples, byte-identical with
intent-packages, whose `destinations` validation re-renders each string and refuses one that
doesn't come back unchanged.
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Mapping
from typing import Any, Final

# The kinds `.cred-consumers.toml` documents, each with the fields that say WHERE the value goes,
# plus `provider`: the credential's own `provider` field, which picks who mints and revokes it.
IDENTITY_FIELDS: Final[Mapping[str, tuple[str, ...]]] = {
    "provider": ("name",),
    "bws-secret": ("uuid",),
    "coolify-env": ("instance", "resource_type", "uuid", "key", "redeploy"),
    "coolify-env-hash": ("instance", "resource_type", "uuid", "key"),
    "gh-actions-secret": ("repo", "name"),
    "keychain": ("service", "account"),
    "runtime-fetch": ("repo", "ref"),
    "shell-export": ("file", "var"),
}
BOOLEAN_FIELDS: Final = frozenset({"redeploy"})
# Kinds whose write lands in a hosted service (ADR-0055 amendment 2: a unit marked non-hosted must
# write none of them).
HOSTED_KINDS: Final = frozenset({"coolify-env", "coolify-env-hash"})


class DestinationError(ValueError):
    """A consumer or string that doesn't fit the format."""


def render(consumer: Mapping[str, Any]) -> str:
    """The destination string for one registry consumer table."""
    kind = consumer.get("kind")
    if not isinstance(kind, str) or kind not in IDENTITY_FIELDS:
        raise DestinationError(f"unknown consumer kind {kind!r}")
    fields = IDENTITY_FIELDS[kind]
    parts = [kind]
    for name in fields:
        # infraops reads a coolify-env consumer without `redeploy` as not restarting.
        value = consumer.get(name, False if name in BOOLEAN_FIELDS else None)
        _check_value(kind, name, value)
        parts.append(f"{name}={json.dumps(value, ensure_ascii=False)}")
    return " ".join(parts)


def credential_destinations(credential: Mapping[str, Any]) -> list[str]:
    """A registry entry's provider and consumers, as the sorted, unique list a package carries."""
    consumers = credential.get("consumer", [])
    if not isinstance(consumers, list) or not all(isinstance(c, Mapping) for c in consumers):
        raise DestinationError("a credential's consumers must be a list of tables")
    provider = {"kind": "provider", "name": credential.get("provider")}
    rendered = [render(provider), *(render(consumer) for consumer in consumers)]
    if len(set(rendered)) != len(rendered):
        raise DestinationError("two consumers render to the same destination")
    return sorted(rendered)


def parse(destination: str) -> dict[str, Any]:
    """The consumer a destination string names. It must be exactly what `render` writes."""
    kind, _, rest = destination.partition(" ")
    fields = IDENTITY_FIELDS.get(kind)
    if fields is None:
        raise DestinationError(f"unknown consumer kind {kind!r}")
    consumer: dict[str, Any] = {"kind": kind}
    decoder = json.JSONDecoder()
    position = 0
    for name in fields:
        prefix = f"{name}="
        if not rest.startswith(prefix, position):
            raise DestinationError(f"{kind}: expected field {name!r}")
        try:
            value, position = decoder.raw_decode(rest, position + len(prefix))
        except json.JSONDecodeError as error:
            raise DestinationError(f"{kind}: {name} is not a JSON value") from error
        consumer[name] = value
        position += rest.startswith(" ", position)
    # Anything the loop let through -- trailing text, a missing or doubled space, an escaped
    # character -- makes the re-rendered string differ, so this one comparison refuses it.
    if render(consumer) != destination:
        raise DestinationError(f"{destination!r} is not in canonical form")
    return consumer


def is_hosted(destination: str) -> bool:
    return parse(destination)["kind"] in HOSTED_KINDS


def _check_value(kind: str, name: str, value: object) -> None:
    if name in BOOLEAN_FIELDS:
        if not isinstance(value, bool):
            raise DestinationError(f"{kind}: {name} must be true or false")
    elif not isinstance(value, str) or not value.strip() or value != value.strip():
        raise DestinationError(f"{kind}: {name} must be a non-blank, trimmed string")
    # Two spellings of one character would read the same on the page and compare unequal.
    elif not unicodedata.is_normalized("NFC", value):
        raise DestinationError(f"{kind}: {name} must be NFC-normalised")
