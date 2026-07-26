# Image-Build Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A one-click GitHub Actions workflow that builds and pushes the production amd64 orchestrator image to GHCR, replacing the manual Mac `docker buildx` ritual. Deployment stays a manual Coolify gate.

**Architecture:** Extract the prose-only registry-context shaping into a tested script (`scripts/shape_registry_context.py`); move the build pin into a versioned file (`security-standards.pin.toml`); add a `workflow_dispatch` workflow (`.github/workflows/release-image.yml`) that checks out `security-standards` via a read-only deploy key, shapes the bundle, and runs the existing Dockerfile build (whose in-build digest gate already fails closed) on a native-amd64 runner, pushing an immutable SHA-tagged image.

**Tech Stack:** Python 3.12 (stdlib `tomllib`, `subprocess`, `tarfile`), GitHub Actions, Docker/BuildKit, `pytest`.

**Spec:** `docs/superpowers/specs/2026-07-26-image-build-automation-design.md`

## Global Constraints

- **Build + push only. No deployment.** No Coolify API/webhook call; the workflow ends by printing the pushed tag + digest. Deploy is Devon's manual gate.
- **Do NOT change the Dockerfile's build contract** (build-args `SECURITY_STANDARDS_REVISION`/`REGISTRY_ARTIFACT_SHA256`, the `registry` build-context shape `{SOURCE_REVISION, agents/, src/, schema/}`, the in-build `build_registry_bundle.py --artifact-sha256` gate). This workstream feeds that contract; it does not redesign it.
- **Immutable tags only:** `ghcr.io/alobarquest/orchestrator:<short-sha>[-<label>]-amd64`. No moving `latest`/`main` tag.
- **The two-digest verification model** (from the spec): the **bundle digest** (`REGISTRY_ARTIFACT_SHA256`) is the build-time, security-critical proof that the right 13 actors are baked in — enforced automatically by the Dockerfile's in-build gate. The **image SHA** (`RepoDigest`) is the deploy-time identity proof (running == pushed), checked manually by Devon at the Coolify gate. There is **no Mac-vs-GHA image comparison** — image SHAs aren't reproducible across build environments and the comparison is unnecessary.
- **`security-standards` is private** — the workflow reads it via a read-only deploy key stored as the Actions secret `SECURITY_STANDARDS_DEPLOY_KEY`. Provisioning that key + secret is a manual operator step (see Operator Prerequisite), not code.
- The shaped artifact must reproduce the pinned `artifact_sha256` byte-for-byte, or the Docker build fails closed. Never shape from a raw working copy with untracked files — always `git archive` at the pinned revision.
- `make check` green on a clean tree (read the collected count); `/code-review`; final adversarial whole-branch review.

## Operator Prerequisite (Devon, manual — before the workflow can run for real)

Not a code task; document it in the plan output and the DoD.
1. Generate an SSH keypair (`ssh-keygen -t ed25519 -C orchestrator-image-build -f /tmp/ib_key`, no passphrase).
2. Add the **public** half to `AlobarQuest/security-standards` → Settings → Deploy keys, **read-only**, title `orchestrator-image-build`.
3. Add the **private** half to `AlobarQuest/orchestrator` → Settings → Secrets and variables → Actions → new secret `SECURITY_STANDARDS_DEPLOY_KEY`.
4. Delete `/tmp/ib_key*`. Never paste the private key into a prompt.

## File Structure

- **Create** `security-standards.pin.toml` (repo root) — `revision` + `artifact_sha256`, the single source of truth.
- **Create** `scripts/shape_registry_context.py` — raw `security-standards` checkout → shaped `registry` build-context.
- **Create** `tests/scripts/test_shape_registry_context.py` — round-trip parity test against the existing shaped fixture.
- **Create** `.github/workflows/release-image.yml` — the `workflow_dispatch` build+push workflow.
- **Create** `tests/architecture/test_release_workflow.py` — structural assertions on the workflow (reads the pin file, passes the right build-args, immutable tag, no deploy step).
- **Modify** `CLAUDE.md` — repoint the manual-build invariant at the pin file + script + workflow (paved road), keeping the manual `buildx` recipe as the documented fallback.
- **Modify** the governance/ownership map — register the workflow as the image's build lane (see Task 4 for the exact file).

