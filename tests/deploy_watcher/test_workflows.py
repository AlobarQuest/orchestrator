"""The registry is pinned to the bytes it claims to describe.

A transcription that has drifted from its subject is worse than no transcription: it asserts a
guarantee about a workflow that no longer says what it said. So every entry keeps the file it
was written from, and the filename IS the key.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from deploy_watcher.workflows import (
    ATTESTATION_LEVELS,
    ATTESTS_REVISION,
    ATTESTS_UNKNOWN,
    ATTESTS_UNVERIFIED,
    REGISTRY,
    ROLLOUT_WORKFLOWS,
    attestation_for,
    level_of,
    rollout_for,
)

FIXTURES = Path("tests/fixtures/deploy-workflows")


def _blob_sha(content: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()


@pytest.mark.parametrize("revision", sorted(REGISTRY))
def test_each_transcribed_revision_names_the_bytes_it_encodes(revision: str) -> None:
    fixture = FIXTURES / f"{revision}.yml"
    assert fixture.exists(), f"revision {revision} is transcribed with no fixture of the file"
    assert _blob_sha(fixture.read_bytes()) == revision


def test_the_fixtures_and_the_registry_name_the_same_revisions() -> None:
    """Both directions: a fixture nobody transcribed is as wrong as a transcription with none."""
    assert {path.stem for path in FIXTURES.glob("*.yml")} == set(REGISTRY)


@pytest.mark.parametrize("revision", sorted(REGISTRY))
def test_the_named_job_and_step_exist_in_the_bytes(revision: str) -> None:
    """The whole second axis rests on these two names matching the workflow.

    A typo here is silent: `rollout_step` simply finds nothing, `production_reached` reads
    `no` — "nothing was deployed, do not roll back" — and that is the answer with consequences.
    """
    attestation = REGISTRY[revision]
    text = (FIXTURES / f"{revision}.yml").read_text()
    assert f"\n  {attestation.rollout_job}:\n" in text, attestation.rollout_job
    assert f"name: {attestation.trigger_step}\n" in text, attestation.trigger_step


def test_every_level_is_one_of_the_three() -> None:
    assert {a.level for a in REGISTRY.values()} <= ATTESTATION_LEVELS


def test_only_one_revision_confirms_the_deployed_build() -> None:
    """A literal assertion, not a derived one.

    The count is the increment's headline measurement — of both repositories' entire history,
    exactly one workflow revision makes a green run mean what item 44's acceptance criteria say
    it means. Deriving this from the registry would let the registry change it silently.
    """
    confirming = sorted(r for r, a in REGISTRY.items() if a.level == ATTESTS_REVISION)
    assert confirming == ["a47d4b187c93971a5b5915ce87a963bd4ef35e30"]
    assert len(REGISTRY) == 6


def test_an_unclassified_revision_is_unknown_and_never_upgraded() -> None:
    assert attestation_for("f" * 40) is None
    assert level_of("f" * 40) == ATTESTS_UNKNOWN
    assert level_of(None) == ATTESTS_UNKNOWN
    assert level_of("") == ATTESTS_UNKNOWN


def test_an_undeclared_repository_resolves_to_nothing() -> None:
    assert rollout_for("AlobarQuest/orchestrator") is None
    assert rollout_for("AlobarQuest/CHANGE-MANAGER") is not None  # case-folded like the identity


def test_the_two_declared_repositories_are_the_two_that_deploy_on_merge() -> None:
    assert set(ROLLOUT_WORKFLOWS) == {"alobarquest/change-manager", "alobarquest/brain"}
    change_manager = rollout_for("AlobarQuest/change-manager")
    brain = rollout_for("AlobarQuest/brain")
    assert change_manager is not None and brain is not None
    assert change_manager.path == ".github/workflows/deploy.yml"
    assert brain.path == ".github/workflows/ci.yml"


def test_brain_has_no_revision_confirming_revision_at_all() -> None:
    """Measured, and it is why the middle rung was removed.

    brain polls each domain's `/api/health` for any 2xx 30 seconds after the webhook, while
    Coolify's swap is a rolling update taking 43-73 seconds — so the container that answered is
    the one that was already running. Every brain revision is `rollout_unverified`.
    """
    brain = [a for a in REGISTRY.values() if a.rollout_job == "deploy"]
    assert len(brain) == 3
    assert {a.level for a in brain} == {ATTESTS_UNVERIFIED}
