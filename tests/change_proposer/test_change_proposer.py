"""The change producer: what it proposes, what it refuses, and what it can never reach.

ADR-0019 increment 4. The property that matters most here is a NEGATIVE one — this program cannot
approve the records it creates — and it is asserted three ways: the client refuses the path before
building a request, the import graph proves it holds nothing else, and change-manager's own
`propose` scope refuses it server-side (pinned in that repository, not here).
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from change_proposer.change_manager import (
    ChangeManagerClient,
    ChangeManagerError,
    ForbiddenEndpointError,
    ProposalRefused,
    is_allowed_read,
    is_allowed_write,
)
from change_proposer.cli import (
    BOT_CHANGE_CLASS,
    FACTORY_CHANGE_CLASS,
    FINDING_STATUSES,
    _consider,
    _in_scope,
    _pass,
    _retire_pass,
    run,
)
from change_proposer.criteria import CriteriaUnavailable, acceptance_criteria, rollback_for
from change_proposer.factory_marking import factory_unit_id
from deploy_watcher.github import ReadError
from deploy_watcher.workflows import (
    ATTESTS_REVISION,
    ATTESTS_UNVERIFIED,
    Attestation,
    attestation_for,
)

PROPOSER = Path("src/change_proposer")
ORCHESTRATOR = Path("src/orchestrator")

CM = "alobarquest/change-manager"


def _attestation(level: str = ATTESTS_REVISION) -> Attestation:
    return Attestation(
        revision="a" * 40,
        level=level,
        attests="production reports serving the merged commit",
        rollout_job="build-and-deploy",
        trigger_step="Trigger Coolify redeploy",
    )


# --- what it can never reach ------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/items/1/approve",
        "/api/items/1/claim",
        "/api/items/1/outcome",
        "/api/items/1/handoff",
        "/api/items/1/resolve",
        "/api/items/1/reactivate",
        "/api/sync",
        "/api/window-runs",
    ],
)
def test_the_producer_cannot_write_to_any_lifecycle_route(path: str) -> None:
    """THE property. A producer that could approve its own proposal is a system asking itself
    for permission, and increment 3's admission term would be reading a record it wrote."""
    assert not is_allowed_write(path)


def test_the_only_write_is_the_proposal_ingress() -> None:
    assert is_allowed_write("/api/deploy-changes")
    # Anchored: a prefix, a suffix or a traversal is not the ingress.
    assert not is_allowed_write("/api/deploy-changes/1/approve")
    assert not is_allowed_write("/api/deploy-changes/")
    assert not is_allowed_write("/api/deploy-changes/../items/1/approve")


def test_the_producer_has_no_read_surface_at_all() -> None:
    """It writes one route and reads nothing.

    A first version carried a `GET /api/items` helper for "reporting what is already routed". It
    had ZERO callers, and a mutation pass showed its whole branch was untested — including its
    error mapping. A dead function is a defect in this repository, and deleting it also made the
    guard strictly tighter: every method on every path but the ingress is now refused.
    """
    assert not is_allowed_write("/api/items")
    assert not is_allowed_write("/api/events")


