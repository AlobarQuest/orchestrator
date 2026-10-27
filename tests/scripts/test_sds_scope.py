"""`sds_scope` prints a lane's repositories from the member record, or exits 3 saying why.

Unreadable, unset and empty all exit 3 and print nothing on stdout, so a launcher never runs a
pass over a list it could not trust.
"""

import pytest

import scripts.sds_scope as sds_scope
from scripts.sds_scope import EXIT_INCOMPLETE, EXIT_OK, main
from sds_members.client import SERVICES, Member, MembersUnreadable


def _member(repository, state="member", working_copy=None, machine_local=False, **grants):
    return Member(
        repository,
        "devon",
        state,
        working_copy,
        machine_local,
        (),
        {s: grants.get(s, "not_assessed") for s in SERVICES},
    )


def _serve(monkeypatch, members=None, error=None):
    monkeypatch.setenv("SDS_SCOPE_ORCHESTRATOR_TOKEN", "test")
    seen = {}

    def read(**kwargs):
        seen.update(kwargs)
        if error:
            raise error
        return members

    monkeypatch.setattr(sds_scope, "read_members", read)
    return seen


def test_ledger_prints_observed_repositories(monkeypatch, capsys):
    seen = _serve(monkeypatch, [_member("A/one"), _member("A/gone", "retired")])
    assert main(["ledger"]) == EXIT_OK
    assert capsys.readouterr().out == "A/one\n"
    assert seen["token"] == "test" and seen["credential_key_id"] == "orchestrator-observer"


def test_checkouts_prints_working_copies(monkeypatch, capsys):
    _serve(monkeypatch, [_member("A/one", working_copy="/w/one"), _member("A/two")])
    assert main(["checkouts"]) == EXIT_OK
    assert capsys.readouterr().out == "/w/one\n"


def test_bindable_prints_machine_local_with_rollouts(monkeypatch, capsys):
    _serve(
        monkeypatch,
        [
            _member("A/one", working_copy="/w/one", machine_local=True, rollouts="granted"),
            _member("A/two", working_copy="/w/two", machine_local=True),
        ],
    )
    assert main(["bindable"]) == EXIT_OK
    assert capsys.readouterr().out == "/w/one\n"


def test_granted_prints_repositories_holding_the_service(monkeypatch, capsys):
    _serve(monkeypatch, [_member("A/one", backups="granted"), _member("A/two")])
    assert main(["granted", "backups"]) == EXIT_OK
    assert capsys.readouterr().out == "A/one\n"


def test_unreadable_record_exits_3_and_prints_nothing_on_stdout(monkeypatch, capsys):
    _serve(monkeypatch, error=MembersUnreadable("answered 503"))
    assert main(["ledger"]) == EXIT_INCOMPLETE
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "sds-scope: answered 503\n"


def test_empty_population_exits_3(monkeypatch, capsys):
    _serve(monkeypatch, [_member("A/gone", "retired")])
    assert main(["ledger"]) == EXIT_INCOMPLETE
    captured = capsys.readouterr()
    assert captured.out == "" and "empty" in captured.err


def test_unset_token_exits_3(monkeypatch, capsys):
    monkeypatch.delenv("SDS_SCOPE_ORCHESTRATOR_TOKEN", raising=False)
    assert main(["ledger"]) == EXIT_INCOMPLETE
    captured = capsys.readouterr()
    assert captured.out == "" and "SDS_SCOPE_ORCHESTRATOR_TOKEN" in captured.err


@pytest.mark.parametrize("argv", [["granted"], ["granted", "deploys"], ["ledger", "backups"]])
def test_granted_needs_a_known_service(monkeypatch, capsys, argv):
    _serve(monkeypatch, [_member("A/one", backups="granted")])
    assert main(argv) == EXIT_INCOMPLETE
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.startswith("sds-scope: ")


def test_an_unexpected_error_escapes_rather_than_reading_as_incomplete(monkeypatch):
    _serve(monkeypatch, error=ZeroDivisionError())
    with pytest.raises(ZeroDivisionError):
        main(["ledger"])
