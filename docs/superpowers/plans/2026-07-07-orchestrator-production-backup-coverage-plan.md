# Orchestrator Production Backup Coverage Plan

Work unit: `backup-coverage`
Package: `orchestrator-production-deploy` revision 1
Status: pre-mutation backup coverage handoff

## Boundary

This artifact does not mutate backup infrastructure, Coolify, DNS, secrets, or
production databases. It records the backup requirements that must be satisfied
in the infrastructure-only session before the production orchestrator accepts
real lifecycle data.

The backup authority remains the existing `vps-backup` lane. The orchestrator
deployment consumes that lane and records evidence from it; it does not replace
or fork backup machinery.

## Existing Backup Contract

`vps-backup` protects Hetzner/Coolify production databases with nightly restic
backups to the NAS repository. Standalone Coolify-managed PostgreSQL databases
are backed up by explicit `pg_dump_container` entries in
`/Users/devon/Projects/vps-backup/backup.sh`.

For standalone Coolify-managed databases, `backup.sh` records successful dumps in
`~/.infraops/vps-backup-manifest.json` using the Coolify database UUID. Infraops
rule 572 consumes that manifest to decide whether a Coolify database has real
restic coverage. A database that exists in Coolify but is absent from `backup.sh`
and the manifest is intentionally treated as uncovered drift.

Weekly verification is handled by
`/Users/devon/Projects/vps-backup/verify-backup.sh`, which restores the latest
restic snapshot by tag and checks expected PostgreSQL dump files for presence,
non-empty content, and a valid pg_dump header.

## Required Orchestrator Entry

After the production PostgreSQL resource exists, add the orchestrator database to
the same standalone Coolify-managed PostgreSQL coverage pattern:

- Backup label: `orchestrator`
- Coolify project/service lookup: production database UUID from Coolify
- PostgreSQL user: the configured production DB user, redacted in evidence if it
  is secret-bearing
- Database name: production orchestrator DB name
- Manifest UUID: the same Coolify database UUID
- Verification list: add `orchestrator` to the `vps-production`
  `verify_postgres_tag` expected database list
- Strategy table: add `orchestrator` to `BACKUP-STRATEGY.md` under Production
  VPS as a Coolify-managed PostgreSQL database

## Evidence Required Before First Real Production Data

The infrastructure-only session must record these facts without leaking
credentials, dump content, tokens, or connection strings:

- Coolify app/resource identifier for `sds.alobar.net`
- Coolify PostgreSQL resource UUID for the orchestrator production DB
- Database name and non-secret backup label
- `vps-backup` commit that adds the orchestrator database to `backup.sh`,
  `verify-backup.sh`, and `BACKUP-STRATEGY.md`
- A backup run result showing the orchestrator dump was produced successfully
- The coverage manifest entry for the orchestrator DB UUID with a fresh
  `last_success` value
- A verification or restore-read result showing `orchestrator.sql.gz` restored
  from the `vps-production` restic snapshot and passed the pg_dump-header check

## Go/No-Go Rule

Do not accept real production orchestrator lifecycle data until the evidence
above exists. Health checks, migration checks, and empty-database deployment
smoke tests may run before this evidence, but the deployed service should remain
functionally gated from real package/work-unit intake until backup coverage is
verified.

## Protocol Friction

The current decomposition puts `infra-mutation` after `backup-coverage`, while
physical backup coverage depends on the production database resource created
during infrastructure mutation. Treat this unit as a pre-mutation coverage plan,
not completed backup proof. The completion proof belongs immediately after the
database exists and before real production data is allowed.
