"""The rotation grant, the `rotated_by_sds` registry rows and the registry files agree both ways.

`check` is pure and takes plain collections; the CLI tests drive the real entry point with every
reader replaced (`_wire`), so no mirror is cloned and nothing reaches GitHub.
"""

from __future__ import annotations

from typer.testing import CliRunner

from membership_check import cli
from membership_check.consistency import Report
from rotation_worker.git_mirror import MirrorError
from rotation_worker.registry import REGISTRY_REPOSITORIES, RegistryEntry, RegistryError
from sds_members.client import Member
from tests.membership_check.test_consistency import _check, _member, _wire

ROW = "a rotated_by_sds registry row names it, but the member record does not grant "
RUNNER = CliRunner()


def test_a_flagged_row_without_a_grant_disagrees() -> None:
    report = _check([_member("A/one")], flagged_repositories=["a/ONE"])
    assert report.disagreements == [f"a/ONE: {ROW}credential_rotation"]


def test_a_grant_without_a_flagged_row_disagrees() -> None:
    report = _check([_member("A/one", "credential_rotation")])
    assert report.disagreements == [
        "A/one: granted credential_rotation, but no rotated_by_sds registry row is there"
    ]


def test_a_matching_flagged_row_and_grant_agree_ignoring_case() -> None:
    report = _check([_member("A/one", "credential_rotation")], flagged_repositories=["a/ONE"])
    assert report == Report([], [])


def test_an_unread_registry_skips_the_reverse_row() -> None:
    report = _check([_member("A/one", "credential_rotation")], registries_read=False)
    assert report == Report([], [])


def test_a_registry_file_outside_the_tuple_disagrees() -> None:
    report = _check(
        [_member("A/one", "credential_rotation")],
        flagged_repositories=["A/one"],
        registry_repositories=["A/one"],
        registry_files=["A/one", "A/stray"],
    )
    assert report.disagreements == [
        "A/stray: holds .cred-consumers.toml, outside rotation_worker REGISTRY_REPOSITORIES"
    ]


def test_a_stored_copy_is_never_compared(monkeypatch) -> None:
    """A flagged entry's destination naming an ungranted repository is not a disagreement: the
    row's own repository is what is compared, never what it stores a copy in."""
    _wire(monkeypatch, members=_members_one(), flagged=("A/one",))
    monkeypatch.setattr(
        cli,
        "rotated_by_sds",
        lambda _mirror: {
            "c0": RegistryEntry(
                "A/one",
                "c" * 40,
                {"consumer": [{"kind": "actions-secret", "repository": "A/ungranted"}]},
            )
        },
    )
    result = _run()
    assert result.exit_code == 0, result.output
    assert "A/ungranted" not in result.output


def test_a_retired_member_s_flagged_row_counts_as_not_granted() -> None:
    report = _check(
        [_member("A/one", "credential_rotation", state="retired")],
        flagged_repositories=["A/one"],
    )
    assert report.disagreements == [f"A/one: {ROW}credential_rotation"]


def test_todays_estate_agrees() -> None:
    members = [_member(repository, "credential_rotation") for repository in REGISTRY_REPOSITORIES]
    report = _check(
        members,
        flagged_repositories=list(REGISTRY_REPOSITORIES),
        registry_repositories=list(REGISTRY_REPOSITORIES),
        registry_files=list(REGISTRY_REPOSITORIES),
    )
    assert report == Report([], [])
    assert {m.repository for m in members} == set(REGISTRY_REPOSITORIES)


# --- the Typer entry point -------------------------------------------------------------------


def _members_one() -> list[Member]:
    return [_member("A/one", "app_changes", "dependency_updates", "credential_rotation")]


def _run():
    return RUNNER.invoke(cli.app, [])


def test_the_cli_reports_a_grant_without_a_flagged_row(monkeypatch) -> None:
    _wire(monkeypatch, members=_members_one(), flagged=())
    result = _run()
    assert result.exit_code == 2
    assert "A/one: granted credential_rotation, but no rotated_by_sds registry row" in result.output


def test_the_cli_agrees_when_a_flagged_row_matches(monkeypatch) -> None:
    _wire(monkeypatch, members=_members_one(), flagged=("A/one",))
    assert _run().exit_code == 0


def test_an_unread_mirror_is_a_gap_and_never_an_empty_population(monkeypatch) -> None:
    for error in (MirrorError("git down"), RegistryError("doesn't parse"), OSError("no git")):
        _wire(monkeypatch, members=_members_one(), mirror_error=error)
        result = _run()
        assert result.exit_code == 3
        assert "credential registries couldn't be read" in result.output
        assert "no rotated_by_sds registry row" not in result.output


def test_the_cli_reports_a_registry_file_outside_the_tuple(monkeypatch) -> None:
    _wire(
        monkeypatch,
        members=_members_one(),
        flagged=("A/one",),
        registry_files={"A/one": True},
    )
    result = _run()
    assert result.exit_code == 2
    assert "A/one: holds .cred-consumers.toml, outside rotation_worker" in result.output


def test_a_failed_file_read_is_a_gap(monkeypatch) -> None:
    _wire(
        monkeypatch,
        members=_members_one(),
        flagged=("A/one",),
        registry_files={"A/one": None},
    )
    result = _run()
    assert result.exit_code == 3
    assert ".cred-consumers.toml unread: A/one" in result.output
    assert "holds .cred-consumers.toml" not in result.output


def test_a_retired_member_is_not_scanned(monkeypatch) -> None:
    retired = _member("A/gone", state="retired")
    _wire(
        monkeypatch,
        members=[*_members_one(), retired],
        flagged=("A/one",),
        registry_files={"A/gone": None},
    )
    assert _run().exit_code == 0
