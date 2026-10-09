# Credential rotation increment 3: orchestrator and package changes (session handoff)

ADR-0055 designs credential rotation as a pull-based executor in an isolated OrbStack VM. Its
increment 3 makes the orchestrator, intent-packages and security-standards changes the executor
depends on, and ships and deploys them before any rotation uses them. Amendment 2 (2026-10-09)
revised the increment's list after a code read, with Devon's answers to three forks.

This session builds and deploys those changes. It builds no executor (increment 4), creates no
`Rotation /` BWS project or bearer, and rotates no credential.

## What the session must produce

Each of the following items is merged and, where it is orchestrator code, deployed to production
and verified there (`memory: proceed-means-drive-to-the-gates`). ADR-0055's "Increment 3, as
amended" section is the list of record; if this handoff and the ADR disagree, the ADR wins and the
session says so in its report.

1. **Claim confinement both ways**, on `claim_unit`, against a configured executor `agent_id`, using
   decision 1's rotation-unit predicate. With the setting unset, every claim on a rotation unit is
   refused. The setting stays unset in production in this increment, because the executor's bearer
   arrives in increment 4. Validating at boot that a configured id resolves in the bundled registry
   and isn't a human identity is optional, matching `_m2m_credentials`.
2. **Recovery of a lapsed rotation claim** by release without a grant, and reclaim refusing to grant
   a rotation unit or to grant any unit to the executor. Amendment 2's correction names the shape,
   the eligibility behavior and the replay requirement.
3. **The claim-time `live_estate` window** for rotation units not marked non-hosted, with amendment
   2's start and continuation rule.
4. **A unit's completion resolving the `work_unit` dependencies that name it**, in the same
   transaction. `resolve_dependency_command` commits, so completion calls a non-committing core
   inside `_perform_transition`'s transaction (invariant: request entry points own their
   transaction).
5. **A `/review` form for resolving a human-act dependency**, with a strict `detail` schema and the
   evidence secret scan.
6. **The rotation evidence schema and its secret scan, on rotation units only.** A scan of every
   unit's evidence would refuse other producers' existing keys (factory-runner sends `body`, for
   example), so the schema and scan apply where the rotation-unit predicate holds. A session that
   wants a wider scan first runs it over the stored evidence and reports what it would refuse.
   The executor that files this evidence is increment 4, and the ADR gives only "fingerprints
   (sha256 prefix and length) and probe results per step" (decision 9). The session chooses the
   field names, pins the schema as a contract fixture increment 4 must conform to, and lists the
   names in its report as amendment 3 input. If the schema names step kinds, the word guards under
   `src/orchestrator/` refuse some of them (`deploy` among them): reword, never allowlist.
7. **The bearer `previous` parser** with its eight-day boot cap (decision 7), verified on
   production, and the line decision 7 requires in `docs/operations/deploy.md`: rolling back past
   this image requires removing `previous` first.
8. **The destination-list profile field**, optional as `standing` is, in intent-packages'
   `non-software-operational` schema (required would fail validation of the approved
   `rotation-openrouter-generic` revision 1),
   and the two rotation-proposer runbook steps amendment 2 names: read the field's change against
   the last approved revision, and compare it with the registry before approving. The comparison
   command arrives in increment 4; until then the step is a manual comparison.
9. **The executor's identity**: an agent file and an authority profile in security-standards, the
   pin advanced here (merge security-standards first; `revision` and `artifact_sha256` change
   together), and the image rebuilt so the bundle carries them. Its `environment` is the VM, not
   `mini`. Derive the profile's capabilities from `registry/capabilities.yaml` and the ADR (the
   asset table and decisions 1, 3, 4 and 9): each capability cites the ADR line that grants it.
   ADR-0055's acceptance covers what it states; a capability the session can't trace to a line
   goes to Devon. The report lists the capabilities with their citations.
10. **The two CLAUDE.md corrections** amendment 2 names (#90's renewal exception, and the
    `deployment_observation` rule pointing at `SECRET_KEY_PARTS`), plus a new invariant for each new
    rule a later session could break.
11. **Mutation review of every guard** in items 1 to 7, by hand: the count of mutations, and every
    survivor with its fix or its reason (`memory: mutate-the-code-run-the-controls`).

## Read first

- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`: decisions 1, 3, 7, 8
  and 9, the threat model, and amendment 2 in full. Amendment 1 for context.
- `docs/operations/rotation-proposer.md` and `src/rotation_proposer/standing.py`.
- `docs/operations/driving-a-unit.md`: how operational decompositions and envelopes are written.
- `docs/operations/architecture-guards.md`: route inventories, word guards, the vocabulary registry
  and the unreachable-guard test.
- `docs/operations/deploy.md`: release image, migrate-before-swap, and verifying the served schema.
- `docs/operations/credentials.md`: the M2M credentials and roles settings, and how an `agent_id`
  must resolve in the bundled registry.

## Where the code is

These are starting points, not designs. Read each before changing it; the shapes are the code's,
not this handoff's (`memory: handoffs-state-pointers-not-shapes`).

| Item | Where to start |
|---|---|
| 1, 2, 3 | `services/lifecycle/claims.py` (`claim_unit`, `reclaim_expired_claim`, `_perform_reclaim`, `_acquire_reclaimed_claim`, `_readiness_eligibility_error`, `_claim_replay`, `_reclaim_error_replay`, `requeue_unit`); `lease_policy.py`; `api/routes/lifecycle.py` (the reclaim route and its actor); `services/verifier/evidence.py` (lapse recovery) |
| 3 | `factory_policy.py` (`window_refusal`), `services/execution/reach_admission.py` (`change_window_refusal`, which stays as it is), `clock.py` (`TransactionClock`), `factory-policy.toml` |
| 1, 3 | `kernel/authority.py` (`constraints`), and the package revision's profile fields for the rotation-unit predicate |
| 4 | `services/intake/packages.py` (`resolve_dependency`, `resolve_dependency_command`); `services/lifecycle/lifecycle.py` (`ready_if_satisfied`, `_perform_transition`); `Dependency` in `persistence/models.py` |
| 5 | `web.py` (the `/review` routes; `resolve_reconciliation_condition` is the nearest precedent), `templates/unit.html`; `tests/architecture/test_scope_guards.py`; `tests/idempotency/test_matrix.py` |
| 5, 6 | `kernel/secret_metadata.py` (`secret_metadata_path`, `SECRET_KEY_PARTS`), `services/verifier/evidence.py` (`_validate_evidence_fields`) |
| 7 | `main.py` (`_m2m_credentials`), `identity/auth.py` |
| 8 | `~/Projects/intent-packages/src/intent_packages/profiles/non_software_operational.py` (`PROFILE_FIELDS_SCHEMA` is closed) and the test that existing rotation packages hash as before |
| 9 | `~/Projects/security-standards/registry/agents/`, `registry/profiles/`; this repo's `security-standards.pin.toml` and `Dockerfile` |

## Decisions already made

Don't reopen these; they are Devon's (amendment 2, "Devon's answers").

- Destinations are reviewed in the package diff and written by the package author. The proposer
  doesn't change, and no `/review` code is added for destinations: the list reaches `/review`
  through the unit envelope's `constraints`, which existing pages already show.
- Only the `live_estate` window is asked at claim, and only for rotation units not marked
  non-hosted.
- Per-unit completion clicks stay. Don't add a type to `DETERMINISTIC_TYPES`.

A new design fork found during the build goes to Devon, least machinery first
(`memory: devon-prefers-less-new-machinery`). Everything else is the session's to decide
(`memory: construction-mode-not-bear-wrestling`).

## Constraints

- **Edit only in worktrees**, one per repository (`.worktrees/rotation-inc3` here), each with its
  own venv and test database. The scheduled lanes run from the main tree.
- **Don't touch or read a live credential.** No item here needs one. Creating the executor's bearer
  and adding it to the orchestrator's M2M settings is increment 4.
- **Every new route** goes into the exact route inventories, and every new POST into the idempotency
  matrix or a reasoned non-ingress entry.
- **Each guard ships with tests that fail when the guard is removed.** Run the mutations, don't argue
  them.
- **The `previous` parser ships, deploys and is verified on production before any rotation writes
  `previous`.** Verify boot behavior with a scratch container of the exact image tag production
  serves, never by writing `previous` to production's settings. Use throwaway values for every
  setting, never production's (its proxy marker and CSRF secret are live). Run three cases: no
  `previous`; a valid `previous`; and an `until` more than eight days ahead, which must refuse to
  boot. Whether `previous` authenticates before and after `until` is proven by tests.
- **Before the deploy,** confirm production has no ready or in-flight unit matching the
  rotation-unit predicate; with the executor setting unset, such a unit becomes unclaimable.
- **The orchestrator image doesn't migrate itself.** Follow `docs/operations/deploy.md`: build with
  the release workflow, migrate from the new image, swap, and check `alembic current` equals
  `heads` and the served OpenAPI schema.
- **Run `make check` from a clean tree** before trusting a green (`memory: local-green-needs-clean-tree`),
  and `/code-review` on each diff.

## Out of scope

- The executor, its VM, its adapters and the `Rotation /` BWS projects (increment 4).
- change-manager's equivalent of the `previous` parser (decision 7). Increment 6 rotates
  change-manager's bearers and needs it first; the report names it as an increment 6 prerequisite.
- Any change to `reach_admission.change_window_refusal` or to `factory-policy.toml`'s windows.

## Closing steps

1. Merge each pull request when its checks pass; the session merges its own work.
2. Deploy the orchestrator once, after every orchestrator item has merged, and verify it on
   production as `docs/operations/deploy.md` describes.
3. Resolve this increment's backlog item, naming the pull requests and the deployed revision.
4. Report: what shipped, the mutation counts and survivors, anything the ADR got wrong (as a
   proposed amendment 3, not an edit), and the increment 6 prerequisite.
5. Check the main tree's `git status` for stray files, then remove each worktree, test database and
   merged branch.
6. Write the increment 4 handoff, the executor with no live credentials, if Devon asks for it.
