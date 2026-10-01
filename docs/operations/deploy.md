# Deploying the orchestrator

How to build, migrate, swap and verify a production release, and what each check can and cannot see.

The entries below moved verbatim from the CLAUDE.md invariants section on 2026-10-01 (Tier 3 item 17). Each keeps its original number. Their dated corrections are the record of what was measured when; read the last correction in an entry as its current state.

### #9

- Production Coolify images must be amd64 or multi-arch. Local Apple Silicon
  Docker builds produce arm64 images by default; use `docker buildx build
  --platform linux/amd64 --push` or a multi-arch build for `sds.alobar.net`, and
  verify the running container image/digest after Coolify reports deployment
  finished.

### #19

- **`sds.alobar.net` is NOT Cloudflare-proxied** — it answers `server: uvicorn`
  behind Traefik directly. The portfolio-wide invariant that Cloudflare 403s
  default Python User-Agents with `error code: 1010` does **not** apply here;
  factory-runner's `httpx` default UA authenticates fine (verified from a
  GitHub-hosted runner, 2026-07-09). Do not misdiagnose a failure here as that.

### #37

- **MERGED IS NOT DEPLOYED. Ask production what it is running before you reason about
  what it can do.** On 2026-07-12 production was serving
  `ghcr.io/alobarquest/orchestrator:d6d73b3-ws64-verifier-amd64` — a WS-6.4-era image —
  while WS-P2.1 (PR #47) and WS-P2.15 (PR #50) had been merged to `main` for days.
  `recover-evidence`, `dead-letter`, `requeue`, `reconciliation/detect`,
  `consistency-check` and **`pr-binding`** were all **absent from production** and
  returned 404, though every one of them exists in `main`. Program exit criterion #7
  ("operator status and recovery controls exist") was marked **MET**, citing five routes
  **none of which production served**; the five recovery drills that marked criterion #5
  MET run against a **local** orchestrator and had never touched production. WS-P2.16's
  entire subject — the `pr-binding` route — was undeployed, so a perfectly correct
  worker call would have 404'd; **six adversarial reviews of the WS-P2.16 plan missed
  this, because every one of them read the repository instead of asking production.**
  The check is one command and it is not optional:
  `curl -s https://sds.alobar.net/openapi.json | python3 -c "import sys,json; print(sorted(json.load(sys.stdin)['paths']))"`.
  A green suite on `main` says nothing about the machine that serves traffic.

### #52

- **The prod orchestrator image build is PAVED-ROAD-automated (image-build-automation
  workstream); Coolify only ever pulls a prebuilt GHCR tag and does not build.** The paved
  road: `security-standards.pin.toml` (repo root) is the **single source of truth** for the
  pinned `revision`/`artifact_sha256` — prose here is context, not authority; read the file.
  `scripts/shape_registry_context.py` turns a security-standards checkout at that revision
  into the shaped `{agents/, src/, schema/, SOURCE_REVISION}` build-context via `git archive`
  (never a raw checkout — untracked files would poison the digest); it writes `SOURCE_REVISION`
  **with a trailing newline** — that byte is part of the digest contract, and it's a real
  footgun the old manual recipe left implicit (the fixture convention and
  `build_registry_bundle.py` both hash the file verbatim, newline included). The `Release
  image` GitHub Actions workflow (`.github/workflows/release-image.yml`,
  `workflow_dispatch`, native `linux/amd64`) reads the pin, shapes the context, and runs
  `docker buildx build --push` to **two** tags (WS-P2.18 Inc 7): the derivable
  `ghcr.io/alobarquest/orchestrator:sha-<full-40-char-sha>`, which depends on the commit and
  nothing else, and the human-readable `:<short-sha>[-<label>]-amd64`, which is kept as a caption.
  Both are composed by `scripts/compute_image_tags.py` — it is the function, and it refuses a
  revision that is not a full 40-character sha, so a malformed one fails the build rather than
  producing an image that asserts an unresolvable provenance.
  **The workflow only builds and pushes — it never deploys.** Pointing Coolify at the new tag
  stays a separate, manual gate, same as before.
  Two digests, two different jobs, not competing checks: the **bundle digest**
  (`REGISTRY_ARTIFACT_SHA256`, currently `7aea8471…` per the pin file) is the **build-time,
  security-critical** gate — the Dockerfile's `registry` build stage recomputes it from the
  shaped context and **fails closed** on any mismatch (wrong/tampered actor registry), whether
  the build runs in CI or by hand. The **image SHA / running container's `RepoDigest`** is the
  separate **deploy-time identity** check Devon still does by hand after the Coolify swap —
  proving prod is running bit-for-bit what the workflow pushed.
  **CORRECTED 2026-07-31 (WS-P2.17 Inc 4): the recipe this file has been shipping for that check
  does not work.** `docker inspect <container> --format '{{index .RepoDigests 0}}'` fails, because
  `RepoDigests` is a property of an **image**, not of a container — inspecting a container returns
  no such field. Go container → image → digest instead:
  `docker image inspect "$(docker inspect <container> --format '{{.Image}}')" --format '{{index .RepoDigests 0}}'`.
  The invariant ("ask production what it is running") was right; only the command was wrong, and
  a wrong command that errors is at least loud — do not replace it with one that prints an empty
  string. Bumping either digest requires
  bumping `security-standards.pin.toml`'s `revision` and `artifact_sha256` together (see that
  file's own header comment for the recompute recipe).
  **Fallback / differential baseline — keep this runnable, don't delete it:** the manual
  `docker buildx` recipe still works and is the thing to fall back to if the workflow is down,
  or to diff against if a CI-built image looks wrong. Recipe:
  `docs/software-delivery-system/2026-07-09-ws64a-deploy-and-onboarding-state.md`. Unless an
  actor/registry change is intended, PIN `SECURITY_STANDARDS_REVISION` to the pin file's
  `revision` (`65655ddf…`) and assert the computed `artifact_digest()` equals the pin file's
  `artifact_sha256` (`7aea8471…`) BEFORE the long build — that is the same byte-identical bundle
  gate (13 actors), done by hand. Then `docker buildx build --platform linux/amd64
  --build-context registry=$ART --build-arg SECURITY_STANDARDS_REVISION=$SHA --build-arg
  REGISTRY_ARTIFACT_SHA256=$DIGEST -t ghcr.io/alobarquest/orchestrator:<sha>-<ws>-amd64 --push .`
  produces a single amd64 v2 manifest; verify the running container's RepoDigest == the pushed
  digest after Coolify swaps (via `.Image`, per the correction above).
  **A hand-run build must also pass the three `--label` flags the workflow passes**
  (`org.opencontainers.image.revision` with the FULL sha, `.source`, `.created`), the second
  `-t sha-<full-sha>`, **and `--build-arg ORCHESTRATOR_REVISION=<the same full sha>`** — or the
  fallback silently produces a less-identifiable artifact than the paved road, precisely the state
  Inc 7 closed. The workflow refuses such an image; a hand build has nothing to refuse it.
  **The build arg is what `/health/live` serves, and omitting it is SILENT rather than loud**: the
  image runs, the endpoint answers 200, and `revision` reads `null` — which the currency census
  reports as a subject that cannot state what it is rather than as one that is stale, so a hand
  build with the labels and without the arg is invisible until somebody asks why one row of the
  census never resolves. `ARG ORCHESTRATOR_REVISION` is declared in the **runtime** stage, and that
  is load-bearing: measured 2026-09-08 against a throwaway image, the identical `--build-arg`
  against an `ARG` on the builder stage lands EMPTY in the runtime environment, so a well-formed
  build command produces an unstamped image.
  **The FULL 40-character SHA goes in the workflow's `ref` INPUT, not in `gh workflow run --ref`.**
  These are two different things and this bullet used to conflate them, which cost WS-P2.18 Inc 4 a
  422. `--ref` selects the git ref the workflow FILE is read from and expects a branch or tag;
  passing a raw SHA to it fails `HTTP 422`. The revision to build is a workflow **input**
  (`-f <input>=<40-char-sha>`), and there `actions/checkout` treats a non-40-character value as a
  branch/tag pattern, matches nothing, and fails the run — so a short SHA cannot build, however
  valid it is to `git`. Read `.github/workflows/release-image.yml`'s `inputs:` block for the input's
  actual name rather than guessing it. A plain `docker build .` with no `registry` context fails at
  `COPY --from=registry` — that is expected, not a Dockerfile bug. (Verified 2026-07-25, WS-P2.5
  Inc 2 deploy. Automation added 2026-07-26, image-build-automation workstream.)

### #53

- **An orchestrator image with NO labels is an old image, not a tampered one — every image built
  before 2026-08-02 carries `Config.Labels: null`.** WS-P2.18 Inc 7 made the build assert
  `org.opencontainers.image.revision` (full 40-char sha), `.source` and `.created`, and made the
  workflow **pull the pushed image back from the registry and fail closed** unless the revision
  label equals the commit built — a label present in the build command and absent from the
  artifact proves nothing, which is exactly how the no-label state survived unnoticed. Ask an
  image what it is with
  `docker image inspect <ref> --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'`.
  **Until an image is next rebuilt the answer is `<no value>`, and for those the ONLY provenance is
  the tag's short sha** — `git rev-parse <short-sha>` back to the commit, and nothing at all if the
  tag was lost. Production's `c755c99-wsp218inc5-amd64`
  (`sha256:615c0b3053671fed9a33f9012f645f301eaf6da7aa9dafa29e14a7c06f820939`) is such an image; the
  discontinuity ends the first time production is rebuilt, and nothing about the deployed image was
  changed to close it. Note the container→image→digest correction above applies to reading labels
  too: `Config.Labels` on a *container* is the container's own label set, not the image's.
  **`sha-<full-sha>` is DERIVABLE, not IMMUTABLE — a rebuild silently re-points it, measured, not
  assumed.** Two builds of `e1204893…` on 2026-08-02 produced the same tag name and the digests
  `sha256:da035e69…` and `sha256:a0936e08…`. The `.created` label alone guarantees this (it is a
  build timestamp), and `python:3.12-slim` is a moving base tag underneath it, so image
  reproducibility was never a property this build had. **The digest is the identity; the tag is how
  you find it.** Two consequences: a rollback target should be recorded as a digest whenever one is
  to hand, and during a rebuild there is a real (seconds-long) window in which the two tags
  disagree, because buildx pushes them sequentially — observed live, and a plausible way to
  misdiagnose a defect that is not there. Making the derivable tag refuse to overwrite is an open
  follow-up, not something GHCR enforces.

### #61

- **Migrating before the image swap puts the STILL-RUNNING old image into `/health/ready` 503
  `migration_drift`, and that is only survivable because neither health check consults
  `/health/ready`.** The readiness probe compares the code's expected head against the database's,
  so between `alembic upgrade head` and the Coolify swap the old container reports unavailable
  while `/health/live` stays 200 and traffic keeps flowing. Coolify's own health check is
  **disabled** (`health_check_enabled: false`) and the Dockerfile `HEALTHCHECK` probes
  `/health/live`. **If either is ever pointed at `/health/ready`, migrate-first becomes an
  outage** — the container would be killed as unhealthy mid-window. Keep the window short and do
  not "improve" the health checks without re-deciding the migration order. Verified 2026-07-28
  (WS-P2.8 deploy, ~4-minute window, no traffic impact).

### #70

- **Coolify stores an `is_literal` env value wrapped in single quotes and injects the STRIPPED
  form — so a write must send the RAW value, and `is_build_time` is rejected outright.** Two
  independent traps in the same API, both hit during the GAP-7 password rotation. (1) The
  production `ORCHESTRATOR_DATABASE_URL` is stored as 111 bytes (`'<109-byte DSN>'`) while the
  container receives 109; POSTing the already-quoted form yields a **double-quoted** 113-byte
  value that still parses one layer down, so a naive readback check passes while the app would
  get a broken DSN. Verify by hashing what the CONTAINER receives
  (`docker exec … sh -c 'printf %s "$VAR"' | sha256sum`), never what the API returns. (2)
  `POST /api/v1/applications/{uuid}/envs` **422s on `is_build_time`** — `{"errors":
  {"is_build_time":["This field is not allowed."]}}` — the accepted spelling is `is_buildtime`,
  exactly as the GET response spells it. Because the reliable write path is
  delete-by-uuid + recreate, a 422 leaves the variable **absent**: validate an unfamiliar body
  against a throwaway key first, and retry rather than exit. (Verified 2026-07-29, GAP-7.)

### #82

- **Changing policy costs a release, and a release restarts the orchestrator — the artifact is read
  per call but its bytes arrive with the image.** WS-P2.18 Inc 2 separated two things that read as
  one: *getting new bytes to the process* (a release) and *making the process notice bytes it has*
  (free — `factory-policy.toml` is re-read per call, never cached). Only the second was decidable at
  that increment, and it is the half that cannot be retrofitted. **Operational rule: change policy
  only when no run is live** — the Actions run concluded, the unit out of `executing`, cost-actuals
  recorded. This is the same rule closing a bounded window already imposes, and for the same reason:
  the runner calls back at the *end* of its run, `fail-run` fails the same way `finalize-run` does,
  and a restart in that window strands the unit with its attempt spent.

### #105

- **The reach check needs `ORCHESTRATOR_APP_BRAIN_URL` and `ORCHESTRATOR_APP_BRAIN_READ_KEY` in the
  environment, and without them it fails closed SILENTLY.** WS-P2.28. Absent, admission refuses with
  `reach_estate_source_unconfigured` — correct behaviour, but nothing is stranded and nothing
  complains until somebody routes work and wonders why it will not run. **Both variables must ship
  with the release that first carries the check**; verify them from inside the container after the
  swap, the way `ORCHESTRATOR_M2M_*` is verified. Use the **read-only** key WS-P2.29 provisioned,
  never the full-access one.

### #194

- **A post-deploy check that asks whether production is HEALTHY, or which IMAGE it runs, cannot see
  that the served surface is missing a field a consumer needs. Ask what it SERVES.** Two days were
  lost to this on 2026-08-16: `#167` (the freshness rule) and `#177` (the deadlock fix) were merged
  and undeployed, production's `EstateLandingAdmissionResponse` carried only its seven pre-Increment-6
  keys, and the lander read `branch_update_qualifies`, got nothing, and **skipped every record**. The
  recurring `0 updated, 0 would-update` was the field being absent — not the rule finding nothing to
  do — and HQ read those lines each morning and reasoned from them. Production had been on
  `4cb6dd8-adr0019inc5b-amd64` since ~2026-08-12, which predates Increment 6 entirely.
  **The check is one command and belongs in every deploy verification alongside the digest:**
  `curl -s https://sds.alobar.net/openapi.json | python3 -c "import sys,json;
  print(sorted(json.load(sys.stdin)['components']['schemas']['<ResponseModel>']['properties']))"`.
  Confirmed working after the 2026-08-16 swap to `6e47adb-adr0024-amd64`: the served properties are
  now `branch_update_qualifies, change_record_id, head_sha, policy_version, pr_number, refusals,
  repository, rollout_base_matches_pin, satisfied`.
  Note the pairing with the existing `response_model` invariant: that one says a field the model
  does not declare is silently dropped **in the code**; this one says a field the deployed image does
  not carry is silently absent **in production**. Both fail the same way — the consumer reads a key
  that is not there and continues — and neither is visible to a green test suite.

### #195

- **Pointing Coolify at a new tag is HQ's mechanic, not Devon's gate.** The paved road's *"pointing
  Coolify at the new tag stays a separate, manual gate"* means **the workflow does not do it**, not
  that Devon does. His gate is deciding what and how a change may happen; the execution is HQ's, via
  `infraops` (`coolify_update_application` + `coolify_deploy`), which is the sanctioned path and
  which this session's infra-separation waiver permits. Misreading it cost a round-trip on
  2026-08-16 and it is the same conflation as assigning him a merge. **Record the outgoing tag
  before the write** — here `4cb6dd8-adr0019inc5b-amd64` — because the derivable `sha-<full>` tag
  can be re-pointed by a later build and the digest is the only immutable identity.

### #201

- **A DEPLOY VERIFICATION THAT CHECKS HEALTH, DIGEST, REVISION LABEL AND SERVED SCHEMA STILL CANNOT
  SEE THAT THE DATABASE IS BEHIND.** On 2026-08-19 HQ deployed three images (`#181`, `#182`, `#183`)
  and ran **zero** migrations; production sat at `0026_adr19_estate_merge` while head was
  `0028_adr26_change_record`, serving code that expected a column the database did not have. It
  surfaced as a **bare HTTP 500** from `POST /api/v1/package-intakes` — because only `DomainError`
  and `APIAuthenticationError` have registered handlers, a schema mismatch reaches the wire with no
  diagnosis, and only the container log named it:
  `psycopg.errors.UndefinedColumn: column work_package_revisions.change_record_id does not exist`.
  **Add `alembic current` vs `alembic heads` to every deploy verification.** It is one command and
  it is the only one of the five that can see this:
  `docker exec <container> sh -c 'cd /app && .venv/bin/alembic current; .venv/bin/alembic heads'`.
  The ordering rule already recorded here — migrate BEFORE the image swap, because the old container
  tolerates the new schema and the new one does not tolerate the old — was skipped three times in one
  afternoon without anything noticing. Earlier deploys were fine only because nothing needed a
  migration; that is luck, not a working practice.

### #267

- **THE ORCHESTRATOR'S COOLIFY SWAP IS NOT ZERO-DOWNTIME, unlike `brain`'s — and the difference is
  that its own Coolify health check is DISABLED.** Measured 2026-09-01 during the ADR-0038 deploy,
  timestamps from one probe loop and `docker inspect`: `11:23:36Z` the old container answering
  `/health/live` 200; `11:23:39Z` the new container started; `11:23:56Z` the proxy answering
  **`no available server`**; `11:24:18Z` the new container serving. So the old container was gone
  ~17 seconds after the new one started and ~22 seconds before it could serve. **This qualifies the
  recorded rolling-update invariant** — *"Coolify serves the OLD container throughout the swap"* —
  which was measured on `brain`, where `health_check_enabled` is TRUE and the log shape is
  `New container started` → `Waiting for healthcheck` → `New container is healthy` → `Removing old
  containers`. The orchestrator has `health_check_enabled: false` (deliberately, so `/health/ready`'s
  `migration_drift` 503 cannot kill the container during a migrate-first window), so Coolify has **no
  readiness signal to gate the removal on** and drops the old container on a timer instead. The
  timing is measured; that mechanism is inferred from the configuration and is not proven.
  Two consequences. A brief public outage is expected on every orchestrator deploy — do not diagnose
  it. And the CI-poll invariant that leans on the old container answering throughout does **not**
  hold here, so a liveness poll against `sds.alobar.net` during a swap can fail for a reason that is
  not a bad build.

### #285

- **[CORRECTED 2026-09-07 — the straggler is neither slow nor stuck: COOLIFY'S DEPLOYMENT
  FAILED. Read the correction at the end of this bullet before acting on any of it.]
  `brain`'s deploy verification fails on ONE OF FOUR APPS, a DIFFERENT one each time, and the
  straggler does not report within the full 600-second deadline.** Measured 2026-09-05 across the
  last eight push-triggered `ci.yml` runs: **two failed (25%)**, both at *"Verify every deployed
  brain is serving the new revision"*. Run `33843605720` — `infra`, `open`, `code` reported inside
  ~40 seconds and **`app` never did**; run `33871480326` — `open`, `infra`, `app` reported and
  **`code` never did**. So it is not one slow application, and it is not the deadline being
  marginally short: three swap in under a minute and the fourth is still absent ten minutes later.
  It resolves afterwards — `deploy_watcher` independently records `production_reached=yes
  attests=revision_confirmed` for both — so the change is fine and the workflow's verdict is not.
  Two consequences worth knowing before touching it. The per-app check is CORRECT and must stay:
  the four brains genuinely swap at different times, and a poll that checked one and generalised
  would pass while three served the previous image. And the failing run leaves its change record at
  `approved` rather than `resolved` (item 78 on 2026-09-04), so the records accumulate slowly.
  **Raising the deadline would be a guess** — nothing here says the straggler is slow rather than
  stuck, and that distinction is what a remedy would have to rest on.
  **CORRECTED 2026-09-07 BY ASKING COOLIFY, WHICH IS THE ONE PARTY NOBODY HAD ASKED. It is a
  third thing: the deployment FAILED and was rolled back, about two seconds in.** Coolify's helper
  container exits between the `docker run` that starts it and the `docker exec` that follows, the
  rolling update aborts on `No such container`, Coolify removes the new version, and the previous
  container keeps serving — which is byte-for-byte what a deployment still in flight looks like
  from the outside. The workflow's verdict was HONEST; what it could not do was name the cause,
  because it only ever asked production what it was SERVING.
  **Census over 154 deployments of the four brains: 5 failed, all in `brain-app` (3) and
  `brain-code` (2), none in `brain-infra` or `brain-open`.** Every failure carries a doubled
  `Preparing container with helper image` line and every success carries one — a clean
  discriminator, and the cheapest way to classify a new one. The Docker daemon log puts the
  helper's `task-delete` 1.7 s after its network join, with **no OOM kill** (the host has 3.8 GB
  and 2.8 GB of swap in use, so it is under pressure, but nothing killed it).
  **The obvious hypothesis is FALSIFIED, and the falsification is the reusable part.** `brain-app`
  and `brain-code` are triggered third and fourth against `concurrent_builds: 2`, so queueing
  looks like the answer. Measured, the wait before a deployment's first log line is 25.7–30.9 s
  for the five failures and **min 0.8 / median 24.6 / max 62.0 s for the 149 successes** — the
  failed range sits inside the successful distribution. Position separates the two PAIRS; queue
  wait separates nothing. **Why the helper exits is not established**, and a remedy resting on
  queueing would have been resting on nothing.
  **`It resolves afterwards` IS FALSE, and that is the half that cost real time.** It resolved for
  the 2026-09-04 pair because a later push redeployed them. Nothing corrected 2026-09-06:
  **`app-brain` served `4b80fac8` while `main` was `de1bb53b` for a day and a half**, under a red
  run on `main` whose message sent a reader looking at three things that were all wrong. A failed
  Coolify deployment is not self-healing — it waits for the next deploy of that app, or for a hand.
  **CLOSED for the REPORTING half by `brain#62`.** The trigger step keeps the deployment id Coolify
  names (it was printed and discarded, which is why nothing downstream could ask about it), and the
  verify step reads that deployment's status before each revision poll, failing fast with Coolify's
  own error line. Only an explicit `failed` decides — an unreadable status is not a verdict, so an
  unreachable Coolify costs a slower failure and never a wrong one — and the revision poll stays
  the authority on success, because `finished` and *serving the revision* are different facts and
  the four brains swap at measurably different times off one image. **The deployment still fails;
  it now says so in about forty seconds instead of ten minutes, and production still needs a
  redeploy.** Whether to retry automatically, serialize the four triggers, or raise
  `concurrent_builds` is open, and the last of those touches every app on that host rather than
  these four.

