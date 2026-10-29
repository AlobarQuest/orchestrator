"""The record is compared with every list that restates it; each rule fires in one direction each.

`check` is pure, so the rules are tested on plain collections. The CLI test drives the real Typer
entry point with every reader replaced, so nothing reaches GitHub, change-manager or the network.
"""

from __future__ import annotations

from typing import TypedDict, Unpack, cast

import pytest
from typer.testing import CliRunner

from bump_proposer.landing_policy import LandingPolicy, LandingPolicyError
from fork_sync.watch import Unreadable
from membership_check import cli
from membership_check.auto_merge import AutoMergeState
from membership_check.consistency import Report, Scope, canonical, check, factory_targets
from membership_check.declaration import Declaration
from pin_watcher.github import PinWatcherError
from sds_members.client import SERVICES, Member

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


class AutoMerge:
    """Every repository reads clean unless named."""

    def __init__(self, states: dict[str, AutoMergeState]) -> None:
        self._states = states

    def state(self, repository: str) -> AutoMergeState:
        return self._states.get(repository, AutoMergeState(False, ()))

    def __enter__(self) -> AutoMerge:
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
    assert scope.unreadable == ["AlobarQuest/rtk"]
    assert len(scope.gaps) == 1 and "AlobarQuest/rtk" in scope.gaps[0]


def test_policy_names_are_respelled_as_github_spells_them():
    assert canonical(["alobarquest/rtk"], OWNED) == Scope(["AlobarQuest/rtk"])


def test_a_policy_name_that_is_not_an_active_repository_is_a_gap():
    scope = canonical(["alobarquest/rtk", "alobarquest/archived-fork"], OWNED)
    assert scope.names == ["AlobarQuest/rtk"]
    assert len(scope.gaps) == 1 and "alobarquest/archived-fork" in scope.gaps[0]


def _member(repository: str, *granted: str, state: str = "member") -> Member:
    return Member(
        repository=repository,
        owner="devon",
        state=state,
        working_copy=None,
        machine_local=False,
        apps=(),
        grants={s: "granted" if s in granted else "not_granted" for s in SERVICES},
    )


class _Sources(TypedDict, total=False):
    declarations: Scope
    inert: frozenset[str]
    deploying: frozenset[str]
    rollout_repositories: list[str]
    tool_repositories: list[str]
    hardened_forks: list[str]
    synced_forks: list[str]
    registry_repositories: list[str]
    rollbacks: list[str]
    standing_update_repositories: list[str]
    auto_merge: dict[str, AutoMergeState]


QUIET = AutoMergeState(allowed=False, arming_workflows=())


def _check(members: list[Member], **sources: Unpack[_Sources]) -> Report:
    empty: _Sources = {
        "declarations": Scope([]),
        "inert": frozenset(),
        "deploying": frozenset(),
        "rollout_repositories": [],
        "tool_repositories": [],
        "hardened_forks": [],
        "synced_forks": [],
        "registry_repositories": [],
        "rollbacks": [],
        "standing_update_repositories": [],
        "auto_merge": {m.repository: QUIET for m in members},
    }
    return check(members, **{**empty, **sources})


def test_an_agreeing_estate_is_clean():
    members = [
        _member(
            "A/one",
            "app_changes",
            "dependency_updates",
            "security_patches",
            "rollouts",
            "credential_rotation",
        )
    ]
    report = _check(
        members,
        declarations=Scope(["a/ONE"]),
        inert=frozenset({"a/one"}),
        deploying=frozenset({"A/one"}),
        rollout_repositories=["a/one"],
        tool_repositories=["A/ONE"],
        hardened_forks=["A/one"],
        synced_forks=["A/one"],
        registry_repositories=["A/one"],
        rollbacks=["a/one"],
        standing_update_repositories=["A/ONE"],
    )
    assert report == Report([], [])


# (rule source argument, the service it needs)
RULES = [
    ("declarations", "app_changes"),
    ("inert", "dependency_updates"),
    ("deploying", "dependency_updates"),
    ("rollout_repositories", "rollouts"),
    ("tool_repositories", "rollouts"),
    ("hardened_forks", "security_patches"),
    ("synced_forks", "dependency_updates"),
    ("synced_forks", "security_patches"),
    ("registry_repositories", "credential_rotation"),
    ("rollbacks", "rollouts"),
    ("standing_update_repositories", "dependency_updates"),
]


def _source(argument: str, repository: str) -> _Sources:
    """One source, named by `argument`, naming `repository`."""
    if argument == "declarations":
        return {"declarations": Scope([repository])}
    if argument in ("inert", "deploying"):
        return cast(_Sources, {argument: frozenset({repository.lower()})})
    return cast(_Sources, {argument: [repository]})


