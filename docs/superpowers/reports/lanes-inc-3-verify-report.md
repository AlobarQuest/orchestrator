Outcome: done

# Verify before ADR-0059 increment 3: report

Worker W-60, branch `hq/lanes-inc-3-verify`, base `origin/main` at `fb485d2c`. Read-only
investigation; the only change is this report. No production act, no token mint, no BWS, Coolify,
VM or Healthchecks call, and no `gh` call that writes. GitHub was read with `gh api` GETs only.
GitHub Docs were read on 2026-11-09 (the per-endpoint "Fine-grained access tokens" sections are
quoted from the rendered `docs.github.com/en/rest/...` pages).

## Summary

- **One finding needs a decision before the pin watcher and membership check move.** Both lanes
  list their population with `GET /user/repos?affiliation=owner`, which GitHub Docs say
  installation tokens can't call. As written, both fail on any App token. The replacement,
  `GET /installation/repositories`, returns only installed repositories: today the account has 65
  non-archived owned repositories (33 private, 32 public), and the ADR's install set covers about
  14 of them. So either the read-only App is installed on every account repository (wider than
  the ADR), or both sweeps lose every non-member repository. Item 5 has the detail.
- **Every other lane fits D-18's six read permissions.** No lane writes with its GitHub token, and
  none uses GraphQL, the Checks API, commit statuses, deployments or releases on a member.
- **Public reads need no install.** Every endpoint the lanes use on factory-runner and the
  upstreams documents "can be used without authentication or the aforementioned permissions if
  only public resources are requested." factory-runner is still in the install set because it's
  a member, and the unit verifier's branch-protection read has no public path.
- **A narrowed mint doesn't confine public reads.** The repository list and permission subset
  bound what the token is *granted*; they don't stop it reading public resources, which anyone
  can read unauthenticated. Every repository in the install set is public today.
- **"The member record's repository" names nothing a lane reads.** Every lane reads the record
  from `GET /api/v1/members` on the orchestrator. Every repository the ADR names by role is
  already a member, so the read-only App's install set is the live member list.

## 1. factory-runner's install (Q9)

**Answer.** No lane needs the read-only App installed on factory-runner to read it: every
factory-runner read is a public read. factory-runner still belongs in the install set, for two
reasons: it's a member (`docs/operations/membership-seed.toml:165`, `dependency_updates` granted
at `:175`), and the unit verifier reads `main`'s branch protection on each bump unit's target,
which has no public path (item 5, unit verifier).

factory-runner is public (`gh api repos/AlobarQuest/factory-runner`: `"visibility":"public"`).

The lane calls that read factory-runner:

| Lane | Call | Endpoint | Permission if private |
|---|---|---|---|
| Fork-sync watch | `forks`, `src/fork_sync/watch.py:156` (`REGISTRY_REPO`, `src/fork_sync/onboarding.py:50`) | `GET /repos/AlobarQuest/factory-runner/contents/.cred-consumers.toml?ref=main` | Contents read |
| Pin watcher | `recommendation`, `src/pin_watcher/github.py:135` (`RUNNER_REPOSITORY`, `:34`) | `GET /repos/AlobarQuest/factory-runner/contents/RECOMMENDED_CALLER_PIN` | Contents read |
| Pin watcher | `src/pin_watcher/github.py:192` | `GET /repos/AlobarQuest/factory-runner/compare/{a}...{b}` | Contents read |
| Pin watcher | `src/pin_watcher/github.py:197` | `GET /repos/AlobarQuest/factory-runner/commits/{sha}` | Contents read |
| Membership check | `forks(subprocess_runner)`, `src/membership_check/cli.py:167`, which runs `watch.py:156` through `gh` | Same as the fork-sync watch's | Contents read |
| Membership check | `RepoMirror`, `src/membership_check/cli.py:118`, over `REGISTRY_REPOSITORIES` (`src/rotation_worker/registry.py:26-31`) | git fetch, no credential (`src/rotation_worker/git_mirror.py`) | Not applicable |

GitHub Docs, per endpoint:

