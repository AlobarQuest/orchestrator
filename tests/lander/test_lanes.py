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
CEILING = "landing_version_ceiling_exceeded"
NOT_GRANTED = "landing_repository_not_granted"
REVERTED = "landing_version_reverted"
REVERTING = "landing_repository_reverting"


class _Client:
    def __init__(
        self,
        answer: dict[str, Any],
        update_error: Exception | None = None,
        close_error: Exception | None = None,
        replayed: bool = False,
        land_answer: dict[str, Any] | None = None,
        land_error: Exception | None = None,
    ) -> None:
        self.replayed = replayed
        self.land_answer = land_answer or {"status": "merged"}
        self.land_error = land_error
        self.landed: list[tuple[str, str]] = []
        self.answer = answer
        self.update_error = update_error
        self.close_error = close_error
        self.keys: list[str] = []
        # The list a dry-run or a no-hold test asserts is EMPTY.
        self.closed: list[tuple[str, int, str]] = []

    def admission(self, repository: str, pr_number: int) -> dict[str, Any]:
        return self.answer

    def land(
        self, repository: str, pr_number: int, *, head_sha: str, idempotency_key: str
    ) -> dict[str, Any]:
        self.keys.append(idempotency_key)
        self.landed.append((head_sha, idempotency_key))
        if self.land_error is not None:
            raise self.land_error
        return self.land_answer

    def update_branch(
        self, repository: str, pr_number: int, *, head_sha: str, idempotency_key: str
    ) -> dict[str, Any]:
        self.keys.append(idempotency_key)
        if self.update_error is not None:
            raise self.update_error
        return {}

    def close_held(
        self, repository: str, pr_number: int, *, head_sha: str, idempotency_key: str
    ) -> dict[str, Any]:
        self.closed.append((repository, pr_number, idempotency_key))
        self.keys.append(head_sha)
        if self.close_error is not None:
            raise self.close_error
        return {"commented": True, "replayed": self.replayed, "ceilings": []}


def _status(module: Any, refusals: list[str], **extra: Any) -> str:
    client = _Client({"satisfied": False, "refusals": refusals, **extra})
    [outcome] = module._pass(SUBJECT, client, True)
    return outcome.status


# ---- The descriptors, as literals ------------------------------------------------------------


