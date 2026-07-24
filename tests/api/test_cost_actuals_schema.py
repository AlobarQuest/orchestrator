import pytest
from pydantic import ValidationError

from orchestrator.api.schemas import CostActualsCommand
from tests.contract.test_cost_actuals_contract import golden_cost_actuals


def test_schema_accepts_the_golden_fixture():
    command = CostActualsCommand.model_validate(golden_cost_actuals())
    assert command.cost_known is True
    assert command.llm_calls == 37
    assert command.cost_usd == 9.14


def test_cost_known_true_requires_all_numerics():
    payload = golden_cost_actuals() | {"llm_calls": None}
    with pytest.raises(ValidationError):
        CostActualsCommand.model_validate(payload)


def test_cost_known_false_requires_all_numerics_null():
    unknown = {
        "idempotency_key": "k",
        "attempt": 2,
        "lease_token": "t",
        "cost_known": False,
        "llm_calls": None,
        "num_turns": None,
        "input_tokens": None,
        "output_tokens": None,
        "cost_usd": None,
    }
    assert CostActualsCommand.model_validate(unknown).cost_known is False
    with pytest.raises(ValidationError):
        CostActualsCommand.model_validate(unknown | {"llm_calls": 5})


def test_negative_values_rejected():
    with pytest.raises(ValidationError):
        CostActualsCommand.model_validate(golden_cost_actuals() | {"input_tokens": -1})