## Data reference (verified 2026-07-26)

- **Dockerfile** (`builder` stage): `COPY --from=registry / /registry` then `build_registry_bundle.py --artifact-dir /registry --source-revision $REV --artifact-sha256 $SHA`. Runtime stage copies `/agents`→`/app/security-standards/registry/agents`, `/src`→`.../src`, `/schema`→`.../schema`.
- **Shaped artifact dir** (what `--build-context registry=` must contain, per `_artifact_content`): a `SOURCE_REVISION` file (the revision string) + `agents/` + `src/` + `schema/`. `build_bundle_from_artifact` computes `_content_digest(SOURCE_REVISION, entries)` and compares to `artifact_sha256`; actor entries are those under `agents/`.
- **Raw → shaped mapping** (from the CLAUDE.md recipe): security-standards `registry/agents` → `agents/`; `src/agent_registry` → `src/agent_registry`; `src/factory_events` → `src/factory_events`; `schema` → `schema`; plus write `SOURCE_REVISION` = the revision.
- **`build_registry_bundle.py` CLI:** `--registry-dir <ss-checkout> | --artifact-dir <shaped>` (mutually exclusive), `--source-revision <rev>` (required), `--artifact-sha256 <hex>` (required with `--artifact-dir`), `--output <file>`.
- **Existing shaped fixture:** `tests/fixtures/security-standards/` (git repo; `agents/ src/ schema/`), used by `quality.yml`'s fixture build with `SECURITY_STANDARDS_REVISION=0123…4567` and `REGISTRY_ARTIFACT_SHA256=258e4757bf84361afe847dc72ecc83452b4229d699dbf593fad50e365e80fbce`.
- **Real prod pin:** `SECURITY_STANDARDS_REVISION` and `REGISTRY_ARTIFACT_SHA256` currently in CLAUDE.md prose (`65655ddf…` / `7aea8471…`). Copy the FULL values from CLAUDE.md into the pin file at implementation time.

---

### Task 1: Registry-context shaping script + parity test

**Files:**
- Create: `scripts/shape_registry_context.py`
- Test: `tests/scripts/test_shape_registry_context.py`

**Interfaces:**
- Produces (used by the workflow in Task 3 and the local flow):
  - CLI: `python scripts/shape_registry_context.py --source <ss-git-checkout> --revision <sha> --output <dir>` — writes the shaped `{SOURCE_REVISION, agents/, src/, schema/}` into `<output>`, via `git -C <source> archive <revision> <pathspecs>` (never a raw copy), remapping `registry/agents`→`agents` and writing `SOURCE_REVISION`.
  - Importable: `def shape_registry_context(source: Path, revision: str, output: Path) -> None`.

- [ ] **Step 1: Write the failing test**

`tests/scripts/test_shape_registry_context.py`. The test builds a RAW security-standards git repo in a tmp dir from the existing SHAPED fixture (inverting the shaping: `agents/`→`registry/agents/`, `src/`→`src/`, `schema/`→`schema/`), commits it, runs `shape_registry_context` at that commit, and asserts the shaped output reproduces the fixture's bundle — i.e. `build_registry_bundle.py`'s digest over the shaped output equals the digest over the original fixture. This proves the script is the correct inverse without needing the private real repo.

