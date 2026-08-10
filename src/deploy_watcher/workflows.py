"""What a green rollout run actually attests, keyed by the bytes that produced it.

`landing_ledger/rules.py`'s mechanism, applied to a different artifact and for the same reason.

WHY THIS EXISTS. The obvious design reads a workflow run's conclusion and calls a `success` "the
acceptance criteria held". Measured on 2026-08-10, that is wrong for almost all of the history it
would be applied to. `change-manager`'s job ended at `curl -fsSL "$COOLIFY_DEPLOY_WEBHOOK"` until
2026-08-07 (`191ec5a`): a green run meant *the webhook returned 2xx*. Three of its thirty-four
merge-caused runs carry the stronger meaning; thirty-one do not.

THERE ARE TWO RUNGS, NOT THREE, AND THE MISSING ONE IS THE INTERESTING RESULT. A first draft of
this registry had `liveness_confirmed` between "the webhook answered" and "the merged build is
serving", for `brain`, which polls each domain's `/api/health` for any 2xx. Adversarial review
measured it away. Coolify's swap is a ROLLING update -- the old container serves the public
domain until the new one is healthy -- and `brain-code`'s three retained deployments took 43, 59
and 73 seconds. `brain`'s job sleeps 30 seconds and breaks on the first 2xx, so in every
deployment anyone can still measure, the poll that passed was answered by the container that was
already running. `change-manager`'s own workflow says so in a comment, which is why it stopped
doing it. A liveness poll is `trigger_only` plus "the site was not already down", so the LEVEL --
the machine-readable rung a consumer keys on -- must not claim otherwise. What each revision
actually checked is preserved verbatim in `attests`, which is where a difference that carries no
guarantee belongs.

FAIL CLOSED ON AN UNKNOWN REVISION. A blob sha with no entry is not "probably the strong one":
it is a workflow nobody classified, deciding what a record asserts. `attestation_for` returns
None and the caller records `unknown`, never an upgrade.

THE LITERAL IS TRANSCRIBED, NOT CORRECTED. `6cad4cf9`'s loops skip any brain whose UUID secret is
empty, in the health check as well as the rollout -- so with every secret unset the job concludes
success having deployed and checked nothing. Transcribing what the bytes permit rather than what
the repository happens to be configured to do is the point, and it is also the honest limit of
this mechanism: the values of those secrets are readable by nobody, so no pin over bytes can
resolve them.

WHY A REGISTRY AND NOT A PARSER. Deciding what a workflow attests, and which of its jobs is the
one that talks to production, means understanding a shell script inside a YAML document evaluated
by GitHub. A re-implementation would be a second interpreter of a language nobody owns -- wrong
in ways that look right.
"""

from __future__ import annotations

from dataclasses import dataclass

# A green run establishes that the merged build is the one production is serving.
ATTESTS_REVISION = "revision_confirmed"
# A green run does NOT establish that. What it did check is in the revision's `attests`.
ATTESTS_UNVERIFIED = "rollout_unverified"
# These bytes are not transcribed here. Recorded as-is; never upgraded to either of the above.
ATTESTS_UNKNOWN = "unknown"

ATTESTATION_LEVELS = frozenset({ATTESTS_REVISION, ATTESTS_UNVERIFIED, ATTESTS_UNKNOWN})


@dataclass(frozen=True)
class RolloutWorkflow:
    """Which workflow is a repository's rollout, addressed two ways.

    By PATH because that is how a run is queried, and by ID because a path that has been renamed
    or a workflow that has been disabled answers the path query with zero runs -- which reads as
    `absent`, "the rollout never ran", when the truth is that the question was asked of the wrong
    thing. Holding both lets that be refused as unmeasurable instead of reported as a finding.
    """

    path: str
    workflow_id: int
    # The branch the workflow fires on (`on: push: branches: [...]`). Transcribed rather than
    # assumed to be the default branch, because the question is not "did this land on the default
    # branch" but "will the rollout run at all" -- and a pull request merged into some other base
    # is `merged: true` with a real merge commit that no run will ever exist at. Without this the
    # settle window turns that into `rollout_never_ran`: a fabricated instance of the headline
    # finding, which is the class review killed twice.
    trigger_branch: str = "main"


ROLLOUT_WORKFLOWS: dict[str, RolloutWorkflow] = {
    "alobarquest/change-manager": RolloutWorkflow(".github/workflows/deploy.yml", 295785634),
    "alobarquest/brain": RolloutWorkflow(".github/workflows/ci.yml", 299003284),
}


@dataclass(frozen=True)
class Attestation:
    """One transcribed revision of a rollout workflow.

    `rollout_job` and `trigger_step` exist because a RUN conclusion cannot answer the question
    that decides whether rolling back is the right remedy or is itself the day's only production
    mutation: *was production ever told?* Every rollout failure in both repositories' history
    carries `conclusion: "failure"` at the run, and two of the three never reached production --
    one because the test job failed and the rollout job was skipped, one because the webhook call
    itself failed. Naming the job and the step is a per-revision judgment, which is what this
    registry is for.
    """

    revision: str
    level: str
    attests: str
    # The job that talks to production. Its absence from a completed run, or a `skipped`
    # conclusion, is positive evidence that nothing was told.
    rollout_job: str
    # The step within that job after which production HAS been told. Only its `success` is taken
    # as evidence that production was reached.
    trigger_step: str


