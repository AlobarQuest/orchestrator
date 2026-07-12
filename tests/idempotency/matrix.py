"""AC-007: the duplicate-delivery coverage matrix.

Executable, so it cannot rot. `test_matrix.py` gates it two ways: every ingress POST route must
have a row here, and every row must name a test that actually exists.

Three mechanisms are in play across the codebase:
"""

from dataclasses import dataclass

# Postgres advisory xact lock on the key + unique idempotency_key + replay-equality.
ADVISORY_LOCK = "pg_advisory_xact_lock + unique idempotency_key + replay-equality"
# No advisory lock: the WorkUnit row lock (SELECT ... FOR UPDATE) serializes writers, and the
# loser finds the Event by its globally-unique key and replays it. This asymmetry is REAL and is
# proven under a concurrent double-submit, not merely a sequential one.
ROW_LOCK = "unique Event.idempotency_key + WorkUnit row lock (with_for_update), NO advisory lock"
# The trickiest replay surface in the repo: reclaim writes three events under two derived keys.
COMPOUND_KEY = "compound :failed/:ready Event keys + WorkUnit row lock + error replay"


@dataclass(frozen=True)
class MatrixRow:
    ingress: str
    route: str | None
    mechanism: str
    test: str


COVERAGE_MATRIX: tuple[MatrixRow, ...] = (
    MatrixRow(
        "lifecycle transition",
        "/api/v1/work-units/{unit_id}/commands/{command}",
        ROW_LOCK,
        "tests/idempotency/test_lifecycle_idempotency.py::test_a_concurrent_duplicate_transition_writes_one_event",
    ),
    MatrixRow(
        "claim",
        "/api/v1/work-units/{unit_id}/claim",
        ROW_LOCK,
        "tests/idempotency/test_claim_family_idempotency.py::test_a_duplicate_claim_replays_and_withholds_the_lease_token",
    ),
    MatrixRow(
        "renew",
        "/api/v1/work-units/{unit_id}/renew",
        ROW_LOCK,
        "tests/idempotency/test_claim_family_idempotency.py::test_a_duplicate_renew_replays_and_withholds_the_lease_token",
    ),
    MatrixRow(
        "reclaim expired claim",
        "/api/v1/work-units/{unit_id}/reclaim-expired-claim",
        COMPOUND_KEY,
        "tests/idempotency/test_reclaim_idempotency.py::test_a_duplicate_reclaim_writes_one_failed_and_one_ready_event",
    ),
    MatrixRow(
        "retry authorization",
        "/api/v1/work-units/{unit_id}/retry-authorization",
        ROW_LOCK,
        "tests/services/test_package_registration.py::test_authority_approval_idempotency_binds_expected_version",
    ),
    MatrixRow(
        "worker evidence",
        "/api/v1/work-units/{unit_id}/evidence",
        ADVISORY_LOCK,
        "tests/services/test_evidence.py::test_exact_evidence_replay_returns_original_and_conflicting_reuse_fails",
    ),
    MatrixRow(
        "verifier evidence",
        "/api/v1/work-units/{unit_id}/verify",
        ADVISORY_LOCK,
        "tests/services/test_verifier.py::test_verifier_replay_does_not_duplicate_rows",
    ),
    MatrixRow(
        "adjudication",
        "/api/v1/work-units/{unit_id}/adjudications",
        ADVISORY_LOCK,
        "tests/services/test_adjudications.py::test_adjudication_idempotency_is_exact",
    ),
    MatrixRow(
        "observation",
        "/api/v1/observations",
        ADVISORY_LOCK,
        "tests/services/test_observations.py::test_replay_is_idempotent_and_conflict_rejects_changed_facts",
    ),
    MatrixRow(
        "deployment observation",
        "/api/v1/release-artifacts/{binding_id}/deployment-observations",
        ADVISORY_LOCK,
        "tests/services/test_deployment_observations.py::test_replay_is_idempotent_and_conflict_rejects_changed_facts",
    ),
    MatrixRow(
        "dispatch",
        "/api/v1/work-units/{unit_id}/dispatch",
        "unique DispatchRecord.idempotency_key + (unit, runner_attempt) guard",
        "tests/services/test_dispatch.py::test_dispatch_replay_is_idempotent_against_the_per_unit_repository",
    ),
    MatrixRow(
        "release artifact binding",
        "/api/v1/work-units/{unit_id}/release-artifacts",
        ADVISORY_LOCK,
        "tests/services/test_release_artifacts.py::test_release_artifact_replay_is_idempotent_and_conflict_rejects_digest_change",
    ),
    MatrixRow(
        "infra-lane link",
        "/api/v1/work-units/{unit_id}/infra-lane-links",
        ADVISORY_LOCK,
        "tests/services/test_infra_links.py::test_infra_lane_link_replay_and_conflict",
    ),
    MatrixRow(
        "knowledge promotion proposal",
        "/api/v1/knowledge-promotion-proposals",
        ADVISORY_LOCK,
        "tests/idempotency/test_gap_idempotency.py::test_a_duplicate_knowledge_promotion_writes_one_row",
    ),
    MatrixRow(
        "approval",
        "/api/v1/work-units/{unit_id}/approvals",
        ROW_LOCK,
        "tests/services/test_package_registration.py::test_authority_approval_idempotency_binds_expected_version",
    ),
    MatrixRow(
        "dependency",
        "/api/v1/work-units/{unit_id}/dependencies",
        ROW_LOCK,
        "tests/idempotency/test_gap_idempotency.py::test_a_duplicate_dependency_registration_writes_one_row",
    ),
    MatrixRow(
        "dependency resolution",
        "/api/v1/dependencies/{dependency_id}/resolve",
        ROW_LOCK,
        "tests/idempotency/test_gap_idempotency.py::test_a_duplicate_dependency_resolution_replays",
    ),
    MatrixRow(
        "package intake",
        "/api/v1/package-intakes",
        ROW_LOCK,
        "tests/services/test_package_intake.py::test_package_intake_is_idempotent",
    ),
    MatrixRow(
        "decomposition proposal",
        "/api/v1/package-intakes/{revision_id}/decomposition-proposals",
        ROW_LOCK,
        "tests/services/test_decomposition.py::test_proposal_idempotency_replays_exact_command",
    ),
    MatrixRow(
        "revision registration",
        "/api/v1/revisions",
        ROW_LOCK,
        "tests/services/test_package_registration.py::test_revision_registration_is_idempotent_and_normalized",
    ),
    MatrixRow(
        "work-unit registration",
        "/api/v1/revisions/{revision_id}/work-units",
        ROW_LOCK,
        "tests/services/test_package_registration.py::test_concurrent_identical_first_registration_converges",
    ),
    # --- WS-P2.1 ingress, idempotent from birth -------------------------------------------
    MatrixRow(
        "reconciliation condition (on-ingest)",
        None,
        ADVISORY_LOCK,
        "tests/idempotency/test_wsp21_ingress_idempotency.py::test_a_duplicate_condition_ingest_records_one_row",
    ),
    MatrixRow(
        "reconciliation resolution",
        "/review/reconciliation/conditions/{condition_id}/resolution",
        ADVISORY_LOCK,
        "tests/idempotency/test_wsp21_ingress_idempotency.py::test_a_duplicate_resolution_replays",
    ),
    MatrixRow(
        "reconciliation detect-pass",
        "/api/v1/reconciliation/detect",
        ADVISORY_LOCK,
        "tests/idempotency/test_wsp21_ingress_idempotency.py::test_a_duplicate_detect_pass_records_no_second_condition",
    ),
    MatrixRow(
        "recover evidence",
        "/api/v1/work-units/{unit_id}/attempts/{attempt}/recover-evidence",
        ADVISORY_LOCK,
        "tests/idempotency/test_wsp21_ingress_idempotency.py::test_a_duplicate_recovery_replays_and_never_forks_the_chain",
    ),
    MatrixRow(
        "requeue",
        "/api/v1/work-units/{unit_id}/requeue",
        ROW_LOCK,
        "tests/idempotency/test_wsp21_ingress_idempotency.py::test_a_duplicate_requeue_replays",
    ),
    MatrixRow(
        "pr binding report",
        "/api/v1/work-units/{unit_id}/pr-binding",
        ROW_LOCK,
        "tests/idempotency/test_wsp21_ingress_idempotency.py::test_a_duplicate_pr_binding_report_replays",
    ),
)
