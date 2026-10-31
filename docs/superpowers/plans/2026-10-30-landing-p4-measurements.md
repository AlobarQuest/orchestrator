# Landing Part 4, Task 0: measurements

Measured 2026-10-30, read-only. Orchestrator `main` at `4fff334`, intent-packages `origin/main` at `02bebf5`.
No dependency code ran: no `uv add`, `uv lock`, `npm`, `dry_run_mutation`, or target code.

## Step 1: how `automated_check` decides over several named-check rows

**Only one row ever counts, and it is the newest. Rows do not accumulate; they supersede.**

- `verifier._verify_criterion` calls `evidence.current_evidence(session, revision_id, unit_id, ac_id)`,
  which selects every `Evidence` row for that `(revision, unit, ac_id)` and returns `_terminal(rows,
  "supersedes_evidence_id", ...)`: the one row no other row supersedes. It returns a single `Evidence | None`.
- `verifier_evidence.record_named_check_evidence` writes through `append_verifier_evidence`, which sets
  `supersedes_evidence_id = current_evidence(...).id` on every new row for the same `ac_id`.
- `_evaluate_declared_type("automated_check", evidence)` takes that single row and passes it to
  `_named_check_result`. Nothing iterates; there is no "any" and no "all".
- `check_name` is a field of the evidence command and payload, not of the criterion. The criterion carries
  no check name, so two rows for different checks under one `AC-001` form a chain of two, and the second
  silently replaces the first. The unit passes if the LAST filed check succeeded, whatever the earlier
  ones concluded.
- `_stabilize_named_check_evaluations` re-reads the chain head before the transition and fails closed if it
  moved, so a row filed during verification is caught, but it does not widen the rule to all rows.
- `validate_named_check_bindings` checks the single row against the unit's PR binding (head sha, attempt,
  dispatch, repository). It also does not know about other checks.

Consequence for Task 4: filing one row per required check under one AC does NOT require every check to
pass. Step 6 is needed, in one of two shapes: (a) one criterion per required check, so each has its own
chain (the generated package has exactly one, `AC-001`, `automated_check`), or (b) make the evaluator
require every distinct `check_name` in the head's rows to be `success`, which means changing
`current_evidence` use at the `automated_check` call site, since its chain model assumes one terminal.
The chain is also strict: `_terminal` raises `evidence_chain_invalid` on multiple terminals, so rows cannot
be filed side by side under one `ac_id` without changing that.

## Step 2: members, landing value, required checks

Member list: `GET /api/v1/members` (observer bearer), 14 members, all state `member`. Landing value from
App Brain `list_repositories` (`default_branch_landing`); required checks from
`gh api repos/<r>/branches/main/protection/required_status_checks` (contexts and checks agree on every repo).

| Member | Landing | Required checks on `main` |
|---|---|---|
| brain | redeploys | test; Lint, type-check, and test |
| change-manager | redeploys | Lint, type-check, and test |
| claude-octopus | inert | build and test; security review |
| factory-runner | inert | Quality |
| infraops-mcp-server | inert | build; Lint, type-check, and test |
| intent-packages | inert | Lint, type-check, and test; Routing policy compatibility; validate |
| n8n-as-code | inert | build and test; security review |
| n8n-manager | inert | build and test; security review |
| orchestrator | inert | Quality; Runner consumer compatibility |
| project-standards | inert | Lint, type-check, and test |
| rea | inert | build and test; security review |
| rtk | inert | build and test; security review |
| sds-canary | inert | test |
| security-standards | inert | Lint, type-check, and test; scan |

- Inert members: 12 (all but brain and change-manager). Deploying (redeploys): 2.
- Members with zero required checks: **none**. Fewest is one (change-manager, factory-runner,
  project-standards, sds-canary). Every other member has two or three.
- Multi-check members (2+ required): 10 of 14, so Task 4's one-row-per-check case is the common case.
- `sds-canary` was added 2026-10-30 (`dependency_updates` granted, `app_changes` not assessed).
- `dependency_updates` is `granted` for all 14. `app_changes` is granted only for brain, change-manager,
  infraops-mcp-server, intent-packages, orchestrator, project-standards, security-standards.

