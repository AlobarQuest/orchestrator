Outcome: done

# Report: adr-0059-inc-9-placement (W-150)

- **PR:** #701 (ready for review, not merged)
- **Content commit:** `5b1b871f`, fixed by `67fcbd30` and `52a11d5c` in fix round 1. The head is the commit that
  updates this report, on top of it.
- **Base:** orchestrator `cc6d9afd`
- **Amendment's line range:** lines 3071-3201 of
  `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md` (the amendment
  is appended after line 3069, with one blank line before it)

## What I built

I built ADR-0059 Amendment 10, "increment 9's minting runs in a new follow-up-mint lane". It
follows Amendment 9's form: Status, Decided by (D-49 quoted from `decisions.md` at `2b35ab4`),
Relates to, the line-number note, Context, Decision (four items), Verify in increment 9, and the
table. It appends only.

- **Decision item 1:** a new single-purpose VPS lane, `follow-up-mint`, in the moved lanes'
  pattern. It runs once a day and gets a new Healthchecks check, because it has no Mac
  predecessor.
- **Decision item 2:** the command the lane runs, `orchestrator mint-follow-ups`.
- **Decision item 3:** the authority, exactly. One lane BWS account reads two secrets: the
  SYSTEM bearer (`221a48d5-…`) and its own ping-URL secret (decision 3, line 694). The amendment
  also names one Coolify resource and one check, and grants nothing at merge. The ping-URL read
  follows HQ's answer (A) to my question: it's lane plumbing, not new authority.
- **Decision item 4:** the proposed done check, for increment 9's plan to confirm.
- **Verify items:**
  - Amendment 9's item, answered in part.
  - The repositories and documents increment 9 changes (line 1087). No launcher exists at base.
  - The Python CLI's environment variables and its `--idempotency-key`.
  - Whether a failed pass fails the lane's check.
  - ADR-0007, `follow-up-scheduling.md` and `credentials.md` still name infra-drift.
  - Increment 9 runs after increment 4.

## The command the lane runs

The lane runs `orchestrator mint-follow-ups`. Its sources:

- The command is defined at `src/orchestrator/cli.py:765`, and the entry point at
  `pyproject.toml:32`.
- It's in the lane image because the image is built `FROM` the orchestrator image (decision 1,
  line 92), and the Dockerfile puts the venv on `PATH`.
- It posts the same route and body as infra-drift's Node CLI (infraops-mcp-server
  `src/orchestrator/api-client.ts` at `c3359bf`).
- The Node CLI isn't in the lane image, so the lane doesn't run it. infra-drift runs it from
  `scripts/drift-audit.sh` line 176 at `c3359bf`.

## Table rows (lines at `cc6d9afd`)

| Passage | Disposition |
|---|---|
| Amendment 9 decision item 2, where minting runs and which identity (3005-3006) | Answered by decision items 1 and 3 |
| Amendment 9 decision item 2, the done check (3006-3007) | Proposed by decision item 4 |
| Amendment 9 decision item 3, "at the check its plan states" (3011-3012) | Stands; the check is item 4's once confirmed |
| Amendment 9 decision item 5, the SYSTEM bearer's new holder (3020) | Answered by decision item 3 |
| Amendment 9 "Verify in increment 9" (3041-3044) | Answered in part; done check still gates |
| Amendment 9 row on increment 9, the SYSTEM bearer question (3057) | Answered by decision item 3 |
| Amendment 9 row on Amendment 6 decision item 4 (3063) | Proposed by decision item 4; no Mac job is booted out |
| Amendment 9 closing paragraph, ADR-0007 and follow-up-scheduling.md (3068-3069) | Stands; carried as a verify item |
| Decision 3, "Every lane keeps its check" (675-676) | Stands for moved lanes; `follow-up-mint` gets a new check |
| Decision 5, classification of the 20 launch agents (768) | Stands; the count doesn't change |

## Fix round 1

HQ's Opus review at `d2fd4e38` raised two blocking and four non-blocking items. Fix commits:
`67fcbd30` and `52a11d5c`; the head is the commit that updates this report, on top of it. Each item's fix, with
the line confirmed by `git show cc6d9afd:<path> | cat -n`:

1. **Decision 3's citation (blocking).** "Every lane keeps its check" is lines 675-676. Decision
   item 1 now cites lines 675-676 for 674, and the ADR's table row and this report's row cite
   675-676 for 674-675.
2. **Decision 5's heading (blocking).** The heading is line 768. The ADR's table row and this
   report's row now cite 768.
3. **ADR-0055 amendment 23.** Decision item 1 now says no person pastes the ping URL: the VM
   rotation executor reads it from the Healthchecks Management key's check listing, as ADR-0055
   amendment 23 narrows decision 3 (lines 714-716), and the lane never holds that key (line 677).
   `52a11d5c` names the executor as the actor, so the sentence can't read as the lane holding the
   Management key.