@pytest.mark.parametrize(("argument", "service"), RULES)
def test_a_source_naming_a_repository_without_the_grant_is_a_disagreement(argument, service):
    report = _check([_member("A/one")], **_source(argument, "A/one"))
    assert any("a/one" in line.lower() and service in line for line in report.disagreements)


@pytest.mark.parametrize(("argument", "service"), RULES)
def test_a_source_naming_a_repository_with_the_grant_agrees(argument, service):
    other = "dependency_updates" if service == "security_patches" else "security_patches"
    members = [_member("A/one", service, other)]
    assert _check(members, **_source(argument, "A/one")).disagreements == []


def test_a_granted_app_changes_member_must_declare_a_target():
    report = _check([_member("A/one", "app_changes")], declarations=Scope(["A/two"]))
    [line] = [d for d in report.disagreements if d.startswith("A/one")]
    assert "factory_target = true" in line


def test_a_retired_member_counts_as_not_granted():
    members = [_member("A/one", "rollouts", "app_changes", state="retired")]
    report = _check(members, rollout_repositories=["A/one"], declarations=Scope(["A/one"]))
    assert len(report.disagreements) == 2


def test_an_unreadable_declaration_is_a_gap():
    members = [_member("A/one", "app_changes")]
    declarations = Scope([], ["declaration unreadable: A/one"], ["A/one"])
    report = _check(members, declarations=declarations)
    assert report.disagreements == []
    assert report.gaps == ["declaration unreadable: A/one"]


def test_a_member_allowing_auto_merge_is_a_disagreement():
    state = AutoMergeState(allowed=True, arming_workflows=())
    report = _check([_member("A/one")], auto_merge={"a/one": state})
    [line] = report.disagreements
    assert line.startswith("A/one: ") and "allow_auto_merge" in line
    assert report.gaps == []


def test_a_member_with_an_arming_workflow_is_a_disagreement():
    state = AutoMergeState(
        allowed=False, arming_workflows=(".github/workflows/a.yml", ".github/workflows/b.yml")
    )
    report = _check([_member("A/one")], auto_merge={"A/one": state})
    assert len(report.disagreements) == 2
    assert all(line.startswith("A/one: ") for line in report.disagreements)
    assert ".github/workflows/a.yml" in report.disagreements[0]
    assert ".github/workflows/b.yml" in report.disagreements[1]


@pytest.mark.parametrize(
    "state",
    [
        AutoMergeState(allowed=None, arming_workflows=()),
        AutoMergeState(allowed=False, arming_workflows=None),
        AutoMergeState(allowed=None, arming_workflows=None, detail="answered 503"),
    ],
)
def test_an_unreadable_setting_is_a_gap_not_a_pass(state):
    report = _check([_member("A/one")], auto_merge={"A/one": state})
    assert report.disagreements == []
    [gap] = report.gaps
    assert "A/one" in gap and state.detail in gap


def test_a_member_missing_from_the_auto_merge_read_is_a_gap_not_a_pass():
    report = _check([_member("A/one"), _member("A/two")], auto_merge={"A/one": QUIET})
    assert report.disagreements == []
    [gap] = report.gaps
    assert "A/two" in gap


def test_a_retired_member_is_still_read_for_auto_merge():
    state = AutoMergeState(allowed=True, arming_workflows=())
    report = _check([_member("A/one", state="retired")], auto_merge={"A/one": state})
    assert len(report.disagreements) == 1


def test_a_member_with_auto_merge_off_and_no_arming_workflow_agrees():
    report = _check([_member("A/one")], auto_merge={"A/one": QUIET})
    assert report == Report([], [])


def test_fork_sync_needs_both_grants():
    report = _check([_member("A/fork", "dependency_updates")], synced_forks=["A/fork"])
    [line] = report.disagreements
    assert "security_patches" in line


# --- the Typer entry point -------------------------------------------------------------------