## Step 3: generator replay for records 170 and 171

Records (production, read by SQL on `work_package_revisions` and by `GET /api/v1/package-intakes/{id}`):

| Record | Revision id | Package | Rev | Content hash | Source commit |
|---|---|---|---|---|---|
| 170 | f962475a-1964-46df-81ac-2b3029dec2a7 | orchestrator-uv-httpx2 | 1 | fb75ba36f216... | 8194bfca9cde... |
| 171 | 117359a0-7182-4d32-ab95-a24f2dfb85a6 | orchestrator-uv-sqlalchemy | 1 | 8cff5af7249a... | 8194bfca9cde... |

(`f962475a...` and `117359a0...` are the revision UUID prefixes, not content hashes.)

`profile_fields` is NOT in the intake API response (the response carries `authority`, `enforcement_snapshot`,
`acceptance_criteria`, not the package body). It was read from the package file at the registered
`source_commit`: `git show 8194bfca:packages/orchestrator-uv-<pkg>/package.yaml`.

- 170: `target_repo AlobarQuest/orchestrator`, `package httpx2`, `from 2.13.1`, `to 2.16.0`, `standing true`.
- 171: `target_repo AlobarQuest/orchestrator`, `package sqlalchemy`, `from 2.0.54`, `to 2.1.6`, `standing true`.

The registered intake's `authority` is bare (`conformance` null, `constraints` {}, `capabilities` {},
`change_class` null, `unknown_fields` allowed/prohibited/requires_approval). The runner-shaped envelope
comes from the decomposition proposal, not the intake. Both enforcement snapshots declare reach
`["source_repository"]`.

Replay: fresh `git clone --depth 1` of orchestrator (`4fff334`), intent-packages worktree at `origin/main`
with its own venv, `discover_pin_sites` then `decompose.build_proposal(...)` (which calls `build_envelope`)
with the dry run skipped. One pin site found for each. Conformance: `orchestrator conformance-claim <clone>
--json` **errored** (`scanner_unavailable`: `portfolio.compliance` is not importable), so a placeholder
was used, the shape the pinned fixture carries: `{"accepted_standards": [], "standards_touched":
["project"], "status": "green"}`.

Generated envelopes (identical except the package):

- budgets `{max_attempts 3, max_llm_calls 360}`; change_class `dependency-update`; capabilities
  command.run, github.pr.create, github.pr.merge, orchestrator.claim, orchestrator.evidence.write,
  repo.edit, repo.read all `allowed`; target_repository `AlobarQuest/orchestrator`.
- 170: `allowed_commands` = [`uv add --dev 'httpx2>=2.16.0'`, `uv lock --check`]; `mutation_commands` = the
  first; `verify_commands` = [`uv lock --check`]. (The pin is in the dev group, hence `--dev`.)
- 171: `allowed_commands` = [`uv add 'sqlalchemy>=2.1.6'`, `uv lock --check`]; same split.

Recognition: loaded with `load_factory_policy()` (policy version 9), called as the gate does:
`policy.authority_refusals(reach_from_snapshot(snapshot), normalize_authority(envelope), unit_id,
rotation_unit=False)`. (`known_good_recognition` named in the brief does not exist; `authority_refusals`
is the entry point `authority_gate.human_authority_gate` uses, and `_recognises` is its matcher.)

- Both envelopes: refusals `()`, recognised by `uv dependency pin bump` under `reach.source_repository`.
- **The envelope as the generator emits it is NOT recognised**: without `constraints.work_unit_id` both draw
  `authority_envelope_novel`, because `_constraints_recognised` requires `work_unit_id == str(unit_id)` and
  the orchestrator stamps it at unit registration. The generator never emits it. A Task 2 predicate that
  tests the proposal envelope must stamp it first, or test the stored unit's envelope.
- Controls (same stamped envelope, to show the check discriminates): `max_llm_calls` raised to 1,000,000
  draws `authority_envelope_novel`; an extra command `rm -rf /` appended to `allowed_commands` draws
  `authority_envelope_novel`. So a recognition is not a vacuous pass.

