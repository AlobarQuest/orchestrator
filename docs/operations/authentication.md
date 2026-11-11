# Authentication operations

The image embeds a generated actor registry bundle whose `source_revision` exactly
matches the `SECURITY_STANDARDS_REVISION` build argument and whose deterministic artifact
digest exactly matches the separately pinned `REGISTRY_ARTIFACT_SHA256`. The bundle contains actor
identity and authority-profile references only; it contains no credential, token, or
password.

The digest is SHA-256 over length-framed path and byte-content pairs for
`SOURCE_REVISION` followed by sorted `agents/*.yaml` files. Compute it with the same
fail-closed builder used by the image:

```bash
uv run python -c 'from pathlib import Path; from scripts.build_registry_bundle import artifact_digest; print(artifact_digest(Path("REGISTRY_ARTIFACT_DIR")))'
```

Revision and digest are independent pins: changing actor content without changing the
recorded revision still fails the build.

Human review requests are accepted only from the configured trusted Alobar ID proxy
boundary. Machine requests require a bearer credential and its credential-key ID.
Runtime mappings and the CSRF signing secret are injected by the runtime configuration;
they are never baked into the image or committed to this repository.

When the embedded `ORCHESTRATOR_REGISTRY_BUNDLE` path is enabled, startup requires:

- `ORCHESTRATOR_M2M_CREDENTIALS`: JSON credential-key mappings with actor IDs and
  SHA-256 token hashes.
- `ORCHESTRATOR_TRUSTED_PROXY_IPS`: a JSON list of direct trusted proxy peers. In production
  that's `coolify-proxy`'s address on the `coolify` network, which Docker assigns, so it can
  change when the proxy is re-created or the host moves. When it no longer matches, every
  `/review` page answers `{"error":{"code":"authentication_failed",...}}` after a successful
  Alobar ID sign-in, and machine bearers keep working. The address can also pass to another
  container; the proxy marker is then the only check left. To fix it, read the address with
  `docker network inspect coolify`, set the variable, and restart the application. Found
  2026-10-26: the move to OVH on 2026-10-23 changed it from `10.0.1.26` to `10.0.1.6`.
- `ORCHESTRATOR_PROXY_MARKER`: the trusted proxy marker supplied at runtime.
- `ORCHESTRATOR_EMAIL_TO_ACTOR`: JSON normalized-email to actor-ID mappings.
- `ORCHESTRATOR_CSRF_SECRET`: at least 32 bytes of runtime secret material.

Four optional settings:

- `ORCHESTRATOR_ROTATION_EXECUTOR_AGENT_ID` names the credential-rotation executor's VM instance
  (ADR-0055). Only that identity may claim a rotation unit whose adapter is not a Keychain adapter,
  and it may claim nothing else. Unset refuses every claim on those units.
- `ORCHESTRATOR_ROTATION_KEYCHAIN_EXECUTOR_AGENT_ID` names the Keychain instance on the operator
  machine (ADR-0055 amendment 12). Only that identity may claim a unit of a Keychain adapter, and
  it may claim nothing else. Unset refuses every claim on those units.
- `ORCHESTRATOR_WORK_CARRIER_AGENT_ID` names the work carrier's observer identity (ADR-0055
  amendment 22 item 3). The decomposition gate admits a policy-approved lane credential's
  revision 1 only beside a standing-approval record `recorded_by` that identity. Unset refuses
  every standing approval.
- `ORCHESTRATOR_ALERT_HUB_TOKEN` is the `sds-orchestrator` sender's token for alert-hub. With it
  set, an unhandled exception raises one keyed alert (`api/server_errors.py`). Unset, the 500 is
  logged and no alert is sent. The setting is a held copy; see "Held copies" in
  `docs/operations/rotation-executor.md`.

Boot refuses a credential whose identity holds `rotation-executor-v1` or
`rotation-executor-keychain-v1` unless the setting for that profile names that identity. It also
refuses a configured executor that does not hold its own profile. The failure is the generic
`invalid runtime authentication configuration`.

The work carrier's check is stricter (`_require_work_carrier`): the identities whose credentials
hold `work-carrier-observer-v1` must be exactly the one `ORCHESTRATOR_WORK_CARRIER_AGENT_ID`
names, or none when it's unset. So boot refuses a credential holding that profile without the
setting, a setting that names any other identity, a second identity holding the profile, and a
setting with no credential. Boot also refuses any credential whose identity the registry bundle
marks `retired` or `reserved`, with the same generic error.

Because either half alone refuses boot, add the carrier's identity with one restart. This is the
one exception to "verify each variable in the container before the next restart"
(`credentials.md` #20): no restart between the credential and the setting can boot.

1. Add the `work-carrier-observer` entry (agent id `work-carrier`) to
   `ORCHESTRATOR_M2M_CREDENTIALS`, both rows (`is_preview` false and true).
2. Add `"work-carrier-observer": "observer"` to `ORCHESTRATOR_M2M_ROLES`, both rows.
3. Set `ORCHESTRATOR_WORK_CARRIER_AGENT_ID=work-carrier`, both rows.
4. Read all three variables back from Coolify, both rows, parsed in-process with only key ids,
   agent ids and roles printed. This is the only check before the restart.
5. Restart, confirm it boots, and verify all three variables in the container.

Don't restart between these steps. The rotation executor rewrites `ORCHESTRATOR_M2M_CREDENTIALS`
from what the running process loaded, so do the four steps with no rotation pending. To remove
the identity, remove all three in one restart the same way.

The carrier presents this identity from `scripts/run-work-carrier.sh`, which reads its bearer
from BWS (`work-carrier-observer-bearer`, `a9843d17-…`) on a `--register` pass only. For the
standing-approval observation, see `docs/operations/rotation-proposer.md`.

Optional `ORCHESTRATOR_M2M_ROLES` JSON assigns non-worker machine roles. Missing or
malformed required authentication configuration aborts process startup without logging
the supplied values. Local Compose disables bundle loading and remains fail-closed for
all authenticated routes; its liveness and migration exercises require no credentials.

When runtime secrets are provisioned, follow the portfolio BWS standard:

- Fetch by stable UUID at runtime.
- Source `BWS_ACCESS_TOKEN` from the approved Keychain helper or a gitignored file.
- Add consumed UUIDs to `.bws-secrets.toml` in the same approved change.
- Never log, render, or place secret values in command arguments or tracked files.

No BWS secret is consumed by the current local fixture configuration.

Production is deployed at `https://sds.alobar.net` behind Alobar ID forward-auth
for the human review surface and M2M bearer credentials for API callers. The
production human surface uses the Authentik application `Orchestrator`, slug
`orchestrator`, provider mode `forward_single`, external host
`https://sds.alobar.net`, and the `authentik Embedded Outpost`.

The WS-4.1 runner-facing durable credential uses credential key ID
`factory-runner-github` and BWS secret UUID
`d2a4c0fc-128b-4bf5-8e25-b481010e1be0`. Production stores only the credential
hash in `ORCHESTRATOR_M2M_CREDENTIALS`; GitHub Actions receives the raw bearer
token through repository secrets. Runner clients must send both
`X-Credential-Key-Id` and `Authorization: Bearer <token>` for authenticated API
calls.
