"""The lanes' hold matcher works on the member record as the client reads it.

Its behaviour is the orchestrator copy's, held equal over one corpus by
`tests/services/test_hold_agreement.py`; this file pins what only the lane side has.
"""

from sds_members.client import Hold
from sds_members.holds import Bump, HoldBreach, bumps_of, exceeded

MESSAGE = """chore(deps): bump the python group across 1 directory with 2 updates

---
updated-dependencies:
- dependency-name: alembic
  dependency-version: 1.18.0
  dependency-group: python
- dependency-name: SQLAlchemy
  dependency-version: 2.1.6
  dependency-group: python
...
"""


def test_a_client_hold_is_matched() -> None:
    hold = Hold(ecosystem="pip", directory="/", dependency="sqlalchemy", ceiling="2.0.54")
    bumps = bumps_of(
        "chore(deps): bump the python group across 1 directory with 2 updates", MESSAGE, "pip"
    )
    assert bumps == (
        Bump("pip", None, "alembic", "1.18.0"),
        Bump("pip", None, "SQLAlchemy", "2.1.6"),
    )
    assert exceeded([hold], "AlobarQuest/brain", bumps or ()) == (
        HoldBreach("AlobarQuest/brain", "pip", "/", "sqlalchemy", "2.0.54", "2.1.6"),
    )


def test_an_unreadable_message_is_none() -> None:
    assert bumps_of("bump x from 1 to 2", "no block", "pip") is None
