import uuid
from typing import Any, Literal

from orchestrator.kernel.evidence_types import (
    NAMED_CHECK_MAX_ASSERTION_NAME_LENGTH,
    NAMED_CHECK_MAX_ASSERTION_VALUE_LENGTH,
    NAMED_CHECK_MAX_ASSERTIONS,
    NAMED_CHECK_MAX_CHECK_NAME_LENGTH,
    NAMED_CHECK_MAX_HEAD_SHA_LENGTH,
    NAMED_CHECK_MAX_INTEGER_ABS,
    NAMED_CHECK_MAX_REFERENCE_LENGTH,
    NAMED_CHECK_MAX_REPOSITORY_LENGTH,
    NAMED_CHECK_MAX_RUN_ID_LENGTH,
    VERIFIER_NAMED_CHECK_EVIDENCE_TYPE,
)
from orchestrator.persistence.models import Evidence, PackageAcceptanceCriterion

EvaluationStatus = Literal["passed", "failed", "failed_closed", "judgment_required"]

DETERMINISTIC_TYPES = frozenset(
    {
        "test",
        "tests",
        "pytest",
        "runner.verification",
        "gate.summary",
        "security.scan",
        "security_scan",
        "github.checks",
        "github.check_run",
        "health.probe",
        "production.health",
        "release.deployment_observed",
        "production.route_presence",
        "production.auth_behavior",
        "production.dispatch_posture",
        "infra_lane.final",
        # A package evidence type. Deterministic, but special-cased above the EVALUATORS lookup
        # (it is deterministic only when the evidence is verifier-owned named-check evidence), so
        # it carries no EVALUATORS entry and is declared in SPECIAL_CASE_TYPES.
        "automated_check",
    }
)
# The package (intent-packages) evidence vocabulary that maps to human review. Naming these
# explicitly is what makes a typo distinguishable from a real judgment type: today they land on
# `judgment_required` by falling off the end of DETERMINISTIC_TYPES, which is indistinguishable
# from a misspelling. `automated_check` is NOT here -- it is deterministic (above).
JUDGMENT_TYPES = frozenset(
    {
        "human.review",
        "code_review",
        "judgment",
        "manual",
        "automated_test",
        "human_review",
        "external_attestation",
        "observation",
    }
)
# DETERMINISTIC_TYPES members that resolve WITHOUT an EVALUATORS entry -- each handled by a
# dedicated branch in evaluate_criterion. Assertion D (in tests/services/
# test_criterion_evidence_vocabulary.py) pins DETERMINISTIC_TYPES == EVALUATORS keys | these, so
# a new deterministic type with no evaluator and no declared special case reds the suite -- which
# is what makes promoting a type to deterministic later safe to attempt.
SPECIAL_CASE_TYPES = frozenset({"infra_lane.final", "automated_check"})
# The criterion `evidence_type` vocabulary a package may legally declare, validated at intake. A
# criterion type outside this set is a NAMED error at the gate instead of a silent
# `judgment_required` at verify time.
SUPPORTED_CRITERION_EVIDENCE_TYPES = DETERMINISTIC_TYPES | JUDGMENT_TYPES

CriterionFloor = Literal["human", "deterministic_permitted"]

# The MINIMUM scrutiny a criterion's declared evidence_type demands (WS-P2.17, ruling R1).
# Evidence may satisfy AT or ABOVE this floor and never below it. This is a separate concept from
# DETERMINISTIC_TYPES, which describes what the verifier knows how to READ; the floor describes what
# the package author is entitled to INSIST ON. Keeping them separate is what lets `automated_test`
# become machine-evaluable without being added to DETERMINISTIC_TYPES -- an addition CLAUDE.md
# records as halting the factory.
HUMAN_FLOOR_TYPES: frozenset[str] = JUDGMENT_TYPES - {"automated_test"}
DETERMINISTIC_PERMITTED_TYPES: frozenset[str] = DETERMINISTIC_TYPES | {"automated_test"}


def floor_for(evidence_type: str) -> CriterionFloor:
    """The minimum scrutiny a criterion type demands. Unknown types floor to human (fail closed)."""
    normalized = evidence_type.strip().lower()
    if normalized in DETERMINISTIC_PERMITTED_TYPES:
        return "deterministic_permitted"
    return "human"


