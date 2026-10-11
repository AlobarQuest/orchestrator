"""`rotation-worker`: the credential-rotation executor's commands.

`compare-destinations <credential>` is read-only. It fetches intent-packages and the registry
repositories into the executor's mirrors and prints the registry's destinations, ready to paste
into a package, then compares them with the standing package's, at each repository's `main`.
Exit codes: 0 equal, 1 different (the package needs a revision before approval), 2 unreadable.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from rotation_worker.compare import Comparison, report
from rotation_worker.destinations import DestinationError
from rotation_worker.git_mirror import MirrorError, RepoMirror
from rotation_worker.registry import (
    PACKAGES_REPOSITORY,
    RegistryError,
    find_credential,
    latest_package_destinations,
)

EXIT_EQUAL: Final = 0
EXIT_DIFFERENT: Final = 1
EXIT_UNREADABLE: Final = 2


def default_mirror_root() -> Path:
    cache = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(cache) / "rotation-worker" / "mirrors"


def compare_destinations(credential_id: str, mirror: RepoMirror) -> int:
    try:
        entry = find_credential(mirror, credential_id)
        registry = entry.destinations
        print(f"credential {credential_id}")
        print(f"registry  {entry.repository}@{entry.commit[:12]}, as a package lists them:")
        for destination in registry:
            print(f"  - '{destination}'")
        package = latest_package_destinations(mirror, credential_id)
    except (MirrorError, RegistryError, DestinationError) as error:
        print(f"compare-destinations: {error}", file=sys.stderr)
        return EXIT_UNREADABLE
    except Exception as error:
        # Exit 1 means "different" to the runbook, and Python's uncaught exit is 1 too, so an
        # unforeseen failure is reported here as unreadable rather than as a verdict.
        print(f"compare-destinations: unexpected {type(error).__name__}: {error}", file=sys.stderr)
        return EXIT_UNREADABLE
    comparison = Comparison(tuple(registry), tuple(package.destinations))
    print(f"package   {PACKAGES_REPOSITORY}@{package.commit[:12]} {package.path}")
    for line in report(comparison):
        print(line)
    return EXIT_EQUAL if comparison.equal else EXIT_DIFFERENT


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rotation-worker")
    commands = parser.add_subparsers(dest="command", required=True)
    comparing = commands.add_parser(
        "compare-destinations",
        help="print a credential's registry destinations beside its standing package's",
    )
    comparing.add_argument("credential_id")
    comparing.add_argument("--mirror-root", type=Path, default=None)
    arguments = parser.parse_args(argv)
    mirror = RepoMirror(arguments.mirror_root or default_mirror_root())
    return compare_destinations(arguments.credential_id, mirror)
