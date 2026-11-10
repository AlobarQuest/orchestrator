"""A standing rotation package generated from a registry row (ADR-0055 amendment 15, increment 4).

The fixture is held byte-identical with intent-packages' copy, whose `create-rotation` test
validates it: a change to what is rendered must still be a package intent-packages accepts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from rotation_proposer.generate import (
    CREATED_BY,
    GenerateError,
    StatedPlanError,
    acceptance,
    authority,
    plan_for,
    reach,
    reissue_body,
    render,
)
from rotation_worker.adapters import ADAPTERS
from rotation_worker.plans import candidates, select_plan
from rotation_worker.registry import RegistryEntry
from rotation_worker.types import CLASS_TYPES, TYPES

FIXTURE = Path(__file__).parents[1] / "fixtures" / "rotation_package_generated.json"
KEEPER_UUID = "11111111-1111-4111-8111-111111111111"


def _entry(credential_class: str, **fields: Any) -> RegistryEntry:
    keeper: dict[str, Any] = (
        {"keeper_service": "Claude", "keeper_account": "BWS_ACCESS_TOKEN_X"}
        if TYPES[CLASS_TYPES[credential_class]].store_kind == "keychain"
        else {"bws_uuid": KEEPER_UUID}
    )
    # A ping URL's row names the check its URL is read from (ADR-0055 amendment 23).
    check = (
        {"healthchecks_check": "sds-example"} if credential_class == "healthchecks-ping-url" else {}
    )
    credential = {
        "id": "example",
        "class": credential_class,
        "provider": "p",
        **keeper,
        **check,
        **fields,
    }
    return RegistryEntry("AlobarQuest/example-registry", "c" * 40, credential)


# The row `rotation_package_generated.json` is rendered from.
GENERATED_ENTRY = RegistryEntry(
    "AlobarQuest/example-registry",
    "c" * 40,
    {
        "id": "example-pat",
        "class": "github-pat-fine-grained",
        "provider": "github",
        "provider_identity": "AlobarQuest (fine-grained PAT)",
        "bws_uuid": KEEPER_UUID,
        "rotated_by_sds": True,
        "consumer": [
            {
                "kind": "gh-actions-secret",
                "repo": "AlobarQuest/example-app",
                "name": "EXAMPLE_TOKEN",
            }
        ],
    },
)


def test_the_rendered_package_is_the_one_intent_packages_validates() -> None:
    rendered = render(GENERATED_ENTRY, "2026-10-28-requested", "2026-10-28T12:00:00Z")
    text = json.dumps(rendered, indent=2, sort_keys=True) + "\n"
    assert text == FIXTURE.read_text(encoding="utf-8")


def _first_issues(credential_class: str) -> list[bool]:
    """The `first_issue` values a type renders a package for: both for a type with an issue plan
    and a rotation plan, only an issue for a type with no rotation plan, either for the rest."""
    capabilities = TYPES[CLASS_TYPES[credential_class]].capabilities
    return [True] if not capabilities.rotates else [True, False]


@pytest.mark.parametrize(
    ("credential_class", "first_issue"),
    [(c, first) for c in sorted(CLASS_TYPES) for first in _first_issues(c)],
)
def test_every_type_s_criteria_describe_its_derived_plan(
    credential_class: str, first_issue: bool
) -> None:
    # The executor runs the plan an approved package's criteria describe (`select_plan`), so a
    # generated package must describe the plan derived for its row, step for step, and no other
    # candidate the type runs.
    entry = _entry(credential_class)
    _, plan = plan_for(entry, first_issue=first_issue)
    declared = TYPES[CLASS_TYPES[credential_class]]
    plans = candidates(declared.capabilities, entry.destinations, entry.keeper, declared.store_kind)
    rendered = render(
        entry, "2026-11-07-requested", "2026-11-07T12:00:00Z", first_issue=first_issue
    )
    assert select_plan(plans, rendered["acceptance"]) == plan
    assert select_plan(tuple(reversed(plans)), rendered["acceptance"]) == plan


# ---- an issue (ADR-0055 amendment 19) ----------------------------------------------------------

LANE_SERVICE = "lanesvc0000000000000000"
LANE_ROWS: dict[str, dict[str, Any]] = {
    "bws-lane-token": {
        "id": "example-lane-bws-token",
        "class": "bws-lane-token",
        "provider": "bws-lane-token",
        "provider_identity": "Bitwarden Secrets Manager (machine account sds-lane-example)",
        "bws_uuid": KEEPER_UUID,
        "rotated_by_sds": True,
        "consumer": [
            {"kind": "bws-secret", "uuid": KEEPER_UUID},
            {
                "kind": "coolify-env",
                "instance": "prod",
                "resource_type": "service",
                "uuid": LANE_SERVICE,
                "key": "EXAMPLE_BWS_ACCESS_TOKEN",
                "redeploy": False,
            },
        ],
    },
    "healthchecks-ping-url": {
        "id": "example-lane-ping-url",
        "class": "healthchecks-ping-url",
        "provider": "healthchecks-ping-url",
        "provider_identity": "Healthchecks (the example lane's check)",
        "bws_uuid": KEEPER_UUID,
        "rotated_by_sds": True,
        "healthchecks_check": "sds-example-lane",
        "consumer": [{"kind": "bws-secret", "uuid": KEEPER_UUID}],
    },
}


def _issue_fixture(credential_class: str) -> Path:
    return FIXTURE.parent / f"rotation_package_issue_{credential_class}.json"


def _lane_entry(credential_class: str) -> RegistryEntry:
    return RegistryEntry("AlobarQuest/example-registry", "c" * 40, LANE_ROWS[credential_class])


def _dumped(package: dict[str, Any]) -> str:
    return json.dumps(package, indent=2, sort_keys=True) + "\n"


@pytest.mark.parametrize("credential_class", sorted(LANE_ROWS))
def test_a_first_issue_renders_its_fixture(credential_class: str) -> None:
    rendered = render(
        _lane_entry(credential_class),
        "2026-11-07-requested",
        "2026-11-07T12:00:00Z",
        first_issue=True,
    )
    assert _dumped(rendered) == _issue_fixture(credential_class).read_text(encoding="utf-8")


@pytest.mark.parametrize("credential_class", sorted(LANE_ROWS))
def test_an_issue_package_states_no_retire_and_no_revoke(credential_class: str) -> None:
    """Kills: an issue package carrying a retire's criteria, prose or authority."""
    rendered = json.loads(_issue_fixture(credential_class).read_text(encoding="utf-8"))
    evidence = " ".join(c["evidence"] for c in rendered["acceptance"])
    assert "retire_old" not in evidence and "confirm_dead" not in evidence
    assert "credential_revoke" in rendered["authority"]["prohibited"]
    assert "credential_revoke" not in rendered["dependencies"]["required_capabilities"]
    prose = " ".join(
        [
            rendered["outcome"]["what"],
            rendered["outcome"]["success_signal"],
            rendered["risk"]["max_impact"],
            rendered["risk"]["rollback"],
        ]
    )
    for retire_word in ("proved dead", "is refused", "quarantined;", "stays valid"):
        assert retire_word not in prose, retire_word
    destinations = rendered["profile_fields"]["destinations"]
    assert sum(d.startswith("provider ") for d in destinations) == 1


