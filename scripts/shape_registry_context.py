"""Shape a security-standards checkout into the `registry` build-context.

Produces the {SOURCE_REVISION, agents/, src/, schema/} artifact the Dockerfile's
`--build-context registry=` expects, via `git archive` at a pinned revision (never a raw copy,
which would let untracked files poison the digest). Used by both the local build flow and the
release workflow so the two produce byte-identical bundles.
"""

import argparse
import io
import subprocess
import tarfile
from pathlib import Path

# security-standards source pathspec -> destination under the shaped output.
_MAP = {
    "registry/agents": "agents",
    "src/agent_registry": "src/agent_registry",
    "src/factory_events": "src/factory_events",
    "schema": "schema",
}


def shape_registry_context(source: Path, revision: str, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for pathspec, dest in _MAP.items():
        archive = subprocess.run(
            ["git", "-C", str(source), "archive", revision, pathspec],
            check=True,
            capture_output=True,
        ).stdout
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            _extract_remapped(tar, pathspec, dest, output)
    # Trailing newline is part of the artifact's digest contract: it matches the fixture
    # convention and the production build_registry_bundle.py digest, which hashes these bytes
    # verbatim.
    (output / "SOURCE_REVISION").write_text(f"{revision}\n")


def _extract_remapped(tar: tarfile.TarFile, pathspec: str, dest: str, output: Path) -> None:
    # `git archive <rev> registry/agents` yields members under `registry/agents/...`; strip the
    # pathspec prefix and re-root under `dest`.
    for member in tar.getmembers():
        if not member.isfile():
            continue
        rel = Path(member.name).relative_to(pathspec)
        out_path = output / dest / rel
        out_path.parent.mkdir(parents=True, exist_ok=True)
        extracted = tar.extractfile(member)
        if extracted is None:
            continue
        out_path.write_bytes(extracted.read())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    shape_registry_context(args.source, args.revision, args.output)


if __name__ == "__main__":
    main()