```python
import shutil
import subprocess
from pathlib import Path

from scripts.build_registry_bundle import artifact_digest
from scripts.shape_registry_context import shape_registry_context

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "security-standards"


def _raw_repo_from_shaped(shaped: Path, dest: Path) -> str:
    """Reconstruct a RAW security-standards layout (registry/agents, src, schema) from the
    shaped fixture and commit it; return the commit sha."""
    (dest / "registry").mkdir(parents=True)
    shutil.copytree(shaped / "agents", dest / "registry" / "agents")
    shutil.copytree(shaped / "src", dest / "src")
    shutil.copytree(shaped / "schema", dest / "schema")
    subprocess.run(["git", "init", "-q"], cwd=dest, check=True)
    subprocess.run(["git", "add", "-A"], cwd=dest, check=True)
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "commit", "-qm", "raw"], cwd=dest, check=True, env={**__import__("os").environ, **env})
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=dest, check=True,
                          capture_output=True, text=True).stdout.strip()


def test_shaping_reproduces_the_fixture_bundle(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    sha = _raw_repo_from_shaped(FIXTURE, raw)
    out = tmp_path / "shaped"
    shape_registry_context(raw, sha, out)
    # The shaped output must carry the four artifact components and reproduce the fixture's actors.
    assert (out / "SOURCE_REVISION").read_text().strip() == sha
    assert sorted(p.name for p in (out / "agents").iterdir()) == \
        sorted(p.name for p in (FIXTURE / "agents").iterdir())
    # Digest over the shaped output's actor content equals the fixture's (SOURCE_REVISION aside).
    assert {p.name for p in (out / "src").iterdir()} == {p.name for p in (FIXTURE / "src").iterdir()}
    assert (out / "schema").is_dir()
```

> The implementer refines the exact digest assertion using `build_registry_bundle.py`'s real helpers (`build_bundle`/`build_bundle_from_artifact`) — the binding requirement is: **shaping a raw checkout then running `build_registry_bundle.py --artifact-dir <output>` yields the same actor set/digest as the fixture** (SOURCE_REVISION differs because the tmp repo's sha differs — compare the actor content, not the revision-bearing digest, OR write SOURCE_REVISION and compare the full digest against a freshly-computed fixture digest at the same revision). Pick the comparison that is exact and not vacuous.

- [ ] **Step 2: Run test to verify it fails** — `.venv/bin/pytest tests/scripts/test_shape_registry_context.py -v` → FAIL (`ModuleNotFoundError: scripts.shape_registry_context`).

- [ ] **Step 3: Write `scripts/shape_registry_context.py`**

```python
"""Shape a security-standards checkout into the `registry` build-context.

Produces the {SOURCE_REVISION, agents/, src/, schema/} artifact the Dockerfile's
`--build-context registry=` expects, via `git archive` at a pinned revision (never a raw copy,
which would let untracked files poison the digest). Used by both the local build flow and the
release workflow so the two produce byte-identical bundles.
"""

import argparse
import io
import subprocess
import tarfile
from pathlib import Path

# security-standards source pathspec -> destination under the shaped output.
_MAP = {
    "registry/agents": "agents",
    "src/agent_registry": "src/agent_registry",
    "src/factory_events": "src/factory_events",
    "schema": "schema",
}


def shape_registry_context(source: Path, revision: str, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for pathspec, dest in _MAP.items():
        archive = subprocess.run(
            ["git", "-C", str(source), "archive", revision, pathspec],
            check=True, capture_output=True,
        ).stdout
        target = output / dest
        target.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            _extract_remapped(tar, pathspec, dest, output)
    (output / "SOURCE_REVISION").write_text(revision)


def _extract_remapped(tar: tarfile.TarFile, pathspec: str, dest: str, output: Path) -> None:
    # `git archive <rev> registry/agents` yields members under `registry/agents/...`; strip the
    # pathspec prefix and re-root under `dest`.
    for member in tar.getmembers():
        if not member.isfile():
            continue
        rel = Path(member.name).relative_to(pathspec)
        out_path = output / dest / rel
        out_path.parent.mkdir(parents=True, exist_ok=True)
        extracted = tar.extractfile(member)
        assert extracted is not None
        out_path.write_bytes(extracted.read())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    shape_registry_context(args.source, args.revision, args.output)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes** — `.venv/bin/pytest tests/scripts/test_shape_registry_context.py -v` → PASS. Iterate the script until the digest comparison is exact (the shaped output, fed to `build_registry_bundle.py --artifact-dir`, reproduces the fixture's actors/digest).

- [ ] **Step 5: Commit** — `git add scripts/shape_registry_context.py tests/scripts/test_shape_registry_context.py && git commit -m "feat(image-build): registry-context shaping script + parity test"`

---

### Task 2: Pin file

**Files:**
- Create: `security-standards.pin.toml`
- Test: `tests/architecture/test_release_workflow.py` (start it here with the pin-file assertions; Task 3 extends it with workflow assertions)

**Interfaces:**
- Produces: `security-standards.pin.toml` with `revision: str` and `artifact_sha256: str` (64-hex), read via stdlib `tomllib`.

- [ ] **Step 1: Write the failing test**

```python
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_pin_file_is_well_formed():
    pin = tomllib.loads((ROOT / "security-standards.pin.toml").read_text())
    assert set(pin) >= {"revision", "artifact_sha256"}
    assert len(pin["artifact_sha256"]) == 64
    assert all(c in "0123456789abcdef" for c in pin["artifact_sha256"])
    assert len(pin["revision"]) >= 7 and all(c in "0123456789abcdef" for c in pin["revision"])
