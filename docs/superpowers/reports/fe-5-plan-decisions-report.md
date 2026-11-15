Outcome: done

# FE-5 plan decisions: D-50 A, D-51 A and D-52 A recorded, and the `fe-5-chain-reset` act named

Handoff: `sds-hq/state/handoffs/fe-5-plan-decisions.md`. Worker W-162. Branch
`hq/fe-5-plan-decisions`, base `origin/main` `7b486715`. PR #706 (open, not merged). Docs only.

Superseded in part: D-53 C (ADR-0055 amendment 28, 2026-11-14) withdraws the `fe-5-chain-reset`
act this report describes. A post-import rollback ends FE-5 until a new amendment plans the next try.

## What I built

### The plan (`docs/superpowers/plans/2026-11-14-fe-5-cutover.md`)

- **Introduction and citations:** names Devon's three answers and where the ADRs record them. It
  cites sds-hq PR #89 at head `5d5ac1e` and security-standards `237e422` (PR #94).
- **Holders:** the cycle's row says `check_chain` reads under the append role (D-50 A), and its PR
  column is "P-HQ (sds-hq #89)".
- **P-HQ:** a new "Pull request" line says sds-hq #89, head
  `5d5ac1eeeff05d6f823a9dfdddbd660765488486`, is `MERGED` as `0d139c8f`, read from `gh pr view 89
  --repo AlobarQuest/sds-hq --json state,headRefOid,mergeCommit`. It was `OPEN` at my first read
  and merged during this session. The `check_chain` change names the append role. "Blocked on" says
  nothing in the plan blocks it now.
- **Merge-order checks:** adds that security-standards #94 is `MERGED` at `237e422`. I read that
  from `gh pr view 94`.
- **RP row:** the "copy that the trigger won't let anyone delete" text is replaced with the D-51 A
  path. It also says to record step 4's head and step 6's `<n>` each time step 6 succeeds,
  because the reset's precondition compares the database against the import it holds.
- **Step 12:** FE-5's one `verify` runs on the Mac. The VPS verify belongs to 3f-2's cut-over
  (D-52 A).
- **What rolls back after the rollback point:** a new bullet says a retry after a rollback from
  RP or steps 7 to 9 needs `fe-5-chain-reset` before its step 6.
- **New section "Reset the chain before a retry: `fe-5-chain-reset`":** the act's steps, listed
  in the next section.
- **Open questions:** questions 1 to 3 are marked "Answered by Devon (D-5n A, 2026-11-14)". I
  added a new open question 9, covered under "How I resolved the head check".
- **Follow-up 3:** sds-hq's file-path code retires after the act. PR #89 keeps it only as the
  switch-off mode.
- **New follow-up 5:** 3f-2's cut-over carries the VPS verify (D-52 A), so the deferral isn't
  dropped. FE-6 becomes follow-up 6.

### ADR-0055 (`docs/decisions/0055-*.md`)

- **Amendment 27** comes after amendment 26 and quotes each answer's option text verbatim from
  sds-hq `state/decisions.md` lines 13-15. It has three items:
  - Item 1 adds the owner act for D-51 A. It also records that "drops the chain schema" means
    `DROP TABLE public.factory_events`, because the table is in `public`.
  - Item 2 adds one line for D-50 A. The append role is already held, and the read-only role
    still has one holder.
  - Item 3 adds one line for D-52 A.

  Its "What it grants, and nothing more" section lists only the one owner act.
- **Status line:** it carries the same HQ D-12 review clause as amendments 25 and 26, and cites
  `decisions.md` at sds-hq `b87452e`.
- **Pointers:** I added one-line pointers to amendment 27 at amendment 24 item 3, after "step
  FE-4", and at the end of "Open before building", in the history-import entry.

### ADR-0059 (`docs/decisions/0059-*.md`)

The "one `verify` on each machine" text exists only in amendment 7, act 5. I added one sentence
there, in the same form as the "**Amendment 8 names…**" sentence next to it. It records that
D-52 A limits FE-5's verify to the Mac and points to ADR-0055 amendment 27 item 3.

## The act `fe-5-chain-reset`, as written

The act is allowed only when a rollback came after a successful step 6 and before any append,
that is, from RP or from steps 7 to 9. It never runs from step 10 on. HQ announces it with
`sds-hq prod announce fe-5-chain-reset "…" --why "D-51 A; FE-5 rollback after step 6"`.

