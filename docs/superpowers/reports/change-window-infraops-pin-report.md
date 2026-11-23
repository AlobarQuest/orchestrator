Outcome: done

# Report: the change-window lane runs infraops at a pinned commit (W-278)

Handoff: sds-hq `state/handoffs/change-window-infraops-pin.md`. PR #795. Branch
`hq/change-window-infraops-pin`, base `origin/main` `d96a6e44`. Nothing was released or deployed.

## What I built

1. **ADR-0059 Amendment 18 (Proposed, appended only):** "change-window runs
   infraops-mcp-server from a pin baked into the lane image (D-70 A, D-66 B)". It states what the
   pin attests, the no-secret fetch, the bump procedure, the stale-pin signal as a verify item,
   the passages it governs (cited at `d96a6e44`), the untouched Mac job, and rollback.
2. **`lanes/Dockerfile`:** `ARG INFRAOPS_COMMIT=8dc4d43e309499e7420848177e1adabc8479f418` and one
   `ADD --checksum=sha256:cb94a415ec9395bfb4ca4431fd126dc947ad5ef046aca1d3efb1bb5d91b1f228` of
   `https://raw.githubusercontent.com/AlobarQuest/infraops-mcp-server/${INFRAOPS_COMMIT}/dist/bundle/change-mgr-cli.js`
   in the `tools` stage. It's installed `0444` and copied to `/app/infraops/change-mgr-cli.js`
   in the final stage, root-owned in a root-owned `0755` directory.
3. **`scripts/run-change-window.sh`:** runs `$REPO_ROOT/infraops/change-mgr-cli.js` (that's
   `/app/infraops/...` in the image). The clone, the `sds-cred-registries.sh` source and the
   clone-failure exit are gone. A missing bundle is still exit 2, checked before any secret
   read. Its `FATAL:` line is reworded to name the baked file: `FATAL: the lane image has no
   infraops/change-mgr-cli.js (lanes/Dockerfile's pinned bundle): the window can't run`. Every
   other exit code and `FATAL:` line is unchanged. The header comment is updated. The path comes from the launcher's own
   location, not from an environment variable, so there's no seam that could choose the code run
   with production secrets.
4. **Tests:**
   - `tests/scripts/test_run_change_window.py`: a pass runs `git` zero times; the launcher's text
     holds exactly `BUNDLE="$REPO_ROOT/infraops/change-mgr-cli.js"` and no clone helper, no
     `infraops-mcp-server` and no `git`. The clone-failure tests are removed.
   - `tests/architecture/test_lane_image.py`: six checked downloads. The infraops pin is a
     40-hex commit with an exact HTTPS raw URL and a sha256 checksum. The installed name and
     mode, the final `COPY` (no `--chown`), and the launcher's path all agree.
5. **Docs:**
   - `docs/operations/deploy.md`: the change-window paragraph describes the baked bundle, and a
     new subsection, "Bump change-window's infraops pin", gives the five-step bump and the rollback.
   - `lanes/change-window/compose.yaml` is unchanged, because it's on the do-not-touch list. Its
     header comment is now stale (follow-up 5).

## What the pin attests

- **The digest attests the bytes only.** BuildKit fails the build on a mismatch. I proved this
  in a scratch build with a wrong digest, which failed with `digest mismatch`.
- **The commit is a locator.** The orchestrator build doesn't check that the bytes came from it.
- **"Built from that commit's source" rests on infraops' `Build` job.** It runs `npm ci` and
  `npm run build`, then fails on any `git status --porcelain dist/` change, on every pull request
  and every push to `main`.
  - `build` is a required check on infraops' `main`, but `enforce_admins` is off and no review is
    required.
  - So the bump procedure confirms that the `Build` push run on the target commit succeeded. On
    `8dc4d43e` it did (read 2026-11-23).
- **The pinned bytes equal infraops `main`.** `8dc4d43e` and `fdf3e91b` (#171, the bundle's only
  change) hold the same bytes, so the pin equals what the lane ran from `main` on 2026-11-23.

Two facts found and designed around:

- **The bundle's filename is part of its contract.** It runs `main()` only when `argv[1]` ends
  with `change-mgr-cli.js`, and otherwise exits 0 having done nothing. A renamed file would make
  a `--submit` pass report a green window. The baked file keeps its name, and a test holds the
  Dockerfile and the launcher to it.
- **The bundle reads no sibling file.** I ran it alone, with no `package.json`, on Node 26.

## The bump path

