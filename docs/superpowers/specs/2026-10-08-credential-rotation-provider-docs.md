# Provider credential lifecycle: what official docs say (researched 2026-10-08)

Key: (a) mint via API, (b) revoke via API, (c) live/dead probe, (d) expiry and narrowing.
"Inference" marks my reasoning, not a doc statement. "Unverified" means the docs did not settle it.

## 1. OpenRouter

- **(a) Mint: yes.** `POST https://openrouter.ai/api/v1/keys` with a **management key** (older docs call it a "provisioning key"). Body (fetched): required `name`; optional `expires_at` (ISO 8601 UTC with seconds), `limit` (USD), `limit_reset` (daily/weekly/monthly), `include_byok_in_limit`, `workspace_id`, `creator_user_id` and `external`. The plaintext key is returned once, in the create response only.
  - https://openrouter.ai/docs/api/api-reference/api-keys/create-a-new-api-key
  - https://openrouter.ai/docs/features/provisioning-api-keys
- **Management key blast radius:** it cannot call completion endpoints. It is **unscoped**, with access across all of the account's workspaces, and cannot be narrowed to a workspace or model. Org Admins create it in the dashboard (Management API Keys page). I found no API for creating a management key (unverified, but the docs describe UI creation only). https://openrouter.ai/docs/guides/overview/auth/management-api-keys
- **(b) Revoke: yes.** `DELETE /api/v1/keys/{hash}`, or `PATCH /api/v1/keys/{hash}` with `disabled: true`. Same page as above.
- **(c) Probe:** `GET https://openrouter.ai/api/v1/key` returns the key's label, limits and usage. An invalid, disabled or expired key gets 401 ("API key expired" for an expired key). The docs do not say whether the call costs credits (unverified). Inference: it is a metadata read and runs no inference. https://openrouter.ai/docs/api-reference/limits
- **(d) Expiry:** yes, via `expires_at`. It is fixed at creation and cannot be changed; to extend, create a new key and delete the old one. A key can be narrowed to a credit `limit` and reset, and placed in one workspace with `workspace_id`. I found no per-model or per-endpoint scoping (unverified). A rotation cookbook exists: https://openrouter.ai/docs/cookbook/administration/api-key-rotation

## 2. GitHub

### Classic and fine-grained PATs
- **(a) Mint: no API.** The PAT docs describe UI creation only. Inference: I found no REST endpoint, and the absence is consistent with this. https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens
- **(b) Revoke:**
  - `POST /credentials/revoke` (it is a POST, not a DELETE). Body `{"credentials": [...]}`, at most 1000 per request. It must be sent **unauthenticated** (an authenticated request gets 403) and is limited to 60 requests per hour. It returns 202. It covers `ghp_`, `github_pat_`, `gho_`, `ghu_` and `ghr_` tokens. https://docs.github.com/en/rest/credentials/revoke
  - Org fine-grained PAT endpoints: `GET/POST /orgs/{org}/personal-access-tokens[/{pat_id}]` revoke a token's access to org resources, and `/orgs/{org}/personal-access-token-requests` approves requests. **Only GitHub Apps can call these, and they are org-only.** They do not apply to AlobarQuest, which is a personal account. https://docs.github.com/en/rest/orgs/personal-access-tokens
  - `/applications/{client_id}/token` is for OAuth-app tokens only (not re-fetched; this is from your premise, unverified here).
- **(c) Probe:** `GET /rate_limit` with the token. Per the docs, it "does not count against your REST API rate limit". https://docs.github.com/en/rest/rate-limit/rate-limit . Inference: a revoked or invalid token gets 401 there and on `GET /user`. I did not find doc text that confirms 401 specifically for `/rate_limit`.
- **(d) Expiry and scope:** fine-grained PATs have a custom expiry, and an infinite lifetime is allowed unless an org or enterprise policy caps it. Each one is limited to a single resource owner and can be limited to specific repositories. Classic PATs offer preset or custom expiry, and GitHub auto-removes tokens unused for a year. Same PAT docs URL.

### GitHub App installation tokens (the alternative)
- **(a) Mint:** `POST /app/installations/{installation_id}/access_tokens`, authenticated with a JWT signed by the App's private key. Body: `repositories` (names), `repository_ids` and `permissions`, all of which narrow the token. It returns 201 with `token`, `expires_at`, `permissions` and `repositories`. **The token expires 1 hour after creation.** https://docs.github.com/en/rest/apps/apps#create-an-installation-access-token-for-an-app
- **There is no REST endpoint to create an App private key.** Keys are made in the App settings UI, which makes the private key the long-lived secret. Same URL; this is a doc-summary statement, so treat it as "no endpoint documented".
- **(b) Revoke:** `DELETE /installation/token` revokes the installation token used to call it. https://docs.github.com/en/rest/apps/installations