def test_a_forbidden_path_fails_before_a_request_is_built() -> None:
    """Not merely "the server would refuse" -- nothing leaves the process.

    The transport raises if it is ever reached, so the assertion is that it is NOT reached.
    """

    def explode(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        raise AssertionError(f"a request left the process: {request.url}")

    client = ChangeManagerClient("tok", transport=httpx.MockTransport(explode))
    with pytest.raises(ForbiddenEndpointError):
        client._request("POST", "/api/items/1/approve")
    client.close()


def test_the_program_imports_nothing_from_the_orchestrator() -> None:
    """Hosting an out-of-process program here is a packaging choice, not a coupling."""
    for source in sorted(PROPOSER.rglob("*.py")):
        tree = ast.parse(source.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("orchestrator"), f"{source} imports {node.module}"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("orchestrator"), f"{source}: {alias.name}"


# --- the criteria are transcribed, and refuse rather than guess ---------------------------------


def test_criteria_refuse_when_the_workflow_revision_is_not_transcribed() -> None:
    """A record whose criteria were guessed is worse than no record: the guess is what a later
    reader would hold the rollout to."""
    with pytest.raises(CriteriaUnavailable):
        acceptance_criteria(CM, None)


def test_criteria_refuse_for_a_repository_with_no_rollback_plan() -> None:
    with pytest.raises(CriteriaUnavailable):
        rollback_for("alobarquest/not-a-deploying-repo")


def test_criteria_name_the_job_and_step_that_reach_production() -> None:
    criteria = acceptance_criteria(CM, _attestation())
    assert any("build-and-deploy" in c and "Trigger Coolify redeploy" in c for c in criteria)
    assert any("production reports serving the merged commit" in c for c in criteria)


def test_a_weaker_attestation_says_so_in_the_record() -> None:
    """The ceiling on what a record can ever confirm must travel WITH the record.

    A reader who sees only the criteria, and not the attestation level they were derived from,
    would otherwise take "the rollout succeeded" as "the merged build is serving".
    """
    strong = acceptance_criteria(CM, _attestation(ATTESTS_REVISION))
    weak = acceptance_criteria(CM, _attestation(ATTESTS_UNVERIFIED))
    assert not any("does NOT prove" in c for c in strong)
    assert any("does NOT prove" in c for c in weak)


def test_a_repository_in_githubs_own_casing_finds_its_plan() -> None:
    """GitHub answers `AlobarQuest/change-manager`; the table is keyed lowercase.

    `_consider` already folds case for the workflow lookup, so a case-sensitive plan lookup would
    make the module accept GitHub's casing for one and refuse it for the other — refusing a
    repository as if it had no rollback plan at all. A mutation survived here until this existed:
    the fix was written first and nothing proved it.
    """
    assert rollback_for("AlobarQuest/Change-Manager").target == "image"
    assert rollback_for("ALOBARQUEST/BRAIN").target == "image"


def test_brain_rolls_back_to_an_image_never_a_commit() -> None:
    """brain builds from requirements.txt with no lockfile, so the same commit can rebuild to a
    different dependency set. Rolling back to a commit would be rolling forward into an untested
    one."""
    assert rollback_for("alobarquest/brain").target == "image"
    assert any("revert" in step for step in rollback_for("alobarquest/brain").steps)


def test_brains_derived_criteria_are_the_pair_change_managers_deploy_policy_must_carry() -> None:
    """THE CROSS-REPO PAIR, spelled out, because nothing mechanical joins the two sides.

    `change-manager`'s `deploy_policy.objections` byte-compares a record's stored criteria against
    the tuple its current version pins, and a record's stored criteria are what this function
    derived. So these two strings are the same judgment held in two repositories on purpose --
    that second copy is what makes a rollout-workflow change stop unattended landing until a human
    ratifies it. The failure mode if they drift is silent and total: every `brain` record objects
    `acceptance_criteria_not_ratified` forever, and nothing anywhere says which side moved.

    Literals rather than a derivation, for the reason the registry's own count test gives: a
    derived assertion would let the string change and stay green, which is exactly the direction
    that breaks the other repository. Change this and change deploy policy v3 in the same breath.

    Note there are TWO criteria and no third: the NOTE line is appended only below
    `ATTESTS_REVISION`, and `c5c0887` is at it.
    """
    criteria = acceptance_criteria(
        "alobarquest/brain", attestation_for("c5c088719cd340f0071b875c6a82439292ed8756")
    )
    assert criteria == (
        "the rollout runs for this merge on alobarquest/brain, and its production step "
        "concludes success (job 'deploy', step 'Deploy brain apps')",
        "every brain application this rollout triggered answered /api/health reporting the "
        "merged commit as its revision and a status of ok, within 600 seconds; an application "
        "whose Coolify UUID secret is unset is neither triggered nor checked, and a rollout "
        "that triggered none fails rather than passing empty",
    )


def test_every_in_scope_repository_has_both_halves() -> None:
    """Scope is the intersection, so a repository can never be considered with only one."""
    for repository in _in_scope():
        assert rollback_for(repository).steps


# --- eligibility ---------------------------------------------------------------------------------


class _Reader:
    def __init__(self, revision: str | None = "a" * 40) -> None:
        self._revision = revision

    def blob_revision(self, repository: str, path: str, ref: str) -> str | None:
        return self._revision


def _pull(**overrides: object) -> dict:
    base = {
        "number": 7,
        "title": "bump x",
        "author": "dependabot[bot]",
        "is_bot": True,
        "draft": False,
        "base_ref": "main",
    }
    return {**base, **overrides}


def test_no_proposed_fact_carries_the_pull_request_title() -> None:
    """A frozen record must carry only what stays true, and the title does not.

    `propose_deploy_change` compares proposed fields to stored ones and 409s on any difference,
    with no update path — and Dependabot rewrites a pull request IN PLACE when a newer version
    appears, changing its title. A title in the payload therefore turns every later pass into a
    permanent refusal for that pull request. Measured against a live change-manager before this
    test existed: a drifted `reasoning` answered 409.

    Asserted over the WHOLE payload rather than over `reasoning` alone, so moving the title to
    another field does not slip past.
    """
    title = "chore(deps): bump uvicorn from 0.51.0 to 0.52.0"
    proposal, why = _consider(_Reader(revision=_any_transcribed_revision()), CM, _pull(title=title))
    assert why == "eligible" and proposal is not None
    assert title not in json.dumps(proposal)
    assert "0.52.0" not in json.dumps(proposal)


def test_the_same_pull_request_proposes_identical_facts_across_a_title_change() -> None:
    """The property the test above protects, stated as the behaviour that matters."""
    reader = _Reader(revision=_any_transcribed_revision())
    first, _ = _consider(reader, CM, _pull(title="bump x from 1.0 to 1.1"))
    second, _ = _consider(reader, CM, _pull(title="bump x from 1.0 to 1.2"))
    assert first == second


def test_the_whole_proposed_payload_is_pinned() -> None:
    """Every field is frozen at the first row carrying it, so every field is worth asserting.

    A first version asserted four keys; mutations changing `actor`, `risk` and `change_class`
    survived. `actor` is the sharpest — attribution on a write-once record is permanent, and a
    mutation making it read `human-operator` would have had the estate's records claim a person
    proposed what a program did.
    """
    proposal, _ = _consider(_Reader(revision=_any_transcribed_revision()), CM, _pull(number=7))
    assert proposal is not None
    assert proposal["actor"] == "change-proposer"
    assert proposal["risk"] == "caution"
    assert proposal["change_class"] == "dependency-update"
    assert proposal["target_repository"] == CM
    assert proposal["pull_request_number"] == 7
    assert set(proposal) == {
        "target_repository",
        "pull_request_number",
        "change_class",
        "risk",
        "reasoning",
        "acceptance_criteria",
        "rollback_plan",
        "actor",
    }


def test_a_human_authored_pull_request_is_skipped() -> None:
    """ADR-0019 puts a human merging a pull request out of scope by construction.

    Keyed on the account TYPE. The landing ledger already carries the defect of keying on a
    `[bot]` login suffix, which a machine account can lack.
    """
    proposal, why = _consider(_Reader(), CM, _pull(is_bot=False, author="AlobarQuest"))
    assert proposal is None and why == "human-authored"


def test_a_pull_request_against_another_base_is_skipped() -> None:
    """Merging into some other base fires no rollout, so there is no deploy to record."""
    proposal, why = _consider(_Reader(), CM, _pull(base_ref="preview"))
    assert proposal is None and "preview" in why


def test_a_draft_is_skipped() -> None:
    """With a REAL revision, and asserting the REASON.

    A first version used `_Reader()`, whose default revision is untranscribed — so deleting the
    draft check entirely left the pull refusing at criteria and `proposal is None` still held. A
    mutation pass proved it: `if pull.get("draft")` → `if False` survived. The control now cannot
    pass for any reason but the one it names.
    """
    proposal, why = _consider(_Reader(revision=_any_transcribed_revision()), CM, _pull(draft=True))
    assert proposal is None and why == "draft"


def test_an_untranscribed_workflow_revision_refuses_rather_than_guessing() -> None:
    proposal, why = _consider(_Reader(revision="f" * 40), CM, _pull())
    assert proposal is None and why.startswith("REFUSED")


def test_an_eligible_pull_request_carries_both_required_fields() -> None:
    """Increment 1 refuses a record without either, so a proposal missing one is dead on arrival."""
    proposal, why = _consider(_Reader(revision=None), CM, _pull())
    assert proposal is None  # None revision is also untranscribed
    real = _any_transcribed_revision()
    proposal, why = _consider(_Reader(revision=real), CM, _pull())
    assert why == "eligible" and proposal is not None
    assert proposal["acceptance_criteria"] and proposal["rollback_plan"]["steps"]
    assert proposal["target_repository"] == CM
    assert proposal["pull_request_number"] == 7


def _any_transcribed_revision() -> str:
    """SOME revision the registry classifies — deliberately not "change-manager's".

    Named honestly after a first version called `_real_revision` and looked up
    `ROLLOUT_WORKFLOWS[CM].path` before returning the first entry with a truthy `rollout_job`,
    which can belong to a different repository: `REGISTRY` is flat, keyed by revision, and carries
    no repository association at all. The test would have passed while pairing a `brain`
    attestation with a `change-manager` pull request.

    Nothing here needs that pairing. In production it holds by construction — the revision is read
    from the target repository's own workflow file — so what these tests exercise is only "a
    transcribed revision yields a proposal", and the name now says so.
    """
    from deploy_watcher.workflows import REGISTRY

    for revision, attestation in REGISTRY.items():
        if attestation.rollout_job:
            return revision
    raise AssertionError("the registry transcribes no revision with a rollout job")


# --- the factory's third author case ------------------------------------------------------------

SPECIMEN_UNIT = "0f1e2d3c-4b5a-4968-8776-655443332211"
FACTORY_TITLE = f"SDS {SPECIMEN_UNIT}: Reformat embedded code blocks"


def _factory_pull(**overrides: object) -> dict:
    """What factory-runner opens: a USER account, carrying the marking on the title."""
    return _pull(**{"title": FACTORY_TITLE, "author": "AlobarQuest", "is_bot": False, **overrides})


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        (FACTORY_TITLE, SPECIMEN_UNIT),
        (f"SDS {SPECIMEN_UNIT}:", SPECIMEN_UNIT),
        # Anchored at the very start: a marking somewhere inside a title is not a marking.
        (f"chore: SDS {SPECIMEN_UNIT}: x", None),
        (f" SDS {SPECIMEN_UNIT}: x", None),
        # The identifier must be one, and lower case, exactly as the estate's other two readers
        # of a unit claim spell it.
        ("SDS not-a-uuid: x", None),
        (f"SDS {SPECIMEN_UNIT.upper()}: x", None),
        # The colon is part of the format.
        (f"SDS {SPECIMEN_UNIT} x", None),
        ("SDS ", None),
        ("", None),
        (None, None),
        # NOT a string. `open_pull_requests` projects the title straight from the response and
        # guards only the two nested objects it reads, so a malformed body puts anything here --
        # and `_pass` catches `ReadError`, not `TypeError`, so a raise would kill the whole
        # scheduled run with a traceback instead of reporting a finding.
        (17, None),
        (["SDS", SPECIMEN_UNIT], None),
        ({"title": SPECIMEN_UNIT}, None),
    ],
)
def test_the_marking_is_read_from_the_title_or_not_at_all(
    title: object, expected: str | None
) -> None:
    assert factory_unit_id(title) == expected


