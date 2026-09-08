"""Which applications are asked, and why this list is allowed to exist.

**A DECLARED LIST IS NORMALLY THE DEFECT, AND THE ANSWER HERE IS NOT THAT IT IS SMALL.**
`pin_watcher` refuses one in its own module docstring, and the reasoning is right: this estate has
had four disagreeing answers to "which repositories are factory targets", ADR-0015 ruled the
declaration belongs to the artifact rather than to a list the artifact cannot see, and a fifth
hand-maintained list would go stale in exactly the way the lane holding it exists to catch.

The objection is to a list that rots SILENTLY. This one cannot: `census.undeclared` asks the
platform for every running application, probes the ones absent from this table, and reports any
that answers with a revision. So the table's gap is a finding rather than a blind spot, and adding
a subject is a response to the lane telling you it is missing one.

**WHY THE POPULATION IS NOT DERIVED OUTRIGHT**, measured 2026-09-08 rather than assumed:

- App Brain holds everything a subject needs -- `deployment_url`, `github_repo`,
  `environments[].branch` -- and its read-only credential reaches exactly ONE route,
  `/api/apps/default-branch-landing`. `/api/apps`, `/api/apps/{slug}` and `/api/repositories` all
  answer 401 with that key. Deriving the population would mean holding the broad `brains` key,
  which can also POST proposals -- a strictly wider credential for a read.
- Coolify knows every application and its FQDN and is the population source used below, but for a
  GHCR-image application `git_repository` reads `coollabsio/coolify`, so it cannot say which
  repository an application is built from. It also could not reach the orchestrator's health path:
  that application's platform health check is deliberately disabled, so the recorded path is a
  default that answers 404.
- `default_branch_landing` is the wrong predicate even though it looks like the right one. It says
  whether LANDING redeploys, and `orchestrator` is `inert` -- its swap is performed by hand -- yet
  it is the subject whose currency matters most and the one that motivated this lane.

So the table below carries the two facts no reachable surface joins for us, and the platform is
asked to police it.

**AN APPLICATION OPTS IN BY SERVING THE FIELD.** A subject that answers without a `revision` is
`unstamped`, which is reported and is deliberately NOT a finding -- 6 of the 18 applications
running on 2026-09-08 answer health with no revision at all, and a lane that called each of them a
finding would be red forever about applications that never claimed to be askable.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Subject:
    """One application, and the branch its production is expected to be serving."""

    name: str
    health_url: str
    repository: str
    branch: str = "main"


# Measured 2026-09-08 across all 18 running Coolify applications: exactly five serve a revision
# today, and they are the four brains plus change-manager. `orchestrator` is the sixth and reports
# `null` until the image carrying the field is built and swapped -- which is the honest answer for
# every build made before the field existed, and is why `unstamped` is a state rather than an error.
SUBJECTS: tuple[Subject, ...] = (
    Subject("infra-brain", "https://infra-brain.devonwatkins.com/api/health", "AlobarQuest/brain"),
    Subject("open-brain", "https://open-brain.devonwatkins.com/api/health", "AlobarQuest/brain"),
    Subject("app-brain", "https://app-brain.devonwatkins.com/api/health", "AlobarQuest/brain"),
    Subject("code-brain", "https://code-brain.devonwatkins.com/api/health", "AlobarQuest/brain"),
    Subject(
        "change-manager",
        "https://change-mgr.alobar.net/api/health",
        "AlobarQuest/change-manager",
    ),
    # Its health path is NOT /api/health -- the platform's own check is disabled for this
    # application so that a migration-drift 503 on /health/ready cannot kill the container during
    # a migrate-first window, and the recorded path is a default that answers 404.
    Subject(
        "orchestrator",
        "https://sds.alobar.net/health/live",
        "AlobarQuest/orchestrator",
    ),
)

# The field names an application may answer with. `revision` is what this estate's three stamped
# applications serve; the other two are read because a future application may arrive already
# speaking one of them, and refusing to look would report it as unstamped rather than as current.
REVISION_KEYS = ("revision", "commit", "git_sha")


def revision_of(body: object) -> str | None:
    """The commit an application claims to be serving, or None when it claims nothing.

    An empty string is None: an application that serves the key with no value knows no more about
    itself than one that omits it, and treating the two differently would put a subject into the
    decidable set on the strength of a field that says nothing.
    """
    if not isinstance(body, dict):
        return None
    for key in REVISION_KEYS:
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