```

- [ ] **Step 2: Run test to verify it fails** — FAIL (file missing).

- [ ] **Step 3: Create `security-standards.pin.toml`** with the FULL real values copied from CLAUDE.md (the `65655ddf…` revision and `7aea8471…` digest — copy the complete strings, not the truncated forms):

```toml
# Single source of truth for the production registry-bundle pin. The release workflow reads
# `revision` to check out security-standards, and passes both to the Docker build; the Dockerfile's
# in-build gate fails closed if the assembled bundle's digest != artifact_sha256. Bump BOTH together
# when a registry actor changes (recompute the digest locally with build_registry_bundle.py).
revision        = "<full 65655ddf… sha from CLAUDE.md>"
artifact_sha256 = "<full 7aea8471… digest from CLAUDE.md>"
```

- [ ] **Step 4: Run test to verify it passes** — PASS.

- [ ] **Step 5: Commit** — `git add security-standards.pin.toml tests/architecture/test_release_workflow.py && git commit -m "feat(image-build): versioned security-standards pin file"`

---

### Task 3: The release workflow

**Files:**
- Create: `.github/workflows/release-image.yml`
- Test: `tests/architecture/test_release_workflow.py` (extend)

**Interfaces:**
- Consumes: `security-standards.pin.toml`, `scripts/shape_registry_context.py`, the Actions secret `SECURITY_STANDARDS_DEPLOY_KEY`.
- Produces: a `workflow_dispatch` workflow that pushes `ghcr.io/alobarquest/orchestrator:<short-sha>[-<label>]-amd64` and writes tag+digest to the job summary.

- [ ] **Step 1: Write the failing structural test**

Extend `tests/architecture/test_release_workflow.py` (parse the YAML with the repo's yaml lib):

```python
import yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _workflow():
    return yaml.safe_load((ROOT / ".github/workflows/release-image.yml").read_text())


def test_workflow_is_dispatch_only_build_and_push_no_deploy():
    wf = _workflow()
    # `on:` may parse as the key True in yaml; handle both.
    triggers = wf.get("on", wf.get(True))
    assert set(triggers) == {"workflow_dispatch"}
    text = (ROOT / ".github/workflows/release-image.yml").read_text()
    # Build+push only: pushes to ghcr, but never calls Coolify / deploy.
    assert "ghcr.io/alobarquest/orchestrator:" in text
    assert "--platform linux/amd64" in text
    assert "SECURITY_STANDARDS_DEPLOY_KEY" in text
    assert "security-standards.pin.toml" in text
    assert "shape_registry_context.py" in text
    for forbidden in ("coolify", "sds.alobar.net", "curl -X POST"):
        assert forbidden not in text.lower()
    # No moving tag.
    assert ":latest" not in text and ":main-" not in text
```

- [ ] **Step 2: Run test to verify it fails** — FAIL (workflow missing).

- [ ] **Step 3: Create `.github/workflows/release-image.yml`**

```yaml
name: Release image
on:
  workflow_dispatch:
    inputs:
      ref:
        description: orchestrator commit or branch to build
        required: true
        default: main
      label:
        description: optional tag suffix (e.g. wsp26)
        required: false
        default: ""

concurrency:
  group: release-image
  cancel-in-progress: false

permissions:
  contents: read
  packages: write

