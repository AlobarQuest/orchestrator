"""The unit-caused lane, measured against real git repositories (ADR-0030).

Real repositories, for the reason this suite's conftest already records: a fake git runner would
let these tests agree with a model of git rather than with git, and the whole subject here is a
git predicate whose OTHER reading is the estate's standing rule about squash-merge.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from activation_sweep.bind import (
    BOUND,
    CHECKED,
    OBSERVED,
    RECORDED,
    REFUSED,
    SUPERSEDED,
    UNAVAILABLE,
    UNSATISFIED,
    WAITING,
    Candidate,
    NullBinder,
    bind_checkout,
    binding_payload,
    has_conditions,
    has_findings,
)
from activation_sweep.binding import BindingError, content_digest, has_activated
from activation_sweep.binding_client import BindingCallError, is_allowed_write
from tests.activation_sweep.conftest import Estate, git

REPOSITORY = "AlobarQuest/example"
UNIT_ID = "eb7c36f7-4f7e-5d00-9709-779c0c1152a4"


def candidate_row(commit: str, *, binding_id: str | None = None) -> dict[str, Any]:
    return {
        "work_unit_id": UNIT_ID,
        "work_package_revision_id": "11111111-2222-3333-4444-555555555555",
        "package_revision_hash": "sha256:package",
        "unit_key": "example-ac-001",
        "work_unit_version": 3,
        "source_repository": REPOSITORY,
        "pr_number": 81,
        "source_commit": "f" * 40,
        "merge_commit": commit,
        "binding_id": binding_id,
    }


class FakeBinder:
    """A hermetic orchestrator. Records what it was asked to bind, so the payload is inspectable."""

    def __init__(self, rows: list[dict[str, Any]], *, refuse: bool = False) -> None:
        self.rows = rows
        self.refuse = refuse
        self.bound: list[tuple[str, dict[str, Any]]] = []
        self.observed: list[tuple[str, dict[str, Any]]] = []
        self.asked: list[str] = []

    def candidates(self, repository: str) -> list[dict[str, Any]]:
        self.asked.append(repository)
        return list(self.rows)

    def bind(self, work_unit_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self.refuse:
            raise BindingCallError("orchestrator rejected POST: 409")
        self.bound.append((work_unit_id, payload))
        return {"id": "binding-1"}

    def observe(self, binding_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self.refuse:
            raise BindingCallError("orchestrator rejected POST: 409")
        self.observed.append((binding_id, payload))
        return {"id": "observation-1"}


class BrokenBinder:
    def candidates(self, repository: str) -> list[dict[str, Any]]:
        raise BindingCallError("orchestrator answered 401")

    def bind(self, work_unit_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("must not be reached")

    def observe(self, binding_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("must not be reached")


# ---------------------------------------------------------------------------
# The predicate.
# ---------------------------------------------------------------------------


def test_a_landing_commit_the_machine_holds_is_activated(estate: Estate) -> None:
    head = git(estate.local, "rev-parse", "HEAD").strip()

    assert has_activated(estate.local, head)


def test_a_landing_commit_the_machine_has_not_pulled_is_not_activated(estate: Estate) -> None:
    """Acceptance 7's mechanism: the commit exists upstream and this checkout has never seen it."""
    upstream = estate.land_upstream()

    assert not has_activated(estate.local, upstream)


def test_an_ancestor_stays_activated_after_the_default_branch_moves_on(estate: Estate) -> None:
    """REACHABILITY, NOT EQUALITY -- and this is the test that says why.

    `HEAD == merge_commit` would be true for about as long as it takes the next thing to land, so
    a machine that is perfectly up to date would report having activated nothing.
    """
    landed = git(estate.local, "rev-parse", "HEAD").strip()
    estate.land_upstream()
    git(estate.local, "fetch")
    git(estate.local, "merge", "--ff-only", "origin/main")

    assert git(estate.local, "rev-parse", "HEAD").strip() != landed
    assert has_activated(estate.local, landed)


def test_a_commit_that_is_not_in_this_repository_at_all_is_not_activated(estate: Estate) -> None:
    assert not has_activated(estate.local, "0" * 40)


# ---------------------------------------------------------------------------
# The digest.
# ---------------------------------------------------------------------------


def test_the_digest_is_stable_across_two_runs_of_an_unchanged_checkout(estate: Estate) -> None:
    """Acceptance 5."""
    assert content_digest(estate.local) == content_digest(estate.local)


