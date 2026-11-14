# Question: rename the two factory-events secrets?

Worker W-145, handoff `fe-password-only-adr`. The handoff asks that the rename stay a question.
The rest of the work is done (see `fe-password-only-adr-report.md`); this question blocks nothing.

ADR-0055 amendment 26 item 2: once each secret holds only its role's password, should the BWS
names change?

- **A (recommended): rename** `factory-events-append-dsn` to `factory-events-append-password` and
  `factory-events-read-dsn` to `factory-events-read-password`, in the same act that rewrites the
  values (amendment 26, increment 4). A name then describes what the secret holds. No code reads
  either secret by name; the only place the names appear is sds-hq `state/gates/factory-events-db.md`
  (lines 42-44 at `f4b3a18`), so the rename breaks no reader.
- **B: keep the names.** Nothing changes beyond the values; the names stay slightly wrong.

Either way, the UUIDs, project and grants stay the same (append `2ce0dbc7-eeac-4bf0-8204-b50200c97516`,
read `2f708d08-ea5d-4d11-bde0-b50200c9a60b`).
