# Credentials

Where the SDS credentials live, how to fetch them without exposing them, and the traps in each identity.

The entries below moved verbatim from the CLAUDE.md invariants section on 2026-10-01 (Tier 3 item 17). Each keeps its original number. Their dated corrections are the record of what was measured when; read the last correction in an entry as its current state.

### #12

- Coolify's env PATCH endpoint intermittently 500s on this app; the reliable
  fallback is delete-by-env-uuid + recreate. All `/envs` API responses include
  `real_value` for every variable (DB URLs with passwords) — parse them
  in-process and print only whitelisted fields, never through ad-hoc shell
  pipelines.
- **Measured 2026-10-09 (dev beta.470; permission code identical at beta.473):** `value` and
  `real_value` are present only for a token with `read:sensitive` (and, from the source, `root`); a
  token without it
  gets the rows with both fields absent, not masked. Each variable is stored as **two rows**,
  `is_preview` false and true. A PATCH without `is_preview` changes only the first; changing a value
  everywhere takes a second PATCH with `is_preview: true`, or the old value stays in the preview row.
  That run's PATCHes returned 201, not 500. Detail: ADR-0055 amendment 1.

### #20

- **Write `ORCHESTRATOR_M2M_CREDENTIALS` before `ORCHESTRATOR_M2M_ROLES`, and
  verify each from inside the container before the next restart.** `main.py`
  raises when `set(roles) ⊄ set(credentials)` — it fails **closed**, so the
  container will not boot. A half-applied credentials write leaves roles absent,
  which is a healthy configuration; a half-applied roles write is an outage (this
  is the WS-6.3 ~3-minute 503). There is no ordering of the two that can strand
  production, at the cost of one extra restart. Never "save a restart".

### #21

- The registry bundle is built from the **git tree** at `SECURITY_STANDARDS_REVISION`,
  not from a working copy, and is baked into the image. A credential's `agent_id`
  must resolve to an actor in that bundle, and `ActorContext(identity.actor_id, role)`
  means **every event is attributed to that `agent_id` forever**. Adding an actor
  therefore requires a merged security-standards commit plus an image rebuild —
  never borrow an unrelated identity for a durable credential. `token_hash` is
  `sha256(bearer_token)`; Coolify stores only the hash, so the hash is safe to
  handle and the token must never leave BWS. During a credential rotation an entry may also
  carry `"previous": {"token_hash": "<sha256>", "until": "<UTC instant>"}` (ADR-0055 decision
  7): the previous hash authenticates only before `until`, and boot refuses an `until` more than
  eight days ahead. Rolling back past the image that parses it needs `previous` removed first
  (`deploy.md`, "Rolling back past the bearer overlap parser").

### #41

