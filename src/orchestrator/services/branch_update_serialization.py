"""The lane edits one Dependabot branch per repository at a time. ADR-0045.

Bringing a Dependabot pull request up to date with its base writes a commit under this estate's
identity, and from then on Dependabot refuses to rebase that branch. Two such branches in one
repository, and a landing of either, is the whole precondition of the deadlock this rule exists to
prevent: the landing conflicts the other, Dependabot will not rebase it, and the lane will not
freshen a conflicted head.

This module owns the rule so that both branch-update acts and both admission reads ask one
question in one place.
"""

from __future__ import annotations

from typing import Final

# The event actions the two branch-update acts record, one per lane. They live HERE rather than in
# the act modules because the sibling rule reads them back out of the event log, and the act
# modules import this one -- so this one cannot import them. Each act re-imports its own under the
# same name, so every existing reader keeps working.
BRANCH_UPDATE_ACTION: Final = "estate_pr_branch_update.updated"
INERT_BRANCH_UPDATE_ACTION: Final = "inert_pr_branch_update.updated"
