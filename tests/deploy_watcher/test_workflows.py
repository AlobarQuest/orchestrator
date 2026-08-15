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


def test_exactly_two_revisions_confirm_the_deployed_build() -> None:
    """A literal assertion, not a derived one.

    Of both repositories' entire history, two workflow revisions make a green run mean that the
    merged build is the one production is serving: `change-manager`'s `191ec5a` (2026-08-07) and
    `brain`'s `1d9e7d3` (2026-08-14), which is the same improvement made a week later. Deriving
    this from the registry would let the registry change it silently — and a REVISION level is
    the difference between a record that can be landed unattended and one that cannot.
    """
    confirming = sorted(r for r, a in REGISTRY.items() if a.level == ATTESTS_REVISION)
    assert confirming == [
        "a47d4b187c93971a5b5915ce87a963bd4ef35e30",
        "c5c088719cd340f0071b875c6a82439292ed8756",
    ]
    assert len(REGISTRY) == 7


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


def test_every_brain_revision_before_the_revision_poll_is_unverified() -> None:
    """Measured, and it is why the middle rung was removed.

    brain's first three revisions poll each domain's `/api/health` for any 2xx 30 seconds after
    the webhook, while Coolify's swap is a rolling update taking 43-73 seconds — so the container
    that answered is the one that was already running. All three are `rollout_unverified`, and
    that fact does not change now that a fourth revision does better: a record approved against
    those bytes still means what they said.
    """
    brain = [a for a in REGISTRY.values() if a.rollout_job == "deploy"]
    assert len(brain) == 4
    unverified = sorted(a.revision for a in brain if a.level == ATTESTS_UNVERIFIED)
    assert unverified == [
        "6cad4cf9f03d816ce8bf8fb87fa67d8634486ef1",
        "ab3735c4cd9ebf986bf61d3b5f241d4202d36833",
        "cf01da8ddb72879dfa008477a25dfc600d567baf",
    ]


def test_brains_revision_poll_transcribes_what_the_bytes_permit_not_the_configuration() -> None:
    """The one judgment this transcription makes, pinned so a later edit has to re-make it.

    All four applications are configured today and all four reported the merged revision on this
    workflow's first live run — but the bytes skip an application whose Coolify UUID secret is
    unset, so "all four" is a claim about secrets nobody can read and no pin over bytes can
    resolve. The transcription therefore says "every application it triggered". What the bytes
    genuinely did gain is a refusal of the empty case, which `6cad4cf9` passed.
    """
    attestation = attestation_for("c5c088719cd340f0071b875c6a82439292ed8756")
    assert attestation is not None
    assert attestation.level == ATTESTS_REVISION
    assert "every brain application this rollout triggered" in attestation.attests
    assert "unset is neither triggered nor checked" in attestation.attests
    assert "triggered none fails rather than passing empty" in attestation.attests
    assert "all four" not in attestation.attests
