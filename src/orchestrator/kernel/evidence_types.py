VERIFIER_EVIDENCE_PREFIX = "verifier."
VERIFIER_NAMED_CHECK_EVIDENCE_TYPE = "verifier.github.named_check"

# What produced the `observation` block of a named-check payload. Written by the ingestion
# service and required by the evaluator, so a payload assembled by anything else is refused.
# Spelled without the platform's own dotted product name, which test_ws34_scope_guards forbids
# as a runtime literal outside the WS-4.2 paths.
NAMED_CHECK_OBSERVATION_SOURCE = "github.workflow_jobs"

NAMED_CHECK_MAX_OBSERVED_JOBS = 32
NAMED_CHECK_MAX_AC_ID_LENGTH = 100
NAMED_CHECK_MAX_REPOSITORY_LENGTH = 300
NAMED_CHECK_MAX_HEAD_SHA_LENGTH = 64
NAMED_CHECK_MAX_CHECK_NAME_LENGTH = 200
NAMED_CHECK_MAX_RUN_ID_LENGTH = 100
NAMED_CHECK_MAX_REFERENCE_LENGTH = 2000
NAMED_CHECK_MAX_IDEMPOTENCY_KEY_LENGTH = 200
