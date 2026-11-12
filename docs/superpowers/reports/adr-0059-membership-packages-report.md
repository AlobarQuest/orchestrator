Outcome: done

# Report: adr-0059-membership-packages (worker W-101)

## What I built

I appended **ADR-0059 amendment 5** ("the membership check clones intent-packages anonymously each
pass"), status Proposed, to
`docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md`. At base
`a6442254` the last amendment was amendment 4 (line 2133), so this is number 5. The amendment only
appends: no earlier line is rewritten. It cites line numbers at `a6442254`.

The amendment records HQ's mechanism. On the container path, the launcher runs
`git clone --quiet --depth 1 --branch main https://github.com/AlobarQuest/intent-packages.git "$HOME/intent-packages"`.
It runs with `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1` and `GIT_TERMINAL_PROMPT=0`,
and never through `gh repo clone`. The clone sits in the pass's `HOME`, which `lane-run` creates
new for each pass and deletes on exit (`scripts/lane_run.py`, lines 86-90). The launcher then
exports `BUMP_PROPOSER_PACKAGES_CHECKOUT`, which joins no `lane-run` list. The mechanism uses no
token, no `sds-git.sh`, no venv and no Python change. The Mac path keeps its current checkout.
Giving the Mac a per-pass clone is marked as optional follow-up. The amendment rejects three
options, each with one reason: an API reader (it can drift from `discover()`), `RepoMirror` (it
can't list a directory), and the minted token (it widens the token's use for no gain).

### ADR lines the amendment changes (by disposition, at `a6442254`)

| Passage | Disposition |
|---|---|
| Decision 1, the clone lanes (306-310) | Extended: the membership check is added, with an anonymous clone. |
| Decision 1, the checkout variables (315-317) | Stands: a third container setter for `BUMP_PROPOSER_PACKAGES_CHECKOUT`. |
| Decision 1, the lane-state bullet (351-353) | Stands: there's no volume. |
| Decision 2, the anonymous-clone exception (541-543) | Extended: a second exception, for intent-packages. |
| Decision 2, the helper call (561-562, 587) | The lane clones without calling the helper. |
| Decision 2, the launcher bullet (589-591) | Stands for the clone. There's no helper call. |
| Decision 2, the mint and Devon-login repositories (603-605, 609-613) | Stands: intent-packages stays outside this lane's mint. |
| Decision 2, the install set (620-622) | Stands: the clone adds nothing to it. |
| **Decision 5, line 788** | **Reworded:** "Compares the member record with declarations through APIs, and with the standing dependency-update packages in an anonymous per-pass clone of intent-packages." |
| Increment 4, `sds-git.sh` "with the first lane that clones" (1025-1026) | Stands: the first helper-using clone lane is still increment 4's. |
| Cut-over step 2 (1143-1145) | Stands, with one addition: the paired pass records the Mac checkout's HEAD beside the clone's commit. |
| Amendment 3 rows (1992, 1998) | Stand. The `forks()` call is cited again at `cli.py` line 170. |
| Amendment 4 row (2326) | Superseded for its wording of line 788. |

### Secrets (item 3), from `.bws-secrets.toml` at `a6442254`

| Secret | ID | Approval |
|---|---|---|
| Observer bearer | `f793576f-…` | Decision 2's identity bullet, lines 487-490 (the launcher fetches it today). The manifest's `lanes` list doesn't name the lane yet. |
| change-manager READ bearer | `314f276d-…` | The same bullet, lines 487-490: the launcher fetches it today (`run-membership-check.sh`, lines 42 and 79). So the lane's account gets its grant at the identity gate. **No question was needed.** The entry has no `lanes` field yet. |
| Read-only App key | `314a966c-…` | Lines 490-491 and 646-648, and Amendment 3's line 1993 (the read-only set of eleven includes the membership check). |
| Ping-URL secret | Not created yet | Decision 3, lines 688-698, created at the lane's cut-over. |
| Healthchecks Management key | `51509d5e-…` | Never read in the container: lines 488-490 and decision 3, lines 677-687. |

The amendment grants no new permission, App installation or credential.

### Open items, as the amendment records them

1. Prove the anonymous clone in the container. This is resolved at the lane's cut-over step 1.
   My probe ran on the Mac.
2. Decide whether the launcher writes the URL as a literal or derives it from
   `PACKAGES_REPOSITORY`. This is resolved by plan step 3e-6, with an agreement test if it uses
   a literal.
3. Decide whether the Mac path also gets a per-pass clone. This is optional follow-up, or ends
   with increment 6.

## Tests and checks run

- **Mechanism probe (Mac, 2026-11-11).**
  - An anonymous shallow clone under `env -i` with an empty `HOME`, no git configuration and
    prompts off succeeded. `discover()` on the clone returned 5 standing packages, targeting
    `AlobarQuest/infraops-mcp-server` and `AlobarQuest/orchestrator`.
  - `discover()` on an absent directory raised `StandingError`.
  - An anonymous clone of a repository that isn't public failed at once, with exit 128.
  - The probe directory, `/tmp/w101-probe.SVeN`, still exists. It holds only the public clone.
    The permission rules denied my `rm -rf` of it, and I didn't work around the rule.
- **`make check`** against `orchestrator_test_hq_w_101`, with `PGTZ=UTC`: exit 0, lint and
  pyright clean. 14,622 items were collected: 14,621 passed and 1 was skipped.
- **Security scan from the repo root:** 0 BLOCK. The 2 WARN findings
  (`bws.secret-files-gitignored`, `bws.manifest-matches-usage`) and the 1 INFO finding
  (`bws.least-privilege-scope`) are repository-wide findings, not caused by this diff.
- **PR #670**, ready (not a draft). The amendment commit is `652fea4e`. The report commit follows
  it on the same branch. I didn't merge the PR.

## Files changed

- `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md`: amendment 5
  appended, with 205 added lines.
- `docs/superpowers/reports/adr-0059-membership-packages-report.md`: this report.

## Self-review

- I checked each source fact the handoff gave against `a6442254`, and all of them held. The
  repository is `PUBLIC`. `discover()` reads files only and raises on a missing `packages/`.
  `RepoMirror` can't list a directory. The image has `git` and `gh`.
- I checked the handoff's no-empty-population claim against `consistency.py`. The standing
  direction is `_require`, which is one-way, so an empty list can hide findings but can't invent
  a disagreement. The gap reports the loss.
- The amendment states the exit code exactly. A gap exits 3 only when nothing disagrees, and
  either way the pass pings `/fail`. The launcher must not exit 1 on a failed clone.
- I corrected the handoff's "nothing keeps it fresh" claim: three other lanes' acting runs
  fast-forward the Mac checkout (#668). The membership check itself doesn't.
- Amendment 4's citation of the `forks()` call at line 167 has drifted to 170 at `a6442254`.
  The amendment cites 170.

## Concerns

- The ping-URL secret's name, `membership-check-ping-url`, follows the other lanes' pattern. The
  name isn't fixed until the VM root creates the secret.
- `rtk` isn't on this session's `PATH`, so I ran `make` directly into a log.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** doesn't apply. The change is
  documentation only, and it adds no logging code.
- **Code Brain rule 4 (never swallow an exception silently):** the amendment requires the clone
  to fail loudly. git's error goes into the pass's report. `discover()` raises `StandingError`,
  which becomes a named gap, and the pass exits 3 (or 2) and pings `/fail`. It's never an empty
  population and never a silent exit 0 (decision item 5).
- **Code Brain rule 5 (never log secrets):** the amendment names secret IDs by prefix (`…`) only,
  never a value. The clone carries no credential, and git's configuration is reset so that no
  stored credential applies.
- **Infra Brain 591, 592, 593 (no BWS token in tracked files or history; no inline
  `BWS_ACCESS_TOKEN` value; examples are placeholders):** the diff holds no token and no
  `BWS_ACCESS_TOKEN` value. It names the variable only. The session-end scan reports 0 BLOCK.
- **Infra Brain 1032 (exposure means rotate):** I saw no secret value during the work. The scan
  and my reads printed none. This session made no BWS or Coolify read.
- **Infra Brain 212 (HTTPS for public FQDNs):** doesn't apply. The change is documentation only,
  and the one URL it names is `https://github.com/AlobarQuest/intent-packages.git`.
