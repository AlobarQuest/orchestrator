"""The change producer: what it proposes, what it refuses, and what it can never reach.

ADR-0019 increment 4. The property that matters most here is a NEGATIVE one — this program cannot
approve the records it creates — and it is asserted three ways: the client refuses the path before
building a request, the import graph proves it holds nothing else, and change-manager's own
`propose` scope refuses it server-side (pinned in that repository, not here).
"""

from __future__ import annotations

import ast
from pathlib import Path

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
from change_proposer.cli import _consider, _in_scope, run
from change_proposer.criteria import CriteriaUnavailable, acceptance_criteria, rollback_for
from deploy_watcher.workflows import ATTESTS_REVISION, ATTESTS_UNVERIFIED, Attestation

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


def test_reads_are_bounded_too() -> None:
    assert is_allowed_read("/api/items")
    assert not is_allowed_read("/api/items/1/handoff")


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


def test_brain_rolls_back_to_an_image_never_a_commit() -> None:
    """brain builds from requirements.txt with no lockfile, so the same commit can rebuild to a
    different dependency set. Rolling back to a commit would be rolling forward into an untested
    one."""
    assert rollback_for("alobarquest/brain").target == "image"
    assert any("revert" in step for step in rollback_for("alobarquest/brain").steps)


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
    proposal, _ = _consider(_Reader(), CM, _pull(draft=True))
    assert proposal is None


def test_an_untranscribed_workflow_revision_refuses_rather_than_guessing() -> None:
    proposal, why = _consider(_Reader(revision="f" * 40), CM, _pull())
    assert proposal is None and why.startswith("REFUSED")


def test_an_eligible_pull_request_carries_both_required_fields() -> None:
    """Increment 1 refuses a record without either, so a proposal missing one is dead on arrival."""
    proposal, why = _consider(_Reader(revision=None), CM, _pull())
    assert proposal is None  # None revision is also untranscribed
    real = _real_revision()
    proposal, why = _consider(_Reader(revision=real), CM, _pull())
    assert why == "eligible" and proposal is not None
    assert proposal["acceptance_criteria"] and proposal["rollback_plan"]["steps"]
    assert proposal["target_repository"] == CM
    assert proposal["pull_request_number"] == 7


def _real_revision() -> str:
    from deploy_watcher.workflows import REGISTRY, ROLLOUT_WORKFLOWS

    path = ROLLOUT_WORKFLOWS[CM].path
    for revision, attestation in REGISTRY.items():
        if attestation.rollout_job:
            return revision
    raise AssertionError(f"no transcribed revision for {path}")


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
    with pytest.raises(ChangeManagerError, match="propose-scoped"):
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
