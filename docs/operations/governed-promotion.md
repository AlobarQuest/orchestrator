# Governed Promotion

WS-6.2 adds a narrow, review-only path from bounded orchestrator observations to
proposed Brain knowledge.

## Boundary

The orchestrator remains canonical lifecycle truth. Observations, correlations,
promotion proposals, Brain records, event publications, monitors, trackers, CI,
deployment tooling, workers, verifiers, and generated artifacts do not control
work-unit lifecycle state.

Creating a knowledge promotion proposal does not write to Brain. Submitting a
proposal to Brain creates a Brain record with `status=proposed` only. Devon must
approve or reject that record in Brain through the existing WS-1.4 governance
approval tools. WS-6.2 does not approve knowledge, create follow-up work units,
merge pull requests, deploy code, or enable dispatch automation.

## API

Create a bounded proposal:

`POST /api/v1/knowledge-promotion-proposals`

The caller must be a registered human actor. The request references one or more
existing WS-6.1 observation IDs, a correlation identity, a bounded correlation
summary, target Brain/type, proposed authority, applicability, provenance, and
the proposed Brain payload.

List proposals:

`GET /api/v1/knowledge-promotion-proposals`

Optional filters: `target_brain`, `target_type`, `state`.

Submit an existing proposal to Brain:

`POST /api/v1/knowledge-promotion-proposals/{proposal_id}/submit-to-brain`

The caller must be a registered human actor. The orchestrator calls the target
Brain REST proposal endpoint with a contributor/propose-only credential and
requires Brain to return `status=proposed`.

## Storage

`knowledge_promotion_proposals` is append-only and immutable after insert.
`knowledge_promotion_proposal_actions` is also append-only. Current proposal
state is derived from action history:

- no action: `proposed`;
- submitted action: `submitted_to_brain`;
- rejected action, if later added: `rejected`.

Creation is idempotent by `idempotency_key` and by `proposal_hash`. The
`proposal_hash` covers source observation IDs and hashes, target, authority,
applicability, and proposed payload. A different proposal for the same
`correlation_identity` is rejected until an explicit supersession model exists.

## Evidence

Creation records local event `knowledge_promotion.proposed`.
Brain submission records local event `knowledge_promotion.submitted_to_brain`.
Both events can be queued through event-publications as `factory-event/v1`
projections. Event publication is a projection and does not mutate lifecycle
truth.

## Brain Configuration

Production submission requires target Brain base URLs and contributor-only Brain
credentials:

- `ORCHESTRATOR_BRAIN_PROPOSAL_TARGET_URLS`, JSON object keyed by target Brain;
- `ORCHESTRATOR_BRAIN_PROPOSAL_CREDENTIALS`, JSON object keyed by target Brain;
- optional `ORCHESTRATOR_BRAIN_PROPOSAL_TIMEOUT_SECONDS`.

Store any production Brain contributor credential in BWS and inject it at runtime
without writing the secret value to tracked files. Do not use an approver key for
orchestrator proposal submission.
