---
name: orchestrator
tier: active
status: active
purpose: Canonical work-unit lifecycle control plane for the software factory.
version: 0.1.0
version_source: pyproject
updated: '2026-07-06'
foundation: true
foundation_contract: 1
applicable_standards:
  project: '1.0'
  security: '1.0'
  code: '1.0'
required_checks:
- id: quality
  executor: github-actions:quality.yml
---

## Backlog

## Future plans

## Known Non-obvious Invariants

- Generic authority approvals satisfy work-unit readiness only. Authority-expanding
  standing-context updates require a named human approval bound to the exact
  standing-context fingerprint.
- Protocol smoke tests may manipulate time or lease expiry as deterministic fixture
  setup. Runtime recovery behavior itself must go through public API/CLI surfaces,
  not private service shortcuts.

## WS-3.1 verification

Persistent orchestrator core is merged and closed. Orchestrator PR #1 merged at
`1ca7090079999dc25441cb0d1066b920b828e271`; intent-package closure PR #9
merged at `473de819ed31a2ab5beadde54dd03c7c71b4c178`. The closed package is
`ws-3.1-orchestrator-core` revision 1 with hash
`4414eae543d9dac8b1983f796593569d9abf97dfee1b8a06ef29b308e7b8337b`.

## WS-3.2 verification

Package intake and decomposition are merged and closed. Orchestrator PR #6
merged at `dd0e3f0deecd12e904b30cb29bfcfc57fb8fd688`; orchestrator
documentation PR #7 merged at `2a73b794665503240e58d12e3df55a8384bbec55`;
intent-package closure PR #10 merged at
`a48a72e10152b08739b3b83d1fba996c203d2f10`. The closed package is
`ws-3.2-package-intake-decomposition` revision 1 with hash
`84c929bc0860b6a585a62ec02fa35d9cdf89fce84773660aea1e383d955689df`.

## WS-3.3 verification

Runtime protocol semantics are merged and closed. Orchestrator PR #9 merged at
`183cbd945ad0dbe871661252cd313d84fd737f22`; intent-package closure PR #11
merged at `61550f21f59b4f70c4f03205e15415bf97cd87fd`. The closed package is
`ws-3.3-protocol-smoke-runtime-semantics` revision 1 with hash
`7829f22bfa30630a906d75131c84bc018c5dac3ceac7b933b7c9b46d23e5047a`.