The act runs inside the retry. The retry repeats steps 1 to 5, with every writer paused and
`chain-db.env` absent. Then it runs these steps:

1. **Precondition.** The database head must equal the step 4 file head of the import the database
   holds (the most recent successful step 6), and the event count must equal that step 6's `<n>`,
   before the drop. The mechanism is open question 9, and
   the act doesn't run until that's settled. From RP to step 9, the act's structure already keeps
   holders from appending.
2. **Drop and rebuild, as the owner.** The owner is the resource's superuser, kept in Coolify
   (amendment 24 items 3 and 11; amendment 26 item 9). Its password comes from Coolify resource
   `b82fa1vg5whecgnjreuqb5tk`, and the session reads it into one process's environment only, as
   `FACTORY_EVENTS_OWNER_DSN`. It's never printed, written to a file, or put in argv. The owner
   runs `DROP TABLE public.factory_events` from a process that reads the DSN from its environment,
   and then `python -m factory_events.db_setup` with the same environment. The plan says not to
   pass the DSN to `psql` as an argument. No role changes, and no password resets.
3. **Check.** `db_setup` exits 0, and the table is empty. The emptiness check uses no new reader:
   step 6's `import-file` refuses a non-empty chain, so its exit 0 confirms it.
4. **Continue from step 6.** The retry runs `import-file` and that step's own checks. Then HQ runs
   `sds-hq prod verified fe-5-chain-reset`.

## How I resolved the head check against open question 8

I didn't break question 8, and I didn't invent a command. I checked what exists. The session
wrapper has only `approve` and `import-file`. The security-standards CLI at `237e422` has `emit`,
`verify`, `adapt`, `ship` and `import-file`, and no head command. `db.py` reads the head only
inside `append_event`, `verify` and `import_file`. sds-hq PR #89's guard query runs inside the
cycle, not as a session command.

No existing command reads the head and count without being a `verify`, so I wrote the mechanism
as open question 9. The reset act can't run until HQ or Devon names it. I didn't use the nightly,
because it's paused and its `adapt` appends. I folded one more gap into question 9: no documented
procedure reads a Coolify database password in-process. FE-4's owner step was Devon's, typed at a
hidden prompt (sds-hq `state/gates/factory-events-db.md` step 3).

## Tests and checks run

| Check | Result |
| --- | --- |
| PR #706 `Quality` (full quality gate) at `33721ea2` | pass in 11m17s. From the job log saved to a file: `14983 passed, 23 skipped, 5 warnings`. |
| PR #706 `Rollout transcription currency` | pass |
| PR #706 `Runner consumer compatibility` | pass |
| `layers.toml` coverage for the report | `docs/superpowers/**` (line 759) covers it. No manifest edit. |

The later commits (this report and one docs fix-up) change only `docs/`. Each push reruns the same
checks, and I waited for the final head's run before exiting. I didn't run `make check` locally. The change is docs only, and CI's `Quality` job runs it.

## Files changed

- `docs/superpowers/plans/2026-11-14-fe-5-cutover.md`
- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`
- `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md`
- `docs/superpowers/reports/fe-5-plan-decisions-report.md` (this report)

## Self-review

- The amendment quotes the decision text verbatim. The handoff's own phrasing was a paraphrase, so
  I quoted `decisions.md` instead.
- Amendment 27 adds no credential, reader, role or session `verify`. D-50 A reuses the append role
  the cycle already holds.
- The precondition compares the database against the import it holds. A retry's fresh step 4
  file differs after the writers resume on the file, and a second rollback replaces the import,
  so the RP row says to record the values each time step 6 succeeds.
- Outside the worktree I ran only reads, plus one `git fetch` in `~/Projects/security-standards`.
  It moved only remote-tracking refs and changed no checkout.
- No password, DSN value or token appears anywhere in the diff.

## Concerns

1. **Open question 9 blocks `fe-5-chain-reset`, but not FE-5 itself.** The act needs two named
   mechanisms: a read of the head and count that isn't a session `verify`, and an in-process read
   of the Coolify owner password. It matters only if a rollback comes after step 6. Candidate
   heads I didn't choose between:
   - The owner reads the head inside the announced act.
   - Devon runs the owner steps by hand, as in FE-4.
   - A new read-only command, which would need its own decision.
2. **Who runs the owner act.** The handoff has the session read the owner password. FE-4's owner
   act was Devon's, by hand. D-51 A doesn't name the actor. I wrote the session reading it, as the
   handoff directs, and left the mechanism in question 9.
3. **W-161's concern 1, the owner's privileges, is answered on paper.** ADR-0055 amendment 24
   item 3 makes the owner the resource's superuser, and G-57 ran `db_setup` as `postgres`. That
   covers `ALTER ROLE` and the schema and database `REVOKE`s. No live check was made.
4. **Uncovered concern:** the `## Standards` section has no rule on reading a Coolify-held
   database password. That gap is part of question 9.