def test_the_estate_lane_is_exactly_this() -> None:
    assert estate.LANE == Lane(
        name="estate",
        deliberate=frozenset({PACE, WINDOW}),
        exception=frozenset({UNPARSEABLE, CEILING, NOT_GRANTED, REVERTED}),
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
        exception=frozenset({ECOSYSTEM, CEILING, NOT_GRANTED, REVERTED}),
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
    # And not only through `consider`: the classifier itself applies the lane's flag, so no other
    # caller can hand it the pin and switch the suppression on for the inert lane.
    assert (
        core.held_status(inert.LANE, [BEHIND, ROLLOUT, ECOSYSTEM], rollout_base_matches_pin=True)
        == "held"
    )
    # The control for the estate half: the same answer with the base NOT carrying the pin.
    assert _status(estate, [BEHIND, ROLLOUT, UNPARSEABLE], rollout_base_matches_pin=False) == "held"


def test_the_DELIBERATE_status_is_unreachable_for_the_inert_lane() -> None:
    """With an empty deliberate set the classifier's last branch is reached only when the sibling
    key emptied the unexplained set, which returns `waiting` first. Walked over the whole relevant
    vocabulary, both flags, rather than asserted from reading."""
    from itertools import combinations

    vocabulary = (
        BEHIND,
        ROLLOUT,
        PACE,
        WINDOW,
        UNPARSEABLE,
        ECOSYSTEM,
        REVERTING,
        "landing_checks_not_clean",
    )
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


# ---- A held bump is closed (bridge layer Part 4, Ruling 7) ------------------------------------

LANDERS = [estate, inert]
CLOSE_KEY = "held-pr-close:alobarquest/x#7"


@pytest.mark.parametrize("module", LANDERS)
@pytest.mark.parametrize(
    "refusals", [[CEILING], [CEILING, BEHIND, "landing_checks_not_clean"]], ids=["alone", "beside"]
)
def test_an_acting_pass_closes_a_held_bump_even_beside_other_refusals(
    module: Any, refusals: list[str]
) -> None:
    client = _Client({"satisfied": False, "refusals": refusals, "head_sha": HEAD})

    [outcome] = module._pass(SUBJECT, client, True)

    assert outcome.status == "closed"
    assert client.closed == [("alobarquest/x", 7, CLOSE_KEY)]
    # The head the answer named, and no landing key: nothing was asked to land.
    assert client.keys == [HEAD]


@pytest.mark.parametrize("module", LANDERS)
def test_a_dry_run_says_it_would_close_and_sends_nothing(module: Any) -> None:
    client = _Client({"satisfied": False, "refusals": [CEILING], "head_sha": HEAD})

    [outcome] = module._pass(SUBJECT, client, False)

    assert outcome.status == "would-close"
    assert client.closed == []


@pytest.mark.parametrize("module", LANDERS)
@pytest.mark.parametrize("submit", [True, False], ids=["acting", "dry-run"])
def test_a_held_bump_in_an_ungranted_repository_is_an_exception_and_never_closed(
    module: Any, submit: bool
) -> None:
    """Landing Part 2. The orchestrator refuses to close a pull request in a repository the member
    record does not grant, so asking would only turn a durable exception into an hourly finding --
    and a dry run would promise a close that cannot happen."""
    client = _Client({"satisfied": False, "refusals": [CEILING, NOT_GRANTED], "head_sha": HEAD})

    [outcome] = module._pass(SUBJECT, client, submit)

    assert outcome.status == "exception"
    assert client.closed == []


@pytest.mark.parametrize("module", LANDERS)
def test_no_ceiling_refusal_closes_nothing(module: Any) -> None:
    client = _Client(
        {"satisfied": False, "refusals": ["landing_dependency_list_unreadable"], "head_sha": HEAD}
    )

    [outcome] = module._pass(SUBJECT, client, True)

    assert outcome.status == "held"
    assert client.closed == []


@pytest.mark.parametrize("module", LANDERS)
def test_a_settled_held_bump_is_not_closed_again(module: Any) -> None:
    """A closed pull request is no longer open, so the next pass reads it settled."""
    client = _Client({"satisfied": False, "refusals": [CEILING, "landing_pull_request_not_open"]})

    [outcome] = module._pass(SUBJECT, client, True)

    assert outcome.status == "settled"
    assert client.closed == []


@pytest.mark.parametrize("module", LANDERS)
@pytest.mark.parametrize(
    ("error", "status"),
    [
        (core.LandingRefused("refused", "held_close_not_held"), "held"),
        (core.OrchestratorError("unreachable"), "error"),
    ],
)
def test_a_refused_or_failed_close_is_a_finding(module: Any, error: Exception, status: str) -> None:
    client = _Client(
        {"satisfied": False, "refusals": [CEILING], "head_sha": HEAD}, close_error=error
    )

    [outcome] = module._pass(SUBJECT, client, True)

    assert outcome.status == status
    assert status not in core.NOT_A_FINDING


def test_closing_is_not_a_finding_and_names_no_held_status() -> None:
    assert {"closed", "would-close", "reopened"} <= core.NOT_A_FINDING
    assert {"closed", "would-close", "reopened"} <= set(core.REPORTED)


@pytest.mark.parametrize("module", LANDERS)
def test_a_close_answered_from_the_record_says_reopened_not_closed(module: Any) -> None:
    """A person reopened a pull request an earlier pass closed; this pass closed nothing."""
    client = _Client({"satisfied": False, "refusals": [CEILING], "head_sha": HEAD}, replayed=True)

    [outcome] = module._pass(SUBJECT, client, True)

    assert outcome.status == "reopened"


@pytest.mark.parametrize("module", LANDERS)
def test_a_held_answer_naming_no_head_is_unreadable_and_sends_nothing(module: Any) -> None:
    client = _Client({"satisfied": False, "refusals": [CEILING]})

    [outcome] = module._pass(SUBJECT, client, True)

    assert outcome.status == "unreadable"
    assert client.closed == []


# ---- Landing Part 3a: a bump waiting on a revert in its repository -------------------------------


@pytest.mark.parametrize("module", [estate, inert])
@pytest.mark.parametrize(
    ("refusals", "expected"),
    [
        pytest.param([REVERTING], "waiting", id="alone"),
        # Behind beside it is the same wait: the bump is freshened after the revert lands.
        pytest.param([REVERTING, BEHIND], "waiting", id="beside-behind"),
        # A red build is still a finding, whatever it waits on.
        pytest.param([REVERTING, "landing_checks_not_clean"], "held", id="beside-a-red-build"),
        pytest.param([REVERTING, BEHIND, "landing_checks_not_clean"], "held", id="red-and-behind"),
        # The durable fact outranks the wait.
        pytest.param([REVERTING, REVERTED], "exception", id="beside-an-exception"),
    ],
)
def test_a_bump_waiting_on_a_revert_is_waiting_in_both_lanes_never_a_finding(
    module: Any, refusals: list[str], expected: str
) -> None:
    assert _status(module, refusals) == expected


def test_the_wait_on_a_revert_is_not_a_deliberate_refusal_in_either_lane() -> None:
    """It clears when the revert lands, not on a clock, so it is in neither lane's `deliberate`
    set; the inert lane's stays empty."""
    assert REVERTING not in estate.LANE.deliberate | inert.LANE.deliberate
    assert REVERTING not in estate.LANE.exception | inert.LANE.exception
    assert core.REVERTING == REVERTING


# An unknown landing outcome (Landing Part 5): the answer says so, and the land call settles it.
UNKNOWN = "landing_outcome_unknown"
NOT_OPEN = "landing_pull_request_not_open"


def _unknown(*refusals: str, head: str | None = HEAD) -> dict[str, Any]:
    return {"satisfied": False, "refusals": [UNKNOWN, *refusals], "head_sha": head}


@pytest.mark.parametrize("module", LANDERS)
@pytest.mark.parametrize(
    ("status", "expected"),
    [("already_merged", "settled"), ("merged", "landed"), ("refused", "unreadable")],
)
def test_outcome_unknown_replays_the_landing(module: Any, status: str, expected: str) -> None:
    """Beside `not open` too: a landed pull request answers both, and that one is SETTLED."""
    client = _Client(_unknown(NOT_OPEN), land_answer={"status": status})

    outcome = core.consider(module.LANE, client, *SUBJECT[0], True)

    assert outcome.status == expected
    assert client.landed == [(HEAD, core.landing_key(module.LANE, *SUBJECT[0], HEAD))]


@pytest.mark.parametrize("module", LANDERS)
def test_outcome_unknown_with_no_submit_acts_on_nothing(module: Any) -> None:
    client = _Client(_unknown(NOT_OPEN))

    outcome = core.consider(module.LANE, client, *SUBJECT[0], False)

    assert outcome.status == "unreadable"
    assert UNKNOWN in outcome.detail
    assert client.landed == []


@pytest.mark.parametrize("module", LANDERS)
def test_outcome_unknown_naming_no_head_is_unreadable(module: Any) -> None:
    client = _Client(_unknown(head=None))

    outcome = core.consider(module.LANE, client, *SUBJECT[0], True)

    assert outcome.status == "unreadable"
    assert client.landed == []


@pytest.mark.parametrize("module", LANDERS)
def test_a_still_unknown_replay_reports_unreadable(module: Any) -> None:
    client = _Client(
        _unknown(), land_answer={"status": "refused", "reason_code": "merge_refused_by_remote:x"}
    )

    outcome = core.consider(module.LANE, client, *SUBJECT[0], True)

    assert outcome.status == "unreadable"
    assert "merge_refused_by_remote:x" in outcome.detail


def test_a_refused_replay_reads_as_its_other_refusals() -> None:
    """Outside the window the row stays and a replay is refused, every pass: that is the window's
    deliberate refusal, not a finding, and a closed pull request stays settled."""
    refused = core.LandingRefused("may not be landed", "estate_merge_not_admissible")

    client = _Client(_unknown(WINDOW), land_error=refused)
    assert core.consider(estate.LANE, client, *SUBJECT[0], True).status == "deliberate"

    client = _Client(_unknown(NOT_OPEN), land_error=refused)
    assert core.consider(estate.LANE, client, *SUBJECT[0], True).status == "settled"

    client = _Client(_unknown(), land_error=refused)
    held = core.consider(estate.LANE, client, *SUBJECT[0], True)
    assert (held.status, held.detail) == ("held", str(refused))


def test_a_replay_the_orchestrator_could_not_answer_is_an_error() -> None:
    client = _Client(_unknown(), land_error=core.OrchestratorError("no answer"))

    assert core.consider(estate.LANE, client, *SUBJECT[0], True).status == "error"
