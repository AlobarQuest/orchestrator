"""The offline half of the gate that holds derived deploy criteria to change-manager's policy.

`scripts/check_rollout_policy_ratification.py` reads change-manager's `app/deploy_policy.py` over
the network and compares its current version against what this repository derives. Everything the
script DECIDES with is exercised here against a synthetic policy module of the same shape, built
from this repository's own derivation so that the agreeing case is agreement by construction and
each control moves exactly one side.
"""

from __future__ import annotations

import pytest

from change_proposer.criteria import acceptance_criteria, rollback_for
from deploy_watcher.workflows import attestation_for
from scripts import check_rollout_policy_ratification as check

BRAIN = "alobarquest/brain"
CHANGE_MANAGER = "alobarquest/change-manager"
BRAIN_BLOB = "7cf6ca2d2a508b1643cdb5ac0d5390357f397d54"
CM_BLOB = "a47d4b187c93971a5b5915ce87a963bd4ef35e30"


def _derived(repository: str, blob: str) -> tuple[str, ...]:
    return acceptance_criteria(repository, attestation_for(blob))


def _rollback(repository: str) -> tuple[tuple[str, ...], str]:
    plan = rollback_for(repository)
    return plan.steps, plan.target


def _policy_source(
    *,
    brain_criteria: tuple[str, ...] | None = None,
    brain_blob: str = BRAIN_BLOB,
    brain_rollback: tuple[tuple[str, ...], str] | None = None,
    extra_import: str = "",
) -> str:
    """A module shaped like change-manager's: dataclasses, a registry, and `current()`."""
    brain_criteria = brain_criteria or _derived(BRAIN, BRAIN_BLOB)
    brain_steps, brain_target = brain_rollback or _rollback(BRAIN)
    cm_steps, cm_target = _rollback(CHANGE_MANAGER)
    return f"""
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final
{extra_import}

@dataclass(frozen=True)
class WorkflowPin:
    path: str
    blob_sha: str

@dataclass(frozen=True)
class LandingConditions:
    rollout_workflows: Mapping[str, WorkflowPin] = field(default_factory=dict)

@dataclass(frozen=True)
class Rollback:
    steps: tuple[str, ...]
    target: str

    def as_stored(self) -> dict:
        return {{"steps": list(self.steps), "target": self.target}}

@dataclass(frozen=True)
class DeployPolicy:
    version: int
    acceptance_criteria: Mapping[str, tuple[str, ...]]
    rollback_plans: Mapping[str, Rollback]
    landing: LandingConditions

V9: Final = DeployPolicy(
    version=9,
    acceptance_criteria={{
        {CHANGE_MANAGER!r}: {_derived(CHANGE_MANAGER, CM_BLOB)!r},
        {BRAIN!r}: {brain_criteria!r},
    }},
    rollback_plans={{
        {CHANGE_MANAGER!r}: Rollback(steps={cm_steps!r}, target={cm_target!r}),
        {BRAIN!r}: Rollback(steps={brain_steps!r}, target={brain_target!r}),
    }},
    landing=LandingConditions(rollout_workflows={{
        {CHANGE_MANAGER!r}: WorkflowPin(path="deploy.yml", blob_sha={CM_BLOB!r}),
        {BRAIN!r}: WorkflowPin(path="ci.yml", blob_sha={brain_blob!r}),
    }}),
)

def current() -> DeployPolicy:
    return V9
"""


def _run(source: str) -> int:
    return check.main(fetcher=lambda repo, path, ref: source)


def test_the_comparison_passes_when_the_two_sides_agree(capsys) -> None:
    assert _run(_policy_source()) == 0
    assert "PASS" in capsys.readouterr().out


def test_it_fires_when_the_ratified_criteria_move_alone(capsys) -> None:
    """The 2026-09-27 shape: one side's literal moved and the other's did not."""
    ratified = (*_derived(BRAIN, BRAIN_BLOB)[:1], "a criterion this repository never derived")

    assert _run(_policy_source(brain_criteria=ratified)) == 1
    assert "acceptance criteria differ" in capsys.readouterr().err


def test_it_fires_when_only_the_rollback_plan_moves(capsys) -> None:
    """`rollback_plan` is byte-compared too, and was pinned on change-manager's side alone."""
    steps, target = _rollback(BRAIN)

    assert _run(_policy_source(brain_rollback=(steps[:1], target))) == 1
    assert "rollback plan differs" in capsys.readouterr().err


def test_it_fires_when_the_pinned_revision_is_not_transcribed_here(capsys) -> None:
    """A policy pinning bytes nobody here classified derives nothing to compare with."""
    assert _run(_policy_source(brain_blob="0" * 40)) == 1
    assert "not transcribed" in capsys.readouterr().err


def test_it_compares_the_revision_the_policy_pins_not_the_newest_transcription() -> None:
    """Keyed on change-manager's pin: ratifying the OLDER brain revision with its own criteria
    agrees, because the transcription registry still holds that revision."""
    older = "c5c088719cd340f0071b875c6a82439292ed8756"

    source = _policy_source(brain_blob=older, brain_criteria=_derived(BRAIN, older))

    assert _run(source) == 0


def test_a_new_import_in_the_far_module_refuses_rather_than_executing() -> None:
    """The far module is executed; an import outside the allowlist changes what that means."""
    assert _run(_policy_source(extra_import="import os")) == 1


def test_the_far_module_is_not_left_importable() -> None:
    import sys

    check.load_policy_module(_policy_source())

    assert check.MODULE_NAME not in sys.modules


@pytest.mark.parametrize(
    "source",
    ["def current(:\n", "current = 1\n", "def current():\n    raise RuntimeError('boom')\n"],
    ids=["syntax-error", "not-callable", "raises-when-called"],
)
def test_an_unusable_far_module_is_unresolvable_rather_than_agreement(source: str) -> None:
    assert _run(source) == 1


def test_an_unreadable_far_module_is_unresolvable() -> None:
    def boom(repo, path, ref):
        raise check.Unresolvable("simulated")

    assert check.main(fetcher=boom) == 1


def test_the_gate_runs_the_script_this_module_tests() -> None:
    """A check nothing invokes is the defect it guards against."""
    from pathlib import Path

    workflows = [
        path.name
        for path in Path(".github/workflows").glob("*.yml")
        if "check_rollout_policy_ratification" in path.read_text(encoding="utf-8")
    ]

    assert workflows == ["quality.yml"]


def test_the_token_is_withheld_while_far_code_runs_and_restored_after(monkeypatch) -> None:
    """The far module executes; it must not see the token, and the caller must not lose it."""
    import os

    monkeypatch.setenv("GITHUB_TOKEN", "sentinel")
    probe = _policy_source().replace(
        "def current() -> DeployPolicy:\n    return V9",
        "def current() -> DeployPolicy:\n"
        "    assert 'GITHUB_TOKEN' not in __import__('os').environ\n"
        "    return V9",
    )

    assert _run(probe) == 0
    assert os.environ["GITHUB_TOKEN"] == "sentinel"
