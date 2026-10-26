"""Architecture guard: every tracked file belongs to exactly one layer.

`layers.toml` maps path patterns to the layers the orchestrator is worked on one at a time (the
SDS layer map's twelve units of ownership, plus tooling and config, and project records). A layer's
file list is only useful if it is complete, so this guard holds the manifest to three rules: no
file is in no layer, no file is in two, and no pattern names a path that no longer exists.

When a test here fails, place the file in `layers.toml`; never widen a pattern until the failure
goes away. A placement the rules in the manifest's header do not settle is a decision to record in
the pull request.
"""

import pytest

from scripts.layers import Assignment, Layer, assign, load, tracked_files, unused_patterns


@pytest.fixture(scope="module")
def files() -> list[str]:
    return tracked_files()


@pytest.fixture(scope="module")
def layers() -> dict[str, Layer]:
    return load()


@pytest.fixture(scope="module")
def assignment(files: list[str], layers: dict[str, Layer]) -> Assignment:
    return assign(files, layers)


def test_every_file_belongs_to_a_layer(assignment: Assignment):
    assert assignment.unassigned == (), "place each file in layers.toml"


def test_no_file_belongs_to_two_layers(assignment: Assignment):
    assert dict(assignment.overlapping) == {}, "narrow the patterns so one layer claims each file"


def test_every_pattern_names_a_file(files: list[str], layers: dict[str, Layer]):
    assert unused_patterns(files, layers) == [], "drop or correct the stale pattern"
