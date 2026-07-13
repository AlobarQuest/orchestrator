# Runtime observations

A production-drill run must bind an immutable runtime observation before a HUMAN can start it.
The observation proves the runtime that answered the live OpenAPI request; it is not a caller
attestation of an image tag.

## Current prerequisite

The repository intentionally does not yet provide the constrained read-only observer capability
needed to collect the running container facts. Do not replace it with SSH as root, Docker-socket
access, an environment-provided executable, or a generic Coolify command. Each would let the
production-drill path become an infrastructure-control path.

Provision a dedicated observer capability before invoking the adapter. It must have all of these
properties:

- It is permanently bound to Coolify application `eqj5l7k705fhi12x9i74fqf0` and cannot accept an
  application ID, host, container selector, shell expression, or command from the caller.
- It has read-only access sufficient to return the running container ID, the configured image
  reference, and the container's first Docker `RepoDigest`. It cannot restart, deploy, delete,
  attach to, execute in, or otherwise mutate the application or host.
- It returns a bounded JSON document with exactly `container_id`, `configured_image_ref`,
  `observed_image_digest`, and `observed_at`. The reference must be
  `ghcr.io/alobarquest/orchestrator:<tag>` and the digest must be
  `ghcr.io/alobarquest/orchestrator@sha256:<64 lowercase hex characters>`.
- It authenticates as a dedicated observer identity. Its credential and the dedicated orchestrator
  M2M observer credential are stored in BWS by stable UUID; bearer material is neither command-line
  input nor evidence output.
- It records its own read audit with the fixed application ID, observer identity, and timestamp.

Once that capability is provisioned, the adapter has a fixed, narrow protocol:

1. Retrieve the observer credentials by stable BWS UUID.
2. Ask the fixed observer capability for its bounded runtime document.
3. Fetch raw `https://sds.alobar.net/openapi.json` bytes and calculate their SHA-256 digest without
   JSON reserialization.
4. POST only the fixed observation fields, an idempotency key, and `expected_version: 0` to
   `POST /api/v1/runtime-observations`, using the dedicated runtime-observer M2M credential and
   `X-Credential-Key-Id` header.
5. Give the resulting observation UUID to the HUMAN for `POST /api/v1/production-drills`.

The target URL and Coolify application ID are service constants. They are not adapter inputs.
