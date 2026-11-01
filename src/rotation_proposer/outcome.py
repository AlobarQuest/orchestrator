"""What a pass reports about one credential, and which reports are findings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# What makes a pass a FINDING rather than a clean run. `legacy` is absent: a credential the
# registry has not handed to the SDS is infraops' to rotate. `unlaned` and `not-handed-over` are
# findings, because each is a credential the two lanes disagree about -- handed over with no package
# (nothing rotates it) or a package with no handover (both could). `ungranted` is a handed-over
# credential whose repository the member record does not grant `credential_rotation` on. `stalled`
# is an approved record with a failed or cancelled unit, which stays until a person acts.
# `record-wontfix` and `due-after-rotation` are a due credential nothing will move: its carrying
# revision's record is `wontfix`, or it rotated and is still due.
# `awaiting-approval`, `declined` and `revised` are absent because waiting on Devon is the lane
# working, not failing; the first two raise an alert only once they have waited long enough
# (`alerts.APPROVAL_ALERT_HOURS`).
FINDING_STATUSES: Final = frozenset(
    {
        "error",
        "refused",
        "unobserved",
        "superseded",
        "stacked",
        "unexpected-state",
        "unrecognised",
        "unlaned",
        "not-handed-over",
        "ungranted",
        "exposure-open",
        "resolved-by-person",
        "stalled",
        "record-wontfix",
        "due-after-rotation",
    }
)


@dataclass(frozen=True)
class Outcome:
    credential_id: str
    status: str
    detail: str
