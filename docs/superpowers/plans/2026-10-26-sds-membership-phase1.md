# SDS membership, phase 1: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the SDS one member record in the orchestrator database, with per-service grants that
only a human can approve, seed it with today's members, and move the four hand-written scope lists
and a consistency check onto it.

**Architecture:** Changes to the record arrive as *member proposals*: the SYSTEM role stages one
through `POST /api/v1/member-proposals`, and only a human confirming it on
`/review/member-proposals/{id}` applies it, which mirrors the staged-intake pattern (migration
`0040_staged_package_intakes`, #365). Any authenticated role reads the record through
`GET /api/v1/members`. A new client package, `src/sds_members/`, reads the record and computes
populations; `scripts/sds_scope.py` prints them for launchers, and a new daily lane checks the record
against `factory-target.toml`, the landing policy and the per-repository configuration lists.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy, Alembic, Postgres, pydantic, Typer, httpx through
`estate_clients.confined.ConfinedClient`, bash launchers under launchd.

**Spec:** `docs/superpowers/specs/2026-10-26-sds-membership-design.md` (approved 2026-10-26, merged
in #521). Read it before starting; this plan argues from it.

## Global Constraints

- Every invariant in `CLAUDE.md` applies. The ones this plan meets most often are as follows:
  - Request entry points own their transaction and `session.commit()`; services never commit.
  - A service rejects every value a DB CHECK would, as a `DomainError`, before the write.
  - CHECK vocabularies are joined explicitly (`", ".join(f"'{v}'" for v in VOCAB)`), identically in
    the model and the migration, never `f"{VOCAB!r}"`.
  - Alembic revision ids are at most 32 characters.
  - New routes join the POST/GET inventories in `tests/architecture/test_scope_guards.py`. A new
    POST joins `tests/idempotency/matrix.py` or `NON_INGRESS_POST_ROUTES`.
  - Files importing an HTTP client join `OUTBOUND_ALLOWLIST`.
  - Module-level string collections are registered in `VOCABULARY_REGISTRY` or marked
    `# not-a-vocabulary: <reason>`.
  - Every new file is placed in `layers.toml`.
  - New LaunchAgent installers source `scripts/sds-install.sh` and call
    `sds_refuse_linked_worktree`.
- **Word guards:** under `src/orchestrator/`, code may not contain `deploy` or `dispatch` (ws32), nor
  `coolify` (ws32 and ws34). The deploys service's wire value is therefore **`rollouts`** everywhere,
  in the orchestrator and in clients alike. Its human label "Deploys" appears only in the Jinja
  template and in docs.
- **How a grant is approved:** the spec's "Calls made" says grants are approvals in the existing
  `/review` model. `record_approval` accepts only work units, so this plan uses the staged-intake
  form of a `/review` approval (a staged record a named human confirms) instead of an `Approval`
  row. Devon was told this when the plan was handed over.
- **Service vocabulary (wire values, in this order):** `dependency_updates`, `security_patches`,
  `app_changes`, `backups`, `monitoring`, `rollouts`, `credential_rotation`.
- **Member states:** `onboarding`, `member`, `retired`.
- **Grant states:** `not_assessed`, `remediating`, `granted`, `not_applicable`, `withdrawn`.
- **Proposal states:** `staged`, `confirmed`, `withdrawn`.
- **Repository key:** `owner/name` as GitHub spells it, matched case-insensitively. It must match
  `^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$`.
- **No lane enrolls a repository** (spec anti-goal 1). Nothing but a human-confirmed proposal writes
  the member tables.
- **No lane silently loses coverage** (anti-goal 2). Every lane move in this plan has a parity step:
  the old list and the new population must be equal before the launcher switches.
- Work in a worktree with its own venv and test database (`docs/operations/local-development.md`).
  The lanes run the main tree.
- Production changes only through `scripts/deploy_orchestrator.py`, which builds the image and runs
  `alembic upgrade head`.

## Review Focus

1. **Case drift in repository names.** A proposal naming `alobarquest/Brain` for the existing member
   `AlobarQuest/brain` must update that member, never create a second one. Owner: Task 2 and Task 3.
2. **A proposal confirmed after the record changed under it.** Confirmation re-validates against the
   record as it is at confirmation time, and applies all of the proposal or none of it. Owner: Task 3.
3. **An unreadable or empty record at lane time.** Every population read that fails, or comes back
   empty, exits 3 and prints the reason. It never hands a launcher a shorter list. Owner: Task 6.
4. **Revision-watcher subject names.** Stored observations key on the subject name, so the seed must
   carry today's six names byte for byte, or each write becomes `observation_conflict`. Owner: Task 7
   and Task 9.
5. **A retired member still named in a configuration list.** The consistency check reports it,
   because `retired` members are excluded from every population. Owner: Task 11.

---

## Part A: the record in the orchestrator (one pull request)

### Task 1: Tables, vocabularies, and migration

**Files:**
- Modify: `src/orchestrator/persistence/models.py` (append after `StagedPackageIntake`)
- Create: `migrations/versions/0042_sds_members.py`
- Test: `tests/persistence/test_migrations.py` (add a test next to the staged-intake one near line 961)

**Interfaces:**
- Produces, in `persistence/models.py`:
  - `MEMBER_SERVICES: tuple[str, ...]`, `MEMBER_STATES`, `GRANT_STATES`, `MEMBER_PROPOSAL_STATES`,
    holding the Global Constraints values in that order. CHECKs reference them, so they need no
    `VOCABULARY_REGISTRY` entry.
  - `SdsMember`, table `sds_members`: `id` UUID pk, `repository` text not null, `owner` text not
    null, `state` text, `working_copy` text null (the directory name under the operator's
    `~/Projects`), `machine_local` bool not null default false, `created_at`, `updated_at`,
    `state_changed_by` text not null, `state_reason` text not null. Unique index on
    `lower(repository)`. CHECKs: state vocabulary; `machine_local` implies `working_copy IS NOT
    NULL`; `owner <> ''`; `state_reason <> ''`.
  - `SdsMemberGrant`, table `sds_member_grants`: `member_id` FK, `service` text, `state` text,
    `seeded` bool not null, `set_by` text not null, `reason` text not null, `set_at`. Primary key
    `(member_id, service)`. CHECKs: service and state vocabularies; `reason <> ''`.
  - `SdsMemberApp`, table `sds_member_apps`: `member_id` FK, `name` text, `health_url` text,
    `branch` text not null default `'main'`. Primary key `(member_id, name)`; unique `name` across
    the table, because revision-watcher subjects are keyed by name alone.
  - `SdsMemberProposal`, table `sds_member_proposals`: `id` UUID pk, `idempotency_key` text unique,
    `payload` JSONB not null, `payload_digest` text not null, `state` text, `staged_by`, `staged_at`,
    `decided_by` null, `decided_at` null, `decision_reason` null. CHECKs: state vocabulary;
    `(state = 'staged') = (decided_by IS NULL AND decided_at IS NULL)`.

- [ ] **Step 1: Write the failing migration test**

`test_0042_creates_the_member_tables_and_refuses_bad_values` upgrades to `0042_sds_members`, then
asserts the following, each insert in its own savepoint:
- the four tables exist;
- `INSERT` of a member with `state = 'active'` raises `IntegrityError`;
- `machine_local = true` with `working_copy = NULL` raises;
- a second member `alobarquest/BRAIN` beside `AlobarQuest/brain` raises (case-insensitive unique);
- a grant with `service = 'deploys'` raises (the wire value is `rollouts`);
- `downgrade()` to `0041_rotation_proposer_obs` drops all four tables.

Run the migration tests against Postgres, as the suite already does. SQLite proves nothing about
these constraints.

- [ ] **Step 2: Run it and see it fail**

Run: `.venv/bin/pytest tests/persistence/test_migrations.py -k 0042 -v`
Expected: FAIL, revision `0042_sds_members` not found.

- [ ] **Step 3: Add the models and `migrations/versions/0042_sds_members.py`**

Use `revision = "0042_sds_members"` and `down_revision = "0041_rotation_proposer_obs"`. Before you
write it, confirm with `.venv/bin/alembic heads` that `0041` is still the head, and renumber if not.
The migration pins its own copy of each vocabulary rather than importing the models.

- [ ] **Step 4: Run it and see it pass**

Run: `.venv/bin/pytest tests/persistence/test_migrations.py -k 0042 -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/persistence/models.py migrations/versions/0042_sds_members.py tests/persistence/test_migrations.py
git commit -m "feat(membership): member, grant, app and proposal tables"
```

### Task 2: Staging a proposal (SYSTEM only)

**Files:**
- Create: `src/orchestrator/api/schemas/membership.py`
- Create: `src/orchestrator/services/membership/__init__.py`, `src/orchestrator/services/membership/proposals.py`
- Create: `src/orchestrator/api/routes/membership.py`; register its router in `API_ROUTERS` (`src/orchestrator/main.py:38`)
- Modify: `tests/architecture/test_scope_guards.py` (POST inventory), `tests/idempotency/matrix.py`
- Test: `tests/services/test_member_proposals.py`, `tests/api/test_membership_api.py`

**Interfaces:**
- Consumes: Task 1's models and vocabularies.
- Produces, in `api/schemas/membership.py` (all `extra="forbid"`):
  - `MemberApp(name: str, health_url: HttpUrl, branch: str = "main")`.
  - `GrantChange(state: Literal[...grant states], reason: str)`.
  - `MemberChange(repository: str, reason: str, state: Literal[...member states] | None = None,
    owner: str | None = None, working_copy: str | None = None, machine_local: bool | None = None,
    apps: list[MemberApp] | None = None, grants: dict[Literal[...services], GrantChange] = {})`.
    `None` means unchanged; `apps`, when given, replaces the member's whole app list.
  - `MemberProposalBody(summary: str, seeded: bool = False, changes: list[MemberChange])`, with
    1 to 100 changes.
  - `MemberProposalResponse(id: UUID, state: str, review_path: str, staged_by: str, staged_at:
    datetime)`. `review_path` is `/review/member-proposals/{id}`.
- Produces, in `services/membership/proposals.py`:
  - `stage_member_proposal(session, *, actor: ActorContext, body: MemberProposalBody,
    idempotency_key: str) -> SdsMemberProposal`.
  - `validate_changes(session, changes: list[MemberChange]) -> None`, which raises `DomainError`.
    Task 3 calls it again at confirmation.
- Route: `POST /api/v1/member-proposals` with an `Idempotency-Key` header, the way
  `/api/v1/staged-intakes` takes its key. It returns 201 with `MemberProposalResponse`.

**Validation and its error codes.** `validate_changes` checks the following and raises `DomainError`
with these codes:

| Condition | Code |
|---|---|
| A repository that fails the Global Constraints pattern | `member_repository_invalid` |
| Two changes whose repositories are equal ignoring case | `member_change_duplicated` |
| A change for an unknown repository without both `state` and `owner` | `member_creation_incomplete` |
| A blank `reason`, `summary`, `owner` or grant reason | `member_text_blank` |
| `machine_local = true` with no `working_copy` after the change applies | `member_working_copy_required` |
| A grant set to `withdrawn` whose current state isn't `granted` | `grant_withdrawal_invalid` |
| An app name already used by another member | `member_app_name_taken` |

A new member's unnamed services start as `not_assessed`.

- [ ] **Step 1: Write the failing service tests**

In `tests/services/test_member_proposals.py`, add the following tests:
- `test_system_stages_a_proposal`: the row is `staged`, `staged_by == "system"`, and nothing has
  been written to `sds_members`.
- `test_only_system_may_stage`: parametrized over WORKER, VERIFIER, OBSERVER and HUMAN, each raises
  `DomainError` with code `role_forbidden`.
- `test_same_key_same_body_replays` and `test_same_key_different_body_conflicts` (code
  `idempotency_conflict`).
- One test per row of the validation table, asserting its code.
- `test_a_change_differing_only_in_case_targets_the_existing_member`: with `AlobarQuest/brain`
  present, a change for `alobarquest/BRAIN` with no `state` and no `owner` validates without error
  (Review Focus 1).

- [ ] **Step 2: Run them and see them fail**

Run: `.venv/bin/pytest tests/services/test_member_proposals.py -v`
Expected: FAIL on import of `orchestrator.services.membership.proposals`.

- [ ] **Step 3: Implement the schemas, `validate_changes` and `stage_member_proposal`**

Take the advisory lock on the idempotency key, as `staged_intake.py:79` does. `payload_digest` is the
sha256 of the canonical JSON of `body.model_dump(mode="json")`, and replay compares digests.

- [ ] **Step 4: Run them and see them pass**

Run: `.venv/bin/pytest tests/services/test_member_proposals.py -v`
Expected: PASS.

- [ ] **Step 5: Add the route, its API tests and the guard entries**

`tests/api/test_membership_api.py::test_system_stages_through_the_api` posts with the `SYSTEM`
headers and expects 201 with `review_path`. `test_observer_cannot_stage` expects 403
`role_forbidden`. Add `"/api/v1/member-proposals",` to the POST inventory. Add a `MatrixRow` with
mechanism `ADVISORY_LOCK` naming the replay test.

Run: `.venv/bin/pytest tests/api/test_membership_api.py tests/architecture/test_scope_guards.py tests/idempotency -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A src/orchestrator tests
git commit -m "feat(membership): SYSTEM stages member proposals"
```

### Task 3: Confirming or withdrawing a proposal (human only)

**Files:**
- Modify: `src/orchestrator/services/membership/proposals.py`
- Modify: `src/orchestrator/web.py` (next to the staged-intake routes, around line 689)
- Create: `src/orchestrator/templates/member_proposal.html`
- Modify: `tests/architecture/test_scope_guards.py`, `tests/idempotency/test_matrix.py` (`NON_INGRESS_POST_ROUTES`)
- Test: `tests/services/test_member_proposals.py`, `tests/web/test_member_proposals.py`

**Interfaces:**
- Consumes: `validate_changes`, `SdsMemberProposal`, and `MemberChange`, rebuilt from the stored
  payload with `MemberProposalBody.model_validate`.
- Produces:
  - `confirm_member_proposal(session, *, actor: ActorContext, proposal_id: UUID, expected_digest:
    str) -> SdsMemberProposal`.
  - `withdraw_member_proposal(session, *, actor: ActorContext, proposal_id: UUID, reason: str) ->
    SdsMemberProposal`.
  - Pages and forms: `GET /review/member-proposals/{proposal_id}`, plus
    `POST /review/member-proposals/{proposal_id}/confirm` and `.../withdraw`. The CSRF action names
    are `_CONFIRM_MEMBER_PROPOSAL = "confirm_member_proposal"` and
    `_WITHDRAW_MEMBER_PROPOSAL = "withdraw_member_proposal"`.

**Behavior:**
- Both service functions require `ActorRole.HUMAN` (`human_actor_required`) and a `staged` proposal
  (`member_proposal_not_staged`). They lock the proposal row `FOR UPDATE`.
- Confirmation takes `expected_digest` from the form, so a person confirms the payload they read. A
  mismatch raises `member_proposal_changed`.
- Confirmation re-runs `validate_changes` against the current record, then applies every change in
  one transaction:
  - A missing member is created. Any grant a change names is written with `seeded = payload.seeded`
    and `set_by = actor.actor_id`.
  - An `apps` list replaces the member's apps.
- Each applied change writes one `Event` with `subject_type="sds_member"`, `actor_id` the human, and
  an action of `member.created` or `member.changed`. The event detail lists the fields and grants
  that moved, each with its old and new value.
- The page renders every change as a table: repository, state, owner, links, apps, and one column per
  service. The service labels are Dependency updates, Security patches, App changes, Backups,
  Monitoring, Deploys and Credential rotation, written in the template only. A cell shows the old
  state, then an arrow, then the new state, plus the reason. The page carries the digest in a hidden
  field.

- [ ] **Step 1: Write the failing service tests**

- `test_human_confirmation_applies_every_change_and_records_who`: rows exist, the grants carry
  `set_by == "devon"`, and one `Event` exists per change.
- `test_system_cannot_confirm` (`human_actor_required`).
- `test_confirming_twice_is_refused` (`member_proposal_not_staged`).
- `test_a_changed_digest_is_refused` (`member_proposal_changed`).
- `test_confirmation_revalidates_and_applies_nothing_on_failure`: stage two proposals that create
  the same repository, and confirm the first. Confirming the second then raises
  `member_change_duplicated` or `member_creation_incomplete`, whichever validation reports, and
  every table row count is unchanged (Review Focus 2).
- `test_withdrawal_needs_a_reason_and_writes_nothing_to_the_record`.

- [ ] **Step 2: Run them and see them fail**

Run: `.venv/bin/pytest tests/services/test_member_proposals.py -k "confirm or withdraw" -v`
Expected: FAIL, functions not defined.

- [ ] **Step 3: Implement both functions**

- [ ] **Step 4: Run them and see them pass**

Run: `.venv/bin/pytest tests/services/test_member_proposals.py -v`
Expected: PASS.

- [ ] **Step 5: Add the pages, the template and their web tests**

`tests/web/test_member_proposals.py` mirrors `tests/web/test_staged_intake.py`:
- `test_human_confirms_from_the_page` (GET, extract `csrf_token`, `idempotency_key` and the digest,
  POST with `confirm=yes`, expect 303, rows exist).
- `test_page_shows_deploys_label_for_rollouts`.
- `test_machine_actor_gets_403_on_the_page`.

Add both POST routes to the inventory. Add both to `NON_INGRESS_POST_ROUTES`, with a comment naming
the confirm-twice test as the replay proof.

Run: `.venv/bin/pytest tests/web/test_member_proposals.py tests/architecture/test_scope_guards.py tests/idempotency -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A src/orchestrator tests
git commit -m "feat(membership): a human confirms or withdraws member proposals on /review"
```

### Task 4: Reading the record, the write-split guard, and documentation

**Files:**
- Create: `src/orchestrator/services/membership/members.py`
- Modify: `src/orchestrator/api/routes/membership.py`, `src/orchestrator/api/schemas/membership.py`
- Create: `tests/architecture/test_membership_writers.py`
- Modify: `tests/architecture/test_scope_guards.py` (GET inventory), `layers.toml`, `CLAUDE.md`
- Create: `docs/operations/membership.md`, `docs/decisions/0058-sds-membership-is-one-record-with-per-service-grants.md`
- Test: `tests/api/test_membership_api.py`

**Interfaces:**
- Produces:
  - `list_members(session) -> list[SdsMember]`, every member including retired, with grants and
    apps eagerly loaded, ordered by `lower(repository)`.
  - `GET /api/v1/members` returns `MembersResponse(members: list[MemberView])`.
    - `MemberView(repository, owner, state, working_copy, machine_local, apps: list[MemberApp],
      grants: dict[str, GrantView])` carries all seven services.
    - `GrantView(state, seeded: bool, set_by: str, set_at: datetime, reason: str)`.
  - Any authenticated role may read, OBSERVER included.

- [ ] **Step 1: Write the failing tests**

- `tests/api/test_membership_api.py::test_observer_reads_every_member_with_seven_grants` pins the
  field names of `MemberView` and `GrantView`, per the `response_model` invariant.
- `tests/architecture/test_membership_writers.py::test_only_the_proposal_service_writes_member_tables`
  parses every module under `src/orchestrator/` with `ast`. It asserts that the names `SdsMember`,
  `SdsMemberGrant`, `SdsMemberApp` and `SdsMemberProposal` are referenced only in the following
  three modules, and that only `proposals.py` constructs, adds to the session, or assigns attributes
  of the first three:
  - `persistence/models.py`
  - `services/membership/proposals.py`
  - `services/membership/members.py`
- `test_membership_write_routes_are_exactly_these`: the routes whose endpoints reach
  `stage_member_proposal`, `confirm_member_proposal` or `withdraw_member_proposal` are exactly
  `{"/api/v1/member-proposals": SYSTEM, "/review/member-proposals/{proposal_id}/confirm": HUMAN,
  "/review/member-proposals/{proposal_id}/withdraw": HUMAN}`. Find them the way
  `test_unreachable_guards.py` resolves endpoints to callees.
- Prove the guard fires: temporarily add a `session.add(SdsMember(...))` in another service, run the
  test, see it fail, and remove the line. Clear `__pycache__` around the mutation.

- [ ] **Step 2: Run them and see them fail**

Run: `.venv/bin/pytest tests/api/test_membership_api.py tests/architecture/test_membership_writers.py -v`
Expected: FAIL; the route doesn't exist yet.

- [ ] **Step 3: Implement `list_members` and the GET route; add `"/api/v1/members"` to the GET inventory**

- [ ] **Step 4: Place the new files and write the documentation**

- In `layers.toml`, add a `[membership]` layer holding every new file from Tasks 1 through 4 except
  `models.py`, the migration, `web.py` and templates, which `[platform]` already covers.
- `docs/operations/membership.md` covers how to add a member ("now add this one"), how to change a
  grant, and where to confirm. Run `sds-members stage <file.toml>`, then confirm the printed
  `/review` page. It also lists the services with their wire values and notes that `rollouts` is
  Deploys.
- ADR-0058 records the decision in about a page and points to the spec for the detail.
- Add one invariant bullet to `CLAUDE.md`, below the managed block:

  > Only a human confirming a member proposal on `/review` writes the member tables
  > (`services/membership/proposals.py`), and `test_membership_writers.py` names every route that
  > may write them and the role each requires. The deploys service's wire value is
  > `rollouts` (word guards). Detail: `docs/operations/membership.md`.

- [ ] **Step 5: Run the full gate**

Run: `rtk proxy make check > /tmp/check.log 2>&1; grep -E "passed|failed|error" /tmp/check.log | tail -5`
Expected: all passed, 0 failed. Read the collected-test count.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(membership): read the record; guard the write split; ADR-0058"
```

### Task 5: Ship part A

- [ ] **Step 1:** Run `/code-review` on the branch diff and fix every confirmed finding.
- [ ] **Step 2:** Push, open the pull request, and wait for checks with `gh pr checks <n> --watch --interval 30`. Merge when green, and confirm the state is `MERGED`.
- [ ] **Step 3:** Deploy with `scripts/deploy_orchestrator.py` (see `docs/operations/deploy.md`). It migrates before the swap.
- [ ] **Step 4:** Verify:
  - `https://sds.alobar.net/openapi.json` lists `/api/v1/members` and `/api/v1/member-proposals`.
  - `alembic current` equals `heads`.
  - A GET of `/api/v1/members` with the observer bearer returns `{"members": []}`.

---

## Part B: clients, seed and lanes (a second pull request, merged only after the seed is confirmed)

### Task 6: The `sds_members` client package and `scripts/sds_scope.py`

**Files:**
- Create: `src/sds_members/__init__.py`, `client.py`, `populations.py`, `cli.py`
- Modify: `pyproject.toml` (`[project.scripts]`: `sds-members = "sds_members.cli:app"`)
- Rewrite: `scripts/sds_scope.py`
- Modify: `OUTBOUND_ALLOWLIST` in `tests/architecture/test_wsp21_invariant_scan.py`, `layers.toml`
- Test: `tests/sds_members/test_client.py`, `tests/sds_members/test_populations.py`, `tests/scripts/test_sds_scope.py`

**Interfaces:**
- Produces, in `client.py`:
  - `@dataclass(frozen=True) Member(repository, owner, state, working_copy: str | None,
    machine_local: bool, apps: tuple[App, ...], grants: Mapping[str, str])`, where `grants` maps
    each service to its state.
  - `App(name, health_url, branch)`.
  - `class MembersUnreadable(Exception)`.
  - `read_members(*, token: str, credential_key_id: str, base_url: str = "https://sds.alobar.net",
    transport=None) -> list[Member]`. It uses `ConfinedClient`, permitting only `GET
    /api/v1/members`, with User-Agent `sds-members/1 (+AlobarQuest/orchestrator)`. Any transport
    error, non-2xx status, or malformed body raises `MembersUnreadable`. Catch `(httpx.HTTPError,
    httpx.InvalidURL, ValueError)`.
  - `stage(*, token, credential_key_id, body: dict, idempotency_key: str, base_url=...) -> dict`,
    a POST to `/api/v1/member-proposals`. It is the only other permitted route.
- Produces, in `populations.py` (pure functions; `retired` members excluded from every one):
  - `observed(members) -> list[str]`: repositories of members in `onboarding` or `member`.
  - `working_copies(members) -> list[str]`: `working_copy` values.
  - `bindable(members) -> list[str]`: `working_copy` of members with `machine_local` true and
    `rollouts` granted.
  - `granted(members, service: str) -> list[str]`: repositories. An unknown service raises
    `ValueError`.
  - `app_subjects(members) -> list[tuple[str, App]]`: pairs of repository and app.
- `cli.py` (Typer):
  - `sds-members stage <file.toml>` reads `SDS_MEMBERS_SYSTEM_TOKEN` and prints the proposal id and
    `https://sds.alobar.net/review/member-proposals/<id>`. The key id is `orchestrator-system`.
  - `sds-members check` is added in Task 11.
- `scripts/sds_scope.py <population> [service]`, where `population` is one of `ledger` (prints
  `observed`), `checkouts` (prints `working_copies`), `bindable` or `granted` (prints `granted`
  for the named service). It reads `SDS_SCOPE_ORCHESTRATOR_TOKEN`, with key id
  `orchestrator-observer`. It prints one value per line. It exits 0, or exits 3 with
  `sds-scope: <reason>` on stderr when the record is unreadable, the token is unset, or the
  population is empty.

The old `factory_targets`, `canonical` and `ledger_scope` functions move to
`src/sds_members/consistency.py` in Task 11, and their tests move with them. Delete them from
`sds_scope.py` here, and keep a copy of their tests for Task 11.

- [ ] **Step 1: Write the failing tests**

- `test_populations.py`: one test per function over a fixture of four members (one retired, one
  `onboarding`). Include `test_retired_members_appear_in_no_population` and
  `test_bindable_needs_both_machine_local_and_rollouts_granted`.
- `test_client.py`, using `httpx.MockTransport`:
  - `test_reads_members`
  - `test_non_2xx_is_unreadable`
  - `test_malformed_body_is_unreadable`
  - `test_refuses_any_other_route` (a `ConfinedClient` refusal)
- `test_sds_scope.py`:
  - `test_unreadable_record_exits_3_and_prints_nothing_on_stdout`
  - `test_empty_population_exits_3`
  - `test_unset_token_exits_3`
  - `test_granted_needs_a_known_service`

These cover Review Focus 3.

- [ ] **Step 2: Run them and see them fail**

Run: `.venv/bin/pytest tests/sds_members tests/scripts/test_sds_scope.py -v`
Expected: FAIL on imports.

- [ ] **Step 3: Implement the package and rewrite `sds_scope.py`**

- [ ] **Step 4: Run them and see them pass, then run `.venv/bin/pytest tests/architecture -q`**

Expected: PASS, including the outbound and vocabulary scans.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(sds-members): a client and populations over the member record"
```

### Task 7: The seed, staged and confirmed

**Files:**
- Create: `docs/operations/membership-seed.toml` (a `MemberProposalBody` in TOML, `seeded = true`)
- Test: `tests/sds_members/test_seed.py`

The seed holds the following 13 members, all with owner `devon` and state `member`. Spell each
repository the way GitHub's `full_name` does; `gh api repos/AlobarQuest/<name> -q .full_name` is
authoritative.

| Repository | Grants (all others `not_assessed`) | Links |
|---|---|---|
| `AlobarQuest/brain` | app_changes, dependency_updates, rollouts | working_copy `brain`; apps `infra-brain`, `open-brain`, `app-brain`, `code-brain` |
| `AlobarQuest/change-manager` | app_changes, dependency_updates, rollouts, credential_rotation | working_copy `change-manager`; app `change-manager` |
| `AlobarQuest/orchestrator` | app_changes, dependency_updates, rollouts, credential_rotation | working_copy `orchestrator`, machine_local; app `orchestrator` |
| `AlobarQuest/infraops-mcp-server` | app_changes, dependency_updates, rollouts, credential_rotation | working_copy `infraops-mcp-server`, machine_local |
| `AlobarQuest/intent-packages` | app_changes, dependency_updates, rollouts | working_copy `intent-packages`, machine_local |
| `AlobarQuest/security-standards` | app_changes, dependency_updates, rollouts | working_copy `security-standards`, machine_local |
| `AlobarQuest/factory-runner` | dependency_updates, credential_rotation; app_changes `remediating` | none |
| `AlobarQuest/project-standards` | dependency_updates; app_changes `remediating` | none |
| `AlobarQuest/rtk` | dependency_updates, security_patches, rollouts | none |
| `AlobarQuest/claude-octopus` | dependency_updates, security_patches, rollouts | none |
| `AlobarQuest/rea` | dependency_updates, security_patches, rollouts | none |
| `AlobarQuest/n8n-as-code` | dependency_updates, security_patches, rollouts | none |
| `AlobarQuest/n8n-manager` | dependency_updates, security_patches | none |

The six apps take their `name`, `health_url` and `branch` byte for byte from
`src/revision_watcher/subjects.py:49-81` (Review Focus 4). Every grant needs a reason that names its
source, for example "factory-target.toml declares factory_target = true" or "in tool_installer's
TOOLS". The `remediating` reason is "Devon decided 2026-10-26 (membership design); onboarding
pending".

**The dependency_updates rule:** grant it to the union of three sources, and name the source in
each grant's reason:
- the live inert landing population, read with `bump_proposer.landing_policy.read_inert_landing`
  (the inert lane and the bump proposer act only there);
- the repositories in `deploy_watcher.workflows.ROLLOUT_WORKFLOWS` (`brain`, `change-manager`),
  where the estate lander lands Dependabot updates under approved change records;
- the five forks, where fork-sync brings in upstream releases.

If one of the 13 repositories falls in none of the three, set its `dependency_updates` to
`not_assessed` and list it in the pull request body for Devon. Don't guess, and don't trust the
table over the sources.

**The credential_rotation rule:** a repository owns a credential when its `.cred-consumers.toml`
holds that credential's `[[credential]]` entry with `rotated_by_sds = true`. Those repositories are
`rotation_worker.registry.REGISTRY_REPOSITORIES`, and Task 11 checks the same definition.

- [ ] **Step 1: Write the failing test**

`test_seed.py::test_seed_parses_and_matches_todays_lists` loads the TOML into `MemberProposalBody`.
It then asserts the following, so the seed reproduces today's populations exactly:
- `populations.working_copies` equals the six `CHECKOUTS` directory names in
  `scripts/run-activation-sweep.sh:101-108`;
- `bindable` equals the four `BINDABLE` names;
- the app subjects equal `revision_watcher.subjects.SUBJECTS` as `(name, health_url, branch)`
  triples;
- `granted(..., "security_patches")` contains every `fork_harden.rules.FORKS` repository.

Build the `Member` list from the TOML in the test, since the record is not yet read. Delete this
test in Tasks 8 to 10 as each old list it compares against is deleted.

- [ ] **Step 2: Run it, write the seed, run it again**

Run: `.venv/bin/pytest tests/sds_members/test_seed.py -v`
Expected: FAIL, then PASS once the file is written.

- [ ] **Step 3: Commit, then stage the seed in production**

Run `sds-members stage docs/operations/membership-seed.toml` from the worktree venv. Fetch the SYSTEM
bearer `221a48d5-…` with the narrow identity (`scripts/sds-token.sh`) into
`SDS_MEMBERS_SYSTEM_TOKEN` in the same process, and never echo it. The command prints the review URL.

- [ ] **Step 4: Gate: Devon confirms the seed**

Send Devon the review URL and stop. The seed is his one approval of the whole record. Don't confirm
it on his behalf, and don't merge part B until he has. After he confirms, run
`.venv/bin/python -m scripts.sds_scope ledger` with the observer bearer. Expect the 13 repositories.

### Task 8: The landing ledger and activation sweep read the record

**Files:**
- Modify: `scripts/run-landing-ledger.sh`, `scripts/run-activation-sweep.sh`
- Modify: `docs/operations/landing-audit.md` ("Which repositories it covers")
- Test: `tests/scripts/test_launcher_scope.py`

**Behavior:**
- **Landing ledger:** set `SDS_SCOPE_ORCHESTRATOR_TOKEN` from the observer bearer the launcher
  already fetches as `LANDING_LEDGER_TOKEN`, and call `sds_scope ledger`. Remove
  `SDS_SCOPE_GITHUB_TOKEN` and `SDS_SCOPE_CHANGE_MANAGER_TOKEN` from the scope call.
  - Then run `grep -rn "change_manager\|CHANGE_MANAGER\|314f276d" src/landing_ledger scripts/run-landing-ledger.sh`.
  - If nothing but the scope call used the change-manager read token, remove its fetch from the
    launcher too, and in Task 12 remove the `scripts/run-landing-ledger.sh` reader row from
    change-manager's `.cred-consumers.toml` (added in change-manager #135).
  - Keep the exit-code handling as it is (0 or 3 accepted; any other code fails).
- **Activation sweep:** replace the `CHECKOUTS` array with `sds_scope checkouts` and the `BINDABLE`
  array with `sds_scope bindable`, prefixing each name with `$HOME/Projects/`.
  - A scope exit of 3 on either makes the sweep exit 3 (incomplete) without running that pass; the
    other pass still runs.
  - Delete the comment block at lines 54-100, including the TODO(scope-registry), and point to
    `docs/operations/membership.md` in one line.

- [ ] **Step 1: Write the failing launcher tests**

Run each launcher under `env -i PATH=... HOME=<tmp>` with a stub `sds_scope` on `PATH` (follow the
existing launcher tests in `tests/scripts/`):
- `test_sweep_takes_its_checkouts_from_scope`;
- `test_sweep_reports_incomplete_when_scope_fails`;
- `test_ledger_passes_each_scoped_repository`.

- [ ] **Step 2: Run them and see them fail; change both launchers; run them and see them pass**

Run: `.venv/bin/pytest tests/scripts/test_launcher_scope.py -v`

- [ ] **Step 3: Parity check against production (anti-goal 2)**

From the worktree, run `sds_scope ledger`, `checkouts` and `bindable` against production. Diff each
against the old lists: the ledger's output from today's run in its log, and the two deleted arrays.
Expected: no differences. Any difference stops the task and goes to Devon.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat(lanes): the landing ledger and activation sweep take their scope from the member record"
```

### Task 9: The revision watcher reads its subjects from the record

**Files:**
- Modify: `src/revision_watcher/subjects.py` (delete `SUBJECTS`; add `load_subjects`), `census.py`, `cli.py`
- Modify: `tests/revision_watcher/test_census.py` (fixture subjects instead of `SUBJECTS`)
- Test: `tests/revision_watcher/test_subjects.py`

**Interfaces:**
- Consumes: `sds_members.client.read_members` and `sds_members.populations.app_subjects`.
- Produces: `load_subjects(*, token: str, credential_key_id: str, transport=None) ->
  tuple[Subject, ...]`, which raises `MembersUnreadable` unchanged. `census.sweep` and
  `undeclared_subjects` take `subjects` as a parameter instead of importing the constant. `cli.py`
  calls `load_subjects` with the `ORCHESTRATOR_API_TOKEN` and key id it already reads. When it
  catches `MembersUnreadable`, or gets no subjects, it exits 3 (incomplete) and names the reason.

Keep the module docstring's reasoning, rewritten to say the list is declared in the member record.

- [ ] **Step 1: Write the failing tests**

- `test_subjects_come_from_member_apps`.
- `test_retired_member_apps_are_not_subjects`.
- `test_unreadable_record_exits_3` (through the Typer entry point, per the CLI-testing rule).

- [ ] **Step 2: Run them and see them fail; implement; run the whole revision-watcher suite**

Run: `.venv/bin/pytest tests/revision_watcher -v`
Expected: PASS. Delete the `SUBJECTS` assertion from `test_seed.py`.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "feat(revision-watcher): subjects come from the member record"
```

### Task 10: fork_harden acts only on forks with security patches granted

**Files:**
- Modify: `src/fork_harden/cli.py`, `scripts/run-fork-harden.sh`
- Modify: `.cred-consumers.toml` (`orchestrator-observer-bearer`, line 246: add a `runtime-fetch` row for `scripts/run-fork-harden.sh` with `healthchecks_check = "sds-fork-hardening"`, shaped like its other rows)
- Test: `tests/fork_harden/test_cli.py`

**Behavior:**
- `run` takes a repeated `--granted <repository>` option. It hardens only the `FORKS` entries whose
  repository is among the granted names, compared ignoring case. `FORKS` stays code, because its
  rules are Python callables.
- A `FORKS` entry that isn't granted is skipped and reported on stderr as
  `fork-harden: <repo> is not granted security patches; skipped`. The run then exits 2 (unreadable,
  in fork-harden's convention), and the outcome alarm reports it.
- A granted name with no `FORKS` entry is normal: fork-sync covers it.
- The launcher fetches the observer bearer with the narrow identity, read directly from the Keychain
  into its own variable (the two-identity rule), and runs `sds_scope granted security_patches`.
  - If scope exits non-zero, the launcher exits 2 without hardening.
  - Otherwise it passes each name as `--granted`.

- [ ] **Step 1: Write the failing tests**

- `test_only_granted_forks_are_hardened`.
- `test_an_ungranted_fork_is_skipped_and_exits_2`.
- `test_a_granted_name_without_rules_is_not_an_error`.

- [ ] **Step 2: Run them and see them fail; implement; run them and see them pass**

Run: `.venv/bin/pytest tests/fork_harden -v`

- [ ] **Step 3: Prove the launcher's secret path under `env -i`**

Under `env -i PATH=/usr/bin:/bin HOME=$HOME`, run the launcher's credential and scope lines alone
(not the hardening pass) and confirm they print the granted forks. Don't use `bash -x`.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat(fork-harden): harden only forks granted security patches"
```

### Task 11: The consistency check lane

**Files:**
- Create: `src/sds_members/consistency.py` (holding `factory_targets` and `canonical`, moved from the old `sds_scope.py`, plus the checks)
- Modify: `src/sds_members/cli.py` (`check` command)
- Create: `scripts/run-membership-check.sh`, `scripts/com.devon.sds-membership-check.plist`, `scripts/install-membership-check.sh`
- Modify: `.cred-consumers.toml` (an observer-bearer row for the new launcher), `layers.toml`
- Test: `tests/sds_members/test_consistency.py`, `tests/scripts/test_launchd_installers.py` (picks up the new installer)

**Interfaces:**
- Produces: `check(members, *, declarations: Scope, inert: frozenset[str], rollout_repositories,
  tool_repositories, hardened_forks, synced_forks, registry_repositories) -> Report`, where
  `Report(disagreements: list[str], gaps: list[str])`. All inputs are plain collections, so the
  function is pure. Each disagreement is one line naming the repository, the source and the grant
  expected.

**Rules (each a disagreement when violated):**

| Source | Required of the record |
|---|---|
| `factory-target.toml` declares `factory_target = true` | member with `app_changes` granted |
| a member with `app_changes` granted | its `factory-target.toml` declares `true` |
| the inert landing population | member with `dependency_updates` granted |
| `deploy_watcher.workflows.ROLLOUT_WORKFLOWS`, `tool_installer.tools.TOOLS` | member with `rollouts` granted |
| `fork_harden.rules.FORKS` | member with `security_patches` granted |
| `fork_sync.watch.forks()` | member with `dependency_updates` and `security_patches` granted |
| `rotation_worker.registry.REGISTRY_REPOSITORIES` | member with `credential_rotation` granted |

Retired members count as not granted (Review Focus 5). An unreadable source is a gap, not a
disagreement. Declarations whose `target` is `None` are gaps, as in today's `factory_targets`.

- `sds-members check` reads the record, the declarations for every owned repository, the inert
  population and the lists in the table. It prints disagreements on stdout and gaps on stderr. Exit
  codes follow the found/unreadable convention: 0 clean, 1 tool failed, 2 found, 3 incomplete.
  Disagreements outrank gaps.
- The launcher is an **outcome alarm**, like `run-fork-sync-watch.sh`: on any non-zero exit, it
  pings `/fail` on Healthchecks check `sds-membership-check` with the report, and it never arms the
  dead-man switch. Its credentials, each read straight from the Keychain into its own variable:
  - the observer bearer (narrow identity);
  - change-manager READ `314f276d-55ca-4ddc-a24d-b4a3013508cd` (broad identity);
  - GitHub through `gh auth token`.
- The plist runs daily at 07:45, after the landing ledger.

- [ ] **Step 1: Write the failing tests**

- One test per rule in both directions (agreeing input passes, violating input reports).
- `test_a_retired_member_counts_as_not_granted`.
- `test_an_unreadable_declaration_is_a_gap`.
- The moved tests for `factory_targets` and `canonical`.
- A CLI test through the Typer entry point, `test_check_exits_2_on_a_disagreement_and_3_on_a_gap_only`.

- [ ] **Step 2: Run them and see them fail; implement; run them and see them pass**

Run: `.venv/bin/pytest tests/sds_members tests/scripts/test_launchd_installers.py -v`

- [ ] **Step 3: Create the Healthchecks check**

Follow `infraops-mcp-server/docs/operations/healthchecks.md` on the self-hosted instance: name
`sds-membership-check`, daily period, a grace of 2 hours, and alerts to alert-hub. Store its ping URL
the way the fork-sync-watch launcher's is stored.

- [ ] **Step 4: Run the check once against production from the worktree**

Expected: exit 0. Any disagreement here is a seed or code-list mismatch; take it to Devon before
merging.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(sds-members): a daily check of the record against declarations and configuration lists"
```

### Task 12: Ship part B

- [ ] **Step 1:** Run `rtk proxy make check > /tmp/check.log 2>&1`, then `grep` the summary. Expect all passed. Then run `/code-review` and fix confirmed findings.
- [ ] **Step 2:** Confirm that Devon has confirmed the seed (Task 7, Step 4). Push, open the pull request, watch the checks with a pause, merge, and confirm `MERGED`.
- [ ] **Step 3:** In change-manager, open a pull request for the registry rows:
  - add `scripts/run-membership-check.sh` as a `runtime-fetch` reader of `314f276d` with `healthchecks_check = "sds-membership-check"`;
  - remove the landing ledger's row if Task 8 removed its fetch.

  Merging it redeploys change-manager, so do it while no rotation unit for change-manager is ready.
- [ ] **Step 4:** Install the new launch agent from the main tree (after it pulls the merge) with `scripts/install-membership-check.sh`.
- [ ] **Step 5:** Kick each moved lane once with `launchctl kickstart` and read its log: landing ledger, activation sweep, revision watcher, fork-harden and the membership check. Expect each lane's usual exit code and the same repositories as before.
- [ ] **Step 6:** Remove the worktree, its test database and the merged branch, and check the main tree's `git status` for stray files.

## Out of scope for phase 1

These are named so no task grows into them:
- readiness checks for any service;
- a SYSTEM route to set `not_assessed` or `remediating`, which waits for the first readiness check
  that would call it;
- generating the declarations from the record;
- the broken-runner recovery mechanism;
- onboarding `factory-runner` and `project-standards` for app changes, which follows the October 18
  procedure separately.