PASS_VALUES = frozenset({"pass", "passed", "success", "successful", "ok", "green"})
FAIL_VALUES = frozenset(
    {
        "fail",
        "failed",
        "failure",
        "error",
        "red",
        "cancelled",
        "canceled",
        "timed_out",
        "action_required",
    }
)
CHECK_PASS_CONCLUSIONS = frozenset({"success", "neutral", "skipped"})
CHECK_FAIL_CONCLUSIONS = frozenset({"failure", "cancelled", "timed_out", "action_required"})


def evaluate_criterion(
    criterion: PackageAcceptanceCriterion,
    evidence: Evidence | None,
) -> tuple[EvaluationStatus, str | None, str]:
    evidence_type = criterion.evidence_type.strip().lower()
    if evidence_type == "automated_check":
        if evidence is None or evidence.evidence_type != VERIFIER_NAMED_CHECK_EVIDENCE_TYPE:
            return (
                "judgment_required",
                None,
                "automated_check requires verifier named-check evidence",
            )
        return _named_check_result(evidence)
    if floor_for(evidence_type) == "human":
        return ("judgment_required", None, f"{criterion.evidence_type} requires review")
    if evidence is None:
        return ("judgment_required", None, "no evidence has been recorded for this criterion")
    if not isinstance(evidence.payload, dict):
        return ("judgment_required", None, "evidence payload is not machine-readable")
    arriving_type = evidence.evidence_type.strip().lower()
    evaluator = EVALUATORS.get(arriving_type)
    if evaluator is not None:
        return evaluator(evidence.payload)
    if arriving_type == "infra_lane.final":
        return _infra_lane_result(evidence)
    return ("judgment_required", None, f"{evidence.evidence_type} has no deterministic evaluator")


def _named_check_result(evidence: Evidence) -> tuple[EvaluationStatus, str, str]:
    payload = evidence.payload
    if not isinstance(payload, dict):
        return ("failed_closed", "failed", "named-check payload is malformed")
    repository = _bounded_text(payload.get("repository"), NAMED_CHECK_MAX_REPOSITORY_LENGTH)
    pr_number = payload.get("pr_number")
    pr_url = _bounded_text(payload.get("pr_url"), NAMED_CHECK_MAX_REFERENCE_LENGTH)
    head_sha = _bounded_text(payload.get("head_sha"), NAMED_CHECK_MAX_HEAD_SHA_LENGTH, minimum=7)
    check_name = _bounded_text(payload.get("check_name"), NAMED_CHECK_MAX_CHECK_NAME_LENGTH)
    run_id = _bounded_text(payload.get("run_id"), NAMED_CHECK_MAX_RUN_ID_LENGTH)
    run_url = _bounded_text(payload.get("run_url"), NAMED_CHECK_MAX_REFERENCE_LENGTH)
    dispatch_id = _uuid_text(payload.get("dispatch_id"))
    conclusion = _string_value(payload.get("conclusion"))
    if (
        repository is None
        or not isinstance(pr_number, int)
        or isinstance(pr_number, bool)
        or pr_number <= 0
        or pr_url != f"https://github.com/{repository}/pull/{pr_number}"
        or head_sha is None
        or check_name is None
        or run_id is None
        or run_url is None
        or dispatch_id is None
        or evidence.stable_ref != run_url
        or evidence.source_revision != head_sha
    ):
        return ("failed_closed", "failed", "named-check identity is malformed")
    assertions = payload.get("assertions")
    if (
        not isinstance(assertions, list)
        or not assertions
        or len(assertions) > NAMED_CHECK_MAX_ASSERTIONS
    ):
        return ("failed_closed", "failed", "named-check assertions are missing")
    names: set[str] = set()
    for assertion in assertions:
        if not isinstance(assertion, dict):
            return ("failed_closed", "failed", "named-check assertions are malformed")
        name = _bounded_text(assertion.get("name"), NAMED_CHECK_MAX_ASSERTION_NAME_LENGTH)
        expected = assertion.get("expected")
        observed = assertion.get("observed")
        if (
            name is None
            or name in names
            or not _scalar(expected)
            or not _scalar(observed)
            or type(expected) is not type(observed)
            or expected != observed
        ):
            return ("failed_closed", "failed", "named-check assertion mismatch")
        names.add(name)
    if conclusion == "success":
        return ("passed", "passed", "named check and assertions passed")
    if conclusion in CHECK_FAIL_CONCLUSIONS:
        return ("failed", "failed", f"named check conclusion is {conclusion}")
    return ("failed_closed", "failed", "named check conclusion does not pass")


