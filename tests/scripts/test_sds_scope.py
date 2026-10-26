"""The derivation names what it can, says what it couldn't, and spells names as GitHub does.

The readers it composes are tested in their own packages; these tests hold the composition. A gap
never shortens the list silently: it is printed to stderr and turns the exit into 3, so the
launcher runs on the known names and still reports the pass as incomplete.
"""

import pytest

import scripts.sds_scope as sds_scope
from bump_proposer.landing_policy import InertLanding, LandingPolicyError
from pin_watcher.github import PinWatcherError
from scripts.sds_scope import (
    EXIT_INCOMPLETE,
    EXIT_OK,
    Scope,
    canonical,
    factory_targets,
    ledger_scope,
    main,
)
from work_carrier.declaration import Declaration

OWNED = ["AlobarQuest/orchestrator", "AlobarQuest/brain", "AlobarQuest/rtk", "AlobarQuest/notes"]


class Declarations:
    def __init__(self, answers: dict[str, bool | None]) -> None:
        self._answers = answers

    def declaration(self, slug: str) -> Declaration:
        return Declaration(self._answers.get(slug, False), "test double")

    def __enter__(self) -> Declarations:
        return self

    def __exit__(self, *_: object) -> None:
        return None


class Reader:
    def __enter__(self) -> Reader:
        return self

    def __exit__(self, *_: object) -> None:
        return None


def test_factory_targets_are_the_repositories_declaring_true():
    source = Declarations({"AlobarQuest/orchestrator": True, "AlobarQuest/brain": True})
    assert factory_targets(OWNED, source) == Scope(
        ["AlobarQuest/brain", "AlobarQuest/orchestrator"]
    )


def test_an_unreadable_declaration_is_a_gap_and_the_rest_are_still_named():
    source = Declarations({"AlobarQuest/orchestrator": True, "AlobarQuest/rtk": None})
    scope = factory_targets(OWNED, source)
    assert scope.names == ["AlobarQuest/orchestrator"]
    assert len(scope.gaps) == 1 and "AlobarQuest/rtk" in scope.gaps[0]


def test_policy_names_are_respelled_as_github_spells_them():
    assert canonical(["alobarquest/rtk"], OWNED) == Scope(["AlobarQuest/rtk"])


def test_a_policy_name_that_is_not_an_active_repository_is_a_gap():
    scope = canonical(["alobarquest/rtk", "alobarquest/archived-fork"], OWNED)
    assert scope.names == ["AlobarQuest/rtk"]
    assert len(scope.gaps) == 1 and "alobarquest/archived-fork" in scope.gaps[0]


def test_the_ledger_covers_targets_and_the_inert_population_once_each():
    targets = Scope(["AlobarQuest/brain", "AlobarQuest/orchestrator"])
    inert = canonical(["alobarquest/orchestrator", "alobarquest/rtk"], OWNED)
    assert ledger_scope(targets, inert) == Scope(
        ["AlobarQuest/brain", "AlobarQuest/orchestrator", "AlobarQuest/rtk"]
    )


def _wire(monkeypatch, *, declarations, policy=None, policy_error=None, listing_error=None):
    monkeypatch.setenv("SDS_SCOPE_GITHUB_TOKEN", "test")
    monkeypatch.setenv("SDS_SCOPE_CHANGE_MANAGER_TOKEN", "test")
    monkeypatch.setattr(sds_scope, "GitHubReader", lambda token: Reader())

    def listing(_reader):
        if listing_error:
            raise listing_error
        return OWNED

    def read_policy(_token):
        if policy_error:
            raise policy_error
        return policy

    monkeypatch.setattr(sds_scope, "repositories", listing)
    monkeypatch.setattr(sds_scope, "GitHubDeclarationSource", lambda token: declarations)
    monkeypatch.setattr(sds_scope, "read_inert_landing", read_policy)


def _policy(*repositories: str) -> InertLanding:
    return InertLanding(12, frozenset(repositories), frozenset(), frozenset())


def test_a_complete_derivation_prints_one_name_per_line_and_exits_ok(monkeypatch, capsys):
    _wire(
        monkeypatch,
        declarations=Declarations({"AlobarQuest/orchestrator": True}),
        policy=_policy("alobarquest/rtk"),
    )
    assert main(["ledger"]) == EXIT_OK
    captured = capsys.readouterr()
    assert captured.out == "AlobarQuest/orchestrator\nAlobarQuest/rtk\n"
    assert captured.err == ""


def test_an_unreadable_policy_still_names_the_targets_and_exits_incomplete(monkeypatch, capsys):
    _wire(
        monkeypatch,
        declarations=Declarations({"AlobarQuest/orchestrator": True}),
        policy_error=LandingPolicyError("change-manager answered 503"),
    )
    assert main(["ledger"]) == EXIT_INCOMPLETE
    captured = capsys.readouterr()
    assert captured.out == "AlobarQuest/orchestrator\n"
    assert "landing policy couldn't be read" in captured.err


def test_an_unlisted_account_prints_nothing_and_exits_incomplete(monkeypatch, capsys):
    _wire(
        monkeypatch,
        declarations=Declarations({}),
        policy=_policy(),
        listing_error=PinWatcherError("github rejected GET /user/repos: 401"),
    )
    assert main(["ledger"]) == EXIT_INCOMPLETE
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "401" in captured.err


def test_a_missing_credential_prints_nothing_and_exits_incomplete(monkeypatch, capsys):
    monkeypatch.delenv("SDS_SCOPE_GITHUB_TOKEN", raising=False)
    assert main(["ledger"]) == EXIT_INCOMPLETE
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "SDS_SCOPE_GITHUB_TOKEN" in captured.err


def test_an_unexpected_error_escapes_rather_than_reading_as_incomplete(monkeypatch):
    _wire(monkeypatch, declarations=Declarations({}), policy=_policy())
    monkeypatch.setattr(sds_scope, "repositories", lambda _reader: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        main(["ledger"])
