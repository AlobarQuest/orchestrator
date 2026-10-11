"""The registry and package readers, and the read-only comparison command over both."""

from __future__ import annotations

import textwrap

import pytest

from rotation_worker import cli
from rotation_worker.compare import Comparison, report
from rotation_worker.git_mirror import RepoMirror
from rotation_worker.registry import (
    PACKAGES_REPOSITORY,
    REGISTRY_PATH,
    REGISTRY_REPOSITORIES,
    RegistryError,
    find_credential,
    latest_package_destinations,
    package_path,
)
from tests.rotation_worker.conftest import Origins

CANARY = textwrap.dedent(
    """
    version = 1

    [[credential]]
    id = "canary"
    class = "rotation-canary"
    provider = "fake"
    rotated_by_sds = true

      [[credential.consumer]]
      kind = "bws-secret"
      uuid = "11111111-2222-3333-4444-555555555555"
      note = "the keeper"
    """
)
REGISTERED = [
    'bws-secret uuid="11111111-2222-3333-4444-555555555555"',
    'provider name="fake"',
]


def _registries(origins: Origins, canary_in: str = REGISTRY_REPOSITORIES[1]) -> None:
    for repository in REGISTRY_REPOSITORIES:
        text = CANARY if repository == canary_in else "version = 1\n"
        origins.commit(repository, {REGISTRY_PATH: text})


def _package(origins: Origins, destinations: list[str] | None, credential: str = "canary") -> str:
    fields = {"standing": True, "credential_id": credential}
    lines = [
        f"  {key}: {str(value).lower() if value is True else value}"
        for key, value in fields.items()
    ]
    if destinations is not None:
        lines.append("  destinations:")
        lines += [f"  - '{destination}'" for destination in destinations]
    text = "profile: non-software-operational\nprofile_fields:\n" + "\n".join(lines) + "\n"
    return origins.commit(PACKAGES_REPOSITORY, {package_path("canary"): text})


def test_the_credential_is_found_in_whichever_registry_holds_it(
    mirror: RepoMirror, origins: Origins
) -> None:
    _registries(origins, canary_in=REGISTRY_REPOSITORIES[2])
    entry = find_credential(mirror, "canary")
    assert entry.repository == REGISTRY_REPOSITORIES[2]
    assert entry.destinations == REGISTERED
    assert entry.rotated_by_sds


def test_an_absent_or_duplicated_credential_is_refused(
    mirror: RepoMirror, origins: Origins
) -> None:
    _registries(origins)
    with pytest.raises(RegistryError, match="0 registry entries"):
        find_credential(mirror, "absent")
    origins.commit(REGISTRY_REPOSITORIES[0], {REGISTRY_PATH: CANARY})
    with pytest.raises(RegistryError, match="2 registry entries"):
        find_credential(mirror, "canary")


def test_a_registry_that_does_not_parse_is_refused(mirror: RepoMirror, origins: Origins) -> None:
    _registries(origins)
    origins.commit(REGISTRY_REPOSITORIES[3], {REGISTRY_PATH: "[[credential]\n"})
    with pytest.raises(RegistryError, match="doesn't parse"):
        find_credential(mirror, "canary")


def test_only_true_hands_the_credential_over(mirror: RepoMirror, origins: Origins) -> None:
    _registries(origins)
    origins.commit(
        REGISTRY_REPOSITORIES[1],
        {REGISTRY_PATH: CANARY.replace("rotated_by_sds = true", "rotated_by_sds = 1")},
    )
    assert not find_credential(mirror, "canary").rotated_by_sds


def test_the_package_list_is_read_at_main(mirror: RepoMirror, origins: Origins) -> None:
    commit = _package(origins, REGISTERED)
    found = latest_package_destinations(mirror, "canary")
    assert (found.commit, found.destinations) == (commit, REGISTERED)
    _package(origins, None)
    with pytest.raises(RegistryError, match="destinations"):
        latest_package_destinations(mirror, "canary")


def test_a_package_for_another_credential_is_refused(mirror: RepoMirror, origins: Origins) -> None:
    origins.commit(
        PACKAGES_REPOSITORY,
        {package_path("canary"): "profile_fields:\n  credential_id: other\n"},
    )
    with pytest.raises(RegistryError, match="credential_id"):
        latest_package_destinations(mirror, "canary")


