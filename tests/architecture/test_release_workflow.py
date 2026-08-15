"""Architecture guard: the security-standards registry-bundle pin file.

`security-standards.pin.toml` at the repo root is the single source of truth for the
production registry-bundle pin. The release workflow (Task 3) reads `revision` to check
out security-standards and passes both `revision` and `artifact_sha256` as Docker build
args; the Dockerfile's in-build gate fails closed if the assembled bundle's digest does
not match `artifact_sha256`.

This module is extended in Task 3 with assertions over the release workflow itself.
"""

import tomllib
from pathlib import Path

import yaml

from scripts.compute_image_tags import IMAGE

ROOT = Path(__file__).resolve().parents[2]
RELEASE_WORKFLOW = ROOT / ".github/workflows/release-image.yml"


def test_pin_file_is_well_formed():
    pin = tomllib.loads((ROOT / "security-standards.pin.toml").read_text())
    assert set(pin) >= {"revision", "artifact_sha256"}
    assert len(pin["artifact_sha256"]) == 64
    assert all(c in "0123456789abcdef" for c in pin["artifact_sha256"])
    assert len(pin["revision"]) >= 7 and all(c in "0123456789abcdef" for c in pin["revision"])


def _workflow():
    return yaml.safe_load(RELEASE_WORKFLOW.read_text())


def test_workflow_is_dispatch_only_build_and_push_no_deploy():
    wf = _workflow()
    # `on:` parses as the YAML 1.1 boolean key `True` under PyYAML's safe_load; handle both
    # in case that resolver behavior ever changes.
    triggers = wf.get("on", wf.get(True))
    assert set(triggers) == {"workflow_dispatch"}
    # The control-plane guards (test_no_automatic_merge.py, test_ws33_scope_guards.py) exempt
    # this workflow by name, on the assumption that `contents: read` makes it structurally
    # incapable of pushing to the repo / merging / pushing main. Pin that invariant here so a
    # future widen to `contents: write` fails this test, not just the exempted guards.
    assert wf["permissions"] == {"contents": "read", "packages": "write"}
    text = RELEASE_WORKFLOW.read_text()
    tag_source = (ROOT / "scripts/compute_image_tags.py").read_text()
    # Build+push only: pushes to ghcr, but never calls Coolify / deploy. The image reference
    # itself now lives in the tag function, so assert it there -- the workflow only names the
    # function.
    assert IMAGE == "ghcr.io/alobarquest/orchestrator"
    assert "scripts/compute_image_tags.py" in text
    assert "--platform linux/amd64" in text
    assert "SECURITY_STANDARDS_DEPLOY_KEY" in text
    assert "security-standards.pin.toml" in text
    assert "shape_registry_context.py" in text
    for forbidden in ("coolify", "sds.alobar.net", "curl -x post"):
        assert forbidden not in text.lower()
        assert forbidden not in tag_source.lower()
    # No moving tag, in either the workflow or the function that composes the tags.
    for source in (text, tag_source):
        assert ":latest" not in source and ":main-" not in source


def _step(name_fragment: str) -> dict:
    steps = _workflow()["jobs"]["build-and-push"]["steps"]
    matches = [s for s in steps if name_fragment in s.get("name", "")]
    assert len(matches) == 1, f"expected exactly one step named like {name_fragment!r}"
    return matches[0]


def test_tags_are_computed_by_the_tested_function_not_composed_in_shell():
    """`scripts/compute_image_tags.py` is where commit -> tag is asserted to be a function
    (tests/scripts/test_compute_image_tags.py). That guarantee only reaches the artifact if the
    workflow actually calls it, so pin the call rather than the tag string."""
    run = _step("Compute tags")["run"]
    assert "scripts/compute_image_tags.py" in run
    assert "git rev-parse HEAD" in run
    # A short sha cannot resolve a provenance claim, and `github.sha` is the ref the workflow
    # file came from rather than the commit being built.
    assert "--short" not in run
    assert "github.sha" not in run


def test_the_image_asserts_its_own_provenance_at_build_time():
    run = _step("Build and push")["run"]
    for key in (
        "org.opencontainers.image.revision",
        "org.opencontainers.image.source",
        "org.opencontainers.image.created",
    ):
        assert f"--label {key}=" in run, f"the pushed image must assert {key}"
    # The revision label must carry the validated full sha the tag function emitted. Anything
    # else -- a short sha, an unvalidated shell variable -- is a provenance claim that cannot be
    # resolved back to a commit.
    assert 'org.opencontainers.image.revision="${{ steps.tag.outputs.revision }}"' in run


def test_both_the_derivable_and_the_readable_tag_are_pushed():
    """Additive: the derivable tag is what makes a rollback target computable, and the readable
    tag is what the estate already reads. Dropping either is a behaviour change."""
    run = _step("Build and push")["run"]
    assert '-t "${{ steps.tag.outputs.sha_tag }}"' in run
    assert '-t "${{ steps.tag.outputs.tag }}"' in run


def test_the_pushed_image_is_read_back_out_of_the_registry():
    """The build command carrying a label is not evidence the artifact carries it."""
    run = _step("Verify the pushed image asserts its own provenance")["run"]
    assert "docker pull" in run
    assert "org.opencontainers.image.revision" in run
    # Read back via the derivable tag, so resolving that tag to the pushed digest is proven by
    # the same step rather than assumed.
    assert "$SHA_TAG" in run and "$DIGEST" in run
