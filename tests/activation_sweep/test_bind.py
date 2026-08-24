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
    RECORDED,
    REFUSED,
    UNAVAILABLE,
    WAITING,
    Candidate,
    NullBinder,
    bind_checkout,
    binding_payload,
    has_findings,
)
from activation_sweep.binding import BindingError, content_digest, has_activated
from activation_sweep.binding_client import BindingCallError
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
        self.asked: list[str] = []

    def candidates(self, repository: str) -> list[dict[str, Any]]:
        self.asked.append(repository)
        return list(self.rows)

    def bind(self, work_unit_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self.refuse:
            raise BindingCallError("orchestrator rejected POST: 409")
        self.bound.append((work_unit_id, payload))
        return {"id": "binding-1"}


class BrokenBinder:
    def candidates(self, repository: str) -> list[dict[str, Any]]:
        raise BindingCallError("orchestrator answered 401")

    def bind(self, work_unit_id: str, payload: dict[str, Any]) -> dict[str, Any]:
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
