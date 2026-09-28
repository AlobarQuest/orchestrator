"""`bump_proposer` is a separate program that happens to live in this repository.

Same shape as the watcher's, the ledger's, the lander's and the carry's isolation tests, and
for the same reason: hosting an out-of-process program here is a packaging choice, and the
moment it can import the orchestrator it stops being one.

**IT MATTERS PARTICULARLY HERE, because this program's whole judgment is a READING.** It decides
which bumps the estate will not land by itself, and since ADR-0038 it reads that rule from
change-manager's landing policy -- the one holder, asked rather than transcribed. A program that
could import the orchestrator could reach the admission module that answers an adjacent question
about the same pull requests from that same declaration, and the two answers would drift into
each other while both cited one document.

The import confinement this module used to assert -- nothing from the orchestrator, the
orchestrator nothing from here, and the dependency allowlist -- is now a row of
`tests/architecture/test_out_of_process_isolation.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

PROPOSER = Path("src/bump_proposer")


def test_the_only_orchestrator_route_this_producer_names_is_the_observation_one() -> None:
    """It writes to change-manager, to a checkout, and -- since G1+G2 -- one FACT to the
    orchestrator. Nothing else.

    **THIS ASSERTED THAT THE PRODUCER COULD NOT REACH THE ORCHESTRATOR AT ALL, AND THE NARROWING
    IS DELIBERATE.** It is recorded here rather than in a commit message because a guard that
    loosens silently is worth less than no guard, and because the property it was protecting is
    unchanged and is still asserted below.

    What made it worth having: the orchestrator learns about this work through the carry, from a
    record a person APPROVED, so a producer that could register an intake would be the machine
    approving its own proposal -- which ADR-0026 deliberately did not decide. Filing an
    observation is the opposite act. It states a fact, decides nothing, and uses the OBSERVER
    bearer, whose entire write surface is that single route; the signal->work contract requires
    it precisely so the cause of a record is a durable fact rather than prose inside the record.

    So the prohibition moves from "no orchestrator route" to "no orchestrator route but this
    one", and every route that could create work, move a unit or land anything stays named.
    """
    text = "\n".join(path.read_text() for path in sorted(PROPOSER.rglob("*.py")))

    assert "/api/v1/observations" in text
    for forbidden in (
        # The production host stays absent: the launcher supplies it, so a URL this program
        # could not have been pointed away from is not a value it needs to carry.
        "sds.alobar.net",
        "package-intakes",
        "/api/v1/work-units",
        "/api/v1/change-records",
        "/api/v1/revisions",
        "/api/v1/package-intakes",
        "/api/v1/observations/",
    ):
        assert forbidden not in text, forbidden


def test_no_second_orchestrator_path_can_be_spelled_at_all() -> None:
    """The complement of the list above, which is a denylist and therefore cannot be complete.

    Every `/api/v1/...` string in the package must be the one route. A route added later is
    caught by this even if nobody thinks to add it to the names above -- the direction that
    fails closed.
    """
    paths = set()
    for path in sorted(PROPOSER.rglob("*.py")):
        paths |= set(re.findall(r"/api/v1/[A-Za-z0-9_\-/{}]*", path.read_text()))

    assert paths == {"/api/v1/observations"}
