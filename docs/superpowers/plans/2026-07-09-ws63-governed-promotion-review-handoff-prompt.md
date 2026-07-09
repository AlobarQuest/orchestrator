# WS-6.3 Governed Promotion Review Handoff

Start from merged WS-6.2 once Devon has reviewed and merged the orchestrator and
Brain PRs. Do not assume merge implies production deployment.

## Verified WS-6.2 Shape

- Orchestrator owns bounded correlation/provenance records.
- Brain owns the proposed/approved/rejected knowledge lifecycle.
- `POST /api/v1/knowledge-promotion-proposals` creates an immutable
  orchestrator proposal from existing WS-6.1 observations.
- `POST /api/v1/knowledge-promotion-proposals/{proposal_id}/submit-to-brain`
  explicitly submits the proposal to Brain and requires Brain to return
  `status=proposed`.
- Brain REST proposal endpoints exist for code and infra:
  - `POST /api/proposals/lessons`;
  - `POST /api/proposals/rules`.
- Brain submission must use contributor/propose-only credentials, never the
  approver key.
- Devon approval remains inside Brain through the WS-1.4 approval tools.

## WS-6.3 Candidate Scope

Add the smallest review/operations surface needed for Devon to inspect proposed
Brain records created from orchestrator promotions and approve or reject them
deliberately.

Do:

- verify merged WS-6.2 schema/routes in orchestrator and Brain;
- verify proposal submission creates Brain `status=proposed` records only;
- document or build a Devon review queue if existing Brain tools are not enough;
- preserve append-only orchestrator proposal/action history;
- preserve Brain approver-key gate;
- add tests that contributor keys cannot approve;
- add production closeout steps only after Devon explicitly approves deployment.

Do not:

- auto-approve Brain records;
- automatically generate follow-up work units;
- treat observations, event publications, monitors, CI, trackers, or Brain
  records as lifecycle authorities;
- merge or deploy automatically;
- use or store Brain approver credentials in orchestrator.