def _status_result(payload: dict[str, Any]) -> tuple[EvaluationStatus, str, str]:
    for key in ("status", "conclusion", "result"):
        value = _string_value(payload.get(key))
        if value in PASS_VALUES:
            return ("passed", "passed", f"{key} is {value}")
        if value in FAIL_VALUES:
            return ("failed", "failed", f"{key} is {value}")
    exit_code = payload.get("exit_code")
    if isinstance(exit_code, int):
        if exit_code == 0:
            return ("passed", "passed", "exit_code is 0")
        return ("failed", "failed", f"exit_code is {exit_code}")
    return ("failed_closed", "failed", "no deterministic result field found")


def _security_scan_result(payload: dict[str, Any]) -> tuple[EvaluationStatus, str, str]:
    block_count = _count_value(payload, "block", "BLOCK", "blocks")
    warn_count = _count_value(payload, "warn", "WARN", "warnings")
    if block_count is None or warn_count is None:
        return ("failed_closed", "failed", "security scan counts are missing")
    if block_count == 0 and warn_count == 0:
        return ("passed", "passed", "security scan has 0 BLOCK and 0 WARN findings")
    return ("failed", "failed", f"security scan has {block_count} BLOCK and {warn_count} WARN")


def _github_checks_result(payload: dict[str, Any]) -> tuple[EvaluationStatus, str, str]:
    checks = payload.get("checks")
    if checks is None:
        checks = payload.get("check_runs")
    if not isinstance(checks, list) or not checks:
        conclusion = _string_value(payload.get("conclusion"))
        if conclusion in CHECK_PASS_CONCLUSIONS:
            return ("passed", "passed", f"conclusion is {conclusion}")
        if conclusion in CHECK_FAIL_CONCLUSIONS:
            return ("failed", "failed", f"conclusion is {conclusion}")
        return ("failed_closed", "failed", "github check conclusions are missing")

    conclusions: list[str] = []
    for check in checks:
        if not isinstance(check, dict):
            return ("failed_closed", "failed", "github check payload is malformed")
        conclusion = _string_value(check.get("conclusion"))
        if conclusion is None:
            return ("failed_closed", "failed", "github check conclusion is missing")
        conclusions.append(conclusion)
    if all(conclusion in CHECK_PASS_CONCLUSIONS for conclusion in conclusions):
        return ("passed", "passed", "all github checks passed")
    if any(conclusion in CHECK_FAIL_CONCLUSIONS for conclusion in conclusions):
        return ("failed", "failed", "one or more github checks failed")
    return ("failed_closed", "failed", "github check conclusion is unrecognized")


def _health_probe_result(payload: dict[str, Any]) -> tuple[EvaluationStatus, str, str]:
    probes = payload.get("probes")
    statuses: list[int] = []
    if isinstance(probes, list):
        for probe in probes:
            if not isinstance(probe, dict):
                return ("failed_closed", "failed", "health probe payload is malformed")
            status_code = probe.get("status_code", probe.get("http_status"))
            if not isinstance(status_code, int):
                return ("failed_closed", "failed", "health probe status is missing")
            statuses.append(status_code)
    else:
        status_code = payload.get("status_code", payload.get("http_status"))
        if isinstance(status_code, int):
            statuses.append(status_code)
    if not statuses:
        return ("failed_closed", "failed", "health probe status is missing")
    if all(200 <= status < 300 for status in statuses):
        return ("passed", "passed", "all health probes returned 2xx")
    return ("failed", "failed", "one or more health probes returned non-2xx")


def _infra_lane_result(evidence: Evidence) -> tuple[EvaluationStatus, str, str]:
    payload = evidence.payload
    if not isinstance(payload, dict):
        return ("failed_closed", "failed", "infra lane payload is missing")
    status = _string_value(payload.get("status"))
    final_ref = payload.get("final_evidence_ref") or evidence.stable_ref
    if status == "completed" and isinstance(final_ref, str) and final_ref.strip():
        return ("passed", "passed", "infra lane completed with final evidence")
    if status in {"failed", "cancelled", "canceled"}:
        return ("failed", "failed", f"infra lane status is {status}")
    return ("failed_closed", "failed", "infra lane final evidence is insufficient")