- "Get repository content": "This endpoint works with the following fine-grained token types:
  GitHub App user access tokens, GitHub App installation access tokens, Fine-grained personal
  access tokens. The fine-grained token must have the following permission set: "Contents"
  repository permissions (read). This endpoint can be used without authentication or the
  aforementioned permissions if only public resources are requested."
- "Compare two commits" and "Get a commit": the same wording, "Contents" (read), with the same
  public-resources sentence.
- "Permissions required for GitHub Apps": "These permissions are required to access private
  resources. Some endpoints can also be used to access public resources without these
  permissions. To see whether an endpoint can access public resources without a permission, see
  the documentation for that endpoint."

**Confidence: high for the reads, from the docs.** No page says in one sentence that an
installation token reads a public repository *outside its installation*. The per-endpoint clause
covers it ("without authentication"), but it's an inference from that clause. Item 2 names the
call that settles it.

## 2. Upstream public releases

**Answer.** Yes, by the same documented clause. The fork-sync watch reads each upstream's
releases at `src/fork_sync/watch.py:279`: `GET /repos/{config.upstream}/releases?per_page=100`,
reached from `classify` (`:290`) through `_release_reasons` (`:272`) when a fork has no open sync
pull request (`:302-308`). `gh_api` (`src/fork_sync/gh.py:33-44`) runs `gh api <path>`, so the
token is whatever `gh` takes from `GH_TOKEN`.

GitHub Docs, "List releases": "This endpoint works with the following fine-grained token types:
GitHub App user access tokens, GitHub App installation access tokens, Fine-grained personal access
tokens. The fine-grained token must have the following permission set: "Contents" repository
permissions (read). This endpoint can be used without authentication or the aforementioned
permissions if only public resources are requested." The page also says: "Information about
published releases are available to everyone."

The five upstreams, read from each fork's `.github/fork-sync.toml` on `main`, are all public:
`rtk-ai/rtk`, `nyldn/claude-octopus`, `morluto/rea`, `EtienneLescot/n8n-as-code` and
`EtienneLescot/n8n-manager`. They belong to other owners, so the read-only App can never be
installed on them, and a mint can't name them: "The installation access token cannot be granted
access to repositories that the installation was not granted access to." The fork-sync watch's
mint must name only the forks and factory-runner. An upstream read relies on the public clause
alone, and it counts against the installation's rate limit, not the unauthenticated one.

**Confidence: high from the docs; not proven live.** The call that settles items 1 and 2, made
after Devon creates and installs the read-only App, and by the increment, not here:

1. Mint a read-only App token narrowed to one fork: `repositories: ["rtk"]`,
   `permissions: {"metadata": "read"}`.
2. With it, `GET /repos/rtk-ai/rtk/releases?per_page=1` and
   `GET /repos/AlobarQuest/factory-runner/contents/.cred-consumers.toml`. Both are expected to
   answer 200.
3. As the discriminating control, `GET /repos/AlobarQuest/rtk/branches/main/protection` with the
   same token is expected to refuse (no Administration in the request). Without a refusal on the
   control, the two 200s prove nothing.

## 3. Narrowed mint

**Answer.** The mint narrows what the token is granted; it doesn't stop public reads. GitHub
Docs, "Generating an installation access token for a GitHub App":

- "Optionally, you can use the `repositories` or `repository_ids` body parameters to specify
  individual repositories that the installation access token can access."
- "If you don't use `repositories` or `repository_ids` to grant access to specific repositories,
  the installation access token will have access to all repositories that the installation was
  granted access to."
- "The installation access token cannot be granted access to repositories that the installation
  was not granted access to."
- "Optionally, use the `permissions` body parameter to specify the permissions that the
  installation access token should have."
- "If `permissions` is not specified, the installation access token will have all of the
  permissions that were granted to the app."
- "The installation access token cannot be granted permissions that the app was not granted."
- "The installation access token will expire after 1 hour."

The REST reference ("Create an installation access token for an app",
`POST /app/installations/{installation_id}/access_tokens`) adds: "Up to 500 repositories can be
listed in this manner." It describes `repositories` as "List of repository names that the token
should have access to" (names, not `owner/name`, within the installation's one account), and
`repository_ids` as "List of repository IDs that the token should have access to." It also says:
"You must use a JWT to access this endpoint."

