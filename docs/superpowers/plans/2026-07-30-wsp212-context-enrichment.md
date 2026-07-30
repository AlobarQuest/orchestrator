# WS-P2.12 Context Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every dispatched work unit carries a deterministic, human-approved projection of governed brain material into the worker's prompt, resolved at authoring time.

**Architecture:** `factory decompose` (intent-packages) reads the brains' REST API, builds a fingerprinted enrichment document per change class, and attaches it to each proposed unit. The orchestrator stores it write-once on the work unit and serves it verbatim on the runner brief. factory-runner renders it into the coding prompt. The orchestrator gains no HTTP egress.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 + Alembic (orchestrator); httpx + BWS (intent-packages); pydantic v2 + typer (factory-runner).

**Spec:** `docs/superpowers/specs/2026-07-30-wsp212-context-enrichment-design.md`

## Global Constraints

- **Repo order is load-bearing: factory-runner merges FIRST.** `RunnerBrief` is `extra="forbid"`; an orchestrator that emits `enrichment` to an older runner breaks every brief parse at claim. The runner is installed fresh per run from its default branch, so merge-first is sufficient.
- **The orchestrator gains no HTTP client import.** `tests/architecture/test_wsp21_invariant_scan.py::test_only_the_allowlisted_files_can_speak_http` must stay green with `OUTBOUND_ALLOWLIST` unchanged.
- **No new routes.** Enrichment rides the existing decomposition-proposal POST and the existing runner-brief GET. The route-inventory sets in `tests/architecture/test_scope_guards.py` and the idempotency `COVERAGE_MATRIX` must need no edit; verify, don't assume.
- **New orchestrator modules must not contain the bare tokens `dispatch`, `deploy`, or `merges` in any prose or docstring** (`test_ws32_scope_guards.py`, `test_ws33_scope_guards.py`). `dispatches`/`deployment` do not match; the exact bare token does.
- **Alembic revision ids ≤ 32 characters.** Use `0021_wsp212_enrichment` (22). Current head is `0020_wsp28_follow_up`; re-confirm before writing.
- **Every rejection path raises `DomainError`.** Only `DomainError` and `APIAuthenticationError` have handlers in `main.py`; anything else is a bare 500.
- **`make check` in the orchestrator requires** a running Postgres on `127.0.0.1:5432`, `SECURITY_STANDARDS_DIR=tests/fixtures/security-standards`, and `alembic upgrade head`. **Read the collected-test count** — exit 5 (no tests collected) is deliberately swallowed by the Makefile, so exit 0 is not proof anything ran.
- **Never run two pytest suites against `orchestrator_test` concurrently** — the fixtures drop and recreate it.
- Enrichment **grants nothing**. It must never enter the authority envelope, the authority fingerprint, or `ContextSnapshot.context`.

### Verified live facts (2026-07-30) — use these, do not re-derive

| Fact | Value |
|---|---|
| Code Brain base URL | `https://code-brain.devonwatkins.com` |
| Infra Brain base URL | `https://infra-brain.devonwatkins.com` |
| Auth header | `x-brain-key: <key>` (or `?key=`) |
| Code Brain key | BWS `750f737f-4cb6-4876-9a98-b48200ea1c0b` (`CODE_BRAIN_CONTRIBUTOR_KEY`, project `brains`) |
| Infra Brain key | BWS `da8134b0-565f-45c8-8965-b48200ea1c40` (`INFRA_BRAIN_CONTRIBUTOR_KEY`, project `brains`) |
| Bootstrap identity that can read project `brains` | Keychain `Claude` / `BWS_ACCESS_TOKEN_VPS_BACKUP` (**not** the SDS-narrow account) |
| `GET /api/road/{slug}` (code) | `{"road": {...}, "rules": [...], "exemplars": [...], "lessons": [...]}` |
| `road` keys | `adr_ref, category, created_at, decided_approach, home, last_validated_at, name, owner_standard, slug, status, summary, updated_at, validation_note` |
| code `rule` keys | `applicability, authority, bad_example, category, check, conflict, created_at, good_example, id, reason, retired_at, road_slug, rule, severity, source, status` |
| `exemplar` keys | `applicability, authority, conflict, id, label, location, note, road_slug, status` |
| `GET /api/rules` (infra) | `{"rules": [...]}`, keys `applicability, authority, category, check, conflict, created_at, id, reason, retired_at, rule, severity, source_app, status` |
| `error-logging` road content | 9 rules, 2 exemplars, 0 lessons, `decided_approach` 309 chars, `status: paved` |
| Infra Brain rule counts | 42 approved total; **4** `authority: required`; **12** `severity: BLOCK` |
| Code Brain `authority: required` rules | **zero** |
| REST returns approved-only | Yes — `RuleRepository.list_all` defaults `include_proposed=False`; the route does not override |
| REST authority filtering | Client-side only — the route exposes `category/severity/road_slug/include_retired`, **not** `min_authority` |

---

## File Structure

**factory-runner** (`AlobarQuest/factory-runner`)
- Modify `src/factory_runner/models.py` — `RunnerBrief.enrichment`
- Modify `src/factory_runner/cli.py` — `_prompt()` renders the enrichment section
- Create `tests/fixtures/runner_brief.json` — the shared cross-repo fixture
- Create `tests/test_runner_brief_contract.py` — hash pin + model validation

**intent-packages** (`AlobarQuest/intent-packages`)
- Modify `src/intent_packages/profiles/base.py` — `EnrichmentSpec`, `DeliveryProfile.enrichment`
- Modify `src/intent_packages/profiles/{software_delivery,dependency_update,maintenance_remediation}.py` — spec values
- Create `src/intent_packages/factory/brains.py` — read client + resolver + fingerprint
- Modify `src/intent_packages/factory/factory_cli.py` (or the module that builds proposed units) — wiring
- Modify `.bws-secrets.toml` — the two brain UUIDs
- Create `tests/factory/test_brains.py`, `tests/factory/fixtures/{code_road_error_logging,infra_rules}.json`

**orchestrator** (`AlobarQuest/orchestrator`)
- Create `migrations/versions/0021_wsp212_enrichment.py`
- Modify `src/orchestrator/persistence/models.py` — two `context_enrichment` columns
- Create `src/orchestrator/kernel/enrichment.py` — shape validation + fingerprint recomputation (pure, no I/O)
- Modify `src/orchestrator/services/decomposition.py` — `ProposedUnit.context_enrichment`, validation, persistence
- Modify `src/orchestrator/services/packages.py` — `register_approved_unit` accepts and assigns it
- Modify `src/orchestrator/api/schemas.py` — proposal request/response fields
- Modify `src/orchestrator/services/runner_brief.py` — the `enrichment` key
- Create `tests/architecture/test_enrichment_write_once.py`
- Create `tests/contract/test_runner_brief_contract.py`, `tests/fixtures/runner_brief.json`
- Modify `tests/services/test_decomposition.py`, `tests/api/test_runner_brief_api.py`

---

## Task 1: factory-runner accepts and renders enrichment

**Files:**
- Modify: `src/factory_runner/models.py:74-83`
- Modify: `src/factory_runner/cli.py:142-176`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `RunnerBrief.enrichment: dict[str, Any] | None` (default `None`); `_prompt(brief, allowed_commands, *, title=...)` unchanged in signature.

- [ ] **Step 1: Write the failing tests**

In `tests/test_cli.py`, after the existing `_runner_brief()` helper:

```python
def _enrichment() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "profile": "software-delivery",
        "change_class": "software-delivery",
        "roads": [
            {
                "brain": "code",
                "slug": "error-logging",
                "name": "Error handling & structured logging",
                "category": "application",
                "status": "paved",
                "summary": "How we handle errors and emit structured logs.",
                "decided_approach": "structlog + asgi-correlation-id (Python) / pino (TS).",
            }
        ],
        "rules": [
            {
                "brain": "code",
                "id": 5,
                "road_slug": "error-logging",
                "category": "security",
                "severity": "BLOCK",
                "authority": "informational",
                "rule": "Never log secrets, credentials, auth headers, tokens, or full request bodies.",
                "reason": "Logs are aggregated and retained; a secret in a log is a leaked secret.",
            }
        ],
        "exemplars": [],
        "content_fingerprint": "sha256:0000",
        "resolved_at": "2026-07-30T00:00:00+00:00",
        "sources": [{"brain": "code", "endpoint": "/api/road/error-logging", "query": "slug"}],
    }


def test_runner_brief_accepts_enrichment() -> None:
    payload = _runner_brief().model_dump()
    payload["enrichment"] = _enrichment()

    brief = RunnerBrief.model_validate(payload)

    assert brief.enrichment is not None
    assert brief.enrichment["rules"][0]["id"] == 5


def test_runner_brief_enrichment_defaults_to_none() -> None:
    assert _runner_brief().enrichment is None


def test_prompt_renders_enrichment_as_governed_material() -> None:
    payload = _runner_brief().model_dump()
    payload["enrichment"] = _enrichment()
    brief = RunnerBrief.model_validate(payload)

    prompt = _prompt(brief, ("make check",))

    assert "Governed standards for this change class" in prompt
    assert "error-logging" in prompt
    assert "Never log secrets" in prompt
    assert "BLOCK" in prompt
    # Governed material is explicitly NOT the hostile data the warning covers.
    assert "These records are governed" in prompt


def test_prompt_omits_the_enrichment_section_when_absent() -> None:
    prompt = _prompt(_runner_brief(), ("make check",))

    assert "Governed standards for this change class" not in prompt
```

