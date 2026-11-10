Outcome: done

# Report: ADR-0055 amendment 22, a lane credential's revision 1 by standing answer

## What I built

I appended **amendment 22** to `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`
and added its line to the ADR's "Amended" index. The number is 22 because `origin/main` at
`5145adff` ends at amendment 21. Its status is **proposed**, and it quotes Devon's D-34 answer. It
follows the shape of amendments 20 and 21: Status, Context, Decision, Increments, Open before
building, and Consequences.

Draft PR: **#647**. Its head is the commit that adds this report, on branch
`hq/adr-0055-amendment-22-lane-credential-standing-approval`. I didn't merge it or mark it ready.

## Citations for "Read first" (1)-(4)

Orchestrator citations are at `5145adff`, and intent-packages citations are at `c5c9225`.

1. **Where intent-packages refuses an approval policy for the rotation profile.** The profile is
   `non-software-operational` (`generate.py:47`, `work_carrier/prepare.py:214`). Its row,
   `approval-policy.toml:112`, has no grant, so `approval_policy.py:152-169` returns
   `profile_not_policy_approvable` (lines 166-167). `operations.py:174-209`
   (`_authorize_approver`) admits a human operator, or a policy approver only when there's no
   objection. `do_approve` emits to the chain before it writes the lineage
   (`operations.py:288-305`).
2. **Where the hash is checked against the chain, and whether a named person approved it.** The
   carrier delegates to `intent-packages emit-intake-payload` (`work_carrier/prepare.py:139-145`).
   `verify_approval` (`operations.py:529`) matches the chain on hash and revision only
   (`_events_file_has_matching_approval`, line 472). At intake, the orchestrator also binds the
   approver (`package_sources.py:256-273`), and it accepts a person or `policy:` (lines 26 and
   199-214). **Finding:** the carrier doesn't itself check for a named person. Two places enforce
   it: intent-packages' missing grant, and `services/intake/decomposition.py:357-358`
   (`revision_approved_by_policy`). Both go into the amendment's Context and increments.
3. **`compare-destinations`, and what "equal" means.** The command is at
   `rotation_worker/cli.py:91-112` and exits 0, 1 or 2. An unforeseen exception exits 2 (lines
   101-107). It reads the registry at `main` (`registry.py:99-104`). "Equal" is
   `destinations.same` (`destinations.py:113-116`): the same strings in any order, where a repeat
   counts. The comparison type is `compare.py:16-23`.
4. **Where a type's standard authority set is defined.** It's `generate.authority`
   (`generate.py:201-219`), a function of the type's capabilities, the row's destinations and
   whether the plan is an issue. It isn't a constant. The two types are at `types.py:53-59`, and
   their classes at lines 66 and 70.