**Documented exceptions.** The docs don't use the word "refuse." They bound what is *granted*.
The exception is public resources: every per-endpoint section quoted in items 1, 2 and 5 that
carries the public-resources sentence is readable outside the token's repositories and
permissions, because it's readable with no token at all. Metadata isn't an exception in the
mint docs; a narrowed request names it like any other permission. The branch-protection endpoints
carry no public-resources sentence, so they're bounded by the mint.

**What this means for the ADR's sentence** ("a token that refuses everything outside the
request"). For private resources and non-public endpoints the docs support it. For public
resources it's false, and every repository in the install set is public today. The narrowing still
limits what a leaked token adds beyond anonymous access: Administration reads, private
repositories, and the installation's rate limit.

**The in-process proof** is the mint response, which echoes `permissions` and `repositories`. The
rotation App already checks both (`src/rotation_worker/github_actions.py:119-125`): the
permission dict must equal the request, and the casefolded repository set must equal the request.
A lane mint helper must make the same check (item 7).

**Confidence: high** (direct quotes).

## 4. The exact install set

### The read-only App

Sources:

- **Members**, from the seed (`docs/operations/membership-seed.toml` at `fb485d2c`). The seed is
  stale: it lists 13 and the live record holds 14. The 14th isn't reachable from this session, so
  **HQ must take the install list from the live record** (`GET /api/v1/members`, every member in
  `onboarding` or `member`).
- **"The member record's repository"** (ADR-0059 lines 611 and 614) names no repository any lane
  reads. Every lane reads the record from the orchestrator: `GET /api/v1/members`
  (`src/sds_members/client.py:179`), through `scripts/sds_scope.py` or `read_members`. The seed
  file has no runtime reader (only `layers.toml` and `tests/sds_members/test_seed.py` name it). If
  HQ means `AlobarQuest/orchestrator`, where the seed and the record's code live, it's already a
  member.
