# Credential rotation increment 3: build report

ADR-0055 increment 3, as amended by amendment 2, built and deployed on 2026-10-10 from
`docs/superpowers/plans/2026-10-09-rotation-increment-3-orchestrator-handoff.md`. Production
serves `4ca3d517bcca3c831e9a663ddfc0ff5dd9951894` (image `4ca3d51-amd64`, digest
`sha256:795831ed…24ed74`). No migration. `ORCHESTRATOR_ROTATION_EXECUTOR_AGENT_ID` is unset in
production, so every claim on a rotation unit is refused until increment 4. Before the deploy,
production had no ready or in-flight unit; its only open units were four failed dependency
updates, none of them rotation units.

## What shipped

| Item | Pull request | Mutations | Survivors |
|---|---|---|---|
| 9: executor identity and profile | security-standards #70 | — | — |
| 8: `destinations` profile field | intent-packages #117 | 8 | 0 |
| 7: bearer `previous` parser | #387, restored by #392 | 18 | 1 equivalent |
| 1–3: confinement, window, release | #388 | 44 | 0 (4 fixed) |
| 9: pin advance; 10: `deployment_observation` correction | #389 | — | — |
| 4–5: completion resolves dependencies; human-act form | #390 | 30 | 2 equivalent (3 fixed) |
| 6 and 8's runbook: rotation evidence schema | #391 | 23 | 1 equivalent (1 fixed) |

Every mutation was made by hand and run against its tests, with the tests shown passing
unmutated first. The rotation-evidence mutations ran without the schema-pin test, so each had to
be caught by behaviour rather than by the pinned schema.

**Equivalent survivors.** In #387, removing the `isinstance(previous, dict)` check: a non-dict is
still refused by the key-set check or by the `TypeError` boot converts. In #390, removing the
route's `_human` check (the CSRF token is issued only to the human who rendered the page) and
removing `extra="forbid"` from `HumanActDetail` (the route builds the detail from fixed fields). In
#391, dropping `include_input=False` (the refusal message is built from locations and types only).

**Fixed survivors.** #388: the latest claim decides continuation; only SYSTEM releases an
ineligible unit; an ineligible release's `FAILED` is committed; a redundant replay check deleted.
#390: a non-`work_unit` row naming the unit, a resolved dependency leaving the page, and a settled
unit offering no form. #391: a probe's extra key.

**Production verification of the `previous` parser**, in a container of `4ca3d51-amd64` with
`--network none` and placeholder values for every setting: no `previous` boots; a valid `previous`
with `until` seven days ahead boots and parses it; an `until` nine days ahead refuses to boot. The
same image's bundle carries `rotation-executor` (`active`, `rotation-executor-v1`) at
security-standards `9f59945`. The served OpenAPI schema has the release route.

**The executor's capabilities** (`rotation-executor-v1`), each with the ADR-0055 line that grants
it:

| Capability | ADR-0055 |
|---|---|
| `task_claim` | Decision 1: "It claims approved rotation units from the orchestrator with its own WORKER M2M identity" |
| `event_emit` | Decision 9: "Rotation state lives in the orchestrator, as evidence on the units" (`observer-v1` maps evidence-like rows to this term) |
| `repository_read` | Decision 1: "checks the revision against intent-packages' git" and "reads the registry at a revision" |
| `secret_read` | Decision 4: "read on `Rotation / <provider>`, `Rotation / Executor` and `Rotation / Healthchecks`" |
| `secret_write` | Decision 4: "write on the keeper projects and `Rotation / Work`" |
| `credential_create` | Decision 3's mint step; per-class table, OpenRouter "Mint: machine" |
| `credential_revoke` | Decision 3's retire-old step; per-class table, OpenRouter and GitHub PAT retire-old "machine" |
| `data_delete` | Decision 3: "After confirm-dead, the quarantine and staging secrets are deleted" |
| `infra_mutation` | Decision 4 and amendment 1, option 2: the Coolify token with `read`, `write` and `deploy` |

## Incident: #388 reverted #387, and a second deploy followed

#388's branch predated #387. Its final squash was `git reset --soft origin/main` after `origin/main`
had advanced to include #387, so the squash commit recorded #387's files at their older content
and merged as a revert of the parser, its tests and its docs. CI stayed green because the test
file went with the code. The first deploy (`73af81a`) went out without the parser; the
post-deploy scratch-container check found it (`M2MCredential` had no `previous`). Nothing in
production sets `previous`, so nothing failed. #392 re-applied #387's diff (`auth.py` and the test
file byte-identical), the 18 mutations were re-run with the same result, and `4ca3d51` was
deployed and verified, boot cases first. The handoff asked for one deploy; this increment took two.
The other squash commits were checked file by file and carry only their own changes.

## Proposed amendment 3

Proposed, not applied: each point is input for Devon or for increment 4.

1. **Evidence field names** (`tests/fixtures/rotation_evidence_schema.json`, the contract increment
   4 conforms to): `format` (`rotation-step/1`), `step`, `destination`,
   `fingerprints[].which|sha256_prefix|length`, `probes[].target|subject|expected|http_status|outcome`,
   `note`. Step kinds: `mint`, `stage`, `verify_new`, `store`, `distribute`, `verify_consumers`,
   `retire_old`, `confirm_dead`. Decision 3's "deploy" is `distribute`, because the word guards
   refuse `deploy` in `src/orchestrator/` code.
2. **The destination string has no format yet.** intent-packages validates `destinations` as
   sorted, unique, trimmed strings. Increment 4 defines the "consumer kind and destination" form
   the executor compares against the registry, and the package field should then validate it.
3. **The secret detector knows two value shapes** (an `Authorization: Bearer` header and the BWS
   token shape). Rotation evidence adds a refusal of any 32-character unbroken run, because the
   credentials being rotated are long runs; the human-act note at `/review` has only the shared
   detector. Decision 9's "secret-scanned at ingest" reads stronger than what any scan can do.
4. **Verifier-owned evidence on a rotation unit isn't checked.** Rotation criteria have no
   `automated_check`, so nothing files it today.
5. **Beyond the ADR, by this build:** boot refuses a credential holding `rotation-executor-v1`
   that the setting doesn't name, and a setting naming an identity without that profile (otherwise
   the executor direction of confinement fails open when the setting is missing). The release
   route accepts any unit, not only rotation units. The security-standards `environment` enum
   gained `isolated-vm`. The executor's identity is `active`, so increment 4 needs no pin advance.
6. **Dependencies that stay pending, as before this increment:** a `work_unit` dependency naming
   a condition other than `completed`, one registered after its predecessor completed, and the
   dependents of a predecessor that fails. SYSTEM can resolve them through the API; no human
   surface can, because the `/review` form refuses `work_unit` dependencies.
7. **The handoff said `resolve_dependency_command` commits.** It doesn't; its route commits.
   Completion calls the shared non-committing writer, `dependency_resolution.apply_resolution`.
8. **Writing GitHub Actions secrets isn't in `rotation-executor-v1`.** Increment 7 needs a
   profile revision (and possibly a capability term) before the rotation App is used.

## Increment 6 prerequisite

change-manager needs the equivalent of the `previous` parser (decision 7) before increment 6
rotates its bearers: an optional previous hash per identity, accepted only before `until`, with
boot refusing an `until` more than eight days ahead, shipped and verified on production first.