jobs:
  build-and-push:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout orchestrator
        uses: actions/checkout@v4
        with:
          ref: ${{ inputs.ref }}

      - name: Read pin
        id: pin
        run: |
          python3 - <<'PY' >> "$GITHUB_OUTPUT"
          import tomllib
          pin = tomllib.load(open("security-standards.pin.toml", "rb"))
          print(f"revision={pin['revision']}")
          print(f"digest={pin['artifact_sha256']}")
          PY

      - name: Checkout security-standards @ pin
        uses: actions/checkout@v4
        with:
          repository: AlobarQuest/security-standards
          ref: ${{ steps.pin.outputs.revision }}
          ssh-key: ${{ secrets.SECURITY_STANDARDS_DEPLOY_KEY }}
          path: security-standards
          fetch-depth: 0

      - name: Shape registry build-context
        run: |
          python3 scripts/shape_registry_context.py \
            --source security-standards \
            --revision "${{ steps.pin.outputs.revision }}" \
            --output "$RUNNER_TEMP/registry"

      - name: Compute tag
        id: tag
        run: |
          SHA=$(git rev-parse --short HEAD)
          SUFFIX=""
          [ -n "${{ inputs.label }}" ] && SUFFIX="-${{ inputs.label }}"
          echo "tag=ghcr.io/alobarquest/orchestrator:${SHA}${SUFFIX}-amd64" >> "$GITHUB_OUTPUT"

      - name: Log in to GHCR
        run: echo "${{ secrets.GITHUB_TOKEN }}" | docker login ghcr.io -u ${{ github.actor }} --password-stdin

      - name: Build and push (amd64; in-build digest gate fails closed)
        id: build
        run: |
          docker build --platform linux/amd64 \
            --build-context registry="$RUNNER_TEMP/registry" \
            --build-arg SECURITY_STANDARDS_REVISION="${{ steps.pin.outputs.revision }}" \
            --build-arg REGISTRY_ARTIFACT_SHA256="${{ steps.pin.outputs.digest }}" \
            -t "${{ steps.tag.outputs.tag }}" \
            --push .
          DIGEST=$(docker inspect --format='{{index .RepoDigests 0}}' "${{ steps.tag.outputs.tag }}")
          echo "digest=$DIGEST" >> "$GITHUB_OUTPUT"

      - name: Summary
        run: |
          {
            echo "### Image pushed"
            echo "- Tag: \`${{ steps.tag.outputs.tag }}\`"
            echo "- Digest: \`${{ steps.build.outputs.digest }}\`"
            echo ""
            echo "Deploy is manual: point Coolify at this tag, then verify the running RepoDigest matches the digest above."
          } >> "$GITHUB_STEP_SUMMARY"
