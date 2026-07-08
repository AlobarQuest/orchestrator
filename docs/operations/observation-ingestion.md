# Observation Ingestion Operations

WS-6.1 records bounded production and delivery observations as orchestrator-owned
facts. The orchestrator remains canonical lifecycle truth; monitors, trackers,
CI, deployment tools, GitHub, watchtower, ops-dashboard, Healthchecks, uptime
monitors, and drift digests are source systems only after their output is
normalized into bounded facts.

## Routes

```text
POST /api/v1/observations
GET  /api/v1/observations
```

The write command uses the standard command envelope:

- `idempotency_key`
- `expected_version`

Only the orchestrator system role may record observations. Workers, verifiers,
dispatchers, CI workflows, deployment tooling, monitors, trackers, and external
systems do not gain merge, deploy, completion, adjudication, dispatch, tracker,
or brain authority through this route.

## Accepted Sources

WS-6.1 accepts already-normalized observations from:

- `deployment_observation`
- `watchtower`
- `ops_dashboard`
- `healthchecks`
- `uptime_monitor`
- `github`
- `drift_digest`

It does not poll those systems and does not mutate their configuration.

## Subjects And Facts

Subjects are normalized as:

- `service`
- `repo`
- `deployment`
- `release_binding`
- `deployment_observation`
- `work_unit`
- `package_revision`
- `endpoint`
- `monitor`
- `external_run`

Observation records store:

- source system, reference, optional stable HTTPS source URL;
- trust classification;
- subject type and reference;
- optional environment;
- observation type, status, severity;
- observed-at and received-at timestamps;
- small summary;
- bounded normalized facts;
- normalized fact hash;
- optional payload digest;
- recorded-by actor;
- local event ID.

Do not store raw logs, response bodies, PR bodies, issue text, tracker text,
email text, generated artifacts, secrets, auth headers, production payloads, or
external text as instructions.

## Idempotency And Conflicts

Replaying the same idempotency key and command returns the original observation.

Replaying the same source system, source reference, and normalized fact hash with
a different idempotency key returns the existing observation.

Recording the same source system and source reference with different normalized
facts is rejected as `observation_conflict`. WS-6.1 does not implement
observation supersession.

## Events And Publication

Every accepted observation records one append-only `observations` row and one
local `observation.recorded` event.

The event-publication mapper projects `observation.recorded` as
`orchestrator.observation_recorded` in `factory-event/v1`. Publication is an audit
projection only. Publication failure or retry does not change lifecycle state.

## Non-Goals

WS-6.1 does not:

- promote brain knowledge;
- create lessons, rules, or governed proposals;
- create follow-up work units;
- canonicalize trackers or monitors;
- call GitHub, Coolify, Healthchecks, uptime monitors, watchtower,
  ops-dashboard, Linear, Todoist, or brains;
- merge pull requests;
- deploy artifacts;
- enable dispatch automation;
- supersede observations.

## Secret Handling

WS-6.1 adds no secret, runtime credential, env file, BWS manifest entry, GitHub
Actions secret, production observation credential, merge authority, or deploy
authority.

If a future caller needs to submit observations from production automation, use
the existing M2M pattern: fetch bearer values by stable BWS UUID at runtime, store
only token hashes in `ORCHESTRATOR_M2M_CREDENTIALS`, assign roles through
`ORCHESTRATOR_M2M_ROLES`, and never write raw tokens to tracked files, prompts,
logs, package YAML, evidence, PR bodies, generated artifacts, or observation
records.