def test_a_factory_pull_request_is_proposable_and_a_human_s_is_still_refused() -> None:
    """BOTH, in one control. A single positive case cannot tell "recognises the marking" from
    "stopped filtering", and the filter is the thing that must not have moved."""
    reader = _Reader(revision=_any_transcribed_revision())

    factory, factory_why = _consider(reader, CM, _factory_pull())
    human, human_why = _consider(reader, CM, _pull(is_bot=False, author="AlobarQuest"))

    assert factory_why == "eligible" and factory is not None
    assert human is None and human_why == "human-authored"


def test_a_title_that_carries_the_prefix_but_names_no_unit_is_refused() -> None:
    """Fail toward refusing. A record asserting the factory opened it, that cannot say for what,
    is worse than no record -- and the line says which of the two refusals this was, because a
    user-authored title carrying the prefix is what one class of format drift looks like."""
    proposal, why = _consider(
        _Reader(revision=_any_transcribed_revision()),
        CM,
        _factory_pull(title="SDS not-a-uuid: something"),
    )

    assert proposal is None
    assert why.startswith("human-authored") and "names no work unit" in why


def test_the_shape_is_keyed_on_the_marking_rather_than_on_who_opened_it() -> None:
    """A machine account carrying the marking gets the factory record, not the update bot's.

    factory-runner opens as a user today because `FACTORY_PR_TOKEN` is a PAT. If it ever opens as
    the estate's App instead, GitHub reports a bot and nothing else about the pull request changes
    -- so the record must not change either.
    """
    proposal, why = _consider(
        _Reader(revision=_any_transcribed_revision()), CM, _pull(title=FACTORY_TITLE, is_bot=True)
    )

    assert why == "eligible" and proposal is not None
    assert proposal["change_class"] == FACTORY_CHANGE_CLASS
    assert SPECIMEN_UNIT in proposal["reasoning"]


