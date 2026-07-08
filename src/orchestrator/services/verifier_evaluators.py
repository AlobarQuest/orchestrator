from typing import Any, Literal

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
        "infra_lane.final",
    }
)
JUDGMENT_TYPES = frozenset({"human.review", "code_review", "judgment", "manual"})
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
    if evidence_type in JUDGMENT_TYPES or evidence_type not in DETERMINISTIC_TYPES:
        return ("judgment_required", None, f"{criterion.evidence_type} requires review")
    if evidence is None:
        return ("failed_closed", "failed", "missing required evidence")
    if not isinstance(evidence.payload, dict):
        return ("failed_closed", "failed", "evidence payload is missing or malformed")

    if evidence_type in {"test", "tests", "pytest", "runner.verification", "gate.summary"}:
        return _status_result(evidence.payload)
    if evidence_type in {"security.scan", "security_scan"}:
        return _security_scan_result(evidence.payload)
    if evidence_type in {"github.checks", "github.check_run"}:
        return _github_checks_result(evidence.payload)
    if evidence_type in {"health.probe", "production.health"}:
        return _health_probe_result(evidence.payload)
    if evidence_type == "infra_lane.final":
        return _infra_lane_result(evidence)
    return ("judgment_required", None, f"{criterion.evidence_type} requires review")


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
