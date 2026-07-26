# Image-Build Automation — Design

**Date:** 2026-07-26
**Owning repo:** `AlobarQuest/orchestrator`
**Status:** design approved; ready for implementation plan
**Depends on:** cross-repo read access to `AlobarQuest/security-standards` (private)

## Summary

Replace the manual, error-prone Mac `docker buildx` ritual for the production orchestrator
(`sds.alobar.net`) image with a **one-click GitHub Actions workflow** that builds and pushes the
amd64 image to GHCR. The workflow is **build + push only** — **deployment stays a manual Coolify
gate**, exactly as today. The tedious, mistake-prone parts (Apple-Silicon cross-compile, the
cross-repo `security-standards` bundle assembly, the byte-identical digest pin) move into a native
amd64 runner + a versioned pin file + a scripted bundle shaper.

Most of the machinery already exists: the Dockerfile's registry-build-context contract,
`scripts/build_registry_bundle.py` (bundle validation + digest), and a working (fixture-registry)
image build in `quality.yml`. The net-new is a pin file, a cross-repo read credential, a shaping
script, and a ~50-line workflow.

## Why this is safe to automate now

- **The digest gate is already fail-closed *inside* the Docker build.** The builder stage runs
  `build_registry_bundle.py --artifact-sha256 ${REGISTRY_ARTIFACT_SHA256}`, which fails the build
  on any bundle mismatch. Feeding the wrong pin breaks the build — no separate assertion needed.
- **amd64 is native on GitHub-hosted runners**, so the whole `docker buildx --platform linux/amd64`
  cross-build from the Mac becomes a plain native `docker build --push`.

## Decisions (locked)

1. **Trigger: `workflow_dispatch`** with inputs `ref` (commit/branch to build) and optional `label`
   (the `-<ws>` tag suffix). An image is produced only when deliberately requested — matches the
   "never auto-anything" posture. No `push`/`tag` trigger.
2. **Cross-repo auth: a read-only deploy key** on `security-standards`; its private half is stored
   as an orchestrator GitHub Actions secret (`SECURITY_STANDARDS_DEPLOY_KEY`). Repo-scoped,
   read-only, no user-account coupling, no token-expiry churn. **Provisioning the deploy key +
   Actions secret is a manual operator step (Devon), not automatable from here — it handles a
   private key.**
3. **Pin source-of-truth: a versioned file** `security-standards.pin.toml` at repo root holding
   `revision` and `artifact_sha256`. Replaces the CLAUDE.md prose pin as the single source both the
   local flow and the workflow read.
4. **Tag policy: immutable only** — `ghcr.io/alobarquest/orchestrator:<short-sha>[-<label>]-amd64`,
   matching the current convention. **No moving `latest`/`main` tag** (Coolify targets a specific
   pinned tag per deploy; a moving tag invites accidental auto-pull).
5. **Build + push only; deploy stays manual.** The workflow ends by writing the pushed **tag +
   digest** to the job summary. **No Coolify API/webhook call.** Devon does the Coolify swap and the
   running-container RepoDigest verification.
6. **The shaping step becomes a script.** The `git archive` assembly of the `registry`
   build-context (today CLAUDE.md prose only) is extracted into `scripts/shape_registry_context`
   that both the local Mac flow and the workflow invoke.

## Non-goals

- **No automated deployment.** No Coolify API/webhook, no auto-swap, no health re-check. The
  build's blast radius stops at GHCR.
- **No moving tags**, no registry cleanup/retention automation.
- **No pin-computation workflow.** Updating the pin when a `security-standards` actor changes is a
  rare manual step (bump `revision`, recompute the digest locally via
  `build_registry_bundle.py`, update the file, commit) — not worth its own workflow (YAGNI).
- **No change to the Dockerfile's build contract** — the build-args, registry-context shape, and
  in-build digest gate stay exactly as they are. This workstream feeds them, it does not redesign
  them.
- **No removal of the manual Mac path** — it remains a valid fallback (and the differential
  verification baseline). The pin file + shaping script make the manual path easier too.

## Current state (verified 2026-07-26)

| Piece | Status |
|---|---|
| Dockerfile registry-build-context contract (`--build-context registry=…`, build-args, in-build digest gate) | exists, unchanged |
| `scripts/build_registry_bundle.py` (validate bundle + `artifact_digest()`) | exists; operates on a checked-out/shaped dir |
| Image build in CI (`quality.yml` "Build fixture-registry image") | exists — but uses the **fixture** registry (`tests/fixtures/security-standards`, fake revision/digest); a build smoke test, not a real push |
| Real pin (`revision=65655dd…`, `artifact_sha256=7aea8471…`) | **prose only in CLAUDE.md** — no versioned config |
| `registry` build-context shaping (`git archive registry/agents, src/agent_registry, src/factory_events, schema → /agents /src /schema`) | **prose only in CLAUDE.md** — no script |
| `security-standards` repo | **private** — needs a read credential in CI |
| `tests/architecture/test_container.py` | asserts the container/registry shape — a guardrail for the shaping script |

## Components

### 1. `security-standards.pin.toml` (new, repo root)
```toml
# The security-standards git revision the registry bundle is built from, and the byte-identical
# artifact digest that revision must produce. The Dockerfile's in-build gate fails closed if the
# assembled bundle does not match artifact_sha256. Bump both together when a registry actor changes.
revision        = "65655dd…"
artifact_sha256 = "7aea8471…"
```
Single source of truth. Both `scripts/shape_registry_context` (local) and the workflow read it.