- **Standards repositories the lanes clone.** None of the seven increment-3 lanes clones. Today
  the Mac launchers use `~/Projects` checkouts: `scripts/run-bump-driver.sh:106-107`
  (intent-packages' venv, `project-standards/src`, `security-standards/src`),
  `scripts/run-bump-proposer.sh:123` and `scripts/run-rotation-proposer.sh:79` (intent-packages),
  and `scripts/run-rotation-proposer.sh:80` (infraops-mcp-server). ADR-0059 decision 1 (lines
  306-310) turns these into per-pass clones in increment 4. All four repositories are members.
- **intent-packages** (D-17): a member.
- **factory-runner and the forks**, from factory-runner's `.cred-consumers.toml` on `main`
  (`main` at `e1fb580aac7b4e72d6458fd10c7f639969cafa51`; the file last changed at
  `4b1580f7246b7a12f08b33181dc691ee50487133`). The forks are the `gh-actions-secret` consumers
  named `APP_PRIVATE_KEY` of credential `fork-sync-app-private-key` (the selection in
  `src/fork_sync/watch.py:154-175`): rtk, claude-octopus, rea, n8n-as-code and n8n-manager. All
  five, and factory-runner, are members.

| Repository | Seed line | Other sources |
|---|---|---|
| `AlobarQuest/brain` | 5 | |
| `AlobarQuest/change-manager` | 44 | `REGISTRY_REPOSITORIES` |
| `AlobarQuest/orchestrator` | 72 | `REGISTRY_REPOSITORIES`; the record's code |
| `AlobarQuest/infraops-mcp-server` | 101 | `REGISTRY_REPOSITORIES`; rotation proposer checkout |
| `AlobarQuest/intent-packages` | 125 | D-17; both proposers' checkout; bump driver |
| `AlobarQuest/security-standards` | 145 | bump driver `FACTORY_PYTHONPATH` |
| `AlobarQuest/factory-runner` | 165 | `REGISTRY_REPO`; `REGISTRY_REPOSITORIES`; `RUNNER_REPOSITORY` |
| `AlobarQuest/project-standards` | 183 | bump driver `FACTORY_PYTHONPATH` |
| `AlobarQuest/rtk` | 197 | fork (registry) |
| `AlobarQuest/claude-octopus` | 215 | fork (registry) |
| `AlobarQuest/rea` | 233 | fork (registry) |
| `AlobarQuest/n8n-as-code` | 251 | fork (registry) |
| `AlobarQuest/n8n-manager` | 269 | fork (registry) |
| The 14th live member | not in the seed | Live record only |

So the read-only App's set is the live member list, 14 repositories, unless HQ decides the
`/user/repos` question (item 5) by installing it more widely. Every repository in the table is
public.

### The "SDS lanes" App

Six repositories: `AlobarQuest/intent-packages` and the five forks (`AlobarQuest/rtk`,
`AlobarQuest/claude-octopus`, `AlobarQuest/rea`, `AlobarQuest/n8n-as-code`,
`AlobarQuest/n8n-manager`).

**Confidence: high** for the seed and the registry; the 14th member is unknown to this session.

## 5. Per-lane needs

Every client in these lanes allows GET only: `src/landing_ledger/github.py:85-86`,
`src/pin_watcher/github.py`, `src/deploy_watcher/github.py:43`, `src/revision_watcher/github.py:28`,
`src/membership_check/declaration.py:110,119` and `auto_merge.py:51`, and
`src/unit_verifier/required_checks.py:20`. No lane writes with its GitHub token. None uses
GraphQL, check runs or commit statuses (`src/landing_ledger/github.py:657-660` records that
`/check-runs` answers 403 to the estate's App).

### Landing ledger

- **Launcher:** `scripts/run-landing-ledger.sh`.
- **Variable:** `LANDING_LEDGER_GITHUB_TOKEN`, from `gh auth token` (`:84-86`); read at
  `src/landing_ledger/cli.py:308` and `:361`.
- **Repositories:** the record's `onboarding` and `member` repositories, from
  `scripts.sds_scope ledger` (`scripts/run-landing-ledger.sh:102-103`;
  `src/sds_members/populations.py:23`). The mint must name onboarding repositories too, so the
  install set must include them.
- **Calls** (`src/landing_ledger/github.py`): `/repos/{r}` (604), `/branches/{base}` (575),
  `/commits?sha&since` (587), `/commits/{sha}` (457, 532), `/commits/{sha}/pulls` (272),
  `/pulls/{n}` (481, 738), `/pulls?state=open&base` (726), `/pulls/{n}/commits` (704),
  `/actions/runs?head_sha` (395, 636, 671), `/actions/runs/{id}/jobs` (320, 643),
  `/actions/runs/{id}/attempts/{n}` (362), and the gate file through `/contents/...` (425, 621).
- **Permissions:** Metadata, Contents, Pull requests, Actions, all read.

### Pin watcher

- **Launcher:** `scripts/run-pin-watcher.sh`.
- **Variable:** `PIN_WATCHER_GITHUB_TOKEN`, from `gh auth token` (`:103-105`); read at
  `src/pin_watcher/cli.py:109`.
- **Repositories:** every non-archived repository the token's owner owns, from
  `GET /user/repos?affiliation=owner` (`src/pin_watcher/github.py:157`, through `sweep`,
  `src/pin_watcher/measure.py:101`), plus factory-runner (item 1).
- **Calls:** `/user/repos` (157), `/contents/.github/workflows/factory-runner-pilot.yml` per
  repository (176), and factory-runner's contents, compare and commits (135, 192, 197).
- **Permissions:** Contents and Metadata read.
- **Flag (to Devon).** GitHub Docs, "List repositories for the authenticated user": "This
  endpoint works with the following fine-grained token types: GitHub App user access tokens,
  Fine-grained personal access tokens." Installation access tokens aren't listed. On an App token
  `repositories()` fails, so `sweep` never runs and the lane exits incomplete every pass.

### Deploy watcher

- **Launcher:** `scripts/run-deploy-watcher.sh`.
- **Variable:** `DEPLOY_WATCHER_GITHUB_TOKEN`, from `gh auth token` (`:136-138`); named at
  `src/deploy_watcher/cli.py:73`.
- **Repositories:** change-manager `deploy` records' `target_repository`, filtered to
  `ROLLOUT_WORKFLOWS` (`src/deploy_watcher/workflows.py:79-82`): `AlobarQuest/change-manager` and
  `AlobarQuest/brain`.
- **Calls** (`src/deploy_watcher/github.py`): `/pulls/{n}` (112), `/commits/{sha}` (189),
  `/contents/{path}` (206), `/actions/workflows/{id}` (263), `/actions/workflows/{id}/runs` (337,
  366, 425), `/actions/runs/{id}/attempts/{n}/jobs` (292), `/compare/{a}...{b}` (467).
- **Permissions:** Metadata, Contents, Pull requests, Actions, all read.

### Revision watcher

- **Launcher:** `scripts/run-revision-watcher.sh`.
- **Variable:** `REVISION_WATCHER_GITHUB_TOKEN`, from `gh auth token` (`:160-162`); read at
  `src/revision_watcher/cli.py:160`.
- **Repositories:** the member record's apps, one repository and branch each
  (`src/revision_watcher/subjects.py:66-80`).
- **Calls** (`src/revision_watcher/github.py`): `/commits/{sha}` (83), `/branches/{branch}`
  (109), `/compare/{a}...{b}` (125).
- **Permissions:** Contents and Metadata read.

### Fork-sync watch

- **Launcher:** `scripts/run-fork-sync-watch.sh`.
- **Variable:** none. It runs `fork-sync watch` (`:61`) on the operator's `gh` login (header
  `:30-32`); increment 3 exports `GH_TOKEN`, as the ADR says.
- **Repositories:** factory-runner, the five forks, and their five upstreams (items 1 and 2).
- **Calls** (`src/fork_sync/watch.py` unless named):
  - The registry: `/contents/.cred-consumers.toml` (156).
  - Per fork: `/contents/.github/fork-sync.toml` (291), `/repos/{fork}` (293),
    `/actions/workflows/upstream-sync.yml/runs` (178-179), `/pulls?state=open&head` (302),
    `/commits/{sha}` (226), `/actions/runs?head_sha` (`src/fork_sync/acknowledge.py:381`, through
    `latest_ci_run`), `/actions/runs/{id}/jobs` (240), `/issues?state=open&creator` (196),
    `/issues/{n}/comments` (`acknowledge.py:119`), and `/contents/{version_manifest}` (275).
  - Per upstream: `/releases` (279).
- **Permissions:** Metadata, Contents, Pull requests, Issues, Actions, all read. This is the only
  lane that needs Issues read.

### Membership check

- **Launcher:** `scripts/run-membership-check.sh`.
- **Variable:** `SDS_MEMBERS_GITHUB_TOKEN`, from `gh auth token` (`:80-83`); named at
  `src/membership_check/cli.py:39`.
- **Repositories:** every non-archived owned repository, through the pin watcher's `repositories()`
  and `/user/repos` (`src/membership_check/cli.py:57`), plus members not in that list (`:61-62`).
  It also reads each member's `.cred-consumers.toml` and workflows, and factory-runner through
  `gh` (`:167`).
- **Calls:** `/user/repos`; `/contents/factory-target.toml` per repository
  (`src/membership_check/declaration.py:302`); `/contents/.cred-consumers.toml` per member
  (`declaration.py:276`, through `cli.py:132-143`); `/repos/{r}` (`auto_merge.py:126`;
  `declaration.py:350-376`); `/contents/.github/workflows` and each workflow file
  (`auto_merge.py:141`, `:158`).
- **Permissions:** Contents and Metadata read.
- **Flag (to Devon).** The `/user/repos` limit is the same as the pin watcher's. On an App token
  `_declarations` records the listing as a gap (`cli.py:58-60`), and the factory-target sweep
  shrinks to the members.
- **Flag (to HQ, increment scope).** `forks(subprocess_runner)` (`cli.py:167`) is a `gh api`
  read on the ambient login, and the launcher exports no `GH_TOKEN`. In the container that read
  fails every pass. Increment 3 must export `GH_TOKEN` in `run-membership-check.sh` as well as
  `run-fork-sync-watch.sh`; the ADR names only the second.

### Unit verifier

- **Launcher:** `scripts/run-unit-verifier.sh`.
- **Variable:** `ROTATION_VERIFIER_GITHUB_TOKEN`, from `gh auth token` (`:87-89`); read at
  `src/unit_verifier/cli.py:339`, used only when non-empty (`:358-361`).
- **Repositories:** each submitted unit's `target_repository` from the orchestrator API, for bump
  units and app-change units with an `automated_check` criterion
  (`src/unit_verifier/cli.py:223`, `:242`).
- **Calls:** `GET /repos/{r}/branches/main/protection/required_status_checks`
  (`src/unit_verifier/required_checks.py:55`).
- **Permissions:** Administration read. GitHub Docs, "Get status checks protection": "The
  fine-grained token must have the following permission set: "Administration" repository
  permissions (read)", with no public-resources sentence. So this read needs the install on each
  target, and it's the only Administration read in the seven lanes.

### Permission flags

No lane needs a permission outside D-18's list (Metadata, Contents, Pull requests, Issues,
Actions, Administration, all read), and no lane writes. The `/user/repos` limit is about which
repositories the App is installed on, not about a permission.