4. **The rotation-decomposer reader.** Decision item 3 now names the rotation-decomposer lane as a
   SYSTEM bearer reader, citing `.bws-secrets.toml`. The handoff cited lines 14-20; the reader is
   named on lines 20-21 (line 20 starts the sentence, line 21 names the lane's container), so the
   amendment cites lines 20-21.
5. **Alert-hub sends.** The failure-handling verify item now says an alert-hub send from the lane
   would need a sender token, a secret decision item 3 doesn't grant, so it would need a new Devon
   decision. Alert-hub sender tokens are BWS secrets (`.bws-secrets.toml`, project
   `Alert Hub Senders`).
6. **The order.** The order item now cites decision 6's heading, "Migration order, cut-over,
   rollback and the week bar" (line 828), and its "Increments" list (line 903), with increment 4 at
   line 1023 and increment 9 at line 1079. Neither passage states "after increment 4" in words:
   the order is the list's numbered order under a heading that names it as the migration order,
   and the amendment says it that way.

No other text changed. The amendment grows from 124 to 131 lines (3071-3201).

## Tests and checks

Fix round 1 (W-152, on the tree at `67fcbd30`; the later fix commits change only Markdown):

- `make check` with `TEST_DATABASE_URL` and `ORCHESTRATOR_DATABASE_URL` set to
  `orchestrator_test_hq_w_152`, migrated to head (`alembic current` equals `heads`,
  `0048_standing_approval_obs`): exit 0. pytest reported `10 workers [14887 items]`: 14886
  passed and 1 was skipped. Pyright reported 0 errors.
- Security scan from the repo root: 0 BLOCK, 2 WARN and 1 INFO, the same findings as the
  original round.

Original round (W-150):

- `make check` with `TEST_DATABASE_URL` and `ORCHESTRATOR_DATABASE_URL` set to
  `orchestrator_test_hq_w_150`, migrated to head: exit 0. It collected 14887 items: 14886 passed
  and 1 was skipped. Pyright reported 0 errors.
- Security scan (`security_scan.cli . --category security`) from the repo root: 0 BLOCK. It
  reported 2 WARN (`bws.secret-files-gitignored`, `bws.manifest-matches-usage`) and 1 INFO
  (`bws.least-privilege-scope`). All three are repo-wide, and this docs-only diff doesn't touch
  what they check.

## Files changed

- `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md` (+132 lines,
  appended)
- `docs/superpowers/reports/adr-0059-inc-9-placement-report.md` (this report)

## Self-review

- I checked every cited line number against `cc6d9afd`: the ADR, `credentials.md` lines 63-68
  and 88-92, `cli.py` lines 43-45 and 765, and `pyproject.toml` line 32.
- The amendment states no general lane rule and doesn't fix an hour.
- It doesn't decide failure semantics; that's carried as a verify item.
- It names only the readers that the sources name: `sds-operator` and infra-drift's read through
  it (`credentials.md`), and the rotation-decomposer lane (`.bws-secrets.toml`, lines 20-21).
- Before writing, I asked HQ one question: the ping-URL read conflicted with "reads only the
  SYSTEM bearer". HQ answered (A), and decision item 3 records it with the line citation.

## Concerns

- I didn't run `/code-review`. The change is docs only, and HQ's Opus whole-branch review is the
  merge gate.

## Follow-ups outside the ADR (also in the PR body)

- ADR-0007 "Scheduled trigger": update when minting moves.
- `docs/operations/follow-up-scheduling.md` "Running a pass": it still names infra-drift, and
  its by-hand recipe omits the required `--idempotency-key`.
- `docs/operations/credentials.md` lines 88-92: they still name `drift-audit.sh` as a SYSTEM
  bearer consumer.
- infraops-mcp-server `scripts/drift-audit.sh` lines 152-180: increment 9's PR removes the
  minting step.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** doesn't apply; the change is docs only.
- **Code Brain rule 4 (never silently swallow an exception):** doesn't apply; the change is docs
  only.
- **Code Brain rule 5 (never log secrets, credentials, tokens):** the amendment names no secret
  value and refers to the SYSTEM bearer by its truncated UUID, `221a48d5-…`, as the ADR does.
- **Infra Brain 591, 592, 593:** no BWS token is in the diff or its history, and no inline
  `BWS_ACCESS_TOKEN` value appears. The scan reports 0 BLOCK.
- **Infra Brain 1032 (`cred.exposure-rotate`):** I saw no secret value in any source I read. I
  read `drift-audit.sh` only for its fetch commands, which hold UUIDs and Keychain item names, not
  values.
- **Infra Brain 212 (HTTPS for public FQDNs):** doesn't apply; the change is docs only.
