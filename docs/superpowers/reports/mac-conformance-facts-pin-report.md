Outcome: done

# Build report: mac-conformance-facts-pin (W-221)

## What I built

- PR: https://github.com/AlobarQuest/orchestrator/pull/744. Base `main` `0714aff9`. Branch
  `hq/mac-conformance-facts-pin`. `063c1874` is the only commit that touches a file other than
  this report; every later commit on the branch edits only this report. The PR is open and not
  merged.
- `security-standards.pin.toml`:
  - `revision`: `08754a0fb887e572509337f36948b048963c25f4` → `1dfda4d0f578516b73c980275840bb25416f8752`.
  - `artifact_sha256`: `0d081268…bf6d` → `ee00a16c0f9267e1d4606b0067a5f8fbe8e6cd85c61fd1181909b19a0cc3daba`.
  - The header comment (the recipe) is unchanged.
- The fixtures are unchanged. The reason is in the "Fixture changes" section.
- No test names the old revision or digest as the pin, so no test changed. See "Tests that name
  the old revision".

## Recompute command and digest

I read security-standards only through `git archive` (inside `shape_registry_context.py`) after a
`git fetch`. `origin/main` was `1dfda4d0f578516b73c980275840bb25416f8752`. I didn't change that
checkout. I ran `/tmp/w221-digest.sh <rev>` once at the old pin and twice at the new revision.
Each run writes to a fresh `mktemp -d` directory. The script, verbatim:

```bash
#!/bin/bash
set -euo pipefail
cd /Users/devon/Projects/orchestrator/.worktrees/hq-W-221
REV=$1; OUT=$(mktemp -d /tmp/w221-reg.XXXXXX)/registry
.venv/bin/python scripts/shape_registry_context.py --source ~/Projects/security-standards --revision "$REV" --output "$OUT"
.venv/bin/python -c "import sys,json; sys.path.insert(0,'scripts'); from build_registry_bundle import artifact_digest, build_bundle_from_artifact; from pathlib import Path; d=artifact_digest(Path(sys.argv[1])); b=build_bundle_from_artifact(Path(sys.argv[1]), sys.argv[2], d); print('out', sys.argv[1]); print('digest', d); print('source_revision', b['source_revision']); print('actors', len(b['actors']), [a['agent_id'] for a in b['actors']])" "$OUT" "$REV"
```

The `build_bundle_from_artifact` call is the in-build gate's own check. It confirms that the
digest, `SOURCE_REVISION` and the revision agree, and it reads back the actors.

| Run | Revision | Digest | Actors |
|-----|----------|--------|--------|
| Control | `08754a0f…25f4` | `0d081268db15e54eace6978d4686f70ede909e49dc38be2d91585d7d60e9bf6d` (equals the old pin) | 21 |
| 1 (`/tmp/w221-reg.DZCmQC`) | `1dfda4d0…8752` | `ee00a16c0f9267e1d4606b0067a5f8fbe8e6cd85c61fd1181909b19a0cc3daba` | 22 (adds `mac-conformance-facts`) |
| 2 (`/tmp/w221-reg.cf9QwP`) | `1dfda4d0…8752` | `ee00a16c0f9267e1d4606b0067a5f8fbe8e6cd85c61fd1181909b19a0cc3daba` | 22 |

`diff -rq` of run 1 against run 2 found no differences. The control run shows that the recipe
and the pin agree.

## Files the image picks up between the two revisions

The image copies `registry/agents`, `src/agent_registry`, `src/factory_events` and `schema`
(`scripts/shape_registry_context.py` `_MAP`, lines 16-21; `Dockerfile` lines 14 and 42-44, at
`0714aff9`). I ran `diff -rq` on the shaped outputs for `08754a0f` and `1dfda4d0`. It lists the
following files:

