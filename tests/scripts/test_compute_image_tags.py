"""The property under test is that the derivable tag is a function of the commit alone.

The workflow-level guard in `tests/architecture/test_release_workflow.py` can only assert that
the workflow calls this module; only these tests can assert what it computes.
"""

import subprocess

import pytest

from scripts.compute_image_tags import (
    IMAGE,
    SHORT_SHA_LENGTH,
    derivable_tag,
    main,
    readable_tag,
    tag_outputs,
)

# The commit production's current image was built from, and the tag it actually carries in the
# registry. Pinning a real (commit, tag) pair keeps `readable_tag` a faithful reimplementation of
# the shell it replaced rather than a plausible-looking rewrite.
PRODUCTION_REVISION = "c755c997fee581a908a25474cebfe6b0795fab64"
PRODUCTION_TAG = f"{IMAGE}:c755c99-wsp218inc5-amd64"


def test_readable_tag_reproduces_a_tag_that_exists_in_the_registry():
    assert readable_tag(PRODUCTION_REVISION, "wsp218inc5") == PRODUCTION_TAG


def test_short_sha_agrees_with_git_but_does_not_track_it():
    """`readable_tag` slices a fixed 7 where the old workflow shelled out to
    `git rev-parse --short`, whose length git chooses from the repo's object count and may grow.
    Fixing it makes the readable tag stable too; the two must still agree on this commit, or
    replacing the shell silently renamed every image."""
    abbreviated = subprocess.run(
        ["git", "rev-parse", "--short", PRODUCTION_REVISION],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    ours = readable_tag(PRODUCTION_REVISION).removeprefix(f"{IMAGE}:").removesuffix("-amd64")
    assert len(ours) == SHORT_SHA_LENGTH
    # Deliberately a prefix check, not equality: if git ever abbreviates to 8, staying at 7 is
    # the intended behaviour, and this test should keep passing rather than demand we drift.
    assert abbreviated.startswith(ours)


def test_derivable_tag_is_a_function_of_the_commit_alone():
    """The defect this increment closes: the label used to reach the tag."""
    assert derivable_tag(PRODUCTION_REVISION) == f"{IMAGE}:sha-{PRODUCTION_REVISION}"
    labelled = tag_outputs(PRODUCTION_REVISION, "wsp218inc7")
    unlabelled = tag_outputs(PRODUCTION_REVISION)
    assert labelled["sha_tag"] == unlabelled["sha_tag"]
    # ... and the readable tag still does vary with the label, which is the point of keeping it.
    assert labelled["tag"] != unlabelled["tag"]


def test_derivable_tag_carries_the_full_sha_not_the_abbreviation():
    """An abbreviated sha in the tag is what made image -> commit a parsing convention."""
    tag = derivable_tag(PRODUCTION_REVISION)
    assert tag.rsplit(":sha-", 1)[1] == PRODUCTION_REVISION
    assert len(tag.rsplit(":sha-", 1)[1]) == 40


@pytest.mark.parametrize(
    "revision",
    ["c755c99", "", "C755C997FEE581A908A25474CEBFE6B0795FAB64", "main", "c755c99" * 6],
)
def test_a_revision_that_is_not_a_full_sha_is_refused(revision):
    """Fail closed: an image must never carry a provenance claim that cannot be resolved."""
    with pytest.raises(ValueError, match="full 40-character"):
        derivable_tag(revision)
    with pytest.raises(ValueError, match="full 40-character"):
        tag_outputs(revision)


@pytest.mark.parametrize("label", ["WSP218", "ws p218", "wsp218/inc7", "wsp218_inc7"])
def test_an_out_of_vocabulary_label_is_refused(label):
    with pytest.raises(ValueError, match="label must match"):
        readable_tag(PRODUCTION_REVISION, label)


def test_revision_output_is_the_full_sha_the_labels_are_built_from():
    outputs = tag_outputs(PRODUCTION_REVISION, "wsp218inc7")
    assert outputs["revision"] == PRODUCTION_REVISION
    assert outputs["source"] == "https://github.com/AlobarQuest/orchestrator"


def test_cli_emits_github_output_lines(capsys):
    assert main(["--revision", PRODUCTION_REVISION, "--label", "wsp218inc7"]) == 0
    emitted = dict(line.split("=", 1) for line in capsys.readouterr().out.splitlines())
    assert emitted == tag_outputs(PRODUCTION_REVISION, "wsp218inc7")


def test_cli_exits_non_zero_on_a_short_sha(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--revision", "c755c99"])
    assert exit_info.value.code != 0
    assert "full 40-character" in capsys.readouterr().err
