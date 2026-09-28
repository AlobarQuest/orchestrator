"""The two landing callers share one body (`lander.core`) and differ ONLY in their `Lane`.

Tier 3 item 27 merged the body; ADR-0038 keeps the programs two. Every difference that survived
the merge is a field of `Lane`, and each is pinned here twice: once as a literal, so a field
collapsed into the other lane's value reddens by name, and once as BEHAVIOUR driven through each
lane's own module entry points, so a lane module that stopped passing its own descriptor -- or a
body that stopped reading a field -- reddens too. The behavioural pins are PAIRS: one answer each
lane must classify differently, because a single-lane assertion cannot tell a collapsed field from
a correct one when the collapsed value happens to agree on that input.
"""

from __future__ import annotations

from typing import Any

import pytest

import estate_lander.cli as estate
import estate_lander.orchestrator_client as estate_client
import inert_lander.cli as inert
import inert_lander.orchestrator_client as inert_client
from lander import core
from lander.core import Lane

HEAD = "9f7f6ea6b3adde1cfc712f737647bc308cadb59a"
SUBJECT = [("alobarquest/x", 7)]

BEHIND = "landing_head_not_current_with_base"
ROLLOUT = "landing_rollout_moved"
PACE = "landing_pace_exhausted"
WINDOW = "landing_outside_change_window"
UNPARSEABLE = "landing_update_type_unparseable"
ECOSYSTEM = "landing_ecosystem_excluded"


class _Client:
    def __init__(self, answer: dict[str, Any], update_error: Exception | None = None) -> None:
        self.answer = answer
        self.update_error = update_error
        self.keys: list[str] = []

    def admission(self, repository: str, pr_number: int) -> dict[str, Any]:
        return self.answer

    def land(
        self, repository: str, pr_number: int, *, head_sha: str, idempotency_key: str
    ) -> dict[str, Any]:
        self.keys.append(idempotency_key)
        return {"status": "merged"}

    def update_branch(
        self, repository: str, pr_number: int, *, head_sha: str, idempotency_key: str
    ) -> dict[str, Any]:
        self.keys.append(idempotency_key)
        if self.update_error is not None:
            raise self.update_error
        return {}


def _status(module: Any, refusals: list[str], **extra: Any) -> str:
    client = _Client({"satisfied": False, "refusals": refusals, **extra})
    [outcome] = module._pass(SUBJECT, client, True)
    return outcome.status


# ---- The descriptors, as literals ------------------------------------------------------------


def test_the_estate_lane_is_exactly_this() -> None:
    assert estate.LANE == Lane(
        name="estate",
        deliberate=frozenset({PACE, WINDOW}),
        exception=frozenset({UNPARSEABLE}),
        update_self_clearing=frozenset(
            {
                "estate_branch_update_head_moved",
                "estate_branch_update_not_qualified",
                "estate_branch_update_sibling_holding",
            }
        ),
        reads_rollout_pin=True,
    )


def test_the_inert_lane_is_exactly_this() -> None:
    """The EMPTY deliberate set and the False rollout flag are the statements the inert lander's
    docstring makes -- no clock, no rollout pin -- and the two most tempting to "tidy" into the
    sibling's values."""
    assert inert.LANE == Lane(
        name="inert",
        deliberate=frozenset(),
        exception=frozenset({ECOSYSTEM}),
        update_self_clearing=frozenset(
            {
                "inert_branch_update_head_moved",
                "inert_branch_update_not_qualified",
                "inert_branch_update_sibling_holding",
            }
        ),
        reads_rollout_pin=False,
    )


# ---- The descriptors, as behaviour: pairs each lane must answer differently -------------------


@pytest.mark.parametrize("refusal", [PACE, WINDOW])
def test_a_DELIBERATE_refusal_is_quiet_for_the_estate_lane_and_a_finding_for_the_inert_lane(
    refusal: str,
) -> None:
    assert _status(estate, [refusal]) == "deliberate"
    assert _status(inert, [refusal]) == "held"


def test_each_lane_suppresses_its_OWN_exception_and_not_the_others() -> None:
    assert _status(estate, [UNPARSEABLE]) == "exception"
    assert _status(inert, [UNPARSEABLE]) == "held"
    assert _status(inert, [ECOSYSTEM]) == "exception"
    assert _status(estate, [ECOSYSTEM]) == "held"


