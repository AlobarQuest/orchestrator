# Credential rotation: research for the design session (2026-10-08)

This research was read-only. No secret value was read: no `bws secret get`, no Keychain reads, no Coolify `/envs`, and no `coolify_get_deployment`. The sources were the four registries, the code, the docs, and official provider documentation. Inferences are marked **(inference)**, and claims the docs do not settle are marked **unverified**. The full provider findings, with every URL, are in `2026-10-08-credential-rotation-provider-docs.md` beside this file.

Sources used throughout:
- **Registries (R):**
  - `~/Projects/infraops-mcp-server/.cred-consumers.toml` (R-infra)
  - `~/Projects/change-manager/.cred-consumers.toml` (R-cm)
  - `~/Projects/factory-runner/.cred-consumers.toml` (R-fr)
  - `~/Projects/orchestrator/.cred-consumers.toml` (R-orch)
- **Policy:** `~/Projects/infraops-mcp-server/src/security-drift/cred-rotation.ts` (`CLASS_POLICY` at lines 40-102).
- **Executor:** `.../rotation-executor.ts`.
- **State:** `~/.config/infra-drift/cred-rotation-state.json` (ids and dates only).

---

## 1. Inventory

### Counts

There are 21 credentials:
- infraops: 9
- change-manager: 4
- factory-runner: 1
- orchestrator: 7

### Open exposures

**There are no open exposures.** Every exposure in the registries is resolved in the state file:

| Credential | Exposure id | Resolved (UTC) |
|---|---|---|
| github-classic-aihelper | `codex-2026-07-02` | 2026-07-02T22:49 |
| github-classic-lifeops | `codex-2026-07-02` | 2026-07-02T22:49 |
| openrouter-generic | `codex-2026-07-02` | 2026-07-02T22:58 |
| openai-project | `codex-2026-07-02` | 2026-07-02T23:18 |
| github-finegrained-mirror | `codex-2026-07-02` | 2026-07-02T23:32 |
| bitbucket-mirror-token | `container-log-2026-06-20` | 2026-07-03T00:17 |

So "exposed ones first" currently has an empty queue. **The only active trigger is `cred.rotation-requested` for openrouter-generic** (`rotate_requested = "2026-10-07"`, which is later than its lastRotated of 2026-07-02).

### Executor eligibility

The "Exec" column means two different things:
- **Class policy:** `CLASS_POLICY[class].executor`.
- **Plan-eligible today:** every gate in `rotationClassification` (cred-rotation.ts:400-440) passes. The gates are:
  - `consumers_verified` is set;
  - there are no `rotation_preconditions`;
  - every consumer kind is in `SUPPORTED_CONSUMER_KINDS` (bws-secret, keychain, coolify-env, gh-actions-secret; cred-rotation.ts:107);
  - `bws_uuid` is set;
  - the class has a probe.

### Probes

The probes the executor uses are at rotation-executor.ts:357-361 and :486-491:
- **github:** `GET api.github.com/user`
- **openrouter:** `GET openrouter.ai/api/v1/auth/key`
- **openai:** `GET api.openai.com/v1/models`
- **bitbucket:** Basic auth against `GET /2.0/repositories/{workspace}?pagelen=1`

A probe counts a credential live only on 200, and dead only on exactly 401 (`DEAD_STATUS`, :96). Every class without a probe in `CLASS_POLICY` has none.

### Due dates

The next due date is the anchor plus the class `maxAgeDays`. The anchor is lastRotated, else `created`. **(inference)** These are computed from the anchor plus maxAgeDays.

### Consumer abbreviations

| Abbreviation | Consumer kind |
|---|---|
| bws | bws-secret |
| kc | keychain |
| cenv | coolify-env |
| chash | coolify-env-hash |
| gha | gh-actions-secret |
| rf | runtime-fetch |

### The credentials