def test_the_whole_factory_payload_is_pinned() -> None:
    """Every field is frozen at the first row carrying it, so every field is worth asserting.

    `change_class` most of all. It is DELIBERATELY outside change-manager's deploy policy, which
    pins `change_classes` to `{"dependency-update"}`: a factory record reusing that value would be
    approved by policy the instant it is proposed, and whether a machine-written change may land
    unattended is a decision for a person rather than a consequence of a string this program
    chooses.
    """
    proposal, _ = _consider(_Reader(revision=_any_transcribed_revision()), CM, _factory_pull())

    assert proposal is not None
    assert proposal["change_class"] == FACTORY_CHANGE_CLASS != BOT_CHANGE_CLASS
    assert proposal["reasoning"] == (
        f"landing this pull request on the default branch of {CM} redeploys production, so it is "
        "a deploying merge and carries a change record (ADR-0019). The factory opened it for work "
        f"unit {SPECIMEN_UNIT}: the change was produced by a runner acting under an authority "
        "envelope a human approved, rather than by a person or by an update bot."
    )
    # The rest is the deploying merge's own facts, and they do not depend on who opened it.
    assert proposal["risk"] == "caution"
    assert proposal["actor"] == "change-proposer"
    assert proposal["target_repository"] == CM
    assert proposal["pull_request_number"] == 7
    # The title's PROSE is still absent -- only the identifier, which is fixed for the life of the
    # pull request, is frozen into the record.
    assert "Reformat embedded code blocks" not in json.dumps(proposal)


