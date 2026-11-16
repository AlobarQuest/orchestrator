"""The rotation grant, the `rotated_by_sds` registry rows and the registry files agree both ways.

`check` is pure and takes plain collections; the CLI tests drive the real entry point with every
reader replaced (`_wire`), so no mirror is cloned and nothing reaches GitHub. The tests of a
registry file outside `REGISTRY_REPOSITORIES` put the real GitHub reader back over a mock
transport (`_github`), so the file's bytes go through the code that judges them.
"""

from __future__ import annotations

import httpx
import pytest
from typer.testing import CliRunner

from membership_check import cli
from membership_check.consistency import Report
from membership_check.declaration import GitHubDeclarationSource
from rotation_worker.git_mirror import MirrorError
from rotation_worker.registry import REGISTRY_REPOSITORIES, RegistryEntry, RegistryError
from sds_members.client import Member
from tests.membership_check.test_consistency import _check, _member, _wire

ROW = "a rotated_by_sds registry row names it, but the member record does not grant "
HANDS_OVER = (
    "A/one: its .cred-consumers.toml hands the SDS a credential (rotated_by_sds = true), "
    "outside rotation_worker REGISTRY_REPOSITORIES"
)
# The shape of security-standards' file (0226b199): one row the SDS doesn't rotate.
FALSE_ONLY = b'[[credential]]\nid = "bws-factory-events-nightly-token"\nrotated_by_sds = false\n'
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
        "A/stray: its .cred-consumers.toml hands the SDS a credential (rotated_by_sds = true), "
        "outside rotation_worker REGISTRY_REPOSITORIES"
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
                {
                    "class": "openrouter-key",
                    "consumer": [{"kind": "actions-secret", "repository": "A/ungranted"}],
                },
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
    _wire(monkeypatch, members=_members_one(), flagged=("A/one",), hands_over={"A/one": True})
    result = _run()
    assert result.exit_code == 2
    assert HANDS_OVER in result.output


def test_a_failed_file_read_is_a_gap(monkeypatch) -> None:
    _wire(monkeypatch, members=_members_one(), flagged=("A/one",), hands_over={"A/one": None})
    result = _run()
    assert result.exit_code == 3
    assert ".cred-consumers.toml unread: A/one" in result.output
    assert "outside rotation_worker REGISTRY_REPOSITORIES" not in result.output


def test_a_retired_member_is_not_scanned(monkeypatch) -> None:
    retired = _member("A/gone", state="retired")
    _wire(
        monkeypatch,
        members=[*_members_one(), retired],
        flagged=("A/one",),
        registry_files={"A/gone": None},
        hands_over={"A/gone": None},
    )
    assert _run().exit_code == 0


# --- a registry file outside the tuple, read by the real GitHub reader (D-59 A) ---------------