### #305

- **THE ORCHESTRATOR IMAGE DOES NOT SELF-MIGRATE, change-manager's DOES, AND THIS REPOSITORY'S TWO
  RECORDED RECIPES DISAGREE ABOUT IT.** Settled 2026-09-19 from the image rather than from prose,
  during the G1+G2 deploys. The orchestrator's `Dockerfile` ends at a bare
  `CMD ["uvicorn", "orchestrator.main:app", …]` — **no entrypoint script and no `alembic` anywhere**
  — so a swap applies no migration. change-manager is the opposite: its `entrypoint.sh` runs
  `alembic upgrade head` at container start, which is where the "no manual migrate step" line in the
  ADR-0019 Increment 1 report comes from. Two notes here conflict and both are about the
  orchestrator: `docs/superpowers/plans/2026-07-30-wsp212-context-enrichment.md:1719` says migrate
  against production, then build, then point Coolify; `…/2026-07-27-wsp27-inc2-inbound-reconciliation.md:1190`
  says point Coolify and deploy, then run alembic **in the new container**. The first is right, and
  the reason the second reads plausibly is that it describes what change-manager does.
  **THE MECHANISM IS THE PART NOBODY WROTE DOWN, and the obvious reading of "migrate first" is
  impossible:** the new migration exists only in the new image, and the running container carries
  the old code. So the order is **build → migrate → swap**, where BUILDING DEPLOYS NOTHING
  (`release-image.yml` only pushes; `build_pack: dockerimage` means Coolify pulls a prebuilt tag and
  never builds). Migrate by running alembic **from the new image** on the `coolify` network, with
  the database URL extracted inside the VPS from the running container's own environment and never
  printed:
  `DB=$(docker inspect <c> --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^ORCHESTRATOR_DATABASE_URL=' | cut -d= -f2-)`
  then `docker run --rm --network coolify -e ORCHESTRATOR_DATABASE_URL="$DB" <new-image> sh -c 'cd /app && .venv/bin/alembic upgrade head'`.
  Measured twice that day — `0034→0035` and `0035→0036`, `current == heads` after each, swap ~50s,
  `/health/ready` back to 200 — and the drift window between migrate and swap is exactly the
  documented one: the old container reports `migration_drift` 503 on a readiness endpoint that
  Coolify's own check (`health_check_enabled: false`) and the Dockerfile `HEALTHCHECK`
  (`/health/live`) both ignore. Keep it short; do the tag write BEFORE the migration so the swap is
  one call afterwards.
