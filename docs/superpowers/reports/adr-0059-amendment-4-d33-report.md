Outcome: done

# ADR-0059 amendment 4 (HQ D-33): build report

Worker W-86, branch `hq/adr-0059-amendment-4-d33`, base `origin/main` at `8ac53534`. PR #659
(ready, not merged). Amendment commit: `84962710`. This report is committed on top of it.

## What I built

I appended **Amendment 4 (2026-11-11)** to
`docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md`. At the base, the
last amendment was 3, so 4 is the next free number. The amendment's status is **Proposed**. It
quotes D-33's question and Devon's answer "C" (sds-hq `state/decisions.md`), and the 2026-11-09
standing answer (sds-hq `standing-answers.md`). It has the same sections as amendments 1 to 3:
Status, Decided by, Relates to, Context, Decision (five items), Consequences, and "Open before
building". It only appends to the file. It cites line numbers at `8ac53534`.

### Endpoints chosen

| Part | Source | Token |
|---|---|---|
| Members' repositories | `GET /api/v1/members`, `onboarding` and `member` (`sds_members.populations.observed`) | The lane's observer bearer, which both lanes already hold |
| Installation accounts | The distinct owners of the members' repositories. No owner name is in code. | None (derived) |
| Account type | `GET /users/{login}` (`type`: `User` or `Organization`) | The lane's GitHub token. GitHub names no permission for this endpoint. |
| Public repositories | `GET /users/{login}/repos?type=owner` (user) or `GET /orgs/{org}/repos?type=public` (organization), paged with `per_page=100` | The lane's GitHub token, Metadata read |
| Per-repository reads on public non-members | The pin watcher's caller file and the membership check's `factory-target.toml` | The lane's GitHub token, as a public read outside the mint |

GitHub Docs (read 2026-11-11) list `GET /users/{username}`, `GET /users/{username}/repos` and
`GET /orgs/{org}/repos` as available to installation access tokens. They don't list
`GET /user/repos`. "Permissions required for GitHub Apps" gives both list endpoints as Metadata
`read`, `UAT, IAT`. On 2026-11-11 I also ran an unauthenticated probe against a generic user
(`octocat`) and a generic organization (`github`); I didn't use Devon's account. The probe found
the following:

- `/users/{login}` returns `type`.
- `/users/{org}/repos` also serves an organization.
- `/orgs/{user}/repos` answers 404.

