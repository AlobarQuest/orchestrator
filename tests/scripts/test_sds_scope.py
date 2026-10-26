"""The derivation answers whole or not at all, and spells every name as GitHub does.

The readers it composes are tested in their own packages; these tests hold the composition: a
single unreadable declaration or an unknown policy name withholds the whole list, because a
shorter list would let a lane pass clean over what it dropped.
"""

import pytest

from scripts.sds_scope import (
    EXIT_UNAVAILABLE,
    ScopeUnavailable,
    canonical,
    factory_targets,
    ledger_repositories,
    main,
)
from work_carrier.declaration import Declaration

OWNED = ["AlobarQuest/orchestrator", "AlobarQuest/brain", "AlobarQuest/rtk", "AlobarQuest/notes"]


class Declarations:
    def __init__(self, answers: dict[str, bool | None]) -> None:
        self._answers = answers

    def declaration(self, slug: str) -> Declaration:
        return Declaration(self._answers.get(slug, False), "test double")


def test_factory_targets_are_the_repositories_declaring_true():
    source = Declarations({"AlobarQuest/orchestrator": True, "AlobarQuest/brain": True})
    assert factory_targets(OWNED, source) == ["AlobarQuest/brain", "AlobarQuest/orchestrator"]


def test_one_unreadable_declaration_withholds_every_target():
    source = Declarations({"AlobarQuest/orchestrator": True, "AlobarQuest/rtk": None})
    with pytest.raises(ScopeUnavailable, match="AlobarQuest/rtk"):
        factory_targets(OWNED, source)


def test_policy_names_are_respelled_as_github_spells_them():
    assert canonical(["alobarquest/rtk"], OWNED) == ["AlobarQuest/rtk"]


def test_a_policy_name_the_account_does_not_own_withholds_the_list():
    with pytest.raises(ScopeUnavailable, match="alobarquest/elsewhere"):
        canonical(["alobarquest/rtk", "alobarquest/elsewhere"], OWNED)


def test_the_ledger_covers_targets_and_the_inert_population_once_each():
    targets = ["AlobarQuest/orchestrator", "AlobarQuest/brain"]
    inert = ["alobarquest/orchestrator", "alobarquest/rtk"]
    assert ledger_repositories(targets, inert, OWNED) == [
        "AlobarQuest/brain",
        "AlobarQuest/orchestrator",
        "AlobarQuest/rtk",
    ]


def test_a_missing_credential_prints_nothing_and_exits_unavailable(monkeypatch, capsys):
    monkeypatch.delenv("SDS_SCOPE_GITHUB_TOKEN", raising=False)
    assert main(["ledger"]) == EXIT_UNAVAILABLE
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "SDS_SCOPE_GITHUB_TOKEN" in captured.err