A person (HQ or a build session) bumps the pin; no lane proposes it. The bump is one pull request
that changes `INFRAOPS_COMMIT` and the digest together. Before the pull request, the session:

1. Picks a full commit on infraops' `main`.
2. Confirms that commit's `Build` push run succeeded (`gh run list ... --workflow Build --event
   push`).
3. Hashes the file fetched from the commit URL. It fetches to a file first, never through a pipe.

The bump then needs a lane image release and a redeploy of `sds-lane-change-window`. The procedure
is in `deploy.md`, "Bump change-window's infraops pin".

**The stale-pin signal (design only, verify item):** a keyed alert,
`infraops-pin-behind:change-window`. It fires when the sha256 of the bundle at infraops `main`
differs from the pinned digest, judged on the digest, not on the commit. The pin watcher is the
candidate owner. The amendment says HQ records the step that builds it in `state/goal.md` with the
merge, and the security phase doesn't start while it's open. The amendment names the step
`cw-infraops-pin-staleness`. That name is my proposal, not an existing HQ step: **HQ, record it in
`state/goal.md` at merge, or rename it in the amendment.**

## Tests and checks

- **Touched suites:** `tests/scripts/test_run_change_window.py` and
  `tests/architecture/test_lane_image.py`, 69 passed.
- **Mutations:** each mutation ran against both suites, with `__pycache__` cleared and the files
  restored from backup copies. The test databases (`orchestrator_test_hq_w_278` and its xdist
  copies) are dropped. The control is 69 passed, and all six mutations are killed:

  | Mutation | Result |
  |---|---|
  | M1: the old launcher (clones `main`) | 44 failed, including `test_a_pass_clones_nothing_...` and `test_the_baked_bundle_is_read_only_...` |
  | M2: the infraops `ADD` without `--checksum` | 2 failed (`test_every_download_is_checked...`, `test_the_infraops_bundle_is_pinned...`) |
  | M3: `INFRAOPS_COMMIT=main` | 1 failed (`test_the_infraops_bundle_is_pinned...`) |
  | M4: the bundle renamed in the image | 1 failed (`test_the_baked_bundle_is_read_only_and_where_the_launcher_runs_it`) |
  | M5: the bundle path from an environment variable | 2 failed (the image agreement test and `test_the_launcher_runs_the_baked_bundle_and_clones_nothing`) |
  | M6: a `git clone` call added back | 5 failed (the behavioral no-`git` tests on token and socket, and the text test) |

- **Real image build:** I built `lanes/Dockerfile` locally with BuildKit (`--load`, no push) on
  `ghcr.io/alobarquest/orchestrator:sha-957bcebd…`, then checked it with `--network none`:
  - As `orchestrator`, `/app` and `/app/infraops` are `root:root 755`, the bundle is
    `root:root 444` with sha256 `cb94a415…`, and the directory isn't writable.
  - `node /app/infraops/change-mgr-cli.js run-window --bogus` prints
    `unknown run-window flag: --bogus` and exits 1. A dry run against an unreachable host exits 1
    with `fetch failed`. So the bundle runs under its name in the image.
  - The launcher outside `lane-run` refuses with its one `FATAL:` line and exit 1.
  - I removed the image afterward.
- **`make check`:** green (exit 0): ruff, formatting and pyright clean; pytest 14,707 passed and 1 skipped, out of 14,708 collected. It ran on the first commit; the second changes only `deploy.md`.
- **CI on PR #795** (head `ad24bd4d`): `Quality` passed with 14,694 passed and 14 skipped (14,708, the same total as local); `Rollout transcription currency` and `Runner consumer compatibility` passed.
- **Risk:** `code-standards risk --repo .` printed `normal`, so vendor-review doesn't apply.
- **`/code-review` (medium):** no correctness bug, and four nits:
  - Fixed: the bump's digest step piped `curl` into `shasum`, which hashes an error body. It now
    fetches to a file and hashes it only on success.
  - Not changed: the image test is text-only. It's a drift guard, and the ADR's second verify item
    (the bare `lane-run` in the redeployed container) is the run proof.
  - Not changed: the stale-pin signal isn't built. It's a verify item by design.
  - Not changed: the launcher checks `-f`, not `-s`. The image's digest check rules out an empty
    or truncated bundle, and the name is held by a test.

## Files changed

- `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md` (Amendment 18)
- `lanes/Dockerfile`
- `scripts/run-change-window.sh`
- `tests/scripts/test_run_change_window.py`
- `tests/architecture/test_lane_image.py`
- `docs/operations/deploy.md`
- This report

## Self-review

- **The handoff's do-not-touch files are untouched.** `sds-cred-registries.sh` and
  `sds_anonymous_clone` are unchanged; the rotation proposer still uses them. The other files are
  `.cred-consumers.toml`, `rotation-executor.md`, `tests/rotation_worker/`, every other launcher
  and compose, and the main tree.
- **Network paths:** no new one at run time. The lane loses its github.com clone path, and the
  fetch is the Actions runner's at build time. No new credential, BuildKit secret, image or source
  build.
- **Amendment review fixes:** after the first draft, I removed three overclaims from the
  amendment: "reviewed" bytes, "staleness costs fixes, not safety", and "every `FATAL:` line
  unchanged". I also added the missed passage, Amendment 14 decision item 7's anonymous-clone
  sentence (lines 4327-4328), to its table. A grep of Amendments 14 and 15 for `clone`, `main` and
  `unpinned` finds no other passage left out.
- **Stop point:** none was hit. The amendment found no false statement about current code, only
  line-number drift in Amendment 14's launcher cites (its "lines 111-121", "line 93" and "lines
  151-153" are 109-119, 101 and 152 at `d96a6e44`). It records that drift without rewriting the
  earlier text.

## Concerns and follow-ups

1. **The stale-pin step:** HQ needs to record it in `state/goal.md` with `cw-infraops-pin-merge`,
   as the amendment requires (verify item 1).
2. **Release order:** the release (`cw-infraops-pin-release`, `--lane change-window`) goes only
   after `change-window-schedule-on` is verified. The residual ends at that redeploy, not at merge.
3. **Rollback:** redeploying a lane tag from before this change brings back the clone of `main`
   and Amendment 14's residual.
4. **Pin age:** `INFRAOPS_COMMIT` is `8dc4d43e`, infraops' `main` on 2026-11-23. If infraops
   changes the bundle before the release, bump the pin first.
5. **Stale compose comment:** `lanes/change-window/compose.yaml` lines 8-9 still say "each pass
   clones infraops-mcp-server into a directory it removes". I left the file alone, as the handoff
   requires. A follow-up can make it read "runs the infraops bundle the image bakes at a pinned
   commit (ADR-0059 Amendment 18)"; no test reads that comment.
6. **A concern the Standards section doesn't cover:** pin bumps are a dependency update of a kind
   the handoff's standards don't govern (Code Brain's `dependency-updates` road has no rules). The
   amendment's bump procedure is the only rule for them.
7. **What the amendment still leaves:** the bundle is trusted as infraops' `main` held it at the
   pinned commit. Nothing reviews its source, because infraops requires no review and the bump
   checks the commit, the `Build` run and the digest. A later amendment can add a step that reads
   the source diff between pins. This amendment doesn't claim one.

## Standards followed

- **Code Brain 3 (structured logs):** no new logging system. The launcher keeps its stderr
  `FATAL:` lines and outcome report.
- **Code Brain 4 (never swallow an error):** `set -uo pipefail` is unchanged, and no `|| true`
  was added. A missing bundle fails closed with exit 2.
- **Code Brain 5 (never log secrets):** no secret, token or body is logged. The existing
  no-secret-in-argv and no-secret-in-output tests still pass.
- **Infra Brain 591 and 592 (no BWS token in tracked files or history):** none is added. The only
  hex strings are a git commit and a sha256 digest.
- **Infra Brain 593 (no inline `BWS_ACCESS_TOKEN`):** none.
- **Infra Brain 1032 (exposure, rotate):** no real credential appeared in any output.
- **Infra Brain 212 (HTTPS):** the fetch URL is `https://raw.githubusercontent.com/...`, which a
  test asserts.
- **Infra Brain 2 (no secrets committed):** none.
- **Infra Brain 3 (no `:latest`):** the pin is an exact 40-character commit plus a digest. A test
  refuses anything else, and mutation M3 (`main`) fails it.
- **Infra Brain 1 (no source build on the VPS):** the bundle is prebuilt. The image builds in
  `Release image` on GitHub Actions, and nothing builds on the VPS.
- **Infra Brain 5 (redeploy after push):** not exercised. The release and redeploy are HQ's.
- **Infra Brain 4, 6 and 211:** not applicable, as the handoff says.
- **Orchestrator `CLAUDE.md`:** `make check` ran (preceding section). No new file needed a
  `layers.toml` entry beyond this report's directory pattern.
