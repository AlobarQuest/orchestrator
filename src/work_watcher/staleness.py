"""Which live work records have gone stale, and why -- REPORTED, never acted on (ruling B1).

Devon, 2026-09-27: a change-manager `work` record that is pending or approved while its Dependabot
pull request was closed or merged by hand, or that a newer revision's record has superseded, is
surfaced as a FINDING, and a person retires it. No new write authority and no new retirement
vocabulary: the watcher's one write stays `work_unit_completed`, and nothing in this module can
reach it.

**WHY THIS LIVES IN THE WATCHER AND NOT IN THE PRODUCER.** "Closed or merged BY HAND" is only
decidable beside the completion verdict. The ordinary success path produces the same observable --
the factory lands the bump, the pull request closes, and the record stays approved until this
watcher retires it on its next pass. A producer that saw only "pull request closed, record live"
would report every successful landing once. The watcher already asks the orchestrator what each
record caused and runs before the carry, so it applies the rule in the one order that is sound:
complete -> retire (not a finding); not complete and stale -> finding; otherwise waiting.

**SUPERSESSION NEEDS NEITHER GITHUB NOR A CHECKOUT.** A record for package P at revision r is
superseded when the pipeline holds any record for P at a higher revision, whatever that record's
status: a higher record exists only because the standing package was advanced, so the checkout's
tip is at least that revision and the carry refuses r on `revision_mismatch` forever.

**THE PULL REQUEST IS READ OFF THE RECORD'S REASONING, and that is a same-repository contract,
not prose-scraping.** The record carries no pull request field. Its `reasoning` is composed by
`bump_proposer.cli._reasoning` and is FROZEN -- change-manager refuses a changed asserted field with
a terminal 409 -- so its first clause is stable for the life of the record, and
`tests/work_watcher/test_staleness.py` pins the pattern below to that function's live output. A
record whose reasoning does not match (hand-proposed, or an older template) is UNASSESSED: printed,
never a finding, because a report that went red for every record it cannot parse would stop being
read.
"""

from __future__ import annotations

import re
from typing import Final

from work_carrier.change_manager import PipelineRecord

LIVE: Final = frozenset({"pending", "approved"})

_PULL_REQUEST: Final = re.compile(
    r"^(?P<repository>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+) carries an open dependency update of "
    r".+? \(pull request (?P<number>[0-9]{1,9})\)"
)


def superseded_by(
    package_id: str, package_revision: int, pipeline: tuple[PipelineRecord, ...]
) -> PipelineRecord | None:
    """The highest-revision record for the same package ABOVE this revision, or None."""
    newer = [
        row
        for row in pipeline
        if row.package_id == package_id and row.package_revision > package_revision
    ]
    if not newer:
        return None
    return max(newer, key=lambda row: (row.package_revision, row.change_record_id))


def pull_request_of(reasoning: str) -> tuple[str, int] | None:
    """`(repository, number)` named by a bump-proposer record's reasoning, or None."""
    match = _PULL_REQUEST.match(reasoning)
    if match is None:
        return None
    return match.group("repository"), int(match.group("number"))
