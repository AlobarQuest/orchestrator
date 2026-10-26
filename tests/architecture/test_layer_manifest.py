"""Architecture guard: every file in the repository belongs to exactly one layer.

`layers.toml` maps path patterns to the layers the orchestrator is worked on one at a time (the
SDS layer map's twelve units of ownership, plus tooling and config, and project records). A layer's
file list is only useful if it is complete, so this guard holds the manifest to three rules: no
file is in no layer, no file is in two, and no pattern names a path that no longer exists.

When a test here fails, place the file in `layers.toml`; never widen a pattern until the failure
goes away. A placement the rules in the manifest's header do not settle is a decision to record in
the pull request.
"""

from scripts.layers import assign, load, repository_files, unused_patterns


def test_every_file_belongs_to_a_layer():
    assignment = assign(repository_files(), load())
    assert assignment.unassigned == (), "place each file in layers.toml"


def test_no_file_belongs_to_two_layers():
    assignment = assign(repository_files(), load())
    assert dict(assignment.overlapping) == {}, "narrow the patterns so one layer claims each file"


def test_every_pattern_names_a_file():
    assert unused_patterns(repository_files(), load()) == [], "drop or correct the stale pattern"