def test_a_lane_token_s_issue_and_reissue_render_different_plans() -> None:
    """A lane token's first issue renders the issue plan, and a reissue the rotation plan, each
    of which `select_plan` picks over the other (amendment 19 items 3 and 10)."""
    entry = _lane_entry("bws-lane-token")
    issue = render(entry, "2026-11-07-requested", "2026-11-07T12:00:00Z", first_issue=True)
    reissue = render(entry, "2026-12-01-requested", "2026-12-01T12:00:00Z", first_issue=False)
    issue_steps = [c["evidence"].split(" step's")[0] for c in issue["acceptance"]]
    reissue_steps = [c["evidence"].split(" step's")[0] for c in reissue["acceptance"]]
    assert issue_steps == [
        "external: the verify_new",
        "external: the store",
        "external: the distribute",
        "external: the verify_consumers",
    ]
    assert reissue_steps == [
        "external: the verify_new",
        "external: the store",
        "external: the distribute",
        "external: the verify_consumers",
        "external: the confirm_dead",
    ]
    assert "proved dead" in reissue["outcome"]["what"]
    assert "proved dead" not in issue["outcome"]["what"]
    # A person revokes a lane token, so neither package lets the executor revoke.
    assert "credential_revoke" in reissue["authority"]["prohibited"]


def test_a_lane_token_whose_history_infraops_doesn_t_serve_generates_nothing() -> None:
    """Kills: a fallback to either plan when infraops can't say whether this is a first issue."""
    with pytest.raises(GenerateError, match="serves no last_rotated"):
        plan_for(_lane_entry("bws-lane-token"))


