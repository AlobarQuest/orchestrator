---
name: orchestrator
tier: active
status: active
purpose: Canonical work-unit lifecycle control plane for the software factory.
version: 0.1.0
version_source: pyproject
updated: '2026-07-05'
foundation: true
foundation_contract: 1
applicable_standards:
  project: '1.0'
  security: '1.0'
  code: '1.0'
  infra: null
required_checks:
- id: quality
  executor: github-actions:quality.yml
---

## Backlog

## Future plans

## WS-3.1 verification

Application implementation is verified at `aa76b29`; see
`docs/evidence/ws-3.1-evidence-index.md`.

Outstanding gates:

- onboard orchestrator into the project-standards foundation matrix;
- complete Devon's rendered UI review;
- confirm `Quality` on the final evidence-only PR head;
- complete Devon's final review and merge decision.
