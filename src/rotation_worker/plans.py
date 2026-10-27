"""A credential type's plan, derived from what its provider can do and its registry row
(ADR-0055 amendment 15, increment 1).

A type declares only which steps its provider does by machine (`Capabilities`). Everything else in
the plan follows from the credential's destinations:

* a hosted type learns the new value at `host`, before anything probes it;
* `distribute` runs when a destination other than the keeper is written: an Actions secret, a
  held copy, or a secret the type's store reads back;
* when a person revokes, every consumer is verified in a unit of its own straight before the
  revoke, and the confirmation is a unit of its own after it.

A derived plan has no person attesting consumers, since a person's acts are only minting and
revoking: a `runtime-fetch` reader whose registry row names no read is refused before the claim
(`checks._check_runtime_fetch_verifiable`) until its row names one.

Unit names and criterion numbering are fixed by that shape, so the same inputs always give the
same plan, and the approved package's criteria say which plan it was approved with
(`select_plan`).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from rotation_worker.contract import (
    ACTIONS_KIND,
    HELD_KIND,
    Actor,
    ContractError,
    HumanAct,
    Plan,
    Step,
    StepKind,
    UnitPlan,
    validate_plan,
)
from rotation_worker.destinations import parse

M, H = Actor.MACHINE, Actor.HUMAN
# A package criterion's evidence names the step it covers; the verifier reads the same text
# (`rotation_verification._CRITERION` in the orchestrator).
_CRITERION: Final = re.compile(
    r"^external: the (?:(?P<unit>\w+) unit's )?(?P<step>\w+) step's rotation-step/1 evidence, "
    r"filed by the rotation executor\.$"
)


@dataclass(frozen=True)
class Capabilities:
    """Which steps a type's provider does by machine, and whether it is a hosted service."""

    machine_mint: bool
    machine_revoke: bool
    hosted: bool = False


def derive(
    capabilities: Capabilities,
    destinations: Sequence[str],
    keeper: str,
    store_kind: str,
) -> Plan:
    """The plan for one credential of a type."""
    distributes = any(
        destination != keeper
        and parse(destination)["kind"] in {ACTIONS_KIND, HELD_KIND, store_kind}
        for destination in destinations
    )
    switch = [
        *([StepKind.HOST] if capabilities.hosted else []),
        StepKind.VERIFY_NEW,
        StepKind.STORE,
        *([StepKind.DISTRIBUTE] if distributes else []),
        # A machine retire re-verifies every consumer in its own unit; until then a partial
        # count is filed here. A person's revoke gets a unit of its own to verify first.
        *([StepKind.VERIFY_CONSUMERS] if capabilities.machine_revoke else []),
    ]
    segments: list[tuple[str, list[StepKind]] | HumanAct] = []
    if capabilities.machine_mint:
        segments.append(("mint", [StepKind.MINT, StepKind.STAGE, *switch]))
    else:
        segments.append(HumanAct("mint", (Step(StepKind.MINT, H), Step(StepKind.STAGE, H))))
        segments.append(("store", switch))
    if capabilities.machine_revoke:
        segments.append(
            ("retire", [StepKind.VERIFY_CONSUMERS, StepKind.RETIRE_OLD, StepKind.CONFIRM_DEAD])
        )
    else:
        segments.append(("verify", [StepKind.VERIFY_CONSUMERS]))
        segments.append(HumanAct("revoke", (Step(StepKind.RETIRE_OLD, H),)))
        segments.append(("confirm", [StepKind.CONFIRM_DEAD]))
    plan = Plan(tuple(_numbered(segments)))
    validate_plan(plan)
    return plan


def _numbered(
    segments: Sequence[tuple[str, list[StepKind]] | HumanAct],
) -> list[UnitPlan | HumanAct]:
    numbered: list[UnitPlan | HumanAct] = []
    criterion = 0
    for segment in segments:
        if isinstance(segment, HumanAct):
            numbered.append(segment)
            continue
        name, kinds = segment
        steps = []
        for kind in kinds:
            criterion += 1
            steps.append(Step(kind, M, f"AC-{criterion:03d}"))
        numbered.append(UnitPlan(name, tuple(steps)))
    return numbered


def criteria_steps(plan: Plan) -> dict[str, tuple[str, StepKind]]:
    """Each machine step's criterion, with the unit that holds it and the step it covers."""
    return {
        step.ac_id: (segment.name, step.kind)
        for segment in plan.segments
        if isinstance(segment, UnitPlan)
        for step in segment.steps
        if step.ac_id is not None
    }


# A criterion's step, and its unit when the evidence names one.
Covered = dict[str, tuple[str | None, StepKind]]


def packaged_steps(acceptance: object) -> Covered | None:
    """What an approved package's criteria cover, by the step (and unit, when named) each one's
    evidence names; `None` when any criterion isn't a rotation step's, or an id is missing or
    repeated."""
    if not isinstance(acceptance, list):
        return None
    covered: Covered = {}
    for criterion in acceptance:
        if not isinstance(criterion, Mapping):
            return None
        ac_id = criterion.get("id")
        match = _CRITERION.fullmatch(str(criterion.get("evidence", "")))
        if not isinstance(ac_id, str) or ac_id in covered or match is None:
            return None
        if match["step"] not in StepKind._value2member_map_:
            return None
        covered[ac_id] = (match["unit"], StepKind(match["step"]))
    return covered


def _describes(covered: Covered | None, plan: Plan) -> bool:
    steps = criteria_steps(plan)
    if covered is None or covered.keys() != steps.keys():
        return False
    return all(
        kind == steps[ac_id][1] and unit in (None, steps[ac_id][0])
        for ac_id, (unit, kind) in covered.items()
    )


def select_plan(candidates: Sequence[Plan], acceptance: Any) -> Plan:
    """The candidate whose criteria the approved package states exactly.

    The package pins its plan: it was approved with one criterion per machine step, so the plan
    that runs is the one those criteria describe, step for step and, where a criterion names its
    unit, unit for unit. A package that describes none is refused before the claim.
    """
    covered = packaged_steps(acceptance)
    for plan in candidates:
        if _describes(covered, plan):
            return plan
    raise ContractError("the approved package's criteria describe no plan this type runs")