Add `from factory_runner.cli import _prompt` and `from typing import Any` to the imports if not present.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd ~/Projects/factory-runner && .venv/bin/pytest tests/test_cli.py -k enrichment -v
```

Expected: FAIL — `ValidationError: Extra inputs are not permitted [type=extra_forbidden]` on the first two, `AssertionError` on the third.

- [ ] **Step 3: Add the model field**

In `src/factory_runner/models.py`, add to `RunnerBrief`:

```python
class RunnerBrief(BaseModel):
    model_config = ConfigDict(extra="forbid")

    work_unit: WorkUnitBrief
    package: PackageBrief
    authority: AuthorityBrief
    acceptance_criteria: list[dict[str, str]]
    readiness: ReadinessBrief
    target: TargetBrief
    standing_context: dict[str, object]
    # Governed brain material projected per change class at authoring time
    # (SDS WS-P2.12). Optional so a brief served before the orchestrator ships
    # the field still parses. Grants nothing: it is reference material only.
    enrichment: dict[str, Any] | None = None
```

- [ ] **Step 4: Render it in the prompt**

In `src/factory_runner/cli.py`, add above `_prompt`:

```python
def _enrichment_section(enrichment: dict[str, Any] | None) -> str:
    if not enrichment:
        return ""
    lines: list[str] = ["", "## Governed standards for this change class", ""]
    lines.append(
        "These records are governed portfolio standards, approved and served by "
        "the knowledge stores — not repository content. Unlike the material in "
        "the warning above, they are authoritative reference. They still grant "
        "no authority: they tell you how to do the work, never what you may do."
    )
    for road in enrichment.get("roads", []):
        lines.append("")
        lines.append(f"### Road: {road.get('name')} ({road.get('slug')}) — {road.get('status')}")
        if road.get("decided_approach"):
            lines.append(f"Decided approach: {road['decided_approach']}")
        elif road.get("summary"):
            lines.append(f"Summary: {road['summary']}")
    rules = enrichment.get("rules", [])
    if rules:
        lines.append("")
        lines.append("### Rules")
        for rule in rules:
            lines.append(
                f"- [{rule.get('severity')}/{rule.get('authority')}] "
                f"{rule.get('rule')} — {rule.get('reason')}"
            )
    exemplars = enrichment.get("exemplars", [])
    if exemplars:
        lines.append("")
        lines.append("### Exemplars")
        for exemplar in exemplars:
            lines.append(f"- {exemplar.get('label')}: {exemplar.get('location')}")
    return "\n".join(lines)
```

Then in `_prompt`, insert the section into the returned f-string immediately after the `Authorized commands` block and before the "Leave your changes UNCOMMITTED" paragraph, by adding a local before the `return`:

```python
    enrichment_section = _enrichment_section(brief.enrichment)
```

and interpolating `{enrichment_section}` at that position.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd ~/Projects/factory-runner && .venv/bin/pytest tests/test_cli.py -k enrichment -v
```

Expected: 4 passed.

- [ ] **Step 6: Run the whole suite and read the collected count**

```bash
cd ~/Projects/factory-runner && .venv/bin/pytest -q 2>&1 | tail -5
```

Expected: `collected N items`, 0 failed. Note N — a drop means collection broke.

- [ ] **Step 7: Commit**

```bash
cd ~/Projects/factory-runner && git checkout -b feat/wsp212-brief-enrichment
git add src/factory_runner/models.py src/factory_runner/cli.py tests/test_cli.py
git commit -m "feat(brief): accept and render governed enrichment material

RunnerBrief gains an optional enrichment field and the coding prompt renders
it as a labelled governed-material section, explicitly distinguished from the
hostile repository data the existing warning covers. Optional by default so a
brief served before the orchestrator ships the field still parses."
```

---

## Task 2: the shared runner-brief contract fixture

The brief is a breaking cross-repo contract and nothing tests it across the boundary. Only the *envelope* has a shared fixture and a pinned hash. This closes the same gap for the brief.

**Files:**
- Create: `factory-runner/tests/fixtures/runner_brief.json`
- Create: `factory-runner/tests/test_runner_brief_contract.py`

**Interfaces:**
- Consumes: `RunnerBrief` from Task 1.
- Produces: `tests/fixtures/runner_brief.json` — the byte-identical fixture the orchestrator copies in Task 7; `CONTRACT_SHA256` computed over `json.dumps(fixture, sort_keys=True, separators=(",", ":"))`.

- [ ] **Step 1: Write the fixture**

