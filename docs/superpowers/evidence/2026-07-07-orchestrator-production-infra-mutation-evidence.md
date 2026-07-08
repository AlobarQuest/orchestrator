# Orchestrator Production Infra-Mutation Evidence

Date: 2026-07-07
Work unit: `infra-mutation`
Package: `orchestrator-production-deploy` revision 1
Approved hash: `2f6bc7da07aa00106cb6008fc8a85878e001652f6ec645bf25a37760d84c2e7d`

## Outcome

The Option-A production bootstrap is complete through `infra-mutation`.

Production orchestrator resources:

- Domain: `https://sds.alobar.net`
- Coolify project: `Software Delivery System` (`qxvgaak9j0eleitz56kf9a2b`)
- Coolify app: `orchestrator` (`eqj5l7k705fhi12x9i74fqf0`)
- Coolify Postgres: `orchestrator-postgres` (`mv6bei4lwkj552kp20zo7603`)
- Production image deployed during bootstrap: `ghcr.io/alobarquest/orchestrator:22ce0a9`
- DNS: Cloudflare zone `alobar.net`, DNS-only A record `sds.alobar.net -> 178.156.247.239`

## Verification

Baseline gates before mutation:

- Orchestrator `main` was at merge commit `22ce0a9fd4183df1794f0155ec4bd4ba6e4a83b5`.
- `make check` passed with 673 tests.
- Security scan reported `0 BLOCK`, `0 WARN`, `1 INFO` for the judgment-only BWS least-privilege note.
- `portfolio foundation` reported `violations=0 accepted=0 unknown=0`.
- Factory-events chain verification passed before relying on event publication paths.

Runtime checks:

- Alembic migration was run explicitly inside the app container.
- Current migration head after deploy: `0006_approval_event_id_text`.
- `GET https://sds.alobar.net/health/live` returned 200.
- `GET https://sds.alobar.net/health/ready` returned 200.
- Unauthenticated `GET https://sds.alobar.net/api/v1/status-ledger` returned 401.
- Missing and invalid M2M credentials were rejected.
- The temporary bootstrap smoke credential was accepted only with the configured credential-key ID plus raw bearer token, then deleted.

Forward-auth checks:

- Authentik application: `Orchestrator`
- Slug: `orchestrator`
- Provider mode: `forward_single`
- External host: `https://sds.alobar.net`
- Outpost: `authentik Embedded Outpost`
- `https://sds.alobar.net/review` redirects unauthenticated users to Alobar ID.
- Spoofed Authentik headers did not bypass forward-auth.

Backup proof:

- `vps-backup` commit `8ed7586` adds the production orchestrator DB dump and verification.
- Coverage manifest included resource `mv6bei4lwkj552kp20zo7603` with label `orchestrator`.
- Restic latest `vps-production` snapshot included `postgres/orchestrator.sql.gz`.
- `./verify-backup.sh` passed with `orchestrator.sql.gz` present, non-empty, and readable as a PostgreSQL dump.

## Runtime State

Local dogfood runtime database: `orchestrator_runtime`.

- `infra-mutation` work unit: `26d1323c-722c-5fb8-9d6e-f82e2a767a2f`
- Final state: `completed`
- Final version: `11`
- Attempts: `2`

Attempt 1 expired because the in-memory lease token was lost during evidence-recording recovery. Attempt 2 recorded the deploy/auth/migration/backup evidence and completed after Devon approved the narrow completion policy update.

## Policy Update

During evidence recording, package-level AC adjudications were recorded on the decomposed `infra-mutation` unit in addition to the ACs mapped to that unit. Devon approved the policy that decomposed-unit completion is evaluated only against the approved unit AC mapping. Adjudications outside that mapping are ignored by the completion guard rather than poisoning completion.

This repository implements that policy in `src/orchestrator/services/lifecycle.py` and covers it with `test_completion_ignores_adjudications_outside_decomposed_unit_mapping`.

## Scope Exclusions

This work did not start or implement:

- WS-4.1 factory-runner
- WS-4.2 dispatch adapter
- WS-4.3 local-heavy-runtime codification
- WS-4.4 infra-lane linkage
- Phase 5 verifier/release immutability
- tracker canonicalization
- brain learning/promotion
- graduation automation
- automatic merge

No worker merged a PR.