| # | id | class | provider | consumers (kind × n) | hosted consumer (`live_estate`)? | disposition | Exec: class / plan-eligible | probe | exposure | next due |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | github-classic-aihelper | github-pat-classic | github | bws×1 | no | revoke-no-replacement | yes / n/a (revoked) | github | codex-2026-07-02, resolved | none: recorded revoked |
| 2 | github-classic-lifeops | github-pat-classic | github | bws×1 | no | revoke-no-replacement | yes / n/a (revoked) | github | codex-2026-07-02, resolved | none: recorded revoked |
| 3 | github-finegrained-mirror | github-pat-fine-grained | github | bws×1, cenv×1 (mirror app `v04wc0…`, redeploy) | **yes** | reissue-least-privilege | yes / **yes** | github | codex-2026-07-02, resolved | **2026-12-29** (the "first natural due date" in the amendment) |
| 4 | bitbucket-mirror-token | atlassian-api-token | bitbucket | bws×1, cenv×1 (same app) | **yes** | reissue | yes / **yes** | bitbucket | container-log-2026-06-20, resolved | 2027-07-03 |
| 5 | openrouter-generic | openrouter-key | openrouter | bws×1, kc×1 | no | reissue | yes / yes, but `rotated_by_sds = true`, so the legacy window refuses it | openrouter | codex-2026-07-02, resolved; `rotate_requested` 2026-10-07 is **active** | requested now |
| 6 | openai-project | openai-key | openai | none | no | revoke-no-replacement | yes / no (no `bws_uuid`) | openai | codex-2026-07-02, resolved | none (no anchor: never ages) |
| 7 | bws-tok-ops-mini-20260730 | bws-machine-token | bitwarden-sm | kc×3 (INFRA_DRIFT, INFRAOPS, VPS_BACKUP) | no | reissue | **no** / no | none | none | 2027-07-30 |
| 8 | bws-tok-content-mini | bws-machine-token | bitwarden-sm | kc×1 (VIDEOCREATOR) | no | reissue | no / no | none | none | 2027-06-12 |
| 9 | bws-cred-rotation-token | bws-machine-token | bitwarden-sm | kc×1 (CRED_ROTATION) | no | reissue | no / no | none | none | 2027-07-02 |
| 10 | change-manager-full-bearer | change-manager-m2m-bearer | change-manager (ours) | bws×1, cenv×1 (CM app `re45ta…`, redeploy), rf×1 | **yes** | reissue | no / no | none | none | 2027-06-14 |
| 11 | change-manager-read-bearer | change-manager-m2m-bearer | change-manager | bws×1, cenv×1, rf×1 | **yes** | reissue | no / no | none | none | 2027-08-11 |
| 12 | change-manager-propose-bearer | change-manager-m2m-bearer | change-manager | bws×1, cenv×1, rf×1 | **yes** | reissue | no / no | none | none | 2027-08-12 |
| 13 | change-manager-observe-bearer | change-manager-m2m-bearer | change-manager | bws×1, cenv×1, rf×1 | **yes** | reissue | no / no | none | none | 2027-08-12 |
| 14 | factory-pr-token | github-pat-fine-grained | github | bws×1 (SDS Operator project), gha×8 | no hosted service; 8 CI secret copies (inference: not `live_estate`) | reissue | yes / **no**: two `rotation_preconditions` and no `consumers_verified` | github | none | 2027-01-30 |
| 15 | bws-sds-operator-token | bws-machine-token | bitwarden-sm | kc×1 (BWS_ACCESS_TOKEN_SDS), rf×1 | no | reissue | no / no | none | none | 2027-07-30 |
| 16 | orchestrator-system-bearer | orchestrator-m2m-bearer | orchestrator (ours) | bws×1, chash×1 (orch app `eqj5l7…`), rf×4 | **yes** (hash in the orchestrator env; restart needed, inference) | reissue | no / no | none | none | 2027-07-09 |
| 17 | orchestrator-verifier-bearer | orchestrator-m2m-bearer | orchestrator | bws×1, chash×1, rf×1 | **yes** | reissue | no / no | none | none | 2027-07-10 |
| 18 | orchestrator-operator-bearer | orchestrator-m2m-bearer | orchestrator | bws×1, chash×1 | **yes** | reissue | no / no | none | none | 2027-07-30 |
| 19 | orchestrator-observer-bearer | orchestrator-m2m-bearer | orchestrator | bws×1, chash×1, rf×1 | **yes** | reissue | no / no | none | none | 2027-07-28 |
| 20 | orchestrator-drift-reporter-bearer | orchestrator-m2m-bearer | orchestrator | bws×1, chash×1, rf×3 | **yes** | reissue | no / no | none | none | 2027-07-28 |
| 21 | orchestrator-factory-runner-bearer | orchestrator-m2m-bearer | orchestrator | bws×1, chash×1, gha×7, rf×1 | **yes** | reissue | no / no | none | none | 2027-07-08 |

### Registry facts the design must carry

- **Shared value across rows 19 and 20.** The observer and drift-reporter bearers share ONE value (R-orch:673-678). Their exposure is shared and their rotation is independent.
- **Rows 7-9 have one consumer set each, under several names.** Row 7 is a single token held under three Keychain names. No BWS-machine-token row carries `consumers_verified`, and a machine token has no BWS copy of itself (R-infra:246-251). So the executor has no old-value source for these tokens and could never confirm their revoke.
- **Row 14's preconditions** (R-fr:485):
  - The PAT's repository-access list can be edited on the settings page only.
  - The only valid verify is a throwaway branch workflow that pushes a change to `.github/workflows/**`.
- **Row 21 has seven Actions-secret copies.** A missed copy fails at claim time (R-orch:744-747).
- **Two classes in `CLASS_POLICY` have no registry rows: `brain-mcp-key` and `coolify-pg-password`.** ADR-0054 increment 4 names them, but no credential of either class is registered. This includes `BRAIN_OPENROUTER_API_KEY`, which R-infra:185-186 only mentions. So the "refused classes" are partly unregistered.
- **Row 1's sweep has a known blind spot.** Account SSH keys can't be listed with the keeper's scope (R-infra:242-243). This matters because the GitHub PAT landmine is that deleting a PAT deletes the SSH and deploy keys it created (cred-rotation.ts:46-54).

### Grouped by class

| Class | Count | Ids | `CLASS_POLICY` executor | maxAge | Probe |
|---|---|---|---|---|---|
| github-pat-classic | 2 | aihelper, lifeops (both revoked) | true | 180 | github |
| github-pat-fine-grained | 2 | mirror, factory-pr-token | true | 180 | github |
| atlassian-api-token | 1 | bitbucket-mirror-token | true | 365 | bitbucket |
| openrouter-key | 1 | openrouter-generic | true | 365 | openrouter |
| openai-key | 1 | openai-project (revoked) | true | 365 | openai |
| bws-machine-token | 4 | ops-mini, content-mini, cred-rotation, sds-operator | **false** | 365 | none |
| change-manager-m2m-bearer | 4 | full, read, propose, observe | **false** | 365 | none |
| orchestrator-m2m-bearer | 6 | system, verifier, operator, observer, drift-reporter, factory-runner | **false** | 365 | none |
| brain-mcp-key | 0 | none | false | 365 | none |
| coolify-pg-password | 0 | none | false | ∞ | none |

