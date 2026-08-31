"""What change-manager declares, read at the source (ADR-0038).

**THE DOCUMENT BELOW IS THE ONE PRODUCTION SERVES**, fetched from `GET /api/landing-policy` on
2026-08-31 with the READ-scoped bearer and pasted here in full rather than invented. A parser
tested against a shape somebody thought of passes while being wrong for the only document it will
ever read, which is a mistake this estate has already paid for.

**AND THE DIFFERENTIAL AT THE END IS WHY THE MOVE IS SHIPPABLE MID-FLIGHT.** Nothing removes the
auto-merge workflow during this increment, so this producer must go on giving the same answer while
both the workflow and the declaration are live. All six repositories carried gate blob `3457db3c`
when measured, and that revision's transcription and this declaration are asserted here to
classify every input identically -- measured rather than argued, with a control showing the
comparison can fail.
"""

from __future__ import annotations

import httpx
import pytest

from bump_proposer.landing_policy import (
    InertLanding,
    LandingPolicyError,
    parse,
    read_inert_landing,
)
from landing_ledger.rules import REGISTRY

# `GET /api/landing-policy`, 2026-08-31, version 6. The rationale is elided to its first sentence
# -- it is prose this program never reads, and pasting 3KB of it here would bury the fields that
# are load-bearing.
LIVE_BLOCK = {
    "repositories": [
        "alobarquest/factory-runner",
        "alobarquest/infraops-mcp-server",
        "alobarquest/intent-packages",
        "alobarquest/orchestrator",
        "alobarquest/project-standards",
        "alobarquest/security-standards",
    ],
    "permitted_authors": ["dependabot[bot]"],
    "excluded_ecosystems": ["docker"],
    "require_head_current_with_base": True,
    "rationale": "WHERE LANDING ON THE DEFAULT BRANCH CHANGES NOTHING ALREADY SERVING. [...]",
}
LIVE = {
    "version": 6,
    "decided": "2026-08-31",
    "rationale": "[...]",
    "repositories": ["alobarquest/change-manager", "alobarquest/brain"],
    "change_classes": ["dependency-update"],
    "risks": ["caution"],
    "landing": {},
    "inert_landing": LIVE_BLOCK,
}


def _document(**overrides):
    """The live document with one part replaced, so every case below differs in one thing."""
    block = dict(LIVE_BLOCK)
    document = dict(LIVE)
    for key, value in overrides.items():
        if key == "version":
            document["version"] = value
        elif key == "inert_landing":
            document["inert_landing"] = value
        else:
            block[key] = value
    if "inert_landing" not in overrides:
        document["inert_landing"] = block
    return document


def test_the_document_production_serves_parses_to_what_it_declares() -> None:
    rule = parse(LIVE)

    assert rule.version == 6
    assert rule.permitted_authors == frozenset({"dependabot[bot]"})
    assert rule.excluded_ecosystems == frozenset({"docker"})
    assert len(rule.repositories) == 6


def test_the_declared_population_is_case_folded_against_the_way_github_spells_it() -> None:
    """Kills: dropping `.lower()` in `declares`.

    change-manager serves the population lowercased and a standing package names its target the
    way GitHub does. A comparison of the two as given answers False for EVERY repository in the
    lane -- so the lane goes quiet and every pass reports a clean nothing, which is the direction
    nobody notices.
    """
    rule = parse(LIVE)

    assert rule.declares("AlobarQuest/infraops-mcp-server")
    assert rule.declares("alobarquest/infraops-mcp-server")


def test_a_repository_the_document_governs_on_the_OTHER_terms_is_not_declared_inert() -> None:
    """The live case, and the reason `declares` is not a formality: one document, two
    populations. `change-manager` is in `repositories` at the top level -- landing there
    redeploys production -- and it is deliberately not in the inert block."""
    rule = parse(LIVE)

    assert "alobarquest/change-manager" in LIVE["repositories"]
    assert not rule.declares("AlobarQuest/change-manager")


def test_an_excluded_ecosystem_is_refused_and_an_unexcluded_one_is_permitted() -> None:
    rule = parse(LIVE)

    assert rule.permits("npm_and_yarn")
    assert rule.permits("github_actions")
    assert not rule.permits("docker")


