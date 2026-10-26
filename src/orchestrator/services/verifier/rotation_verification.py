"""Whether a rotation step's evidence proves its acceptance criterion (ADR-0055 amendment 14).

A rotation unit's criteria are `external_attestation`, so `evaluate_criterion` hands each one to a
person. For a rotation unit the rotation executor files a `rotation-step/1` payload per step, and
what a person checked at `/review` is a property of that payload: the step is the one the
criterion names, each probe stands beside a refused known-bad control, and the probes answered as
the step requires. This module reads those properties and nothing else. It never decides that a
criterion failed: anything it can't prove stays with a person.

Who filed the row, and on which unit, is the verifier's to check (`verifier.py`); this module sees
only the criterion text, the unit's name in its adapter's plan, and the evidence.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from pydantic import ValidationError

from orchestrator.services.verifier.rotation_evidence import RotationStepEvidence

# The executor's evidence type (`rotation_worker/worker.py`), pinned by a contract test.
ROTATION_STEP_EVIDENCE_TYPE = "rotation.step"
# The only criterion type a rotation step decides: every standing package declares its steps so.
ATTESTED_CRITERION_TYPE = "external_attestation"

# Every standing rotation package states its evidence this way, one criterion per machine step.
_CRITERION = re.compile(
    r"^external: the (?:(?P<unit>\w+) unit's )?(?P<step>\w+) step's rotation-step/1 evidence, "
    r"filed by the rotation executor\.$"
)
# The executor's coverage note (`rotation_worker/contract.py::_verify_consumers`).
_COVERAGE = re.compile(r"^(?P<held>\d+) of (?P<total>\d+) consumers hold or attest the new value")
_BEFORE_REVOKE = "the old value is live before the revoke"
# Steps whose probes are the whole point: each needs at least one, answered as expected.
_PROBED_STEPS = frozenset({"verify_new", "confirm_dead"})  # not-a-vocabulary: step-rule selector


def rotation_step_verdict(
    criterion_type: str,
    criterion_evidence: str,
    rotation_unit: str | None,
    evidence_type: str,
    payload: Mapping[str, object],
) -> str | None:
    """``None`` when the evidence proves the criterion, else why it stays with a person."""
    if criterion_type != ATTESTED_CRITERION_TYPE:
        return f"a {criterion_type} criterion is not decided from rotation evidence"
    if evidence_type != ROTATION_STEP_EVIDENCE_TYPE:
        return f"{evidence_type} is not rotation step evidence"
    if "recovery" in payload:
        return "the evidence was recovered by the orchestrator, not filed by the executor"
    try:
        step = RotationStepEvidence.model_validate(dict(payload))
    except ValidationError:
        return "the evidence is not a rotation-step/1 payload"
    named = _CRITERION.match(" ".join(criterion_evidence.split()))
    if named is None:
        return "the criterion does not name a rotation step"
    if named["step"] != step.step:
        return f"the criterion names {named['step']}, the evidence is {step.step}"
    if named["unit"] is not None and named["unit"] != rotation_unit:
        return f"the criterion names the {named['unit']} unit, the evidence is on {rotation_unit}"
    return _fingerprints(step) or _probes(step) or _step_rule(step)


def _fingerprints(step: RotationStepEvidence) -> str | None:
    which = sorted(print_.which for print_ in step.fingerprints)
    if which != ["new", "old"]:
        return "the evidence needs exactly one old and one new fingerprint"
    old, new = (next(p for p in step.fingerprints if p.which == kind) for kind in ("old", "new"))
    if old.sha256_prefix == new.sha256_prefix:
        return "the old and new fingerprints are the same value"
    return None


def _probes(step: RotationStepEvidence) -> str | None:
    if any(probe.outcome == "indeterminate" for probe in step.probes):
        return "a probe was indeterminate"
    refused = {p.target for p in step.probes if p.subject == "known_bad" and p.outcome == "dead"}
    if any(p.subject != "known_bad" and p.target not in refused for p in step.probes):
        return "a probe has no refused known-bad control on the same target"
    if any(p.subject == "known_bad" and p.outcome != "dead" for p in step.probes):
        return "a known-bad control was accepted"
    return None


def _step_rule(step: RotationStepEvidence) -> str | None:
    subjects = [p for p in step.probes if p.subject != "known_bad"]
    if step.step in _PROBED_STEPS:
        if not subjects:
            return f"{step.step} filed no probe"
        if any(p.outcome != p.expected for p in subjects):
            return f"a {step.step} probe answered other than expected"
    if step.step == "confirm_dead":
        answers = {(p.subject, p.outcome) for p in subjects}
        if not {("old", "dead"), ("new", "live")} <= answers:
            return "confirm_dead needs the old value refused and the new value live"
    if step.step == "retire_old" and any(p.subject != "old" for p in subjects):
        return "retire_old probes only the old value"
    if step.step == "verify_consumers":
        return _coverage(step.note or "")
    return None


def _coverage(note: str) -> str | None:
    counted = _COVERAGE.match(note)
    if counted is None:
        return "the consumer count is unreadable"
    held, total = int(counted["held"]), int(counted["total"])
    if total < 1 or held > total:
        return "the consumer count is inconsistent"
    if _BEFORE_REVOKE in note and held != total:
        # A person revokes next: every consumer must hold the new value first. Before then,
        # partial coverage is by design, and retire_old refuses any consumer still missing.
        return f"{held} of {total} consumers hold the new value before the revoke"
    return None
