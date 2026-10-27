"""Which applications are asked, and where that list is declared.

**A DECLARED LIST IS NORMALLY THE DEFECT, AND THE ANSWER HERE IS NOT THAT IT IS SMALL.**
`pin_watcher` refuses one in its own module docstring, and the reasoning is right: this estate has
had four disagreeing answers to "which repositories are factory targets", ADR-0015 ruled the
declaration belongs to the artifact rather than to a list the artifact cannot see, and a fifth
hand-maintained list would go stale in exactly the way the lane holding it exists to catch.

The objection is to a list that rots SILENTLY. This one doesn't, for two reasons. It is declared in
the SDS member record (each member's `apps`), the one place a repository's membership is decided,
so it is edited by a confirmed proposal rather than by a code change here. And `census.undeclared`
asks the platform for every running application, probes the ones absent from the record, and lists
any that answers with a revision in every pass's output. Adding a subject is a response to that
line.

**AN UNDECLARED APPLICATION IS REPORTED, NEVER A FINDING.** The member record is the SDS's list, and
applications built outside the SDS serve a revision too, because the health standard asks every
application to. Until 2026-10-15 an undeclared one failed the pass, so garexchange, which is not
an SDS application, held `sds-production-current` down for a week from the day it started serving a
revision, and a real drift in a subject would have raised nothing new.

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

So the member record carries the two facts no reachable surface joins for us, and the platform is
asked to police it. A retired member's apps are not subjects.

**A SUBJECT IS ASKABLE WHEN IT SERVES THE FIELD.** A subject that answers without a `revision` is
`unstamped`, which is reported and is deliberately NOT a finding -- 6 of the 18 applications
running on 2026-09-08 answer health with no revision at all, and a lane that called each of them a
finding would be red forever about applications that never claimed to be askable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sds_members.client import DEFAULT_BASE_URL, read_members
from sds_members.populations import app_subjects


@dataclass(frozen=True)
class Subject:
    """One application, and the branch its production is expected to be serving."""

    name: str
    health_url: str
    repository: str
    branch: str = "main"


def load_subjects(
    *,
    token: str,
    credential_key_id: str,
    base_url: str = DEFAULT_BASE_URL,
    transport: Any = None,  # an httpx transport; typed Any to keep this module off the HTTP scan
) -> tuple[Subject, ...]:
    """The apps of every live member, in the record's order. `MembersUnreadable` is not caught."""
    members = read_members(
        token=token, credential_key_id=credential_key_id, base_url=base_url, transport=transport
    )
    return tuple(
        Subject(app.name, app.health_url, repository, app.branch)
        for repository, app in app_subjects(members)
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
