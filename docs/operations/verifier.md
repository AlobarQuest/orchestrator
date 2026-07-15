# Verifier Operations

WS-5.1 adds an independent verifier command that evaluates recorded SDS evidence
against acceptance criteria mapped to a work unit. The orchestrator remains
canonical lifecycle truth; verifier responses are command results backed by
ordinary lifecycle events, evidence rows, and adjudications.

## Route

```text
POST /api/v1/work-units/{unit_id}/verify
```

The request uses the standard command envelope:

- `idempotency_key`
- `expected_version`

Only actors with the `verifier` role may run the command. Workers can submit
evidence, but they cannot verify their own work or record adjudications.

For an `automated_check` criterion, the verifier uses this sequence:

1. Wait for the named CI check to reach a terminal conclusion on the submitted head.
2. Independently inspect the bounded repository, pull request, head, check, run, and
   expected-versus-observed assertion facts.
3. Record those facts with
   `POST /api/v1/work-units/{unit_id}/verifier-evidence/named-check`.
4. Invoke `POST /api/v1/work-units/{unit_id}/verify` with a fresh idempotency key.

The named-check evidence request uses the standard command envelope plus the package revision,
mapped AC, dispatch, canonical pull-request identity, check identity, run identity, conclusion,
and one to 32 bounded scalar assertions. Only a verifier may call this route. The service resolves
the evidence attempt from the locked work unit and supersedes the current evidence head.

The orchestrator does not call GitHub from either verifier route. The independent verifier
observes CI and submits bounded facts. The current armed `verification_read_head_sha` for the
unit's dispatched attempt is authoritative; stale or mismatched heads fail closed.

## Input States

The verifier accepts work units in `submitted` or `verifying`.

For a `submitted` unit, the verifier first transitions the unit to `verifying`
through the normal lifecycle service. After evaluation it transitions to one of:

- `completed` when every required mapped AC has a satisfying verifier adjudication;
- `revision_required` when deterministic evidence fails or fails closed;
- `awaiting_review` when at least one required criterion needs judgment and no
  deterministic failure is present.

The existing completion guard remains authoritative. A unit cannot reach
`completed` unless current terminal adjudications satisfy every required AC.

## Evidence Boundaries

WS-5.1 evaluates only evidence already recorded in the orchestrator. It does not
call GitHub, CI providers, production endpoints, change-manager, infraops,
trackers, brains, web pages, or logs.

External systems may be referenced by evidence, but the orchestrator stores only
bounded facts:

- stable references;
- normalized statuses and counts;
- hashes or digests;
- small structured summaries.

Do not store raw tokens, full logs containing secrets, unbounded external
payloads, private infra mutation details, or tracker text as authoritative
instructions.

## Deterministic Evaluation

The initial evaluator registry handles recorded facts for:

- test and gate summaries;
- security scan summaries;
- GitHub check summaries already recorded as evidence;
- health probe summaries already recorded as evidence;
- infra-lane final evidence pointers.

Missing, malformed, stale, untrusted, or insufficient deterministic evidence
fails closed. The verifier records a bounded `verifier.finding` evidence row and
a failed adjudication, then moves the unit to `revision_required`.

`automated_check` is deterministic only when its current evidence is verifier-owned
`verifier.github.named_check`. Pre-CI worker evidence such as `runner.pr.opened` remains
judgment-routed because it cannot prove the later named-check result.

## Judgment Criteria

Criteria with judgment-only evidence types, including `human.review`,
`code_review`, `judgment`, and `manual`, route to `awaiting_review`. The verifier
does not fabricate a pass or fail for those criteria.

A future LLM review layer must not make a single LLM verdict canonical. The
Phase-5 design constraint is at least two independent reviews with isolated or
truncated context and a cross-critique step.

## Idempotency

The verifier derives child idempotency keys from the request key for lifecycle
transitions, verifier findings, and adjudications. Replaying the same verify
command does not duplicate evidence, adjudications, or lifecycle transitions.

A new verifier run with a new idempotency key may supersede prior adjudications
through the existing adjudication supersession chain.

## Secret Handling

WS-5.1 introduces no new secret, runtime credential, BWS manifest entry, or env
file.

If future automation needs a verifier credential, use the existing M2M pattern:
fetch by stable BWS UUID at runtime, store only token hashes in
`ORCHESTRATOR_M2M_CREDENTIALS`, assign the verifier role through
`ORCHESTRATOR_M2M_ROLES`, and never write raw tokens to tracked files, prompts,
logs, package YAML, evidence, PR bodies, or generated artifacts.

## Non-Goals

The verifier does not:

- bind approved revisions to release artifact digests;
- create post-deploy verification work units;
- query live GitHub or production systems;
- deploy production infrastructure;
- merge pull requests;
- make trackers canonical lifecycle truth;
- promote brain knowledge;
- automate graduation.
