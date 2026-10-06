"""The admission posture the test harness dispatches under (schema 7 of the policy artifact).

The harness's envelopes carry no ``change_class``, so admission matches them on their capability
name, ``repo.edit``, which production names as no change class. Rather than give every harness
envelope a real change class -- ``dependency-update`` brings the mutation-command rules with it --
a module that dispatches opts in to this posture: the SHIPPED artifact, with that one fallback
name added to its change classes. Everything else, the capabilities included, is what ships.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from orchestrator.factory_policy import PACKAGED_ARTIFACT, FactoryPolicy, load_factory_policy
from orchestrator.services.execution import reach_admission

# The change class an envelope with none presents: its `required_capability`.
HARNESS_FALLBACK_CHANGE_CLASS = "repo.edit"


def harness_policy(path: Path = PACKAGED_ARTIFACT) -> FactoryPolicy:
    policy = load_factory_policy(path)
    admission = replace(
        policy.admission,
        change_classes=policy.admission.change_classes | {HARNESS_FALLBACK_CHANGE_CLASS},
    )
    return replace(policy, admission=admission)


@pytest.fixture
def harness_posture(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reach_admission, "load_factory_policy", harness_policy)