- **Driving a dispatch needs the M2M bearer tokens — fetch them, don't hunt.** The two
  credentials and how to get them are already recorded; do not re-derive them each session.
  `.bws-secrets.toml` (repo root) names the BWS UUIDs; source `BWS_ACCESS_TOKEN` via the
  approved Keychain helper **`scripts/sds-token.sh` in this repo** (service `Claude`, account
  `BWS_ACCESS_TOKEN_SDS`), then `bws secret get <uuid>` — never echo any value.
  **Do NOT use `~/Projects/vps-backup/bws-token.sh` — it no longer works for these secrets.**
  Until 2026-07-30 every SDS fetch bootstrapped with that helper, i.e. the shared broad machine
  account (one account behind BOTH the `BWS_ACCESS_TOKEN_VPS_BACKUP` and
  `BWS_ACCESS_TOKEN_INFRA_DRIFT` Keychain names — verified identical, sha256 `da55db37ea81`).
  The three SDS runtime secrets now live in the `SDS Operator` BWS project, readable only by
  the read-only `sds-operator` machine account. The old token is DENIED on all three; that is
  the migration working, not a fault. No secret VALUE changed — this narrowed who can read.
  Consumers resolve by UUID, and the UUIDs survived the project move unchanged.
  **SYSTEM** (`orchestrator-system`, decomposition-submit / `commands/ready` / dispatch):
  `221a48d5-3f29-4898-b300-b4820140c880`. **VERIFIER** (`orchestrator-verifier`,
  `verifier-evidence/named-check` + `/verify`): `660d5846-abcb-4751-be86-b483012899eb`.
  **WORKER** (`orchestrator-operator`, agent_id `claude-code-interactive` — claim / `start` /
  evidence / `submit`): `bd71bed1-4aac-4af8-9094-b4970180bc59`, added WS-P2.13 2026-07-30.
  A unit **cannot reach COMPLETED without a WORKER actor**: `CLAIMED→EXECUTING` and
  `EXECUTING→SUBMITTED` are worker-only edges and there is no human path around them. It carries
  **no `ORCHESTRATOR_M2M_ROLES` entry** — `authenticate_m2m` returns WORKER for every M2M
  credential and the roles map only *promotes*, so worker is what an unpromoted credential falls
  to. The alternative was `factory-runner-github`, whose `agent_id` attribution is permanent and
  untrue for non-software operational work. Every
  M2M call sends both `Authorization: Bearer <token>` and `X-Credential-Key-Id: <key-id>`.
  Read endpoints (`status-ledger`, `runner-brief`, …) also require the SYSTEM bearer — a
  bare GET is `401`. (Verified 2026-07-22, AC-003; token migration 2026-07-30.)
  The third SDS secret in that project is the Todoist token
  (`ff396349-aec1-4250-b2f0-b493015188da`, BWS key `TODIST-API-DEVON-PERSONAL`), used by the
  tracker launchers. **The SDS consumer set is five, not the three the migration plan named:**
  this repo's three launchers, intent-packages' `credentials.py` (env-driven, no code change),
  **and `infraops-mcp-server/scripts/drift-audit.sh`** — which runs on a 03:00 LaunchAgent and
  fetches the SYSTEM bearer for its `mint-follow-ups` step with a *different* BWS identity from
  every other secret it reads, so it overrides `BWS_ACCESS_TOKEN` for that one call.
  `factory-validation-kit-restart-recovery/credentials.py` hardcodes the same SYSTEM UUID but is
  operator-invoked, on no schedule. A grep of `src/` in one repo would have found neither: when
  moving a secret, grep the whole portfolio for the UUID, not the repos you expect to own it.

### #47

- **The brains have a REST read API built for off-machine agents, it is approved-only by
  default, and there is NO read-only credential.** Code Brain (`https://code-brain.devonwatkins.com`)
  serves `GET /api/roads`, `/api/road/{slug}`, `/api/rules`, `/api/search`; Infra Brain
  (`https://infra-brain.devonwatkins.com`) serves `GET /api/rules`. Auth is
  `x-brain-key: <key>` (or `?key=`), and the middleware accepts **either** the approver key or
  the contributor key, gating every non-allowlisted path identically — so the contributor key
  (`CODE_BRAIN_CONTRIBUTOR_KEY` `750f737f-4cb6-4876-9a98-b48200ea1c0b`,
  `INFRA_BRAIN_CONTRIBUTOR_KEY` `da8134b0-565f-45c8-8965-b48200ea1c40`, BWS project `brains`,
  bootstrap identity Keychain `Claude`/`BWS_ACCESS_TOKEN_VPS_BACKUP`, **not** the SDS-narrow
  account) is the least privilege available and can also POST proposals. Narrowing that is open.
  Containment comes free from the repositories: `list_all` defaults `include_proposed=False`, so
  REST serves approved-only records. The REST route exposes `category/severity/road_slug/
  include_retired` but **not** `min_authority` (the repository supports it, the route does not
  pass it), so an authority floor is applied client-side.
  **Severity and authority are orthogonal and disagree** — Infra Brain has **12** BLOCK-severity
  rules of which only **4** are `authority: required`, and Code Brain has **zero** at `required`
  (all 11 of its rules are `informational`). A filter keyed on the wrong one carries three times
  the material. Content is thin: the only substantive road is `error-logging` (9 rules, 2
  exemplars, a real `decided_approach`); `dependency-update` is `paved` with `decided_approach:
  null` and 0 rules/exemplars/lessons. (Verified live 2026-07-30, WS-P2.12.)

### #59

- **An M2M credential's `agent_id` is resolved against a registry bundle BAKED INTO THE IMAGE, and
  an unresolvable one is a boot failure, not a 401.** `_m2m_credentials` (`main.py:140`) calls
  `registry.resolve(agent_id)` at startup against `/app/registry-bundle.json`, built at image-build
  time from the security-standards tree at `security-standards.pin.toml`'s `revision`. So checking
  that an actor exists in git — even at exactly the pinned revision — does **not** establish that
  the running image carries it: the image may predate the pin. Ask production before writing the
  env var:
  `docker exec <container> python3 -c "import json;b=json.load(open('/app/registry-bundle.json'));print(b['source_revision'],[a['agent_id'] for a in b['actors']])"`.
  Getting this wrong fails **closed** on the next restart, which is the same outage shape as the
  WS-6.3 roles-before-credentials write. Verified 2026-07-27 (WS-P3.0) on image
  `8da4af3-wsp27inc2-amd64`: bundle revision `65655ddf…`, 13 actors, `drift-reconciler` present.