@pytest.mark.parametrize(
    "text",
    [
        "profile_fields: [1]\n",
        "- a list\n",
        "profile_fields:\n  credential_id: canary\n  destinations: x\n",
        "profile_fields:\n  credential_id: canary\n  destinations: [1]\n",
        "a: [\n",
        "profile_fields:\n  credential_id: canary\n  destinations: []\n",
        "profile_fields:\n  standing: true\n  credential_id: canary\n"
        "  destinations: ['provider  name=\"fake\"']\n",
    ],
)
def test_a_malformed_package_is_refused(mirror: RepoMirror, origins: Origins, text: str) -> None:
    origins.commit(PACKAGES_REPOSITORY, {package_path("canary"): text})
    with pytest.raises(RegistryError):
        latest_package_destinations(mirror, "canary")


def test_the_report_marks_each_destination_by_where_it_appears() -> None:
    comparison = Comparison(("a", "b"), ("b", "c"))
    assert not comparison.equal
    assert comparison.only_in_registry == ("a",)
    assert comparison.only_in_package == ("c",)
    assert report(comparison) == [
        "  registry  a",
        "  both      b",
        "  package   c",
        "differ: 1 only in the registry, 1 only in the package",
    ]


def test_the_same_set_in_another_order_is_not_equal() -> None:
    comparison = Comparison(("a", "b"), ("b", "a"))
    assert not comparison.equal
    assert report(comparison)[-1].startswith("differ: the same destinations")


def test_the_command_exits_0_when_equal_and_1_when_not(
    mirror: RepoMirror, origins: Origins, capsys: pytest.CaptureFixture[str]
) -> None:
    _registries(origins)
    _package(origins, REGISTERED)
    assert cli.compare_destinations("canary", mirror) == cli.EXIT_EQUAL
    assert capsys.readouterr().out.splitlines()[-1].startswith("equal")
    _package(origins, REGISTERED[1:])
    assert cli.compare_destinations("canary", mirror) == cli.EXIT_DIFFERENT
    assert '  registry  bws-secret uuid="11111111' in capsys.readouterr().out


def test_the_command_exits_2_when_it_cannot_read(
    mirror: RepoMirror, origins: Origins, capsys: pytest.CaptureFixture[str]
) -> None:
    _registries(origins)
    assert cli.compare_destinations("canary", mirror) == cli.EXIT_UNREADABLE
    assert "compare-destinations:" in capsys.readouterr().err


def test_the_command_exits_2_on_a_consumer_it_cannot_render(
    mirror: RepoMirror, origins: Origins, capsys: pytest.CaptureFixture[str]
) -> None:
    _registries(origins)
    origins.commit(
        REGISTRY_REPOSITORIES[1],
        {REGISTRY_PATH: CANARY.replace('kind = "bws-secret"', 'kind = "carrier-pigeon"')},
    )
    _package(origins, REGISTERED)
    assert cli.compare_destinations("canary", mirror) == cli.EXIT_UNREADABLE
    assert "carrier-pigeon" in capsys.readouterr().err


@pytest.mark.parametrize("text", ['[credential]\nid = "x"\n', 'credential = ["x"]\n'])
def test_a_registry_whose_credentials_are_not_tables_is_refused(
    mirror: RepoMirror, origins: Origins, text: str
) -> None:
    _registries(origins)
    origins.commit(REGISTRY_REPOSITORIES[3], {REGISTRY_PATH: text})
    with pytest.raises(RegistryError, match="array of tables"):
        find_credential(mirror, "canary")


def test_an_unforeseen_failure_exits_2_not_the_verdict_code(
    monkeypatch: pytest.MonkeyPatch, mirror: RepoMirror, capsys: pytest.CaptureFixture[str]
) -> None:
    def explodes(_mirror: RepoMirror, _credential_id: str) -> None:
        raise AttributeError("a shape nobody foresaw")

    monkeypatch.setattr(cli, "find_credential", explodes)
    assert cli.compare_destinations("canary", mirror) == cli.EXIT_UNREADABLE
    assert "unexpected AttributeError" in capsys.readouterr().err


def test_the_entry_point_parses_its_arguments(
    monkeypatch: pytest.MonkeyPatch, origins: Origins, tmp_path
) -> None:
    _registries(origins)
    _package(origins, REGISTERED)
    monkeypatch.setattr(cli, "RepoMirror", lambda root: RepoMirror(root, origin=origins.url))
    root = tmp_path / "entry"
    assert cli.main(["compare-destinations", "canary", "--mirror-root", str(root)]) == 0
    assert (root / "AlobarQuest__intent-packages.git").is_dir()


def test_the_default_mirror_root_follows_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert cli.default_mirror_root() == tmp_path / "rotation-worker" / "mirrors"