def _wire(
    monkeypatch,
    *,
    members,
    answers=None,
    policy=("a/one",),
    deploying=("a/one",),
    policy_error=None,
    fork_list=(),
    fork_error=None,
    listing_error=None,
    rollbacks=None,
    auto_merge=None,
):
    for name in (
        "SDS_MEMBERS_OBSERVER_TOKEN",
        "SDS_MEMBERS_GITHUB_TOKEN",
        "SDS_MEMBERS_CHANGE_MANAGER_TOKEN",
    ):
        monkeypatch.setenv(name, "test")
    monkeypatch.setattr(cli, "read_members", lambda **_: members)

    class Reader:
        def __init__(self, token):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    def listing(_reader):
        if listing_error:
            raise listing_error
        return ["A/one"]

    monkeypatch.setattr(cli, "GitHubReader", Reader)
    monkeypatch.setattr(cli, "repositories", listing)
    monkeypatch.setattr(
        cli, "GitHubDeclarationSource", lambda token: Declarations(answers or {"A/one": True})
    )

    def read_policy(_token):
        if policy_error:
            raise policy_error
        return LandingPolicy(
            version=12,
            inert=frozenset(policy),
            permitted_authors=frozenset(),
            inert_excluded_ecosystems=frozenset(),
            deploying=frozenset(deploying),
            deploying_excluded_ecosystems=frozenset(),
        )

    monkeypatch.setattr(cli, "read_landing_policy", read_policy)
    monkeypatch.setattr(cli, "GitHubAutoMergeSource", lambda token: AutoMerge(auto_merge or {}))

    def read_forks(_run):
        if fork_error:
            raise fork_error
        return list(fork_list)

    monkeypatch.setattr(cli, "forks", read_forks)
    monkeypatch.setattr(cli, "ROLLOUT_WORKFLOWS", {})
    monkeypatch.setattr(cli, "TOOLS", ())
    monkeypatch.setattr(cli, "FORKS", ())
    monkeypatch.setattr(cli, "REGISTRY_REPOSITORIES", ())
    monkeypatch.setattr(cli, "ROLLBACKS", rollbacks or {})


CLEAN = [_member("A/one", "app_changes", "dependency_updates")]


@pytest.fixture(autouse=True)
def packages_checkout(tmp_path, monkeypatch):
    """An empty, readable packages checkout; never the real one."""
    root = tmp_path / "intent-packages"
    (root / "packages").mkdir(parents=True)
    monkeypatch.setenv("BUMP_PROPOSER_PACKAGES_CHECKOUT", str(root))
    return root


def _standing_tree(root, *targets):
    for number, target in enumerate(targets):
        package = root / "packages" / f"pkg-{number}"
        package.mkdir(parents=True)
        (package / "package.yaml").write_text(
            f"profile: dependency-update\nstanding: true\ntarget_repo: {target}\n"
            f"package: dep{number}\nrevision: 1\n",
            encoding="utf-8",
        )
    return root


def _invoke():
    return CliRunner().invoke(cli.app, [])


def test_check_exits_0_when_the_estate_agrees(monkeypatch):
    _wire(monkeypatch, members=CLEAN)
    assert _invoke().exit_code == 0


def test_check_exits_2_on_a_disagreement_and_3_on_a_gap_only(monkeypatch):
    _wire(monkeypatch, members=[_member("A/one", "app_changes")])
    result = _invoke()
    assert result.exit_code == 2
    assert "A/one" in result.stdout and "dependency_updates" in result.stdout

    _wire(monkeypatch, members=CLEAN, policy_error=LandingPolicyError("503"))
    result = _invoke()
    assert result.exit_code == 3
    assert "landing policy couldn't be read" in result.stderr and result.stdout == ""


def test_a_disagreement_outranks_a_gap(monkeypatch):
    _wire(
        monkeypatch,
        members=[_member("A/one", "app_changes")],
        fork_error=Unreadable("no registry"),
    )
    assert _invoke().exit_code == 2


def test_an_unreadable_fork_list_is_a_gap(monkeypatch):
    _wire(monkeypatch, members=CLEAN, fork_error=Unreadable("no registry"))
    result = _invoke()
    assert result.exit_code == 3 and "fork-sync fork list" in result.stderr


def test_an_unlisted_account_is_a_gap_not_a_disagreement(monkeypatch):
    _wire(monkeypatch, members=CLEAN, listing_error=PinWatcherError("401"))
    result = _invoke()
    assert result.exit_code == 3 and result.stdout == ""


def test_the_cli_reports_a_member_allowing_auto_merge(monkeypatch):
    _wire(monkeypatch, members=CLEAN, auto_merge={"A/one": AutoMergeState(True, ())})
    result = _invoke()
    assert result.exit_code == 2 and "A/one: " in result.stdout
    assert "allow_auto_merge" in result.stdout


def test_the_cli_reads_every_member_and_an_unread_one_is_a_gap(monkeypatch):
    members = [*CLEAN, _member("A/retired", state="retired")]
    _wire(
        monkeypatch,
        members=members,
        auto_merge={"A/retired": AutoMergeState(None, None, "answered 503")},
    )
    result = _invoke()
    assert result.exit_code == 3 and result.stdout == ""
    assert "A/retired" in result.stderr and "answered 503" in result.stderr


