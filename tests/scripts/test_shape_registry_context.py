import os
import shutil
import subprocess
from pathlib import Path

from scripts.build_registry_bundle import artifact_digest
from scripts.shape_registry_context import shape_registry_context

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "security-standards"
_IGNORE_PYCACHE = shutil.ignore_patterns("__pycache__")


def _raw_repo_from_shaped(shaped: Path, dest: Path) -> str:
    """Reconstruct a RAW security-standards layout (registry/agents, src, schema) from the
    shaped fixture and commit it; return the commit sha."""
    (dest / "registry").mkdir(parents=True)
    shutil.copytree(shaped / "agents", dest / "registry" / "agents")
    shutil.copytree(shaped / "src", dest / "src", ignore=_IGNORE_PYCACHE)
    shutil.copytree(shaped / "schema", dest / "schema")
    subprocess.run(["git", "init", "-q"], cwd=dest, check=True)
    subprocess.run(["git", "add", "-A"], cwd=dest, check=True)
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "commit", "-qm", "raw"], cwd=dest, check=True, env=env)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=dest, check=True, capture_output=True, text=True
    ).stdout.strip()


def _fixture_digest_at_revision(revision: str, tmp_path: Path) -> str:
    """Copy the fixture and rewrite its SOURCE_REVISION to `revision`, so its digest is
    comparable to a freshly-shaped artifact pinned at that same (tmp-repo) revision. The
    fixture's own recorded SOURCE_REVISION is a fixed placeholder sha, not the tmp repo's real
    commit sha, so the two are never digest-identical without this rewrite."""
    copy = tmp_path / "fixture-at-revision"
    shutil.copytree(FIXTURE, copy, ignore=_IGNORE_PYCACHE)
    (copy / "SOURCE_REVISION").write_text(f"{revision}\n")
    return artifact_digest(copy)


def test_shaping_reproduces_the_fixture_bundle(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    sha = _raw_repo_from_shaped(FIXTURE, raw)
    out = tmp_path / "shaped"
    shape_registry_context(raw, sha, out)

    assert (out / "SOURCE_REVISION").read_text().strip() == sha
    # Direct convention lock: SOURCE_REVISION must carry exactly one trailing newline, matching
    # the fixture convention and the production digest -- this is what the earlier
    # write_text(revision) (no newline) bug would have failed.
    assert (out / "SOURCE_REVISION").read_bytes() == f"{sha}\n".encode()
    assert sorted(p.name for p in (out / "agents").iterdir()) == sorted(
        p.name for p in (FIXTURE / "agents").iterdir()
    )
    assert {p.name for p in (out / "src").iterdir()} == {
        p.name for p in (FIXTURE / "src").iterdir()
    }
    assert (out / "schema").is_dir()

    # Non-vacuous parity check: shape a raw checkout, then hash the result with
    # build_registry_bundle's real digest function (the same one the Dockerfile build path
    # trusts). It must reproduce the fixture's digest byte-for-byte once both sides are pinned
    # to the same SOURCE_REVISION -- proving shape_registry_context is the exact inverse of the
    # shaping this fixture already represents, not just a structurally-similar directory.
    assert artifact_digest(out) == _fixture_digest_at_revision(sha, tmp_path)
