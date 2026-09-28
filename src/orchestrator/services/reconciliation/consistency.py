"""Projection-vs-source consistency check (AC-008).

It REPORTS. It never repairs, never transitions, never commits, and never raises on the
corruption it exists to find -- a checker that crashes on corruption is not a checker.

Two constraints shape the whole design:

* It must NOT reuse `_terminal` / `current_evidence`. Those RAISE on a broken supersession chain,
  so a check built on them would crash instead of report.
* It must NOT reuse the projection's own set-difference. That would make the clean-fixture result
  a tautology -- the projection would simply agree with itself.

So it recomputes INDEPENDENTLY, in SQL.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
from orchestrator.kernel.states import WAIVER_RISK_CLASSES
from orchestrator.persistence.models import (
    EVIDENCE_HEAD_BOOKKEEPING_AC_ID,
    WorkPackageRevision,
    WorkUnit,
)
from orchestrator.services.lifecycle import required_ac_ids

# Exactly one unsuperseded head per (revision, unit, ac) -- for evidence that participates in
# supersession. `release_artifacts` writes one row per binding under the constant ac_id
# 'release-artifact', all with supersedes_evidence_id NULL, and a unit may legitimately carry
# several bindings, so that triple genuinely has MANY heads. Those rows are never superseded and
# never adjudicated. Counting them here would flag every healthy multi-binding unit -- and a
# checker that cries wolf on healthy units is worse than no checker.
#
# The `groups` CTE is what makes a ZERO-head chain visible: joining straight to the heads would
# drop the whole group, and zero heads is precisely the reachable corruption (the partial unique
# index makes two heads structurally impossible).
_EVIDENCE_HEAD_COUNTS = text(
    """
    WITH scoped AS (
        SELECT * FROM evidence WHERE ac_id <> :bookkeeping_ac_id
    ),
    groups AS (
        SELECT DISTINCT work_package_revision_id, work_unit_id, ac_id FROM scoped
    ),
    heads AS (
        SELECT e.work_package_revision_id, e.work_unit_id, e.ac_id, count(*) AS head_count
        FROM scoped e
        WHERE NOT EXISTS (
            SELECT 1 FROM scoped s WHERE s.supersedes_evidence_id = e.id
        )
        GROUP BY 1, 2, 3
    )
    SELECT g.work_package_revision_id, g.work_unit_id, g.ac_id,
           coalesce(h.head_count, 0) AS head_count
    FROM groups g
    LEFT JOIN heads h
      ON h.work_package_revision_id = g.work_package_revision_id
     AND h.work_unit_id = g.work_unit_id
     AND h.ac_id = g.ac_id
    WHERE coalesce(h.head_count, 0) <> 1
    ORDER BY g.work_unit_id, g.ac_id
    """
)

# Independent recomputation of what the completion guard concludes -- deliberately NOT a call to
# that guard, which would be auditing the guard with the guard. The terminal adjudication for a
# (unit, ac) is the one nothing supersedes; it must be unique and satisfying.
SATISFIED_ACS = text(
    """
    WITH terminals AS (
        SELECT a.ac_id, a.outcome, a.scope, a.expires_at
        FROM adjudications a
        WHERE a.work_unit_id = :unit_id
          AND NOT EXISTS (
              SELECT 1 FROM adjudications s
              WHERE s.supersedes_adjudication_id = a.id
          )
    )
    SELECT t.ac_id
    FROM terminals t
    GROUP BY t.ac_id
    HAVING count(*) = 1
       AND bool_and(
           t.outcome IN ('passed', 'not_applicable')
           OR (t.outcome = 'waived' AND t.scope IS NULL
               AND (t.expires_at IS NULL OR t.expires_at > :now))
       )
    """
)


# Reporting-only, legacy-defense audit of current (unsuperseded) waivers -- the completion gate
# already refuses an expired waiver at the moment of completion, but nothing else makes an
# outlived accepted-risk visible after the fact, and a risk outside the controlled vocabulary
# should be structurally impossible post-CHECK but is defended here for legacy rows anyway.
_THIN_WAIVERS = text(
    """
    WITH terminals AS (
        SELECT a.work_unit_id, a.ac_id, a.risk, a.expires_at
        FROM adjudications a
        WHERE a.outcome = 'waived'
          AND NOT EXISTS (
              SELECT 1 FROM adjudications s
              WHERE s.supersedes_adjudication_id = a.id
          )
    )
    SELECT work_unit_id, ac_id, risk, expires_at
    FROM terminals
    WHERE (expires_at IS NOT NULL AND expires_at <= :now)
       OR risk IS NULL
       OR NOT (risk = ANY(:risk_classes))
    ORDER BY work_unit_id, ac_id
    """
)


@dataclass(frozen=True)
class ConsistencyFinding:
    check: str
    work_unit_id: uuid.UUID | None
    subject: str
    detail: str
    observed: str
    expected: str


@dataclass(frozen=True)
class ConsistencyReport:
    checked_at: datetime
    findings: tuple[ConsistencyFinding, ...]

    @property
    def divergent(self) -> bool:
        return bool(self.findings)


def check_consistency(session: Session) -> ConsistencyReport:
    now = TransactionClock().now(session)
    return ConsistencyReport(
        checked_at=now,
        findings=(
            *_evidence_head_findings(session),
            *_completion_findings(session, now),
            *_waiver_findings(session, now),
        ),
    )


def _evidence_head_findings(session: Session) -> tuple[ConsistencyFinding, ...]:
    rows = session.execute(
        _EVIDENCE_HEAD_COUNTS, {"bookkeeping_ac_id": EVIDENCE_HEAD_BOOKKEEPING_AC_ID}
    ).all()
    return tuple(
        ConsistencyFinding(
            check="evidence_head_count",
            work_unit_id=unit_id,
            subject=ac_id,
            detail=(
                f"revision {revision_id} has {head_count} unsuperseded evidence heads; "
                "a supersession chain must have exactly one"
            ),
            observed=str(head_count),
            expected="1",
        )
        for revision_id, unit_id, ac_id, head_count in rows
    )


def _completion_findings(session: Session, now: datetime) -> tuple[ConsistencyFinding, ...]:
    """A unit cannot legitimately be COMPLETED without its required ACs satisfied -- the lifecycle
    guard forbids it. If one is, the guard was bypassed, and the operator has to know."""
    findings: list[ConsistencyFinding] = []
    units = session.scalars(
        select(WorkUnit).where(WorkUnit.state == "completed").order_by(WorkUnit.unit_key)
    ).all()
    for unit in units:
        revision = session.get(WorkPackageRevision, unit.work_package_revision_id)
        if revision is None:
            continue
        # The REQUIRED set is a source lookup (the approved decomposition's AC mapping), so
        # reusing it is honest. What must be recomputed independently is the SATISFACTION
        # determination -- calling the completion guard here would be auditing the guard with the
        # guard, and it would agree with itself by construction.
        required = required_ac_ids(session, revision, unit)
        if required is None:
            continue
        satisfied = set(session.scalars(SATISFIED_ACS, {"unit_id": unit.id, "now": now}))
        findings.extend(
            ConsistencyFinding(
                check="completion_integrity",
                work_unit_id=unit.id,
                subject=ac_id,
                detail="completed unit has no satisfied terminal adjudication for this criterion",
                observed="unsatisfied",
                expected="satisfied",
            )
            for ac_id in sorted(set(required) - satisfied)
        )
    return tuple(findings)


def _waiver_findings(session: Session, now: datetime) -> tuple[ConsistencyFinding, ...]:
    """Surface current waivers that are structurally thin -- expired, or (legacy defense) a risk
    outside the controlled vocabulary. Reporting only; the completion gate already refuses an
    expired waiver, but nothing else makes an outlived accepted-risk visible."""
    # Scope is intentionally NOT audited here: a scoped, unexpired, valid-risk waiver is not a
    # finding. This audit's remit is "expired / out-of-vocab risk," and the human adjudication
    # form never writes `scope` at all (it is deliberately unexposed), so no scoped waiver can
    # originate from that path today.
    rows = session.execute(
        _THIN_WAIVERS, {"now": now, "risk_classes": list(WAIVER_RISK_CLASSES)}
    ).all()
    findings: list[ConsistencyFinding] = []
    for work_unit_id, ac_id, risk, expires_at in rows:
        if expires_at is not None and expires_at <= now:
            detail = "waiver expired; its accepted risk has outlived the approved window"
            observed = f"expired at {expires_at.isoformat()}"
        else:
            detail = "waiver risk is outside the controlled vocabulary"
            observed = f"risk={risk!r}"
        findings.append(
            ConsistencyFinding(
                check="waiver_hardening",
                work_unit_id=work_unit_id,
                subject=ac_id,
                detail=detail,
                observed=observed,
                expected="a current waiver with an in-vocabulary risk class",
            )
        )
    return tuple(findings)