**Worked examples.** `rotation-landing-ledger-bws-token` was generated in `d6440e5` and approved
in `fff7fde` (#167). `rotation-landing-ledger-ping-url` was generated in `0d09201` and approved in
`c5c9225` (#168). Both lineages show `approver: devon`. Re-rendered from their rows at `5145adff`,
both equal the stored packages apart from `status`. A mutated copy (with `infra_mutation` added)
differs, so the probe discriminates. Both lineages record `author: claude-code-interactive`
although `create_rotation` passes `rotation-proposer`, which is one more reason the amendment
doesn't treat any marker as proof.

## What the amendment narrows

Amendment 22 narrows amendment 16 decision 1, and the basis of decisions 2 and 4, for a lane
credential's revision 1 only. The following statements are listed as narrowed, not withdrawn:

- amendment 15 decision 7;
- amendment 16 decisions 1 to 4 and its Consequences;
- amendment 17's Consequences;
- amendment 19 item 10 and its "Unchanged" line;
- amendment 20 decision 4;
- amendment 21 decision 3 and its "Unchanged" line.

The handoff named amendment 21 decision 5, but decision 5 is the read-only key's reach. The one
click is decision 3, and the amendment cites decision 3.

## Increments (not built)

1. intent-packages: an `approval-policy.toml` schema-4 grant for `non-software-operational`
   (`approval_policy.py`, versions at line 58).
2. orchestrator: `rotation-proposer standing-approval <credential_id>`, which re-renders, compares
   destinations and checks authority. It logs structured JSON and exits 0, 1 or 2.
3. orchestrator: the carrier re-runs that command for a `policy:`-approved rotation revision, and
   the decomposition gate admits exactly `policy:non-software-operational@v4` for the two types.
   The CLAUDE.md invariants are rewritten in the same change.
4. sds-hq: the cycle step that runs the command, approves, and opens the PR that cites the
   standing answer.

## Open items (in the amendment)

- Whether a revision 1 on a rotation plan qualifies, which happens when `last_rotated` is already
  set. As D-34 reads, it does. Devon may want the standing answer limited to issue plans.
- Whether `work_carrier` may import `rotation_proposer.generate`. The architecture guards decide
  that.
- Whether the approving cycle and the carrier read the same factory-events chain (both are on the
  Mac today), given ADR-0059's lane moves.
- Which agent id the cycle emits as (`FACTORY_AGENT_ID`).
- Generator drift fails closed to Devon. This is stated as an expected cost.
- Related, separate work: the 2026-11-10 standing answer that a ping URL is configuration might
  retire the ping-URL issue package and narrow the type condition.

## Tests and checks run

- `make check` against `orchestrator_test_hq_w_66`, migrated to head with
  `SECURITY_STANDARDS_DIR` set to the fixtures: ruff "All checks passed!", pyright "0 errors", and
  pytest **14207 passed, 1 skipped**. Exit 0.
- Security scan from the repo root (`security_scan.cli . --category security`): exit 0, no BLOCK
  findings, 2 WARN and 1 INFO (judgment). None of them come from this diff.
- I ran the read-only re-render probe described in the preceding section with a scratch script in
  `/tmp`, and it changed nothing in either repository.

## Files changed

- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`: amendment 22 and its
  index line.
- `docs/superpowers/reports/adr-0055-amendment-22-lane-credential-standing-approval-report.md`: this
  report.

## Self-review

- Every code claim in the amendment cites a file and line that I read at the pinned commits. The
  re-render equality is measured, not assumed.
- The design reuses ADR-0028's `policy:` approver shape. The alternative was a new `standing:`
  shape, which would need new recognisers in two repositories. Under this design, intent-packages
  needs a new grant, and the orchestrator needs one gate change plus the carrier check.
- I didn't run `/code-review`, because the change is docs only and the increments carry the code.
- I didn't read change-manager's source. Amendment 16 decision 2 and the root CLAUDE.md say its
  work policy keys on record fields, not on the approver, so the amendment leaves it unchanged.

## Concerns

- In the worked examples, the chain's `approver: devon` is the string the caller passed. The chain
  doesn't prove that Devon made the click, only that a human-operator id was named. The amendment
  doesn't depend on this, but it means the existing "named person" guarantee is a process
  guarantee, not a cryptographic one.
- The handoff asked for `Outcome: done/partial/blocked`, but the worker rules allow only
  `done/blocked/failed`. I used `done`.
- The scratch directories `/tmp/w66-ip`, `/tmp/w66-mut` and `/tmp/w66-scripts`, and the test
  database `orchestrator_test_hq_w_66`, are still in place for HQ's teardown.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** docs only, so it doesn't apply to this
  diff. Increment 2 states that the command logs structured JSON on stdout, as the proposer does,
  and names no value.
- **Code Brain rule 4 (never swallow an exception silently):** decision 2 makes every condition a
  refusal, and any unreadable input or exception exits 2, following `compare-destinations`'
  pattern (`cli.py:103-107`). The carrier and the decomposition gate fail closed to a person.
  Consequences restates this.
- **Code Brain rule 5 (never log secrets):** the amendment names only package ids, commits, BWS
  secret ids that are already public in tracked packages, and no values.
- **Infra Brain 591, 592 and 593:** the diff contains no BWS token, no inline `BWS_ACCESS_TOKEN`
  value and no token-shaped example. I grepped the diff to confirm, and the scan has no BLOCK
  finding.
- **Infra Brain 1032 (exposure means rotate):** I saw no secret value anywhere during the work.
- **Infra Brain 212 (HTTPS for public FQDNs):** docs only, so it doesn't apply.
- **Infra Brain 2890 (never commit a ping URL):** the diff contains no
  `https://healthcheck.alobar.net/ping/...` URL. I grepped the diff for `ping/` and found nothing.
