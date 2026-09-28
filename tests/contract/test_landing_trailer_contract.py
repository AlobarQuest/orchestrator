"""The commit trailers one program writes and another reads, pinned from both ends.

`services/landing/estate_pr_merge.py` writes `SDS-Change-Record:` / `SDS-Policy-Version:` into the
squash body of every landing it performs, and `landing_ledger/github.py::policy_permission` reads
them back to record what permitted the landing. The two live in different programs --
`src/orchestrator` imports nothing from `src/landing_ledger`, and an architecture test enforces that
-- so neither module can notice if the other renames a trailer. Until 2026-09-27 the writer's own
test asserted through its constant, so a rename there reddened nothing: the ledger would have
recorded every such landing as having no basis, and no detector reads that class.

`SDS-Unit:` is read by two programs, `deploy_watcher/units.py` and `landing_ledger/github.py`,
each with its own copy of the pattern for the same isolation reason. They must match the same
messages; the copies are pinned equal here rather than shared, because sharing would need an
import across the boundary the isolation tests exist to keep.

Tests are not confined by the isolation guards, which is what makes this the right place for it.
"""

from __future__ import annotations

import pytest

from deploy_watcher import orchestrator as watcher_orchestrator
from deploy_watcher import units
from landing_ledger import github as ledger_github
from landing_ledger import model as ledger_model
from landing_ledger.model import PolicyPermission
from orchestrator.services.landing import estate_pr_merge
from orchestrator.services.landing.estate_landing_admission import EstateLandingAdmission

UNIT = "7a81c2c2-0835-5bba-a308-36e868719b62"


def test_what_the_writer_composes_is_what_the_reader_parses() -> None:
    """The writer's actual output, through the reader's actual parser. A rename on either side
    reds this; the literal spellings are pinned beside the writer's own tests as well."""
    admission = EstateLandingAdmission(
        satisfied=True,
        refusals=(),
        repository="alobarquest/brain",
        pr_number=7,
        head_sha="a" * 40,
        change_record_id=52,
        policy_version=8,
        branch_update_qualifies=False,
        rollout_base_matches_pin=True,
        branch_update_withheld_for_sibling=False,
    )

    body = estate_pr_merge._trailers(admission)

    assert ledger_github.policy_permission(body) == PolicyPermission(
        change_record=52, policy_version=8
    )


def test_the_two_unit_trailer_patterns_are_one_pattern() -> None:
    assert units.SDS_UNIT.pattern == ledger_github.SDS_UNIT.pattern
    assert units.SDS_UNIT.flags == ledger_github.SDS_UNIT.flags
    assert watcher_orchestrator.WORK_UNIT_ID == ledger_model.WORK_UNIT_ID


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (f"Bump x\n\nSDS-Unit: {UNIT}\nSDS-Package-Rev: 2\n", UNIT),
        (f"  SDS-Unit:   {UNIT}  \n", UNIT),
        (f"see SDS-Unit: {UNIT} in prose\n", None),
        ("SDS-Unit: not-a-unit\n", None),
        (f"SDS-Unit: {UNIT.upper()}\n", None),
    ],
    ids=["trailer", "padded", "in-prose", "malformed", "uppercase"],
)
def test_both_unit_readers_answer_alike(message: str, expected: str | None) -> None:
    """Behaviour, not only source text: the two readers must agree on each shape."""
    watcher = units.claimed_unit(message)
    ledger = ledger_github.SDS_UNIT.search(message)

    assert watcher == expected
    assert (ledger.group(1) if ledger else None) == expected
