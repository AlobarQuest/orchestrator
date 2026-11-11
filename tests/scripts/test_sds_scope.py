"""`sds_scope` prints a lane's repositories from the member record, or exits 3 saying why.

Unreadable, unset and empty all exit 3 and print nothing on stdout, so a launcher never runs a
pass over a list it could not trust.
"""

import pytest

import scripts.sds_scope as sds_scope
from scripts.sds_scope import EXIT_INCOMPLETE, EXIT_OK, main
from sds_members.client import SERVICES, App, Member, MembersUnreadable


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


def _with_apps(repository, *names, state="member"):
    apps = tuple(App(name, f"https://{name}.example/health", "main") for name in names)
    return Member(repository, "devon", state, None, False, apps, dict.fromkeys(SERVICES, "granted"))


def test_apps_prints_each_app_repository_once_in_the_records_order(monkeypatch, capsys):
    _serve(
        monkeypatch,
        [
            _with_apps("A/brains", "app-brain", "infra-brain"),
            _with_apps("A/none"),
            _with_apps("A/one", "one"),
            _with_apps("A/gone", "gone", state="retired"),
        ],
    )
    assert main(["apps"]) == EXIT_OK
    assert capsys.readouterr().out == "A/brains\nA/one\n"


def test_apps_is_the_repositories_the_revision_watchers_subjects_name(monkeypatch, capsys):
    """The lane's token is minted for these, so they must be exactly the repositories the watcher
    asks GitHub about: one read of the record, one population."""
    from revision_watcher import subjects

    members = [
        _with_apps("A/brains", "app-brain", "infra-brain"),
        _with_apps("A/one", "one"),
        _with_apps("A/gone", "gone", state="retired"),
    ]
    _serve(monkeypatch, members)
    monkeypatch.setattr(subjects, "read_members", lambda **_: members)
    assert main(["apps"]) == EXIT_OK
    printed = capsys.readouterr().out.splitlines()
    loaded = subjects.load_subjects(token="test", credential_key_id="orchestrator-observer")
    assert printed == list(dict.fromkeys(subject.repository for subject in loaded))


def test_the_apps_repositories_pass_the_mints_request_checks(monkeypatch, capsys):
    """Each once and one owner, so one mint; `contents` with the `metadata` every token holds."""
    from estate_clients.github_app_mint import _check_request

    _serve(
        monkeypatch,
        [_with_apps("A/brains", "app-brain", "infra-brain"), _with_apps("A/one", "one")],
    )
    assert main(["apps"]) == EXIT_OK
    names = capsys.readouterr().out.splitlines()
    permissions = {"metadata": "read", "contents": "read"}
    assert sorted(_check_request("5684758", names, permissions).values()) == sorted(names)


def test_apps_with_no_live_app_exits_3(monkeypatch, capsys):
    _serve(monkeypatch, [_with_apps("A/none"), _with_apps("A/gone", "gone", state="retired")])
    assert main(["apps"]) == EXIT_INCOMPLETE
    captured = capsys.readouterr()
    assert captured.out == "" and "apps population is empty" in captured.err


def test_apps_names_a_repository_once_whatever_its_case(monkeypatch, capsys):
    """The mint refuses a repository named twice under casefolding, which would fail every pass."""
    _serve(
        monkeypatch, [_with_apps("A/Brains", "app-brain"), _with_apps("a/brains", "infra-brain")]
    )
    assert main(["apps"]) == EXIT_OK
    assert capsys.readouterr().out == "A/Brains\n"
