"""The cost-actuals seam contract (WS-P2.4 Increment 1), orchestrator side.

`tests/fixtures/runner_cost_actuals.json` is a byte-identical copy of the file of the same
name in AlobarQuest/factory-runner. `COST_ACTUALS_CONTRACT_SHA256` is identical in both
repos' tests, so a one-sided edit fails here rather than at the next dispatch.
"""

import hashlib
import json
from pathlib import Path

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "runner_cost_actuals.json"
COST_ACTUALS_CONTRACT_SHA256 = "1338e794272f983f3d0a4f82e36f6368b11a516b2a66b92d8bea9169fed02fac"


def golden_cost_actuals() -> dict:
    return json.loads(FIXTURE.read_text())


def test_golden_cost_actuals_is_unchanged() -> None:
    """A one-sided edit here means factory-runner's copy has silently drifted."""
    canonical = json.dumps(golden_cost_actuals(), sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(canonical.encode()).hexdigest() == COST_ACTUALS_CONTRACT_SHA256