Cleanup: intent-packages worktree removed (`worktree list` shows only main, `status` clean); scratch clone
deleted.

## Step 4: where the brain keys live

`resolve_brain_key` (`intent_packages/factory/brains.py`): env var first (`CODE_BRAIN_KEY`, `INFRA_BRAIN_KEY`,
derived as `<NAME>_BRAIN_KEY`), else `bws secret get <uuid>` using `BWS_ACCESS_TOKEN` via `fetch_bws_secret`.
The UUID is selected by the `brain = "code" | "infra"` field of intent-packages' `.bws-secrets.toml`, not by name.

| Env var | BWS name | UUID | Project |
|---|---|---|---|
| CODE_BRAIN_KEY | CODE_BRAIN_CONTRIBUTOR_KEY | 750f737f-4cb6-4876-9a98-b48200ea1c0b | brains |
| INFRA_BRAIN_KEY | INFRA_BRAIN_CONTRIBUTOR_KEY | da8134b0-565f-45c8-8965-b48200ea1c40 | brains |

They are the propose-only contributor keys (the brains' auth middleware accepts approver or contributor key;
no read-only brain key exists for code or infra). Also in `brains`: `APP_BRAIN_READ_KEY`
(726a18ba-7a38-4ecc-aa03-b49a015fd302), read by `scripts/run-revision-watcher.sh` through the broad
identity.

Which identity can read them: **documented, not probed.** This repository's `.bws-secrets.toml` says of
`APP_BRAIN_READ_KEY` that `brains` "only the broad machine account can read", and the revision watcher reads
it with its `BROAD_IDENTITY`. The narrow read-only `sds-operator` identity (Keychain `BWS_ACCESS_TOKEN_SDS`,
the one `sds-token.sh` loads) is documented as unable to read `Ops / Platform`; for `brains` the same
should be assumed. I did not attempt a fresh-HOME read of the two brain keys (cached-session pitfall,
and it would pull a key value into a process for no need). A launcher that needs them must use the broad
identity, or Task N must probe each UUID under `env -i`.

## Live proof, first units (2026-10-30 to 2026-10-31 EDT)

| Time | Step | Actor |
|---|---|---|
| 23:50 | Records 170 and 171: proposals approved by policy; httpx2 unit dispatched, sqlalchemy unit `busy` | `bump-driver` (SYSTEM) |
| 23:50 | Runner opened #563 (`sds/2ad51d97-attempt-1`) | factory-runner |
| 23:58 | Quality failed on #563: `tests/web/test_csrf.py::test_csrf_is_secure_bound_and_expires`, 1 failed, 12060 passed | CI |
| 00:50 | httpx2 unit reported `failed`; alert `bump-unit-failed` raised, nothing requeued | `bump-driver` |
| 00:50 | sqlalchemy unit dispatched; runner opened #564 | `bump-driver`, factory-runner |

Differential for #563: the test passes on httpx2 2.13.1 and fails on 2.16.0. In 2.16.0 a client holding two
cookies of one name sends the later-set one first, so the server reads the original and the fixture's
attacker cookie has no effect. The application is unchanged; the test's cookie replacement is what broke.
| 00:58 | Quality failed on #564: 11 pyright errors in `graduation_ledger.py` and `traceability.py` | CI |
| 01:50 | sqlalchemy unit reported `failed`; both units held, nothing requeued | `bump-driver` |

Cause for #564: SQLAlchemy 2.1 changes `Select`'s type parameters, so `Select[tuple[UUID, int]]` annotations in
repo code no longer type-check. Both bumps need a code change, which a dependency-update unit has no authority
to make.

Result: Step 5's second branch. Both records were proposed, approved by policy and dispatched with no person,
and each failure ended in the alert with the bump held. No unit reached the verified, merged and watched steps
live; those steps have test coverage only.

A factory bump starts from a Dependabot pull request that failed its gate, and both of these failed for reasons
the unit can't fix. A unit lands only when re-running the bump clears that failure.