def _github(monkeypatch, registry: httpx.Response) -> None:
    """The real reader, answering `registry` for `.cred-consumers.toml` and yes elsewhere."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/factory-target.toml"):
            return httpx.Response(200, text='factory_target = true\nfactory_target_reason = "x"\n')
        if path.endswith("/.cred-consumers.toml"):
            return registry
        return httpx.Response(200, json={})

    monkeypatch.setattr(
        cli,
        "GitHubDeclarationSource",
        lambda token: GitHubDeclarationSource(
            token=token, base_url="https://example.invalid", transport=httpx.MockTransport(handler)
        ),
    )


def _outside(monkeypatch, registry: httpx.Response):
    _wire(monkeypatch, members=_members_one(), flagged=("A/one",))
    _github(monkeypatch, registry)
    return _run()


def test_an_outside_file_with_only_false_rows_hands_nothing_over(monkeypatch) -> None:
    result = _outside(monkeypatch, httpx.Response(200, content=FALSE_ONLY))
    assert result.exit_code == 0, result.output
    assert ".cred-consumers.toml" not in result.output


def test_an_outside_file_with_rows_that_omit_the_flag_hands_nothing_over(monkeypatch) -> None:
    """A missing flag is `False`, as `RegistryEntry.rotated_by_sds` reads it."""
    for body in (b'[[credential]]\nid = "x"\n', b"", b'note = "no credential table"\n'):
        result = _outside(monkeypatch, httpx.Response(200, content=body))
        assert result.exit_code == 0, (body, result.output)


def test_an_outside_file_with_a_true_row_disagrees(monkeypatch) -> None:
    body = FALSE_ONLY + b'[[credential]]\nid = "handed"\nrotated_by_sds = true\n'
    result = _outside(monkeypatch, httpx.Response(200, content=body))
    assert result.exit_code == 2, result.output
    assert HANDS_OVER in result.output


def test_a_true_row_beside_a_malformed_one_still_disagrees(monkeypatch) -> None:
    """A row that does hand a credential over is the finding, whatever its neighbour says."""
    body = b'[[credential]]\nrotated_by_sds = "yes"\n[[credential]]\nrotated_by_sds = true\n'
    result = _outside(monkeypatch, httpx.Response(200, content=body))
    assert result.exit_code == 2, result.output
    assert HANDS_OVER in result.output


@pytest.mark.parametrize(
    "registry",
    [
        httpx.Response(200, content=b"[[credential]\nid = 'SECRET-MARKER'\n"),
        httpx.Response(200, content=b"credential = 'SECRET-MARKER'\n"),
        httpx.Response(200, content=b"credential = ['SECRET-MARKER']\n"),
        httpx.Response(200, content=b"[credential]\nid = 'SECRET-MARKER'\n"),
        httpx.Response(200, content=b"[[credential]]\nid = '\xffSECRET-MARKER'\n"),
        httpx.Response(200, content=b"[[credential]]\nrotated_by_sds = 'true'\n"),
        httpx.Response(200, content=b"[[credential]]\nrotated_by_sds = 1\n"),
        httpx.Response(500, content=b"SECRET-MARKER"),
        httpx.Response(403, content=b"SECRET-MARKER"),
        httpx.Response(301, content=b"SECRET-MARKER"),
    ],
    ids=[
        "toml",
        "string",
        "array-of-strings",
        "table",
        "utf-8",
        "string-true",
        "int",
        "500",
        "403",
        "301",
    ],
)
def test_an_outside_file_that_cant_be_judged_is_a_gap(monkeypatch, registry) -> None:
    """Never a pass and never a disagreement, and nothing the file holds reaches the output."""
    result = _outside(monkeypatch, registry)
    assert result.exit_code == 3, result.output
    assert ".cred-consumers.toml unread: A/one" in result.output
    assert "outside rotation_worker REGISTRY_REPOSITORIES" not in result.output
    assert "SECRET-MARKER" not in result.output


def test_an_absent_outside_file_is_nothing(monkeypatch) -> None:
    result = _outside(monkeypatch, httpx.Response(404))
    assert result.exit_code == 0, result.output
    assert ".cred-consumers.toml" not in result.output


def test_a_file_inside_the_tuple_is_read_for_presence_only(monkeypatch) -> None:
    """Its rows come through the mirror, so a body this reader can't parse is not its finding."""
    inside = REGISTRY_REPOSITORIES[0]
    member = _member(inside, "app_changes", "dependency_updates", "credential_rotation")
    _wire(monkeypatch, members=[member], flagged=(inside,), policy=(inside,), deploying=(inside,))
    monkeypatch.setattr(cli, "REGISTRY_REPOSITORIES", (inside,))  # `_wire` empties it
    _github(monkeypatch, httpx.Response(200, content=b"not toml ["))
    result = _run()
    assert ".cred-consumers.toml" not in result.output


def test_a_typeless_flagged_row_does_not_count(monkeypatch) -> None:
    """The canary's row sets the flag, but it is no type the SDS rotates, so it reaches no grant."""
    _wire(monkeypatch, members=_members_one())
    canary = RegistryEntry("A/one", "c" * 40, {"class": "rotation-canary"})
    monkeypatch.setattr(cli, "rotated_by_sds", lambda _mirror: {"canary": canary})
    result = _run()
    assert result.exit_code == 2
    assert "A/one: granted credential_rotation, but no rotated_by_sds registry row" in result.output
