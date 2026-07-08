# Infra-Lane Linkage Operations

WS-4.4 records how an approved orchestrator work unit routes to the existing
change-manager/infraops lane. The orchestrator remains canonical lifecycle truth
for SDS work units, but it does not approve, execute, schedule, verify, or roll
back production infrastructure mutation.

## Routing Criteria

Use the GitHub-hosted factory runner when a work unit is repo-local, stateless,
compatible with the reusable workflow, and can complete inside GitHub Actions
tool and timeout limits.

Use local-heavy execution when the unit is approved and Ready, but needs
stateful local/cloud execution, larger verification loops, or explicitly
authorized multi-repo context.

Use the infra lane when the authority envelope covers production infrastructure
mutation or when the existing change-manager/infraops path owns the work.
Change-manager remains the sensitive infra-change approval authority. Infraops
remains the guarded mutation executor with its existing change-window,
post-verify, and rollback behavior.

## Linkage Record

A claimed worker records infra-lane linkage through:

```text
POST /api/v1/work-units/{unit_id}/infra-lane-links
GET  /api/v1/work-units/{unit_id}/infra-lane-links
```

The command requires the current orchestrator attempt and lease token. Stale,
lost, or mismatched worker control is rejected by the orchestrator. Recover
control through `reclaim-expired-claim`; do not edit the database directly.

The linkage stores non-secret references only:

- `change_manager_ref` and optional `change_manager_url`
- optional `infraops_ref`
- optional `approval_ref`
- optional `rollback_ref`
- optional `verify_ref`
- optional `final_evidence_ref`
- optional structured `payload` for reference metadata

Allowed statuses are `requested`, `approved`, `executing`,
`verification_pending`, `completed`, `failed`, and `cancelled`.

Every accepted linkage creates an `infra_lane_link.recorded` orchestrator event.
The event and link are evidence of coordination only; they do not transition the
work unit, complete acceptance criteria, or replace change-manager/infraops
records.

## Boundaries

The orchestrator must not:

- duplicate change-manager approvals;
- call infraops mutation tools as part of linkage recording;
- implement change-window policy;
- implement post-verify or rollback behavior;
- store raw credentials or private mutation payloads;
- merge pull requests or bypass Devon's merge gate.

WS-4.4 introduces no new credential. If future tooling needs to call the
orchestrator API, use the existing BWS-managed M2M pattern documented in
`docs/operations/authentication.md`: fetch by stable UUID at runtime, send
credentials only in request headers, and never write raw tokens to tracked files,
logs, evidence, PR bodies, or generated artifacts.
