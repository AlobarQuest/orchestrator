Outcome: done

# fe-5-docs-followup: build report

Worker W-174, branch `hq/fe-5-docs-followup`, base `origin/main` `6f0a3514`.

PR: https://github.com/AlobarQuest/orchestrator/pull/717

## What I built

The change is docs and registry notes only. Nothing in `scripts/`, `src/`, or `lanes/` changed.

1. **The FE-5 plan records the act's two departures**
   (`docs/superpowers/plans/2026-11-14-fe-5-cutover.md`).
   - Step 0's row: the "Who" cell reads "Devon; as run, Devon and the session". The command cell
     opens with an "As run on 2026-11-15, superseding the plan's text" paragraph that cites D-54 A
     and G-63. It describes the BWS keeper copy route, the temporary `sds-operator` grants, the
     `security -i` write with the command on stdin, the row's check, and the grants' removal. It
     names the three keeper copies by registry id and UUID. The original text follows under
     "**The plan's text:**", unchanged.
   - Step 14's row: the "Who" cell names the HQ cycle (G-63, "Plan step 14 is a cycle's") and keeps
     "the plan said Session". The bar cell opens with the `fe-5-step14-bar` amendment's bar: the
     pre-act baseline, the two named findings (record 171 superseded/`STALE`, the `ambiguous`
     infraops-mcp-server bump), the failing conditions, and first chain contact as a carried check.
     The original bar follows under "**The plan's bar:**", unchanged.
   - A short "As run" section sits after the act table, before "What the act doesn't prove before
     step 10". It holds one dated paragraph: the act's time, gate, and the two departures. I chose a
     section over a follow-up line because both departures are facts about the act, not pending work.
2. **The registry records the BWS keeper copies** (`.cred-consumers.toml`).
   - `bws-bump-proposer-token` and `bws-factory-events-session-token` each gain a
     `kind = "bws-secret"` consumer with the keeper copy's UUID. Its note names the project
     (`Rotation / Keepers`), the removed `sds-operator` grant, and D-54 A. This is the form of the
     keepers at lines 318 and 362.
   - The section comment records that step 0 ran on 2026-11-15 by D-54 A, that a hand rotation
     updates both copies, and that the nightly's token has no row (item 3).
   - No `id`, `bws_uuid`, `keeper_service`, `keeper_account`, `rotated_by_sds`, `created`, or
     fingerprint field changed. The Keychain item stays each row's rotation keeper, because
     `RegistryEntry.keeper` requires exactly one of `bws_uuid` or the Keychain pair.

## Item 3: the nightly's token row

- **This repository:** no `[[credential]]` row exists for the nightly's token
  (`bws-factory-events-nightly-token`). The only mention is a `runtime-fetch` consumer note on the
  `factory-events-append-password` row (line 1334), which names its Keychain item
  `BWS_ACCESS_TOKEN_FACTORY_EVENTS_NIGHTLY`.
- **security-standards `origin/main` (`e905304`):** no row, because the repository keeps no
  `.cred-consumers.toml`. `git show origin/main:.cred-consumers.toml` exits 128, and
  `git ls-tree -r` lists no consumer registry. The token appears only in `.bws-secrets.toml`'s
  comment (line 14), `scripts/factory-events-nightly.sh` (lines 174 and 176),
  `src/factory_events/README.md`, and `tests/test_factory_nightly.py`.

So the nightly's token, and its keeper copy `4c1bcf0e-46f6-4ac3-9d3f-b50300d50015`, is in no
registry. Follow-up 1's condition, "if that repository keeps one", is false. A row for it is new
registry content, outside this handoff's notes-only scope; HQ decides where it goes.

## Tests and checks

- Registry-reading suites (`tests/rotation_worker`, `tests/rotation_proposer`,
  `tests/rotation_decomposer`, `tests/membership_check`), before the claim corrections:
  2,235 passed.
- `make check` on the committed, clean tree (`2c3da7d1`): exit 0. Ruff and pyright are clean, and
  pytest collected 15,002 items (10 workers): 15,001 passed, 1 skipped.
- `/code-review`: not run. The diff is docs and TOML notes only (repo `CLAUDE.md` lets trivial
  changes skip it). I reviewed the diff by hand and through an advisor pass, which led to the
  claim corrections in "Self-review".

Test database: `orchestrator_test_hq_w_174`.

## Out-of-repo readers of the new rows

I checked that the new `bws-secret` consumers change no automated behavior:

- infraops-mcp-server `origin/main` (`c3359bf`), `src/security-drift/cred-rotation.ts`, renders the
  manual rotation checklist for rows not `rotated_by_sds`. A new consumer appears there as a
  destination to update, which is the wanted effect.
- `scripts/cred-consumer-sweep.py` there scans locations for a value; it doesn't iterate registry
  consumer kinds.
- In this repository, the rotation executor rotates only `rotated_by_sds = true` rows, and every
  other row falls to the manual checklist (the registry's header). Both rows are `false`. The
  registry suites pass with the rows in place.

## Files changed

- `docs/superpowers/plans/2026-11-14-fe-5-cutover.md`
- `.cred-consumers.toml`
- `docs/superpowers/reports/fe-5-docs-followup-report.md` (this report)

## Self-review

- Each "as run" statement traces to the handoff's facts or G-63's page. I dropped one sentence
  that G-63 states as an instruction but no source records as a result: the session's confirmation
  that `sds-operator` is refused after the grants were removed.
- The notes say only what is known about access: `sds-operator`'s temporary grants were removed. I
  don't claim that no other account reads the copies.
- The original plan text stays verbatim in each changed cell, after a "The plan's" label.
- No token value appears anywhere. The UUIDs are BWS identifiers.

## Concerns

1. The section comment above the two rows still says "no `created` or fingerprint until then". The
   tokens exist since 2026-11-15, so both rows have no `created` and won't age. I didn't add the
   field: it isn't a note, and the handoff limits the change to notes.
2. The `bws-sds-operator-token` row's comment (line 38) and `RegistryEntry.keeper`'s docstring
   (`src/rotation_worker/registry.py`) say a machine token has no BWS copy of itself. That's now
   untrue for these three tokens. The docstring is in `src/`, outside this handoff.
3. A hand rotation of one of these Keychain items leaves its BWS copy stale unless the rotator
   updates both. The section comment says so, and the infraops checklist lists the copy, but
   nothing enforces it.
4. The nightly's token has no registry row (item 3).

## Standards followed

- **Code Brain rule 4 (BLOCK), never swallow an exception:** no code changed.
- **Code Brain rule 5 (BLOCK), never log or write secrets:** the change holds BWS UUIDs, which are
  identifiers, and no token value. I ran no `bws` and read no Keychain item.
- **Code Brain rule 3 (BLOCK), structured logs to stdout:** doesn't apply; no production code.
- **Infra Brain 2, 591, 592, 593 (BLOCK), no secret, `.env`, or BWS token in a tracked file or
  commit:** no token or `.env` content in the diff; the session-end BWS scan runs on the repository.
- **Repo style (orchestrator `CLAUDE.md`, Google developer style):** the prose uses present tense
  and active voice, no "should", and sentence-case headings. The registry stays strict-subset TOML,
  and the registry suites parse it.