def test_an_absent_ecosystem_is_fail_closed_rather_than_faithful() -> None:
    """The landing party always has one -- it is the second segment of the update bot's branch --
    so `None` never means "the party that lands saw nothing". It means this program could not
    read what that party reads, and permitting on that leaves a bump to a lane whose exclusion
    nobody can re-check."""
    assert not parse(LIVE).permits(None)


def test_the_author_the_reader_can_see_is_the_one_the_document_permits() -> None:
    rule = parse(LIVE)

    assert rule.covers_author("dependabot[bot]")
    assert not rule.covers_author("app/dependabot")


def test_a_version_declaring_no_inert_population_is_a_refusal_and_names_the_version() -> None:
    """ADR-0038: an absent block is a version that did not decide the question, not one that
    opened the lane to everybody. Versions 1 to 5 are exactly this, so it is the shape a
    rollback produces rather than an invented one."""
    with pytest.raises(LandingPolicyError) as raised:
        parse(_document(inert_landing=None))

    assert "version 6" in str(raised.value)
    assert "refusal rather than a waiver" in str(raised.value)


def test_an_empty_declared_population_is_not_a_declaration_that_everything_is_inert() -> None:
    with pytest.raises(LandingPolicyError):
        parse(_document(repositories=[]))


@pytest.mark.parametrize(
    "key",
    [
        "repositories",
        "permitted_authors",
        "excluded_ecosystems",
        "require_head_current_with_base",
    ],
)
def test_a_block_missing_any_declared_condition_is_refused(key: str) -> None:
    """INCLUDING THE ONE THIS PRODUCER DOES NOT USE, which is the case worth naming.

    `require_head_current_with_base` is a condition on the ACT and this program does not act, so
    nothing here reads its value -- and a landing party that dropped it would be dropping a
    condition the document states. Requiring the key without keeping the value is what stops a
    document silently losing a term while this reader answers as though nothing changed.
    """
    block = {k: v for k, v in LIVE_BLOCK.items() if k != key}

    with pytest.raises(LandingPolicyError) as raised:
        parse(_document(inert_landing=block))

    assert key in str(raised.value)


@pytest.mark.parametrize(
    "document",
    [
        "not an object",
        {"version": 6},
        {"version": "6", "inert_landing": LIVE_BLOCK},
        {"version": 6, "inert_landing": "not an object"},
    ],
    ids=["not-an-object", "no-block", "version-not-an-integer", "block-not-an-object"],
)
def test_a_document_that_is_not_this_document_is_refused(document) -> None:
    with pytest.raises(LandingPolicyError):
        parse(document)


@pytest.mark.parametrize(
    "value", ["docker", [1, 2], {"docker": True}], ids=["string", "not-strings", "object"]
)
def test_a_name_list_that_is_not_a_list_of_names_is_refused(value) -> None:
    with pytest.raises(LandingPolicyError):
        parse(_document(excluded_ecosystems=value))


# ---------------------------------------------------------------------------------------------
# The transport. One request, one path, one credential -- and every failure a refusal rather
# than a traceback, because a pass that crashes reports nothing a scheduled run can read.
# ---------------------------------------------------------------------------------------------


def _transport(handler):
    return httpx.MockTransport(handler)


def test_it_asks_for_the_one_path_and_carries_the_bearer() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.headers.get("authorization", "")))
        return httpx.Response(200, json=LIVE)

    rule = read_inert_landing("token", base_url="https://cm.example", transport=_transport(handler))

    assert rule.version == 6
    assert seen == [("/api/landing-policy", "Bearer token")]


def test_an_empty_credential_is_refused_before_anything_is_sent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - never called
        raise AssertionError("a request left the process without a credential")

    with pytest.raises(LandingPolicyError):
        read_inert_landing("", base_url="https://cm.example", transport=_transport(handler))


@pytest.mark.parametrize("status", [401, 403, 404, 500], ids=str)
def test_a_refused_or_broken_response_is_a_refusal_naming_the_status(status: int) -> None:
    rule = read_inert_landing
    with pytest.raises(LandingPolicyError) as raised:
        rule(
            "token",
            base_url="https://cm.example",
            transport=_transport(lambda request: httpx.Response(status, json={})),
        )

    assert str(status) in str(raised.value)
    assert ("not scoped" in str(raised.value)) == (status == 403)


