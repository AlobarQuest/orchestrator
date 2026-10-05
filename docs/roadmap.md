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

The instance runs `a351452-tier3-amd64`, which carries Tier 1, Tier 2 and Tier 3. No 1.0 work
item remains open. The last one, Python 3.14 for the three hook-run satellites (`fe09c9110a5c`),
landed on 2026-10-03: security-standards #65 and #66, project-standards #45 and #46, and
code-standards #55.

`tests/architecture/test_layering.py` keeps its own import scanner on purpose. It needs the names
after `from orchestrator import …` and must tell `import orchestrator` apart from
`from orchestrator import x`; the shared `import_scan.py` reports both as `orchestrator`, and its
other callers depend on that output staying as it is.

## SDS 1.1

SDS 1.1 finishes the layer-by-layer simplification review of 2026-07-31
(`~/docs/software-delivery-system/2026-07-31-simplification-review-notes.md`). The review's
decisions and rulings were audited against the code on 2026-10-03: about half are built. This
section holds every one that's partial, not started or deferred. Each entry names the review's
identifier, so its reasoning can be found in the notes. When an item ships, delete its entry and
record the outcome in the ADR or pull request that shipped it.

Shipped so far: the stalled-execution instruction (#336) and reversible decomposition before claim
(2b-2, R5; ADR-0052, #338), both deployed on 2026-10-04 as `72fc88f-adr0052-amd64`. The landing join for traceability (5c, #343) deployed
as `ae9ebe4-sds11trace-amd64`, and the server-assigned dispatch ordinal (3a-2, #344, with
intent-packages #106) as `ff4936f-sds11ordinal-amd64`. Automatic `ready` (2d-1, #346, with
intent-packages #107 and #108) deployed as `8371b04-sds11autoready-amd64`.

Two decisions aren't carried forward:

- 6b, confirming Todoist as the place to see factory work: ADR-0040 retired the tracker lane.
- 6c, deriving candidate lessons automatically: Tier 3 item 24 deleted the promotion surface it
  would have fed.

### Order

1. **Dead-letter entries carry an acknowledged state (6a).** `services/reporting/dead_letter.py`
   still lists cancelled units that were already handled.

### Blocked

- **Traceability through observations (5c), the rest.** The chain joins each release commit's
  landing and derives a pull request made outside the factory from it (#343). Wave-2 exit clause
  2 still needs a post-deployment observation of a release, and Devon ruled on 2026-10-04 that a
  landing, recorded at merge, does not answer it. Blocked by 5a: no deploy records a release
  binding, so no release exists for the revision watcher's `production_revision` records (from
  2026-09-08) to observe. Devon chose on 2026-10-05 to take it after 5a.

### Deferred, with the evidence

Devon deferred these two on 2026-10-03, after measuring showed neither costs anything today.

- **Evidence attribution (3d-1, R8).** factory-runner files its one evidence row under the first
  acceptance criterion (`_first_ac_id`). That rarely matters: `factory decompose` maps exactly one
  criterion to each unit and retains the rest. The row's type, `runner.pr.opened`, has no
  evaluator, so it resolves no criterion either way. Correct attribution alone makes the record
  honest but removes no human gate. Removing gates would need the runner's measured exit codes to
  resolve `automated_test` and count toward landing, which makes worker-reported results
  decision-bearing: a trust decision the review didn't make. The dead `build_verification_evidence`
  went in factory-runner #88.
- **A lapse costs no attempt (3b-2, 3b-3).** Reclaiming an expired claim still increments
  `attempt_count` (`services/lifecycle/claims.py`), and a renewal after expiry is refused. But the
  lease lengths per lane of 2026-08-02 (#128) ended the lapses: production's SLO report counts 8
  expiries in 75 claims since 2026-07-01, and none in the 9 claims since 2026-08-15. Revisit if
  `claim_expiry_rate` rises again.

### The rest of 1.1

These follow the ordered list, in no fixed order.

| Review id | Item | State today |
|---|---|---|
| waiver | A human may waive only a criterion that's currently failing. | Any human may waive any criterion. |
| 5b | One declared deployment-observation schema. | Five summary dicts; only the secret detector is shared. |
| 3c-1 | Separate the runner's permitted commands from its ordered verify script. | factory-runner still runs `allowed_commands` as the script. |
| 3d-2 | Record real exit codes, or drop the field. | factory-runner writes a literal `exit_code: 0`. |
| 4a-2 | One evidence vocabulary with a divergence test in both directions. | Five types copied by hand, checked one way. |
| 4c-3 | Separate the deciding view from the auditing view. | One page with a decision section and context-gated forms. |
| 5a | Record the release binding as part of deploying. Unblocks the rest of 5c. | Only machine-local activations bind; Coolify deploys and self-builds don't. |
| 7b | Dead-letter names the recovery action, as pending decisions does. | Pending decisions names dispositions; dead-letter doesn't. |
| 7c | Run the whole-repo guards where the work happens. | `make check` refuses an empty collection; the guards still run only in a full `make check`. |
| 2a, L1a | A human intake without pasting, and a simpler authoring interface above intent packages. | Machines can register intakes (ADR-0027); the human paste survives, and packages are LLM-authored through `factory create`. |
| L1b | Machine-originated inputs first: the rotation lane (WS-P3.5). | Four producers exist; where WS-P3.5 belongs is open decision 1. |
| 3a-1 | Admission keeps two checks; posture moves to policy. | Admission grew (reach, estate and declaration terms). Review which terms fold into `factory-policy.toml`. |
| R9 | Net gate load is a target set per wave. | Counted once, in the WS-P2.17 spec. |
| R11 | Deploy documentation reflects R11. | `docs/operations/deploy.md` still says a step is done by hand by Devon. |
| 7a | Deploy is automated like the build, and migrate/swap ordering is safe by construction. | Deferred by Devon on 2026-08-02. Migrate-before-swap is safe only because no health check reads readiness. Re-decide before building. |

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