def _deployment_observed_result(payload: dict[str, Any]) -> tuple[EvaluationStatus, str, str]:
    binding_id = _string_value(payload.get("binding_id"))
    release_digest = _string_value(payload.get("release_artifact_digest"))
    observed_digest = _string_value(payload.get("observed_artifact_digest"))
    if binding_id is None or release_digest is None or observed_digest is None:
        return ("failed_closed", "failed", "deployment observation digest facts are missing")
    if release_digest == observed_digest:
        return ("passed", "passed", "observed digest matches release binding")
    return ("failed", "failed", "observed digest does not match release binding")


def _route_presence_result(payload: dict[str, Any]) -> tuple[EvaluationStatus, str, str]:
    routes = payload.get("routes")
    if not isinstance(routes, list) or not routes:
        return ("failed_closed", "failed", "route presence summary is missing")
    for route in routes:
        if not isinstance(route, dict):
            return ("failed_closed", "failed", "route presence payload is malformed")
        path = route.get("path")
        present = route.get("present")
        if not isinstance(path, str) or not path.strip() or not isinstance(present, bool):
            return ("failed_closed", "failed", "route presence payload is malformed")
        if not present:
            return ("failed", "failed", f"route {path} is missing")
    return ("passed", "passed", "all required routes are present")


def _auth_behavior_result(payload: dict[str, Any]) -> tuple[EvaluationStatus, str, str]:
    missing_status = payload.get("missing_m2m_status")
    configured_status = payload.get("configured_m2m_status")
    if missing_status != 401:
        if isinstance(missing_status, int):
            return ("failed", "failed", f"missing M2M returned {missing_status}")
        return ("failed_closed", "failed", "missing M2M status is missing")
    if configured_status is not None and configured_status != 200:
        if isinstance(configured_status, int):
            return ("failed", "failed", f"configured M2M returned {configured_status}")
        return ("failed_closed", "failed", "configured M2M status is malformed")
    return ("passed", "passed", "M2M behavior matches expected posture")


def _dispatch_posture_result(payload: dict[str, Any]) -> tuple[EvaluationStatus, str, str]:
    dispatch_enabled = payload.get("dispatch_enabled")
    if not isinstance(dispatch_enabled, bool):
        return ("failed_closed", "failed", "dispatch posture is missing")
    if dispatch_enabled:
        return ("failed", "failed", "dispatch automation is enabled")
    return ("passed", "passed", "dispatch automation is disabled")


# not-a-vocabulary: the consumer-side resolver map, keyed by DETERMINISTIC_TYPES members (which
# ARE a registered vocabulary). Assertion D pins its keys to DETERMINISTIC_TYPES; it is not a
# second, independent vocabulary.
EVALUATORS = {
    "test": _status_result,
    "tests": _status_result,
    "pytest": _status_result,
    "runner.verification": _status_result,
    "gate.summary": _status_result,
    "security.scan": _security_scan_result,
    "security_scan": _security_scan_result,
    "github.checks": _github_checks_result,
    "github.check_run": _github_checks_result,
    "health.probe": _health_probe_result,
    "production.health": _health_probe_result,
    "release.deployment_observed": _deployment_observed_result,
    "production.route_presence": _route_presence_result,
    "production.auth_behavior": _auth_behavior_result,
    "production.dispatch_posture": _dispatch_posture_result,
}


def _count_value(payload: dict[str, Any], *keys: str) -> int | None:
    summary = payload.get("summary")
    nested: dict[str, Any] = {}
    if isinstance(summary, dict):
        by_severity = summary.get("by_severity")
        if isinstance(by_severity, dict):
            nested = by_severity
    for key in keys:
        value = payload.get(key)
        if isinstance(value, int):
            return value
        nested_value = nested.get(key)
        if isinstance(nested_value, int):
            return nested_value
    return None


def _string_value(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip().lower()


def _bounded_text(value: object, limit: int, *, minimum: int = 1) -> str | None:
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= limit:
        return None
    return value


def _uuid_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


def _scalar(value: object) -> bool:
    if isinstance(value, str):
        return bool(value.strip()) and len(value) <= NAMED_CHECK_MAX_ASSERTION_VALUE_LENGTH
    return isinstance(value, bool) or (
        isinstance(value, int)
        and not isinstance(value, bool)
        and abs(value) <= NAMED_CHECK_MAX_INTEGER_ABS
    )