Create `tests/fixtures/runner_brief.json` with the full served shape (this is what the orchestrator's `runner_brief()` returns, with an enrichment document attached):

```json
{
  "work_unit": {"id": "00000000-0000-0000-0000-000000000001", "state": "ready", "version": 1, "title": "Adopt the structured-logging road", "outcome": "Logging follows the paved road.", "required_capability": "repo.edit", "max_attempts": 3},
  "package": {"id": "pkg-enrichment", "revision_id": "00000000-0000-0000-0000-000000000002", "revision": 1, "content_hash": "sha256:abc", "source_repository": "AlobarQuest/orchestrator", "source_path": "package.yaml", "source_commit": "abc123"},
  "authority": {"fingerprint": "0f7ef81ecfab22d2a7b8258e94a670f414067d7298f5a5e71b66ade70d7b6f31", "envelope": {"capabilities": {"repo.read": "allowed", "repo.edit": "allowed", "command.run": "allowed", "github.pr.create": "allowed", "orchestrator.claim": "allowed", "orchestrator.evidence.write": "allowed"}, "constraints": {"work_unit_id": "00000000-0000-0000-0000-000000000001", "target_repository": "AlobarQuest/orchestrator", "allowed_commands": ["make check"], "mutation_commands": ["make check"]}}},
  "acceptance_criteria": [],
  "readiness": {"status": "ready", "reasons": []},
  "target": {"repository": "AlobarQuest/orchestrator"},
  "standing_context": {},
  "enrichment": {"schema_version": 1, "profile": "software-delivery", "change_class": "software-delivery", "roads": [{"brain": "code", "slug": "error-logging", "name": "Error handling & structured logging", "category": "application", "status": "paved", "summary": "How we handle errors and emit structured logs.", "decided_approach": "structlog + asgi-correlation-id (Python) / pino (TS).", "home": "ADR-0006 + Code Brain (this road)", "owner_standard": "code-standards", "adr_ref": "code-standards/docs/decisions/0006-error-logging.md"}], "rules": [{"brain": "code", "id": 5, "road_slug": "error-logging", "category": "security", "severity": "BLOCK", "authority": "informational", "rule": "Never log secrets, credentials, auth headers, tokens, or full request bodies. Redact or omit.", "reason": "Logs are aggregated and retained; a secret in a log is a leaked secret."}], "exemplars": [], "content_fingerprint": "sha256:0000000000000000000000000000000000000000000000000000000000000000", "resolved_at": "2026-07-30T00:00:00+00:00", "sources": [{"brain": "code", "endpoint": "/api/road/error-logging", "query": "error-logging"}]}
}
```

- [ ] **Step 2: Write the contract test**

Create `tests/test_runner_brief_contract.py`:

```python
"""The runner-brief cross-repo contract.

`RunnerBrief` is `extra="forbid"`: a field the orchestrator adds and this repo
does not know about raises at parse time, killing every run at claim. Nothing
tested that across the boundary -- only the authority envelope had a shared
fixture. `tests/fixtures/runner_brief.json` is byte-identical to the
orchestrator's copy under the same name, and CONTRACT_SHA256 makes a one-sided
edit loud.
"""

import hashlib
import json
from pathlib import Path

from factory_runner.cli import _prompt
from factory_runner.models import RunnerBrief

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "runner_brief.json"
CONTRACT_SHA256 = "PLACEHOLDER_COMPUTE_IN_STEP_3"


def golden_brief() -> dict:
    return json.loads(FIXTURE.read_text())


def test_golden_brief_is_unchanged() -> None:
    canonical = json.dumps(golden_brief(), sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(canonical.encode()).hexdigest() == CONTRACT_SHA256


def test_the_runner_parses_the_golden_brief() -> None:
    """The assertion that never existed: the served brief, validated by the consumer."""
    brief = RunnerBrief.model_validate(golden_brief())

    assert brief.work_unit.id == "00000000-0000-0000-0000-000000000001"
    assert brief.enrichment is not None
    assert brief.enrichment["rules"][0]["severity"] == "BLOCK"


def test_the_golden_brief_reaches_the_worker_prompt() -> None:
    """A hash pin proves the file is unchanged; it says nothing about use.

    This asserts the derivation instead: enrichment content in the fixture
    appears in the text the coding model actually reads.
    """
    brief = RunnerBrief.model_validate(golden_brief())

    prompt = _prompt(brief, ("make check",))

    assert "Never log secrets" in prompt
    assert "error-logging" in prompt
```

- [ ] **Step 3: Compute the hash and fill it in**

```bash
cd ~/Projects/factory-runner && python3 -c "
import hashlib, json, pathlib
d = json.loads(pathlib.Path('tests/fixtures/runner_brief.json').read_text())
print(hashlib.sha256(json.dumps(d, sort_keys=True, separators=(',',':')).encode()).hexdigest())
"
```

Replace `PLACEHOLDER_COMPUTE_IN_STEP_3` with the printed value.

- [ ] **Step 4: Run the contract test**

```bash
cd ~/Projects/factory-runner && .venv/bin/pytest tests/test_runner_brief_contract.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Prove the guard FIRES**

Temporarily add `"scratch": 1` as a top-level key to `tests/fixtures/runner_brief.json` and re-run:

```bash
cd ~/Projects/factory-runner && .venv/bin/pytest tests/test_runner_brief_contract.py -v
```

Expected: `test_golden_brief_is_unchanged` FAILS (hash mismatch) **and** `test_the_runner_parses_the_golden_brief` FAILS (`extra_forbidden`). Both must fail — the first catches drift, the second catches the actual breakage. **Revert the scratch key.** Record both failure messages in the commit body; a guard not proven to fire is not a guard.

- [ ] **Step 6: Commit**

```bash
cd ~/Projects/factory-runner && git add tests/fixtures/runner_brief.json tests/test_runner_brief_contract.py
git commit -m "test(contract): pin the runner-brief shape across both repos

The brief is extra=forbid, so an orchestrator-side field this repo does not
know about kills every run at claim -- and nothing tested it across the
boundary. Mirrors the authority-envelope contract: byte-identical fixture,
pinned hash, plus a derivation assertion that the fixture's enrichment
actually reaches the worker prompt. Proven to fire by adding a stray key:
hash mismatch AND extra_forbidden, both red."
```

- [ ] **Step 7: Open the PR — this must merge before Task 6 ships**

```bash
cd ~/Projects/factory-runner && git push -u origin feat/wsp212-brief-enrichment
gh pr create --title "WS-P2.12: accept and render governed enrichment in the runner brief" \
  --body "Adds the optional \`enrichment\` field to RunnerBrief, renders it as a governed-material section in the coding prompt, and pins the brief shape with a shared cross-repo fixture.

**Merge order matters:** this must land before the orchestrator starts emitting the field. RunnerBrief is \`extra=\"forbid\"\`, so the reverse order breaks every brief parse at claim.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01DQAhHi7JBpX2NNFKUdmijY"
```

**STOP. This PR is Devon's merge gate.** Report it and continue with Tasks 3–5 (intent-packages) while it waits; do not start Task 6 until it is merged.

---

## Task 3: EnrichmentSpec on DeliveryProfile

**Files:**
- Modify: `src/intent_packages/profiles/base.py`
- Modify: `src/intent_packages/profiles/software_delivery.py:71-78`
- Modify: `src/intent_packages/profiles/dependency_update.py` (its `DELIVERY_PROFILE`)
- Modify: `src/intent_packages/profiles/maintenance_remediation.py` (its `DELIVERY_PROFILE`)
- Modify: `src/intent_packages/profiles/__init__.py:19-27` (export)
- Test: `tests/test_profiles_registry.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `EnrichmentSpec(code_road_slugs: tuple[str, ...], infra_min_authority: str)`; `DeliveryProfile.enrichment: EnrichmentSpec | None`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_profiles_registry.py`:

```python
from intent_packages.profiles import PROFILES, EnrichmentSpec


def test_every_factory_executable_profile_declares_enrichment() -> None:
    """A profile the factory can execute must say what its workers are told.

    An absent spec is indistinguishable from 'we forgot', which is the dead-config
    shape this repo has paid for before. Empty content is fine; absent is not.
    """
    for profile in PROFILES.values():
        if profile.change_class is None:
            continue
        assert profile.enrichment is not None, f"{profile.name} has no EnrichmentSpec"
        assert isinstance(profile.enrichment, EnrichmentSpec)


def test_software_delivery_pulls_the_error_logging_road() -> None:
    spec = PROFILES["software-delivery"].enrichment
    assert spec is not None
    assert spec.code_road_slugs == ("error-logging",)
    assert spec.infra_min_authority == "required"


def test_dependency_update_is_enriched_but_empty_of_code_roads() -> None:
    """Empty by CONTENT, not absent. The brains hold nothing for this class yet."""
    spec = PROFILES["dependency-update"].enrichment
    assert spec is not None
    assert spec.code_road_slugs == ()
    assert spec.infra_min_authority == "required"
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd ~/Projects/intent-packages && .venv/bin/pytest tests/test_profiles_registry.py -k enrichment -v
```

Expected: FAIL — `ImportError: cannot import name 'EnrichmentSpec'`.

- [ ] **Step 3: Add the dataclass and the field**

In `src/intent_packages/profiles/base.py`, after `AuthorityDefaults`:

```python
@dataclass(frozen=True)
class EnrichmentSpec:
    """What governed brain material a change class projects into its workers' briefs.

    The single definition site. The orchestrator holds no copy of this
    vocabulary -- it receives a resolved document and validates its shape,
    never its membership.

    `infra_min_authority` is an AUTHORITY floor, not a severity floor. The two
    disagree: Infra Brain carries 12 BLOCK-severity rules of which only 4 are
    `authority: required`.
    """

    code_road_slugs: tuple[str, ...] = ()
    infra_min_authority: str = "required"
```

Add to `DeliveryProfile`:

```python
    enrichment: EnrichmentSpec | None = None
```

- [ ] **Step 4: Set the values on the three factory-executable profiles**

`software_delivery.py` — add to `DELIVERY_PROFILE(...)`:

```python
    enrichment=EnrichmentSpec(code_road_slugs=("error-logging",), infra_min_authority="required"),
```

`dependency_update.py` and `maintenance_remediation.py` — add to each `DELIVERY_PROFILE(...)`:

```python
    enrichment=EnrichmentSpec(code_road_slugs=(), infra_min_authority="required"),
```

Import `EnrichmentSpec` from `intent_packages.profiles.base` in each of the three modules, and add `"EnrichmentSpec"` to `__all__` plus the `from ... import` line in `profiles/__init__.py`.

Note: `software-delivery`'s `DELIVERY_PROFILE` currently has `change_class=None`. Leave it as it is — Task 8 addresses whether it needs a routing row; changing it here would break `test_profiles_dispatch.py`'s routing-row invariant in a task that is not about routing.

- [ ] **Step 5: Run the tests**

```bash
cd ~/Projects/intent-packages && .venv/bin/pytest tests/test_profiles_registry.py -v
```

Expected: all pass. `test_every_factory_executable_profile_declares_enrichment` skips `software-delivery` while its `change_class` is `None`, and covers `dependency-update` and `maintenance-remediation`.

- [ ] **Step 6: Commit**

```bash
cd ~/Projects/intent-packages && git checkout -b feat/wsp212-enrichment-resolver
git add src/intent_packages/profiles/ tests/test_profiles_registry.py
git commit -m "feat(profiles): EnrichmentSpec — the one definition site per change class

A profile names the code roads its class touches and an Infra Brain authority
floor. Authority is not severity: 12 infra rules are BLOCK, only 4 are
authority=required."
```

---

## Task 4: the brain read client and resolver

**Files:**
- Create: `src/intent_packages/factory/brains.py`
- Modify: `.bws-secrets.toml`
- Create: `tests/factory/fixtures/code_road_error_logging.json`
- Create: `tests/factory/fixtures/infra_rules.json`
- Test: `tests/factory/test_brains.py`

**Interfaces:**
- Consumes: `EnrichmentSpec` from Task 3.
- Produces:
  - `class BrainKey(enum.StrEnum): CODE = "code"; INFRA = "infra"`
  - `resolve_brain_key(brain: BrainKey, *, runner: Runner | None = None) -> str`
  - `class BrainClient: def get_road(self, slug: str) -> dict; def list_infra_rules(self) -> list[dict]`
  - `resolve_enrichment(spec: EnrichmentSpec, *, profile: str, change_class: str, client: BrainClient, now: datetime) -> dict[str, Any]`
  - `content_fingerprint(document: Mapping[str, Any]) -> str`

- [ ] **Step 1: Capture the fixtures from the live brains**

Fixtures come from live sources, never from hand-authoring. Run:

```bash
cd ~/Projects/orchestrator && python3 - <<'PY'
import subprocess, json, os, urllib.request, tomllib, pathlib

tok = subprocess.run(["security","find-generic-password","-s","Claude","-a",
                      "BWS_ACCESS_TOKEN_VPS_BACKUP","-w"],
                     capture_output=True, text=True).stdout.strip()
uuids = {s["name"]: s["uuid"]
         for s in tomllib.loads(pathlib.Path(".bws-secrets.toml").read_text())["secret"]}
env = {**os.environ, "BWS_ACCESS_TOKEN": tok}

def key(name):
    out = subprocess.run(["bws","secret","get",uuids[name],"--output","env"],
                         capture_output=True, text=True, env=env, timeout=30).stdout
    return out.split("=",1)[1].strip().strip('"') if "=" in out else out.strip()

def get(url, k):
    req = urllib.request.Request(url, headers={"x-brain-key": k,
                                               "User-Agent": "sds-wsp212/1"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)

dest = pathlib.Path.home() / "Projects/intent-packages/tests/factory/fixtures"
dest.mkdir(parents=True, exist_ok=True)
(dest / "code_road_error_logging.json").write_text(json.dumps(
    get("https://code-brain.devonwatkins.com/api/road/error-logging",
        key("CODE_BRAIN_CONTRIBUTOR_KEY")), indent=2, sort_keys=True) + "\n")
(dest / "infra_rules.json").write_text(json.dumps(
    get("https://infra-brain.devonwatkins.com/api/rules",
        key("INFRA_BRAIN_CONTRIBUTOR_KEY")), indent=2, sort_keys=True) + "\n")
print("fixtures written; no secret printed")
PY
```

Never print the key. The fixture files contain only public governed content — read them once to confirm no credential leaked in before committing.

- [ ] **Step 2: Write the failing tests**

Create `tests/factory/test_brains.py`:

```python
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from intent_packages.factory.brains import (
    BrainKey,
    content_fingerprint,
    resolve_enrichment,
)
from intent_packages.profiles import EnrichmentSpec

FIXTURES = Path(__file__).parent / "fixtures"


class FakeBrainClient:
    def __init__(self) -> None:
        self.road_calls: list[str] = []
        self.infra_calls = 0

    def get_road(self, slug: str) -> dict:
        self.road_calls.append(slug)
        return json.loads((FIXTURES / "code_road_error_logging.json").read_text())

    def list_infra_rules(self) -> list[dict]:
        self.infra_calls += 1
        return json.loads((FIXTURES / "infra_rules.json").read_text())["rules"]


def _resolve(spec: EnrichmentSpec, *, when: datetime) -> dict:
    return resolve_enrichment(
        spec,
        profile="software-delivery",
        change_class="software-delivery",
        client=FakeBrainClient(),
        now=when,
    )


def test_resolves_the_named_road_and_its_rules() -> None:
    doc = _resolve(
        EnrichmentSpec(code_road_slugs=("error-logging",)),
        when=datetime(2026, 7, 30, tzinfo=UTC),
    )

    assert doc["schema_version"] == 1
    assert [road["slug"] for road in doc["roads"]] == ["error-logging"]
    assert doc["roads"][0]["decided_approach"]
    assert len([r for r in doc["rules"] if r["brain"] == "code"]) == 9


def test_infra_rules_are_filtered_by_authority_not_severity() -> None:
    """The REST route has no min_authority filter; the client applies the floor.

    12 infra rules are BLOCK-severity and only 4 are authority=required. A
    resolver that filtered on severity would carry three times the material.
    """
    doc = _resolve(EnrichmentSpec(infra_min_authority="required"), when=datetime(2026, 7, 30, tzinfo=UTC))

    infra = [rule for rule in doc["rules"] if rule["brain"] == "infra"]
    assert len(infra) == 4
    assert {rule["authority"] for rule in infra} == {"required"}


def test_an_empty_spec_resolves_to_an_empty_but_present_document() -> None:
    """dependency-update today. Empty by content is not the same as absent."""
    doc = _resolve(EnrichmentSpec(code_road_slugs=(), infra_min_authority="nonexistent-rank"),
                   when=datetime(2026, 7, 30, tzinfo=UTC))

    assert doc["roads"] == []
    assert doc["rules"] == []
    assert doc["schema_version"] == 1
    assert doc["content_fingerprint"].startswith("sha256:")


def test_the_fingerprint_ignores_resolution_time() -> None:
    """Same brain state -> same fingerprint. Otherwise a clock defeats the audit."""
    spec = EnrichmentSpec(code_road_slugs=("error-logging",))

    first = _resolve(spec, when=datetime(2026, 7, 30, tzinfo=UTC))
    second = _resolve(spec, when=datetime(2027, 1, 1, tzinfo=UTC))

    assert first["resolved_at"] != second["resolved_at"]
    assert first["content_fingerprint"] == second["content_fingerprint"]


def test_the_document_is_byte_identical_across_runs() -> None:
    spec = EnrichmentSpec(code_road_slugs=("error-logging",))
    when = datetime(2026, 7, 30, tzinfo=UTC)

    first = json.dumps(_resolve(spec, when=when), sort_keys=True, separators=(",", ":"))
    second = json.dumps(_resolve(spec, when=when), sort_keys=True, separators=(",", ":"))

    assert first == second


def test_the_fingerprint_changes_when_content_changes() -> None:
    """A fingerprint that never moves is not attesting anything."""
    base = _resolve(EnrichmentSpec(code_road_slugs=("error-logging",)),
                    when=datetime(2026, 7, 30, tzinfo=UTC))
    mutated = {**base, "rules": base["rules"][:-1]}

    assert content_fingerprint(mutated) != base["content_fingerprint"]


def test_only_approved_records_are_carried() -> None:
    """Containment: the REST API returns approved-only, and the resolver keeps it that way."""
    doc = _resolve(EnrichmentSpec(code_road_slugs=("error-logging",)),
                   when=datetime(2026, 7, 30, tzinfo=UTC))

    fixture = json.loads((FIXTURES / "code_road_error_logging.json").read_text())
    assert all(rule["status"] == "approved" for rule in fixture["rules"])
    assert len(doc["rules"]) >= len(fixture["rules"])


@pytest.mark.parametrize("brain", list(BrainKey))
def test_every_brain_key_has_a_manifest_entry(brain: BrainKey) -> None:
    from intent_packages.factory.brains import brain_secret_uuid

    assert brain_secret_uuid(brain)
```

- [ ] **Step 3: Run to verify failure**

```bash
cd ~/Projects/intent-packages && .venv/bin/pytest tests/factory/test_brains.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'intent_packages.factory.brains'`.

- [ ] **Step 4: Add the BWS manifest entries**

Append to `.bws-secrets.toml`:

```toml
# Brain read credentials for WS-P2.12 authoring-time context enrichment.
# The brains' auth middleware accepts either the approver key or the
# lower-privilege contributor key for ALL non-allowlisted paths; there is no
# read-only key today, so the contributor key is the least privilege available.
[[secret]]
uuid = "750f737f-4cb6-4876-9a98-b48200ea1c0b"
name = "CODE_BRAIN_CONTRIBUTOR_KEY"
project = "brains"
brain = "code"

[[secret]]
uuid = "da8134b0-565f-45c8-8965-b48200ea1c40"
name = "INFRA_BRAIN_CONTRIBUTOR_KEY"
project = "brains"
brain = "infra"
```

- [ ] **Step 5: Write the module**

Create `src/intent_packages/factory/brains.py`:

```python
"""Authoring-time governed-knowledge resolution for `factory decompose` (WS-P2.12).

Reads the brains' REST lookup API -- the surface their own docstring describes as
"lets off-machine agents query Code Brain accurately without an MCP client" -- and
projects a change class's declared roads and rules into one canonical document.

The document is attached to each proposed unit, so what a worker will read is
inside the artifact a human approves, and the orchestrator never calls out.

Containment: the REST repositories default to approved-only records, so nothing
proposed, deprecated, superseded, or retired can ride the projection.
"""

from __future__ import annotations

import enum
import hashlib
import json
import tomllib
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from intent_packages.factory.credentials import CredentialError, Runner, _default_runner
from intent_packages.profiles import EnrichmentSpec

MANIFEST = Path(__file__).resolve().parents[3] / ".bws-secrets.toml"
SCHEMA_VERSION = 1
TIMEOUT_SECONDS = 15.0
USER_AGENT = "intent-packages-enrichment/1 (+AlobarQuest/intent-packages)"

# Ranked low to high. Mirrors the brains' own AUTHORITY_RANK; an unknown floor
# name matches nothing, which fails closed to an empty projection.
AUTHORITY_RANK: Mapping[str, int] = {"informational": 0, "recommended": 1, "required": 2}

ROAD_FIELDS = ("slug", "name", "category", "status", "summary",
               "decided_approach", "home", "owner_standard", "adr_ref")
RULE_FIELDS = ("id", "category", "severity", "authority", "rule", "reason")
EXEMPLAR_FIELDS = ("id", "road_slug", "label", "location", "note")


class BrainKey(enum.StrEnum):
    CODE = "code"
    INFRA = "infra"

    @property
    def base_url(self) -> str:
        return {
            BrainKey.CODE: "https://code-brain.devonwatkins.com",
            BrainKey.INFRA: "https://infra-brain.devonwatkins.com",
        }[self]


def brain_secret_uuid(brain: BrainKey) -> str:
    """The BWS UUID for `brain`'s access key, selected by the manifest's `brain` field.

    By UUID, never by name: BWS secret names are mutable labels.
    """
    try:
        manifest = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))
    except OSError as error:
        raise CredentialError(f"cannot read {MANIFEST}") from error
    for entry in manifest.get("secret", []):
        if entry.get("brain") == brain.value:
            uuid = entry.get("uuid")
            if isinstance(uuid, str) and uuid:
                return uuid
    raise CredentialError(f"{MANIFEST} has no [[secret]] entry with brain = {brain.value!r}")


def resolve_brain_key(brain: BrainKey, *, runner: Runner | None = None) -> str:
    """Return `brain`'s access key from the environment or BWS. Never logged."""
    import os

    env_var = f"{brain.name}_BRAIN_KEY"
    from_env = os.environ.get(env_var, "")
    if from_env:
        return from_env
    uuid = brain_secret_uuid(brain)
    if not os.environ.get("BWS_ACCESS_TOKEN"):
        raise CredentialError(
            f"no credential for the {brain.value} brain: set {env_var}, or set "
            f"BWS_ACCESS_TOKEN so it can be fetched from BWS secret {uuid}"
        )
    result = (runner or _default_runner)(["bws", "secret", "get", uuid, "--output", "env"])
    if result.returncode != 0:
        raise CredentialError(
            f"bws secret get failed for the {brain.value} brain (secret {uuid}), "
            f"exit {result.returncode}"
        )
    for line in result.stdout.splitlines():
        _, separator, value = line.partition("=")
        if separator and (value := value.strip().strip('"')):
            return value
    bare = result.stdout.strip()
    if bare and "=" not in result.stdout:
        return bare
    raise CredentialError(f"bws secret get returned no value for the {brain.value} brain")


class BrainClient:
    """Read-only HTTP access to the brains' lookup API."""

    def __init__(self, keys: Mapping[BrainKey, str], *, timeout: float = TIMEOUT_SECONDS) -> None:
        self._keys = keys
        self._timeout = timeout

    def _get(self, brain: BrainKey, path: str) -> Any:
        response = httpx.get(
            f"{brain.base_url}{path}",
            headers={"x-brain-key": self._keys[brain], "User-Agent": USER_AGENT},
            timeout=self._timeout,
        )
        response.raise_for_status()
        return response.json()

    def get_road(self, slug: str) -> dict[str, Any]:
        return self._get(BrainKey.CODE, f"/api/road/{slug}")

    def list_infra_rules(self) -> list[dict[str, Any]]:
        payload = self._get(BrainKey.INFRA, "/api/rules")
        rules = payload.get("rules", [])
        return list(rules) if isinstance(rules, list) else []


def _pick(record: Mapping[str, Any], fields: Sequence[str]) -> dict[str, Any]:
    return {field: record.get(field) for field in fields}


def _meets_authority(record: Mapping[str, Any], floor: str) -> bool:
    minimum = AUTHORITY_RANK.get(floor)
    if minimum is None:
        return False
    rank = AUTHORITY_RANK.get(str(record.get("authority", "")))
    return rank is not None and rank >= minimum


def content_fingerprint(document: Mapping[str, Any]) -> str:
    """sha256 over the document's CONTENT -- provenance deliberately excluded.

    `resolved_at` and `sources` are outside the digest so that "same brain state
    yields the same fingerprint" is a real property rather than one a clock defeats.
    """
    content = {
        "schema_version": document["schema_version"],
        "profile": document["profile"],
        "change_class": document["change_class"],
        "roads": document["roads"],
        "rules": document["rules"],
        "exemplars": document["exemplars"],
    }
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}"


def resolve_enrichment(
    spec: EnrichmentSpec,
    *,
    profile: str,
    change_class: str,
    client: BrainClient,
    now: datetime,
) -> dict[str, Any]:
    """Project `spec` into the canonical enrichment document."""
    roads: list[dict[str, Any]] = []
    rules: list[dict[str, Any]] = []
    exemplars: list[dict[str, Any]] = []
    sources: list[dict[str, str]] = []

    for slug in spec.code_road_slugs:
        payload = client.get_road(slug)
        sources.append(
            {"brain": "code", "endpoint": f"/api/road/{slug}", "query": slug}
        )
        roads.append({"brain": "code", **_pick(payload["road"], ROAD_FIELDS)})
        for rule in payload.get("rules", []):
            rules.append({"brain": "code", "road_slug": slug, **_pick(rule, RULE_FIELDS)})
        for exemplar in payload.get("exemplars", []):
            exemplars.append({"brain": "code", **_pick(exemplar, EXEMPLAR_FIELDS)})

    infra_rules = client.list_infra_rules()
    sources.append(
        {"brain": "infra", "endpoint": "/api/rules",
         "query": f"authority>={spec.infra_min_authority}"}
    )
    for rule in infra_rules:
        if _meets_authority(rule, spec.infra_min_authority):
            rules.append({"brain": "infra", "road_slug": None, **_pick(rule, RULE_FIELDS)})

    document: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "profile": profile,
        "change_class": change_class,
        "roads": sorted(roads, key=lambda road: (road["brain"], str(road["slug"]))),
        "rules": sorted(rules, key=lambda rule: (rule["brain"], int(rule["id"] or 0))),
        "exemplars": sorted(exemplars, key=lambda item: (item["brain"], int(item["id"] or 0))),
        "resolved_at": now.isoformat(),
        "sources": sources,
    }
    document["content_fingerprint"] = content_fingerprint(document)
    return document
```

- [ ] **Step 6: Run the tests**

```bash
cd ~/Projects/intent-packages && .venv/bin/pytest tests/factory/test_brains.py -v
```

Expected: all pass. If `test_infra_rules_are_filtered_by_authority_not_severity` reports a count other than 4, the live fixture has moved — re-read the fixture and update the expected count with a note, do not loosen the assertion.

- [ ] **Step 7: Verify against the LIVE brains once**

```bash
cd ~/Projects/intent-packages && BWS_ACCESS_TOKEN="$(security find-generic-password -s Claude -a BWS_ACCESS_TOKEN_VPS_BACKUP -w)" .venv/bin/python -c "
from datetime import UTC, datetime
from intent_packages.factory.brains import BrainClient, BrainKey, resolve_brain_key, resolve_enrichment
from intent_packages.profiles import PROFILES
keys = {b: resolve_brain_key(b) for b in BrainKey}
doc = resolve_enrichment(PROFILES['software-delivery'].enrichment, profile='software-delivery',
                         change_class='software-delivery', client=BrainClient(keys),
                         now=datetime.now(UTC))
print('roads', len(doc['roads']), 'rules', len(doc['rules']), 'exemplars', len(doc['exemplars']))
print('fingerprint', doc['content_fingerprint'][:20])
"
```

Expected: `roads 1 rules 13 exemplars 2` (9 code + 4 infra rules) and a `sha256:` prefix. A fixture test passing while the live call fails means the client is wrong, not the fixture.

- [ ] **Step 8: Run the full suite and commit**

```bash
cd ~/Projects/intent-packages && .venv/bin/pytest -q 2>&1 | tail -5
git add src/intent_packages/factory/brains.py tests/factory/ .bws-secrets.toml
git commit -m "feat(factory): resolve governed brain material at authoring time

Reads the brains' REST lookup API and projects a change class's declared roads
and rules into one canonical, fingerprinted document. The fingerprint covers
content only -- resolved_at and sources sit outside it, so 'same brain state,
same fingerprint' is a real property instead of one a clock defeats.

Authority floors, not severity floors: 12 infra rules are BLOCK, only 4 are
authority=required. No read-only brain credential exists; the contributor key
is the least privilege currently available."
```

---

## Task 5: wire the resolver into `factory decompose`

**Files:**
- Modify: the module that builds proposed-unit payloads in `src/intent_packages/factory/` (locate with `grep -rn "proposed_units" src/intent_packages/factory/`)
- Test: `tests/factory/test_profiles_dependency_update.py` or a new `tests/factory/test_decompose_enrichment.py`

**Interfaces:**
- Consumes: `resolve_enrichment`, `BrainClient`, `resolve_brain_key`, `BrainKey` from Task 4; `PROFILES` from Task 3.
- Produces: each proposed-unit dict in the `--data` payload carries `"context_enrichment": <document>`.

- [ ] **Step 1: Locate the payload builder**

```bash
cd ~/Projects/intent-packages && grep -rn "proposed_units\|unit_key" src/intent_packages/factory/*.py | head -20
```

Read the function that assembles the proposal body before editing.

- [ ] **Step 2: Write the failing test**

Create `tests/factory/test_decompose_enrichment.py`:

```python
"""Every proposed unit carries its class's enrichment, resolved once per proposal."""

import json
from datetime import UTC, datetime
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


class FakeBrainClient:
    def get_road(self, slug: str) -> dict:
        return json.loads((FIXTURES / "code_road_error_logging.json").read_text())

    def list_infra_rules(self) -> list[dict]:
        return json.loads((FIXTURES / "infra_rules.json").read_text())["rules"]


def test_every_proposed_unit_carries_the_same_document() -> None:
    """One resolution per proposal, not one per unit.

    Per-unit resolution would let two units of the same proposal disagree about
    the standards they were approved under.
    """
    from intent_packages.factory.brains import resolve_enrichment
    from intent_packages.profiles import PROFILES

    document = resolve_enrichment(
        PROFILES["dependency-update"].enrichment,
        profile="dependency-update",
        change_class="dependency-update",
        client=FakeBrainClient(),
        now=datetime(2026, 7, 30, tzinfo=UTC),
    )
    units = [{"unit_key": "a"}, {"unit_key": "b"}]

    enriched = [{**unit, "context_enrichment": document} for unit in units]

    fingerprints = {unit["context_enrichment"]["content_fingerprint"] for unit in enriched}
    assert len(fingerprints) == 1


def test_dependency_update_units_carry_an_empty_but_present_document() -> None:
    from intent_packages.factory.brains import resolve_enrichment
    from intent_packages.profiles import PROFILES

    document = resolve_enrichment(
        PROFILES["dependency-update"].enrichment,
        profile="dependency-update",
        change_class="dependency-update",
        client=FakeBrainClient(),
        now=datetime(2026, 7, 30, tzinfo=UTC),
    )

    assert document["roads"] == []
    assert len(document["rules"]) == 4  # the four infra authority=required rules
    assert document["content_fingerprint"].startswith("sha256:")
```

- [ ] **Step 3: Run to verify failure, then wire it**

```bash
cd ~/Projects/intent-packages && .venv/bin/pytest tests/factory/test_decompose_enrichment.py -v
```

In the payload builder, resolve **once per proposal** and attach the same document to every unit:

```python
    profile = PROFILES[profile_name]
    enrichment = None
    if profile.enrichment is not None:
        keys = {brain: resolve_brain_key(brain) for brain in BrainKey}
        enrichment = resolve_enrichment(
            profile.enrichment,
            profile=profile.name,
            change_class=profile.change_class or profile.name,
            client=BrainClient(keys),
            now=datetime.now(UTC),
        )
```

and add `"context_enrichment": enrichment` to each proposed-unit dict.

- [ ] **Step 4: Run the tests, then the full suite**

```bash
cd ~/Projects/intent-packages && .venv/bin/pytest tests/factory/test_decompose_enrichment.py -v
.venv/bin/pytest -q 2>&1 | tail -5
```

Expected: both new tests pass; the full suite's collected count is at or above its prior value.

- [ ] **Step 5: Run `make check` and commit**

```bash
cd ~/Projects/intent-packages && make check 2>&1 | tail -20
git add -A src/intent_packages/factory tests/factory
git commit -m "feat(factory): attach resolved enrichment to every proposed unit

Resolved once per proposal, not once per unit -- per-unit resolution would let
two units of one proposal disagree about the standards they were approved under."
```

- [ ] **Step 6: Open the PR**

```bash
cd ~/Projects/intent-packages && git push -u origin feat/wsp212-enrichment-resolver
gh pr create --title "WS-P2.12: authoring-time governed-knowledge enrichment" --body "Resolves each change class's declared roads and rules from the brains' REST lookup API and attaches a canonical fingerprinted document to every proposed unit.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01DQAhHi7JBpX2NNFKUdmijY"
```

---

## Task 6: orchestrator — schema, storage, write-once

**Prerequisite: Task 2's factory-runner PR is MERGED.** Verify before starting:
`gh pr list --repo AlobarQuest/factory-runner --state merged --limit 5`.

**Files:**
- Create: `migrations/versions/0021_wsp212_enrichment.py`
- Create: `src/orchestrator/kernel/enrichment.py`
- Modify: `src/orchestrator/persistence/models.py` (`WorkUnit`, `DecompositionProposalUnit`)
- Modify: `src/orchestrator/services/decomposition.py` (`ProposedUnit`, validation, persistence, `register_approved_unit` call at line ~310)
- Modify: `src/orchestrator/services/packages.py` (`register_approved_unit`)
- Modify: `src/orchestrator/api/schemas.py`
- Test: `tests/services/test_decomposition.py`, `tests/architecture/test_enrichment_write_once.py`

**Interfaces:**
- Consumes: the document shape from Task 4.
- Produces:
  - `orchestrator.kernel.enrichment.validate_enrichment(value: object) -> dict[str, Any]` — raises `DomainError` on every rejection, returns the normalized document.
  - `WorkUnit.context_enrichment: Mapping[str, Any] | None`
  - `ProposedUnit.context_enrichment: Mapping[str, Any] | None = None`

- [ ] **Step 1: Confirm the migration head**

```bash
cd ~/Projects/orchestrator && ls migrations/versions/ | sort | tail -2
```

Expected: `0020_wsp28_follow_up.py` is the latest. Use it as `down_revision`.

- [ ] **Step 2: Write the failing validation tests**

Create `tests/kernel/test_enrichment.py`:

```python
"""Ingress bounds for the enrichment document.

Every rejection is a DomainError. Only DomainError and APIAuthenticationError have
registered handlers, so anything else escaping a route is a bare 500.
"""

import pytest

from orchestrator.errors import DomainError
from orchestrator.kernel.enrichment import validate_enrichment


def _document(**overrides) -> dict:
    base = {
        "schema_version": 1,
        "profile": "software-delivery",
        "change_class": "software-delivery",
        "roads": [],
        "rules": [],
        "exemplars": [],
        "content_fingerprint": "sha256:" + "0" * 64,
        "resolved_at": "2026-07-30T00:00:00+00:00",
        "sources": [],
    }
    return {**base, **overrides}


def test_an_empty_document_is_valid() -> None:
    """Empty by content is a legitimate projection, not a malformed one."""
    assert validate_enrichment(_document())["roads"] == []


def test_a_missing_key_is_rejected() -> None:
    payload = _document()
    del payload["change_class"]

    with pytest.raises(DomainError) as error:
        validate_enrichment(payload)

    assert error.value.code == "context_enrichment_invalid"


def test_an_unknown_schema_version_is_rejected() -> None:
    with pytest.raises(DomainError) as error:
        validate_enrichment(_document(schema_version=2))

    assert error.value.code == "context_enrichment_invalid"


def test_an_oversized_document_is_rejected() -> None:
    huge = [{"brain": "code", "id": i, "category": "x", "severity": "BLOCK",
             "authority": "required", "rule": "x" * 1000, "reason": "y",
             "road_slug": None} for i in range(300)]

    with pytest.raises(DomainError) as error:
        validate_enrichment(_document(rules=huge))

    assert error.value.code == "context_enrichment_too_large"


def test_too_many_rules_is_rejected() -> None:
    many = [{"brain": "code", "id": i, "category": "x", "severity": "INFO",
             "authority": "informational", "rule": "r", "reason": "y",
             "road_slug": None} for i in range(201)]

    with pytest.raises(DomainError) as error:
        validate_enrichment(_document(rules=many))

    assert error.value.code == "context_enrichment_too_large"


def test_a_non_mapping_is_rejected() -> None:
    with pytest.raises(DomainError):
        validate_enrichment(["not", "a", "document"])


def test_none_passes_through_for_units_that_predate_enrichment() -> None:
    assert validate_enrichment(None) is None
```

- [ ] **Step 3: Run to verify failure**

```bash
cd ~/Projects/orchestrator && .venv/bin/pytest tests/kernel/test_enrichment.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'orchestrator.kernel.enrichment'`.

- [ ] **Step 4: Write the kernel module**

Create `src/orchestrator/kernel/enrichment.py`. **Do not use the bare tokens `dispatch`, `deploy`, or `merges` anywhere in this file, docstrings included** — the ws32/ws33 word guards scan runtime string literals across all of `src/orchestrator/`.

```python
"""Shape bounds for the governed-knowledge document a unit carries.

The document is authored outside this service and arrives on a decomposition
proposal. This module checks its SHAPE and SIZE only -- never its membership.
The vocabulary of roads and rules belongs to the authoring side; a second copy
here would be a vocabulary to keep in sync and a guard to explain.

It grants nothing. It is reference material a worker reads, never authority.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from orchestrator.errors import DomainError

SCHEMA_VERSION = 1
REQUIRED_KEYS = frozenset(
    {
        "schema_version",
        "profile",
        "change_class",
        "roads",
        "rules",
        "exemplars",
        "content_fingerprint",
        "resolved_at",
        "sources",
    }
)
MAX_BYTES = 16_384
MAX_ROADS = 50
MAX_RULES = 200
MAX_EXEMPLARS = 100
MAX_TEXT = 4_000


def validate_enrichment(value: object) -> dict[str, Any] | None:
    """Return the document, or raise DomainError. `None` is a unit that predates it."""
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise DomainError(
            "context_enrichment_invalid",
            "context enrichment must be a mapping",
            None,
        )
    missing = REQUIRED_KEYS - set(value)
    if missing:
        raise DomainError(
            "context_enrichment_invalid",
            f"context enrichment is missing required keys: {sorted(missing)}",
            None,
        )
    if value["schema_version"] != SCHEMA_VERSION:
        raise DomainError(
            "context_enrichment_invalid",
            f"unsupported context enrichment schema_version: {value['schema_version']!r}",
            None,
        )
    for key, limit in (("roads", MAX_ROADS), ("rules", MAX_RULES), ("exemplars", MAX_EXEMPLARS)):
        records = value[key]
        if not isinstance(records, list):
            raise DomainError(
                "context_enrichment_invalid",
                f"context enrichment {key} must be a list",
                None,
            )
        if len(records) > limit:
            raise DomainError(
                "context_enrichment_too_large",
                f"context enrichment carries {len(records)} {key}, over the limit of {limit}",
                None,
            )
        for record in records:
            _check_record(record, key)
    serialized = json.dumps(dict(value), sort_keys=True, separators=(",", ":"))
    if len(serialized.encode()) > MAX_BYTES:
        raise DomainError(
            "context_enrichment_too_large",
            f"context enrichment is {len(serialized.encode())} bytes, over {MAX_BYTES}",
            None,
        )
    return dict(value)


def _check_record(record: object, key: str) -> None:
    if not isinstance(record, Mapping):
        raise DomainError(
            "context_enrichment_invalid",
            f"every context enrichment {key} entry must be a mapping",
            None,
        )
    for field, field_value in record.items():
        if isinstance(field_value, str) and len(field_value) > MAX_TEXT:
            raise DomainError(
                "context_enrichment_too_large",
                f"context enrichment {key} field {field!r} exceeds {MAX_TEXT} characters",
                None,
            )
        if not isinstance(field_value, (str, int, float, bool, type(None))):
            raise DomainError(
                "context_enrichment_invalid",
                f"context enrichment {key} field {field!r} must be a scalar",
                None,
            )
```

- [ ] **Step 5: Run the validation tests**

```bash
cd ~/Projects/orchestrator && .venv/bin/pytest tests/kernel/test_enrichment.py -v
```

Expected: 7 passed.

- [ ] **Step 6: Write the migration**

Create `migrations/versions/0021_wsp212_enrichment.py`:

```python
"""WS-P2.12: carry governed-knowledge material on units and proposal units.

Revision ID: 0021_wsp212_enrichment
Revises: 0020_wsp28_follow_up
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0021_wsp212_enrichment"
down_revision = "0020_wsp28_follow_up"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "work_units",
        sa.Column("context_enrichment", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "decomposition_proposal_units",
        sa.Column("context_enrichment", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("decomposition_proposal_units", "context_enrichment")
    op.drop_column("work_units", "context_enrichment")
```

- [ ] **Step 7: Add the model columns**

In `src/orchestrator/persistence/models.py`, add to `WorkUnit` (after `authority_approval_id`) and to `DecompositionProposalUnit` (after `authority_fingerprint`):

```python
    context_enrichment: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
```

- [ ] **Step 8: Thread it through the services**

`services/decomposition.py`:
- add `context_enrichment: Mapping[str, Any] | None = None` to `ProposedUnit`
- in `_validate_proposed_units` (around line 474-499), call `validate_enrichment(unit.context_enrichment)` alongside `_validate_unit_constraints`
- persist it on the `DecompositionProposalUnit` row
- pass `context_enrichment=proposal_unit.context_enrichment` into the `register_approved_unit(...)` call at line ~310

`services/packages.py` — `register_approved_unit` gains a keyword-only `context_enrichment: Mapping[str, Any] | None = None` and assigns it **at construction only**, never by later mutation.

`api/schemas.py` — add `context_enrichment: dict[str, Any] | None = None` to the proposal-unit registration payload model and to `DecompositionProposalUnitResponse`.

- [ ] **Step 9: Write the write-once architecture test**

Create `tests/architecture/test_enrichment_write_once.py`, modelled on `tests/architecture/test_authority_write_once.py` (read it first and mirror its AST approach):

```python
"""`context_enrichment` is assigned at construction and never mutated.

What a worker was told is a record of what the unit executed under. A path that
rewrites it after approval would mean the stored document is no longer the one a
human approved -- the same reasoning that makes the authority envelope write-once.
"""

import ast
from pathlib import Path

SOURCE_ROOT = Path("src/orchestrator")
ATTRIBUTE = "context_enrichment"
ALLOWED_CONSTRUCTION_SITES = {
    "src/orchestrator/services/packages.py",
    "src/orchestrator/services/decomposition.py",
}


def test_context_enrichment_is_never_assigned_as_an_attribute() -> None:
    offenders: list[str] = []
    for source in sorted(SOURCE_ROOT.rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if isinstance(target, ast.Attribute) and target.attr == ATTRIBUTE:
                    offenders.append(f"{source}:{node.lineno}")
    assert not offenders, (
        f"{ATTRIBUTE} is assigned as an attribute at {offenders}. It is write-once: "
        "set it in the model constructor. A path that rewrites it means the stored "
        "document is no longer the one a human approved -- ship a fail-closed check "
        "with any such path, do not relax this test."
    )
```

- [ ] **Step 10: Prove the write-once guard FIRES**

Temporarily add `unit.context_enrichment = {}` to any function in `services/decomposition.py` and run:

```bash
cd ~/Projects/orchestrator && .venv/bin/pytest tests/architecture/test_enrichment_write_once.py -v
```

Expected: FAIL naming that file and line. **Revert the scratch line.** Record the failure message in the commit body.

- [ ] **Step 11: Migrate and run the affected suites**

```bash
cd ~/Projects/orchestrator && SECURITY_STANDARDS_DIR=tests/fixtures/security-standards \
  .venv/bin/alembic upgrade head
.venv/bin/pytest tests/kernel/test_enrichment.py tests/services/test_decomposition.py \
  tests/architecture/ -q 2>&1 | tail -5
```

Expected: `collected N items`, 0 failed.

- [ ] **Step 12: Commit**

```bash
cd ~/Projects/orchestrator && git add -A src/orchestrator migrations tests
git commit -m "feat(units): carry governed-knowledge material write-once on a work unit

Stores the authoring-time document on the proposal unit and the work unit,
bounded at ingress (16KB, 200 rules, 4000 chars per field) with every rejection
a DomainError -- an unhandled exception from a route is a bare 500 here.

Write-once, guarded: what a worker was told is a record of what the unit
executed under, so a path that rewrote it would mean the stored document is no
longer the approved one. Guard proven to fire."
```

---

## Task 7: orchestrator — serve it on the brief, pinned to factory-runner

**Files:**
- Modify: `src/orchestrator/services/runner_brief.py:35-78`
- Create: `tests/fixtures/runner_brief.json` (byte-identical to factory-runner's)
- Create: `tests/contract/test_runner_brief_contract.py`
- Modify: `tests/api/test_runner_brief_api.py`

**Interfaces:**
- Consumes: `WorkUnit.context_enrichment` from Task 6; the fixture from Task 2.
- Produces: `runner_brief()` returns an `"enrichment"` key.

- [ ] **Step 1: Copy the fixture byte-identically**

```bash
cp ~/Projects/factory-runner/tests/fixtures/runner_brief.json \
   ~/Projects/orchestrator/tests/fixtures/runner_brief.json
cd ~/Projects/orchestrator && python3 -c "
import hashlib, json, pathlib
d = json.loads(pathlib.Path('tests/fixtures/runner_brief.json').read_text())
print(hashlib.sha256(json.dumps(d, sort_keys=True, separators=(',',':')).encode()).hexdigest())
"
```

The printed hash must equal factory-runner's `CONTRACT_SHA256`. If it does not, the copy is not byte-identical — recopy.

- [ ] **Step 2: Write the failing contract test**

Create `tests/contract/test_runner_brief_contract.py`:

```python
"""The runner-brief cross-repo contract, orchestrator side.

`RunnerBrief` in factory-runner is `extra="forbid"`, so a key added here that the
runner does not know about raises at parse time and kills every run at claim.
Before WS-P2.12 nothing crossed this boundary for the brief -- only the authority
envelope had a shared fixture. `tests/fixtures/runner_brief.json` is byte-identical
to factory-runner's copy; CONTRACT_SHA256 makes a one-sided edit loud.
"""

import hashlib
import json
from pathlib import Path

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "runner_brief.json"
CONTRACT_SHA256 = "COPY_FROM_FACTORY_RUNNER"


def golden_brief() -> dict:
    return json.loads(FIXTURE.read_text())


def test_golden_brief_is_unchanged() -> None:
    canonical = json.dumps(golden_brief(), sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(canonical.encode()).hexdigest() == CONTRACT_SHA256


def test_the_served_brief_has_exactly_the_contracted_keys(migrated_session) -> None:
    """The derivation assertion: what the service builds, not what a file contains.

    A hash pin proves the fixture is unchanged and says nothing about the code.
    Comparing the SERVED key set means adding a brief key without updating both
    repos reds here -- which is the failure this contract exists to catch.
    """
    from orchestrator.services.runner_brief import runner_brief
    from tests.contract.test_runner_envelope_contract import _approved_ready_unit

    unit_id = _approved_ready_unit(migrated_session)

    served = runner_brief(migrated_session, unit_id)

    assert set(served) == set(golden_brief()), (
        "the served brief's key set has drifted from the cross-repo fixture. "
        "factory-runner's RunnerBrief is extra=forbid: an unknown key here kills "
        "every run at claim. Change BOTH repos' fixtures together."
    )
```

Set `CONTRACT_SHA256` to the hash printed in Step 1.

- [ ] **Step 3: Run to verify it fails**

```bash
cd ~/Projects/orchestrator && .venv/bin/pytest tests/contract/test_runner_brief_contract.py -v
```

Expected: `test_golden_brief_is_unchanged` PASSES (the fixture is a copy) and
`test_the_served_brief_has_exactly_the_contracted_keys` FAILS — the served brief has
no `enrichment` key yet. That asymmetry is the point: the hash test cannot see the code.

- [ ] **Step 4: Add the brief key**

In `src/orchestrator/services/runner_brief.py`, add to the returned dict after `"standing_context"`:

```python
        "enrichment": unit.context_enrichment,
```

Re-run the test; both must now pass.

- [ ] **Step 5: Prove the guard FIRES**

Temporarily add `"scratch": 1` to the dict returned by `runner_brief()` and run:

```bash
cd ~/Projects/orchestrator && .venv/bin/pytest tests/contract/test_runner_brief_contract.py -v
```

Expected: `test_the_served_brief_has_exactly_the_contracted_keys` FAILS on the extra key. **Revert.** Record the message in the commit body.

- [ ] **Step 6: Add the API-level test**

In `tests/api/test_runner_brief_api.py`, add:

```python
def test_the_brief_carries_enrichment_verbatim(client, ...) -> None:
    """Verbatim is what makes the projection deterministic: stored bytes out."""
    # Build a unit whose context_enrichment is a known document, GET the brief,
    # and assert response.json()["enrichment"] == that document, byte for byte
    # after canonical JSON serialization.
```

Fill in the fixture arguments to match the module's existing test signatures — read the file's other tests first and mirror them.

- [ ] **Step 7: Run the FULL gate**

```bash
cd ~/Projects/orchestrator && git status --short   # must be clean of stray files
SECURITY_STANDARDS_DIR=tests/fixtures/security-standards make check 2>&1 | tail -30
```

Expected: `collected N items` with N at or above the pre-change count, 0 failed. **Read the collected count — exit 0 alone proves nothing** (exit 5 is swallowed). If `ruff format --check .` reds on files this change never touched, that is pre-existing whole-repo debt — confirm with `git stash && ruff format --check .` before attributing it to this work.

Specifically confirm these are green: `test_wsp21_invariant_scan.py` (allowlist unchanged), `test_scope_guards.py` (route inventories unchanged), `test_ws32_scope_guards.py` / `test_ws33_scope_guards.py` (no banned tokens in the new module), `test_unreachable_guards.py` (`validate_enrichment` has its production caller), `test_cross_boundary_vocabulary.py`, `tests/idempotency/test_matrix.py`.

- [ ] **Step 8: Commit and open the PR**

```bash
cd ~/Projects/orchestrator && git add -A
git commit -m "feat(brief): serve governed-knowledge material on the runner brief

Returns the stored document verbatim -- verbatim is what makes the projection
byte-deterministic. Pins the brief shape with a fixture byte-identical to
factory-runner's, plus a derivation assertion comparing the SERVED key set, so
adding a key without updating both repos reds. Guard proven to fire."
git push -u origin feat/wsp212-context-enrichment
gh pr create --title "WS-P2.12: authoring-time work-unit context enrichment" --body "Stores the authoring-time governed-knowledge document write-once on a work unit and serves it on the runner brief. No new routes, no new orchestrator egress.

Requires AlobarQuest/factory-runner#<N> (merged) — RunnerBrief is \`extra=\"forbid\"\`.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01DQAhHi7JBpX2NNFKUdmijY"
```

**STOP — Devon's merge gate.**

---

## Task 8: deploy and drive the demonstration unit

Do not start until Tasks 5 and 7 are merged.

- [ ] **Step 1: Give `software-delivery` a routing row**

`routing-policy.toml` has no `[change_class.software-delivery]` row and a class absent there is a hard error. Add:

```toml
[change_class.software-delivery]
surface = "runner-implementation"
models = ["sonnet-5"]
rationale = "runner-implementation row default. First execution: WS-P2.12 enrichment demonstration."
decided = "2026-07-30"
```

Set `change_class="software-delivery"` on that profile's `DELIVERY_PROFILE`, bump `version`, and run `.venv/bin/pytest tests/test_profiles_dispatch.py -v`.

- [ ] **Step 2: Migrate production first, then swap the image**

Per the documented order: `alembic upgrade head` against production, then trigger the `Release image` workflow, then point Coolify at the new tag. The window between migrate and swap puts the old container into `/health/ready` 503 `migration_drift` — survivable only because neither health check consults `/health/ready`. Keep it short.

- [ ] **Step 3: Ask production what it runs**

```bash
curl -s https://sds.alobar.net/openapi.json | python3 -c "import sys,json; print(len(json.load(sys.stdin)['paths']))"
```

and verify the running container's `RepoDigest` equals the pushed digest. MERGED ≠ DEPLOYED.

- [ ] **Step 4: Author, intake, decompose, approve, ready, run**

Author a `software-delivery` intent package targeting this repo, then: browser `fetch` intake → `factory decompose --submit` → `/review` decomposition approval → `/review` authority approval → SYSTEM `commands/ready` → open the bounded configuration window → dispatch with a **fresh** `runner_attempt` ordinal → confirm a NEW record id and a new Actions run.

- [ ] **Step 5: Prove the enrichment reached the worker**

```bash
curl -s -H "Authorization: Bearer $SYSTEM" -H "X-Credential-Key-Id: orchestrator-system" \
  "https://sds.alobar.net/api/v1/work-units/<id>/runner-brief" \
  | python3 -c "import sys,json; d=json.load(sys.stdin)['enrichment']; print(d['content_fingerprint']); print(len(d['roads']),'roads',len(d['rules']),'rules')"
```

Then read the Actions run log for the prompt section, and confirm the resulting PR reflects the governed rules. Capture both as closeout evidence.

- [ ] **Step 6: Close the configuration window only after the run is terminal**

Terminal means all three: the Actions run concluded, the unit has left `executing`, and cost-actuals exist. Closing it early restarts the orchestrator into the runner's `finalize-run` and strands the unit — `fail-run` fails the same way, so the attempt is spent with nothing reported.

---

## Self-Review

**Spec coverage:** definition site → Task 3; resolver + fingerprint → Task 4; transport → Task 5; persistence + write-once + bounds → Task 6; brief + cross-repo contract → Tasks 2 and 7; prompt rendering → Task 1; ordering constraint → Global Constraints and Task 6's prerequisite; demo → Task 8. The spec's residuals need no task by definition.

**Type consistency:** `EnrichmentSpec(code_road_slugs, infra_min_authority)` is used identically in Tasks 3, 4, 5. `resolve_enrichment(spec, *, profile, change_class, client, now)` matches between Tasks 4 and 5. `validate_enrichment(value)` matches between Tasks 6's tests and its module. `context_enrichment` is the single attribute name across all three repos' payloads; the brief key is `enrichment` (deliberately different — it is the served projection, not the stored column).

**Placeholder scan:** one deliberate residual. Task 7 Step 6's API test names its fixture arguments generically, because `tests/api/test_runner_brief_api.py`'s existing signatures must be read and mirrored rather than guessed — writing a guessed signature into the plan would be worse than the instruction to read the file. Everything else carries the code an implementer runs.
