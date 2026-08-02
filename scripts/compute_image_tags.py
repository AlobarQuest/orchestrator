"""Commit -> image tag, as a function.

The release workflow used to compose its tag inline in shell, mixing the commit's short sha
with an operator-typed free-text label. That made two builds of one commit produce two
differently-named images with identical content, so a tag could not be derived from a commit
and a rollback target existed only in whoever's memory recorded it.

This module is the function. `derivable_tag` depends on the commit and nothing else, so the
same commit always yields the same tag name; `readable_tag` keeps the human-facing form the
estate already reads, label and all. Both are pushed. The revision is validated as a full
40-character sha here rather than in the workflow, so an abbreviated or malformed sha fails
the build instead of labelling an image with a provenance claim that cannot be resolved.

Note the derivable tag is derivable, not immutable: rebuilding a commit re-points the tag at
a fresh digest, because the base image is a moving tag and the created label is a timestamp.
The digest the workflow reports remains the artifact's identity.
"""

import argparse
import re
from collections.abc import Sequence

IMAGE = "ghcr.io/alobarquest/orchestrator"
SOURCE_URL = "https://github.com/AlobarQuest/orchestrator"
SHORT_SHA_LENGTH = 7

FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
LABEL = re.compile(r"^[a-z0-9-]+$")


def _validated_revision(revision: str) -> str:
    if not FULL_SHA.match(revision):
        raise ValueError(f"revision must be a full 40-character lowercase sha, got {revision!r}")
    return revision


def derivable_tag(revision: str) -> str:
    """The tag that is a pure function of the commit. Carries no operator input."""
    return f"{IMAGE}:sha-{_validated_revision(revision)}"


def readable_tag(revision: str, label: str = "") -> str:
    """The human-facing tag the estate already reads: short sha, optional label, arch."""
    short = _validated_revision(revision)[:SHORT_SHA_LENGTH]
    if label and not LABEL.match(label):
        raise ValueError(f"label must match {LABEL.pattern}, got {label!r}")
    suffix = f"-{label}" if label else ""
    return f"{IMAGE}:{short}{suffix}-amd64"


def tag_outputs(revision: str, label: str = "") -> dict[str, str]:
    """The `key=value` lines the release workflow appends to `$GITHUB_OUTPUT`."""
    return {
        "revision": _validated_revision(revision),
        "source": SOURCE_URL,
        "tag": readable_tag(revision, label),
        "sha_tag": derivable_tag(revision),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Emit release image tags as GITHUB_OUTPUT lines.")
    parser.add_argument("--revision", required=True, help="full 40-character commit sha")
    parser.add_argument("--label", default="", help="optional human-readable tag suffix")
    args = parser.parse_args(argv)
    try:
        outputs = tag_outputs(args.revision, args.label)
    except ValueError as error:
        parser.error(str(error))
    for key, value in outputs.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