def test_a_response_that_is_not_json_is_a_refusal_rather_than_a_traceback() -> None:
    with pytest.raises(LandingPolicyError):
        read_inert_landing(
            "token",
            base_url="https://cm.example",
            transport=_transport(lambda request: httpx.Response(200, content=b"<html>")),
        )


def test_an_unreachable_service_names_the_exception_type_and_nothing_else() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(LandingPolicyError) as raised:
        read_inert_landing("s3cret", base_url="https://cm.example", transport=_transport(handler))

    assert "ConnectError" in str(raised.value)
    assert "s3cret" not in str(raised.value)


@pytest.mark.parametrize(
    "base_url",
    ["https://cm\nexample", "https://cm..example", "https://" + "a" * 300 + ".example"],
    ids=["control-character", "doubled-dot", "over-long-label"],
)
def test_an_unusable_base_url_is_a_refusal_at_either_end(base_url: str) -> None:
    """httpx raises at the CONSTRUCTOR for some malformed URLs and at REQUEST time for others,
    and the split is not guessable: a control character is refused by `urlparse` inside the
    client, while a doubled dot or an over-long DNS label survives until IDNA encoding. A guard
    on one half is not a guard -- an environment-variable typo would crash the pass with a
    traceback instead of reporting that it could not use its inputs."""
    with pytest.raises(LandingPolicyError):
        read_inert_landing("token", base_url=base_url)


# ---------------------------------------------------------------------------------------------
# THE INTERIM. Increment 5 removes the workflow; until then both it and this declaration are
# live, and this producer must not change its answer while they coexist.
# ---------------------------------------------------------------------------------------------

# The gate blob all six repositories carried on 2026-08-31, measured by reading the contents API
# for each of them. `_OUTCOME_NOT_UPDATE_TYPE`: it excludes `docker` and asks nothing else.
INSTALLED_GATE = "3457db3cee85ffa054dee8b434ac25238a81f425"
# The revision before ADR-0034, which refused a major outside `github_actions` on its DECLARATION.
SUPERSEDED_GATE = "a4a4b8da035292fe434badd007607d8a69bc54e2"

MAJOR = "version-update:semver-major"
MINOR = "version-update:semver-minor"

# Every (update type, ecosystem) pair the estate's own open updates have presented, plus the
# unreadable case. Taken from the population measured 2026-08-31 rather than enumerated.
INPUTS = [
    (MAJOR, "npm_and_yarn"),
    (MAJOR, "github_actions"),
    (MINOR, "npm_and_yarn"),
    (None, "uv"),
    (None, "docker"),
    (MAJOR, "docker"),
    (None, None),
]


@pytest.mark.parametrize("update_type,ecosystem", INPUTS, ids=lambda v: str(v))
def test_the_declaration_and_the_installed_gate_agree_on_every_measured_input(
    update_type, ecosystem
) -> None:
    """WHY THIS INCREMENT IS SHIPPABLE BEFORE THE WORKFLOW IS REMOVED.

    Nothing removes the auto-merge workflow here, so between this landing and that removal both
    it and the declaration decide the same estate. If they disagreed, this producer would mint a
    package revision for a bump the still-armed workflow was about to land -- and a package
    revision cannot be unminted.
    """
    assert parse(LIVE).permits(ecosystem) == REGISTRY[INSTALLED_GATE].permits(
        update_type, ecosystem
    )


def test_that_agreement_is_a_measurement_and_not_a_tautology() -> None:
    """THE CONTROL. Two predicates that agreed for any input would prove nothing.

    The revision before ADR-0034 refused an npm major on its declared type; the declaration
    permits it and leaves the outcome to the required checks. So the comparison above can fail,
    and it passes because the installed revision and the declaration say the same thing.
    """
    assert parse(LIVE).permits("npm_and_yarn")
    assert not REGISTRY[SUPERSEDED_GATE].permits(MAJOR, "npm_and_yarn")


def test_the_reader_returns_the_shape_the_pass_expects() -> None:
    assert isinstance(parse(LIVE), InertLanding)