### 2. `scripts/shape_registry_context` (new)
Extracts the CLAUDE.md prose recipe into a script. Given a `security-standards` checkout (or ref)
and an output dir, produces the shaped `registry` build-context (the `/agents /src /schema` layout)
via `git archive` of `registry/agents`, `src/agent_registry`, `src/factory_events`, `schema` — never
a raw checkout (untracked files would poison the digest). Idempotent; safe to re-run. Used by both
the local Mac flow and the workflow. Its output, fed to `build_registry_bundle.py --artifact-dir`,
must reproduce the pinned `artifact_sha256`.

### 3. `.github/workflows/release-image.yml` (new)
```
name: Release image
on:
  workflow_dispatch:
    inputs:
      ref:   { description: commit/branch to build, required: true, default: main }
      label: { description: optional -<label> tag suffix, required: false }
jobs:
  build-and-push:   # ubuntu-latest, native amd64
    steps:
      1. checkout orchestrator @ inputs.ref
      2. read security-standards.pin.toml → REV, SHA
      3. checkout security-standards @ REV   (ssh-key: SECURITY_STANDARDS_DEPLOY_KEY)
      4. scripts/shape_registry_context <ss-checkout> <artifact-dir>
      5. docker login ghcr.io   (GITHUB_TOKEN, packages: write)
      6. docker build --platform linux/amd64
           --build-context registry=<artifact-dir>
           --build-arg SECURITY_STANDARDS_REVISION=$REV
           --build-arg REGISTRY_ARTIFACT_SHA256=$SHA
           -t ghcr.io/alobarquest/orchestrator:<short-sha>[-<label>]-amd64
           --push .
         # in-build digest gate fails closed on any bundle mismatch
      7. write pushed tag + digest to $GITHUB_STEP_SUMMARY (and step output)
```
Permissions: `contents: read`, `packages: write`. Concurrency guard so two dispatches don't race a tag.

### 4. Governance + docs
- Register the workflow as the orchestrator image's **build lane** in `OWNERSHIP.md` /
  `governance-map.toml` (the image is a control-plane/hosted artifact).
- Update the CLAUDE.md manual-build invariant to point at the pin file + shaping script + workflow
  as the paved road, keeping the manual `buildx` recipe documented as the fallback.

## Verification / acceptance

**Two digests attest two different links in the chain — both are used, at different steps. They are
not competing candidates for one check.**

- **Bundle digest (`REGISTRY_ARTIFACT_SHA256`)** — a content hash over the `security-standards`
  revision + the 13 actor identities. Attests: *the security-critical actor registry baked into the
  image is byte-identical to the reviewed, pinned one* (no extra/tampered actor, no
  authority-profile drift). This is the **build-time, security-critical** proof, and the Dockerfile
  already recomputes it during the build and **fails closed** on mismatch.
- **Image SHA (GHCR manifest / running `RepoDigest`)** — a hash of the whole image. Attests: *prod
  is running bit-for-bit the artifact the workflow pushed* (no substitution between push and
  deploy). This is the **deploy-time identity** proof.

Docker image SHAs are **not** reproducible across build environments (Mac vs GHA differ from
identical source), so there is **no Mac-vs-GHA image comparison** — it would fail spuriously and is
unnecessary. The bundle digest deliberately decouples "is the security-critical content correct"
from "are the image bytes reproducible", so an automated build is trustworthy *without* reproducible
images and *without* a human eyeballing the actor list.

**Build-time acceptance (this workstream, automatic + tested):**
- **In-build bundle-digest gate passes** against the pinned `artifact_sha256` on a real workflow run
  — the automatic correctness proof that the right registry is baked in.
- **Wrong-pin fails closed:** a deliberately wrong `artifact_sha256` breaks the build — proves the
  gate is load-bearing in CI, not just locally.
- **Shaping-script parity:** `build_registry_bundle.py --artifact-dir $(shape_registry_context …)`
  reproduces the pinned `artifact_sha256` — asserted in a test, reusing `test_container.py` shape
  expectations where possible.
- **App-code provenance:** the image is built from a specific orchestrator git commit (`inputs.ref`)
  with `uv sync --frozen` — app/deps integrity is rooted in git + the lockfile, not in image-byte
  comparison.

**Deploy-time acceptance (Devon's manual gate, unchanged):**
- After the Coolify swap, the running container's **`RepoDigest` == the digest the workflow pushed**
  — the existing invariant, using the image SHA. Ideally the WS-P2.6 deploy is the first exercise of
  the automated path.

## Definition of done

- `security-standards.pin.toml`, `scripts/shape_registry_context`, and
  `.github/workflows/release-image.yml` exist; the pin prose in CLAUDE.md is replaced by a pointer
  to the file + workflow (manual recipe retained as fallback).
- Deploy-key + Actions secret provisioned (Devon, manual) and documented.
- The workflow builds + pushes a real amd64 image whose in-build digest gate passes against the
  pinned digest; shaping-script parity and wrong-pin-fails-closed are tested.
- Workflow registered in the governance/ownership map.
- `make check` green; `/code-review`; Devon merges.
- Build automation stays **build+push only** — deployment remains the manual Coolify gate.
