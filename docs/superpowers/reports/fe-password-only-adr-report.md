Outcome: done

# Report: ADR-0055 amendment 26, factory-events secrets hold passwords only

Worker W-145, handoff `fe-password-only-adr`, branch `hq/fe-password-only-adr`.

## What I built

I appended **Amendment 26 — Each factory-events secret holds only its role's password
(2026-11-14)** to `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`. It
is docs only, and it edits no earlier text. It states the following:

- **What each secret holds (item 1).** Each secret holds its role's bare password, with the same
  UUIDs, project and grants. Amendments 24 and 25's "append DSN" and "read DSN" read as "append
  password" and "read password".
- **The names (item 2).** The amendment recommends renaming the secrets to `*-password` and
  leaves the choice to Devon. The question is in `fe-password-only-adr-question.md`.
- **One shared shape (items 3 to 8).** It has the following parts:
  - Two secret variables: `FACTORY_EVENTS_APPEND_PASSWORD` and `FACTORY_EVENTS_READ_PASSWORD`.
  - Three non-secret settings with no code default: `FACTORY_EVENTS_DB_HOST`,
    `FACTORY_EVENTS_DB_PORT` and `FACTORY_EVENTS_DB_NAME`. Host and port differ between the Mac
    (tailnet) and the containers (`coolify` network).
  - Role names as constants beside the schema that creates them.
  - Connections built in one place, `factory_events.chain`, as psycopg keyword parameters rather
    than a URL.
  - The same mode-selection rules as today, under the new names.
  - A missing setting, or a set retired `*_DSN` variable, fails closed and names the variable.
    It's never ignored, because ignoring it would drop the process silently to the file chain.
  - No whole-DSN variable is accepted, not even in tests. `FACTORY_TEST_DSN` stays as a fixture.
  - The import runs under the append settings (D-46 A).
  - Redaction targets the password values.
- **Increments.** The order is security-standards, then intent-packages, then orchestrator, and
  then the act that rewrites the secret values (Devon's, or a session's with his OK, done
  in-process, printing only a hash). All of it lands before FE-5.
- **What it grants.** Nothing: no credential, role, scope, project, account or grant changes.

Code reads cite security-standards `e89f69b`, intent-packages `3705e0c`, orchestrator `c8be2f89`
and sds-hq `f4b3a18`, each at `origin/main` after a fetch. I read every cited line.

## Tests and checks

- `make check` in the worktree, against database `orchestrator_test_hq_w_145`, passed: ruff, ruff
  format, pyright, then pytest with 14,886 passed and 1 skipped (rc 0).
- No code changed, so no new tests apply.

## Files changed

- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md` (appended amendment
  26).
- `docs/superpowers/reports/fe-password-only-adr-question.md` (the rename question).
- `docs/superpowers/reports/fe-password-only-adr-report.md` (this report).

## Self-review

- **Stop-point check.** Amendments 24 and 25 don't contradict Devon's change. They fix holders,
  roles and grants, and this change touches only what a secret's value holds and where the
  address lives.
- **Code claims.** I checked every one against the named commit. The intent-packages repository
  doesn't pin security-standards: it finds a checkout at run time (`emitter.py`, lines 105-114).
  The orchestrator's `security-standards.pin.toml` pins the registry bundle for its image. So the
  amendment grounds "security-standards first" in the checkout import, not in a pin.
- **Prose.** No line is over 100 characters, and the amendment uses the ADR's voice ("This
  amendment recommends").

## Concerns

1. **The UUIDs and settings come from sds-hq, not from code.** The host, port, database name and
   both secret UUIDs come from sds-hq `state/goal.md` line 59 and the handoff. No manifest in the
   four repositories names either UUID. ADR-0059 amendment 7 act 3 says the UUIDs go into each
   consumer's `.bws-secrets.toml` and orchestrator `.cred-consumers.toml` "in the same act", but
   at `c8be2f89` they aren't there. HQ needs to make sure FE-4 or FE-5 records them. I didn't
   fetch either secret.
2. **ADR-0059 amendments 7 and 8 still say "DSN"** in acts 3 and 5. The handoff limits me to
   ADR-0055, so amendment 26's Consequences tells readers how to read them. I didn't edit them.
3. **The non-secret settings have no default.** I chose required variables over Mac defaults in
   code, because a container needs different values and a default would only suit the Mac. The
   cost is that each Mac holder sets the same three values: the cycle, the session's command, the
   bump-proposer launcher, the nightly and the Mac carrier. If HQ prefers one copy, a tracked
   settings file in security-standards is the alternative. It isn't in the amendment.
4. **Retiring `FACTORY_EVENTS_IMPORT_DSN` (item 7)** changes FE-1's deliberate separation
   (`chain.py`, lines 17-19). I based it on D-46 A, which settled the import's role.
5. **The test database** `orchestrator_test_hq_w_145` is still in place for any fix round. Tear
   it down with the worktree.

## Standards followed

- **Code Brain rule 5 (BLOCK, never log secrets).** The amendment names secrets by UUID only and
  contains no value. Item 8 keeps redaction on the password values, and amendment 24 item 13's
  logging rules stand.
- **Code Brain rules 3 and 4 (BLOCK, logging and error handling).** Item 5 makes a missing setting
  or a retired variable a refusal that names the variable, raised as `ChainError` and reported as
  `UNAVAILABLE`. It's never silent and never read as "not approved".
- **Infra Brain 591, 592 and 593 (BLOCK, no BWS token in tracked files).** No token or token-shaped
  string is in any file or commit, and the BWS write-guard didn't trigger.
- **Infra Brain 1032 (`cred.exposure-rotate`).** I saw no credential value. The amendment says
  nothing is exposed.
- **Infra Brain 212 (HTTPS for public FQDNs).** It doesn't apply: the database is tailnet-only,
  as amendment 24 item 12 states.
- **Code Brain has no `secrets` road.** I followed Devon's estate rule as the handoff states it
  (secrets hold values, not configuration), and the amendment cites it.