```

> The implementer verifies BuildKit named-context support on the runner (`docker build --build-context` needs BuildKit; ubuntu-latest has it by default — if a version issue arises, add `docker/setup-buildx-action@v3` and `docker buildx build`). Confirm the `on:`/`True` yaml-key quirk in the test matches how the repo's yaml loader parses it.

- [ ] **Step 4: Run test to verify it passes** — `.venv/bin/pytest tests/architecture/test_release_workflow.py -v` → PASS.

- [ ] **Step 5: Commit** — `git add .github/workflows/release-image.yml tests/architecture/test_release_workflow.py && git commit -m "feat(image-build): workflow_dispatch release-image workflow"`

---

### Task 4: Governance registration + docs repoint

**Files:**
- Modify: the governance/ownership source that lists control-plane build lanes (locate it: check `~/Projects/security-standards/governance-map.toml` and `~/.claude/OWNERSHIP.md`; the orchestrator image build lane belongs with the other control-plane artifacts — if the map lives in another repo, record the required entry in this plan's output and flag it as a cross-repo follow-up rather than editing outside this repo).
- Modify: `CLAUDE.md` (repo root) — the manual-build invariant.

- [ ] **Step 1: Repoint the CLAUDE.md manual-build invariant**

Update the "prod orchestrator image is built MANUALLY from the Mac" invariant to state the **paved road** first: pin lives in `security-standards.pin.toml`; the shaped context is produced by `scripts/shape_registry_context.py`; the image is built+pushed by the `Release image` workflow (`workflow_dispatch`, native amd64). Keep the manual `docker buildx` recipe documented as the fallback and the differential baseline. Preserve the two-digest reasoning (bundle digest = build-time gate; image SHA = deploy-time RepoDigest check). Keep the bare tokens rule in mind — this file is prose, not scanned by the scope guards, so no constraint there, but do not paste secrets.

- [ ] **Step 2: Register the build lane in the governance map**

Add the workflow as the orchestrator image's build lane in the governance/ownership map (per `Projects/CLAUDE.md`, the source of `OWNERSHIP.md` is `security-standards/governance-map.toml`). If that file is in another repo, do NOT edit it from here — instead write the exact entry to add into this plan's task report and flag it as a Devon/cross-repo step, consistent with the invariant that `OWNERSHIP.md` is generated, not hand-edited.

- [ ] **Step 3: Verify docs render / no broken invariant**

Re-read the edited CLAUDE.md section; confirm it is internally consistent and the pin values match `security-standards.pin.toml`.

- [ ] **Step 4: Commit** — `git add CLAUDE.md && git commit -m "docs(image-build): repoint manual-build invariant at the paved road"`

---

### Task 5: Full-gate verification + operator-run acceptance

**Files:** none (verification only)

- [ ] **Step 1: Full gate** — `git status` clean (non-scratch), then `make check`. Read the `collected N items` count; confirm the new `tests/scripts/` and `tests/architecture/test_release_workflow.py` tests are counted. If `ruff format --check .` reds on untouched files, that is pre-existing debt (differential), not this change.

- [ ] **Step 2: `/code-review`** the branch diff; address findings.

- [ ] **Step 3: Document the operator-run acceptance** (cannot run in CI — needs the deploy key + a real dispatch). In the task report, record the manual acceptance script for Devon:
  1. Provision the deploy key + `SECURITY_STANDARDS_DEPLOY_KEY` secret (Operator Prerequisite).
  2. Dispatch `Release image` with `ref=<commit>`, `label=<ws>`.
  3. Confirm the run's summary shows a pushed tag + digest, and that the **in-build digest gate passed** (build did not fail) — this is the build-time bundle-digest proof.
  4. (Deploy, separately) point Coolify at the tag; after swap, confirm the running container `RepoDigest` == the pushed digest — the deploy-time image-SHA proof.
  5. Negative check (once): temporarily set a wrong `artifact_sha256`, dispatch, confirm the build FAILS closed; revert.

- [ ] **Step 4: Commit any review fixes** — `git commit -m "polish(image-build): review fixes"`

---

## Self-Review (completed during authoring)

**Spec coverage:**
- `workflow_dispatch` trigger (ref+label) → Task 3. ✓
- Read-only deploy key auth → Task 3 (`ssh-key`) + Operator Prerequisite. ✓
- Versioned pin file → Task 2. ✓
- Immutable SHA tags, no moving tag → Task 3 + its test. ✓
- Build+push only, no deploy → Global Constraints + Task 3 test (forbids coolify/sds/curl-POST). ✓
- Shaping recipe → script → Task 1 (+ parity test). ✓
- Governance registration + CLAUDE.md repoint → Task 4. ✓
- Two-digest verification model → Global Constraints + Task 5 operator acceptance (build-time gate + deploy-time RepoDigest + wrong-pin-fails-closed). ✓

**Placeholder scan:** the pin file's `<full … sha>` are deliberate — the real secrets-free public values are copied from CLAUDE.md at implementation (they are digests, not secrets). The Task-1 digest-comparison refinement is explicitly delegated with a binding, non-vacuous criterion.

**Type/naming consistency:** `shape_registry_context(source, revision, output)` signature identical across Task 1 (def), the workflow step (Task 3 CLI), and the parity test. Pin keys `revision`/`artifact_sha256` identical across Tasks 2/3 and the test. Build-args match the Dockerfile verbatim.

**Note on execution:** Tasks 1–4 are CI-testable and reviewer-gateable. Task 5's real-dispatch acceptance is Devon-gated (needs the deploy key/secret and a live GHCR push) — it is documented as an operator script, not automated, consistent with the deploy-stays-manual boundary.