def test_the_digest_carries_the_prefix_the_binding_table_requires(estate: Estate) -> None:
    """`shasum` and `hashlib` both emit bare hex, and `_validate_digests` refuses that."""
    digest = content_digest(estate.local)

    assert digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 64


def test_the_digest_moves_when_the_content_does(estate: Estate) -> None:
    """The control for stability: a digest that never moved would pass the test above too."""
    before = content_digest(estate.local)
    estate.commit_locally()

    assert content_digest(estate.local) != before


def test_an_unreadable_checkout_raises_rather_than_answering(tmp_path: Path) -> None:
    with pytest.raises(BindingError):
        content_digest(tmp_path / "nowhere")


# ---------------------------------------------------------------------------
# The pass.
# ---------------------------------------------------------------------------


def test_a_unit_whose_landing_is_activated_is_bound(estate: Estate) -> None:
    head = git(estate.local, "rev-parse", "HEAD").strip()
    binder = FakeBinder([candidate_row(head)])

    summary = bind_checkout(str(estate.local), binder, fetch=False, dry_run=False)

    assert binder.asked == [REPOSITORY]
    assert [unit["outcome"] for unit in summary["units"]] == [RECORDED]
    assert len(binder.bound) == 1
    unit_id, payload = binder.bound[0]
    assert unit_id == UNIT_ID
    assert payload["kind"] == "machine_local"
    assert payload["merge_commit"] == head
    assert payload["artifact_digest"] == content_digest(estate.local)
    assert not has_findings([summary])


def test_a_unit_whose_landing_the_machine_has_not_pulled_gets_no_binding(
    estate: Estate,
) -> None:
    """ACCEPTANCE 7, the negative control, end to end.

    WAITING is not a finding. It is the ordinary state between a unit completing and the next
    `git pull`, and reporting it would make this control red for every unit in that gap -- the
    permanently-red control this estate has now produced four times by getting exactly this wrong.
    """
    upstream = estate.land_upstream()
    binder = FakeBinder([candidate_row(upstream)])

    summary = bind_checkout(str(estate.local), binder, fetch=False, dry_run=False)

    assert [unit["outcome"] for unit in summary["units"]] == [WAITING]
    assert binder.bound == []
    assert not has_findings([summary])


def test_a_unit_already_bound_is_skipped_rather_than_rewritten(estate: Estate) -> None:
    """ACCEPTANCE 6. The digest is over HEAD, which moves; a rewrite would conflict, forever.

    A producer must not turn its own earlier work into an alarm either, so BOUND is not a finding.
    """
    head = git(estate.local, "rev-parse", "HEAD").strip()
    binder = FakeBinder([candidate_row(head, binding_id="already-there")])

    summary = bind_checkout(str(estate.local), binder, fetch=False, dry_run=False)

    assert [unit["outcome"] for unit in summary["units"]] == [BOUND]
    assert binder.bound == []
    assert not has_findings([summary])


def test_a_second_pass_over_a_moved_head_binds_nothing_new(estate: Estate) -> None:
    """The two passes composed, which is what the skip actually protects.

    Pass one binds at one HEAD. The machine then moves on. Pass two sees the unit reported as
    bound and does not present the same source tuple with a different digest -- which the
    orchestrator would refuse as a conflict, on a morning when nothing was wrong.
    """
    head = git(estate.local, "rev-parse", "HEAD").strip()
    first = FakeBinder([candidate_row(head)])
    bind_checkout(str(estate.local), first, fetch=False, dry_run=False)

    estate.commit_locally()
    second = FakeBinder([candidate_row(head, binding_id="written-by-the-first-pass")])
    summary = bind_checkout(str(estate.local), second, fetch=False, dry_run=False)

    assert second.bound == []
    assert [unit["outcome"] for unit in summary["units"]] == [BOUND]


def test_a_refused_binding_is_a_finding(estate: Estate) -> None:
    head = git(estate.local, "rev-parse", "HEAD").strip()
    binder = FakeBinder([candidate_row(head)], refuse=True)

    summary = bind_checkout(str(estate.local), binder, fetch=False, dry_run=False)

    assert [unit["outcome"] for unit in summary["units"]] == [REFUSED]
    assert has_findings([summary])


