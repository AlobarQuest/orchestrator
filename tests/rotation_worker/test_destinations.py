"""The destination-string format, held to the fixture intent-packages shares.

`tests/fixtures/rotation_destination_format.json` is a byte-identical copy of the file of the same
name in AlobarQuest/intent-packages, and `DESTINATION_FORMAT_SHA256` is identical in both repos'
tests, so a one-sided change to the format fails here rather than at the executor's first claim.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from rotation_worker.destinations import (
    BOOLEAN_FIELDS,
    HOSTED_KINDS,
    IDENTITY_FIELDS,
    DestinationError,
    credential_destinations,
    is_hosted,
    parse,
    render,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "rotation_destination_format.json"
DESTINATION_FORMAT_SHA256 = "72515b3126099a064ae5c12b504e2d41114881def9e81c7545e288ef17d2b2db"


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text())


def test_the_fixture_is_unchanged() -> None:
    """A one-sided edit here means intent-packages' copy has silently drifted."""
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == DESTINATION_FORMAT_SHA256


def test_the_tables_are_the_fixture_s() -> None:
    fixture = _fixture()
    assert {kind: list(fields) for kind, fields in IDENTITY_FIELDS.items()} == fixture[
        "identity_fields"
    ]
    assert sorted(BOOLEAN_FIELDS) == fixture["boolean_fields"]
    assert sorted(HOSTED_KINDS) == fixture["hosted_kinds"]


@pytest.mark.parametrize("example", _fixture()["examples"], ids=lambda e: e["destination"][:40])
def test_each_example_renders_and_parses_back(example: dict[str, Any]) -> None:
    assert render(example["consumer"]) == example["destination"]
    assert parse(example["destination"]) == example["consumer"]


@pytest.mark.parametrize("destination", _fixture()["refused"])
def test_each_refused_string_is_refused(destination: str) -> None:
    with pytest.raises(DestinationError):
        parse(destination)


def test_note_is_not_identity() -> None:
    consumer = {"kind": "bws-secret", "uuid": "u", "note": "OPENROUTER_API_KEY (Ops / Platform)"}
    assert render(consumer) == 'bws-secret uuid="u"'


def test_redeploy_is_identity() -> None:
    """A registry edit that turns a restart on must change the string Devon approved."""
    base = {"kind": "coolify-env", "instance": "prod", "resource_type": "application"}
    off = render({**base, "uuid": "u", "key": "K", "redeploy": False})
    on = render({**base, "uuid": "u", "key": "K", "redeploy": True})
    assert off != on


@pytest.mark.parametrize(
    "consumer",
    [
        {"kind": "bws-secret"},
        {"kind": "bws-secret", "uuid": ""},
        {"kind": "bws-secret", "uuid": " u"},
        {"kind": "bws-secret", "uuid": 7},
        {
            "kind": "coolify-env",
            "instance": "p",
            "resource_type": "a",
            "uuid": "u",
            "key": "K",
            "redeploy": "yes",
        },
        {"kind": "mystery", "uuid": "u"},
        {"uuid": "u"},
    ],
)
def test_render_refuses_an_incomplete_or_unknown_consumer(consumer: dict[str, Any]) -> None:
    with pytest.raises(DestinationError):
        render(consumer)


def test_a_credential_renders_its_provider_and_consumers_sorted() -> None:
    example = _fixture()["credential_example"]
    assert credential_destinations(example["credential"]) == example["destinations"]


def test_a_credential_without_a_provider_or_with_a_repeated_consumer_is_refused() -> None:
    consumer = {"kind": "bws-secret", "uuid": "u"}
    with pytest.raises(DestinationError):
        credential_destinations({"consumer": [consumer]})
    with pytest.raises(DestinationError):
        credential_destinations(
            {"provider": "p", "consumer": [consumer, {**consumer, "note": "same place"}]}
        )


def test_hosted_kinds_are_the_coolify_ones() -> None:
    assert is_hosted('coolify-env-hash instance="p" resource_type="a" uuid="u" key="K"')
    assert not is_hosted('bws-secret uuid="u"')


def test_a_field_out_of_order_names_the_field_expected() -> None:
    with pytest.raises(DestinationError, match="expected field 'service'"):
        parse('keychain account="a" service="s"')


def test_a_credential_whose_consumers_are_not_a_list_is_refused() -> None:
    with pytest.raises(DestinationError):
        credential_destinations({"provider": "p", "consumer": {"kind": "bws-secret", "uuid": "u"}})


def test_a_coolify_env_without_redeploy_reads_as_not_restarting() -> None:
    """infraops reads a missing `redeploy` as false, so the registry entry is valid without it."""
    base = {"kind": "coolify-env", "instance": "p", "resource_type": "a", "uuid": "u", "key": "K"}
    assert render(base) == render({**base, "redeploy": False})


def test_a_consumer_that_is_not_a_table_is_refused() -> None:
    with pytest.raises(DestinationError):
        credential_destinations({"provider": "p", "consumer": ["bws-secret"]})