### Actions secrets via API
- **Yes.** `PUT /repos/{owner}/{repo}/actions/secrets/{secret_name}`. The value must be a LibSodium sealed box encrypted with the key from `GET /repos/{owner}/{repo}/actions/secrets/public-key`.
  - **Classic PAT or OAuth token:** needs the `repo` scope plus collaborator access. https://docs.github.com/en/rest/actions/secrets
  - **Fine-grained PAT:** needs the repository permission "Secrets": **write** for the PUT, **read** for the public-key GET. https://docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens
  - **GitHub App installation tokens:** the page I fetched did not say whether they work for the PUT (unverified).
  - Org secrets need `admin:org` (not applicable to a personal account).

## 3. Bitbucket Cloud

- **App password deprecation:** new app passwords stopped on 2025-09-09. Brownouts began 2026-06-09 (API returns 401, Git over HTTPS returns 410). **The sources disagree on the final date.** The fetched phase-2 blog says app passwords are permanently disabled on **2026-06-09**. A search summary gave 2026-06-09 for brownouts and 2026-07-28 for full deprecation. Both dates are past, so any remaining app password should be treated as dead.
  - https://www.atlassian.com/blog/bitbucket/bitbucket-cloud-transitions-to-api-tokens-enhancing-security-with-app-password-deprecation
  - https://www.atlassian.com/blog/bitbucket/bitbucket-cloud-enters-phase-2-of-app-password-deprecation
- **API tokens (Atlassian-account tokens with Bitbucket scopes):**
  - (a) Mint: UI only, under Atlassian account > Security > "Create API token with scopes". An expiry date is **mandatory** at creation; the docs give no maximum lifetime. I found no create API (unverified). https://support.atlassian.com/bitbucket-cloud/docs/create-an-api-token/
  - (b) Revoke: documented in the UI; no revoke API found (unverified). https://support.atlassian.com/bitbucket-cloud/docs/api-tokens/
  - (d) Scopes are required, and a token can be restricted to one workspace.
- **Repository, project and workspace access tokens:** created by admins in the UI and can carry an expiry. Scopes are set at creation and tokens cannot be edited afterwards. The cap is 25 per repository, project or workspace, and project and workspace tokens need Premium. I found no create or revoke REST endpoint in the Bitbucket Cloud REST docs (unverified). https://support.atlassian.com/bitbucket-cloud/docs/access-tokens/
- **(c) Probe:**
  - API token: send it as `Authorization: Bearer <token>` (preferred) or as Basic `email:token`. https://support.atlassian.com/bitbucket-cloud/docs/using-api-tokens/
  - `GET https://api.bitbucket.org/2.0/user` should work for an API token. Inference: 401 when dead; not doc-confirmed.
  - **Access tokens "are connected to a repository, not a user"**, so `/2.0/user` is the wrong probe for them. Probe `GET /2.0/repositories/{workspace}/{repo}` instead. Inference: it is not documented that `/user` fails for them. https://support.atlassian.com/bitbucket-cloud/docs/using-access-tokens/

## 4. OpenAI

Note: platform.openai.com returned 403 to the fetcher. These answers come from developers.openai.com, the official docs mirror.

- **(a) Mint:**
  - **Project API keys: no create method.** Only list, retrieve and delete exist: `GET/DELETE /v1/organization/projects/{project_id}/api_keys[/{key_id}]`. https://developers.openai.com/api/reference/typescript/resources/admin/subresources/organization/subresources/projects
  - **Service accounts: yes.** `POST /v1/organization/projects/{project_id}/service_accounts` accepts `name`, `create_service_account_only` and `expires_in_seconds`. By default the response includes an unredacted `api_key.value`.
    - The endpoint and its params are listed at https://developers.openai.com/api/reference/typescript/resources/admin/subresources/organization/subresources/projects
    - The "unredacted key returned once" behaviour is described at https://help.openai.com/en/articles/9186755-managing-projects-in-the-api-platform and in the docs search snippet for the create-service-account reference page. A direct fetch of that page returned 404.
  - **Service-account API keys:** `POST /v1/organization/projects/{project_id}/service_accounts/{sa_id}/api_keys` takes `name` and a `scopes` array, and returns the value once.
    - https://developers.openai.com/api/docs/guides/terraform/service-accounts
  - **Admin keys: yes.** `POST /v1/organization/admin_api_keys` takes `name` and an optional `expires_in_seconds`. It is authorised by an existing admin key, and only Organization Owners can use admin keys. https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/admin_api_keys/methods/create
  - **Blast radius:** an admin key is organisation-wide.