### #71

- **Coolify-managed Postgres runs `local all all trust`, which is why a password rotation is
  survivable and why the backup lane is not a credential consumer.** `pg_hba.conf` in
  `postgres:16-alpine` under Coolify trusts the container's local socket, so (a) `docker exec …
  psql -U postgres` always works regardless of the password — a generated-then-lost credential is
  recoverable by a second `ALTER USER`, not an outage — and (b) vps-backup's
  `pg_dump_container` (`docker exec … pg_dump -U postgres`) needs **no password at all**, so it
  never appears in a credential-consumer inventory. Only TCP connections from other containers
  hit `host all all all scram-sha-256`. To prove a password is dead, probe over TCP against the
  container's network name (not `127.0.0.1`, which is also `trust`), and always pair the probe
  with a wrong-password control — otherwise a broken probe reads as a successful revocation.
  (Verified 2026-07-29, GAP-7.)

### #101

- **The `Alobar SDS Dispatch` App has NO `checks` permission — the Checks API is 403 for the
  orchestrator, and named-check evidence is read from workflow JOBS instead.** Measured 2026-08-02
  from production's own credential. **UPDATED 2026-08-09: the permission set is now
  `{'actions': 'write', 'contents': 'write', 'metadata': 'read', 'pull_requests': 'write'}`**
  (app `4259746`, installation `145535298`, `repository_selection: all`), granted for ADR-0020.
  `checks` is still absent, so everything below stands unchanged — only the merge capability was
  added, and `administration` is absent too, so **branch protection is 403 to this App: whether a
  merge would be blocked can only be learned by attempting it, never by reading the protection
  settings.**
  **`pull_requests: write` alone CANNOT merge — a merge writes a commit to the base branch, so it
  needs `contents: write` too.** Measured 2026-08-09 (WS-P3.7 Inc 2): with `pull_requests: write`
  only, `PUT /repos/{repo}/pulls/{n}/merge` returned **403 `Resource not accessible by
  integration` on a pull request GitHub itself reported `MERGEABLE/CLEAN`** — which is what
  isolates the cause to permission rather than to protection, and the trap is that the identical
  403 on a *red* pull request reads like the safety property working. With `contents: write`
  added, the same call on the same two pull requests answers **405 `Required status check
  "Quality" is failing.`** on the red one and **200 `merged=True`** on the green one. So branch
  protection does bind a GitHub App, and the App now carries a write that reaches every
  repository in the account (backlogged `880ba73ecc24`).
  At the 2026-08-02 measurement the installation carried exactly
  `{'actions': 'write', 'metadata': 'read'}`, so
  `GET /repos/{repo}/commits/{sha}/check-runs` answers **403 Resource not accessible by
  integration** while `GET /repos/{repo}/actions/runs?head_sha=` and `/actions/runs/{id}/jobs`
  answer 200. WS-P2.20's observer (`services/verifier/github_checks.py`) therefore reads Actions
  jobs, which is every check this estate produces — a check run published by any OTHER application
  is invisible to it and refuses rather than guesses. **Two consequences.** (1) `check_name` must be
  the **job** name, not the workflow name: in this repo both are `Quality`, but in `change-manager`
  the workflow is `Quality` and the job is `Lint, type-check, and test`, and naming the workflow
  yields `named_check_not_found`. (2) An App's *token-mint response* reports its own `permissions`,
  so asking what a credential may do costs one call and never needs the private key locally:
  `POST /app/installations/{id}/access_tokens` → `permissions`. Do not infer an App's reach from
  what it is already used for — triggering a run (`actions`) and reading a check (`checks`) are
  different permissions. (3) **ADDED 2026-08-08: the APP and the INSTALLATION carry separate
  permission sets, and only the installation's is the credential.** Granting a permission on the
  App raises a *request*; the installation owner must accept it before any minted token carries
  it. Observed in the gap: `GET /app` reported `pull_requests: write` while
  `GET /app/installations/{id}` still reported `{'actions': 'write', 'metadata': 'read'}`, so a
  check written against `/app` would have said "done" and the token could not have merged
  anything. Read the installation, or the mint response, never the App. And confirm the
  permission *does* something — `GET /repos/{repo}/pulls` answered 403 before and 200 after —
  because a reported permission and a functioning one are the same class of difference.

### #127

- **A push that touches `.github/workflows/**` requires the `workflow` scope (classic PAT) or
  Workflows: Read-and-write (fine-grained) on the pushing credential — and `FACTORY_PR_TOKEN`
  lacked it, which killed two pilot units AFTER their coding and verification succeeded.** The
  rejection is remote (`! [remote rejected] … refusing to allow a Personal Access Token to create
  or update workflow … without workflow scope`), arrives only at finalize's `git push`, and every
  caller-pin remediation — the maintenance-remediation profile's founding queue — edits exactly
  such a file. Three traps inside the fix: GitHub Actions secrets are WRITE-ONLY, so nothing can
  confirm which token a secret holds (Devon's first in-place scope edit landed on a classic token
  while the secret held a fine-grained one); fine-grained PATs do NOT report `x-oauth-scopes` on
  API responses (that header is classic-only), so the settings page is the only scope check; and
  the token had NO BWS record at all (P1 `237b8599e7a1` — it now does: `a3240c2e…`, SDS Operator
  project). **The pattern that broke the loop: verify the credential with a DISCRIMINATING PROBE
  before spending a work unit** — a throwaway branch workflow that pushes a workflow-file-touching
  commit using the secret costs one minute and no units (probe run `30842959171`).

### #152

- **Onboarding a factory target has FOUR parts, and the fourth is invisible until a run dies at
  checkout: the fine-grained PAT's REPOSITORY ACCESS LIST.** The three obvious parts are the caller
  workflow (`.github/workflows/factory-runner-pilot.yml` at `RECOMMENDED_CALLER_PIN`), the four
  Actions secrets, and the orchestrator's `ORCHESTRATOR_DISPATCH_ALLOWED_TARGET_REPOSITORIES` entry.
  All three can be correct while every run fails, because `FACTORY_PR_TOKEN` is a **fine-grained**
  PAT and a fine-grained PAT is scoped to an explicit list of repositories chosen when it was
  issued. Setting the secret in a new repo copies a token that has no access to that repo.
  Measured 2026-08-07 onboarding `project-standards`: caller, secrets and allowlist all in place,
  and the probe run failed in 35 seconds at `actions/checkout` with
  `fatal: unable to access '…/project-standards/': The requested URL returned error: 403` — never
  reaching the claim call. Confirmed by control: the token answers 200 on all seven other repos and
  DENIED on that one. **Extending the list is a settings-page operation on the account that owns the
  PAT; no API does it, and no amount of re-setting the secret helps.**
  **Two consequences.** (1) Fire a throwaway `workflow_dispatch` at a new caller with a well-formed
  but nonexistent `work_unit_id` before believing it works — it costs 35 seconds, mutates nothing
  (the runner claims before it codes, and here it does not even get that far), and it is the only
  thing that distinguishes *configured* from *working*. This is the same class as the 2026-08-03
  failure where the PAT lacked `workflow` scope and it surfaced only at `git push`, after coding and
  verification had already succeeded, costing two work units. (2) **Two sets are now tracked and
  they disagree in both directions** — repos holding a `FACTORY_PR_TOKEN` Actions secret
  (`orchestrator`, `intent-packages`, `infraops-mcp-server`, `security-standards`, `change-manager`,
  `brain`, `project-standards`) versus repos the PAT can reach (the same list minus
  `project-standards`, plus `factory-runner`). As measured 2026-08-07; factory-runner has since been
  given its own copy (2026-08-29), so the holding set is now eight — see the #153 bullet.
  Neither set is derivable from the other.

### #153

- **`FACTORY_PR_TOKEN` has a BWS record and a rotation trail** — `a3240c2e-92d7-4b32-a726-b49b0135565a`,
  `SDS Operator` project, documented in **factory-runner's** `.bws-secrets.toml` (not this repo's).
  **EIGHT repos hold copies as write-only Actions secrets** as of the 2026-10-06 census (`gh secret
  list` over every non-archived AlobarQuest repository) — `orchestrator`, `intent-packages`,
  `infraops-mcp-server`, `security-standards`, `change-manager`, `brain`, `project-standards`, and
  `factory-runner` (set 2026-08-29). (This bullet said "four" until 2026-08-07 and "seven" until
  2026-10-06; the count has drifted three times. factory-runner's `.cred-consumers.toml` is now the
  per-copy list the rotation lane reads.) A rotation means re-setting all eight; a copy left behind is
  dead on the next push with no signal until a run fails at auth. Verify any rotation with the
  discriminating probe, never with a green `gh secret set`: a throwaway branch workflow triggered
  on `push:` that checks out with the secret and pushes a commit **touching
  `.github/workflows/**`**, which is the exact operation a token without Workflows:
  Read-and-write is rejected for. (Rewired and probed for all four, 2026-08-04, WS-P2.34.)
  **THOSE TWO COUNTS WERE NEVER RECONCILED, AND THAT IS EXACTLY HOW A DEAD COPY SURVIVED TWO
  WEEKS.** The bullet said seven repos hold it and, one line later, that the rewire covered "all
  four". The three it did not name were `project-standards` (re-set at onboarding 2026-08-07) and
  **`infraops-mcp-server` and `orchestrator`, both left on the 2026-07-14 token, which had been
  revoked**. It surfaced on 2026-08-19 as the first dispatch to `infraops-mcp-server` since
  2026-07-23 — i.e. the first since the rotation — dying at `actions/checkout` with
  `fatal: could not read Username for 'https://github.com': terminal prompts disabled`. **Read that
  message as an invalid credential, never as a missing permission**: a token that authenticates but
  lacks repository access answers `403`, while a revoked or expired one is refused outright and git
  falls back to prompting. Nothing was lost — checkout precedes the claim, so the unit never left
  `ready` — but a run and a diagnosis were. Both were re-synced from BWS on 2026-08-19 and **all
  seven now carry the current token**. When a bullet states a population and a smaller action over
  it, reconcile the two numbers or the difference is invisible until it fails.
  **Two probe traps, both hit that day.** (1) **`gh secret set --body -` writes the LITERAL string
  `-`; stdin is read only when `--body` is omitted entirely** (`… | gh secret set NAME --repo R`).
  The clobber was visible only because GitHub then masked every `-` in the job log, rendering
  `astral-sh/setup-uv` as `astral***sh/setup***uv`. (2) **Since the repositories went public on
  2026-08-17, `GET /repos/{owner}/{repo}` answers 200 unauthenticated, so a status-code sweep proves
  nothing about a token's reach.** Read `.permissions.push` instead and run the unauthenticated and
  garbage-token controls beside it — both answer "no permissions block", which is what makes
  `push=true` mean something. So measured, the current token reaches all eight factory-adjacent
  repositories with write, **`project-standards` included** — correcting this file's claim that it
  was the one repository the PAT could not reach.

### #175

- **`scripts/sds-token.sh` RESPECTS an already-set `BWS_ACCESS_TOKEN`, so a launcher that needs TWO
  BWS identities must not source it alongside a `${BWS_ACCESS_TOKEN:-…}` default.** One ambient
  value then becomes BOTH identities and **no value of it works**: exported broad, the narrow
  project's fetch is denied; exported narrow, the broad project's is. Under launchd nothing is
  exported and it works, so the failure appears only in the shell an operator debugs the job from —
  and it names BWS rather than the cause. Read each Keychain item **directly**
  (`BWS_ACCESS_TOKEN_VPS_BACKUP` broad, `BWS_ACCESS_TOKEN_SDS` narrow) and give each override a
  distinct variable name, as `run-estate-landing.sh` does with `BWS_ACCESS_TOKEN_BROAD`. Found by
  two reviewers independently; proven with a pre-fix control that fails in both directions.
  Related and pre-existing across these launchers: the exit-code fold `for rc in 1 3 2` lets any
  code outside `{0,1,2,3}` — `127` for a missing binary — fall through to `exit 0`.

### #202

- **`factory decompose` NEEDS TWO BWS IDENTITIES IN ONE INVOCATION, and setting one ambient
  `BWS_ACCESS_TOKEN` for both fails as a bare `http_error: HTTP 400` that names nothing.** Measured
  2026-08-19. The orchestrator bearer lives in the `SDS Operator` project (narrow `sds-operator`
  account, Keychain `BWS_ACCESS_TOKEN_SDS`); the Code/Infra Brain keys the enrichment step needs
  live in the `brains` project, readable only by the broad account (Keychain
  `BWS_ACCESS_TOKEN_VPS_BACKUP`). Export the narrow one for the whole run — the obvious thing to do,
  since the API token needs it — and `bws secret get 750f737f…` answers **`404 Not Found`**, which
  surfaces through the factory client as an HTTP 400 with no error envelope.
  **The diagnosis cost six probes because every plausible cause was eliminated first:** the
  orchestrator's own log showed only a 201 and two 200s and never the failing request; the
  decomposition-proposals GET answers 200 by hand; `orchestrator conformance-claim` returns green;
  and the brain API answers **401** for a missing, empty *or* garbage key — so an empty key was not
  the mechanism either. The call sequence is `api.get_intake` → `client.conformance_claim` →
  `enrichment_for_profile(..., client=brain_client)` → (only with `--submit`)
  `api.propose_decomposition`, so a DRY run that fails has failed in the enrichment step.
  **The fix is the shape `scripts/run-estate-landing.sh` already uses**: read each Keychain item
  directly and give each override a distinct variable name, never one ambient `BWS_ACCESS_TOKEN`
  serving both. This file already carried the two-identity rule for launchers; it applies to
  interactive `factory decompose` too, and reading it in one context did not prevent violating it in
  another an hour later.

### #274

- **THE DISPATCH APP'S REACH IS DELIBERATELY WIDER THAN ITS WORK, AND THAT IS A RULING, NOT AN
  OVERSIGHT — the bound is the orchestrator's own allowlists, checked in code before every call.**
  Measured 2026-09-02 from a mint response inside the running container (never `/app`, which reports
  the App's *requested* permissions rather than the installation's granted ones). App `4259746`,
  installation `145535298`: `actions:write, contents:write, metadata:read, pull_requests:write,
  workflows:write`, `repository_selection: all` — **75 repositories, 18 archived, 57 writable**,
  against **8** the code can address (the five in
  `ORCHESTRATOR_DISPATCH_ALLOWED_TARGET_REPOSITORIES` plus landing policy v6's two deploying and six
  inert repositories; every App call site — `dispatch.py`, `pr_merge.py`, `estate_pr_merge.py`,
  `github_checks.py` — is bounded by one of those sets). **20 of the 57 hold Actions secrets and 12
  are outside the eight**, including `community-atlas`'s `VPS_SSH_KEY` and
  `AdjustRight-Photo-Pro`'s `CF_API_TOKEN`. `workflows: write` (granted 2026-09-01 to unblock the
  inert lane's branch update) made that exposure **unconditional**: the App can author an `on: push`
  workflow that fires on its own push and names any secret, where `contents: write` alone only
  reached secrets a repository's existing CI already exposed.
  **RULED 2026-09-02 (Devon), with the full measurement in hand: leave it.** *"The SDS part of the
  operations factory limits what it works on already."* Backlog `880ba73ecc24` is **answered, not
  deferred** — it stays in `~/.portfolio/inbox.jsonl` only because that file is an append log with
  no close or remove verb. **A future audit will re-derive these numbers and reach the same alarm;
  that is a repeat, not a new fact, and is not a reason to re-raise it.**
  **Two things WOULD re-open it.** (1) **An App call site not bounded by an allowlist** — the ruling
  rests entirely on that property, so a new lane that mints the token and addresses a repository from
  anything else removes the ground it stands on. (2) **ADR-0015's declaration mechanism arriving**
  (programme plan §7 decision 9): the strongest argument against narrowing is that an installation
  list would be a *fourth* hand-maintained answer to "which repositories are factory targets", and if
  that answer ever becomes derivable the cost side of the trade collapses. **The consumer that
  matters here is DISPATCH ADMISSION, and it is still unbuilt** — the kit-side half of ADR-0015
  shipped 2026-08-17, and it does not make the answer derivable for anything without a checkout.
  That trigger had not fired as of 2026-09-02. **It fired on 2026-09-15**, when dispatch admission
  began reading the declaration (ADR-0015 amendment 4, and the bullet at the end of this file).
  Devon chose that with this ruling named, and did not re-decide the ruling itself.
  Two facts that make narrowing safe should it ever be chosen, measured the same day so nobody
  re-establishes them: the out-of-process lanes read GitHub with Devon's PAT (`gh auth token`), not
  this App, so the ledger and audit are unaffected; and `claude-octopus`'s `upstream-sync.yml`
  authenticates as a **different** App — its pull requests are authored by `app/octo-upstream-sync`,
  not `alobar-sds-dispatch`. Evidence:
  `~/docs/software-delivery-system/2026-09-02-permission-and-interpreter-audit.md`.
- **A GitHub App's private keys are on its Credentials page, Key pairs tab** (`/settings/apps/<app>/
  key_pairs`, **New key**), not on the General page, as of 2026-10-09. The install screen defaults to
  **All repositories**; choose **Only select repositories** before clicking Install.
