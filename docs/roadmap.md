# Roadmap

This page holds the work that remains before the Software Delivery System (SDS) reaches 1.0, and the
decisions still open. It replaces the live parts of the programme plan, which lived outside any
repository at `~/docs/software-delivery-system/2026-08-20-programme-plan.md` and is now archived.
Two things stay where they are:

- The pinned Phase-2 plan, `~/docs/software-delivery-system/2026-07-09-program-phase2-post-mvp-plan.md`.
  `docs/operations/wave-exit-manifest.toml` hashes its exit bar, so it must not move or change.
- Architectural decisions, which live in `docs/decisions/`. This page links to them and does not
  restate them.

Update this page when an item is finished or a decision is made. A decision recorded here is not an
ADR: when one is made, write the ADR and delete the entry here.

## What 1.0 means

1.0 is the SDS with the technical debt from the 1.0 debt survey
(`~/docs/software-delivery-system/2026-09-27-1.0-debt-survey.md`) paid down, deployed to the one
instance at `sds.alobar.net`, and its scheduled lanes running against that deployment.

- Tier 1 (defects and risks) is merged.
- Tier 2 (structure: test runtime, domain packages for `services/`, routes and schemas per domain,
  settings, isolation tests) is merged.
- Tier 3 (items that needed a decision) was decided on 2026-09-28. The decisions are recorded in
  `~/docs/software-delivery-system/2026-09-28-tier3-decisions.md`.

## Remaining 1.0 work

1. **Deploy the instance.** Production runs `ae88b28-tier1-amd64`, which predates Tier 2 and most of
   Tier 3. Follow `docs/operations/migrations.md`: build the image, run `alembic upgrade head` from
   the new image (migration 0038 drops `infra_lane_links`), then swap the Coolify tag. Verify the
   digest, `alembic current` against `alembic heads`, the served OpenAPI paths, and the revision
   that `/health/live` reports. Keep the kill-switch environment variables set until this deploy
   lands, and don't break down `wsp211-conformance-kit` revision 1 before it.
2. **Remove what the deploy makes dead.**
   - Delete the Coolify environment variables `ORCHESTRATOR_BRAIN_PROPOSAL_*`. Nothing reads them,
     and they may hold writable Brain keys.
   - Delete the Traefik router `orchestrator-promotion-human`, which points at deleted paths.
3. **Finish the follow-ups.**
   - Point `tests/architecture/test_layering.py` at the shared `import_scan.py`.
   - Advance factory-runner's `RECOMMENDED_CALLER_PIN` and every caller pin onto the Python 3.14
     revision (factory-runner #85).
   - Backlog: Python 3.14 for the three hook-run satellites (`fe09c9110a5c`), a replacement drill
     suite (`d5e596e35e0a`, ADR-0049), and the rest of the seeding helpers (`12b7427f1f46`).

## After 1.0

- Revisit the three build-scoped standing rules (infra separation waived, construction mode, build
  gates pre-authorized). They hold until 1.0 ships (Tier 3 item 19).

## Open decisions

Each entry is carried from §7 of the archived programme plan, where its full history is recorded.
The numbers are that plan's, kept so its history can be found.

1. **Where WS-P3.5 belongs.** It is a rotation lane rather than an input layer.
2. **Whether every automated signal posts a tier-1 observation**, including signals handled
   entirely outside the SDS. It's cheap; the cost is rows nobody reads.
3. **Which existing signals have no tier-1 producer.** The 2026-08-27 enumeration is out of date:
   `pin_watcher`, `tool_installer` and `revision_watcher` all post observations and weren't on it.
   Of the surfaces it named, the daily portfolio scan and `high-power-actions.jsonl` still have no
   producer. Re-enumerate before building producers one at a time.
4. **Retire or lane `rate-shift-calculator`** (from plan decision 5). Its landing is `inert`, but
   nothing consumes the repository, and laning it would put the machine to work merging updates
   into a repository nobody uses.
5. **The activation sweep's scope** (residual of plan decision 9). Dispatch admission reads
   `factory-target.toml` (ADR-0015 amendment 4), but the sweep's checkout list and the conformance
   kit's `delivery_profile` scoping are still separate answers that nothing derives from the
   declaration. `scripts/run-activation-sweep.sh` carries the `TODO(scope-registry)`.
6. **Retire the six daily scheduled `main` verification runs** (plan decision 11). The measurement
   that blocked this was taken on 2026-09-01: inert-lane landings fire push CI. Devon ruled on
   2026-09-02 to leave them for now. Revisiting is a judgment about sample size; don't re-take the
   measurement.
7. **Ordering supersession by `run_started_at`** (plan decision 12, ADR-0044 clause 1). A re-run
   resets `run_started_at`, so an old success can sort first and withhold an excuse. The failure is
   conservative. The fork is between accepting any success whose head is ahead of the merge, and
   ordering by `created_at`. `concurrent_rollout_run` reads the same model and needs
   `run_started_at`.
8. **Run-level `success` in supersession** (plan decision 13, ADR-0044 clause 4). The supersession
   predicate trusts a run's conclusion where the rest of the module reads the rollout step. Clause 3
   keeps this latent. The fork is whether to pay a fifth GitHub read per failed rollout before a
   workflow revision of that shape exists.
9. **Make the rollout-transcription-currency check required** (plan decision 14). It's advisory.
   Making it required would have blocked every open branch for 18h56m and 27h30m in the two
   historical instances, for changes unrelated to the rollout. Adding a required context is a paired
   operation with branch protection.
