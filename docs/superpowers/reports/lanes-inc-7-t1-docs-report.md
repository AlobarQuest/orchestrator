Outcome: done

# Increment 7 docs: ADR-0055 amendment 31, and #789 follow-ups 2, 3 and 5

**PR:** AlobarQuest/orchestrator#790, ready for review, branch `hq/lanes-inc-7-t1-docs` on
`4d8ca873`. HQ reviews and merges.

## What I built

Docs only. No code, test, TOML, script or plist changed.

- **ADR-0055 amendment 31** is at lines 4397-4542 of
  `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`, with one line added
  to the header's "Amended" list (lines 41-42). It covers the following points:
  - It quotes D-73 and D-74, question and answer, from sds-hq `state/decisions.md` at `effa6ea`. I
    confirmed that the text at `effa6ea` matches the text at HEAD.
  - Item 1 (D-73 B): Coolify stays root. T1 names Coolify admins and Coolify API write-token
    holders. It records the `coolify-terminal-off` act, HQ's read of the setting, and that the API
    path and Coolify's SSH key stay.
  - Item 2 (D-74 A): infraops stays root (`ssh-client.ts`, line 32), and T1 names every infraops
    session.
  - Item 3 restates amendment 29's "T1, as amended" list of root holders. Item 4 says the
    prerequisite is met and the cut-over is still unplanned.
  - Item 5 is #789 follow-up 5. The `bad_target` refusal is at `sds-pg-rotate` lines 97-100 and
    103-119. The exact comparison is at `bws_lane_token.py` lines 193-195 and 205-218, plus the
    malformed-answer check, the pre-claim wait and the claimed-step failure. It also states what
    isn't done yet: the per-lane targets and the row `helper_target`.
  - It has a "Passages it governs" table in amendment 29's style, with line numbers at `4d8ca873`.
- **`docs/operations/rotation-executor.md`** covers #789 follow-up 2:
  - "Install the helper" gains the consumers-only target's TOML shape, its `loaded`-only use, the
    `bad_target` refusals and the fingerprint form.
  - The end of "Lane tokens and ping URLs" now has the two proofs. A row naming `helper_target`
    gets the `loaded` pre-claim wait, and every consumer must match exactly. A mismatch, no
    consumer, or an unreadable answer waits and never passes. That match is Infra Brain 1032's
    attestation. A row without it keeps the `PATCH` proof (D-27).
  - The section ends by saying that no `bws-lane-token` row names `helper_target`, and that the
    cut-over reinstalls the helper first. It includes no cut-over procedure.
- **`src/rotation_worker/CLAUDE.md`** gains one invariant (#789 follow-up 3) with the reason drawn
  from #789's question report: a broker lane's container loads no `BWS_ACCESS_TOKEN`.

## Tests and checks

- `make check` ran on worktree database `orchestrator_test_hq_w_273`, with
  `SECURITY_STANDARDS_DIR` at the fixtures and `PGTZ=UTC`. Ruff and pyright were clean (0 errors).
  Pytest collected 14,669 items: 14,668 passed and 1 skipped. That run was on the first commit.
  The fix commit changed only the same three docs.
- `code-standards risk --repo .` returned `normal`, before and after the commit, so I ran no
  `vendor-review`.
- `tests/architecture/test_layer_manifest.py` ran on the committed tree after this report was
  added: 3 passed.

## Files changed

- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`
- `docs/operations/rotation-executor.md`
- `src/rotation_worker/CLAUDE.md`
- `docs/superpowers/reports/lanes-inc-7-t1-docs-report.md` (this report)

## Self-review

- I re-read every line cite in amendment 31 against `4d8ca873` with `sed -n`, and fixed one that
  was off by one (4246-4247). Adding the header line shifts the file by two lines after line 40.
  The amendment states its cites are at `4d8ca873`, so they stay correct.
- No secret, token, or ping URL appears in the diff. Only the server's Coolify UUID and the act's
  name are given, as the handoff states them.
- The `loaded` text never implies that a mismatch or an unreadable answer passes. Each place names
  both as waits before the claim and failures after it.

## Concerns

- **The handoff's "no `.cred-consumers.toml` row names `helper_target`" is too broad as written.**
  A `postgres-role` row does name one (`alobar-id-postgres`, in infraops' registry, shown in the
  runbook). I scoped the claim to `bws-lane-token` rows in both the ADR and the runbook. I checked
  only this repository's `.cred-consumers.toml`, which holds the lane-token rows.
- **Amendment date.** Devon's session was on 2026-11-22 local time, and both answers were recorded
  at 2026-11-23T00:49:54Z. I dated the amendment 2026-11-23 and said both in its text.
- **Stale cites not fixed here.** Amendment 29 item 3 cites the helper's lines before #789
  (line 4146). Amendment 31's consequences name this. The header's "Amended" list also lacks
  amendments 25 to 29. I didn't add them: that's outside this handoff.
- **The `ssh-client.ts` line 32 cite** comes from D-74's text. I didn't read infraops-mcp-server
  in this session. Amendment 29 cites lines 30-33 of the same file.

## Standards followed

- **Code Brain 3 (BLOCK, error-logging):** not applicable, because no code changed.
- **Code Brain 4 (BLOCK, error-handling):** not applicable, because no code changed.
- **Code Brain 5 (BLOCK, security):** the docs carry no secret, token or auth header. Credentials
  are named by row id, keeper or variable name, and the server by its Coolify UUID.
- **Infra Brain 591, 592 and 593 (BLOCK, security):** no BWS token and no inline
  `BWS_ACCESS_TOKEN` value is in any tracked file. The variable is named, and its value never
  appears.
- **Infra Brain 1032 (BLOCK, security):** the runbook and `CLAUDE.md` state that the `loaded`
  match is the consumer attestation the revoke waits on. Both state that a mismatch, no consumer,
  or an unreadable answer waits before the claim and fails the claimed step, and never passes. The
  ADR's item 5 states the same with code cites.
- **Infra Brain 212 (HTTPS):** not applicable.
- **Repo rules:** `make check` is green (14,669 collected). The risk verdict is `normal`. The new
  invariant follows the nested file's style: the rule as it stands now, with a pointer to its
  detail page and ADR.

## Follow-ups

- #789 follow-up 1: the cut-over reinstalls `sds-pg-rotate` at `4d8ca873` or later, writes each
  lane's consumers-only target, and adds `helper_target` to lane-token rows, never to a broker
  lane's row.
- #789 follow-up 4: the evidence note doesn't name which proof (`PATCH` or `loaded`) passed.
- Optional: update the ADR header's missing amendments 25 to 29, and amendment 29 item 3's
  helper line cites.
