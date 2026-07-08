# Release Immutability Operations

WS-5.2 records immutable release artifact bindings after Devon merges an
implementation PR and a build produces an immutable artifact digest. The
orchestrator remains lifecycle truth; release binding is provenance evidence, not
a second state machine.

## Route

```text
POST /api/v1/work-units/{unit_id}/release-artifacts
GET  /api/v1/work-units/{unit_id}/release-artifacts
```

The write command uses the standard command envelope:

- `idempotency_key`
- `expected_version`

Only the orchestrator system role may record a release artifact binding. Workers,
verifiers, dispatchers, CI workflows, and release tooling may submit bounded facts
through an authorized caller, but they do not gain merge, deploy, completion, or
adjudication authority.

## Required Facts

A release binding records bounded, stable facts:

- work unit ID;
- approved package revision ID and package revision hash;
- source repository;
- implementation PR number when available;
- source commit and merge commit;
- artifact registry, repository, name, and immutable digest;
- optional tag as metadata only;
- workflow run ID, attempt, path, ref, and URL;
- builder identity or runner class;
- SBOM and provenance refs and digests when available;
- small normalized summary data.

The artifact digest is the canonical artifact identity. Mutable tags are rejected
as identity and may be stored only when accompanied by a valid digest.

## Preconditions

The command fails closed unless:

- the work unit exists;
- the referenced package revision exists and belongs to the work unit;
- the supplied package revision hash equals the approved revision content hash;
- the work unit is already `completed`;
- source/merge commits and artifact digest are syntactically valid;
- the payload contains no secret-shaped fields or values.

Release binding does not mark a work unit complete. Completion remains governed by
the verifier, adjudication, and lifecycle guards.

## Idempotency And Conflicts

Replaying the same idempotency key and command returns the original binding.

Replaying the same binding facts with a different idempotency key also returns the
existing binding. Binding the same package, work unit, source repository, source
commit, merge commit, registry, artifact repository, and artifact name to a
different digest is rejected as a conflict. WS-5.2 does not implement a
supersession model.

## Evidence And Events

Every accepted binding records:

- one `release_artifact_bindings` row for canonical queryability and uniqueness;
- one `release.artifact_bound` evidence row with bounded release facts;
- one local `release_artifact.bound` event.

The local event can be projected through the existing event-publication layer.
The local `system` actor maps to the registered `unknown` external actor during
publication while preserving the raw actor ID in the mapping evidence record.

## Secret Handling

WS-5.2 adds no new secret, BWS manifest entry, runtime env file, or GitHub Actions
credential.

If later automation calls this route, use the existing M2M pattern:

- fetch raw bearer values by stable BWS UUID at runtime;
- store only token hashes in `ORCHESTRATOR_M2M_CREDENTIALS`;
- assign roles through `ORCHESTRATOR_M2M_ROLES`;
- never write raw tokens to tracked files, prompts, logs, package YAML, evidence,
  PR bodies, generated artifacts, or release records.

## Non-Goals

Release immutability does not:

- merge pull requests;
- deploy artifacts;
- create WS-5.3 post-deploy verification work units;
- scrape production state as canonical release lineage;
- call GitHub, Coolify, change-manager, infraops, trackers, brains, or production
  endpoints;
- bypass verifier completion guards;
- make CI, workers, verifiers, dispatchers, release tooling, or trackers canonical
  lifecycle authorities.