def test_a_ping_url_infraops_records_issued_generates_nothing() -> None:
    """A ping URL has no rotation plan: it is replaced only by recreating its check."""
    with pytest.raises(GenerateError, match="no rotation plan"):
        plan_for(_lane_entry("healthchecks-ping-url"), first_issue=False)
    _, plan = plan_for(_lane_entry("healthchecks-ping-url"))
    assert plan.issue


def test_a_ping_url_s_package_names_its_check_and_allows_no_create() -> None:
    """The check name is in the criterion a person approves, never in its evidence text, which
    `select_plan` and the verifier match (ADR-0055 amendment 23)."""
    rendered = render(
        _lane_entry("healthchecks-ping-url"),
        "2026-11-07-requested",
        "2026-11-07T12:00:00Z",
        first_issue=True,
    )
    mint = rendered["acceptance"][0]
    assert "'sds-example-lane'" in mint["condition"]
    assert mint["evidence"] == (
        "external: the mint step's rotation-step/1 evidence, filed by the rotation executor."
    )
    assert "credential_create" in rendered["authority"]["prohibited"]
    assert rendered["authority"]["allowed"] == ["secret_read", "secret_write"]


@pytest.mark.parametrize("check", [None, "", " sds-example-lane", 7])
def test_a_ping_url_row_naming_no_check_generates_nothing(check: Any) -> None:
    row = {k: v for k, v in LANE_ROWS["healthchecks-ping-url"].items() if k != "healthchecks_check"}
    if check is not None:
        row["healthchecks_check"] = check
    entry = RegistryEntry("AlobarQuest/example-registry", "c" * 40, row)
    with pytest.raises(GenerateError, match="names its check in healthchecks_check"):
        render(entry, "2026-11-07-requested", "2026-11-07T12:00:00Z", first_issue=True)


def test_a_machine_mint_that_creates_is_still_allowed_to_create() -> None:
    assert "credential_create" in authority("openrouter", [])["allowed"]
    assert "credential_create" in authority("healthchecks-ping-url", [])["prohibited"]


def test_the_issue_signal_never_moves_a_type_with_no_issue_plan() -> None:
    """Kills: `last_rotated` null giving an existing type anything but its rotation plan."""
    for credential_class, adapter in CLASS_TYPES.items():
        if TYPES[adapter].capabilities.issues:
            continue
        entry = _entry(credential_class)
        rendered = {
            first: render(entry, "2026-10-29-requested", "2026-10-29T12:00:00Z", first_issue=first)
            for first in (None, True, False)
        }
        assert rendered[None] == rendered[True] == rendered[False], credential_class


def test_an_issue_allows_no_revoke_even_where_the_provider_revokes() -> None:
    assert "credential_revoke" in authority("openrouter", [])["allowed"]
    assert "credential_revoke" in authority("openrouter", [], issue=True)["prohibited"]


def test_a_step_names_its_unit_only_where_its_kind_repeats() -> None:
    _, plan = plan_for(_entry("openrouter-key"))
    evidence = [criterion["evidence"] for criterion in acceptance(plan)]
    assert "external: the mint step's" in evidence[0]
    assert sum("unit's verify_consumers" in text for text in evidence) == 2
    assert not any("unit's mint" in text for text in evidence)


@pytest.mark.parametrize(
    ("credential_class", "expected_reach", "allowed"),
    [
        ("openrouter-key", ["external_system"], ["credential_create", "credential_revoke"]),
        ("github-pat-fine-grained", ["external_system"], ["credential_revoke"]),
        ("atlassian-api-token", ["external_system"], []),
        ("bws-machine-token", ["external_system", "operator_machine"], []),
        (
            "orchestrator-m2m-bearer",
            ["external_system", "live_estate"],
            ["credential_create", "credential_revoke", "infra_mutation"],
        ),
    ],
)
def test_reach_and_authority_follow_from_the_type(
    credential_class: str, expected_reach: list[str], allowed: list[str]
) -> None:
    adapter = CLASS_TYPES[credential_class]
    assert reach(adapter, []) == expected_reach
    granted = authority(adapter, [])
    assert set(granted["allowed"]) == {"secret_read", "secret_write", *allowed}
    assert set(granted["allowed"]).isdisjoint(granted["prohibited"])
    assert "repository_write" in granted["prohibited"]
    assert granted["budgets"] == {"max_attempts": 3, "max_llm_calls": None}


def test_a_class_the_sds_doesn_t_rotate_generates_nothing() -> None:
    entry = RegistryEntry(
        "o/r", "c" * 40, {"id": "x", "class": "openai-key", "bws_uuid": KEEPER_UUID}
    )
    with pytest.raises(GenerateError, match="no type the SDS rotates"):
        render(entry, "2026-10-28-requested", "2026-10-28T12:00:00Z")


