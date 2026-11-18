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
# `manual-exposure`, `manual-request` and `manual-age` are a due credential the registry has not
# handed over, which a person rotates (ADR-0059 Amendment 12 decision item 4). Only the lane's
# container reports them (`cli.NOT_HANDED_OVER_ENV`); on the Mac the same credential is `legacy`.
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
        "manual-exposure",
        "manual-request",
        "manual-age",
    }
)
# A trigger (`findings.TRIGGERS`) -> the finding a credential not handed over reports for it.
NOT_HANDED_OVER_STATUSES: Final = {
    "exposure": "manual-exposure",
    "requested": "manual-request",
    "age": "manual-age",
}


@dataclass(frozen=True)
class Outcome:
    credential_id: str
    status: str
    detail: str