## Fix round 1

Worker W-164, from HQ's Opus whole-branch review at `f77baa29` (two blocking findings). Fix
commit `38300fae` is fast-forward on `f77baa29`. It supersedes this report's earlier text on the
owner steps: "The act `fe-5-chain-reset`, as written" step 2 and concern 2.

1. **The owner steps' actor is open (D-51 A names the session only for the re-import).**
   - Plan line 522, the reset's step 2: removed "The session reads it into one process's
     environment only". It says who runs the drop and the setup, and how the owner password
     reaches them, is open (open question 9). FE-4's precedent, Devon at a hidden prompt, is the
     only approved path.
   - Plan line 590, open question 9: "Devon decides both (a D-number), recorded in an ADR-0055
     amendment before `fe-5-chain-reset` runs", replacing "HQ or Devon". It notes the head read
     is a chain read, which question 8 refuses to sessions (D-44 B; amendment 25 item 1), and
     that it blocks only the act, not FE-5.
   - ADR-0055 line 3926, amendment 27 item 1: says the drop and setup actor is open.
   - ADR-0055 line 3937: a new "Open before the act" section carries question 9 as blocking only
     `fe-5-chain-reset`.
   - ADR-0055 line 3948, "What it grants": the act with its actor unnamed, and no actor gains the
     owner password.
2. **Citation:** ADR-0055 line 3909 and this report's line 42 cite `state/decisions.md` lines
   13-15 at `b87452e`. I read them there: D-50, D-51 and D-52.
3. **Optional sentence fix:** plan line 511 reads "the event count must equal that step 6's
   `<n>`", and the W-161 parenthetical is its own sentence.

CI at `38300fae` (run 43657917020): `Quality` pass in 15m56s, with `14983 passed, 23 skipped, 5
warnings` read from the job log; `Rollout transcription currency` pass; `Runner consumer
compatibility` pass. This section's commit changes only this report.

## Standards followed

- **Code Brain rule 5 (never log secrets):** the docs show no password, DSN value or token. After
  fix round 1, the plan names no holder of the owner password (open question 9). Whoever runs the
  owner steps never puts the password in argv, a file or output, and the plan forbids passing the
  DSN to `psql` as an argument.
- **Code Brain rules 3 and 4:** these don't apply. The change is docs only and adds no code,
  logging or exception handling.
- **Infra Brain 591, 592 and 593 (no BWS token in tracked files, no inline `BWS_ACCESS_TOKEN`):**
  I ran no `bws`, read no Keychain item, and wrote no token or token-shaped string.
- **Infra Brain 1032 (a credential in a transcript is exposed):** no credential appeared in this
  session. I didn't connect to the VPS database or read Coolify.
- **Infra Brain 212 (HTTPS for public FQDNs):** this doesn't apply. No endpoint changes, and the
  database has no public FQDN.
- **Workspace invariant #22 (`--color no` on parsed `bws` calls):** I added no new `bws` call. The
  plan's existing step 0 call keeps `--color no`.
- **Orchestrator doc conventions and Google developer style:** I matched the plan's existing
  structure. I used numbered steps for the act, sentence-case headings, and the "Answered by
  Devon (D-5n A, 2026-11-14)" form the handoff asked for. The ADR amendment follows amendment
  26's shape, and the in-place pointers follow the existing "Amendment 25 item 4 names them" form.
- **Construction mode:** I took no production act. I opened PR #706 and didn't merge it. The
  only remaining gap is written as an open item, not guessed.