def test_an_unreadable_record_exits_3(monkeypatch):
    from sds_members.client import MembersUnreadable

    _wire(monkeypatch, members=CLEAN)

    def unreadable(**_):
        raise MembersUnreadable("answered 503")

    monkeypatch.setattr(cli, "read_members", unreadable)
    result = _invoke()
    assert result.exit_code == 3 and "503" in result.stderr


def test_a_missing_token_is_the_tool_failing(monkeypatch):
    _wire(monkeypatch, members=CLEAN)
    monkeypatch.delenv("SDS_MEMBERS_GITHUB_TOKEN")
    result = _invoke()
    assert result.exit_code == 1 and "SDS_MEMBERS_GITHUB_TOKEN" in result.stderr


def test_every_rules_lines_spell_a_repository_as_github_does(monkeypatch):
    _wire(monkeypatch, members=[_member("A/one", "app_changes")], policy=("a/one",))
    monkeypatch.setattr(cli, "TOOLS", (type("T", (), {"repository": "a/ONE"})(),))
    result = _invoke()
    assert result.exit_code == 2
    assert "a/one" not in result.stdout.replace("A/one", "") and "a/ONE" not in result.stdout
    assert all(line.startswith("A/one") for line in result.stdout.splitlines())


def test_the_inert_lines_fall_back_to_the_policys_spelling_when_the_listing_failed(monkeypatch):
    _wire(
        monkeypatch,
        members=[_member("A/one")],
        policy=("a/one",),
        listing_error=PinWatcherError("401"),
    )
    result = _invoke()
    assert "a/one: the inert landing population" in result.stdout


def test_membership_check_reports_a_deploying_repository_without_dependency_updates(monkeypatch):
    _wire(monkeypatch, members=[_member("A/one", "app_changes")], policy=(), deploying=("a/one",))
    result = _invoke()
    assert result.exit_code == 2
    assert "A/one: the deploying landing population names it" in result.stdout
    assert "dependency_updates" in result.stdout


def test_an_absent_deploying_population_is_a_gap_not_nothing_deploys(monkeypatch):
    _wire(monkeypatch, members=CLEAN, deploying=())
    result = _invoke()
    assert result.exit_code == 3
    assert "declares no deploying population" in result.stderr and result.stdout == ""


def test_a_rollback_for_a_repository_without_rollouts_is_a_disagreement(monkeypatch):
    _wire(monkeypatch, members=CLEAN, rollbacks={"a/one": object()})
    result = _invoke()
    assert result.exit_code == 2
    assert "A/one: the rollback table names it" in result.stdout and "rollouts" in result.stdout


def test_a_standing_package_for_a_repository_without_dependency_updates_is_a_disagreement(
    monkeypatch, packages_checkout
):
    _standing_tree(packages_checkout, "a/two")
    members = [*CLEAN, _member("A/two", "app_changes")]
    _wire(monkeypatch, members=members, answers={"A/one": True, "A/two": True})
    monkeypatch.setattr(cli, "repositories", lambda _r: ["A/one", "A/two"])
    result = _invoke()
    assert result.exit_code == 2
    assert "A/two: a standing dependency-update package names it" in result.stdout


def test_the_live_population_agrees(monkeypatch, packages_checkout):
    _standing_tree(packages_checkout, "alobarquest/infraops-mcp-server")
    members = [
        # Both deploying repositories are granted dependency_updates (all 13 members are,
        # measured 2026-10-28), so the deploying row agrees.
        _member("AlobarQuest/brain", "rollouts", "dependency_updates"),
        _member("AlobarQuest/change-manager", "rollouts", "dependency_updates"),
        _member("AlobarQuest/infraops-mcp-server", "dependency_updates"),
    ]
    _wire(
        monkeypatch,
        members=members,
        answers={},
        policy=(),
        deploying=("alobarquest/brain", "alobarquest/change-manager"),
        rollbacks={"alobarquest/brain": 1, "alobarquest/change-manager": 1},
    )
    owned = [m.repository for m in members]
    monkeypatch.setattr(cli, "repositories", lambda _r: owned)
    assert _invoke().exit_code == 0


def test_an_unreadable_packages_checkout_is_a_gap_not_an_empty_list(monkeypatch, tmp_path):
    _wire(monkeypatch, members=CLEAN)
    monkeypatch.setenv("BUMP_PROPOSER_PACKAGES_CHECKOUT", str(tmp_path / "missing"))
    result = _invoke()
    assert result.exit_code == 3
    assert "packages checkout couldn't be read" in result.stderr and result.stdout == ""