def test_every_type_is_an_adapter_s_and_the_adapter_reads_it_from_there() -> None:
    for credential_class, adapter in CLASS_TYPES.items():
        assert adapter in TYPES, credential_class
    for name, declared in TYPES.items():
        assert ADAPTERS[name].capabilities == declared.capabilities, name
        assert ADAPTERS[name].store_kind == declared.store_kind, name


def test_the_package_carries_the_row_s_destinations_and_the_occurrence() -> None:
    entry = _entry("openrouter-key")
    rendered = render(entry, "2026-10-28-requested", "2026-10-28T12:00:00Z")
    fields = rendered["profile_fields"]
    assert fields["destinations"] == entry.destinations
    assert (fields["occurrence"], fields["credential_id"], fields["standing"]) == (
        "2026-10-28-requested",
        "example",
        True,
    )
    assert (rendered["package_id"], rendered["revision"], rendered["status"]) == (
        "rotation-example",
        1,
        "draft",
    )


def test_a_held_copy_puts_a_type_that_doesn_t_host_into_the_live_estate() -> None:
    """Distribute writes a held copy's Coolify setting and restarts its holder."""
    held = (
        'coolify-env-held instance="prod" resource_type="application" '
        'uuid="eqj5l7k705fhi12x9i74fqf0" key="EXAMPLE_TOKEN"'
    )
    assert reach("github-pat", [held]) == ["external_system", "live_estate"]
    assert "infra_mutation" in authority("github-pat", [held])["allowed"]
    assert "infra_mutation" in authority("github-pat", [])["prohibited"]


BEFORE_OVERLAP = Path(__file__).parents[1] / "fixtures" / "rotation_packages_before_overlap.json"
HOST_OUTAGE = (
    "The hosted service replaces the old value with the new one, and its dependents restart; "
    "they're unavailable until the restart finishes."
)


def test_generated_package_for_postgres_states_the_outage() -> None:
    entry = _entry("postgres-role-password")
    rendered = render(entry, "2026-10-30-requested", "2026-10-30T12:00:00Z")
    _, plan = plan_for(entry)
    conditions = [c["condition"] for c in rendered["acceptance"]]
    assert conditions.count(HOST_OUTAGE) == 1
    assert "The hosted service accepts the new value beside the old one." not in conditions
    assert "any hour of the change window" in rendered["risk"]["max_impact"]
    assert "beside the old" not in rendered["risk"]["max_impact"]
    assert select_plan((plan,), rendered["acceptance"]) is plan
    rollback = rendered["risk"]["rollback"]
    assert "If the host step itself fails, the executor writes the old value back" in rollback
    assert "Once host finishes, recovery is forward" in rollback
    assert "automatic" not in rollback.lower()
    assert "Devon" not in rendered["risk"]["max_impact"]


def test_existing_generated_packages_are_unchanged() -> None:
    """Kills: a no-overlap rule that leaks into a type whose provider keeps the old value alive.
    The fixture was rendered by the generator as it stood before the type existed."""
    expected = json.loads(BEFORE_OVERLAP.read_text(encoding="utf-8"))
    assert expected and "postgres-role-password" not in expected
    for credential_class, package in expected.items():
        entry = _entry(credential_class)
        rendered = render(entry, "2026-10-29-requested", "2026-10-29T12:00:00Z")
        assert rendered == package, credential_class


def test_every_generated_fixture_renders_byte_identical() -> None:
    """Every generated package fixture, re-rendered at the base's inputs, is the same bytes:
    an issue changes no package it doesn't issue."""
    before = BEFORE_OVERLAP.read_text(encoding="utf-8")
    expected = json.loads(before)
    rendered = {
        credential_class: render(
            _entry(credential_class), "2026-10-29-requested", "2026-10-29T12:00:00Z"
        )
        for credential_class in expected
    }
    assert json.dumps(rendered, indent=2, sort_keys=True) + "\n" == before


# ---- a revision re-renders a generated package only when its plan changes (amendment 20) ------