- **(b) Revoke:** `DELETE` on the project API key, or `DELETE /v1/organization/projects/{project_id}/service_accounts/{id}`. An admin-key delete endpoint exists in the admin_api_keys resource (not individually fetched, unverified).
- **(c) Probe:** `GET /v1/models`, a free metadata call (inference). Inference: an invalid key gets 401 `invalid_api_key`. The error-codes guide sits on platform.openai.com, which returned 403, so this is unverified here.
- **(d) Expiry and narrowing:** service accounts and admin keys accept `expires_in_seconds`, and the service-account `api_key` carries `expires_at`. Keys are project-scoped, and service-account keys can take `scopes`.

## 5. Anthropic

- **(a) Mint: no.** Docs FAQ: "Can I create new API keys through the Admin API? No. You create API keys in the Claude Console. The Admin API can only read, rename, and change the status of existing keys." The Admin API is **unavailable for individual accounts**. Admin keys (`sk-ant-admin...`) are created by org admins; see https://platform.claude.com/docs/en/manage-claude/admin-api-keys . Main page: https://platform.claude.com/docs/en/manage-claude/admin-api
- **(b) Revoke: yes, by status.** `POST /v1/organizations/api_keys/{api_key_id}` with `status` set to `active`, `inactive` or `archived` (an `expired` status is read-only). Listing is `GET /v1/organizations/api_keys`. https://platform.claude.com/docs/en/api/admin-api/apikeys/update-api-key
- **(c) Probe:** `GET /v1/models` with `x-api-key`. Per the errors page, 401 `authentication_error` means the key is "malformed, revoked, or expired". 403 `permission_error` means a valid key that lacks access. Inference: listing models costs nothing.
  - https://platform.claude.com/docs/en/api/models-list
  - https://platform.claude.com/docs/en/api/errors
- **(d) Expiry and narrowing:** keys carry `expires_at` (null means no expiry; see the "Key expiration" section of the authentication docs). Keys are workspace-scoped via `scope.workspace_id`. Service accounts and federation exist but are managed only with an `org:admin` OAuth token. Inference: workload identity federation could give short-lived credentials; I did not research it (unverified).

## 6. Bitwarden Secrets Manager (machine-account access tokens)

- **(a) Mint: web app only.** The path is Machine Accounts > account > Access Tokens > Create. https://bitwarden.com/help/access-tokens/
  - The `bws` CLI has only `run`, `secret`, `project` and `config` commands, with no token or machine-account commands. https://bitwarden.com/help/secrets-manager-cli/
  - The Bitwarden Public API covers only members, collections, groups, events and policies. It does not cover Secrets Manager. https://bitwarden.com/help/public-api/
  - Inference: the SDK exposes no token minting either. I did not check the SDK source (unverified).
- **(b) Revoke: web app only.** A machine that has already authenticated "may continue to retrieve and decrypt secrets for up to one hour" after revocation. https://bitwarden.com/help/access-tokens/
- **(c) Probe:** `bws project list` lists the projects the machine account can access. The docs do not give the exit code or message for an invalid token (unverified). Per repo CLAUDE.md, use `--color no` and unset `FORCE_COLOR` when parsing the output.
- **(d) Expiry:** set at creation; the default is Never. Narrowing is by the machine account's project grants. Inference: this is the existing BWS model.

## 7. Coolify

- **Database password:** `PATCH /api/v1/databases/{uuid}` has a `postgres_password` field. https://coolify.io/docs/api-reference/api/operations/update-database-by-uuid
  - **Inference, not in the Coolify docs:** the official postgres image applies `POSTGRES_PASSWORD` only when it initialises an empty data dir. Changing the field likely does **not** change the password stored in an existing volume; an `ALTER USER` inside the DB is also needed. This matches the volume caveat in the repo CLAUDE.md. Verify before relying on it.
- **App env vars:** `PATCH /api/v1/applications/{uuid}/envs` takes `key`, `value`, `is_preview`, `is_literal`, `is_multiline` and `is_shown_once`. A bulk endpoint also exists. https://coolify.io/docs/api-reference/api/operations/update-env-by-application-uuid
- **Token scopes:**

  | Scope | What it allows |
  |---|---|
  | `read` | List and inspect; sensitive values are redacted |
  | `read:sensitive` | Secrets, logs, passwords, private keys, env values, compose |
  | `write` | Create, update and delete |
  | `deploy` | Deploy, restart, stop, cancel and webhooks |
  | `root` | Bypasses the `read`, `write` and `deploy` checks |

  Tokens are team-bound, and the elevated scopes require the owner to be a team admin or owner. "Endpoint access and sensitive-data access are separate checks." https://coolify.io/docs/api-reference/permissions