- `SOURCE_REVISION`: the new revision.
- `agents/mac-conformance-facts.yaml` (new, #102): `authority_profile: none-v1`,
  `capabilities: []`, a label with no credential.
- `schema/factory-event.v1.schema.json` (#102): adds `"mac-conformance-facts"` to the
  `source.system` enum, and nothing else changes.
- `src/factory_events/README.md` (#100): documentation.
- `src/factory_events/cli.py` (#100): `adapt --source portfolio-scan`, and `all` now includes
  it.
- `src/factory_events/adapters/portfolio_scan.py` (new, #100).

`src/agent_registry/` and the rest of `src/factory_events/` are byte-identical. That includes
`store.py`, `chain.py`, `envelope.py`, `db.py` and `__main__.py`. security-standards'
`pyproject.toml` and `uv.lock` don't change in the range (`git diff --stat 08754a0f 1dfda4d0 --
pyproject.toml uv.lock` is empty), so no dependency floor moves here, unlike P-PIN (#705).

### Run-time effect (the stop point check)

Nothing beyond the new schema value and registry label changes what runs:

- The orchestrator image reaches `factory_events` only through
  `import_module("factory_events.store")` and `import_module("factory_events.chain")`
  (`src/orchestrator/factory_chain_read.py:222,240` at `0714aff9`). An unanchored grep for `cli`
  and `adapters` in the shaped `store.py`, `chain.py`, `envelope.py`, `db.py` and `__init__.py`
  at `1dfda4d0` finds only a docstring mention of "the adapters' watermarks" (`store.py:10`). No
  module on the read path imports `cli` or `adapters`.
- No file in this repository runs `factory_events.cli`, `adapt --source` or
  `factory_events.adapters`. I grepped the whole repository except `.venv`, `.git` and `docs`.
- The pin does reach the container lanes. In their container branch, each sets
  `SECURITY_STANDARDS_DIR="$REPO_ROOT/security-standards"`, which is the image's pinned copy at
  `/app/security-standards`. See `scripts/run-work-carrier.sh:350-352` and `:572` (the chain
  read, with `PYTHONPATH="$SECURITY_STANDARDS_DIR/src"`), `run-bump-proposer.sh:375-377`, and
  `run-rotation-proposer.sh:296-303`, each at `0714aff9`. Those lanes reach `factory_events` two
  ways:
  - The chain walk: `orchestrator.factory_chain_read`, and intent-packages'
    `chain_db.py:105,134` (`factory_events.store` and `chain`; intent-packages `origin/main`
    `134b4c9`). Both modules are unchanged.
  - The emitter: intent-packages' `emitter.py:128-137` runs `python -m factory_events emit`,
    which loads `cli.py` through `__main__.py:3`. `cli.py` changes in only three hunks: the body
    of `_cmd_adapt` (`@@ -87`, `@@ -103`, with the new import inside the function) and the `adapt`
    subparser's `choices` (`@@ -148`). The module's top-level imports and `emit` don't change, so
    `emit` behaves as before.
  - Nothing I found runs `adapt` against the image's copy. I didn't check repositories other than
    this one and intent-packages. The `adapt` caller is the Mac nightly in security-standards,
    which runs from the Mac checkout.
- The bump driver's Mac branch puts the Mac checkout's `security-standards/src` on `PYTHONPATH`
  (`scripts/run-bump-driver.sh:229,273`). The pin doesn't change that checkout.
- Effect of the schema value: a chain walk in an image built at this pin accepts a stored event
  whose `source.system` is `mac-conformance-facts`. Before this pin, the walk refuses that event.
  This is the intended change.
- Effect of the registry label: the bundle gains one actor with no capabilities. The M2M
  credentials bind `agent_id`s that already exist, so this addition changes none of them.

## Fixture changes

None. `tests/fixtures/security-standards/` doesn't track the pin. The fixture's convention shows
this in several places:

- `SOURCE_REVISION` is the placeholder `0123456789abcdef0123456789abcdef01234567`.
  `tests/scripts/test_shape_registry_context.py:36-38` calls it "a fixed placeholder sha".
- CI builds the fixture image with that placeholder and a digest of the fixture itself:
  `--build-arg SECURITY_STANDARDS_REVISION=0123…4567` and
  `REGISTRY_ARTIFACT_SHA256=258e4757…0fbce` (`.github/workflows/quality.yml:283-286` at
  `0714aff9`). Any fixture edit would break that gate unless `quality.yml` changes too. That's a
  file outside this handoff's scope.
- The fixture is synthetic. The schema is 4 lines and `envelope.py` is 105. `agents/worker.yaml`
  doesn't exist upstream. None of the seven files matches its upstream copy at `08754a0f` or at
  `1dfda4d0`.
- `git log -- tests/fixtures/security-standards` ends at `a9683b52` (2026-07-09). Earlier pin
  bumps didn't touch the fixture, including #705 and #725.

The handoff's "Facts" section calls the fixture files copies of `src/`. They're stand-ins, not
copies.

## Tests that name the old revision

`grep -rn "08754a0f\|0d081268"` runs over the repository except `.venv` and `.git`. Apart from
the pin and `docs/superpowers/` it finds the following:

- `tests/cli/test_package_chain_database_integration.py:370`: the docstring
  "Needs a checkout whose schema knows `portfolio-scan` (security-standards `08754a0f`)". It names
  the revision that added `portfolio-scan`, not the pin, so it stays correct. The test needs
  `IT_SECURITY_STANDARDS_DIR` (lines 115-119, `scripts/check-integration.sh`), so `make check`
  doesn't run it. I left it unchanged.
- `docs/operations/deploy.md:660-661`: cut-over step 8, which records the `portfolio-scan` pin.
  It's an accurate record of that step, and docs are outside this handoff's scope. I left it
  unchanged. See "Follow-ups".

## Tests and checks run

- `make check` in the worktree against `orchestrator_test_hq_w_221`. The environment was set as
  `local-development.md:144-147` describes: `SECURITY_STANDARDS_DIR` pointed at the fixture,
  `PGTZ=UTC`, and `alembic upgrade head` ran first. Result:
  - ruff check: "All checks passed!"
  - ruff format: "1088 files already formatted"
  - pyright: "0 errors, 0 warnings, 0 informations"
  - pytest: 15,637 collected, 15,636 passed, 1 skipped, exit 0.
- The pin tests in that run include `tests/architecture/test_release_workflow.py`, which checks
  the pin file's shape and its readers.
- I can't run the release workflow's in-build digest gate here. The twice-recomputed digest and
  the `build_bundle_from_artifact` read-back stand in for it.
- After adding this report I ran `tests/architecture/test_layer_manifest.py`: 3 passed. The
  report falls under `docs/superpowers/**` (`layers.toml:770`). The PR's `Quality` job runs the
  full check again.

## Files changed

- `security-standards.pin.toml` (2 lines).
- `docs/superpowers/reports/mac-conformance-facts-pin-report.md` (this report).

## Self-review

The diff replaces two constants. I checked both against the recipe and against the gate's own
read-back function. The recipe reproduces the old digest, which shows the probe can tell a right
pin from a wrong one. `/code-review low main...HEAD` reported no findings.

## Concerns

- The handoff assumed the fixtures copy the pinned `src/`. They don't, so nothing in CI exercises
  the new schema value against the fixture. That coverage comes from security-standards' own
  tests (#102's `tests/test_factory_envelope.py`, `tests/test_agent_registry.py`).
- This pin also brings #100's `cli.py` and `adapters/portfolio_scan.py` into the image. The
  container lanes' emitter loads the changed `cli.py` for `emit`, but the change touches only
  `adapt`. Nothing I found imports `adapters/portfolio_scan.py` in the image.

## Follow-ups

- Building and deploying an image at this pin is a separate step, and it must happen before
  security-standards #101 merges. The reason is the same as in `deploy.md:661-663`: every walk
  re-validates stored events against the image's schema copy. I didn't dispatch `Release image`,
  push a tag, or call Coolify.
- `deploy.md`'s cut-over list has no line for the `mac-conformance-facts` pin yet. Whoever writes
  the bump driver's conformance cut-over can add one.
- Teardown after merge: remove this worktree, `dropdb orchestrator_test_hq_w_221`, and delete
  the branch after GitHub reports the PR as `MERGED`.

## Standards followed

- **Code Brain 3 (structured JSON logs to stdout in production):** The diff makes no logging
  change. It edits one TOML data file and adds this report.
- **Code Brain 4 (never silently swallow an exception):** The diff adds no exception handling.
  The newly copied `cli.py` deliberately swallows `FeedError` (#100, ADR-0059 Amendment 11
  item 6, and the code comment names the reason). Nothing I found runs `adapt` in the image.
- **Code Brain 5 (never log secrets):** The pin holds a commit sha and a content digest. The new
  registry entry is a label with no credential. Neither is a secret, and the diff logs nothing.
- **Infra Brain 591, 592, 593 (no BWS token in a tracked file or in history, no inline
  `BWS_ACCESS_TOKEN`):** The diff contains no token and no `BWS_ACCESS_TOKEN` assignment. I didn't
  run `bws` or read the Keychain.
- **Infra Brain 1032 (`cred.exposure-rotate`):** No credential was exposed or handled, so
  there's nothing to rotate. The work read no secret store.
- **Infra Brain 212 (HTTPS):** No endpoint, URL or transport changed. The pin is read at build
  time from a git checkout.
- **Construction mode:** I drove the work through to the PR without stopping. No stop point was
  met: the recipe and the pin tests agree, the change is the pin file only, and the run-time
  delta is the schema value and the label. I didn't merge, didn't dispatch `Release image`, and
  took no production action.
