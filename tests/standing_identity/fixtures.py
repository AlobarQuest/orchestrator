"""Standing package fixtures shared by the identity and watcher alert tests."""

from __future__ import annotations

from pathlib import Path

PACKAGE_ID = "infraops-mcp-server-npm-types-node"
REPOSITORY = "AlobarQuest/infraops-mcp-server"
DEPENDENCY = "@types/node"

PACKAGE_YAML = """\
schema_version: 1
package_id: {package_id}
revision: 2
status: approved
profile: dependency-update
profile_fields:
  target_repo: {repository}
  package: {dependency}
  from_version: '1.0'
  to_version: '2.0'
  standing: {standing}
"""


def write_package(
    root: Path,
    package_id: str = PACKAGE_ID,
    *,
    repository: str = REPOSITORY,
    dependency: str = DEPENDENCY,
    standing: str = "true",
    profile: str = "dependency-update",
) -> None:
    directory = root / "packages" / package_id
    directory.mkdir(parents=True, exist_ok=True)
    text = PACKAGE_YAML.format(
        package_id=package_id, repository=repository, dependency=dependency, standing=standing
    ).replace("dependency-update", profile)
    (directory / "package.yaml").write_text(text)