def test_a_candidates_read_that_fails_is_a_finding_and_costs_only_that_checkout(
    estate: Estate,
) -> None:
    summary = bind_checkout(str(estate.local), BrokenBinder(), fetch=False, dry_run=False)

    assert summary["unavailable"] is True
    assert summary["reason"] is not None
    assert has_findings([summary])


def test_an_unmeasurable_checkout_is_a_finding_and_names_why(tmp_path: Path) -> None:
    summary = bind_checkout(str(tmp_path / "nowhere"), FakeBinder([]), fetch=False, dry_run=False)

    assert summary["unavailable"] is True
    assert summary["reason"]
    assert has_findings([summary])


def test_a_candidate_missing_a_field_names_the_field_rather_than_raising_a_bare_key_error() -> None:
    """The guard earns its place on the MESSAGE, and only a direct read can see that.

    Through `bind_checkout` the outcome is `unavailable` either way -- a bare `KeyError` is in the
    RECOVERABLE family and reaches the same branch -- so the pass-level test below cannot tell the
    guard from its absence. What differs is what a person reads at 07:10: `the orchestrator's
    candidate is missing merge_commit` names a narrowed contract, where `KeyError: merge_commit`
    reads as this program being broken. Found by mutation: deleting the guard survived every
    control until this existed.
    """
    row = candidate_row("a" * 40)
    del row["merge_commit"]

    with pytest.raises(BindingCallError) as raised:
        Candidate.of(row)

    assert "merge_commit" in str(raised.value)


def test_a_candidate_missing_a_field_is_a_finding_rather_than_a_guess(estate: Estate) -> None:
    """A response model DROPS every key it does not declare, so a narrowed contract arrives
    as absence rather than as an error."""
    head = git(estate.local, "rev-parse", "HEAD").strip()
    row = candidate_row(head)
    del row["merge_commit"]
    binder = FakeBinder([row])

    summary = bind_checkout(str(estate.local), binder, fetch=False, dry_run=False)

    assert [unit["outcome"] for unit in summary["units"]] == [UNAVAILABLE]
    assert binder.bound == []
    assert has_findings([summary])


def test_a_dry_run_reads_and_writes_nothing(estate: Estate) -> None:
    head = git(estate.local, "rev-parse", "HEAD").strip()
    binder = FakeBinder([candidate_row(head)])

    summary = bind_checkout(str(estate.local), NullBinder(binder), fetch=False, dry_run=True)

    assert binder.asked == [REPOSITORY]
    assert binder.bound == []
    unit = summary["units"][0]
    assert unit["outcome"] == RECORDED
    assert unit["dry_run"] is True
    assert unit["record"]["kind"] == "machine_local"


def test_a_pass_over_a_repository_with_no_candidates_is_quiet(estate: Estate) -> None:
    summary = bind_checkout(str(estate.local), FakeBinder([]), fetch=False, dry_run=False)

    assert summary["units"] == []
    assert not has_findings([summary])


# ---------------------------------------------------------------------------
# The payload.
# ---------------------------------------------------------------------------


def test_the_payload_omits_the_registry_columns_rather_than_blanking_them() -> None:
    """Omitting is what says nobody wrote one. The orchestrator REFUSES a placeholder."""
    payload = binding_payload(
        Candidate.of(candidate_row("a" * 40)),
        path="/Users/x/Projects/example",
        head="b" * 40,
        digest="sha256:" + "c" * 64,
    )

    assert "artifact_registry" not in payload
    assert "artifact_repository" not in payload
    assert "artifact_name" not in payload
    assert payload["kind"] == "machine_local"
    assert payload["summary"]["activation"] == {
        "path": "/Users/x/Projects/example",
        "head": "b" * 40,
        "digest_method": "git archive HEAD, sha256",
    }


def test_the_idempotency_key_is_a_function_of_the_unit_and_nothing_that_moves() -> None:
    """The backstop for a lost response inside one pass, where HEAD was read once.

    Keying it on the digest instead would make a retry at a moved HEAD present a NEW key with the
    same source tuple, which is a conflict rather than a replay.
    """
    row = candidate_row("a" * 40)
    first = binding_payload(
        Candidate.of(row), path="/p", head="b" * 40, digest="sha256:" + "1" * 64
    )
    later = binding_payload(
        Candidate.of(row), path="/p", head="e" * 40, digest="sha256:" + "2" * 64
    )

    assert first["idempotency_key"] == later["idempotency_key"] == f"machine-activation:{UNIT_ID}"


