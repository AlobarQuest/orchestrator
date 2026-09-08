"""App Brain's answer vocabulary now has THREE copies in this estate, and this pins the third.

The source of truth is `LANDING_*` in AlobarQuest/brain `src/brains/app/models.py`, served by
GET /api/apps/default-branch-landing. The orchestrator mirrors it in
`services/estate_landing.py`, and this lane mirrors it again -- it cannot import the
orchestrator's, by its own isolation test, and rightly so.

A copy nothing checks is how a vocabulary drifts, and this repository has paid for that four times.
A TEST may import both sides even where the packages may not, so the third copy is held to the
second rather than trusted. Note the direction of the failure this prevents: a member spelled
differently here would classify every repository `unknown` and judge every gap strictly, so the
lane would report a separate-track application's ordinary queue as a finding -- loud, wrong, and
easy to explain away as the estate being behind.
"""

from __future__ import annotations

from orchestrator.services import estate_landing
from revision_watcher import estate


def test_the_three_landing_values_are_spelled_the_same_on_both_sides() -> None:
    assert estate.LANDING_REDEPLOYS == estate_landing.LANDING_REDEPLOYS
    assert estate.LANDING_INERT == estate_landing.LANDING_INERT
    assert estate.LANDING_UNKNOWN == estate_landing.LANDING_UNKNOWN


def test_neither_side_has_invented_a_fourth_value() -> None:
    """The whole vocabulary, not just the members this lane happens to read. A value added on one
    side and not the other is the drift; a value added to BOTH is a decision, and editing this
    literal is how it gets recorded."""
    mirrored = {
        estate.LANDING_REDEPLOYS,
        estate.LANDING_INERT,
        estate.LANDING_UNKNOWN,
    }
    orchestrators = {
        value
        for name, value in vars(estate_landing).items()
        if name.startswith("LANDING_") and isinstance(value, str)
    }

    assert mirrored == orchestrators == {"redeploys", "inert", "unknown"}


def test_the_route_is_the_same_route() -> None:
    """Two spellings of one path is the same defect one field over, and it fails as a 404 nobody
    attributes rather than as a mismatch anybody sees."""
    assert estate.LANDING_ROUTE == estate_landing._ROUTE
