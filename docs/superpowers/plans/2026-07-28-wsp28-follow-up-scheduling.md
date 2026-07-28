# WS-P2.8 Follow-up Scheduling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An approved package's `follow_up` declaration deterministically yields one timed, human-discharged work unit, minted by an idempotent externally-invoked pass.

**Architecture:** Three parts, none of which is a timer. Intake persists the declaration to a new `work_package_revisions.follow_up` JSONB column. A pure predicate decides due-ness from canonical state plus a bounded config constant. A SYSTEM-only route mints one unit per due revision, content-addressed by `uuid5` so re-running is a no-op, and a human discharges it through the existing `/review` adjudication and completion forms.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x (`Mapped`/`mapped_column`), Alembic, Pydantic v2, Typer, pytest, Postgres 16, `uv`.

**Spec:** `docs/superpowers/specs/2026-07-28-wsp28-follow-up-scheduling-design.md` — read it before Task 1. Section references below (§5.1, §7.1, …) point into it.

**Session scope:** This plan is **session 1 only** — build, per-task reviews, final adversarial review, merge. The production deploy, GAP-5, the production demonstration and the Wave-2 closeout note are a separate dedicated session, specified in spec §16.2. Do not deploy from this plan.

---

## Global Constraints

Every task's requirements implicitly include this section. Values are copied verbatim from the spec and from the codebase; do not paraphrase them.

**Verification**

- `make check` must be green on a **clean tree** before the branch is declared done. It needs Postgres on `127.0.0.1:5432`, `SECURITY_STANDARDS_DIR` pointing at `tests/fixtures/security-standards`, and a migrated database.
- **`make check` exit 0 does not prove tests ran.** The vendored Makefile swallows pytest exit code 5 ("no tests collected"). Always read the `collected N items` line, not the exit code.
- Run focused tests in the **FOREGROUND**. Never background a pytest run.
- **Never run two pytest suites against the test database concurrently.** The fixtures `DROP SCHEMA public CASCADE` and re-migrate; a background run plus a foreground run corrupt each other and produce a spray of unrelated failures on a tree that is green when run alone.
- Resolve Python tools from the repo-local `.venv/bin` before global `PATH`.
- Run `ruff format` (or `make fix`), not just `ruff check`, before committing. `make check` runs `ruff format --check .` over the **whole repo**, while the diff-scoped Stop hook only sees changed files and lint rules — so format debt is invisible until the full gate.
- **Never pass a `.json` file to `ruff format`** — it injects a trailing comma and produces invalid JSON.

**Test-database fixtures** (from `tests/services/conftest.py`)

- `migrated_session` — the fixture nearly every service test uses. Drops and re-creates the schema, runs `alembic upgrade head`.
- `db_client`, `auth_config` — re-exported from `tests/api/conftest.py` for API tests.
- Actor contexts are **module-level constants, not fixtures**: `SYSTEM = ActorContext("system", ActorRole.SYSTEM)`, `VERIFIER = ActorContext("verifier-1", ActorRole.VERIFIER)`, human is `ActorContext("human-1", ActorRole.HUMAN)`.
- Unit factories are **imported functions, not fixtures**: `register_unit` from `tests/services/test_dependencies.py`, `completed_unit` from `tests/services/test_release_artifacts.py`.

**Time and state**

- **`work_units.updated_at` cannot be back-dated.** The `set_work_unit_updated_at` trigger (migration 0001) rewrites it on every UPDATE. Any test that "ages" a row is silently testing nothing. Exercise time by **shrinking the threshold**, never by ageing a row.
- Entered-a-state timestamps come from the event ledger: `MAX(Event.occurred_at) WHERE Event.subject_type == "work_unit" AND Event.subject_id == <id> AND Event.to_state == <state>`.

**Transactions**

- A request entry point **owns its transaction and must `session.commit()`**. A function invoked *inside* another transaction must never commit.
- A test asserting persistence must `expire_all()` and re-read, or it is only asserting that a call returned an object.

**Errors**

- Only `DomainError` and `APIAuthenticationError` have registered exception handlers. **Anything else raised from a route is an unhandled HTTP 500.** Parse input defensively at the route boundary and raise `DomainError`; never let `uuid.UUID(bad)`, `datetime.fromisoformat(bad)`, `ValueError`, `TypeError` or `IntegrityError` escape.

**Migrations**

- Alembic revision ids must be **≤32 characters** (`alembic_version.version_num` is `varchar(32)`). `0020_wsp28_follow_up` is 20 characters. A longer id fails at runtime with `StringDataRightTruncation`, not at authoring time.
- Current head is `0019_wsp27_tracker_recon`; that is the `down_revision`.

**Architecture guards — all whole-repo scans that only a full `make check` runs**

A per-task loop can look green and still break CI on every one of these.

1. `tests/architecture/test_ws32_scope_guards.py` — scans **runtime string literals including docstrings** across all of `src/orchestrator/`. Forbidden token sequences, verbatim:

   ```python
   FORBIDDEN_SEQUENCES = (
       ("factory-event/v1", ("factory", "event", "v1")),
       ("merge_pull_request", ("merge", "pull", "request")),
       ("workflow_dispatch", ("workflow", "dispatch")),
       ("factory-runner", ("factory", "runner")),
       ("production mutation", ("production", "mutation")),
       ("auto_merge", ("auto", "merge")),
       ("productionmutation", ("productionmutation",)),
       ("coolify", ("coolify",)),
       ("dispatch", ("dispatch",)),
       ("deploy", ("deploy",)),
   )
   ```

   Matching is on whole lowercase tokens after camel-splitting, so `deployment` and `dispatches` do **not** match, but `deploy` and `dispatch` do.

2. `tests/architecture/test_ws33_scope_guards.py` — same scan root, **no allowlist**:

   ```python
   AUTOMATIC_MERGE_SEQUENCES = (
       ("gh", "pr", "merge"),
       ("git", "push", "origin", "main"),
       ("merge", "pull", "request"),
       ("auto", "merge"),
       ("automerge",),
       ("merges",),
   )
   ```

3. `tests/architecture/test_scope_guards.py::test_production_post_route_inventory_is_explicit` — **exact set equality** over `/api/v1` POST paths. A new route not added to the literal fails CI.
4. `tests/idempotency/test_matrix.py` — every ingress POST route needs a `MatrixRow` in `tests/idempotency/matrix.py`, and every row must name a test that exists.
5. `tests/architecture/test_authority_write_once.py::test_the_named_construction_sites_still_exist` — `CONSTRUCTION_SITES` is exact set equality over files that construct `WorkUnit(...)`.
6. `tests/architecture/test_cross_boundary_vocabulary.py` — every module-level string collection with **≥2** str members used in an `x in S` or `S.get(x)` test must be registered or marked `# not-a-vocabulary: <reason>`.
7. `tests/architecture/test_unreachable_guards.py` — every public `kernel`/`services` function needs a production caller. "A test calls it" is explicitly not a caller. **One deliberate exception spans Tasks 1-2:** Task 1 creates `validate_follow_up` and Task 2 wires it into `register_package_intake`, so this guard is RED between them. Do not allowlist it — the guard's own message says a justification reading "in fact it is called" means the predicate is wrong. Task 2 Step 11 is where it must go green, and Task 2 is not complete until it does.
8. `tests/architecture/test_wsp21_invariant_scan.py` — nothing to do here: this workstream adds no HTTP client to `src/`.

> **THE TRAP FOR THIS WORKSTREAM.** `services/verifier_criteria.py`, `services/lifecycle.py` and `services/evidence.py` are already in `WS53_POST_DEPLOY_PATHS`, so the words `post-deploy`/`deploy` are permitted there. **`src/orchestrator/services/follow_ups.py` is new and is in NO allowlist.** It therefore may not contain the string `post_deploy`, `post-deploy`, `deploy`, `dispatch`, `coolify`, or `merges` — **anywhere, including comments and docstrings, and including a comment contrasting follow-up ACs with post-deploy ones.** Do not request an allowlist entry; reword. Say "the release-observation units" or "the other generated criteria kind" instead. `src/orchestrator/web.py` is likewise in no allowlist.

**Authority envelope**

- Construct only via `normalize_authority(...)`; store `.normalized()`. Storing a raw dict makes `normalized()` a non-fixed-point on re-read and the re-derived fingerprint disagrees with the minted one.
- Never pass an `unknown_fields` key into `normalize_authority`.
- Never add to `KNOWN_FIELDS` — it rewrites every authority fingerprint in the live approval ledger.
- `WorkUnit.authority` is **write-once**: assign at construction, never mutate.

**Capabilities**

- Add `follow_up_review` to `ORCHESTRATOR_ONLY_CAPABILITIES`, **never** to `CAPABILITY_VOCABULARY["runner"]` — the latter is byte-pinned across this repo and `AlobarQuest/factory-runner` via `tests/fixtures/runner_authority_envelope.json` and `CONTRACT_SHA256`.

**Credentials**

- The mint pass and its launcher use **`orchestrator-system`** (BWS `221a48d5-3f29-4898-b300-b4820140c880`, credential key id `orchestrator-system`). It must **not** use `orchestrator-drift-reporter`; see spec §9.1 — that is a correctness constraint, not a preference.
- Never write a token into a tracked file. Fetch by stable BWS UUID at runtime via `~/Projects/vps-backup/bws-token.sh`. Never `bash -x` a token-sourcing script.

**Commits**

- Commit after each task's tests pass. End every commit message with:

  ```
  Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Ac8GrBCxZqisLaXywQFG12
  ```

---

## File Structure

**Created**

| Path | Responsibility |
|---|---|
| `src/orchestrator/services/follow_ups.py` | The due predicate (pure) and the mint service. **Subject to the token trap above.** |
| `migrations/versions/0020_wsp28_follow_up.py` | Adds `work_package_revisions.follow_up` JSONB, nullable, no server default |
| `scripts/run-follow-up-mint.sh` | Operator launcher: one pass, then exit |
| `docs/operations/follow-up-scheduling.md` | Runbook |
| `docs/decisions/0007-declared-follow-up-scheduling.md` | ADR: bounded system-side minting |
| `tests/services/test_follow_ups.py` | Predicate + mint service tests |
| `tests/api/test_follow_ups_api.py` | Route tests |
| `tests/idempotency/test_follow_up_idempotency.py` | The matrix row's named test |
| `tests/cli/test_mint_follow_ups_cli.py` | `CliRunner` test through the real entrypoint |

**Modified**

| Path | Change |
|---|---|
| `src/orchestrator/persistence/models.py` | `WorkPackageRevision.follow_up` column |
| `src/orchestrator/package_sources.py` | Emit `follow_up` in the intake payload |
| `src/orchestrator/services/package_intake.py` | Carry and validate `follow_up` |
| `src/orchestrator/api/schemas.py` | `PackageIntakeRegistration.follow_up`; mint command + response models |
| `src/orchestrator/config.py` | `follow_up_due_after_days` |
| `src/orchestrator/capability_vocabulary.py` | `follow_up_review` |
| `src/orchestrator/services/lifecycle.py` | `FOLLOW_UP_AC_ID`; `required_ac_ids` branch |
| `src/orchestrator/services/verifier_criteria.py` | `_generated_follow_up_criteria` branch |
| `src/orchestrator/services/evidence.py` | `_validated_subject` carve-out (no `allow_*` flag) |
| `src/orchestrator/web.py` | `_adjudicatable_criteria` must not filter the follow-up AC |
| `src/orchestrator/api/routes.py` | `POST /api/v1/follow-ups/mint` |
| `src/orchestrator/cli.py` | `mint-follow-ups` |
| `src/tracker_projection_adapter/cli.py` | `reconcile` per-item fail-open; per-pass idempotency key |
| `tests/architecture/test_scope_guards.py` | POST route inventory |
| `tests/architecture/test_authority_write_once.py` | `CONSTRUCTION_SITES` |
| `tests/architecture/test_cross_boundary_vocabulary.py` | `follow_up` field-name registration |
| `tests/idempotency/matrix.py` | New mechanism constant + row |

**Separate repo** — `AlobarQuest/infraops-mcp-server`: `drift-audit.sh` gains a non-fatal mint step (Task 10).

---

## Task Order And Rationale

```
T1  column + migration + pure validator          ─┐ increment 1
T2  intake wiring + vocabulary registration      ─┘
T3  config + capability + pure due predicate     ─┐
T4  mint service + CONSTRUCTION_SITES            ─┤ increment 2
T5  generated criteria + adjudication carve-out  ─┘
T6  route + schemas + inventory + matrix row     ─┐ increment 3
T7  CLI + launcher                               ─┤
T8  runbook + ADR-0007                           ─┘
T9  tracker adapter fixes                          increment 5
T10 drift-audit wiring (infraops-mcp-server)       increment 4
```

T3 before T4 because the mint service consumes the predicate. T5 after T4 because its tests mint a real unit. T9 and T10 are independent of T1–T8 and may be done in any order relative to them.

---

## Increment 1 — Persist the declaration

### Task 1: The `follow_up` column, migration, and pure validator

**Files:**
- Modify: `src/orchestrator/persistence/models.py:174` (after `verification_limitations`)
- Create: `migrations/versions/0020_wsp28_follow_up.py`
- Create: `src/orchestrator/services/follow_ups.py`
- Test: `tests/services/test_follow_ups.py`
- Test: `tests/persistence/test_migrations.py` (add one test)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `WorkPackageRevision.follow_up: Mapped[dict[str, Any] | None]`
  - `orchestrator.services.follow_ups.validate_follow_up(value: object) -> dict[str, Any] | None`
    — returns the normalized declaration, `None` when the input is `None`, raises `DomainError("follow_up_invalid", …)` otherwise.

> **REMINDER — the token trap.** `src/orchestrator/services/follow_ups.py` is in no scope-guard allowlist. It must not contain `deploy`, `dispatch`, `coolify`, `merges`, or the sequences listed in Global Constraints — **in any string literal or docstring**. Do not write a comment comparing this to the post-deploy generated criteria.

- [ ] **Step 1: Write the failing validator test**

Create `tests/services/test_follow_ups.py`:

```python
import pytest

from orchestrator.errors import DomainError
from orchestrator.services.follow_ups import validate_follow_up

VALID = {
    "required": True,
    "revisit_when": "After the next quarterly review.",
    "signals": ["A guard nobody triaged."],
    "owner": "devon",
}


def test_a_valid_declaration_round_trips() -> None:
    assert validate_follow_up(VALID) == VALID


def test_absent_declaration_is_none_not_an_error() -> None:
    assert validate_follow_up(None) is None


def test_the_fully_degenerate_declaration_is_valid() -> None:
    degenerate = {"required": False, "revisit_when": None, "signals": [], "owner": None}

    assert validate_follow_up(degenerate) == degenerate


@pytest.mark.parametrize(
    "value",
    [
        {"required": True, "revisit_when": None, "signals": []},
        {"required": True, "revisit_when": None, "signals": [], "owner": None, "extra": 1},
        {"required": "yes", "revisit_when": None, "signals": [], "owner": None},
        {"required": True, "revisit_when": 7, "signals": [], "owner": None},
        {"required": True, "revisit_when": None, "signals": "not-a-list", "owner": None},
        {"required": True, "revisit_when": None, "signals": [None], "owner": None},
        "not-a-mapping",
    ],
    ids=[
        "missing-key",
        "unknown-key",
        "required-not-bool",
        "revisit-when-not-str",
        "signals-not-list",
        "signal-item-not-str",
        "not-a-mapping",
    ],
)
def test_a_malformed_declaration_is_a_named_domain_error(value: object) -> None:
    with pytest.raises(DomainError) as caught:
        validate_follow_up(value)

    assert caught.value.code == "follow_up_invalid"
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `.venv/bin/pytest tests/services/test_follow_ups.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestrator.services.follow_ups'`

- [ ] **Step 3: Write the validator**

Create `src/orchestrator/services/follow_ups.py`:

```python
"""Package-declared follow-up scheduling (WS-P2.8).

The intent package declares WHETHER an outcome should be revisited; this module owns the
orchestrator's side of that contract. `revisit_when` and `signals` are prose written for a human
and are never parsed -- the timing comes from configuration, not from the text.

The four field names mirror the intent-packages schema exactly. A fifth key is a validation
error rather than an ignored extra, because a silently-dropped key is how a declaration and its
reader drift apart.
"""

from typing import Any

from orchestrator.errors import DomainError

# The intent-packages `follow_up` block, mirrored field for field. Every key is mandatory-present;
# `revisit_when` and `owner` may be null. Registered in the cross-boundary vocabulary registry.
FOLLOW_UP_FIELDS = ("required", "revisit_when", "signals", "owner")


def _invalid(detail: str) -> DomainError:
    return DomainError(
        "follow_up_invalid",
        f"package follow_up declaration is invalid: {detail}",
        "correct the package follow_up block and re-emit the intake payload",
    )


def validate_follow_up(value: object) -> dict[str, Any] | None:
    """Return the normalized declaration, or None when the package carried none."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _invalid("it must be a mapping")
    unknown = sorted(set(value) - set(FOLLOW_UP_FIELDS))
    if unknown:
        raise _invalid(f"unknown key {unknown[0]!r}")
    missing = [field for field in FOLLOW_UP_FIELDS if field not in value]
    if missing:
        raise _invalid(f"missing required key {missing[0]!r}")
    if not isinstance(value["required"], bool):
        raise _invalid("`required` must be a boolean")
    for field in ("revisit_when", "owner"):
        if value[field] is not None and not isinstance(value[field], str):
            raise _invalid(f"`{field}` must be a string or null")
    signals = value["signals"]
    if not isinstance(signals, list) or not all(isinstance(item, str) for item in signals):
        raise _invalid("`signals` must be a list of strings")
    return {
        "required": value["required"],
        "revisit_when": value["revisit_when"],
        "signals": list(signals),
        "owner": value["owner"],
    }
```

- [ ] **Step 4: Run the validator tests and confirm they pass**

Run: `.venv/bin/pytest tests/services/test_follow_ups.py -v`
Expected: PASS, `collected 10 items`

- [ ] **Step 5: Add the model column**

In `src/orchestrator/persistence/models.py`, immediately after the `verification_limitations` line in `WorkPackageRevision`:

```python
    verification_limitations: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSONB)
    # The package's declared follow-up block (WS-P2.8). NULL means the revision predates the
    # column -- distinguishable from a declaration that says `required: false`, which matters
    # because the first can never be recovered and the second is a real answer.
    follow_up: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    work_package: Mapped[WorkPackage] = relationship()
```

- [ ] **Step 6: Write the migration**

Create `migrations/versions/0020_wsp28_follow_up.py`:

```python
"""Add work_package_revisions.follow_up — the package's declared follow-up block (WS-P2.8).

Revision ID: 0020_wsp28_follow_up
Revises: 0019_wsp27_tracker_recon

Nullable with no server default, deliberately. NULL means "this revision predates the column and
its declaration is unrecoverable" -- the package YAML is never stored, only the derived intake
payload, and the payload did not carry the block. That is distinguishable from a stored
`{"required": false, ...}`, which is a real answer. No backfill is possible.

Note the revision id is 20 characters: `alembic_version.version_num` is varchar(32), and a longer
id fails at runtime with StringDataRightTruncation rather than at authoring time.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0020_wsp28_follow_up"
down_revision = "0019_wsp27_tracker_recon"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "work_package_revisions",
        sa.Column("follow_up", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("work_package_revisions", "follow_up")
```

- [ ] **Step 7: Write the migration test**

Append to `tests/persistence/test_migrations.py`:

```python
def test_migration_0020_adds_a_nullable_follow_up_column() -> None:
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    config = alembic_config()
    command.upgrade(config, "head")

    columns = {
        item["name"]: item
        for item in inspect(engine).get_columns("work_package_revisions")
    }
    engine.dispose()

    assert columns["follow_up"]["nullable"] is True
    assert columns["follow_up"]["default"] is None
```

- [ ] **Step 8: Run the migration tests**

Run: `.venv/bin/pytest tests/persistence/test_migrations.py -v`
Expected: PASS. The existing up/down/re-up test must also stay green — that is what proves `downgrade()` works.

- [ ] **Step 9: Register the vocabulary**

`FOLLOW_UP_FIELDS` is a module-level tuple with ≥2 str members used in membership tests, so `tests/architecture/test_cross_boundary_vocabulary.py` requires it be registered. Add to `VOCABULARY_REGISTRY`, next to the existing `POST_DEPLOY_AC_IDS` entry:

```python
    "services/follow_ups.py:FOLLOW_UP_FIELDS": (
        "intent-packages schema.py TOP_SCHEMA['follow_up'] MapSpec field names"
    ),
```

This is a genuine cross-boundary mirror, so it is REGISTERED, not marked `# not-a-vocabulary`.

- [ ] **Step 10: Run the architecture guards**

Run: `.venv/bin/pytest tests/architecture/ -v`
Expected: **exactly one failure** — `test_unreachable_guards.py::test_every_public_kernel_and_service_function_is_reachable`, naming `validate_follow_up`. That is correct and expected at this point: the function's production caller lands in Task 2, and the two tasks are one increment. **Do not allowlist it and do not wire the caller early** — Task 2's replay exemption is the dangerous part of that wiring and needs its own review gate.

Everything else must pass, in particular `test_cross_boundary_vocabulary.py` and both scope guards. If a scope guard fails on `follow_ups.py`, you wrote a forbidden token — reword, do not allowlist.

- [ ] **Step 11: Format and commit**

```bash
.venv/bin/ruff format src/orchestrator/services/follow_ups.py src/orchestrator/persistence/models.py migrations/versions/0020_wsp28_follow_up.py tests/services/test_follow_ups.py tests/persistence/test_migrations.py tests/architecture/test_cross_boundary_vocabulary.py
.venv/bin/ruff check src/ tests/ migrations/
git add -A
git commit -m "feat(wsp28): persist the package follow-up declaration

Adds work_package_revisions.follow_up (JSONB, nullable) plus migration
0020 and a pure validator mirroring the intent-packages MapSpec field
for field. NULL means the revision predates the column, which is
deliberately distinguishable from a stored required:false -- the first
is unrecoverable because the package YAML is never stored.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ac8GrBCxZqisLaXywQFG12"
```

---

### Task 2: Carry `follow_up` through intake — including the replay exemption

**Files:**
- Modify: `src/orchestrator/package_sources.py:525-552` (`_load_intake_payload` return dict)
- Modify: `src/orchestrator/api/schemas.py:775` (`PackageIntakeRegistration`), `:800` (`PackageIntakeResponse`)
- Modify: `src/orchestrator/api/routes.py:308` (`package_intake_command`), `:1753` (`_package_intake_payload`)
- Modify: `src/orchestrator/services/package_intake.py` (`PackageIntakeCommand`, `register_package_intake`, `_command_identity`, `_legacy_executable_identity_matches`)
- Modify: `src/orchestrator/services/packages.py:160` (`register_revision` kwarg + `candidate`)
- Test: `tests/services/test_package_intake.py`, `tests/api/test_package_intake_api.py`

**Interfaces:**
- Consumes: `validate_follow_up` from Task 1; `WorkPackageRevision.follow_up`.
- Produces: `WorkPackageRevision.follow_up` populated at intake; `PackageIntakeResponse.follow_up`.

> **THE DANGEROUS PART — read before writing code.** `_command_identity` is compared field-for-field against the stored event payload on replay (`_intake_replay`). Adding `follow_up` to it means **every intake event already written — including every one in production — replays as `idempotency_conflict`**, because their stored payload has no such key. The codebase already solved this once, for `intake_purpose`: `_legacy_executable_identity_matches` pops the newer key before comparing. Do the same, and pin it with the regression test in Step 5. Skipping this breaks replay for every existing package in production.

- [ ] **Step 1: Write the failing service test**

Add to `tests/services/test_package_intake.py`:

```python
FOLLOW_UP = {
    "required": True,
    "revisit_when": "After the next quarterly review.",
    "signals": ["A guard nobody triaged."],
    "owner": "devon",
}


def test_intake_persists_the_follow_up_declaration(migrated_session: Session) -> None:
    command = intake_command(follow_up=FOLLOW_UP)

    revision = register_package_intake(migrated_session, command, human_actor())
    migrated_session.expire_all()
    reread = migrated_session.get(WorkPackageRevision, revision.id)

    assert reread is not None
    assert reread.follow_up == FOLLOW_UP


def test_intake_without_a_declaration_stores_null(migrated_session: Session) -> None:
    revision = register_package_intake(migrated_session, intake_command(), human_actor())
    migrated_session.expire_all()
    reread = migrated_session.get(WorkPackageRevision, revision.id)

    assert reread is not None
    assert reread.follow_up is None


def test_a_malformed_declaration_is_rejected_at_the_gate(migrated_session: Session) -> None:
    command = intake_command(follow_up={"required": "yes"})

    with pytest.raises(DomainError) as caught:
        register_package_intake(migrated_session, command, human_actor())

    assert caught.value.code == "follow_up_invalid"
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `.venv/bin/pytest tests/services/test_package_intake.py -k follow_up -v`
Expected: FAIL — `TypeError: PackageIntakeCommand.__init__() got an unexpected keyword argument 'follow_up'`

- [ ] **Step 3: Add the field to the command, the service, and the persistence write**

`src/orchestrator/services/package_intake.py` — add to `PackageIntakeCommand`, **after** `intake_purpose` (it has a default, and a defaulted field cannot precede a non-defaulted one):

```python
    intake_purpose: str = "executable"
    follow_up: dict[str, Any] | None = None
```

In `register_package_intake`, validate before locking, and pass it through. Add the import `from orchestrator.services.follow_ups import validate_follow_up`, then immediately after the `verification_mode` check:

```python
    follow_up = validate_follow_up(command.follow_up)
```

and add `follow_up=follow_up,` to the `register_revision(...)` call.

`src/orchestrator/services/packages.py::register_revision` — add the keyword argument after `verification_limitations`:

```python
    verification_limitations: Mapping[str, Any] | list[Any] | None = None,
    follow_up: Mapping[str, Any] | None = None,
```

and add to `candidate`, after `verification_limitations`:

```python
        "follow_up": _normalize_json(follow_up),
```

Adding it to `candidate` makes it conflict-significant (an existing revision re-registered with a different declaration raises `revision_conflict`) and puts it in the `revision.registered` event identity. Both are correct: the declaration is part of what was approved.

- [ ] **Step 4: Run the service tests**

Run: `.venv/bin/pytest tests/services/test_package_intake.py -v`
Expected: the three new tests PASS. Existing tests must stay green.

- [ ] **Step 5: Write the replay-compatibility test FIRST, then the exemption**

Add to `tests/services/test_package_intake.py`:

```python
def test_a_pre_wsp28_intake_event_still_replays(migrated_session: Session) -> None:
    """An intake event written before the follow_up field existed must not become a conflict.

    `_command_identity` is compared field-for-field on replay. Without an exemption, every
    event already in production -- none of which carries this key -- would raise
    idempotency_conflict on its next replay. Same shape as the intake_purpose exemption.
    """
    command = intake_command()
    actor = human_actor()
    first = register_package_intake(migrated_session, command, actor)

    event = migrated_session.scalar(
        select(Event).where(Event.idempotency_key == command.idempotency_key)
    )
    assert event is not None
    legacy = dict(event.payload["command"])
    legacy.pop("follow_up")
    event.payload = {**event.payload, "command": legacy}
    flag_modified(event, "payload")
    migrated_session.flush()

    replayed = register_package_intake(migrated_session, command, actor)

    assert replayed.id == first.id
```

Imports needed at the top of the module: `from sqlalchemy.orm.attributes import flag_modified`.

- [ ] **Step 6: Run it and confirm it fails**

Run: `.venv/bin/pytest tests/services/test_package_intake.py::test_a_pre_wsp28_intake_event_still_replays -v`
Expected: FAIL with `DomainError` code `idempotency_conflict`. **If this passes before you write Step 7, you have not yet added `follow_up` to `_command_identity` — go back to Step 7's first edit.**

- [ ] **Step 7: Add the identity field and its legacy exemption**

`src/orchestrator/services/package_intake.py::_command_identity` — add after `"verification_limitations"`:

```python
        "follow_up": _normalize_json(command.follow_up),
```

`_legacy_executable_identity_matches` — pop the newer key, exactly as it already does for `intake_purpose`:

```python
def _legacy_executable_identity_matches(
    observed: object,
    expected: dict[str, Any],
    command: PackageIntakeCommand,
) -> bool:
    if command.intake_purpose != "executable" or not isinstance(observed, dict):
        return False
    legacy = dict(expected)
    legacy.pop("intake_purpose", None)
    # WS-P2.8: events written before the follow_up field existed carry no such key. Popping it
    # keeps their replay a replay instead of a conflict; the field is only omittable when the
    # command itself declares none, so a real declaration can never be silently ignored.
    if command.follow_up is None:
        legacy.pop("follow_up", None)
    expected_limitations = legacy.get("verification_limitations")
    if isinstance(expected_limitations, dict):
        expected_limitations = dict(expected_limitations)
        expected_limitations.pop("protocol_fixture_only", None)
        legacy["verification_limitations"] = expected_limitations
    return observed == legacy