def test_the_update_bot_payload_is_unchanged_by_the_factory_case() -> None:
    """The whole dict, as a literal. The key set alone would not see a factory clause leaking into
    `reasoning`, and a leak there is permanent: `reasoning` is an asserted field, so a reword turns
    every existing record into a terminal 409 with no update path."""
    proposal, why = _consider(_Reader(revision=_any_transcribed_revision()), CM, _pull())

    assert why == "eligible" and proposal is not None
    assert proposal["change_class"] == BOT_CHANGE_CLASS
    assert proposal["reasoning"] == (
        f"landing this pull request on the default branch of {CM} redeploys production, so it is "
        "a deploying merge and carries a change record (ADR-0019)."
    )
    assert set(proposal) == {
        "target_repository",
        "pull_request_number",
        "change_class",
        "risk",
        "reasoning",
        "acceptance_criteria",
        "rollback_plan",
        "actor",
    }
    assert "work unit" not in proposal["reasoning"]


# --- the pass itself -----------------------------------------------------------------------------


def test_submit_without_a_credential_is_refused_before_anything_runs(monkeypatch) -> None:
    monkeypatch.setenv("CHANGE_PROPOSER_GITHUB_TOKEN", "gh")
    monkeypatch.delenv("CHANGE_PROPOSER_CHANGE_MANAGER_TOKEN", raising=False)
    assert run(["--submit"]) == 2


def test_a_dry_run_needs_no_change_manager_credential(monkeypatch) -> None:
    """The default is a dry run, so the destructive path needs a deliberate flag."""
    monkeypatch.delenv("CHANGE_PROPOSER_GITHUB_TOKEN", raising=False)
    assert run([]) == 2  # still needs GitHub, but never asks for change-manager


def test_an_out_of_scope_repository_is_refused(monkeypatch) -> None:
    monkeypatch.setenv("CHANGE_PROPOSER_GITHUB_TOKEN", "gh")
    assert run(["--repository", "alobarquest/intent-packages"]) == 2


# --- transport-level answers ---------------------------------------------------------------------


def _client(handler) -> ChangeManagerClient:
    return ChangeManagerClient("tok", transport=httpx.MockTransport(handler))


def test_a_replay_is_reported_as_a_replay_not_a_new_record() -> None:
    """change-manager answers 200 for an identical proposal, which is what makes the pass safe
    to re-run."""
    client = _client(lambda request: httpx.Response(200, json={"id": 44, "status": "pending"}))
    record, created = client.propose({})
    assert record["id"] == 44 and created is False
    client.close()


def test_a_conflicting_proposal_is_a_finding() -> None:
    """Whoever proposes FIRST fixes the facts; a 409 means somebody proposed different ones."""
    client = _client(lambda request: httpx.Response(409, json={"detail": "held"}))
    with pytest.raises(ProposalRefused):
        client.propose({})
    client.close()


def test_a_403_names_the_scope_rather_than_the_transport() -> None:
    """The likeliest real failure once scopes ship: the wrong credential in the environment."""
    client = _client(lambda request: httpx.Response(403, json={"detail": "nope"}))
    with pytest.raises(ChangeManagerError, match="not scoped for this route"):
        client.propose({})
    client.close()


@pytest.mark.parametrize(
    "base_url",
    ["https://host..example", "https://" + "a" * 64 + ".example", "https://host\n"],
)
def test_a_malformed_base_url_is_an_answer_rather_than_a_raise(base_url: str) -> None:
    """`UnicodeError` is a `ValueError` and is neither an `HTTPError` nor an `InvalidURL`.

    Increment 3 found exactly this escaping a reader that promised never to raise, reaching a bare
    HTTP 500 -- and found it only by probing the real library, because the mutation guarding the
    `except` and the control written for it shared one incomplete model of what httpx raises.
    A HOST shape, not only a whitespace shape.

    Constructing the client raises for some of these shapes and requesting raises for others, so
    the assertion spans both -- a guard on only one leaves an env-var typo crashing the pass.
    """
    try:
        client = ChangeManagerClient("tok", base_url=base_url)
    except ChangeManagerError:
        return
    try:
        with pytest.raises(ChangeManagerError):
            client.propose({})
    finally:
        client.close()


# --- the pass itself, end to end -----------------------------------------------------------------


class _Pulls:
    """A reader with both halves, so `_pass` and `run` can be driven without GitHub."""

    def __init__(self, pulls: dict[str, list[dict]], revision: str | None) -> None:
        self._pulls, self._revision = pulls, revision

    def open_pull_requests(self, repository: str) -> list[dict]:
        if repository not in self._pulls:
            raise ReadError(f"github rejected GET for {repository}: 502")
        return self._pulls[repository]

    def blob_revision(self, repository: str, path: str, ref: str) -> str | None:
        return self._revision