### Live versus spent credentials

Of the 21, three are spent revoke-no-replacement entries: rows 1, 2 and 6. They are quiet for different reasons:
- **Rows 1 and 2** are recorded revoked: `isRecordedRevoked` is true because lastRotated equals the exposure's resolution timestamp.
- **Row 6** is quiet because it has no `created` anchor. The state file has no `lastRotated` entry for it, so `isRecordedRevoked` is false.

**(inference)** So the live rotation population is:
- 1 OpenRouter key;
- 2 GitHub PATs;
- 1 Bitbucket token;
- 4 BWS machine tokens;
- 10 self-issued bearers.

Ten of those (all M2M bearers plus row 3 and row 4) have a hosted consumer.

---

## 2. Provider capability by class

URLs are in `2026-10-08-credential-rotation-provider-docs.md`. In the summary table, **M** = mint via API, **R** = revoke via API, **P** = discriminating probe, and **E/S** = expiry and narrower scoping.

| Class | M | R | P | E/S |
|---|---|---|---|---|
| OpenRouter key | **Yes** | **Yes** | Yes | Expiry yes (fixed at creation); credit limit; workspace |
| GitHub PAT (classic / fine-grained) | **No** | Partial (unauthenticated `POST /credentials/revoke`) | Yes | Expiry yes; fine-grained narrows to repos and permissions |
| GitHub App installation token (alternative) | Yes (needs the UI-made private key) | Yes | Yes | 1 h fixed; per-token repos and permissions |
| Bitbucket API token | **No** (unverified) | **No** (unverified) | Yes | Expiry mandatory; scopes; one workspace |
| OpenAI project key | **No**; yes via service account | Yes | Yes (401 unverified) | Expiry and scopes on service-account keys |
| Anthropic key (not in registry) | **No** | Yes (status) | Yes | `expires_at`; workspace |
| BWS machine-account token | **No** | **No** | Partial (exit code unverified) | Expiry at creation; project grants |
| Coolify env / PG password | n/a (we generate) | n/a | Ours | n/a |
| CM / orchestrator bearers, brain MCP keys | We generate | We remove | Ours | Ours |
| Cloudflare token (not in registry) | Yes | Yes | Yes | Yes |
| Resend key (not in registry) | Yes | Yes | Not researched | sending_access per domain |

### OpenRouter (`openrouter-key`)

- **Mint: yes.** `POST https://openrouter.ai/api/v1/keys`, authorised by a **management key**.
  - **Blast radius:** a management key is unscoped across every workspace on the account and cannot be narrowed. It cannot call completions. It is created in the dashboard only; no API to create one was found (unverified).
  - **Request body:** `name`, `limit` (USD), `limit_reset`, `include_byok_in_limit`, `expires_at`, and `workspace_id`.
  - **The plaintext key is returned once, in the top-level `key` field of the 201 response. This settles one of the backlog's open items.** `data` holds:
    - `hash`;
    - `label`, a display string such as `sk-or-v1-0e6...1c96`, which is not the key;
    - `name`, `disabled`, `limit`, `limit_remaining`, `limit_reset`, `expires_at`, `workspace_id`, `created_at`, and `usage`.
  - Source: https://openrouter.ai/docs/api/api-reference/api-keys/create-a-new-api-key (fetched 2026-10-08).
- **Revoke: yes.** `DELETE /api/v1/keys/{hash}`, or `PATCH … {disabled:true}`.
  - The docs call `hash` only a "Unique hash identifier for the API key" and **do not say how it is derived**, so whether it can be computed from the key value is **unverified**. **(inference)** The worker can avoid needing to know: pin the `data.hash` that the create response returns.
  - The old key (the one minted at the console) has no pinned hash. Revoking it means matching the `label` prefix and suffix from `GET /api/v1/keys`, or a console revoke.
  - Management-key docs: https://openrouter.ai/docs/guides/overview/auth/management-api-keys
- **Probe:** `GET /api/v1/key` returns 401 for an invalid, disabled or expired key.
  - The legacy executor uses `/api/v1/auth/key` (rotation-executor.ts:359), which is the older path. **Unverified** that it still answers identically.
  - Whether the probe costs credit is unverified; it is a metadata read **(inference)**.
- **Expiry:** `expires_at` is fixed at creation; extending means replacing the key. Narrowing is by credit limit, reset period and workspace. No per-model scoping was found (unverified).
- OpenRouter publishes a rotation cookbook: https://openrouter.ai/docs/cookbook/administration/api-key-rotation

### GitHub PATs (`github-pat-classic`, `github-pat-fine-grained`)

- **Mint: no API, for either kind.** Creation is UI only (https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens).
- **Revoke:**
  - `POST https://api.github.com/credentials/revoke` must be sent **unauthenticated**. It takes up to 1000 tokens per call, allows 60 calls an hour, and returns 202.
    - Source: https://docs.github.com/en/rest/credentials/revoke
  - The org fine-grained PAT endpoints are for GitHub Apps and organisations only. They do not apply to the personal account AlobarQuest.
- **Probe:** `GET /user` or `GET /rate_limit`. `/rate_limit` does not count against the rate limit. That a dead token gets 401 there is **inference**, not stated in the docs.
- **Expiry:** fine-grained PATs take a custom expiry and are limited to one owner plus selected repositories.
  - The registry already records two consequences: the repository-access list cannot be changed by API (R-fr:485), and Actions secrets are write-only.
