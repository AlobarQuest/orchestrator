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
- `ORCHESTRATOR_TRUSTED_PROXY_IPS`: a JSON list of direct trusted proxy peers.
- `ORCHESTRATOR_PROXY_MARKER`: the trusted proxy marker supplied at runtime.
- `ORCHESTRATOR_EMAIL_TO_ACTOR`: JSON normalized-email to actor-ID mappings.
- `ORCHESTRATOR_CSRF_SECRET`: at least 32 bytes of runtime secret material.

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