```

- [ ] **Step 8: Run the replay test and the whole intake suite**

Run: `.venv/bin/pytest tests/services/test_package_intake.py -v`
Expected: PASS, all tests including the new replay test.

- [ ] **Step 9: Wire the payload, schema, mapper, and projections**

`src/orchestrator/package_sources.py::_load_intake_payload` — add to the returned dict, after `"authority"`:

```python
        "follow_up": package.get("follow_up"),
```

`src/orchestrator/api/schemas.py::PackageIntakeRegistration` — add after `intake_purpose`:

```python
    follow_up: dict[str, Any] | None = None
```

`src/orchestrator/api/schemas.py::PackageIntakeResponse` — add after `authority`:

```python
    follow_up: dict[str, Any] | None
```

`src/orchestrator/api/routes.py::package_intake_command` — add:

```python
        follow_up=body.follow_up,
```

`src/orchestrator/api/routes.py::_package_intake_payload` — add:

```python
        "follow_up": revision.follow_up,
```

`follow_up` defaults to `None` on the registration model, so payloads emitted before this change remain valid.

- [ ] **Step 10: Write the API test**

Add to `tests/api/test_package_intake_api.py`:

```python
def test_package_intake_round_trips_the_follow_up_declaration(db_client: TestClient) -> None:
    declaration = {
        "required": True,
        "revisit_when": "After the next quarterly review.",
        "signals": ["A guard nobody triaged."],
        "owner": "devon",
    }
    created = db_client.post(
        "/api/v1/package-intakes",
        headers=HUMAN,
        json=intake_payload(follow_up=declaration),
    )

    fetched = db_client.get(f"/api/v1/package-intakes/{created.json()['id']}", headers=HUMAN)

    assert created.status_code == 201
    assert fetched.json()["follow_up"] == declaration


def test_a_payload_without_a_follow_up_declaration_is_still_accepted(
    db_client: TestClient,
) -> None:
    created = db_client.post("/api/v1/package-intakes", headers=HUMAN, json=intake_payload())

    assert created.status_code == 201
    assert created.json()["follow_up"] is None
```

- [ ] **Step 10a: Confirm the reachability guard is now green**

Task 1 left `test_unreachable_guards` red on purpose; wiring `validate_follow_up` into
`register_package_intake` is what closes it.

Run: `.venv/bin/pytest tests/architecture/ -v`
Expected: PASS, zero failures. If `validate_follow_up` is still flagged, your Step 3 wiring did not
actually reach it from a production entry point — fix the wiring, never the guard.

- [ ] **Step 11: Run the API and web suites**

Run: `.venv/bin/pytest tests/api/test_package_intake_api.py tests/web/test_intake_form.py -v`
Expected: PASS. `tests/web/test_intake_form.py` imports `intake_payload`, so the browser path is covered by the same helper — it must stay green without modification, which is what proves the form gained the field for free.

- [ ] **Step 12: Format and commit**

```bash
.venv/bin/ruff format src/orchestrator tests/services/test_package_intake.py tests/api/test_package_intake_api.py
.venv/bin/ruff check src/ tests/
git add -A
git commit -m "feat(wsp28): carry the follow-up declaration through intake

Both entry points share PackageIntakeRegistration and
package_intake_command, so the /review form gains the field with no
second validation path (ADR-0006).

Includes the replay exemption: follow_up enters _command_identity, so
without popping it in _legacy_executable_identity_matches every intake
event already written -- including every one in production -- would
replay as idempotency_conflict. Pinned by a regression test.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ac8GrBCxZqisLaXywQFG12"
```

---
## Increment 2 — Due-ness and minting

### Task 3: Config knob, capability, and the pure due predicate

**Files:**
- Modify: `src/orchestrator/config.py:56` (after `reconcile_split_brain_stall_seconds`)
- Modify: `src/orchestrator/capability_vocabulary.py:49` (`ORCHESTRATOR_ONLY_CAPABILITIES`)
- Modify: `src/orchestrator/services/follow_ups.py`
- Test: `tests/test_config.py`, `tests/services/test_follow_ups.py`

**Interfaces:**
- Consumes: `validate_follow_up` (Task 1).
- Produces:
  - `Settings.follow_up_due_after_days: int`
  - `follow_ups.FOLLOW_UP_CAPABILITY: str` = `"follow_up_review"`
  - `follow_ups.UnitFacts`, `follow_ups.RevisionFacts`, `follow_ups.DueDecision`
  - `follow_ups.evaluate_due(facts: RevisionFacts, *, now: datetime, due_after_days: int) -> DueDecision`
  - Skip-reason constants: `SKIP_NOT_REQUIRED`, `SKIP_NO_COMPLETED_UNIT`, `SKIP_UNITS_IN_FLIGHT`, `SKIP_UNSETTLED_FAILED_UNIT`, `SKIP_NOT_YET_DUE`, `SKIP_ALREADY_MINTED`, `SKIP_DECLARATION_MALFORMED`

> The skip reasons are **individual string constants, not a tuple**. A module-level tuple of ≥2 strings used in a membership test would become a discovered subject of `test_cross_boundary_vocabulary.py`; individual constants plus a `Literal[...]` in the response schema sidestep that entirely, and the reasons are internal policy rather than a cross-boundary contract.

- [ ] **Step 1: Write the failing config test**

Add to `tests/test_config.py`:

```python
def test_follow_up_due_after_days_defaults_and_is_env_overridable(monkeypatch) -> None:
    monkeypatch.delenv("ORCHESTRATOR_FOLLOW_UP_DUE_AFTER_DAYS", raising=False)
    assert Settings(database_url=DB_URL).follow_up_due_after_days == 30

    monkeypatch.setenv("ORCHESTRATOR_FOLLOW_UP_DUE_AFTER_DAYS", "0")
    assert Settings(database_url=DB_URL).follow_up_due_after_days == 0


def test_follow_up_due_after_days_cannot_be_set_high_enough_to_silence_it(monkeypatch) -> None:
    """The cap is the point. A large value would silence the mechanism as effectively as an
    off switch, which is the WS-P2.15 failure mode this bound exists to make unreachable."""
    monkeypatch.setenv("ORCHESTRATOR_FOLLOW_UP_DUE_AFTER_DAYS", "100000")
    with pytest.raises(ValidationError):
        Settings(database_url=DB_URL)
```

Add `import pytest` and `from pydantic import ValidationError` at the top of the module if absent.

- [ ] **Step 2: Run it and confirm it fails**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'follow_up_due_after_days'`

- [ ] **Step 3: Add the config knob**

In `src/orchestrator/config.py`, after `reconcile_split_brain_stall_seconds`:

```python
    # How long after a package revision's work settles before its declared follow-up review
    # becomes due. A plain int with NO "off" value and BOUNDED at both ends, following
    # dead_letter_stalled_approval_seconds: a large value silences the mechanism as effectively
    # as None ever did, so the cap is what makes "cannot be switched off" true of the values an
    # operator can actually set. The floor is not the risk -- 0 means "due as soon as the work
    # settles", which is maximally on and is what the production demonstration uses so it needs
    # no waiting.
    follow_up_due_after_days: int = Field(default=30, ge=0, le=365)
```

- [ ] **Step 4: Add the capability**

In `src/orchestrator/capability_vocabulary.py`, replace the `ORCHESTRATOR_ONLY_CAPABILITIES` definition:

```python
# The orchestrator additionally mints these for its own units, which never traverse a runner:
# `post_deploy_verification` for WS-5.1 post-hoc release verification, and `follow_up_review`
# for the WS-P2.8 package-declared follow-up review. Both are in the orchestrator's accepted set
# but NOT the runner's -- the orchestrator vocabulary is a superset.
ORCHESTRATOR_ONLY_CAPABILITIES: Final[frozenset[str]] = frozenset(
    {"post_deploy_verification", "follow_up_review"}
)
```

- [ ] **Step 5: Run the config and contract tests**

Run: `.venv/bin/pytest tests/test_config.py tests/contract/ -v`
Expected: PASS. The cross-repo envelope contract test must stay green — it pins `RUNNER_CAPABILITIES`, which is untouched.

- [ ] **Step 6: Write the failing due-predicate tests**

Add to `tests/services/test_follow_ups.py`:

```python
import uuid
from datetime import UTC, datetime, timedelta

from orchestrator.services.follow_ups import (
    FOLLOW_UP_CAPABILITY,
    SKIP_ALREADY_MINTED,
    SKIP_NO_COMPLETED_UNIT,
    SKIP_NOT_REQUIRED,
    SKIP_NOT_YET_DUE,
    SKIP_UNITS_IN_FLIGHT,
    SKIP_UNSETTLED_FAILED_UNIT,
    RevisionFacts,
    UnitFacts,
    evaluate_due,
)

NOW = datetime(2026, 9, 1, tzinfo=UTC)
SETTLED = NOW - timedelta(days=40)
REQUIRED = {"required": True, "revisit_when": "Later.", "signals": [], "owner": None}


def facts(*units: UnitFacts, follow_up=REQUIRED, has_follow_up_unit=False) -> RevisionFacts:
    return RevisionFacts(
        revision_id=uuid.uuid4(),
        follow_up=follow_up,
        units=units,
        has_follow_up_unit=has_follow_up_unit,
    )


def completed(settled_at=SETTLED) -> UnitFacts:
    return UnitFacts(required_capability="repo.edit", state="completed", settled_at=settled_at)


def cancelled(settled_at=SETTLED) -> UnitFacts:
    return UnitFacts(required_capability="repo.edit", state="cancelled", settled_at=settled_at)


def in_flight() -> UnitFacts:
    return UnitFacts(required_capability="repo.edit", state="executing", settled_at=None)


def failed() -> UnitFacts:
    return UnitFacts(required_capability="repo.edit", state="failed", settled_at=None)


def test_a_settled_revision_past_the_window_is_due() -> None:
    decision = evaluate_due(facts(completed()), now=NOW, due_after_days=30)

    assert decision.skip_reason is None
    assert decision.due_at == SETTLED + timedelta(days=30)


def test_the_anchor_is_the_latest_settling_not_the_earliest() -> None:
    late = NOW - timedelta(days=31)
    decision = evaluate_due(facts(completed(), completed(late)), now=NOW, due_after_days=30)

    assert decision.due_at == late + timedelta(days=30)


def test_a_declaration_that_does_not_require_follow_up_is_skipped() -> None:
    declaration = {"required": False, "revisit_when": None, "signals": [], "owner": None}

    decision = evaluate_due(facts(completed(), follow_up=declaration), now=NOW, due_after_days=30)

    assert decision.skip_reason == SKIP_NOT_REQUIRED


def test_a_revision_with_no_declaration_is_skipped() -> None:
    decision = evaluate_due(facts(completed(), follow_up=None), now=NOW, due_after_days=30)

    assert decision.skip_reason == SKIP_NOT_REQUIRED


def test_a_revision_with_work_still_moving_is_skipped() -> None:
    decision = evaluate_due(facts(completed(), in_flight()), now=NOW, due_after_days=30)

    assert decision.skip_reason == SKIP_UNITS_IN_FLIGHT


def test_a_lingering_failed_unit_blocks_with_its_own_reason() -> None:
    """FAILED is not terminal -- it can go back to READY or on to CANCELLED -- so a revision
    behind one has an undecided outcome. It must NOT read as units_in_flight: 'still working'
    and 'stopped, and nobody decided' are different operator actions."""
    decision = evaluate_due(facts(completed(), failed()), now=NOW, due_after_days=30)

    assert decision.skip_reason == SKIP_UNSETTLED_FAILED_UNIT


def test_a_wholly_cancelled_revision_never_mints() -> None:
    """Nothing shipped, so there is no outcome to revisit."""
    decision = evaluate_due(facts(cancelled(), cancelled()), now=NOW, due_after_days=30)

    assert decision.skip_reason == SKIP_NO_COMPLETED_UNIT


def test_a_revision_with_no_units_at_all_never_mints() -> None:
    decision = evaluate_due(facts(), now=NOW, due_after_days=30)

    assert decision.skip_reason == SKIP_NO_COMPLETED_UNIT


def test_a_revision_inside_the_window_is_not_yet_due() -> None:
    recent = NOW - timedelta(days=5)
    decision = evaluate_due(facts(completed(recent)), now=NOW, due_after_days=30)

    assert decision.skip_reason == SKIP_NOT_YET_DUE
    assert decision.due_at == recent + timedelta(days=30)


def test_zero_days_makes_a_settled_revision_immediately_due() -> None:
    decision = evaluate_due(facts(completed(NOW)), now=NOW, due_after_days=0)

    assert decision.skip_reason is None


def test_an_existing_follow_up_unit_short_circuits_everything() -> None:
    decision = evaluate_due(facts(completed(), has_follow_up_unit=True), now=NOW, due_after_days=30)

    assert decision.skip_reason == SKIP_ALREADY_MINTED


def test_the_revisions_own_follow_up_unit_is_excluded_from_the_predicate() -> None:
    """The minted unit is a unit of its own revision. Once a human completes it, 'everything
    settled' would be true again and the revision would look due a second time. Excluding it
    keeps the counted-skip output honest; the uuid5 id is the structural backstop."""
    own = UnitFacts(
        required_capability=FOLLOW_UP_CAPABILITY, state="awaiting_review", settled_at=None
    )

    decision = evaluate_due(facts(completed(), own, has_follow_up_unit=True), now=NOW, due_after_days=30)

    assert decision.skip_reason == SKIP_ALREADY_MINTED
```

- [ ] **Step 7: Run them and confirm they fail**

Run: `.venv/bin/pytest tests/services/test_follow_ups.py -v`
Expected: FAIL — `ImportError: cannot import name 'evaluate_due'`

- [ ] **Step 8: Write the predicate**

Append to `src/orchestrator/services/follow_ups.py`:

```python
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

# The capability a follow-up review unit requires. Registered in ORCHESTRATOR_ONLY_CAPABILITIES,
# never in the runner vocabulary: no runner works one of these, and the byte-pinned cross-repo
# envelope fixture stays untouched.
FOLLOW_UP_CAPABILITY = "follow_up_review"

# Why a revision was passed over. Individual constants rather than a collection: a module-level
# tuple of strings used in a membership test becomes a discovered subject of the cross-boundary
# vocabulary detector, and these are internal policy, not a contract with another repo.
SKIP_NOT_REQUIRED = "not_required"
SKIP_NO_COMPLETED_UNIT = "no_completed_unit"
SKIP_UNITS_IN_FLIGHT = "units_in_flight"
SKIP_UNSETTLED_FAILED_UNIT = "unsettled_failed_unit"
SKIP_NOT_YET_DUE = "not_yet_due"
SKIP_ALREADY_MINTED = "already_minted"
SKIP_DECLARATION_MALFORMED = "declaration_malformed"

_COMPLETED = "completed"
_CANCELLED = "cancelled"
_FAILED = "failed"


@dataclass(frozen=True)
class UnitFacts:
    """One work unit, reduced to what due-ness depends on.

    `settled_at` is when the unit ENTERED its settled state, read from the event ledger -- never
    from `work_units.updated_at`, which a trigger rewrites on every write and which therefore
    cannot be back-dated or trusted as a state-entry time.
    """

    required_capability: str
    state: str
    settled_at: datetime | None


@dataclass(frozen=True)
class RevisionFacts:
    revision_id: uuid.UUID
    follow_up: dict[str, object] | None
    units: tuple[UnitFacts, ...]
    has_follow_up_unit: bool


@dataclass(frozen=True)
class DueDecision:
    revision_id: uuid.UUID
    due_at: datetime | None
    skip_reason: str | None


def evaluate_due(facts: RevisionFacts, *, now: datetime, due_after_days: int) -> DueDecision:
    """Decide whether a revision's declared follow-up review is due. Pure: no I/O, no clock.

    A revision qualifies when its declaration asks for one, its work actually shipped (at least
    one unit completed) and has stopped moving, the window has elapsed since the last unit
    settled, and no review unit exists yet.

    FAILED deliberately blocks. It is not a terminal state -- a failed unit can return to READY
    or be retired -- so the package's outcome is not yet knowable and there is nothing to
    schedule a revisit of. It gets its OWN skip reason rather than being folded into
    `units_in_flight`, because "still working" and "stopped, and nobody decided" call for
    different operator actions.
    """
    if facts.has_follow_up_unit:
        return DueDecision(facts.revision_id, None, SKIP_ALREADY_MINTED)
    declaration = facts.follow_up
    if not isinstance(declaration, dict) or declaration.get("required") is not True:
        return DueDecision(facts.revision_id, None, SKIP_NOT_REQUIRED)

    # The review unit is itself a unit of this revision; counting it would make the revision look
    # eligible again the moment a human completes it.
    subjects = tuple(
        unit for unit in facts.units if unit.required_capability != FOLLOW_UP_CAPABILITY
    )
    if any(unit.state == _FAILED for unit in subjects):
        return DueDecision(facts.revision_id, None, SKIP_UNSETTLED_FAILED_UNIT)
    if any(unit.state not in (_COMPLETED, _CANCELLED) for unit in subjects):
        return DueDecision(facts.revision_id, None, SKIP_UNITS_IN_FLIGHT)
    if not any(unit.state == _COMPLETED for unit in subjects):
        return DueDecision(facts.revision_id, None, SKIP_NO_COMPLETED_UNIT)

    settled = [unit.settled_at for unit in subjects if unit.settled_at is not None]
    if not settled:
        return DueDecision(facts.revision_id, None, SKIP_UNITS_IN_FLIGHT)
    due_at = max(settled) + timedelta(days=due_after_days)
    if now < due_at:
        return DueDecision(facts.revision_id, due_at, SKIP_NOT_YET_DUE)
    return DueDecision(facts.revision_id, due_at, None)
```

- [ ] **Step 9: Run the predicate tests**

Run: `.venv/bin/pytest tests/services/test_follow_ups.py -v`
Expected: PASS, all 22 tests (10 validator + 12 predicate).

- [ ] **Step 10: Format and commit**

```bash
.venv/bin/ruff format src/orchestrator tests/services/test_follow_ups.py tests/test_config.py
.venv/bin/ruff check src/ tests/
git add -A
git commit -m "feat(wsp28): the due predicate, its config bound, and the review capability

evaluate_due is pure -- time is a parameter -- so the whole matrix is
table-tested without sleeping or ageing a row (updated_at is
trigger-rewritten and cannot be back-dated).

FAILED blocks with its own skip reason rather than folding into
units_in_flight: it is not terminal, so the outcome is undecided, and
'still working' vs 'stopped and nobody decided' are different operator
actions. follow_up_due_after_days is bounded at both ends so no
reachable value can silence the mechanism.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ac8GrBCxZqisLaXywQFG12"
```

---

### Task 4: The mint service

**Files:**
- Modify: `src/orchestrator/services/follow_ups.py`
- Modify: `tests/architecture/test_authority_write_once.py:54` (`CONSTRUCTION_SITES`)
- Test: `tests/services/test_follow_ups.py`

**Interfaces:**
- Consumes: `evaluate_due`, `validate_follow_up`, `FOLLOW_UP_CAPABILITY`, `Settings.follow_up_due_after_days`.
- Produces: `follow_ups.mint_due_follow_ups(session, *, actor: ActorContext, due_after_days: int) -> MintResult` where `MintResult` has `.minted: tuple[MintedFollowUp, ...]`, `.skipped: tuple[SkippedRevision, ...]`, `.considered: int`. Commits.

> This adds a **third** `WorkUnit(...)` construction site and will red `test_the_named_construction_sites_still_exist`. That is the test doing its job: re-verify that the envelope is assigned once at construction and never mutated, then update `CONSTRUCTION_SITES`. Do not weaken the test.

- [ ] **Step 1: Write the failing mint tests**

Add to `tests/services/test_follow_ups.py` (these need the DB, so they use `migrated_session`):

```python
from sqlalchemy import select

from orchestrator.kernel.authority import normalize_authority
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import Event, WorkPackageRevision, WorkUnit
from orchestrator.services.follow_ups import mint_due_follow_ups
from orchestrator.services.lifecycle import ActorContext
from orchestrator.capability_vocabulary import RUNNER_CAPABILITIES

SYSTEM = ActorContext("system", ActorRole.SYSTEM)


def test_a_due_revision_mints_exactly_one_unit(migrated_session, due_revision) -> None:
    result = mint_due_follow_ups(migrated_session, actor=SYSTEM, due_after_days=0)

    assert len(result.minted) == 1
    migrated_session.expire_all()
    unit = migrated_session.get(WorkUnit, result.minted[0].work_unit_id)
    assert unit is not None
    assert unit.state == WorkUnitState.AWAITING_REVIEW
    assert unit.required_capability == "follow_up_review"
    assert unit.authority_approval_id is None
    assert unit.decomposition_approved_by == "system"
    assert unit.max_attempts == 1


def test_the_minted_envelope_carries_no_runner_capability(migrated_session, due_revision) -> None:
    """A minted unit must be structurally unable to reach a runner: no runner capability, no
    target repository, no command list."""
    result = mint_due_follow_ups(migrated_session, actor=SYSTEM, due_after_days=0)
    unit = migrated_session.get(WorkUnit, result.minted[0].work_unit_id)

    envelope = normalize_authority(unit.authority)
    assert set(envelope.capabilities) & RUNNER_CAPABILITIES == set()
    assert envelope.constraints == {}
    assert envelope.change_class is None


def test_the_stored_envelope_is_a_fixed_point_of_normalisation(
    migrated_session, due_revision
) -> None:
    """Storing a raw dict makes normalized() a non-fixed-point on re-read, and the re-derived
    fingerprint then disagrees with the one that was minted."""
    from orchestrator.kernel.authority import authority_fingerprint

    result = mint_due_follow_ups(migrated_session, actor=SYSTEM, due_after_days=0)
    unit = migrated_session.get(WorkUnit, result.minted[0].work_unit_id)

    assert authority_fingerprint(normalize_authority(unit.authority)) == unit.authority_fingerprint


def test_running_the_pass_twice_mints_nothing_new(migrated_session, due_revision) -> None:
    first = mint_due_follow_ups(migrated_session, actor=SYSTEM, due_after_days=0)
    second = mint_due_follow_ups(migrated_session, actor=SYSTEM, due_after_days=0)

    assert len(first.minted) == 1
    assert second.minted == ()
    assert [row.reason for row in second.skipped] == ["already_minted"]
    assert migrated_session.scalar(
        select(func.count()).select_from(WorkUnit).where(
            WorkUnit.required_capability == "follow_up_review"
        )
    ) == 1


def test_the_declaration_prose_reaches_the_unit(migrated_session, due_revision) -> None:
    result = mint_due_follow_ups(migrated_session, actor=SYSTEM, due_after_days=0)
    unit = migrated_session.get(WorkUnit, result.minted[0].work_unit_id)

    assert "Revisit:" in unit.outcome
    assert "After the next quarterly review." in unit.outcome


def test_a_degenerate_declaration_still_produces_a_legible_unit(
    migrated_session, degenerate_due_revision
) -> None:
    """required:true with everything else null is a VALID declaration. It must not yield an
    empty outcome the reviewer cannot act on."""
    result = mint_due_follow_ups(migrated_session, actor=SYSTEM, due_after_days=0)
    unit = migrated_session.get(WorkUnit, result.minted[0].work_unit_id)

    assert unit.outcome.strip() != ""
    assert "Signals" not in unit.outcome


def test_one_malformed_declaration_does_not_abort_the_pass(
    migrated_session, due_revision, malformed_revision
) -> None:
    """Per-item fail-open with a counted skip -- the ADR-0002 discipline. A pass that dies on
    item three and discards items one and two reports nothing about either."""
    result = mint_due_follow_ups(migrated_session, actor=SYSTEM, due_after_days=0)

    assert len(result.minted) == 1
    assert "declaration_malformed" in {row.reason for row in result.skipped}


def test_minting_writes_one_event_per_unit(migrated_session, due_revision) -> None:
    result = mint_due_follow_ups(migrated_session, actor=SYSTEM, due_after_days=0)

    events = migrated_session.scalars(
        select(Event).where(Event.action == "follow_up_unit.created")
    ).all()
    assert len(events) == 1
    assert events[0].subject_id == result.minted[0].work_unit_id
```

Add the fixtures at the top of the module. They build a revision with a settled unit by reusing the existing factory and then driving the unit to COMPLETED through the public lifecycle, so the event ledger carries a real `to_state="completed"` row:

```python
import pytest
from sqlalchemy import func

from orchestrator.services.lifecycle import TransitionCommand, transition_unit
from tests.services.test_dependencies import register_unit

DECLARATION = {
    "required": True,
    "revisit_when": "After the next quarterly review.",
    "signals": ["A guard nobody triaged."],
    "owner": "devon",
}


def _settled_revision(session, key: str, declaration) -> WorkPackageRevision:
    unit = register_unit(session, key)
    revision = session.get(WorkPackageRevision, unit.work_package_revision_id)
    revision.follow_up = declaration
    for target in (
        WorkUnitState.READY,
        WorkUnitState.CLAIMED,
        WorkUnitState.EXECUTING,
    ):
        unit.state = target
    session.flush()
    # Drive the final hop through the ledger so `to_state="completed"` really exists: the
    # predicate reads settling time from Event.occurred_at, never from updated_at.
    session.add(
        Event(
            subject_type="work_unit",
            subject_id=unit.id,
            action="work_unit.transitioned",
            to_state=WorkUnitState.COMPLETED,
            actor_id="human-1",
            actor_role=ActorRole.HUMAN,
            idempotency_key=f"settle:{unit.id}",
            payload={},
        )
    )
    unit.state = WorkUnitState.COMPLETED
    session.flush()
    return revision


@pytest.fixture
def due_revision(migrated_session):
    return _settled_revision(migrated_session, "wsp28-due", DECLARATION)


@pytest.fixture
def degenerate_due_revision(migrated_session):
    return _settled_revision(
        migrated_session,
        "wsp28-degenerate",
        {"required": True, "revisit_when": None, "signals": [], "owner": None},
    )


@pytest.fixture
def malformed_revision(migrated_session):
    return _settled_revision(migrated_session, "wsp28-malformed", {"required": "yes"})
```

> **Verify the `Event` constructor signature against `persistence/models.py` before running.** If a field name differs, fix the fixture — do not change the assertion. If `transition_unit` is easier than a hand-written `Event`, use it; what matters is that a real `to_state="completed"` event row exists, because that is what the predicate reads.

- [ ] **Step 2: Run and confirm failure**

Run: `.venv/bin/pytest tests/services/test_follow_ups.py -k mint -v`
Expected: FAIL — `ImportError: cannot import name 'mint_due_follow_ups'`

- [ ] **Step 3: Write the mint service**

Append to `src/orchestrator/services/follow_ups.py`:

```python
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
from orchestrator.errors import DomainError
from orchestrator.kernel.authority import authority_fingerprint, normalize_authority
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import Event, WorkPackageRevision, WorkUnit
from orchestrator.services.lifecycle import ActorContext

_SETTLED_STATES = (WorkUnitState.COMPLETED, WorkUnitState.CANCELLED)
_MINT_ACTION = "follow_up_unit.created"
_DEFAULT_REVISIT = "No revisit condition was declared; confirm whether this outcome still holds."


@dataclass(frozen=True)
class MintedFollowUp:
    work_unit_id: uuid.UUID
    work_package_revision_id: uuid.UUID
    due_at: datetime


@dataclass(frozen=True)
class SkippedRevision:
    work_package_revision_id: uuid.UUID
    reason: str


@dataclass(frozen=True)
class MintResult:
    minted: tuple[MintedFollowUp, ...]
    skipped: tuple[SkippedRevision, ...]
    considered: int


def _authorize_actor(actor: ActorContext) -> None:
    if actor.role is not ActorRole.SYSTEM:
        raise DomainError(
            "role_forbidden",
            "only the orchestrator system actor may mint follow-up reviews",
            None,
        )


def follow_up_unit_id(revision_id: uuid.UUID) -> uuid.UUID:
    """Content-addressed, so a second pass cannot create a second row.

    This is the structural half of the idempotency story; the already-minted skip is the
    reporting half. The unique constraint on (work_package_revision_id, unit_key) is the backstop
    if both are somehow bypassed.
    """
    return uuid.uuid5(uuid.NAMESPACE_URL, f"sds:follow-up:{revision_id}")


def _describe(declaration: dict[str, object]) -> str:
    revisit = declaration.get("revisit_when") or _DEFAULT_REVISIT
    lines = [f"Revisit: {revisit}"]
    signals = declaration.get("signals") or []
    if signals:
        lines.append("Signals:")
        lines.extend(f"- {signal}" for signal in signals)
    owner = declaration.get("owner")
    if owner:
        lines.append(f"Owner: {owner}")
    return "\n".join(lines)


def _revision_facts(session: Session, revision: WorkPackageRevision) -> RevisionFacts:
    rows = session.execute(
        select(WorkUnit.id, WorkUnit.required_capability, WorkUnit.state).where(
            WorkUnit.work_package_revision_id == revision.id
        )
    ).all()
    units = []
    has_review_unit = False
    for unit_id, capability, state in rows:
        if capability == FOLLOW_UP_CAPABILITY:
            has_review_unit = True
        settled_at = None
        if state in _SETTLED_STATES:
            settled_at = session.scalar(
                select(func.max(Event.occurred_at)).where(
                    Event.subject_type == "work_unit",
                    Event.subject_id == unit_id,
                    Event.to_state == state,
                )
            )
        units.append(UnitFacts(capability, state, settled_at))
    return RevisionFacts(revision.id, revision.follow_up, tuple(units), has_review_unit)


def _mint(
    session: Session,
    revision: WorkPackageRevision,
    declaration: dict[str, object],
    actor: ActorContext,
    now: datetime,
) -> WorkUnit:
    authority = normalize_authority(
        {
            "capabilities": {FOLLOW_UP_CAPABILITY: "allowed"},
            "budgets": {"max_attempts": 1},
        }
    )
    unit = WorkUnit(
        id=follow_up_unit_id(revision.id),
        work_package_revision_id=revision.id,
        unit_key=f"follow-up:{revision.id}",
        title="Follow-up review",
        outcome=_describe(declaration),
        state=WorkUnitState.AWAITING_REVIEW,
        # The system self-attests: ck_work_units_approved_beyond_draft requires both approval
        # columns for any state other than draft, and this unit had no decomposition.
        decomposition_approved_by=actor.actor_id,
        decomposition_approved_at=now,
        required_capability=FOLLOW_UP_CAPABILITY,
        authority=authority.normalized(),
        authority_fingerprint=authority_fingerprint(authority),
        max_attempts=1,
    )
    session.add(unit)
    session.flush()
    session.add(
        Event(
            subject_type="work_unit",
            subject_id=unit.id,
            action=_MINT_ACTION,
            to_state=WorkUnitState.AWAITING_REVIEW,
            actor_id=actor.actor_id,
            actor_role=actor.role,
            idempotency_key=f"{_MINT_ACTION}:{unit.id}",
            payload={"command": {"work_package_revision_id": str(revision.id)}},
        )
    )
    session.flush()
    return unit


def mint_due_follow_ups(
    session: Session,
    *,
    actor: ActorContext,
    due_after_days: int,
) -> MintResult:
    """One pass over approved revisions, minting whatever is due. Externally invoked; nothing
    loops and nothing schedules itself.

    Per-item fail-open with a counted skip: one unusable revision is passed over and reported,
    never allowed to abort the pass and discard the revisions already handled.
    """
    _authorize_actor(actor)
    now = TransactionClock().now(session)
    revisions = session.scalars(
        select(WorkPackageRevision).order_by(WorkPackageRevision.registered_at)
    ).all()
    minted: list[MintedFollowUp] = []
    skipped: list[SkippedRevision] = []
    for revision in revisions:
        try:
            declaration = validate_follow_up(revision.follow_up)
        except DomainError:
            skipped.append(SkippedRevision(revision.id, SKIP_DECLARATION_MALFORMED))
            continue
        if declaration is None or declaration["required"] is not True:
            continue
        decision = evaluate_due(
            _revision_facts(session, revision), now=now, due_after_days=due_after_days
        )
        if decision.skip_reason is not None:
            skipped.append(SkippedRevision(revision.id, decision.skip_reason))
            continue
        assert decision.due_at is not None
        unit = _mint(session, revision, declaration, actor, now)
        minted.append(MintedFollowUp(unit.id, revision.id, decision.due_at))
    session.commit()
    return MintResult(tuple(minted), tuple(skipped), len(revisions))
```

> Note the `not_required` case returns `continue` without recording a skip: every revision that never asked for a follow-up would otherwise flood the response. Malformed declarations ARE recorded, because those are a real defect an operator must see.

- [ ] **Step 4: Run the mint tests**

Run: `.venv/bin/pytest tests/services/test_follow_ups.py -v`
Expected: PASS.

- [ ] **Step 5: Run the write-once guard and watch it fail**

Run: `.venv/bin/pytest tests/architecture/test_authority_write_once.py -v`
Expected: FAIL on `test_the_named_construction_sites_still_exist` — a third construction site appeared.

Before changing the set, verify the premise it protects by reading `_mint` above: `authority=` is assigned once, in the constructor, from a locally-built envelope; nothing afterwards touches `unit.authority`; no `setattr`, no `update(...).values(authority=…)`, no subscript write. Only then:

```python
CONSTRUCTION_SITES = {
    "orchestrator/services/packages.py",
    "orchestrator/services/deployment_observations.py",
    # WS-P2.8: the package-declared follow-up review unit. Same shape as the release-observation
    # site above -- a frozen envelope built inline, assigned once at construction, never mutated.
    "orchestrator/services/follow_ups.py",
}
```

- [ ] **Step 6: Re-run the full architecture suite**

Run: `.venv/bin/pytest tests/architecture/ -v`
Expected: PASS. If a scope guard fails on `follow_ups.py`, you wrote a forbidden token — reword.

- [ ] **Step 7: Format and commit**

```bash
.venv/bin/ruff format src/orchestrator/services/follow_ups.py tests/services/test_follow_ups.py tests/architecture/test_authority_write_once.py
.venv/bin/ruff check src/ tests/
git add -A
git commit -m "feat(wsp28): mint the declared follow-up review unit

Third WorkUnit construction site, following the WS-5.1 release-observation
precedent: deterministic uuid5 id, self-minted envelope carrying no runner
capability and no target repository, born past readiness and claims, with
the SYSTEM actor self-attesting the approval columns the CHECK requires.

CONSTRUCTION_SITES updated only after re-verifying the write-once premise
the guard exists to protect.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ac8GrBCxZqisLaXywQFG12"
```

---
### Task 5: Generated criteria and the adjudication carve-out

**Files:**
- Modify: `src/orchestrator/services/lifecycle.py:41` (`FOLLOW_UP_AC_ID`, `required_ac_ids`)
- Modify: `src/orchestrator/services/verifier_criteria.py:15,90`
- Modify: `src/orchestrator/services/evidence.py:554,591`
- Test: `tests/services/test_follow_ups.py`

**Interfaces:**
- Consumes: `FOLLOW_UP_CAPABILITY`, a minted unit from Task 4.
- Produces: `lifecycle.FOLLOW_UP_AC_ID: str` = `"follow-up-review"`; `load_required_criteria` returns exactly one criterion for a review unit; `record_adjudication` accepts a HUMAN `passed` on it with no `allow_*` flag.

> **The asymmetry is the point.** Generated *release-observation* AC ids are verifier-owned and public adjudication must reject them (`post_deploy_verifier_required`). The generated follow-up AC must do the **opposite**: a human adjudicates it directly through `/review`. Two carve-outs in the same function pointing opposite ways — a future reader will assume they match, so Step 6 asserts both directions.
>
> `web.py::_adjudicatable_criteria` needs **no change**: it filters only `POST_DEPLOY_AC_IDS`, and `"follow-up-review"` is not in that tuple, so the form renders with `is_judgment=True` (evidence type `observation` is in `JUDGMENT_TYPES`). Step 7 pins that rather than assuming it.

- [ ] **Step 1: Write the failing criteria test**

```python
from orchestrator.services.verifier_criteria import load_required_criteria


def test_a_review_unit_requires_exactly_one_generated_criterion(
    migrated_session, due_revision
) -> None:
    """Without a generated branch this falls through to the revision's FULL package AC set and
    the human is asked to re-adjudicate the entire original package."""
    result = mint_due_follow_ups(migrated_session, actor=SYSTEM, due_after_days=0)
    unit = migrated_session.get(WorkUnit, result.minted[0].work_unit_id)
    revision = migrated_session.get(WorkPackageRevision, unit.work_package_revision_id)

    criteria = load_required_criteria(migrated_session, unit, revision)

    assert [criterion.ac_id for criterion in criteria] == ["follow-up-review"]
    assert criteria[0].evidence_type == "observation"
    assert criteria[0].condition.strip() != ""
    assert criteria[0].evidence.strip() != ""
    assert criteria[0].approver == "devon"


def test_a_degenerate_declaration_still_yields_a_non_empty_criterion(
    migrated_session, degenerate_due_revision
) -> None:
    """approver falls back to revision.approved_by, which is NOT NULL and CHECK-non-empty."""
    result = mint_due_follow_ups(migrated_session, actor=SYSTEM, due_after_days=0)
    unit = migrated_session.get(WorkUnit, result.minted[0].work_unit_id)
    revision = migrated_session.get(WorkPackageRevision, unit.work_package_revision_id)

    criteria = load_required_criteria(migrated_session, unit, revision)

    assert criteria[0].approver == revision.approved_by
    assert criteria[0].evidence.strip() != ""
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `.venv/bin/pytest tests/services/test_follow_ups.py -k criterion -v`
Expected: FAIL — the criteria list is the package's ACs, not `["follow-up-review"]`.

- [ ] **Step 3: Add the AC id and the required-ids branch**

In `src/orchestrator/services/lifecycle.py`, immediately after `POST_DEPLOY_AC_IDS`:

```python
# The single source of truth for the generated follow-up review AC id. Same producer/consumer
# split as the tuple above: this module PRODUCES it (required_ac_ids for a review unit) and
# `services.evidence` imports it to decide subject validity. One copy only.
#
# It is deliberately NOT gated the way the ids above are. Those are verifier-owned and public
# adjudication must refuse them; this one is human-owned by design and public adjudication must
# ACCEPT it. Two rules pointing opposite ways, asserted in both directions in the tests.
FOLLOW_UP_AC_ID = "follow-up-review"
```

In `required_ac_ids`, after the existing generated branch:

```python
    if _is_generated_follow_up_unit(unit):
        return (FOLLOW_UP_AC_ID,)
```

and add the predicate near `_is_generated_post_deploy_unit`:

```python
def _is_generated_follow_up_unit(unit: WorkUnit) -> bool:
    """A pure attribute check -- the capability IS the marker, so no join is needed."""
    return unit.required_capability == "follow_up_review"
```

- [ ] **Step 4: Add the generated criteria branch**

In `src/orchestrator/services/verifier_criteria.py::load_required_criteria`, after the existing generated branch:

```python
    follow_up = _generated_follow_up_criteria(unit, revision)
    if follow_up is not None:
        return follow_up
```

and append to the module:

```python
_FOLLOW_UP_DEFAULT_REVISIT = (
    "No revisit condition was declared; confirm whether this outcome still holds."
)


def _generated_follow_up_criteria(
    unit: WorkUnit,
    revision: WorkPackageRevision,
) -> tuple[PackageAcceptanceCriterion, ...] | None:
    """The one criterion a package-declared follow-up review is discharged against.

    `observation` is already in JUDGMENT_TYPES and already accepted by the intake vocabulary
    gate, so this adds no evidence type and no vocabulary migration. It evaluates to
    `judgment_required`, which is what routes the unit to a human rather than to an evaluator.

    Every field falls back, because `revisit_when` and `owner` are nullable in the schema and
    `{"required": true, "revisit_when": null, "signals": [], "owner": null}` is a valid
    declaration -- one that would otherwise produce a criterion a reviewer cannot act on.
    """
    if unit.required_capability != "follow_up_review":
        return None
    declaration = revision.follow_up if isinstance(revision.follow_up, dict) else {}
    revisit = declaration.get("revisit_when") or _FOLLOW_UP_DEFAULT_REVISIT
    owner = declaration.get("owner") or revision.approved_by
    return (
        PackageAcceptanceCriterion(
            work_package_revision_id=revision.id,
            ac_id=FOLLOW_UP_AC_ID,
            condition="The follow-up questions declared by the package were answered.",
            evidence_type="observation",
            evidence=str(revisit),
            approver=str(owner),
        ),
    )
```

Add `from orchestrator.services.lifecycle import FOLLOW_UP_AC_ID` to the imports.

- [ ] **Step 5: Run the criteria tests**

Run: `.venv/bin/pytest tests/services/test_follow_ups.py -k criterion -v`
Expected: PASS.

- [ ] **Step 6: Write the two-direction adjudication test**

```python
from orchestrator.errors import DomainError
from orchestrator.services.evidence import record_adjudication

HUMAN = ActorContext("human-1", ActorRole.HUMAN)


def test_a_human_may_adjudicate_the_generated_follow_up_criterion(
    migrated_session, due_revision
) -> None:
    result = mint_due_follow_ups(migrated_session, actor=SYSTEM, due_after_days=0)
    unit = migrated_session.get(WorkUnit, result.minted[0].work_unit_id)

    adjudication = record_adjudication(
        migrated_session,
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        ac_id="follow-up-review",
        outcome="passed",
        actor=HUMAN,
        rationale="Reviewed; the outcome still holds.",
        idempotency_key="follow-up-adjudication-1",
    )

    assert not isinstance(adjudication, DomainError)
    assert adjudication.outcome == "passed"


def test_the_release_observation_criteria_still_refuse_public_adjudication(
    migrated_session,
) -> None:
    """The counterpart carve-out points the OTHER way and must stay that way. Without this,
    a future reader assumes the two generated-AC rules match and loosens the wrong one."""
    from tests.services.test_deployment_observations import observation_command, release_binding
    from orchestrator.services.deployment_observations import record_deployment_observation

    _unit, binding = release_binding(migrated_session, key="wsp28-asymmetry")
    observation = record_deployment_observation(
        migrated_session, observation_command(binding, key="wsp28-asymmetry-observation")
    )

    result = record_adjudication(
        migrated_session,
        work_package_revision_id=observation.work_package_revision_id,
        work_unit_id=observation.post_deploy_work_unit_id,
        ac_id="post-deploy-artifact",
        outcome="passed",
        actor=HUMAN,
        rationale="attempting the wrong lane",
        idempotency_key="wsp28-asymmetry-adjudication",
    )

    assert isinstance(result, DomainError)
    assert result.code == "post_deploy_verifier_required"