- **Writing the new value to consumers works by API** (https://docs.github.com/en/rest/actions/secrets). `PUT /repos/{o}/{r}/actions/secrets/{name}` takes a libsodium sealed box.
  - A fine-grained PAT needs "Secrets: write" on each repository.
  - A classic PAT needs `repo`.
  - The legacy executor shells out to `gh secret set` with the value on stdin (rotation-executor.ts:480-482).

**The alternative is a GitHub App.**
- Source: https://docs.github.com/en/rest/apps/apps#create-an-installation-access-token-for-an-app
- `POST /app/installations/{id}/access_tokens` is authenticated with a JWT signed by the App's private key.
- The token lasts 1 hour, and the request can narrow it to specific `repositories` and `permissions`.
- `DELETE /installation/token` revokes it.
- **No API creates an App private key**, so that key becomes the long-lived secret, and it is minted and rotated in the UI.
- Whether installation tokens can set Actions secrets is **unverified**.
- The orchestrator already runs a dispatch App. Per the CLAUDE.md invariant, it has no `checks` permission, and its installation reach is "deliberately wider than its work". **(inference)** FACTORY_PR_TOKEN's job (checkout and push, including workflow files) is the textbook case for an installation token. The App would then need `workflows: write`, which widens that App.

### Bitbucket (`atlassian-api-token`)

- **Mint and revoke: no API found for either** (unverified). Sources: https://support.atlassian.com/bitbucket-cloud/docs/create-an-api-token/ and https://support.atlassian.com/bitbucket-cloud/docs/access-tokens/ Atlassian API tokens are created at id.atlassian.com, and an expiry is **mandatory** at creation.
- Repository, project and workspace access tokens are also UI-only, are capped at 25 each, and cannot be edited after creation.
- **App passwords are fully deprecated, and the dates are past.** The token registered here is an API token, so it is unaffected.
- **Probe:** the executor's `/2.0/repositories/{ws}` with Basic `email:token` is the documented form. Bearer is also accepted.
- **Scoping:** scopes are required, and a token can be limited to a single workspace.

### OpenAI (`openai-key`)

- Source: https://developers.openai.com/api/reference/typescript/resources/admin/subresources/organization/subresources/projects
- **Project API keys:** the Admin API can list, retrieve and **delete** them, but **cannot create** them.
- **Creation goes through service accounts:**
  - `POST /v1/organization/projects/{p}/service_accounts` takes `expires_in_seconds` and returns `api_key.value` once.
  - `POST …/service_accounts/{id}/api_keys` takes `scopes`.
- **Admin keys can be created by API** with `POST /v1/organization/admin_api_keys`, authorised by an existing admin key. They are organisation-wide and only Owners can use them, so an admin key can create further admin keys.
- **Probe:** `GET /v1/models`. A 401 on a dead key is **unverified**, because platform.openai.com blocked the fetcher.
- **Relevance:** the only registered OpenAI key is revoked, with no replacement.

### Anthropic (no registry rows)

- **Admin API:** it can list keys and set their status to `active`, `inactive` or `archived`. **It cannot create keys.** The docs FAQ says: "No. You create API keys in the Claude Console." (https://platform.claude.com/docs/en/manage-claude/admin-api)
- The Admin API is not available to individual accounts.
- **Probe:** `GET /v1/models`. 401 `authentication_error` means the key is "malformed, revoked, or expired".
- **Scoping:** keys carry `expires_at` and a `workspace_id` scope.

### BWS machine-account access tokens (`bws-machine-token`)

- **Mint and revoke: web app only** (https://bitwarden.com/help/access-tokens/).
  - The `bws` CLI has no token or machine-account commands.
  - The Bitwarden Public API does not cover Secrets Manager.
  - SDK minting is unverified **(inference: none)**.
- **After revocation, an already-authenticated machine may keep reading secrets for up to one hour.** A dead-probe immediately after revoke can therefore read "live".
- **Probe:** `bws project list`. Its failure exit code and message are **unverified**. It needs `--color no` with `FORCE_COLOR` and `CLICOLOR_FORCE` unset.
- **Expiry and scope:** expiry is set at creation (the default is never). Narrowing is through the machine account's project grants.
- **Conclusion:** this class stays human-minted and human-revoked permanently, which matches ADR-0054's "What stays human".

### Coolify (`coolify-pg-password`, and the cenv consumers)

- **Env vars:** `PATCH /api/v1/applications/{uuid}/envs` takes `key` and `value`, and a bulk variant exists (https://coolify.io/docs/api-reference/api/operations/update-env-by-application-uuid).
- **Postgres password:** `PATCH /api/v1/databases/{uuid}` accepts `postgres_password`.
  - **(inference, not in the Coolify docs)** The Postgres image applies `POSTGRES_PASSWORD` only when it initialises a data directory. Changing the field probably does not change the role password in an existing volume; an `ALTER USER` is also needed.
  - This sits alongside `CLASS_POLICY`'s landmine: never cycle the password in place; delete the volume and redeploy fresh (cred-rotation.ts:68-74).
  - ADR-0054 increment 4 already lists a `postgres_password` parameter for `coolify_update_database` as a prerequisite.
- **Coolify API token scopes:** `read`, `read:sensitive`, `write`, `deploy` and `root` (https://coolify.io/docs/api-reference/permissions). Sensitive reads are a separate check.
  - Tokens are created in the dashboard only (unverified).
  - The executor's `getEnv` reads `real_value` (rotation-executor.ts:470), so **it needs `read:sensitive`** **(inference)**.

### Self-issued bearers: change-manager and orchestrator M2M (and brain MCP keys)

We control both ends of these, so any generator can mint and revoke them.

**change-manager bearers:**
- Each value is plaintext in a Coolify env on the CM app. Rotating one means a Coolify write and a redeploy.
- Landmines (cred-rotation.ts:93-99):
  - No two scopes may share a value, because the first matching scope wins.
  - An unset scope variable grants nothing.
- A redeploy is rolling, so the old and new containers overlap (Projects CLAUDE.md, "rolling update").

**Orchestrator bearers:**
- Coolify stores `sha256(token)` per key id in `ORCHESTRATOR_M2M_CREDENTIALS`, so a new value is deployed as its hash.
- Write the credentials before the roles. Never restart the orchestrator while a dispatched run is live.
- `agent_id` attribution is permanent (cred-rotation.ts:85-91; orchestrator CLAUDE.md).
- **Make-before-break is not possible today (inference).** The map is keyed by key id, so one key id holds one hash. Overlap would need either a second key id or a map that accepts two hashes per id. Unverified against `so/identity/auth.py`.

**Probes:** the probe for an orchestrator bearer would be an authenticated GET on sds.alobar.net, and for a CM bearer a GET on change-mgr.alobar.net (a named User-Agent is required: Cloudflare 1010). Neither exists today.

**Brain MCP keys:** rotating one re-keys a live claude.ai connector, which is a human UI step (cred-rotation.ts:62-66).

---

## 3. The authority each automated path needs, ranked by blast radius

Highest blast radius first. In each row, "crown jewel" is the credential the automated path would hold that is newly concentrated in the rotation worker. All rows are inferred from the sections above.

| # | Path | Authority needed | Crown jewel and blast radius |
|---|---|---|---|
| 1 | Coolify consumer deploys: rows 3 and 4 (cenv), 10-13 (CM env), 16-21 (orchestrator hash env) | A Coolify API token with `write` + `deploy`, and `read:sensitive` if the executor's read-before-write (`getEnv` → `real_value`) is kept | **The Coolify token.** Team-wide: it can rewrite any hosted app's env, deploy, delete. And with `read:sensitive`, read every env secret. Already held by infraops. Giving it to a worker moves the largest jewel in the estate into the rotation path. A narrower alternative: compare sha256 prefixes rather than read values. Writing still needs `write` (inference). |
| 2 | GitHub Actions secret copies: 8 for row 14, 7 for row 21 | Secrets:write on 8 repos (fine-grained PAT, classic `repo`, or `gh` keeper login) | **The secret-writing GitHub identity.** It can replace any CI secret in those repos, which is a supply-chain lever. The legacy executor uses the ambient `gh` login (rotation-executor.ts:480, :509-515). |
| 3 | GitHub App installation tokens as the replacement for FACTORY_PR_TOKEN | An App private key with contents and workflows write | **The App private key.** UI-minted, long-lived, mints 1-hour tokens for every installed repo. It removes the PAT but becomes the jewel. |
| 4 | BWS keeper writes, for every reissue | The cred-rotation BWS token (`bws-cred-rotation-token`, row 9) can write Ops / Platform (R-infra:304-313) | **The cred-rotation BWS token.** It can rewrite or delete any secret in Ops / Platform. It **cannot** write SDS Operator, where the keepers for rows 14 and 16-19 live (R-fr:490; R-orch:594, 636, 663, 692). Rotating those needs a new write grant on SDS Operator, which is a new standing authority (inference from the registry project notes; grants unverified). |
| 5 | OpenRouter mint/revoke ("Option 2", accepted for openrouter-generic) | An OpenRouter management key | **The management key.** It can create keys (spend), and disable or delete any key on the account, including the hosted brains' `BRAIN_OPENROUTER_API_KEY` (a brain outage). It cannot run completions and cannot be scoped. Containment the docs support: credit `limit` and `expires_at` on minted keys; deleting only the hash the worker created. Whether a management key itself carries an expiry is unverified. |
| 6 | OpenAI service-account mint | An OpenAI admin key | **The admin key.** Organisation-wide; it can mint more admin keys. No registered credential needs it (row 6 is revoke-only and already revoked). |
| 7 | Self-issued bearers | Only rows 1 and 4 (Coolify plus BWS) and the worker's own WORKER M2M identity (ADR-0054:76-79) | No provider jewel. The risk is self-lockout. Rotating `orchestrator-system`, or the bearer the worker itself uses, mid-run can cut the worker off its own evidence path. |
| 8 | BWS machine tokens, Bitbucket, GitHub PATs (mint), Anthropic | None possible: no API | Human console steps stay. No new jewel. |

**Note on row 4 (inference).** The jewel that already exists is the cred-rotation BWS token, and it unlocks everything else. The worker would read the Coolify token and the OpenRouter management key from BWS. Whichever identity can read those secrets therefore holds the union of rows 1 and 5. Where each jewel's BWS secret lives, and which machine account can read it, is the real containment boundary.

---

## 4. SDS safety machinery a design can reuse

`orch/` is `~/Projects/orchestrator/`; `so/` is `orch/src/orchestrator/`; `ip/` is `~/Projects/intent-packages/`.

### Human gates and approval policy

- **The AWAITING_APPROVAL → READY gate** is HUMAN-only. It is defined at `so/kernel/transitions.py:66,79` and enforced at `:110-115` with `approval_required`.
- **`record_approval`** is at `so/services/intake/packages.py:73`. It requires a human, accepts subjects `authority` and `action`, and runs `runner_authority_violation`.
  - It enforces no lifecycle state (CLAUDE.md invariant).
  - Production approvals go through `/review`.
- **`human_may_adjudicate`** is at `so/services/verifier/verifier_evaluators.py:108`.
- **Approval policy:** `ip/approval-policy.toml`. Only `[profile.dependency-update.grant]` exists (:119).
  - `[profile.non-software-operational]` (:95-101) has **no grant**: "The least reversible reach in the vocabulary; it earns the most restraint, not the least."
  - A standing-package refusal also applies: `approval_policy.py` `_standing_refusal`.
- **Known-good patterns (ADR-0011):** `so/factory-policy.toml:46-53`, matched in `so/factory_policy.py:573-601`.
  - **No pattern covers `operational_action`.** The only pattern is dependency-update on change-manager (`factory-policy.toml:181-224`).

### Authority envelope and capability vocabulary

- **The envelope:** `so/kernel/authority.py:20` (`KNOWN_FIELDS`), `:40` (`level_for` defaults to prohibited), `:157` (fingerprint).
  - Write-once is enforced by `orch/tests/architecture/test_authority_write_once.py:155,168`.
  - Budgets are at `:24`.
- **`operational_action` is the credential capability** (`so/capability_vocabulary.py:75-93`). It is in `ORCHESTRATOR_ONLY_CAPABILITIES` and deliberately absent from `RUNNER_CAPABILITIES`, so "a unit carrying it can never be handed to a runner".
  - Admission also refuses it with `capability_outside_runner_vocabulary` (`so/services/execution/dispatch.py:436-456`).
  - **There is no finer-grained unit capability** such as `credential.rotate`.
- **The package-level authority vocabulary** is a separate, registry vocabulary. `rotation-openrouter-generic` declares:
  - allowed: `credential_create`, `credential_revoke`, `secret_read`
  - requires_approval: `secret_write`
  - prohibited: `infra_mutation`, `repository_write`, `outward_publish`, `email_send`

  (`ip/packages/rotation-openrouter-generic/package.yaml:224-235`.) **(inference)** A worker that deploys to Coolify would need `infra_mutation`, which that package prohibits.
- **The capability vocabulary has four copies** (CLAUDE.md invariant: grep the portfolio before widening it).

### Reach and change windows

- **Reach vocabulary:** `so/reach_vocabulary.py:47`; `reach_from_snapshot` is at :103 and is all-or-nothing.
- **Windows** (`so/factory-policy.toml`):
  - `live_estate`: 02:00-06:00 America/New_York (:249-263)
  - **`operator_machine`: also 02:00-06:00** (:316-328)
  - `external_system`: no window (:265+)
- **The window is checked only at dispatch admission.** `change_window_refusal` (`so/services/execution/reach_admission.py:157`) says it is "Asked once, at admission, and nowhere else". Its only dispatch caller is `dispatch.py:214`. Landing has separate checks at `pr_merge_admission.py:481` and `estate_landing_admission.py:436`.
  - `services/lifecycle/claims.py` has **no window check**.
- **Two discrepancies with ADR-0054.** Both are flagged as facts, not resolved:
  - ADR-0054 says "the worker's claim checks the `live_estate` change window". No claim-time window code exists. Since operational units are never dispatched, **nothing currently windows them** (inference).
  - Amendment 1 says the OpenRouter package (`external_system` + `operator_machine`) "has no window". The policy gives `operator_machine` a 02:00-06:00 window. It is moot only because of the admission-only enforcement above.
  - The policy comments say windows "compose by intersection" (`factory-policy.toml`, `[reach.external_system]` comment). I did **not** verify how `external_system` (no window) combined with `operator_machine` (windowed) actually composes in code. Treat that result as **unverified**.
- **Carrier opt-in:**
  - `orch/src/work_carrier/workability.py:113`: `STANDING_OPERATIONAL_PACKAGES = frozenset({"rotation-openrouter-generic"})`, a reviewed-diff opt-in.
  - `:118`: `OPERATIONAL_REACH`.
  - `:430-456`: the `_allowlisted`, `_reach` and `assess_operational` functions.

### Leases

- **Constants:** `so/kernel/leases.py:23` (`DEFAULT_LEASE` = 15 min), `:31` (`LEASE_CEILING` = 2 h), `:36` (token hashing).
- **Per-reach leases:** `live_estate` 30 min (`factory-policy.toml:235-247`); `external_system` 60 min (:286-298).
- **Lease resolution:** `so/services/lifecycle/lease_policy.py:41` (`claim_lease`).
- **Claims** (`so/services/lifecycle/claims.py`):
  - `claim_unit` at :47 (WORKER, READY)
  - `renew_claim` at :144
  - `reclaim_expired_claim` at :213
  - `validate_active_claim` at :860, which refuses evidence after a lapse
- **A lapsed lease ends the attempt.** A worker step that overruns its lease mid-deploy cannot file evidence. **(inference)** A long Coolify redeploy or a BWS one-hour revocation lag must fit inside the lease.

### Observation spine and secret detector

- **Route and service:** `POST /api/v1/observations` (`so/api/routes/release.py:219`) calls `record_observation` (`so/services/release/observations.py:70`).
  - Allowed roles are SYSTEM and OBSERVER (:215). The secret scan runs at :269.
- **OBSERVER confinement:** `so/api/dependencies.py:36,114` (`_confine_observer`).
- **Secret detector:** `so/kernel/secret_metadata.py`. It has `BWS_TOKEN_SHAPE` at :22 and key-name parts at :26-38 (`credential`, `token`, `secret`, `api_key`, `password`).
  - **Bare `key` is not in the list.**
  - The detector rejects even innocent names such as `missing_credential_status` (`docs/history/claude-md-invariants-archive.md:558`).
- **The rotation proposer already files** `rotation_proposer` / `rotation_due` / subject `credential` observations (`orch/src/rotation_proposer/observation.py:29-35`). Migration 0041 is deployed.

### Evidence

- **Judgment types:** `JUDGMENT_TYPES` (`verifier_evaluators.py:47`) holds `human_review`, `external_attestation` and `observation`, all with a human floor (:78-82; an unknown type defaults to human).
  - The `non-software-operational` profile allows only those three (`ip/src/intent_packages/profiles/non_software_operational.py:38-42`) and forbids `automated_test` (:94).
  - **So every rotation criterion is human-adjudicated today.** That is the "five criterion adjudications" in amendment 1.
- **Append-only evidence:** `orch/migrations/versions/0001_ws31_core.py:14-18,335-348`.

### change-manager records

- **Sources:** `change-manager/app/sources.py:53` (`WORK_SOURCE`), :58 (`PROPOSED_SOURCES = {deploy, work}`), :65 (policy-approved `{deploy}` only).
  - So **a `work` record is approved by a human** (`app/guards.py:36-66`).
- **Scopes:** `app/scopes.py:67-92` (PROPOSE), :96-109 (`STATUS_MOVING_ROUTES` are full-scope only), `app/auth.py:7` (`_token_scopes`).
- **The `rotation` source** is a derived source re-asserted by scans (`sources.py:3`). The 04:00 executor treats sources as a denylist (:16-21).
- **The work watcher** (ADR-0029) is at `orch/src/work_watcher/__init__.py:1-22`.

### The `rotated_by_sds` handover

The flag passes ownership of a credential from the legacy 04:00 window to the SDS.

**infraops (#110, merged 2026-10-07):**
- Parser: `cred-consumers.ts:72,208-209`.
- `scanFindings` (`cred-rotation.ts:290-295`) strips rotation triggers for SDS credentials from the 03:00 post.
- `buildCredClassifications` skips them (`cli/security-drift-cli.ts:133-136`).
- `rotationRefusals` (`cli/change-mgr-cli.ts:106-123`) refuses them in the 04:00 window. **It fails closed: an unreadable registry refuses every plan.**
- `cred-findings` exposes the flag (`security-drift-cli.ts:166-188`).

**orchestrator (#374, merged 2026-10-07):** the proposer reports four states:
- `legacy`
- `not-handed-over`
- `unlaned`
- a hard stop when the flag is missing

These are described in `orch/docs/operations/rotation-proposer.md`, "States it reports". This is the interlock ADR-0054 asks increment 3 to name.

### The rotation proposer

`orch/src/rotation_proposer/` (`cli.py`, `findings.py`, `observation.py`, `standing.py`):
- Exit codes: 0 / 2 / 3 (`cli.py:83-85`).
- It uses the OBSERVER key id (`cli.py:90`).
- Trigger precedence is exposure, then request, then age (`findings.py:50-59`).
- Its classification row is OBSERVE_ONLY (`orch/tests/architecture/test_external_content_observes_only.py:216`).
- **It is built but not scheduled:** there is no LaunchAgent and no Healthchecks check (`rotation-proposer.md:5-7`).
- A new worker would need its own row in that test file. The row rules are in the docstring at :9-28: the declared writes must equal the writes detected, and the program may not import `orchestrator`.

### The legacy executor (`rotation-executor.ts`)

**Order:** verify-new → store → deploy → revoke-confirm. Each phase returns early on failure, and the revoke branch is unreachable unless verify passed (:5-6).

**Guards:**
- Run-time re-checks of class, attestation and consumer kinds (:110-121).
- The staged value is probed for 200 **before any write** (:158-170).
- The old value is quarantined to a named BWS secret before the keeper is edited in place, so its UUID stays stable (:172-195).
- It refuses when the keeper is already new but no quarantine exists: "never guess" (:175-182).
- Deploys are idempotent and skip a value that is already equal (:276-337).
- The `gh` keeper must authenticate both before and after the dead-probe. This guards against revoking the wrong token (:218-226, :252-257).
- The old value must probe exactly 401. A 200 means "still LIVE", and any other status is indeterminate, so the executor refuses (:234-249).
- Only after that does it retire the quarantine, delete the staging Keychain item, and run `completeRotation` (:259-266).
- A Scrubber redacts every value it has touched from the outcome details (:82-94).
- **It never mints and never revokes at a provider** (:7-9).

**Accepted weaknesses:**
- `bws secret create` and `bws secret edit`, and `security add-generic-password -w`, put values in argv, where the local process list can see them (:16-19).
- `getEnv` reads Coolify `real_value` (:470).
- Bitbucket's 401 is treated as dead; the BWS one-hour revocation lag does not apply to it, because that class is not executor-run.

### infraops MCP redaction gaps

`~/Projects/CLAUDE.md` says: "An MCP tool that returns secrets cannot be made safe by careful use — only by not calling it." It records two confirmed leaks:
- `cloudflare_list_pages_projects` returned env vars in plaintext.
- `coolify_get_deployment` returned the deploy private key as base64 inside a logged `echo` command.

The redactor is `infraops-mcp-server/src/utils/redaction.ts`.
- **Name rule (:12-13):** `SECRET_NAME` = password, secret, token, credentials, private_key, …
- **Value shapes (:25-35):** PEM, JWT, `gh*_`, `github_pat_`, `sk-`.

**(inference, from reading those regexes)** It would not catch:
- BWS access tokens (there is no shape for them);
- Atlassian API tokens;
- a base64-encoded PEM (which is why the deploy key leaked);
- Coolify's `real_value` field, because that name does not match `SECRET_NAME`, so it is redacted only if the value matches a shape.

`sk-or-v1-…` does match the `sk-` shape.

ADR-0054 increment 4 already requires that `coolify_update_app_env` read its value from a file, because today the value is a literal tool argument and so lands in the transcript.

**Design consequence (inference):** the worker must call provider and Coolify HTTP APIs in-process, never through infraops MCP tools.

### Precedent and lessons

- **WS-P2.13** (ADR-0054:28-33): three units using `operational_action`, worked by HQ, with Devon doing the console steps. Its package is `ip/packages/wsp213-bws-machine-token-rotation/`.
- **"Every probe must be proven to tell a good value from a bad one"** (ADR-0054:91-93).
- **`orch/docs/operations/credentials.md`:**
  - #71 (:113-125): always pair a probe with a wrong-password control.
  - #153 (:216-240): FACTORY_PR_TOKEN means re-setting all eight copies, and is verified by a discriminating probe, "never with a green `gh secret set`".
- **`orch/docs/method-lessons.md`:**
  - #72 (:54-61): generate → persist (0600) → mutate → verify; never print parsed components of a secret.
  - :681-690: disable redirect following in any authorization probe.
- **Invariants archive** (`orch/docs/history/claude-md-invariants-archive.md`): incidental mentions only. These are WS-P2.13 adjudication defects (:195) and "grep the whole portfolio for the UUID" (:1130, :1593). There is no dedicated rotation lesson.
- **Other ADRs:**
  - 0009:49 cites the WS-P2.13 package to argue that `non-software-operational` is not a single reach.
  - 0035:100,150-152 says "The rotation surface does not grow".
- **Earlier design:** `change-manager/docs/superpowers/specs/2026-06-18-credential-rotation-backlog-design.md`.

---

## 5. Parked state, verbatim

### Backlog item `37bb0f906c7b`

From `~/Projects/orchestrator/PROJECT.md`, added in f15c326 (#375). The same text is in `~/.portfolio/inbox.jsonl` at lines 488-489 (untriaged, then triaged):

> - [ ] (P1) Credential rotation design work session (ADR-0054): step back and plan an overall rotation position and architecture before more rotation work. Devon 2026-10-07: each credential type is heading toward its own custom process. PARKED STATE: change record 122 approved and carried as intake 496f9c81; decomposition ff08aeed approved, creating units rotate-and-deploy (f509d6e4) and revoke-confirm-and-record (63bfe30b), both draft and awaiting authority approval, none claimed, so the decomposition can still be superseded (ADR-0052). openrouter-generic is marked rotated_by_sds with rotate_requested = 2026-10-07 in infraops' registry; the legacy 04:00 window refuses it and the proposer is unscheduled. No new key minted or staged. INPUT: Devon accepted Option 2 for OpenRouter (the SDS mints and revokes through a management key at /api/v1/keys, with no console steps or Keychain staging). Open before building: which create-response field carries the key (docs say key or label), whether hash derives from the key value, and how to contain the account-wide unscoped management key (BWS-only, an expiry, a credit limit on minted keys, deleting only the pinned hash). Questions for the session: a per-class provider adapter versus a generic make-before-break contract; staging in BWS rather than the Keychain; the Keychain consumer for OPENROUTER_API_KEY; one gate per rotation; the rotation worker's home (ADR-0054 increment 3). — added 2026-10-07

### IDs it names

| Object | Id | Status |
|---|---|---|
| Change record | 122 | Approved |
| Intake | 496f9c81 | Carried |
| Decomposition | ff08aeed | Approved |
| Unit `rotate-and-deploy` | f509d6e4 | Draft, awaiting authority approval |
| Unit `revoke-confirm-and-record` | 63bfe30b | Draft, awaiting authority approval |

- The package is `rotation-openrouter-generic` rev 1, approved, occurrence `'2026-10-07-requested'`.
- The registry entry for `openrouter-generic` sets `rotated_by_sds = true` and `rotate_requested = "2026-10-07"`. Its fingerprint is `1f9697cd` (R-infra:191-196).
- The package's operating procedure still describes the **console mint and Keychain staging** path (package.yaml `operating_procedure`). Option 2 has not yet been written into the package.

### Roadmap row L1b

`docs/roadmap.md`, as amended in #375:

> Increment 2's machinery shipped 2026-10-07: the rotation proposer (#372, #374), the carrier's operational branch (#371), infraops' `cred-findings` and its handover flag `rotated_by_sds`, which the legacy window refuses (infraops-mcp-server #109, #110), and migration 0041 (deployed as `ac97b9a-rotationproposer-amd64`). The first rotation, `openrouter-generic`, reached two approved-decomposition units and is parked before any claim … Increments 3 to 5 wait on that session.
