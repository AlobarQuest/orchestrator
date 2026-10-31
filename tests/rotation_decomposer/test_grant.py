"""A decomposition is proposed only for a credential whose registry repository holds the grant."""

from __future__ import annotations

from rotation_decomposer.cli import FOUND, INCOMPLETE, exit_code
from tests.rotation_decomposer.test_pass import RECORD, FakeOrchestrator, _member, _run

REGISTRY = "AlobarQuest/example-registry"


def test_an_ungranted_credential_is_refused_and_not_proposed(orch: FakeOrchestrator) -> None:
    orch.members_rows = [_member(REGISTRY, grant="withheld"), _member("AlobarQuest/other")]
    outcomes = _run(orch, [RECORD])
    assert [o.result for o in outcomes] == ["refused"]
    assert REGISTRY in outcomes[0].detail
    assert orch.proposed == []
    assert exit_code(outcomes) == FOUND


def test_a_repository_absent_from_the_record_is_refused(orch: FakeOrchestrator) -> None:
    orch.members_rows = [_member("AlobarQuest/other")]
    assert [o.result for o in _run(orch, [RECORD])] == ["refused"]
    assert orch.proposed == []


def test_a_retired_member_s_grant_does_not_count(orch: FakeOrchestrator) -> None:
    orch.members_rows = [_member(REGISTRY, state="retired")]
    assert [o.result for o in _run(orch, [RECORD])] == ["refused"]


def test_a_granted_credential_is_proposed(orch: FakeOrchestrator) -> None:
    orch.members_rows = [_member(REGISTRY.upper())]  # case-folded
    assert [o.result for o in _run(orch, [RECORD])] == ["proposed"]
    assert len(orch.proposed) == 1


def test_an_unread_record_is_unread(orch: FakeOrchestrator) -> None:
    orch.members_rows = None
    outcomes = _run(orch, [RECORD])
    assert [o.result for o in outcomes] == ["unread"]
    assert orch.proposed == []
    assert exit_code(outcomes) == INCOMPLETE


def test_an_empty_or_malformed_record_is_unread_not_a_withdrawal(orch: FakeOrchestrator) -> None:
    for rows in ([], [{"repository": REGISTRY}]):
        orch.members_rows = rows
        outcomes = _run(orch, [RECORD])
        assert [o.result for o in outcomes] == ["unread"]
    assert orch.proposed == []


def test_no_candidate_reads_no_members(orch: FakeOrchestrator) -> None:
    orch.works[139] = {"revision_ids": [], "units": []}
    _run(orch, [RECORD])
    orch.states = ["proposed"]
    orch.works[139] = {"revision_ids": ["0c7a484c-1111-4111-8111-111111111111"], "units": []}
    _run(orch, [RECORD])
    assert orch.member_reads == 0


def test_the_record_is_read_once_per_pass(orch: FakeOrchestrator) -> None:
    second = {**RECORD, "id": 140}
    orch.works[140] = orch.works[139]
    _run(orch, [RECORD, second])
    assert orch.member_reads == 1


def test_a_junk_row_is_unread_not_refused(orch: FakeOrchestrator) -> None:
    orch.members_rows = [_member(REGISTRY), "junk"]
    assert [o.result for o in _run(orch, [RECORD])] == ["unread"]
    assert orch.proposed == []
