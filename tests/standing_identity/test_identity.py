"""The shared reader of a standing package's identity (bridge Part 3, Ruling 5)."""

from __future__ import annotations

from pathlib import Path

import pytest

import standing_identity
from bump_proposer import standing
from standing_identity import BumpIdentity, PackageUnreadable, read_identity
from tests.work_watcher.test_alerts import DEPENDENCY, PACKAGE_ID, REPOSITORY, write_package


def test_a_standing_package_answers_its_repository_ecosystem_and_dependency(
    tmp_path: Path,
) -> None:
    write_package(tmp_path)

    assert read_identity(tmp_path, PACKAGE_ID) == BumpIdentity(REPOSITORY, "npm", DEPENDENCY)


@pytest.mark.parametrize("tooling", ["npm", "pip", "uv"])
def test_the_ecosystem_is_the_tooling_the_id_names(tmp_path: Path, tooling: str) -> None:
    package_id = f"infraops-mcp-server-{tooling}-requests"
    write_package(tmp_path, package_id)

    identity = read_identity(tmp_path, package_id)

    assert identity is not None and identity.ecosystem == tooling


def test_a_missing_package_is_unreadable_not_absent(tmp_path: Path) -> None:
    with pytest.raises(PackageUnreadable):
        read_identity(tmp_path, PACKAGE_ID)


def test_an_id_that_leaves_the_packages_directory_is_not_read(tmp_path: Path) -> None:
    assert read_identity(tmp_path, "../x") is None


def test_the_tooling_names_are_the_ones_the_proposer_creates_packages_for() -> None:
    assert set(standing.TOOLING.values()) == set(standing_identity.TOOLING_NAMES)
