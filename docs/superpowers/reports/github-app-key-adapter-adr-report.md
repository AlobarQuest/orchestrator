Outcome: done

# Report: ADR-0055 Amendment 35, a rotation adapter for GitHub App private keys

Worker W-318, branch `hq/github-app-key-adapter-adr`, based on orchestrator `d8d303b9`. Docs only.
The diff changes `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`
(Amendment 35 appended) and adds this report.

PR: https://github.com/AlobarQuest/orchestrator/pull/818 (#818). Amendment commit: `b32eeba8`; the PR's head is the commit that adds this line, one after `db03c85f`.

## What I built

Amendment 35 (Proposed), in the shape of Amendments 33 and 34, for the two rows
`lanes-read-app-private-key` (`.cred-consumers.toml` lines 1537-1634) and
`lanes-push-app-private-key` (lines 1636-1663). The rows match the handoff's description, so no
stop point applied. One finding the handoff didn't name: ADR-0059 Q4, Q9 and the D-11 addendum
put both keys in the hand-rotated set "with no executor" (ADR-0059 lines 663-668 and 1404-1406).
The amendment treats leaving that set as Devon's (Open item 13), not as given.

### Items

1. **The type** `github-app-key`, `Capabilities(machine_mint=False, machine_revoke=False)`,
   Bitbucket's derived plan: mint (person), store, verify, revoke (person), confirm. The mint note
   includes deleting the downloaded PEM. The class also types localops' `fork-sync-app-private-key`,
   which `check_row` refuses (Actions-secret consumers) and the proposer still reports `legacy`.
2. **Probe and control:** a JWT on `GET /app`, live 200 with the row's App id; the control is a JWT
   from an in-memory RSA key GitHub never issued. The refusal statuses are undocumented, so they're
   unmeasured.
3. **Identity:** `GET /app` id equals the row's App id, which tells the two Apps apart; a re-wrapped
   copy of the old key is refused by comparing public halves.
4. **Working proof in `verify_new`:** a `{metadata: read}` mint for one declared repository, the
   installation id checked, `GET /installation/repositories`, then `DELETE /installation/token`
   (204). Run by the executor's process on `prod-vps`; token in memory only.
5. **Readers' proof (R10):** the existing scheduled `runtime-fetch` route. Every launcher's read
   and mint is fatal or fails its ping, so a green ping from a pass that reached its read proves a
   mint with the new key. Carried check: per-launcher exit maps (the inert lander's exit 2 ends a
   pass before its read), pinned by the code PR's tests.
6. **Open, for HQ:** the inbox's `other_mints` rule blocks two mints waiting together. Options A
   (approve in order), B (a self-identifying provider binds while others wait), C (hand staging).
   Recommendation B. The deletions become a second sitting.
7. **Revoke and confirm-dead:** Devon deletes every other key pair; the alert names the new pair by
   8 characters of GitHub's public-key fingerprint. Stray hand-made pairs are a carried gap.
   `revoke_may_come_first` is set.
8. **Open, for Devon: the keepers' project.** The root can't write `SDS / Lane Apps`. A: move both
   to `Rotation / Keepers` after the Phase C revoke sitting (broker grants; the broker row's reach
   declaration changes with them). B: direct grants. C: by hand. Recommendation A.
9. **Open, for Devon: the root holding the push App's `contents: write`** on intent-packages,
   whose `main` the executor trusts. A: accept (the approval record is the gate). B: read key only.
   C: narrow the push App first. Recommendation A for development.
10. **No new network path:** `api.github.com`, already called by `github_actions.py` from the same
    process; confined client lists five requests.
11. **Row fields:** `app_id`, `installation_id`, `proof_repository`; reworded preconditions and
    comment; `rotated_by_sds = true` only once built, proven and answered.
12. **Logging and failure** (Code Brain 3, 4, 5).
13. **Open, for Devon: leave the hand-rotated set.** A both, B read key only, C neither.
    Recommendation A.
14. **Open, for Devon: the measurement.** D-82 covers only amendment 33's Coolify canary and dev
    tokens. A: the first rotation measures (control on every probe, confirm fails closed). B: a
    throwaway GitHub App. Recommendation A.
15. **The code PR's changes** (file list) and three exit checks.