## 6. Registry class

`github-app-private-key` is in `CLASS_POLICY` at
`~/Projects/infraops-mcp-server/src/security-drift/cred-rotation.ts:54`, with
`maxAgeDays: 365` (`:55`), and one landmine (`:57`): a new key pair doesn't revoke the old one, so
the old pair is deleted on the App's Credentials page. `CLASS_POLICY` starts at `:31`. That
checkout was on `main` at `c3359bf1486cfe63018cfb404b03246aaabd4f32` (the file's last change);
it wasn't fetched, built or run. The ADR's 365 days matches. factory-runner's registry already
uses the class for `fork-sync-app-private-key`.

**Confidence: high.**

## 7. Mint helper fit (recommendation only)

**What exists.**

- `RotationApp.token` (`src/rotation_worker/github_actions.py:101-129`) mints for one repository:
  it resolves the installation with `GET /repos/{r}/installation`, posts
  `{"repositories": [name], "permissions": ...}`, and refuses unless the response echoes exactly
  that repository and those permissions. Its JWT is built from a key callable (`:131-140`).
- `src/orchestrator/services/github_app.py` mints for a configured installation id with no
  narrowing (`:113-117`), and caches one token per process. It's the orchestrator's dispatch
  credential; a lane must not import the orchestrator.

**Recommendation.** Generalize `RotationApp.token` into one module that takes a repository *list*
and a permission dict, under a neutral package the lanes already share (for example
`src/estate_clients/github_app_mint.py`, beside `confined.py`, whose `ConfinedClient` and
`TRANSPORT_ERRORS` it uses). Then have `RotationApp.token` call it with a one-repository list, so
one mint and one echo check exist. Expose it as a console script, for example
`sds-github-mint = "estate_clients.github_app_mint:main"` in `[project.scripts]`. The lane image is
built from the orchestrator image, so the script is at `/app/.venv/bin/sds-github-mint`, which the
launchers reach as `$REPO_ROOT/.venv/bin/...` on the Mac and in the container (`lanes/Dockerfile`
comment at `:60-63`). No `lanes/Dockerfile` change is needed for a console script; a
`scripts/*.py` helper instead would have to join the `COPY` at `lanes/Dockerfile:64`.

**How a launcher passes it the key.**

```sh
LANDING_LEDGER_GITHUB_TOKEN="$(sds_bws_value "$READ_APP_KEY_UUID" \
  | "$REPO_ROOT/.venv/bin/sds-github-mint" --app-id "$READ_APP_ID" \
      --repository AlobarQuest/brain --repository AlobarQuest/orchestrator \
      --permission contents=read --permission metadata=read)"
```

- The key UUID and the App id are constants in the launcher, as other BWS UUIDs are today; neither
  is a secret.
- The key reaches the helper on stdin from `sds_bws_value` (`scripts/sds-bws.sh:47`), never in
  argv or an exported variable. The token leaves on stdout, into the lane's existing
  `<LANE>_GITHUB_TOKEN` variable, so the lane code doesn't change.
- Repositories come from the launcher's own population (`scripts.sds_scope` for the ledger), so
  one installation lookup (`GET /repos/{first}/installation`) serves every name.
- The fork-sync watch passes factory-runner and the forks, never an upstream (item 2).

**Constraints the increment must meet.**

- The helper refuses unless the response's permissions equal the request and its casefolded
  repository set equals the request, as `github_actions.py:119-125` does.
- Every failure exits non-zero with a message that names the cause and never carries the key or
  the token (Code Brain rules 3, 4 and 5). Catch `TRANSPORT_ERRORS` at construction and request.
- Decide the stored encoding: factory-runner's registry records that BWS holds base64 of the PEM
  for the fork-sync App's key; the helper accepts one stated form.
- Register the file in `layers.toml`, add it to `OUTBOUND_ALLOWLIST` with a reason
  (`test_wsp21_invariant_scan.py`), and keep it reachable from the console-script entry point
  (`test_unreachable_guards.py`). `pyjwt[crypto]` is already a dependency (`pyproject.toml:17`).
- The mint request is limited to 500 repositories, which the estate doesn't approach.

## What widens a credential beyond the ADR and D-18 (for Devon)

1. **The pin watcher's and membership check's population.** They enumerate with `/user/repos`,
   which an installation token can't call. To keep today's population (65 non-archived owned
   repositories, 33 of them private), the read-only App would be installed on all repositories
   in the account, which is wider than the ADR's member-only install, and would read private
   non-member repositories. The alternative is a code change to `/installation/repositories` (no
   permission needed) or to the member record, and both sweeps then cover only installed
   repositories. This is a design fork; this report doesn't pick.

No other item widens a credential. Every lane's permission need is inside D-18.

## Follow-ups

- HQ: take the install list from the live record (14), including `onboarding` repositories.
- HQ: decide the `/user/repos` fork with Devon before the pin watcher or membership check moves.
  The landing ledger, the first lane to move, isn't affected.
- Increment 3: add the `GH_TOKEN` export to `run-membership-check.sh`; its header and the
  `forks(subprocess_runner)` call aren't in the ADR's list of changed text.
- Increment 3: run item 2's three-call probe once the App exists, before the fork-sync watch
  moves, and record the result.
- ADR-0059 wording: "a token that refuses everything outside the request" holds only for private
  resources and non-public endpoints (item 3). An amendment can say so.

## Tests and checks run

- No code changed, so no test or `make check` ran.
- Reads: `gh api` GETs for repository metadata (factory-runner, members, forks, upstreams), each
  fork's `.github/fork-sync.toml`, factory-runner's `.cred-consumers.toml` and its commits, and a
  visibility count over `/user/repos`. GitHub Docs pages through `docs.github.com`.
- A subagent mapped the six non-fork lanes' calls; I re-read the load-bearing lines myself
  (`/user/repos` at `pin_watcher/github.py:157` and `membership_check/cli.py:57`, `forks` at
  `cli.py:167`, `ROLLOUT_WORKFLOWS`, the GET-only guards, the unit verifier's protection path).

## Files changed

- `docs/superpowers/reports/lanes-inc-3-verify-report.md` (this report).

## Self-review

- Every list cites a file and line or a quoted doc. Lines marked as ranges were read in full.
- The weakest claim is that an installation token reads public resources outside its
  installation. It rests on the per-endpoint clause, not a direct statement, and item 2 names the
  live call that settles it.
- The 14th member is unknown here, so the install table is one row short by design.

## Concerns

- The seed is stale, as HQ knows. Any install list built from this report alone misses a member.
- Concerns the Standards section doesn't cover: none.

## Standards followed

- **Code Brain rule 5 (never log secrets).** The report holds no token, key, private key or ping
  URL. The identifiers it names are repository names, commit hashes, and App ids already in tracked
  files (factory-runner's registry) and no BWS UUID beyond one already committed in that registry.
  No value was read from BWS.
- **Code Brain rules 3 and 4 (structured logs; never swallow an exception).** No code changed.
  Item 7's recommendation requires the helper to exit non-zero with a named cause and to catch the
  transport errors explicitly, never silently.
- **Infra Brain 591, 592, 593 (BWS token handling).** No BWS token is in the report or the branch;
  no BWS call was made.
- **Infra Brain 1032 (cred.exposure-rotate).** No exposed credential value was found in any file or
  history read: the registry, launchers, lane code and `cred-rotation.ts`.
- **Infra Brain 212.** Not applicable: no domain configuration.