```

- [ ] **Step 7: Run it, confirm the first fails**

Run: `.venv/bin/pytest tests/services/test_follow_ups.py -k adjudicat -v`
Expected: the human-adjudication test FAILS with `evidence_subject_invalid` (the AC id is not in `enforcement_snapshot["acceptance_criteria"]`); the asymmetry test already PASSES.

- [ ] **Step 8: Add the subject carve-out — with no `allow_*` flag**

In `src/orchestrator/services/evidence.py`, add the predicate beside `_is_generated_post_deploy_subject`:

```python
def _is_generated_follow_up_subject(session: Session, unit_id: uuid.UUID, ac_id: str) -> bool:
    """The generated follow-up criterion, which a HUMAN owns.

    No `allow_*` parameter, deliberately: unlike the verifier-owned generated ids above, this one
    is meant to be adjudicated from the public `/review` form. Gating it would make the unit
    undischargeable by the only actor designed to discharge it.
    """
    if ac_id != FOLLOW_UP_AC_ID:
        return False
    unit = session.get(WorkUnit, unit_id)
    return unit is not None and unit.required_capability == "follow_up_review"
```

and widen the subject check in `_validated_subject`:

```python
    generated_post_deploy = _is_generated_post_deploy_subject(session, revision_id, unit_id, ac_id)
    generated_follow_up = _is_generated_follow_up_subject(session, unit_id, ac_id)
    if generated_post_deploy and not allow_generated_post_deploy:
        raise DomainError(
            "post_deploy_verifier_required",
            "post-deploy verification adjudications must be recorded by the verifier command",
            "verify",
        )
    if (
        unit is None
        or revision is None
        or unit.work_package_revision_id != revision_id
        or (
            not generated_post_deploy
            and not generated_follow_up
            and (not isinstance(acceptance_criteria, list) or ac_id not in acceptance_criteria)
        )
    ):
```

Extend the existing import to `from orchestrator.services.lifecycle import FOLLOW_UP_AC_ID, POST_DEPLOY_AC_IDS, ActorContext`.

`_authorize_outcome` needs **no change**: it keys the human `passed` on the criterion's static `evidence_type` being in `JUDGMENT_TYPES`, and `observation` is.

- [ ] **Step 9: Pin the `/review` form rendering**

```python
def test_the_review_form_offers_a_human_outcome_for_the_review_unit(
    migrated_session, due_revision
) -> None:
    """web._adjudicatable_criteria filters POST_DEPLOY_AC_IDS. The follow-up id is not in that
    tuple, so it renders -- but that is a property worth pinning, not assuming."""
    from orchestrator.web import _adjudicatable_criteria

    result = mint_due_follow_ups(migrated_session, actor=SYSTEM, due_after_days=0)
    unit = migrated_session.get(WorkUnit, result.minted[0].work_unit_id)
    revision = migrated_session.get(WorkPackageRevision, unit.work_package_revision_id)

    rows = _adjudicatable_criteria(migrated_session, unit, revision)

    assert [row["ac_id"] for row in rows] == ["follow-up-review"]
    assert rows[0]["is_judgment"] is True
```

- [ ] **Step 10: Run the whole file, then the architecture suite**

Run: `.venv/bin/pytest tests/services/test_follow_ups.py tests/services/test_deployment_observations.py tests/web/ -v`
Then: `.venv/bin/pytest tests/architecture/ -v`
Expected: PASS. `test_unreachable_guards` in particular — every function added here has a production caller.

- [ ] **Step 11: Format and commit**

```bash
.venv/bin/ruff format src/orchestrator tests/services/test_follow_ups.py
.venv/bin/ruff check src/ tests/
git add -A
git commit -m "feat(wsp28): generated review criterion and its human adjudication path

One generated criterion, evidence_type observation -- already in
JUDGMENT_TYPES and already accepted at the intake gate, so no new
vocabulary and no migration. Without it, load_required_criteria falls
through to the revision's full package AC set and the reviewer is asked
to re-adjudicate the whole original package.

The subject carve-out takes NO allow_* flag, opposite to the
verifier-owned generated ids beside it. Both directions asserted, since a
future reader will assume the two rules match.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ac8GrBCxZqisLaXywQFG12"
```

---

## Increment 3 — The invoker

### Task 6: The route, its schemas, and the two inventories

**Files:**
- Modify: `src/orchestrator/api/schemas.py`, `src/orchestrator/api/routes.py`
- Modify: `tests/architecture/test_scope_guards.py:45`, `tests/idempotency/matrix.py`
- Test: `tests/api/test_follow_ups_api.py`, `tests/idempotency/test_follow_up_idempotency.py`

**Interfaces:**
- Consumes: `mint_due_follow_ups`, `Settings.follow_up_due_after_days`.
- Produces: `POST /api/v1/follow-ups/mint` → `FollowUpMintResponse`.

- [ ] **Step 1: Write the failing API tests**

Create `tests/api/test_follow_ups_api.py`:

```python
from fastapi.testclient import TestClient

SYSTEM = {"Authorization": "Bearer system-token", "X-Credential-Key-Id": "orchestrator-system"}
WORKER = {"Authorization": "Bearer worker-token", "X-Credential-Key-Id": "factory-runner-github"}


