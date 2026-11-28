Outcome: done

# Report: d88-standing-approval-plan (W-345)

## Summary

- **PR:** AlobarQuest/orchestrator #838, branch `hq/d88-standing-approval-plan`. Commits:
  `c6da2ef5` (the amendment), `05c72677` (this report), then a fix commit after the advisor's
  review. Read the head with `gh pr view 838 --json headRefOid`.
- **Base:** `origin/main` `25244d142f30eaeb91ab82fe34343d3b887d49ef`.
- **Change:** ADR-0055 amendment 39, Proposed, plus its line in the header index. Docs only.
- **Open item:** D88-A, in `d88-standing-approval-plan-question.md`. It doesn't block the PR.

## Stop points checked

- **The standing answer's text:** it matches the handoff's rule. It's quoted verbatim from sds-hq
  `standing-answers.md` at `origin/main` `5c916e36`.
- **Other ADR-0055 writers:** none. The open orchestrator PRs at start were #782, #781, #777,
  #776, #713, #564, and #563, all dependency bumps. Amendment 38 is the last amendment with a
  body, and amendment 37 has only an index line, so this one is 39.
- **New credential, grant, or type:** the plan needs none.

## What I built

Amendment 39 has these sections:

- **Context:** the D-88 answer and question, quoted. The commits read. The table of refusals with
  file and line. The eight packages, measured.
- **Decision:** eight readings settled from the standing answer, tasks R1 to R4, and exit checks.
- **Open items:** D88-A.
- **What it grants** (nothing), **Consequences**, and **Passages it governs**.

## The eight packages

All eight share these measured properties:

- `ready_for_review`, with a rotation plan and no `HumanAct`.
- Authority equal to `generate.authority` for the type.
- Destinations equal to the registry's, which is the `compare-destinations` comparison.
- Reach `["external_system", "live_estate"]`, and no `human_review` criterion.

| Package | Rev | `created_by` | Flag set | Changed since the last approval | Covered |
|---|---|---|---|---|---|
| orchestrator operator | 2 | `factory-create` | 2026-10-15 | revision, occurrence | Yes |
| orchestrator observer | 3 | `factory-create` | 2026-10-18 | revision, occurrence, `runtime-fetch` lines | Yes |
| orchestrator drift-reporter | 3 | `factory-create` | 2026-10-21 | revision, occurrence | Yes |
| orchestrator factory-runner | 1 | `rotation-proposer` | 2026-11-01 (`c2f6ffbf`) | no earlier package | No: first hand-over (D88-A) |
| change-manager full | 3 | `factory-create` | 2026-10-26 | revision, occurrence | Yes |
| change-manager read | 4 | `factory-create` | 2026-10-20 | revision, occurrence, one `runtime-fetch` line | Yes |
| change-manager propose | 2 | `factory-create` | 2026-10-20 | revision, occurrence | Yes |
| change-manager observe | 2 | `factory-create` | 2026-10-19 | revision, occurrence | Yes |

How I measured the "changed" column: I read each last approval's package at that approval's
`commit` and hashed it. All seven hashes equal the lineage's `approved_hash`.

`change-manager-execute-bearer` has no flag at change-manager `f7ab4a91`. Its coming revision 1 is
a first hand-over, so it isn't covered.

**Factory-runner's first hand-over:** its flag was added on 2026-11-01 by `c2f6ffbf` (#579). I
confirmed that with `git show`, not only `git blame`. Its package was first written on
2026-11-28 (`8bfaa16`). The executor has never rotated it: amendment 18 says "Nothing has proved
its first rotation". The amendment's reading treats it as a first hand-over, and D88-A asks Devon
to confirm, because D-88's question listed it under option A.

**Prior executor rotations:** the registry records the observer's rotation (line 336). For the
drift-reporter, the registry records the hand-over by revision 1 (line 448). For the other five,
the record shows a named approval only, so completion is unverified here. I didn't call infraops.

## Measured refusals (file:line)

All line numbers are at orchestrator `25244d14`, intent-packages `e82a246a`, and sds-hq
`5c916e36`:

- **orchestrator `src/rotation_proposer/standing_approval.py`:**
  - `proposer_generated`: lines 80-81 and 87-94.
  - `unapproved_revision_1`: lines 101-102, 106-109, and 110-111.
  - `issue_plan`: lines 160-162 and 178-183.
  - `LANE_KINDS`, `lane_type`, and `lane_destinations`: lines 38-41, 190-191, and 211-215.
  - `policy_approved_revision_1` and `carry`: lines 125-137 and 263-270.
- **orchestrator `src/work_carrier/standing.py`:** `_verified_approval` requires exactly one
  approval, at lines 143-145.
- **orchestrator `src/orchestrator/services/intake/standing_approval.py`:** `not_revision_1` and
  `not_a_lane_type`, at lines 45-48, 68-71, and 94-99. `decomposition.py` calls it at line 377.
- **intent-packages `src/intent_packages/approval_policy.py`:**
  - `_first_revision_refusal`: lines 694-710.
  - `_lineage_approval_refusal`: lines 714-719.
  - `_proposer_refusal`: lines 722-724.
  - `_granted_type`: lines 739-758.
  - `_destinations_refusal`, which allows exactly one destination of each kind: lines 761-773.
- **intent-packages `approval-policy.toml`:** grants only the two lane types (lines 164-182). Its
  rationale says "Revision 2 and later, and every other type, stay Devon's" (line 151).