def test_only_the_estate_lane_reads_the_ROLLOUT_PIN() -> None:
    """ADR-0024 for one lane, and deliberately not for the other. The same answer shape -- behind,
    a rollout refusal, an exception, and the base carrying the pinned bytes -- is an exception to
    the lane that evaluates a rollout pin and a finding to the lane that does not. Were the inert
    lane to read the key, a future answer carrying it would silently suppress a refusal nobody
    decided to suppress there."""
    assert (
        _status(estate, [BEHIND, ROLLOUT, UNPARSEABLE], rollout_base_matches_pin=True)
        == "exception"
    )
    assert _status(inert, [BEHIND, ROLLOUT, ECOSYSTEM], rollout_base_matches_pin=True) == "held"
    # The control for the estate half: the same answer with the base NOT carrying the pin.
    assert _status(estate, [BEHIND, ROLLOUT, UNPARSEABLE], rollout_base_matches_pin=False) == "held"


def test_the_DELIBERATE_status_is_unreachable_for_the_inert_lane() -> None:
    """With an empty deliberate set the classifier's last branch is reached only when the sibling
    key emptied the unexplained set, which returns `waiting` first. Walked over the whole relevant
    vocabulary, both flags, rather than asserted from reading."""
    from itertools import combinations

    vocabulary = (BEHIND, ROLLOUT, PACE, WINDOW, UNPARSEABLE, ECOSYSTEM, "landing_checks_not_clean")
    for size in range(len(vocabulary) + 1):
        for subset in combinations(vocabulary, size):
            for withheld in (True, False):
                for pin in (True, False):
                    status = core.held_status(
                        inert.LANE,
                        list(subset),
                        rollout_base_matches_pin=pin,
                        withheld_for_sibling=withheld,
                    )
                    assert status != "deliberate", (subset, withheld, pin)


@pytest.mark.parametrize(
    ("code", "estate_status", "inert_status"),
    [
        ("estate_branch_update_head_moved", "deliberate", "held"),
        ("estate_branch_update_sibling_holding", "deliberate", "held"),
        ("inert_branch_update_head_moved", "held", "deliberate"),
        ("inert_branch_update_not_qualified", "held", "deliberate"),
    ],
)
def test_each_lane_excuses_only_its_OWN_self_clearing_update_refusals(
    code: str, estate_status: str, inert_status: str
) -> None:
    for module, expected in ((estate, estate_status), (inert, inert_status)):
        client = _Client(
            {"satisfied": False, "branch_update_qualifies": True, "head_sha": HEAD},
            update_error=core.LandingRefused("refused", code),
        )
        [outcome] = module._branch_updates(SUBJECT, client, True)
        assert outcome.status == expected, module.__name__


def test_each_lane_keys_its_acts_with_its_OWN_prefix() -> None:
    for module, name in ((estate, "estate"), (inert, "inert")):
        client = _Client(
            {
                "satisfied": True,
                "branch_update_qualifies": True,
                "head_sha": HEAD,
                "refusals": [],
            }
        )
        module._pass(SUBJECT, client, True)
        module._branch_updates(SUBJECT, client, True)
        assert client.keys == [
            f"{name}-landing:alobarquest/x:7:{HEAD[:12]}",
            f"{name}-branch-update:alobarquest/x:7:{HEAD[:12]}",
        ]


# ---- The shared refusal classes ---------------------------------------------------------------


@pytest.mark.parametrize("client_module", [estate_client, inert_client])
def test_each_client_raises_the_classes_the_shared_body_CATCHES(client_module: Any) -> None:
    """Identity, not name. A lane-local class spelled the same would sail past every `except` in
    the body: a refusal would escape `consider` as a traceback instead of a line."""
    assert client_module.LandingRefused is core.LandingRefused
    assert client_module.OrchestratorError is core.OrchestratorError
    assert issubclass(client_module.ForbiddenEndpointError, core.OrchestratorError)


# ---- The report's lane-owned lines ------------------------------------------------------------


def test_each_lane_prints_its_OWN_preamble_and_deferral_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The wording differs because what was left alone differs: a record of another change class
    belongs to another LANE, while a pull request by an undeclared author belongs to a PERSON. Only
    the inert lane prints a policy version, because only its permission comes from one. Pinned as
    whole lines, since a substring check on either would pass the other lane's wording."""
    estate.report([], {"factory-delivery": 2})
    assert capsys.readouterr().out.splitlines()[:1] == [
        "2 factory-delivery record(s) not considered; they belong to another lane"
    ]

    inert.report([], {"not-a-declared-author": 1}, 9)
    assert capsys.readouterr().out.splitlines()[:2] == [
        "landing policy version 9",
        "1 open pull request(s) not-a-declared-author; they are not this lane's business",
    ]