# ---------------------------------------------------------------------------
# The activation check: whether the bound artifact is what the next start will execute.
# ---------------------------------------------------------------------------


def _activation(summary: dict[str, Any]) -> dict[str, Any]:
    return summary["units"][0]["activation"]


def test_binding_a_unit_files_its_activation_check_in_the_same_pass(estate: Estate) -> None:
    """The two halves happen together, which is what keeps the window from being missed. The
    digest is computed once per pass, so the observation names the artifact just bound."""
    head = git(estate.local, "rev-parse", "HEAD").strip()
    binder = FakeBinder([candidate_row(head)])

    summary = bind_checkout(str(estate.local), binder, fetch=False, dry_run=False)

    assert summary["units"][0]["outcome"] == RECORDED
    assert _activation(summary)["outcome"] == OBSERVED
    assert len(binder.observed) == 1
    binding_id, payload = binder.observed[0]
    assert binding_id == "binding-1"
    assert payload["kind"] == "machine_local"
    assert payload["environment"] == "operator_machine"
    assert payload["observed_artifact_digest"] == binder.bound[0][1]["artifact_digest"]
    assert payload["activation_summary"]["merge_commit_present"] == "yes"


def test_an_already_observed_binding_is_checked_and_left_alone(estate: Estate) -> None:
    """A second pass must replay nothing: the ingest refuses a repeat carrying different facts,
    and a re-read at a moved HEAD would present exactly that."""
    head = git(estate.local, "rev-parse", "HEAD").strip()
    digest = content_digest(estate.local)
    row = candidate_row(head, binding_id="binding-1")
    row["binding_artifact_digest"] = digest
    row["observation_id"] = "observation-1"
    binder = FakeBinder([row])

    summary = bind_checkout(str(estate.local), binder, fetch=False, dry_run=False)

    assert summary["units"][0]["outcome"] == BOUND
    assert _activation(summary)["outcome"] == CHECKED
    assert binder.observed == []


def test_an_artifact_the_head_has_moved_past_is_superseded_rather_than_observed(
    estate: Estate,
) -> None:
    """Not a finding, and this is the case that says why. The artifact IS the tree its digest was
    taken over, so once HEAD moves past it that tree is no longer what the next start executes
    and there is nothing left to observe -- a fact about time, not about the machine."""
    head = git(estate.local, "rev-parse", "HEAD").strip()
    row = candidate_row(head, binding_id="binding-1")
    row["binding_artifact_digest"] = "sha256:" + "e" * 64
    binder = FakeBinder([row])

    summary = bind_checkout(str(estate.local), binder, fetch=False, dry_run=False)

    assert _activation(summary)["outcome"] == SUPERSEDED
    assert binder.observed == []
    assert not has_findings([summary])
    assert not has_conditions([summary])


def test_a_checkout_that_has_lost_the_landing_commit_records_nothing(
    estate: Estate,
) -> None:
    """The third negative control, and the one that could not be measured any other way: the
    binding exists, so the commit was present when it was written, and the machine has since
    stopped holding it. The fact is MEASURED again rather than carried forward -- asserting it
    from the binding's existence would be the producer attesting to its own act.
    """
    digest = content_digest(estate.local)
    row = candidate_row("c" * 40, binding_id="binding-1")
    row["binding_artifact_digest"] = digest
    binder = FakeBinder([row])

    summary = bind_checkout(str(estate.local), binder, fetch=False, dry_run=False)

    activation = _activation(summary)
    assert activation["outcome"] == UNSATISFIED
    assert activation["unsatisfied"] == ["merge_commit_present"]
    assert activation["activation"]["merge_commit_present"] == "no"
    assert binder.observed == []
    # A condition of the MACHINE, not a missing answer: somebody has to act, and the check files
    # nothing until they do.
    assert has_conditions([summary])
    assert not has_findings([summary])


def test_a_repository_with_no_python_toolchain_is_still_observable(estate: Estate) -> None:
    """The estate's own live subject is a TypeScript project. Two of the three facts do not apply
    to it, and `not applicable` is a distinct answer from `not met`."""
    head = git(estate.local, "rev-parse", "HEAD").strip()
    binder = FakeBinder([candidate_row(head)])

    summary = bind_checkout(str(estate.local), binder, fetch=False, dry_run=False)

    assert summary["repository_facts"] == {
        "console_entry_points_present": "not_applicable",
        "environment_matches_lock": "not_applicable",
    }
    assert _activation(summary)["outcome"] == OBSERVED