def test_mint_requires_the_system_actor(db_client: TestClient) -> None:
    response = db_client.post(
        "/api/v1/follow-ups/mint",
        headers=WORKER,
        json={"idempotency_key": "mint-1", "expected_version": 0},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "role_forbidden"


def test_mint_rejects_a_non_zero_expected_version(db_client: TestClient) -> None:
    response = db_client.post(
        "/api/v1/follow-ups/mint",
        headers=SYSTEM,
        json={"idempotency_key": "mint-2", "expected_version": 3},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "version_conflict"


def test_mint_on_an_empty_ledger_returns_counted_nothing(db_client: TestClient) -> None:
    response = db_client.post(
        "/api/v1/follow-ups/mint",
        headers=SYSTEM,
        json={"idempotency_key": "mint-3", "expected_version": 0},
    )

    assert response.status_code == 200
    assert response.json() == {"minted": [], "skipped": [], "considered": 0}
```

> Match the auth-header style used by the rest of `tests/api/` — read `tests/api/conftest.py` and an existing SYSTEM-role test before writing these headers, and fix the constants above to match. The assertions are what matter.

- [ ] **Step 2: Run and confirm failure**

Run: `.venv/bin/pytest tests/api/test_follow_ups_api.py -v`
Expected: FAIL — 404, the route does not exist.

- [ ] **Step 3: Add the schemas**

In `src/orchestrator/api/schemas.py`:

```python
class FollowUpMintCommand(CommandBase):
    """One minting pass. It has no single subject, so `expected_version` carries no meaning here
    and only 0 is accepted -- the same contract the observation ingress uses. Per-unit
    idempotency is structural: the unit id is content-addressed from the revision id, so a
    re-run under a fresh key still mints nothing new."""


class MintedFollowUpResponse(BaseModel):
    work_unit_id: UUID
    work_package_revision_id: UUID
    due_at: datetime


class SkippedRevisionResponse(BaseModel):
    work_package_revision_id: UUID
    # A second copy of the service's skip-reason strings, because `Literal` needs literals and
    # cannot be built from constants. Kept honest by a sync test rather than by hope --
    # see the assertion in Step 5a.
    reason: Literal[
        "not_required",
        "no_completed_unit",
        "units_in_flight",
        "unsettled_failed_unit",
        "not_yet_due",
        "already_minted",
        "declaration_malformed",
    ]


class FollowUpMintResponse(BaseModel):
    """Counters and reasons, not just a status. A skip is counted so a miss is observable."""

    minted: list[MintedFollowUpResponse]
    skipped: list[SkippedRevisionResponse]
    considered: int
```

- [ ] **Step 4: Add the route**

In `src/orchestrator/api/routes.py`, next to the reconciliation routes:

```python
@router.post("/follow-ups/mint", response_model=FollowUpMintResponse)
def mint_follow_ups(
    body: FollowUpMintCommand,
    actor: ActorDep,
    session: SessionDep,
    settings: SettingsDep,
) -> object:
    """Mint the work units whose package-declared follow-up reviews have come due.

    SYSTEM-only, externally invoked, and a pure database read plus an append-only write: no
    outbound call, no loop, nothing scheduled. The role gate lives in the service.
    """
    _require_zero_expected_version(body.expected_version, "follow-up minting")
    result = mint_due_follow_ups(
        session,
        actor=actor,
        due_after_days=settings.follow_up_due_after_days,
    )
    return {
        "minted": [
            {
                "work_unit_id": row.work_unit_id,
                "work_package_revision_id": row.work_package_revision_id,
                "due_at": row.due_at,
            }
            for row in result.minted
        ],
        "skipped": [
            {"work_package_revision_id": row.work_package_revision_id, "reason": row.reason}
            for row in result.skipped
        ],
        "considered": result.considered,
    }
```

- [ ] **Step 5: Run the API tests**

Run: `.venv/bin/pytest tests/api/test_follow_ups_api.py -v`
Expected: PASS.

- [ ] **Step 5a: Pin the two copies of the skip-reason vocabulary together**

`SkippedRevisionResponse.reason` restates the seven service constants because `Literal` cannot be
built from names. Two copies with nothing coupling them is exactly the drift this repo has been
bitten by, so couple them. Add to `tests/api/test_follow_ups_api.py`:

```python
from typing import get_args

from orchestrator.api.schemas import SkippedRevisionResponse
from orchestrator.services import follow_ups


def test_the_response_vocabulary_matches_the_services_skip_reasons() -> None:
    """The Literal is a second copy by necessity. This is what keeps it a copy and not a fork."""
    declared = set(get_args(SkippedRevisionResponse.model_fields["reason"].annotation))
    service = {
        value
        for name, value in vars(follow_ups).items()
        if name.startswith("SKIP_") and isinstance(value, str)
    }

    assert declared == service
```

Run: `.venv/bin/pytest tests/api/test_follow_ups_api.py -v`
Expected: PASS.

- [ ] **Step 6: Update the POST route inventory**

`tests/architecture/test_scope_guards.py`, inside `test_production_post_route_inventory_is_explicit`:

```python
        # WS-P2.8: mints the work units whose package-declared follow-up reviews have come due.
        # SYSTEM-only, externally invoked; a database read plus an append-only write, with no
        # outbound call and no loop.
        "/api/v1/follow-ups/mint",
```

- [ ] **Step 7: Add the idempotency matrix mechanism and row**

`tests/idempotency/matrix.py` — a genuinely new mechanism, so it gets its own named constant:

```python
# The minting pass has no per-subject idempotency key: the work unit's id is CONTENT-ADDRESSED
# (uuid5 over the revision id), so a duplicate delivery cannot create a second row. The pass
# reports `already_minted` rather than raising, and the unique (work_package_revision_id,
# unit_key) constraint is the backstop if both the id and the pre-check were bypassed.
CONTENT_ADDRESSED_SUBJECT = (
    "uuid5 subject id + unique (work_package_revision_id, unit_key) + already_minted skip, "
    "NO advisory lock"
)
```

and the row:

```python
    MatrixRow(
        "follow-up minting",
        "/api/v1/follow-ups/mint",
        CONTENT_ADDRESSED_SUBJECT,
        "tests/idempotency/test_follow_up_idempotency.py::test_a_duplicate_minting_pass_creates_one_unit",
    ),
```

- [ ] **Step 8: Write the named idempotency test**

Create `tests/idempotency/test_follow_up_idempotency.py`:

```python
"""The minting pass's duplicate-delivery story (matrix row: follow-up minting)."""

from fastapi.testclient import TestClient

SYSTEM = {"Authorization": "Bearer system-token", "X-Credential-Key-Id": "orchestrator-system"}


def test_a_duplicate_minting_pass_creates_one_unit(db_client: TestClient) -> None:
    """Two passes under DIFFERENT keys still mint once: the unit id is content-addressed from
    the revision id, so idempotency does not depend on the caller reusing a key."""
    first = db_client.post(
        "/api/v1/follow-ups/mint",
        headers=SYSTEM,
        json={"idempotency_key": "mint-pass-a", "expected_version": 0},
    )
    second = db_client.post(
        "/api/v1/follow-ups/mint",
        headers=SYSTEM,
        json={"idempotency_key": "mint-pass-b", "expected_version": 0},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    minted_ids = {row["work_unit_id"] for row in first.json()["minted"]}
    assert {row["work_unit_id"] for row in second.json()["minted"]} & minted_ids == set()
    for row in second.json()["skipped"]:
        if row["work_package_revision_id"] in {
            m["work_package_revision_id"] for m in first.json()["minted"]
        }:
            assert row["reason"] == "already_minted"
```

> This needs a due revision in the fixture database. Reuse the `due_revision` fixture by re-exporting it from `tests/idempotency/conftest.py`, the same way that file already re-exports `ready_unit` and `deployed_binding`.

- [ ] **Step 9: Run the guards**

Run: `.venv/bin/pytest tests/architecture/ tests/idempotency/ -v`
Expected: PASS — `test_every_ingress_post_route_has_a_matrix_row`, `test_every_matrix_row_names_a_test_that_exists`, and the POST inventory all green.

- [ ] **Step 10: Format and commit**

```bash
.venv/bin/ruff format src/orchestrator tests/
.venv/bin/ruff check src/ tests/
git add -A
git commit -m "feat(wsp28): the SYSTEM minting route

A route rather than a new out-of-process runner: reconciliation_runner's
client exists specifically to forbid a runner-species process from
minting units, and the detect pass documents that it never writes
work_units. In-process keeps canonical writes where they belong and adds
no HTTP client to src/.

New idempotency mechanism (content-addressed subject id, no advisory
lock) with its matrix row and named test; POST inventory updated.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ac8GrBCxZqisLaXywQFG12"
```

---

### Task 7: CLI command and operator launcher

**Files:**
- Modify: `src/orchestrator/cli.py`
- Create: `scripts/run-follow-up-mint.sh`
- Test: `tests/cli/test_cli_contract.py`

**Interfaces:**
- Consumes: the route from Task 6.
- Produces: `orchestrator mint-follow-ups --idempotency-key <k> [--json]`.

> The CLI is a pure HTTP client — `test_cli_source_has_no_forbidden_domain_or_database_imports` forbids it importing domain or database modules. Do not import anything from `orchestrator.services`.

- [ ] **Step 1: Write the failing CLI test**

Add to `tests/cli/test_cli_contract.py`:

```python
def test_mint_follow_ups_posts_the_command_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, object] = {}

    def fake_request(method: str, path: str, payload=None):
        observed.update(method=method, path=path, payload=payload)
        return {"minted": [], "skipped": [], "considered": 0}

    monkeypatch.setattr("orchestrator.cli.request", fake_request)
    result = CliRunner().invoke(
        app, ["mint-follow-ups", "--idempotency-key", "mint-1", "--json"]
    )

    assert result.exit_code == 0
    assert observed == {
        "method": "POST",
        "path": "/api/v1/follow-ups/mint",
        "payload": {"idempotency_key": "mint-1", "expected_version": 0},
    }
```

- [ ] **Step 2: Run and confirm failure**

Run: `.venv/bin/pytest tests/cli/test_cli_contract.py -k mint_follow_ups -v`
Expected: FAIL — exit code 2, no such command.

- [ ] **Step 3: Add the command**

In `src/orchestrator/cli.py`, beside `reconcile-detect`:

```python
@app.command("mint-follow-ups")
def mint_follow_ups(
    idempotency_key: Annotated[str, typer.Option("--idempotency-key")],
    json_output: JsonOption = False,
) -> None:
    """Mint work units for package-declared follow-up reviews that have come due."""
    _run(
        lambda: request(
            "POST",
            "/api/v1/follow-ups/mint",
            {"idempotency_key": idempotency_key, "expected_version": 0},
        ),
        json_output,
    )
```

- [ ] **Step 4: Run the CLI suite**

Run: `.venv/bin/pytest tests/cli/ -v`
Expected: PASS, including the forbidden-imports test.

- [ ] **Step 5: Write the launcher**

Create `scripts/run-follow-up-mint.sh`:

```bash
#!/usr/bin/env bash
# Operator-invoked follow-up minting pass (WS-P2.8).
#
# No scheduler and no loop (ADR-0002/0003/0007): one pass, then exit. It mints the work units
# whose package-declared follow-up reviews have come due and prints a counted summary. It never
# changes any other unit's state.
#
# The credential is orchestrator-system. It must NOT be orchestrator-drift-reporter: that
# identity's registry profile is observe-and-propose, minting a work unit is canonical mutation,
# and agent_id attribution is permanent.
#
# Prerequisites:
#   - `uv pip install -e .` so the `orchestrator` entry point exists.
#   - The macOS login Keychain holds BWS_ACCESS_TOKEN_VPS_BACKUP (loaded by bws-token.sh).
#
# Usage:
#   scripts/run-follow-up-mint.sh [--json]
set -euo pipefail

# BWS UUID (value fetched at runtime; never stored in this repo). See .bws-secrets.toml.
SYSTEM_BEARER_UUID="221a48d5-3f29-4898-b300-b4820140c880"   # orchestrator-system SYSTEM bearer

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# shellcheck disable=SC1091
source "$HOME/Projects/vps-backup/bws-token.sh"

# `bws secret get <uuid>` returns JSON; extract only the "value" field, never echoing it.
_bws_value() {
  bws secret get "$1" | python3 -c 'import sys, json; print(json.load(sys.stdin)["value"])'
}

ORCHESTRATOR_API_TOKEN="$(_bws_value "$SYSTEM_BEARER_UUID")"
ORCHESTRATOR_API_URL="${ORCHESTRATOR_API_URL:-https://sds.alobar.net}"
ORCHESTRATOR_API_CREDENTIAL_KEY_ID="orchestrator-system"
export ORCHESTRATOR_API_TOKEN ORCHESTRATOR_API_URL ORCHESTRATOR_API_CREDENTIAL_KEY_ID

exec "$REPO_ROOT/.venv/bin/orchestrator" mint-follow-ups \
  --idempotency-key "follow-up-mint:$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  "$@"
```

- [ ] **Step 6: Check the script and confirm the BWS manifest**

```bash
chmod +x scripts/run-follow-up-mint.sh
shellcheck --severity=warning scripts/run-follow-up-mint.sh
grep -c '221a48d5-3f29-4898-b300-b4820140c880' .bws-secrets.toml
```

Expected: shellcheck clean; the grep returns ≥1 (the UUID is already in the manifest for the tracker launchers — if it returns 0, add the entry).

- [ ] **Step 7: Format and commit**

```bash
.venv/bin/ruff format src/orchestrator/cli.py tests/cli/test_cli_contract.py
git add -A
git commit -m "feat(wsp28): mint-follow-ups CLI command and operator launcher

Tested through the real Typer entrypoint with CliRunner, not by calling
the function -- a lone command collapses to top level, so the invocation
is the thing worth asserting.

The launcher fetches orchestrator-system at runtime by stable BWS UUID.
Deliberately not orchestrator-drift-reporter: that identity is
observe-and-propose and agent_id attribution is permanent.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ac8GrBCxZqisLaXywQFG12"
```

---
### Task 8: Runbook and ADR-0007

**Files:**
- Create: `docs/operations/follow-up-scheduling.md`
- Create: `docs/decisions/0007-declared-follow-up-scheduling.md`
- Modify: `docs/operations/observation-ingestion.md:100`

**Interfaces:** none — documentation only, but the ADR is the artifact that bounds the new capability, so it is not optional.

- [ ] **Step 1: Write the runbook**

Create `docs/operations/follow-up-scheduling.md` covering, in this order: what the mechanism does; the `follow_up` block's four fields and that only `required` is machine-read; the due predicate's five clauses including that `FAILED` blocks and why; `follow_up_due_after_days` and its bounds; how to run a pass (`scripts/run-follow-up-mint.sh`, and that `drift-audit.sh` runs one daily); how to read the counted output and each skip reason; how a human discharges a review unit (`/review/units/{id}` → adjudicate `follow-up-review` → Complete); what happens if nobody does (`dead-letter` reports it as `stalled_approval` past `dead_letter_stalled_approval_seconds`); and the retirement path for a moot one (`AWAITING_REVIEW → REVISION_REQUIRED → READY → FAILED → CANCELLED`, all through public surfaces).

State the forward-only limitation explicitly: revisions intaken before migration 0020 have `follow_up = NULL` and can never mint, and the declaration is not recoverable because the package YAML is never stored.

- [ ] **Step 2: Write ADR-0007**

Create `docs/decisions/0007-declared-follow-up-scheduling.md`. Status: Accepted 2026-07-28 by Devon. Precedents: ADR-0002, ADR-0003, ADR-0006. It must record:

- **Context.** WS-6.1 lists "create follow-up work units" as a non-goal; the observation spine is deliberately evidence-only. The `follow_up` block has been part of every approved package since WS-2.1 and nothing ever read it.
- **Decision.** System-side minting is bounded to declared follow-ups. The package (a human-approved artifact) supplies the *intent*; the orchestrator supplies only the *timing*; a human supplies the *discharge*. The orchestrator gains no scheduler.
- **Why a route and not a runner.** `reconciliation_runner/client.py` exists to forbid a runner-species process from minting units; `reconciliation_detection.py` states it never writes `work_units`. A new runner would contradict the first, folding into detect would contradict the second.
- **Mechanical guarantees** (the ADR-0003 four-item template): the query's only source is `follow_up->>'required' = 'true'`; the unit id is content-addressed; the envelope's capability set is disjoint from `RUNNER_CAPABILITIES` and carries no target repository, so a minted unit cannot reach a runner; per-item fail-open with counted skips.
- **This is not an observation→work bridge.** It reads no observation. Phase-3 sources ride it by *declaring* follow-ups in their proposed packages, never by acquiring minting authority.
- **Consequences.** Forward-only, no backfill. One follow-up per revision, forever; recurrence is WS-P2.10's, with the intent-packages schema tightening. `FAILED` blocks minting and nothing surfaces `FAILED` units awaiting disposition — backlogged as `6bcd7ee8b6b2`.
- **Scheduled trigger.** Unlike ADR-0002/0003, this one *is* wired on day one, via the existing daily `drift-audit.sh` run. The orchestrator still has no loop: an already-scheduled external operator job invokes a route.

- [ ] **Step 3: Amend the observation-ingestion non-goal**

In `docs/operations/observation-ingestion.md`, replace the `- create follow-up work units;` bullet with:

```markdown
- create follow-up work units — **superseded in part by ADR-0007.** WS-6.1's ingestion path still
  creates nothing. A package-declared `follow_up` block yields a work unit through the separate
  WS-P2.8 minting pass, which reads the declaration and the clock and never reads an observation.
  An observation still cannot create work.
```

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "docs(wsp28): runbook, ADR-0007, and the amended observation non-goal

ADR-0007 bounds the new capability: the package supplies intent, the
orchestrator supplies only timing, a human supplies discharge. Records
why this is a route rather than a runner, and states plainly that it
reads no observation -- Phase-3 sources ride it by declaring follow-ups,
never by acquiring minting authority.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ac8GrBCxZqisLaXywQFG12"
```

---

## Increment 5 — The folded-in tracker adapter fixes

### Task 9: `reconcile()` per-item fail-open and per-pass idempotency key

**Files:**
- Modify: `src/tracker_projection_adapter/cli.py:126-154` (`reconcile`), `:188-211` (`reconcile_command`)
- Test: `tests/tracker_projection_adapter/test_cli.py`, `tests/tracker_projection_adapter/test_cli_invocation.py`

**Interfaces:**
- Produces: `reconcile(client, projector, *, dry_run, pass_id) -> dict[str, int]` returning `{"reported": n, "skipped": m}`.

> Two independent defects, both live in production today. **(a)** The loop has no `try`/`except`, and `TodoistProjector._get` raises `RuntimeError` on any non-404 ≥400 — so a single 401/429/500 on item 3 of 50 propagates out and the two already-collected observations are discarded, because the report is one POST at the end. **(b)** The idempotency key is the constant `"tracker-detect-pass"`, so every pass ever run shares one key; compare the reconciliation runner's `f"reconcile-detect:{pass_id}"`.
>
> Verify each fix **differentially**: revert it and its test must red.

- [ ] **Step 1: Write the failing tests**

Add to `tests/tracker_projection_adapter/test_cli.py`:

```python
class ExplodingProjector(FakeProjector):
    """Raises on one specific item, the way TodoistProjector does on a non-404 >= 400."""

    def __init__(self, completed, explode_on):
        super().__init__(completed)
        self._explode_on = explode_on

    def item_completed(self, item_ref):
        if item_ref.external_item_id == self._explode_on:
            raise RuntimeError("todoist rejected GET /tasks/tid-2: 500")
        return super().item_completed(item_ref)


def _binding(item_id):
    return {
        "work_unit_id": f"u-{item_id}",
        "tracker_system": "todoist",
        "external_item_id": item_id,
        "external_url": None,
        "projected_state": "ready",
    }


def test_one_unreadable_item_does_not_discard_the_rest():
    """The report is a single POST at the end, so an abort mid-loop reports NOTHING about the
    items already read -- not merely the failing one."""
    client = FakeClient(bindings=[_binding("tid-1"), _binding("tid-2"), _binding("tid-3")])
    projector = ExplodingProjector({"tid-1": True, "tid-3": False}, explode_on="tid-2")

    counts = reconcile(client, projector, dry_run=False, pass_id="p1")

    assert counts == {"reported": 2, "skipped": 1}
    assert [row["external_item_id"] for row in client.reported] == ["tid-1", "tid-3"]


def test_each_pass_reports_under_its_own_idempotency_key():
    client = FakeClient(bindings=[_binding("tid-1")])
    projector = FakeProjector(completed={"tid-1": True})

    reconcile(client, projector, dry_run=False, pass_id="p1")

    assert client.reported_key == "tracker-detect:p1"
```

Extend `FakeClient.report_tracker_reconciliation` to record the key:

```python
    def report_tracker_reconciliation(self, *, observed_states, idempotency_key):
        self.reported = observed_states
        self.reported_key = idempotency_key
        return {}
```

and initialise `self.reported_key = None` in `__init__`.

- [ ] **Step 2: Run and confirm both fail**

Run: `.venv/bin/pytest tests/tracker_projection_adapter/test_cli.py -v`
Expected: the isolation test FAILS with `RuntimeError`; the key test FAILS on `"tracker-detect-pass" != "tracker-detect:p1"`.

- [ ] **Step 3: Fix `reconcile`**

```python
def reconcile(
    client: OrchestratorReader,
    projector: TrackerProjector,
    *,
    dry_run: bool,
    pass_id: str,
) -> dict[str, int]:
    """Report each Todoist-bound item's observed completion. The orchestrator owns the divergence
    rule; this only observes and reports (dumb adapter). Reading Todoist is non-mutating, so a dry
    run still reads -- it only withholds the orchestrator report.

    Per-item fail-open with a counted skip. The report is ONE post at the end, so letting a single
    unreadable item propagate would discard every item already read, not just the failing one --
    a pass that dies on item three reports nothing about items one and two.
    """
    observed_states = []
    skipped = 0
    for row in client.tracker_bindings():
        try:
            binding = binding_view(row)
            if binding.tracker_system != "todoist":
                continue
            completed = projector.item_completed(
                ItemRef(binding.external_item_id, binding.external_url)
            )
        except (RuntimeError, KeyError, TypeError, ValueError):
            skipped += 1
            continue
        observed_states.append(
            {
                "tracker_system": binding.tracker_system,
                "external_item_id": binding.external_item_id,
                "observed_completed": completed,
            }
        )
    if not dry_run and observed_states:
        # Per pass, not a constant. Every pass previously shared the key "tracker-detect-pass";
        # compare the reconciliation runner's f"reconcile-detect:{pass_id}".
        client.report_tracker_reconciliation(
            observed_states=observed_states, idempotency_key=f"tracker-detect:{pass_id}"
        )
    return {"reported": len(observed_states), "skipped": skipped}
```

- [ ] **Step 4: Thread `pass_id` through the command**

In `reconcile_command`, add the option and pass it:

```python
    pass_id: Annotated[str, typer.Option(help="Unique id for this pass.")] = "",
```

and inside the body, before constructing the client:

```python
    resolved_pass_id = pass_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
```

then `counts = reconcile(client, projector, dry_run=dry_run, pass_id=resolved_pass_id)`.

Add `from datetime import UTC, datetime` to the imports.

- [ ] **Step 5: Run the adapter suite**

Run: `.venv/bin/pytest tests/tracker_projection_adapter/ -v`
Expected: PASS. Existing tests asserting `counts == {"reported": 1}` now need `{"reported": 1, "skipped": 0}` — update those assertions; they are the same behaviour with a new counter.

- [ ] **Step 6: Verify differentially**

Revert the `try`/`except` (keep the test), re-run: the isolation test must RED. Restore it. Then revert the key to the constant: the key test must RED. Restore it. **A fix whose test still passes when the fix is removed is not verified.**

- [ ] **Step 7: Update the launcher**

`scripts/run-tracker-reconciliation.sh` — pass a per-run id so the key is unique in production too:

```bash
exec "$REPO_ROOT/.venv/bin/tracker-projection-adapter" reconcile \
  --todoist-project-id "${TODOIST_PROJECT_ID:?set TODOIST_PROJECT_ID to the target Todoist project id}" \
  --pass-id "$(date -u +%Y%m%dT%H%M%SZ)" \
  "$@"
```

- [ ] **Step 8: Format and commit**

```bash
.venv/bin/ruff format src/tracker_projection_adapter tests/tracker_projection_adapter
.venv/bin/ruff check src/ tests/
shellcheck --severity=warning scripts/run-tracker-reconciliation.sh
git add -A
git commit -m "fix(wsp27): per-item fail-open and a per-pass key in tracker reconcile

Two live defects. The loop had no try/except and TodoistProjector._get
raises on any non-404 >= 400, so one bad item aborted the pass -- and
because the report is a single POST at the end, everything already read
was discarded too. Now skipped and counted, the ADR-0002 discipline.

The idempotency key was the constant 'tracker-detect-pass', shared by
every pass ever run; now per-pass, like the reconciliation runner's.

Both verified differentially: reverting either fix reds its test.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ac8GrBCxZqisLaXywQFG12"
```

---

## Increment 4 — Wiring, so this is not the fourth unwired pass

### Task 10: The daily invoker (`AlobarQuest/infraops-mcp-server`)

**Files (separate repo, separate PR):**
- Modify: `src/cli/orchestrator-cli.ts`
- Modify: `scripts/drift-audit.sh`
- Modify: `dist/` (committed build output)
- Test: `tests/orchestrator-cli.test.ts`

**Interfaces:**
- Consumes: `POST /api/v1/follow-ups/mint` (Task 6).
- Produces: a daily invocation of the minting pass.

> **Why this task exists.** As of 2026-07-28, `run-tracker-projection.sh`, `run-tracker-reconciliation.sh` and the reconciliation runner all exist and **nothing schedules any of them** — zero launchd plists reference them. The one external pass producing real production data is WS-P3.0's, solely because it hooked into `com.devon.infra-drift.plist`, which already runs at 03:00 and already holds an orchestrator credential. Shipping without this makes WS-P2.8 the fourth unwired pass.
>
> **Credential:** `orchestrator-system` (BWS `221a48d5-3f29-4898-b300-b4820140c880`), **not** `orchestrator-drift-reporter`. Two credentials in one script, deliberately — see spec §9.1.
>
> **`dist/` is committed and there is no runtime build.** `drift-audit.sh` runs `node "$REPO/dist/cli/orchestrator-cli.js"` directly from the checkout. **Any `src/` change is inert until `npm run build` runs and `dist/` is committed.**

- [ ] **Step 1: Write the failing test**

Add to `tests/orchestrator-cli.test.ts`, matching the file's existing style:

```typescript
describe('mint-follow-ups', () => {
  it('posts the command envelope and prints a counted summary', async () => {
    const calls: Array<{ path: string; body: unknown }> = [];
    // ... stub fetch/OrchestratorClient the way the observe tests already do ...
    await doMintFollowUps(false);
    expect(calls).toHaveLength(1);
    expect(calls[0].path).toContain('/api/v1/follow-ups/mint');
  });

  it('is fail-open: a rejected mint prints a counted WARN and does not throw', async () => {
    // ... stub a 500 ...
    await expect(doMintFollowUps(false)).resolves.toBeUndefined();
  });

  it('dry run needs no token and posts nothing', async () => {
    await doMintFollowUps(true);
  });
});
```

Read the existing `observe` tests first and mirror their stubbing exactly; the assertions above are the contract, the mechanics should match the file.

- [ ] **Step 2: Add the client method and the command**

In `src/orchestrator/api-client.ts`:

```typescript
  mintFollowUps(idempotencyKey: string): Promise<FollowUpMintResponse> {
    return this.req<FollowUpMintResponse>('/api/v1/follow-ups/mint', {
      method: 'POST',
      body: JSON.stringify({ idempotency_key: idempotencyKey, expected_version: 0 }),
    });
  }
```

with the response interface:

```typescript
export interface FollowUpMintResponse {
  minted: Array<{ work_unit_id: string; work_package_revision_id: string; due_at: string }>;
  skipped: Array<{ work_package_revision_id: string; reason: string }>;
  considered: number;
}
```

In `src/cli/orchestrator-cli.ts`, add a second command. It needs its **own** client, because it authenticates as a different actor:

```typescript
/**
 * The follow-up minting pass (WS-P2.8). Uses ORCHESTRATOR_MINT_TOKEN / orchestrator-system --
 * NOT the drift-reporter credential this file's `observe` command uses. That actor's registry
 * profile is observe-and-propose; minting a work unit is canonical mutation, and agent_id
 * attribution is permanent.
 */
export function makeMintClient(): OrchestratorClient {
  const base = process.env.ORCHESTRATOR_API_BASE ?? '';
  const token = process.env.ORCHESTRATOR_MINT_TOKEN ?? '';
  const keyId = process.env.ORCHESTRATOR_MINT_CREDENTIAL_KEY_ID ?? 'orchestrator-system';
  if (!base) throw new Error('ORCHESTRATOR_API_BASE must be set');
  if (!token) throw new Error('ORCHESTRATOR_MINT_TOKEN must be set');
  return new OrchestratorClient(base, token, keyId);
}

export async function doMintFollowUps(dryRun: boolean): Promise<void> {
  if (dryRun) {
    process.stdout.write('follow-ups: would run one minting pass (dry run)\n');
    return;
  }
  let client: OrchestratorClient;
  try {
    client = makeMintClient();
  } catch (e) {
    process.stdout.write(
      `WARN: follow-up mint client unavailable: ${e instanceof Error ? e.message : String(e)}\n`,
    );
    process.exitCode = 1;
    return;
  }
  try {
    const result = await client.mintFollowUps(`follow-up-mint:${new Date().toISOString()}`);
    process.stdout.write(
      `follow-ups: minted=${result.minted.length} skipped=${result.skipped.length} ` +
        `considered=${result.considered}\n`,
    );
  } catch (e) {
    process.stdout.write(
      `WARN: follow-up mint failed: ${e instanceof Error ? e.message : String(e)}\n`,
    );
    process.exitCode = 1;
  }
}
```

and route it in `main()`:

```typescript
  } else if (args.command === 'mint-follow-ups') {
    await doMintFollowUps(args['dry-run'] === true);
  } else {
```

updating the unknown-command message to `(use observe or mint-follow-ups)`.

- [ ] **Step 3: Run the tests and build**

```bash
npm test
npm run build
```

Expected: tests PASS; `dist/cli/orchestrator-cli.js` regenerated.

- [ ] **Step 4: Wire it into the daily run**

In `scripts/drift-audit.sh`, immediately after the WS-P3.0 observation block:

```bash
# ── Best-effort: run one follow-up minting pass (non-fatal) ────────────────────
# WS-P2.8. Mints the work units whose package-declared follow-up reviews have come due. Uses the
# orchestrator-system credential, NOT the drift-reporter one above: that identity is
# observe-and-propose, and minting is canonical mutation attributed to an agent_id forever.
# Deliberately outside RC/RC_REMEDIATE, like the observation step: the drift loop is never
# hostage to the orchestrator being reachable, but a failure is always logged, never silent.
export ORCHESTRATOR_MINT_TOKEN="$(get_secret_by_id "${BWS_ORCHESTRATOR_SYSTEM_SECRET_ID:-221a48d5-3f29-4898-b300-b4820140c880}")"
export ORCHESTRATOR_MINT_CREDENTIAL_KEY_ID="${ORCHESTRATOR_MINT_CREDENTIAL_KEY_ID:-orchestrator-system}"
node "$REPO/dist/cli/orchestrator-cli.js" mint-follow-ups >>"$LOG_FILE" 2>&1 \
  && log "follow-up mint ok" || log "WARN: follow-up mint failed (non-fatal)"
```

- [ ] **Step 5: Register the secret and check the script**

```bash
grep -c '221a48d5-3f29-4898-b300-b4820140c880' .bws-secrets.toml
shellcheck --severity=warning scripts/drift-audit.sh
make check
```

If the grep returns 0, add a `[[secret]]` entry for it (name `ORCHESTRATOR_SYSTEM_TOKEN`, project `Ops / Platform`) — the manifest is generated from runtime code references and this is a new one. `make check` there runs eslint, `tsc --noEmit`, prettier and shellcheck at warning severity.

- [ ] **Step 6: Dry-run against production, read-only**

```bash
node dist/cli/orchestrator-cli.js mint-follow-ups --dry-run
```

Expected: the dry-run line, no network call, exit 0.

- [ ] **Step 7: Commit (including `dist/`)**

```bash
git add -A
git commit -m "feat(wsp28): run a daily follow-up minting pass from the drift audit

Wires the orchestrator's WS-P2.8 minting route into the existing 03:00
launchd run, so the mechanism is live on day one rather than waiting for
someone to remember a launcher. Non-fatal and counted, exactly like the
WS-P3.0 observation step beside it.

Uses orchestrator-system, not orchestrator-drift-reporter: that identity
is observe-and-propose and agent_id attribution is permanent, so
borrowing it would stamp canonical mutation onto it forever.

dist/ rebuilt and committed -- drift-audit.sh runs dist/ directly, so a
src/ change alone is inert.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ac8GrBCxZqisLaXywQFG12"
```

---

## The final gate (after Task 10)

- [ ] **Step 1: Confirm the tree is clean**

```bash
git status --short
```

Expected: empty. A full-gate green with uncommitted edits is a false green — it does not predict CI.

- [ ] **Step 2: Run the full gate and READ THE COUNT**

```bash
export SECURITY_STANDARDS_DIR="$PWD/tests/fixtures/security-standards"
make check 2>&1 | tee /tmp/wsp28-make-check.log
grep -E 'collected [0-9]+ items' /tmp/wsp28-make-check.log
```

Expected: a real collected count (the suite was ~1210 before this branch), all green. **Exit 0 alone proves nothing** — the Makefile swallows pytest exit code 5.

If `ruff format --check .` fails on files this branch never touched, that is pre-existing whole-repo format debt, not your regression. Confirm with `git stash && ruff format --check . ; git stash pop` before spending time on it.

- [ ] **Step 3: `/code-review` the whole branch**

Review the full diff against `main` for correctness bugs and simplification opportunities, not just style.

- [ ] **Step 4: Final adversarial whole-branch review**

Budget for kills. The highest-value things to attack, in order:

1. **The replay exemption (Task 2).** Does a pre-WS-P2.8 intake event genuinely still replay? Construct one whose stored payload lacks the key and prove it.
2. **The asymmetry (Task 5).** Can a human adjudicate a release-observation AC through any path? Can the verifier adjudicate the follow-up one? Both should be answerable from the tests.
3. **Idempotency under a fresh key.** Two passes, two different keys, one unit — proven against the database, not the return value.
4. **The `FAILED` reason code.** Does a lingering `FAILED` unit report `unsettled_failed_unit` and not `units_in_flight`?
5. **The envelope.** Is `authority_fingerprint(normalize_authority(stored)) == stored_fingerprint`? Is the capability set disjoint from `RUNNER_CAPABILITIES`?
6. **The token trap.** Does `follow_ups.py` contain any forbidden token in any string?
7. **Wiring.** Would the daily run actually invoke this, with the right credential, if it ran tonight?

- [ ] **Step 5: Push and open the PRs**

Two PRs: the orchestrator branch, and the `infraops-mcp-server` branch. Devon merges both. **Do not deploy** — that is session 2 (spec §16.2).

---

## Plan Self-Review

Checked against the spec section by section.

| Spec section | Task |
|---|---|
| §4 data model + intake | 1, 2 |
| §5 due predicate, §5.1 `FAILED`, §5.2 self-exclusion, §5.3 config | 3 |
| §6 minted unit, §6.1 capability, §6.2 events | 3, 4 |
| §7 generated criteria, §7.1 asymmetry | 5 |
| §8 invoker, §8.1 CLI + launcher | 6, 7 |
| §9 wiring, §9.1 credential | 7, 10 |
| §10 folded-in fixes | 9 |
| §11 guard story | 3, 4, 9 (each bound has a named test) |
| §12 architecture guards | 1, 4, 6 + the final gate |
| §13 testing | every task |
| §14 limitations | 8 (runbook + ADR) |
| §15 boundary | 8 (ADR) |
| §16.1 session-1 done-when | the final gate |

**Corrections made during writing, from the extraction pass rather than from the spec:**

1. **`_command_identity` / `_legacy_executable_identity_matches`** — absent from the spec entirely. Adding `follow_up` to the intake command puts it in the replay identity, which would make **every existing production intake event** replay as `idempotency_conflict`. Now Task 2 Steps 5–8, with a regression test and an explicit "if this passes before you write Step 7, you skipped Step 7".
2. **`tests/idempotency/matrix.py`** — a sixth architecture guard the spec's §12 did not list. Every ingress POST needs a row naming a test that exists. Task 6 Steps 7–8, with a new mechanism constant, since content-addressing is not one of the three mechanisms already named there.
3. **`web.py` needs no change** — the spec implied one. `_adjudicatable_criteria` filters only `POST_DEPLOY_AC_IDS`, so the follow-up AC passes through and renders with `is_judgment=True`. Task 5 Step 9 pins that rather than editing anything.
4. **`register_revision`'s `candidate` dict** doubles as the conflict-detection key and feeds the `revision.registered` event identity, so `follow_up` becomes conflict-significant. Called out in Task 2 Step 3 as correct-and-intended.
5. **`ORCHESTRATOR_ONLY_CAPABILITIES` is a frozenset literal**, so adding a member is a one-line edit with a comment — Task 3 Step 4 gives the replacement verbatim rather than describing it.
6. **`dist/` is committed in `infraops-mcp-server` and there is no runtime build**, so a `src/` change alone is inert. Task 10 Steps 3 and 7.

**Type-consistency check:** `evaluate_due` is defined in Task 3 and consumed in Task 4 with the same signature; `RevisionFacts`/`UnitFacts`/`DueDecision` field names match across both. `FOLLOW_UP_CAPABILITY` (`follow_ups.py`) and `FOLLOW_UP_AC_ID` (`lifecycle.py`) are deliberately different constants in different modules — the capability marks the *unit*, the AC id marks the *criterion*. `mint_due_follow_ups`'s keyword-only `actor` and `due_after_days` match the route's call site in Task 6. `reconcile`'s new `pass_id` parameter is keyword-only in Task 9 and passed as such from `reconcile_command`.

**Known open item, deliberately not resolved here:** Task 6 Step 1's auth-header constants must be matched to `tests/api/conftest.py`'s actual fixture style, and Task 4 Step 1's `Event(...)` construction must be matched to the real model signature. Both are named in-place as "verify before running" rather than guessed, because guessing them would produce code that looks authoritative and does not run.