- **Token creation:** dashboard only, as far as the docs show; no API endpoint found (unverified). https://coolify.io/docs/api-reference/authorization

## 8. Cloudflare API tokens

- **(a) Mint: yes.**
  - User-owned: `POST /client/v4/user/tokens`. The calling token needs "API Tokens Write".
  - Account-owned: `POST /client/v4/accounts/{account_id}/tokens`. The calling token needs "Account API Tokens Write".
  - Policies are allow/deny lists over resources and permission groups. The secret is shown once.
  - Docs warning: when using the "Create additional tokens" template, do not grant it additional permissions. Inference: a token-writer token is effectively an escalation path.
  - https://developers.cloudflare.com/fundamentals/api/how-to/create-via-api/
- **Roll:** `PUT /user/tokens/{token_id}/value` returns the new secret in `result`. https://developers.cloudflare.com/api/resources/user/subresources/tokens/subresources/value/methods/update/
- **(b) Revoke:** `DELETE /user/tokens/{token_id}`. It needs "API Tokens Write" and returns 200 with `success: true`. https://developers.cloudflare.com/api/resources/user/subresources/tokens/methods/delete/
- **(c) Probe:** `GET /user/tokens/verify` returns `status` of active, disabled or expired. Behaviour for an invalid token is not detailed; expect `success:false` with errors (unverified). https://developers.cloudflare.com/api/resources/user/subresources/tokens/methods/verify/ . Account-owned tokens have their own verify path under `/accounts/{id}/tokens/verify` (inference, unverified).
- **(d) Expiry and narrowing:** `expires_on`, `not_before` and IP CIDR conditions; scoping is per resource and permission group.

## 9. Todoist

- **(a, b) The personal API token cannot be rotated via the API**, as far as I can tell (unverified). The revoke endpoints that exist are for **OAuth** tokens and need the app's client credentials:
  - `POST /api/v1/revoke` (RFC 7009, Basic auth with client credentials)
  - `DELETE /api/v1/access_tokens?client_id&client_secret&access_token`
  - https://developer.todoist.com/api/v1/
  - Inference: moving the consumer to an OAuth app token would make revocation API-able.
- **(c) Probe:** `GET /api/v1/user`. An invalid or expired token gets `401` with `WWW-Authenticate: Bearer error="invalid_token"`. Same URL.
- **(d) Expiry and narrowing:** no expiry or scoping is documented for the personal token (unverified).

## 10. Others (one line each)

- **Healthchecks.io:** read-write and read-only project API keys are created on the Project Settings page. No create or revoke API is documented. https://healthchecks.io/docs/api/
- **Resend:**
  - Mint: yes. `POST https://api.resend.com/api-keys` takes `name`, `permission` (`full_access` or `sending_access`) and `domain_id` (sending-only).
  - Revoke: `DELETE /api-keys/{id}`.
  - Inference: the calling key needs `full_access`.
  - https://resend.com/docs/api-reference/api-keys/create-api-key
  - https://resend.com/docs/api-reference/api-keys/delete-api-key
- **Hetzner Cloud:** tokens are created per project in the Console (Security > API tokens), as Read or Read & Write. No create or revoke API is documented. https://docs.hetzner.com/cloud/api/getting-started/generating-api-token/
- **Purelymail:** an OpenAPI spec exists at https://news.purelymail.com/api/index.html , but token mint and revoke are **unverified**.

## Summary: fully API-rotatable (mint, revoke and probe all via API)

| Provider | Credential | Minted by |
|---|---|---|
| OpenRouter | API keys | Management key |
| OpenAI | Service-account and admin keys | Admin key |
| Cloudflare | API tokens | Token with "API Tokens Write" |
| Resend | API keys | Full-access key |
| GitHub | App installation tokens | App private key (the private key itself is UI-only) |

**Revoke or deactivate via API only:**
- Anthropic: status set to inactive or archived.
- GitHub PATs: `POST /credentials/revoke`.

**UI-only for minting:** GitHub PATs, Anthropic keys, Bitwarden SM access tokens, Bitbucket API and access tokens, Coolify tokens, Hetzner, Healthchecks, Todoist personal token.

**Coolify:** can *write* rotated values (env vars, DB password field) via the API; the DB password caveat in section 7 applies.