# change-manager, .github/workflows/deploy.yml
#
# The original. `test` (pip) -> build and push `:main` and `:<sha>` -> one `curl -fsSL` at the
# Coolify webhook. `-f` makes a non-2xx fail the job, so a green run proves the webhook accepted
# the request and stops there.
_CM_ORIGINAL = Attestation(
    revision="e48d7e2ade54cff2ab57370ddda66fd17ac1565d",
    level=ATTESTS_UNVERIFIED,
    attests=(
        "the image was pushed and the Coolify webhook answered 2xx; nothing observed production"
    ),
    rollout_job="build-and-deploy",
    trigger_step="Trigger Coolify redeploy",
)

# Identical rollout half; the test job moved from pip to uv (`ae4275f`). A separate entry rather
# than an alias, because the registry keys on BYTES and two revisions that happen to agree today
# are still two revisions.
_CM_UV_TESTS = Attestation(
    revision="791d5c9a7df304f8d1b69e3555ccf7a0709ce363",
    level=ATTESTS_UNVERIFIED,
    attests=(
        "the image was pushed and the Coolify webhook answered 2xx; nothing observed production"
    ),
    rollout_job="build-and-deploy",
    trigger_step="Trigger Coolify redeploy",
)

# `191ec5a`, 2026-08-07 -- "verify the deploy landed, instead of assuming the trigger meant it
# did". Adds a step polling https://change-mgr.alobar.net/api/health every 15s for up to 600s and
# failing unless the reported `revision` equals the pushed sha, against an image built with
# GIT_SHA=${{ github.sha }}. The only transcribed revision under which a green run means what
# item 44's acceptance criteria say it means.
_CM_VERIFIES_REVISION = Attestation(
    revision="a47d4b187c93971a5b5915ce87a963bd4ef35e30",
    level=ATTESTS_REVISION,
    attests=(
        "production answered /api/health reporting the merged commit as its revision "
        "within 600 seconds"
    ),
    rollout_job="build-and-deploy",
    trigger_step="Trigger Coolify redeploy",
)

# brain, .github/workflows/ci.yml
#
# The original three-brain pipeline. Webhook per app, then 30s + up to 5 polls at 15s of each
# `<name>-brain.devonwatkins.com/api/health` for any 2xx. Measured against Coolify's rolling
# swap (43-73s), the first poll lands while the OLD container is still serving, so a green run
# says the domain was up -- not that the merged build is behind it.
_BRAIN_THREE = Attestation(
    revision="cf01da8ddb72879dfa008477a25dfc600d567baf",
    level=ATTESTS_UNVERIFIED,
    attests=(
        "three brain domains answered /api/health 2xx starting 30s after the webhook; "
        "Coolify's swap is rolling and takes longer than that, so the old container "
        "answers and no revision is checked"
    ),
    rollout_job="deploy",
    trigger_step="Deploy all three brain apps",
)

# `470e424` dropped a dev-compose validation step from the `test` job. The rollout job is
# byte-identical to `cf01da8d`.
_BRAIN_THREE_NO_COMPOSE = Attestation(
    revision="ab3735c4cd9ebf986bf61d3b5f241d4202d36833",
    level=ATTESTS_UNVERIFIED,
    attests=(
        "three brain domains answered /api/health 2xx starting 30s after the webhook; "
        "Coolify's swap is rolling and takes longer than that, so the old container "
        "answers and no revision is checked"
    ),
    rollout_job="deploy",
    trigger_step="Deploy all three brain apps",
)

# `7823db2` added code-brain, and with it a skip-if-the-secret-is-empty guard in BOTH loops. So
# the honest reading is "the brains whose UUID secret was set", not "four brains" -- with every
# secret unset this job concludes success having deployed and checked nothing.
_BRAIN_FOUR = Attestation(
    revision="6cad4cf9f03d816ce8bf8fb87fa67d8634486ef1",
    level=ATTESTS_UNVERIFIED,
    attests=(
        "each brain domain whose Coolify UUID secret was set answered /api/health 2xx "
        "starting 30s after the webhook; the swap is rolling and slower than that, no "
        "revision is checked, and an unset secret skips both the rollout and its check"
    ),
    rollout_job="deploy",
    trigger_step="Deploy brain apps",
)

REGISTRY: dict[str, Attestation] = {
    attestation.revision: attestation
    for attestation in (
        _CM_ORIGINAL,
        _CM_UV_TESTS,
        _CM_VERIFIES_REVISION,
        _BRAIN_THREE,
        _BRAIN_THREE_NO_COMPOSE,
        _BRAIN_FOUR,
    )
}


def rollout_for(repository: str) -> RolloutWorkflow | None:
    """The workflow whose run is this repository's rollout, or None if nobody said."""
    return ROLLOUT_WORKFLOWS.get(repository.lower())


def attestation_for(revision: str | None) -> Attestation | None:
    """The transcribed meaning of a green run at these bytes, or None for unclassified bytes."""
    if not revision:
        return None
    return REGISTRY.get(revision)


def level_of(revision: str | None) -> str:
    """The attestation level to record. An unclassified revision is `unknown`, never upgraded."""
    attestation = attestation_for(revision)
    return attestation.level if attestation is not None else ATTESTS_UNKNOWN