def _generated_fixtures() -> list[tuple[str, RegistryEntry, dict[str, Any], list[bool | None]]]:
    """Every generated package fixture, with its row and the signals under which the plan its row
    derives is the plan it states."""
    before = json.loads(BEFORE_OVERLAP.read_text(encoding="utf-8"))
    every: list[bool | None] = [None, True, False]
    return [
        ("generated", GENERATED_ENTRY, json.loads(FIXTURE.read_text(encoding="utf-8")), every),
        *((c, _entry(c), package, every) for c, package in before.items()),
        (
            "issue bws-lane-token",
            _lane_entry("bws-lane-token"),
            json.loads(_issue_fixture("bws-lane-token").read_text(encoding="utf-8")),
            [True],
        ),
        (
            "issue healthchecks-ping-url",
            _lane_entry("healthchecks-ping-url"),
            json.loads(_issue_fixture("healthchecks-ping-url").read_text(encoding="utf-8")),
            [None, True],
        ),
    ]


def test_a_lane_token_s_first_reissue_re_renders_its_issue_package_onto_the_full_plan() -> None:
    """HQ D-32 (A): once infraops dates the issued token, the revision states the full plan, which
    `select_plan` picks over the issue plan; nothing is revoked by machine, and verify_consumers
    still precedes confirm_dead (Infra Brain rule 1032)."""
    entry = _lane_entry("bws-lane-token")
    issued = json.loads(_issue_fixture("bws-lane-token").read_text(encoding="utf-8"))
    body = reissue_body(entry, issued, "2026-12-01-requested", first_issue=False)
    assert body is not None
    declared = TYPES["bws-lane-token"]
    plans = candidates(declared.capabilities, entry.destinations, entry.keeper, declared.store_kind)
    chosen = select_plan(plans, body["acceptance"])
    assert not chosen.issue and select_plan(plans, issued["acceptance"]).issue
    assert body == render(entry, "2026-12-01-requested", issued["created_at"], first_issue=False)
    steps = [c["evidence"].split(" step's")[0].rsplit(" ", 1)[-1] for c in body["acceptance"]]
    assert steps.index("verify_consumers") < steps.index("confirm_dead")
    assert "credential_revoke" in body["authority"]["prohibited"]
    assert "proved dead" in body["outcome"]["what"]
    assert body["created_at"] == issued["created_at"]


@pytest.mark.parametrize(
    ("name", "entry", "package", "signals"),
    _generated_fixtures(),
    ids=[fixture[0] for fixture in _generated_fixtures()],
)
def test_a_generated_package_whose_plan_is_unchanged_keeps_its_wording(
    name: str, entry: RegistryEntry, package: dict[str, Any], signals: list[bool | None]
) -> None:
    """Kills: re-rendering a package whose plan didn't change, which would rewrite approved
    wording a revision only carries an occurrence through."""
    assert package["created_by"] == CREATED_BY
    for first_issue in signals:
        assert reissue_body(entry, package, "2027-01-01-age", first_issue=first_issue) is None


def test_an_unknown_signal_refuses_rather_than_choosing_a_plan() -> None:
    """Kills: falling back to either plan when infraops can't say whether the token was issued."""
    issued = json.loads(_issue_fixture("bws-lane-token").read_text(encoding="utf-8"))
    with pytest.raises(GenerateError, match="serves no last_rotated"):
        reissue_body(
            _lane_entry("bws-lane-token"), issued, "2026-12-01-requested", first_issue=None
        )


@pytest.mark.parametrize("author", ["factory-create", None])
def test_a_hand_authored_package_is_never_re_rendered(author: str | None) -> None:
    """Kills: re-rendering by plan alone. A hand-authored package keeps its wording even when it
    states another plan, or a pre-derivation plan no candidate is (amendment 15 decision 5)."""
    issued = json.loads(_issue_fixture("bws-lane-token").read_text(encoding="utf-8"))
    for package in (issued, {**issued, "acceptance": [{"id": "AC-001", "evidence": "by hand"}]}):
        authored = {**package, "created_by": author}
        if author is None:
            del authored["created_by"]
        entry = _lane_entry("bws-lane-token")
        assert reissue_body(entry, authored, "2026-12-01-requested", first_issue=False) is None
        assert reissue_body(entry, authored, "2026-12-01-requested", first_issue=None) is None


def test_a_generated_package_stating_no_derived_plan_is_refused() -> None:
    """Kills: re-rendering a package whose stated plan is unknown, repairing it silently."""
    issued = json.loads(_issue_fixture("bws-lane-token").read_text(encoding="utf-8"))
    broken = {**issued, "acceptance": issued["acceptance"][:2]}
    with pytest.raises(StatedPlanError, match="describe no plan its row derives"):
        reissue_body(
            _lane_entry("bws-lane-token"), broken, "2026-12-01-requested", first_issue=True
        )