def test_a_dry_run_shows_the_activation_it_would_file_and_writes_nothing(
    estate: Estate,
) -> None:
    head = git(estate.local, "rev-parse", "HEAD").strip()
    binder = FakeBinder([candidate_row(head)])

    summary = bind_checkout(str(estate.local), NullBinder(binder), fetch=False, dry_run=True)

    activation = _activation(summary)
    assert activation["outcome"] == OBSERVED
    assert activation["dry_run"] is True
    assert activation["record"]["kind"] == "machine_local"
    assert binder.bound == []
    assert binder.observed == []


def test_a_refused_activation_makes_the_pass_incomplete(estate: Estate) -> None:
    head = git(estate.local, "rev-parse", "HEAD").strip()
    row = candidate_row(head, binding_id="binding-1")
    row["binding_artifact_digest"] = content_digest(estate.local)
    binder = FakeBinder([row], refuse=True)

    summary = bind_checkout(str(estate.local), binder, fetch=False, dry_run=False)

    assert _activation(summary)["outcome"] == REFUSED
    assert has_findings([summary])


def test_a_unit_still_waiting_is_not_asked_about_activation(estate: Estate) -> None:
    """There is nothing to observe: the machine has not pulled the change at all."""
    upstream = estate.land_upstream()
    binder = FakeBinder([candidate_row(upstream)])

    summary = bind_checkout(str(estate.local), binder, fetch=False, dry_run=False)

    assert summary["units"][0]["outcome"] == WAITING
    assert "activation" not in summary["units"][0]
    assert binder.observed == []


# ---------------------------------------------------------------------------
# The confined surface. A surface stated in a docstring is a wish; a surface stated in a matcher
# is a property.
# ---------------------------------------------------------------------------

BINDING_ID = "0b99daeb-94d9-4210-9673-c43bec3926c2"


def test_the_lane_may_write_exactly_the_binding_and_the_activation_check() -> None:
    assert is_allowed_write(f"/api/v1/work-units/{UNIT_ID}/release-artifacts")
    assert is_allowed_write(f"/api/v1/release-artifacts/{BINDING_ID}/deployment-observations")


def test_the_lane_may_not_write_anywhere_else() -> None:
    """Anchored with the id's shape spelled out, so a prefix, a trailing slash or a sibling verb
    under the same id does not match."""
    forbidden = (
        f"/api/v1/release-artifacts/{BINDING_ID}/deployment-observations/",
        f"/api/v1/release-artifacts/{BINDING_ID}/deployment-observations/1",
        f"/api/v1/release-artifacts/{BINDING_ID}",
        "/api/v1/release-artifacts/not-a-uuid/deployment-observations",
        f"/x/api/v1/release-artifacts/{BINDING_ID}/deployment-observations",
        f"/api/v1/work-units/{UNIT_ID}/commands/ready",
        "/api/v1/observations",
    )
    for path in forbidden:
        assert not is_allowed_write(path), path


def test_two_units_at_one_head_each_get_their_own_activation_check(estate: Estate) -> None:
    """EVERY UNIT OF ONE REPOSITORY SHARES A DIGEST -- six of them in `intent-packages` today.

    A key derived from the digest would have the first unit's observation written and every
    sibling refused as `idempotency_conflict`, because the stored command names a different
    binding. The key is a function of the unit, exactly as the binding's is.
    """
    head = git(estate.local, "rev-parse", "HEAD").strip()
    first = candidate_row(head)
    second = {**candidate_row(head), "work_unit_id": "aaaaaaaa-1111-2222-3333-444444444444"}
    second["unit_key"] = "example-ac-002"
    binder = FakeBinder([first, second])

    summary = bind_checkout(str(estate.local), binder, fetch=False, dry_run=False)

    assert [unit["activation"]["outcome"] for unit in summary["units"]] == [OBSERVED, OBSERVED]
    keys = [payload["idempotency_key"] for _binding, payload in binder.observed]
    assert len(set(keys)) == 2
    assert keys == [
        f"machine-activation-check:{first['work_unit_id']}",
        f"machine-activation-check:{second['work_unit_id']}",
    ]
