VERIFIER_EVIDENCE_PREFIX = "verifier."
VERIFIER_NAMED_CHECK_EVIDENCE_TYPE = "verifier.github.named_check"

# Evidence the ORCHESTRATOR OBSERVED FOR ITSELF, as opposed to evidence a worker recorded about
# its own run. ADR-0020 rests on the difference: "resolved deterministically from OBSERVED
# evidence, with no human adjudication." The verifier's evaluator dispatches on the ARRIVING row's
# type, so a `pytest` row a worker wrote saying it passed resolves deterministically and the
# verifier records `passed` -- verifier-decided, and nobody checked. Tolerable while a human saw
# the result at the merge; not tolerable once the factory lands its own work.
#
# NAMED, and with one member on purpose. A second producer must be a deliberate addition here
# rather than a widening nobody reviewed -- the set is the review surface. Today the only evidence
# this estate holds that it saw for itself is the named check WS-P2.20 reads from GitHub's own
# workflow jobs.
#
# Built from the constant above rather than respelling it: the string has exactly one definition
# in this repository and this must not become a second. Membership is tested by set algebra
# (`Evidence.evidence_type.in_(...)`), which is why this is a derived collection rather than a
# vocabulary the cross-boundary scanner should register.
OBSERVED_EVIDENCE_TYPES = frozenset({VERIFIER_NAMED_CHECK_EVIDENCE_TYPE})

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
