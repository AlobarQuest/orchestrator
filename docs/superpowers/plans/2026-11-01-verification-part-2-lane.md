# Verification Part 2: the lane covers every factory unit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Every submitted factory unit is verified without a person starting it.

**Spec:** `docs/superpowers/specs/2026-11-01-verification-layer-design.md`, Part 2. Devon's answers: each `automated_check` criterion gets every required check, and a bump's behavior doesn't change.

## Global Constraints

- Select a factory unit with a dispatch and an armed head. Exclude `required_capability == post_deploy_verification`.
- The rename is cosmetic: `orchestrator-verifier`, the VERIFIER bearer, and its BWS id stay.
- Non-bump units read `GET /api/v1/members` first. An unread record is not a withdrawal. Rotation units skip it.
- Human criteria never resolve by machine.

## Review Focus

- A second `automated_check` criterion doesn't replay the first's evidence.
- A unit with no `automated_check` goes straight to `/verify`, and `automated_test` evaluates or goes to a person.
- A repository with no required check reaches a person, and never passes.
- `image_release` keeps its post-deploy units.
- A session unit with no dispatch is skipped without a `KeyError`.

---

### Task 1: The widened lane

- [ ] Write failing tests for each Review Focus line, using seeded units.
- [ ] Add required `automated_check` ids to `VerifiableUnitResponse`, in the same change as the service. Add the criterion id to the evidence key.
- [ ] Widen selection, and generalize `_file_bump_checks` to file every required check under each criterion. Add the grant read, and update `permits`, `test_client_surface`, and the docstring.
- [ ] Run `make check`. Commit. Merge, deploy, and prove rotations and bumps still pass through the lane.

### Task 2: The rename

- [ ] Create check `sds-unit-verifier`. Add `run-unit-verifier.sh` and its plist beside the old ones, with the launcher registered as a reader of `BWS_ACCESS_TOKEN_SDS`.
- [ ] Merge, swap the LaunchAgents, and prove one pass pings the new check.
- [ ] Delete the old launcher, plist, and check. Rename the package and docs. Merge, and prove the next pass.