Plus "What it grants, and nothing more", "Consequences" and "Passages it governs".

## Checks run on each claim

- **Rows and fields:** read `.cred-consumers.toml` 1511-1663 at `d8d303b9`; `.bws-secrets.toml`
  370-393.
- **Readers:** `git grep` of both keeper UUIDs and both App ids over the repository; a grep of
  every other `~/Projects` git repository, `~/.claude/bin` and `~/Library/LaunchAgents`. Only
  sds-hq `state/` files and codebase-review findings name them elsewhere.
- **Read fatality:** read each launcher's mint block (`sds-github-mint` grep, then the lines after
  each); found the unit verifier's exit-3 path and the inert lander's exit-2-before-read path.
- **Schedules:** each `lanes/<lane>/crontab`.
- **Executor reach:** `docs/operations/rotation-executor.md` lines 675 and 677; decision 4.
- **Inbox:** `src/rotation_worker/inbox.py` lines 1-200.
- **Every line number** in the amendment was re-read after drafting with `sed -n`, and eight were
  corrected.
- **GitHub API:** read through the web-fetch agent from the docs pages the amendment cites. Only
  the first part of the REST Apps page came back word for word, and GraphQL wasn't checked, so the
  amendment scopes the "no key API" claim to what was read. No GitHub API was called.
- **`origin/main`:** fetched before writing and before committing; no change to ADR-0055 past
  `d8d303b9`.
- **Token-shape grep of the diff:** `git diff | grep '^+' | grep -nE
  'BEGIN|-----|ghs_|ghp_|github_pat_|0\.[0-9a-f]{8}-|[A-Za-z0-9+/]{32,}|[0-9a-f]{32}'` matched one
  line, a file path (`src/orchestrator/services/verifier/rotation_evidence.py`). No key, token, PEM
  header or full keeper UUID is in the diff.
- `make check` wasn't run: the change is a Markdown ADR and this report, with no code or config.

## Files changed

- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`
- `docs/superpowers/reports/github-app-key-adapter-adr-report.md`

## Self-review

- The amendment claims no measurement. Every GitHub behavior is a documentation reading or an
  inference, and each is labeled.
- The ADR's header list of amendments stops at 32, as it did for 33 and 34; I left it alone.
- The localops row is read at localops' local `HEAD` `3431928`, not a fetched `origin/main`.

## Concerns

- **Item 6 is HQ's** and changes the inbox's shared rule under B; it decides whether the two rows
  can be rotated together without an ordering rule.
- **The per-launcher exit map** isn't read in this amendment (carried check, item 5).
- **Code Brain and Infra Brain** weren't queryable from this session; the handoff's Standards
  section is the source.

## Standards followed

- **Code Brain 3 (structured JSON logs to stdout):** item 12 plans logging only through the
  executor's existing structured logging and `contract.py`; no new log channel.
- **Code Brain 4 (never swallow an exception):** item 12: a transport failure is indeterminate and
  fails the unit; an unexpected status, id mismatch or unrevoked token fails the step by name.
- **Code Brain 5 (never log secrets):** item 12: no PEM, JWT or installation token in any log,
  exception or evidence; evidence carries fingerprints only; error bodies are never kept, as the
  existing mint does. Item 7's alert names 8 characters of a public-key hash.
- **Infra Brain 591, 592, 593 (no token in tracked files or history, no inline bootstrap
  token):** the diff holds no key, token or token-shaped literal; keepers are abbreviated
  (`314a966c-…`); the grep above confirms it.
- **Infra Brain 1032 (`cred.exposure-rotate`):** item 5: no revoke alert before every reader's
  green ping; item 11 keeps the exposure and consumer fields' form; item 7 sets
  `revoke_may_come_first` for exposure response.
- **Infra Brain 212 (HTTPS for public FQDNs):** not applicable; no endpoint is added. The
  amendment says so in "What it grants".
- **Merge bar (2026-11-06):** every claim about code, config or a decision cites a file and line at
  `d8d303b9`; every new identity, grant, move, App or proof setup is an Open item, not a grant.
- **Construction-mode ruling:** cited in item 6
  (`~/docs/software-delivery-system/2026-07-30-construction-mode-ruling.md`) for the one item HQ
  settles.