def _statuses(outcomes) -> list[str]:
    return [o.status for o in outcomes]


def test_a_dry_run_sends_absolutely_nothing() -> None:
    """`client is None` IS the dry run, and the docstring calls that the design.

    Asserted the way the forbidden-path test is: the transport raises if reached, so the assertion
    is that it is NOT reached. A mutation making `_consider_one` propose regardless survived until
    this existed.
    """

    def explode(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        raise AssertionError(f"a dry run sent a request: {request.url}")

    reader = _Pulls({CM: [_pull()]}, _any_transcribed_revision())
    outcomes = _pass(reader, [CM], None)
    assert _statuses(outcomes) == ["would-propose"]
    ChangeManagerClient("t", transport=httpx.MockTransport(explode)).close()


def test_a_new_record_and_a_replay_are_reported_differently() -> None:
    """201 is a record that did not exist; 200 is one that did. A mutation inverting them survived
    until this existed, so a re-run could have reported every replay as a fresh proposal."""
    reader = _Pulls({CM: [_pull(number=1), _pull(number=2)]}, _any_transcribed_revision())
    codes = iter([201, 200])
    client = ChangeManagerClient(
        "t",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(next(codes), json={"id": 9, "status": "pending"})
        ),
    )
    assert _statuses(_pass(reader, [CM], client)) == ["proposed", "replayed"]
    client.close()


def test_every_failure_mode_is_reported_rather_than_raised() -> None:
    """A conflict, a transport fault and an unreadable repository each become one outcome."""
    reader = _Pulls({CM: [_pull()]}, _any_transcribed_revision())
    conflict = ChangeManagerClient(
        "t", transport=httpx.MockTransport(lambda r: httpx.Response(409, json={"detail": "held"}))
    )
    assert _statuses(_pass(reader, [CM], conflict)) == ["refused"]
    conflict.close()

    broken = ChangeManagerClient(
        "t", transport=httpx.MockTransport(lambda r: httpx.Response(500, json={"detail": "boom"}))
    )
    assert _statuses(_pass(reader, [CM], broken)) == ["error"]
    broken.close()

    # An unreadable repository is a finding about that repository, not the end of the pass.
    assert _statuses(_pass(_Pulls({}, "x" * 40), [CM], None)) == ["unreadable"]


def test_a_conflict_carries_change_managers_own_explanation() -> None:
    """A refusal on a write-once record is permanent, so an operator must be able to see WHICH
    frozen fact drifted. A first version replaced the detail with a fixed sentence."""
    client = ChangeManagerClient(
        "t",
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                409, json={"detail": "asserting different acceptance_criteria"}
            )
        ),
    )
    with pytest.raises(ProposalRefused, match="acceptance_criteria"):
        client.propose({})
    client.close()


def test_an_unreadable_repository_is_a_finding_and_changes_the_exit_code(monkeypatch) -> None:
    """`unreadable` must stay in the findings set and the exit code must reflect it — two separate
    mutations survived here, and either one silently turns a broken pass into a clean one."""
    from change_proposer import cli as cli_module

    monkeypatch.setenv("CHANGE_PROPOSER_GITHUB_TOKEN", "gh")
    monkeypatch.setattr(cli_module, "GitHubReader", lambda token: _Ctx(_Pulls({}, "x" * 40)))
    assert cli_module.run([]) == 3


class _Ctx:
    def __init__(self, inner) -> None:
        self._inner = inner

    def __enter__(self):
        return self._inner

    def __exit__(self, *_: object) -> None:
        return None


def test_without_submit_no_writing_client_is_ever_built(monkeypatch) -> None:
    """`--submit` is the flag that separates reporting from writing, and it must gate the CLIENT.

    A mutation replacing `if args.submit:` with `if True:` survived every other control, because
    nothing drove `run()` far enough to see a client built. Asserted by making construction itself
    fail the test: if a dry run builds one, the sentinel fires.
    """
    from change_proposer import cli as cli_module

    built: list[str] = []

    def _sentinel(token: str, **kwargs: object) -> None:  # pragma: no cover - must not run
        built.append(token)
        raise AssertionError("a dry run built a change-manager client")

    monkeypatch.setenv("CHANGE_PROPOSER_GITHUB_TOKEN", "gh")
    monkeypatch.setenv("CHANGE_PROPOSER_CHANGE_MANAGER_TOKEN", "cm")
    monkeypatch.setattr(cli_module, "ChangeManagerClient", _sentinel)
    monkeypatch.setattr(
        cli_module, "GitHubReader", lambda token: _Ctx(_Pulls({CM: [_pull()]}, "x" * 40))
    )
    # Scoped to one repository so the other's absence from the fake reader is not a finding.
    #
    # EXIT 3, not 0, and the change is the point rather than an inconvenience: this fixture's
    # blob revision is untranscribed, so nothing can say what a green rollout there would
    # prove. That used to be reported as an ordinary `skipped` — the same status a draft gets
    # — and skips are not findings, so the pass exited 0 in silence. It is now `underivable`
    # and a finding. The property this test exists for is the sentinel below.
    assert cli_module.run(["--repository", CM]) == cli_module.EXIT_FINDINGS
    assert built == [], "a dry run built a change-manager client"
    assert built == []


