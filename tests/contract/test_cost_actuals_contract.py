"""The cost-actuals seam contract (WS-P2.4 Increment 1), orchestrator side.

`tests/fixtures/runner_cost_actuals.json` is a byte-identical copy of the file of the same
name in AlobarQuest/factory-runner. `COST_ACTUALS_CONTRACT_SHA256` is identical in both
repos' tests, so a one-sided edit fails here rather than at the next dispatch.
"""

import hashlib
import json
from pathlib import Path

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "runner_cost_actuals.json"
COST_ACTUALS_CONTRACT_SHA256 = "87004ad49dfbca020004d6c5ffa7dec2ce55923bbb0388604cc0bebde6f4386a"


def golden_cost_actuals() -> dict:
    return json.loads(FIXTURE.read_text())


def test_golden_cost_actuals_is_unchanged() -> None:
    """A one-sided edit here means factory-runner's copy has silently drifted."""
    canonical = json.dumps(golden_cost_actuals(), sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(canonical.encode()).hexdigest() == COST_ACTUALS_CONTRACT_SHA256