The amendment uses each type's documented endpoint. The code tests `private: false` and
`archived: false` itself, so the Mac (Devon's login) and the container (the minted token) compute
the same population. Every failure is a gap, never a sweep of the members alone. That covers an
unreadable record, no members, an unreadable type or page, and a page ceiling reached.

### What drops out (cited)

- **Pin watcher.** A private non-member's caller is no longer read (`caller_pin`, `sweep`).
  It gets no row (`_file_rows`) and no `pin-behind` alert. On the first pass under the new
  listing, an open alert for such a repository resolves with the title "Caller pin is at the
  recommendation" (`KeyedAlerts.sync`). The factory can't dispatch into a non-member, so the lost
  signal is small.
- **Membership check.** A private non-member's `factory-target.toml` is no longer read
  (`factory_targets`). The "its factory-target.toml names it, but the member record does not grant
  app_changes" disagreement is lost for such a repository (`check`, `_require`).

### ADR lines the amendment governs (at `8ac53534`)

- **Decision 2:** lines 529-531, 603-605, 609-613, 620-622 and 632-635.
- **Decision 5:** rows 783 and 788.
- **Increment 3:** lines 1010-1017.
- **Cut-over step 2:** lines 1137-1138 and 1143.
- **Consequences:** lines 1388-1390.
- **Q6:** lines 1512-1513.
- **Q9:** lines 1549-1550.
- **Amendment 3's rows:** lines 1992, 1993, 1994, 1996 and 1998.

Each passage has a disposition in the amendment's table. Most stand, some with the precondition
that the new listing lands and runs on the Mac before either lane's cut-over. Lines 609-613 are
marked incomplete for these two lanes. Lines 1388-1390 are narrowed in meaning.

### Open before building

1. An installation account that owns no member isn't listed. The increment that installs the
   read-only App on a second account resolves it.
2. Whether a minted token reads a public non-member. The pin watcher's container `--dry-run` in
   cut-over step 1 confirms it live. If GitHub refuses, the fallback is no token on the public
   calls, which option C allows.
3. How the pin watcher reads the member record: it imports `sds_members` (the isolation row
   changes), or the launcher passes the list. The increment 3 listing change decides it.
4. Members in more than one account: the mint refuses multi-owner requests, and each lane has one
   token variable. The same increment as item 1 resolves it.

## Tests and checks run

- `make check`, with my own database `orchestrator_test_hq_w_86` (created, then migrated with
  `alembic upgrade head`), `SECURITY_STANDARDS_DIR` set to the fixtures, and `PGTZ=UTC`: exit 0.
  pytest ran under xdist (10 workers), which prints no collected line. The run had **14,378
  tests: 14,377 passed and 1 skipped**. Lint, type checks and the other checks ended with
  "All checks passed!".
- Security scan from the repo root (`security_scan.cli . --category security`): exit 0, **0
  BLOCK**, 2 WARN. Both WARNs are the gitignore-coverage pattern check, which this docs-only
  diff doesn't touch.
- I checked every line citation in the amendment against the files at the base and corrected
  ten.

## Files changed

- `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md`: appends
  amendment 4 (about 260 lines).
- `docs/superpowers/reports/adr-0059-amendment-4-d33-report.md`: this report.

## Self-review

- The design reads the accounts from the members' owners, not from `GET /app/installations`.
  That endpoint needs the App's JWT, which the lane program doesn't hold, and the Mac path has no
  App key until increment 6. So it would split the one code path. The amendment states the
  resulting gap as an open item.
- The membership check's `owned` also feeds `canonical` and every `_spelled` call. So the
  amendment makes `owned` the whole union, never the public part alone, and a private member
  (`sds-canary`) doesn't become a false landing-policy gap.
- The public-read assumption rests on docs, as the increment 3 verify's own self-review says. It's
  an open item with a live check and a fallback, not a stated fact.

## Concerns

- `RUNNER_REPOSITORY = "AlobarQuest/factory-runner"` (`src/pin_watcher/github.py`, line 34) is an
  owner name in code. It names one fixed repository and doesn't find repositories, so the
  standing answer's listing rule doesn't reach it. The amendment doesn't change it. HQ can decide
  whether the standing answer is meant to cover it.
- The `/users/{org}/repos` behavior that the probe found is undocumented. The amendment doesn't
  rely on it.
- The amendment's Context says the read-only App is installed on the 14 members, as of the
  increment 3 verify. I didn't re-read the installation's live repository list.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** doesn't apply. The diff is docs only
  and adds no logging.
- **Code Brain rule 4 (never swallow an exception silently):** the specified listing fails
  loudly. The per-account page ceiling raises instead of truncating. An unreadable member record,
  an empty population, or an unreadable account type or page makes the whole listing unreadable.
  The pin watcher then exits 3, on a dry run too, and the membership check records a gap and
  exits 3. A listing failure never produces a clean pass or a sweep of the members alone.
- **Code Brain rule 5 (never log secrets):** the amendment names secret ids only (the observer
  bearer `f793576f-…`) and no secret values.
- **Infra Brain 591, 592 and 593 (no BWS token in tracked files or history, no inline
  `BWS_ACCESS_TOKEN` value, placeholders only):** the diff holds no token, no
  `BWS_ACCESS_TOKEN` value, and no token-shaped example. The security scan reports 0 BLOCK.
- **Infra Brain 1032 (exposure means rotate):** I saw no secret value during the work. I read the
  launchers' UUIDs and never fetched a secret.
- **Infra Brain 212 (HTTPS for public FQDNs):** doesn't apply. The diff is docs only, and every
  endpoint it names is `https://api.github.com` or the orchestrator's HTTPS API.