def test_an_untranscribed_rollout_workflow_is_a_finding_not_a_skip(monkeypatch) -> None:
    """The silent case, and the one adversarial review of increment 5 named.

    When nobody has transcribed the rollout workflow's current bytes, `_consider` refuses to
    guess what a green run there would prove — correctly. But that refusal used to be
    reported as `skipped`, the same status a draft or a human's pull request gets, and
    `skipped` is not a finding. So the workflow deciding what a deploy PROVES could change,
    no record could be derived for any pull request on that repository, and the scheduled
    pass exited 0.

    A skip means "this pull request is not our business". A refusal means "it is, and nobody
    can say what its deploy would attest". They must not share an exit code.
    """
    from change_proposer import cli as cli_module

    monkeypatch.setenv("CHANGE_PROPOSER_GITHUB_TOKEN", "gh")
    monkeypatch.setattr(
        cli_module, "GitHubReader", lambda token: _Ctx(_Pulls({CM: [_pull()]}, "x" * 40))
    )
    assert cli_module.run(["--repository", CM]) == cli_module.EXIT_FINDINGS


def test_a_pull_request_that_is_not_our_business_is_still_only_a_skip(monkeypatch) -> None:
    """The control. Without it the test above passes under "every outcome is a finding",
    which would make the exit code useless in the other direction — a draft or a human's
    pull request is a perfectly clean pass."""
    from change_proposer import cli as cli_module

    monkeypatch.setenv("CHANGE_PROPOSER_GITHUB_TOKEN", "gh")
    monkeypatch.setattr(
        cli_module,
        "GitHubReader",
        lambda token: _Ctx(_Pulls({CM: [_pull(draft=True)]}, "x" * 40)),
    )
    assert cli_module.run(["--repository", CM]) == cli_module.EXIT_OK


# ---------------------------------------------------------------------------
# ADR-0019 increment 5b: the producer is also a RECONCILER.
# ---------------------------------------------------------------------------


class FakeRetireClient:
    """Records what the sweep read and what it retired, so a test can assert on the absence."""

    def __init__(self, records: list[dict[str, Any]], error: Exception | None = None) -> None:
        self._records = records
        self._error = error
        self.retired: list[tuple[int, int]] = []

    def records(self) -> list[dict[str, Any]]:
        if self._error is not None:
            raise self._error
        return self._records

    def retire(self, item_id: int, *, pull_request_number: int) -> dict[str, Any]:
        self.retired.append((item_id, pull_request_number))
        return {"id": item_id, "status": "resolved"}


class FakeDispositions:
    def __init__(self, answers: dict[tuple[str, int], str | None | Exception]) -> None:
        self._answers = answers
        self.asked: list[tuple[str, int]] = []

    def pull_request_disposition(self, repository: str, number: int) -> str | None:
        self.asked.append((repository, number))
        answer = self._answers[(repository, number)]
        if isinstance(answer, Exception):
            raise answer
        return answer if answer is None else str(answer)


def _record(number: int, *, status: str = "approved", item_id: int = 44) -> dict[str, Any]:
    return {
        "id": item_id,
        "target_repository": CM,
        "pull_request_number": number,
        "status": status,
    }


def test_a_record_whose_pull_request_closed_unmerged_is_retired() -> None:
    """Production item 44's case: a record standing approved for a change that can never happen,
    with nothing in the estate positioned to notice."""
    client = FakeRetireClient([_record(42)])
    reader = FakeDispositions({(CM, 42): "closed_unmerged"})

    outcomes = _retire_pass(reader, [CM], client)

    assert client.retired == [(44, 42)]
    assert [o.status for o in outcomes] == ["retired"]


@pytest.mark.parametrize("disposition", ["open", "merged"])
def test_a_pull_request_that_is_open_or_landed_is_left_alone(disposition: str) -> None:
    """The control. Without it the sweep would pass for a rule that retires everything."""
    client = FakeRetireClient([_record(49)])
    reader = FakeDispositions({(CM, 49): disposition})

    outcomes = _retire_pass(reader, [CM], client)

    assert client.retired == []
    assert [o.status for o in outcomes] == ["skipped"]