- **sds-hq `src/sds_hq/standing_approve.py`:**
  - `STANDING_ANSWER`: lines 77-80.
  - `_LANE_PACKAGE` and `_HASH_REV_1`: lines 87-88.
  - `not_revision_1`: lines 416-417.
  - The revision-1 hash check: lines 426-428.
  - `check_first_issue`: lines 433-444.
  - `_approves`, which matches revision 1 only: line 495.
  - `check_lineage_read_back`, which expects exactly one approver: lines 710-716.
  - The outcome reports, which state revision 1: lines 915 and 928.
  - The D-34 strings: lines 741, 779, and 889.
  - `lane_of`: lines 862-868.
- **sds-hq `prompts/cycle.md`:** a cycle runs `standing-approve` only "For a lane credential's
  revision 1" (line 67).
- **What doesn't refuse:** the orchestrator's intake-payload emitter accepts any
  `policy:<profile>@v<N>` approver and matches by revision and hash
  (`src/orchestrator/package_sources.py` lines 28, 201-216, and 446-458). `rotation_worker/checks.py`
  and `src/rotation_decomposer/` don't read the approver.

## The tasks

- **R1. orchestrator, the reissue conditions:** `standing_approval.py`, `cli.py`, and their
  tests.
- **R2. orchestrator, the carrier and the gate:** `work_carrier/standing.py`,
  `services/intake/standing_approval.py`, their tests, and three CLAUDE.md invariants.
- **R3. intent-packages, the reissue grant:** schema 5, `approval-policy.toml`, and
  `approval_policy.py`.
- **R4. sds-hq, `standing-approve` for reissues:** `standing_approve.py`, `prompts/cycle.md`
  line 67, and tests. This task changes HQ's own runtime (sds-hq `src/` and its cycle prompt), so
  an interactive session merges it, not a cycle.

One lineage rule holds in R1, R3, and R4: revision 1 is approved by a person, and the tip is
compared with the last approved revision. A revision that was never approved doesn't refuse.

The amendment's exit checks 1 to 5 name the proofs, ending with an end-to-end policy-approved
decomposition.

## Tests and checks

- **`make check`:** not run. The diff is Markdown only, and the target needs Postgres and a
  migrated database. Nothing in `make check` reads the ADR: no test under `tests/` or `scripts/`
  names the file. `layers.toml` line 732 (`docs/superpowers/**`) covers the two new report files.
  CI's `Quality` job runs the full suite on the PR.
- **`code-standards risk --repo .`:** verdict `normal`, so no vendor review is required.
- **`/code-review`:** not run. The diff is a plan in prose with no code. The advisor reviewed it
  twice instead.
- **The measurements:** scratch scripts in `/tmp/w345-scripts`, outside the repository, run with
  this worktree's `.venv` and the orchestrator's own modules. The results are in the tables.

## Files changed

- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`: amendment 39 and
  its index line.
- `docs/superpowers/reports/d88-standing-approval-plan-question.md`
- `docs/superpowers/reports/d88-standing-approval-plan-report.md`

## Self-review

- **"Proposer-generated":** if the rule meant `created_by: rotation-proposer`, it would cover none
  of the seven packages D-88 asked about. I settled the reading from D-88's question text, which
  the amendment quotes.
- **"Widens reach":** I defined it from the ADR's own use of "widen", for grants, keepers, and
  copies. `runtime-fetch` lines hold no copy, so the observer and read revisions stay covered. A
  new written destination refuses.
- **The carrier's clone:** it clones at `--depth 1` (`run-work-carrier.sh` line 302), so it can't
  compare content. The command compares content before the policy approval, which mirrors how the
  D-34 carry form skips infraops. Deepening the clone was the alternative, and the amendment
  doesn't take it.
- **The advisor's review:** it found three fixes, now applied. The carrier's form had kept
  `no_new_copy`, which its depth-1 clone can't evaluate. The lineage rule differed between tasks.
  And the cycle prompt was missing from R4.
- **`rotated_before`:** this condition refuses when infraops recorded no date. That fails closed,
  and exit check 2 says the row then goes to Devon by name.

## Concerns

- **Infraops dates:** I didn't read infraops' `last_rotated` for any row, as the handoff forbade.
  If some early October rotations never recorded a date, R1's `rotated_before` refuses those rows
  until someone records one.
- **Approver string:** the policy approver string is the same for D-34 and D-88. The pull request,
  the chain event, and the observation's `form` field tell them apart, but `lineage.yaml` alone
  doesn't.

- **Stale PR text:** the PR body and the first commit message say "seven sites". The amendment
  now says "ten checks in six files" plus the cycle prompt line. The handoff allows no GitHub call
  beyond `git push` and `gh pr create`, so I didn't edit the PR body.

## Standards followed

- **Code Brain rule 3 (structured JSON logs) and rule 4 (never swallow an exception):** these
  don't apply to a docs PR. The plan names both for every code task's worker: a check that can't
  be made is a refusal.
- **Code Brain rule 5 (never log secrets):** the amendment names credentials by registry id, BWS
  UUID, or Coolify entry name only. The measurement scripts printed ids, hashes of package
  content, and field names, never a credential value.
- **Infra Brain 591, 592, and 593 (no BWS token in files, history, or inline):** nothing
  token-shaped is in any file or commit.
- **Infra Brain 1032 (`cred.exposure-rotate`):** the amendment treats these as scheduled reissues
  and opens or closes no exposure. It says so under "What it grants".
- **Infra Brain 212 (HTTPS for public FQDNs):** this doesn't apply, because no endpoint is added.