def test_an_unreadable_pull_request_retires_nothing_and_is_a_FINDING() -> None:
    """RETIRE ON A FACT, NEVER ON ABSENCE. "I could not read it" is not "it was closed"."""
    client = FakeRetireClient([_record(42)])
    reader = FakeDispositions({(CM, 42): ReadError("github is unreachable")})

    outcomes = _retire_pass(reader, [CM], client)

    assert client.retired == []
    assert outcomes[0].status in FINDING_STATUSES


def test_a_pull_request_github_does_not_have_retires_nothing() -> None:
    """An absent subject is a question about the record, not an answer about the change."""
    client = FakeRetireClient([_record(9999)])
    reader = FakeDispositions({(CM, 9999): None})

    outcomes = _retire_pass(reader, [CM], client)

    assert client.retired == []
    assert outcomes[0].status in FINDING_STATUSES


@pytest.mark.parametrize("status", ["resolved", "wontfix"])
def test_a_record_a_human_already_settled_is_not_touched(status: str) -> None:
    """`wontfix` is a human's decision and `resolved` is already retired. Re-asserting either
    would be the machine re-deciding rather than reconciling -- and it costs a GitHub read the
    sweep has no reason to make."""
    client = FakeRetireClient([_record(42, status=status)])
    reader = FakeDispositions({})

    outcomes = _retire_pass(reader, [CM], client)

    assert client.retired == [] and reader.asked == [] and outcomes == []


def test_a_record_for_a_repository_out_of_scope_is_not_swept() -> None:
    """A repository this program does not transcribe is one it has no business deciding about."""
    client = FakeRetireClient([{**_record(31), "target_repository": "alobarquest/somewhere-else"}])
    reader = FakeDispositions({})

    assert _retire_pass(reader, [CM], client) == []
    assert client.retired == []


def test_a_record_naming_no_subject_is_reported_rather_than_swept() -> None:
    client = FakeRetireClient([{"id": 44, "target_repository": CM, "pull_request_number": None}])
    reader = FakeDispositions({})

    outcomes = _retire_pass(reader, [CM], client)

    assert client.retired == []
    assert outcomes[0].status in FINDING_STATUSES


def test_a_dry_run_examines_no_records_and_says_so() -> None:
    """Reading the records needs the credential a dry run must not touch. Reported as a skip so a
    bare invocation says it examined nothing rather than that it found nothing."""
    outcomes = _retire_pass(FakeDispositions({}), [CM], None)

    assert [o.status for o in outcomes] == ["skipped"]
    assert "--submit" in outcomes[0].detail


def test_an_unreadable_listing_is_a_finding_rather_than_a_clean_pass() -> None:
    client = FakeRetireClient([], error=ChangeManagerError("change-manager is unreachable"))

    outcomes = _retire_pass(FakeDispositions({}), [CM], client)

    assert [o.status for o in outcomes] == ["error"]


def test_the_retirement_path_is_reachable_and_nothing_adjacent_is() -> None:
    """The guard is anchored, so a prefix, a trailing slash, a traversal and a sibling verb on the
    same item all fail. The producer gained a SECOND write in increment 5b; it did not gain the
    general decision verbs."""
    assert is_allowed_write("/api/items/44/deploy-retirement")
    for forbidden in (
        "/api/items/44/deploy-retirement/",
        "/api/items/44/deploy-retirementx",
        "/api/items/../44/deploy-retirement",
        "/api/items/44/resolve",
        "/api/items/44/approve",
        "/api/items/44/wontfix",
        "/api/items/0/deploy-retirement",
        "/api/items/deploy-retirement",
    ):
        assert not is_allowed_write(forbidden), forbidden


def test_the_read_surface_is_the_listing_and_nothing_else() -> None:
    assert is_allowed_read("/api/items")
    for forbidden in ("/api/items/44", "/api/events", "/api/deploy-policy", "/api/items/"):
        assert not is_allowed_read(forbidden), forbidden


def test_a_forbidden_retirement_never_reaches_the_transport() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        return httpx.Response(200, json={})

    client = ChangeManagerClient("t", transport=httpx.MockTransport(handler))
    with pytest.raises(ForbiddenEndpointError):
        client._send("POST", "/api/items/44/resolve", json={})
    assert seen == []


def test_the_sweep_names_the_pipeline_on_every_listing_it_asks_for() -> None:
    """change-manager withholds a proposed source from a caller that does not name one, so a query
    that forgot it would read a clean empty list and report a pass having examined nothing."""
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, json=[])

    client = ChangeManagerClient("t", transport=httpx.MockTransport(handler))
    client.records()

    assert seen and seen[0].params.get("source") == "deploy"
