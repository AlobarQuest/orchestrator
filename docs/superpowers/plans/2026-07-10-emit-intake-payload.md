# Offline intake-payload emit — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an offline `emit-intake-payload` CLI subcommand that runs the load-bearing local package verification and writes the attested `POST /api/v1/package-intakes` request body, so a human can intake an approved package into production by POSTing that body from a browser session.

**Architecture:** Extract the request-body construction `intake-package` already does into a shared `_build_intake_payload` helper, then add a second command that builds the same body but emits it (stdout or `--out <file>`) instead of POSTing. No server, proxy, auth, or network change; the emit is purely local.

**Tech Stack:** Python 3.12+, Typer CLI (`orchestrator.cli`), pytest + `typer.testing.CliRunner`. Design spec: `docs/superpowers/specs/2026-07-10-offline-intake-payload-emit-design.md`.

## Global Constraints

- Python 3.12+. Resolve tools from the repo-local `.venv/bin` before global PATH.
- Follow existing `cli.py` patterns: `@app.command`, `_run(operation, json_output)`, `_emit`, `CliError`. Do not invent a new output/error style.
- Deterministic JSON only (`sort_keys=True`), matching `_emit`.
- The emit command must make **no** network call and read **no** `ORCHESTRATOR_API_TOKEN`. Zero new credential surface.
- The emitted payload is package metadata + attestation hashes — no secrets. Do not add any token/secret to any file.
- DRY, YAGNI, TDD, frequent commits. `intake-package` and `emit-intake-payload` must build the body through the *same* helper so they cannot drift.
- Gate: `make check` exit 0 does NOT prove tests ran — read the `collected N items` count. The full suite needs Postgres + `SECURITY_STANDARDS_DIR` + a migrated DB (CI `quality.yml` supplies these); the focused CLI tests in this plan need none of that.

---

## File Structure

- Modify: `src/orchestrator/cli.py` — add `_build_intake_payload` helper; refactor `intake_package` to use it; add `emit_intake_payload` command. (~30 lines; the file already owns all CLI commands — follow its structure, do not split it.)
- Create: `tests/cli/test_emit_intake_payload_cli.py` — tests for the new command, modeled on `tests/cli/test_package_intake_cli.py:508`.
- Create: `docs/operations/package-intake.md` — the emit → browser-POST operator flow.

Fixtures already exist and are reused as-is: `tests/fixtures/intent-packages/ws32-approved-software` (approved) and `tests/fixtures/intent-packages/ws32-draft-software` (unapproved).

---

### Task 1: Extract the shared intake-payload builder

Pure refactor: no behavior change. Pull the body construction out of `intake_package` so the new command reuses it verbatim. The existing intake test (`test_package_intake_cli.py:508`) is the regression guard.

**Files:**
- Modify: `src/orchestrator/cli.py` (add helper near `_load_intake_payload` ~line 178; change `intake_package` body ~lines 230-236)
- Test: `tests/cli/test_package_intake_cli.py` (existing, unchanged — used as guard)

**Interfaces:**
- Produces: `_build_intake_payload(path: Path, source_repository: str, idempotency_key: str) -> JsonObject` returning `{**loaded_payload, "idempotency_key": idempotency_key, "expected_version": 0}`.

- [ ] **Step 1: Run the existing intake test to establish a green baseline**

Run: `cd ~/Projects/orchestrator && .venv/bin/pytest tests/cli/test_package_intake_cli.py -q`
Expected: PASS (baseline; note the collected count).

- [ ] **Step 2: Add the shared builder** after `_load_intake_payload` in `src/orchestrator/cli.py`

```python
def _build_intake_payload(
    path: Path, source_repository: str, idempotency_key: str
) -> JsonObject:
    return {
        **_load_intake_payload(path, source_repository),
        "idempotency_key": idempotency_key,
        "expected_version": 0,
    }
```

- [ ] **Step 3: Refactor `intake_package` to use it**

Replace the `operation` body of `intake_package` (`@app.command("intake-package")`) with:

```python
    def operation() -> Any:
        payload = _build_intake_payload(Path(path), source_repository, idempotency_key)
        return request("POST", "/api/v1/package-intakes", payload)
```

- [ ] **Step 4: Run the existing intake test to confirm no behavior change**

Run: `.venv/bin/pytest tests/cli/test_package_intake_cli.py -q`
Expected: PASS, same collected count as Step 1.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/cli.py
git commit -m "refactor: extract _build_intake_payload shared by intake-package"
```

---

### Task 2: Add the `emit-intake-payload` command

**Files:**
- Modify: `src/orchestrator/cli.py` (new `@app.command("emit-intake-payload")` after `intake_package`)
- Test: `tests/cli/test_emit_intake_payload_cli.py` (create)

**Interfaces:**
- Consumes: `_build_intake_payload` (Task 1), `_run`, `_emit`, `CliError`.
- Produces: CLI command `emit-intake-payload <path> --source-repository <repo> --idempotency-key <key> [--out <file>] [--json]`. With `--out`, writes deterministic JSON (`sort_keys`, compact, trailing newline) to the file and emits `{"written": "<path>"}`; without `--out`, emits the payload as JSON to stdout. Verification failure → exit 1, nothing written.

- [ ] **Step 1: Write the failing tests** in `tests/cli/test_emit_intake_payload_cli.py`

```python
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import orchestrator.package_sources as package_sources
from orchestrator.cli import app
from orchestrator.package_sources import VerifiedApproval

_FIXTURE = "tests/fixtures/intent-packages/ws32-approved-software"
_BASE_ARGS = [
    _FIXTURE,
    "--source-repository",
    "AlobarQuest/intent-packages",
    "--idempotency-key",
    "package-intake-1",
]


def _pass_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        package_sources,
        "_verify_current_approval",
        lambda *args: VerifiedApproval(
            approved_by="devon",
            approved_at="2026-07-05T00:02:00Z",
            approval_event_id="22222222-2222-2222-2222-222222222222",
            approval_ledger_commit="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        ),
    )
    monkeypatch.setattr(package_sources, "_git_head", lambda path: "deadbeef")


def test_emit_payload_matches_intake_package_post_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pass_verification(monkeypatch)
    posted: dict[str, object] = {}

    def fake_request(method: str, path: str, payload=None):
        posted.update(payload=payload)
        return {"id": "revision-1", "revision": 1}

    monkeypatch.setattr("orchestrator.cli.request", fake_request)

    intake = CliRunner().invoke(app, ["intake-package", *_BASE_ARGS, "--json"])
    assert intake.exit_code == 0
    emit = CliRunner().invoke(app, ["emit-intake-payload", *_BASE_ARGS, "--json"])
    assert emit.exit_code == 0
    assert json.loads(emit.stdout) == posted["payload"]


def test_emit_payload_writes_out_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _pass_verification(monkeypatch)
    out = tmp_path / "intake.json"
    result = CliRunner().invoke(
        app, ["emit-intake-payload", *_BASE_ARGS, "--out", str(out), "--json"]
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"written": str(out)}
    body = json.loads(out.read_text(encoding="utf-8"))
    assert body["package_id"] == "ws32-approved-software"
    assert body["idempotency_key"] == "package-intake-1"
    assert body["expected_version"] == 0
    assert body["verification_mode"] == "caller_attested_cli_verified"


def test_emit_payload_fails_on_unapproved_package(tmp_path: Path) -> None:
    out = tmp_path / "intake.json"
    result = CliRunner().invoke(
        app,
        [
            "emit-intake-payload",
            "tests/fixtures/intent-packages/ws32-draft-software",
            "--source-repository",
            "AlobarQuest/intent-packages",
            "--idempotency-key",
            "package-intake-1",
            "--out",
            str(out),
        ],
    )
    assert result.exit_code == 1
    assert not out.exists()


def test_emit_payload_is_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    _pass_verification(monkeypatch)
    first = CliRunner().invoke(app, ["emit-intake-payload", *_BASE_ARGS, "--json"])
    second = CliRunner().invoke(app, ["emit-intake-payload", *_BASE_ARGS, "--json"])
    assert first.exit_code == 0
    assert first.stdout == second.stdout
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/cli/test_emit_intake_payload_cli.py -q`
Expected: FAIL — `emit-intake-payload` is not a registered command (Typer exits non-zero / "No such command").

- [ ] **Step 3: Implement the command** — add after `intake_package` in `src/orchestrator/cli.py`

```python
@app.command("emit-intake-payload")
def emit_intake_payload(
    path: Path,
    source_repository: Annotated[str, typer.Option("--source-repository")],
    idempotency_key: Annotated[str, typer.Option("--idempotency-key")],
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Write the payload to a file instead of stdout."),
    ] = None,
    json_output: JsonOption = False,
) -> None:
    def operation() -> Any:
        payload = _build_intake_payload(Path(path), source_repository, idempotency_key)
        if out is None:
            return payload
        try:
            out.write_text(
                json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
        except OSError as error:
            raise CliError(
                {"code": "output_write_failed", "message": f"could not write {out}"}
            ) from error
        return {"written": str(out)}

    _run(operation, json_output)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/cli/test_emit_intake_payload_cli.py -q`
Expected: PASS (4 tests, `collected 4 items`).

- [ ] **Step 5: Lint the changed files**

Run: `.venv/bin/ruff check src/orchestrator/cli.py tests/cli/test_emit_intake_payload_cli.py && .venv/bin/ruff format --check src/orchestrator/cli.py tests/cli/test_emit_intake_payload_cli.py`
Expected: no violations.

- [ ] **Step 6: Commit**

```bash
git add src/orchestrator/cli.py tests/cli/test_emit_intake_payload_cli.py
git commit -m "feat: emit-intake-payload command for browser-POST intake"
```

---

### Task 3: Operator documentation

**Files:**
- Create: `docs/operations/package-intake.md`

- [ ] **Step 1: Write the operations doc**

```markdown
# Package intake in production (human actor)

`POST /api/v1/package-intakes` is human-actor-only and, in production, reachable
only through the `orchestrator-intake-human` Traefik router (Alobar ID
forward-auth). The orchestrator also does not re-verify the approval server-side
(`verification_mode == "caller_attested_cli_verified"`) — it trusts the CLI's
local verification. So intake is split: the CLI verifies and emits the request
body; a human POSTs it from a logged-in browser.

## Steps

1. Emit the verified body (offline — no API token, runs the hash / verify-approval
   / factory-chain checks; requires the local package sources at
   `~/Projects/intent-packages`, `~/.factory/events.jsonl`,
   `~/Projects/security-standards`):

   ```bash
   orchestrator emit-intake-payload <package-dir> \
       --source-repository AlobarQuest/intent-packages \
       --idempotency-key <unique-key> \
       --out /tmp/intake.json
   ```

   A package that fails verification exits non-zero and writes nothing.

2. In a browser tab already authenticated to `https://sds.alobar.net`, open the
   devtools console and POST the body:

   ```js
   const body = /* paste the contents of /tmp/intake.json */;
   let r = await fetch('/api/v1/package-intakes', {
     method: 'POST',
     headers: { 'Content-Type': 'application/json' },
     body: JSON.stringify(body),
   });
   if (r.status === 401) {  // known first-POST quirk: retry once
     r = await fetch('/api/v1/package-intakes', {
       method: 'POST',
       headers: { 'Content-Type': 'application/json' },
       body: JSON.stringify(body),
     });
   }
   console.log(r.status, await r.json());
   ```

   Success returns the `revision_id`. The idempotency key makes a re-run after an
   ambiguous attempt replay to the same revision rather than double-registering.
```

- [ ] **Step 2: Commit**

```bash
git add docs/operations/package-intake.md
git commit -m "docs: package intake (human actor) operator flow"
```

---

### Task 4: Full gate and PR

**Files:** none (verification + PR only)

- [ ] **Step 1: Run the focused CLI suite**

Run: `.venv/bin/pytest tests/cli/ -q`
Expected: PASS. Confirm the collected count is non-zero (exit 0 alone is not proof — see Global Constraints).

- [ ] **Step 2: Run `make check` if the local env has Postgres + `SECURITY_STANDARDS_DIR`; otherwise rely on CI**

Run: `make check`
Expected: PASS with a real `collected N items`. If the local environment lacks Postgres/`SECURITY_STANDARDS_DIR`, a bare run fails `18 failed …` *unmodified* (known invariant) — do not treat that as caused by this change; push and let CI `quality.yml` run the authoritative full suite, then read its collected count.

- [ ] **Step 3: Run `/code-review` on the diff**

Review `git diff origin/main...HEAD` against `~/Developer/code-standards/STANDARDS.md`. Address any correctness / duplication / weak-test findings before opening the PR.

- [ ] **Step 4: Push and open the PR for Devon to merge**

```bash
git push -u origin feat/emit-intake-payload
gh pr create --title "feat: offline emit-intake-payload for human-actor production intake" \
  --body "$(cat <<'EOF'
## What
Adds `emit-intake-payload`: an offline CLI command that runs the load-bearing local package verification and emits the attested `POST /api/v1/package-intakes` body, so a human can intake an approved package into production by POSTing it from a logged-in sds.alobar.net browser session.

## Why
`POST /api/v1/package-intakes` is human-actor-only at the proxy and the server trusts the client's attestation (it does not re-verify). The existing `intake-package` CLI speaks M2M only, so it cannot intake in production. This bridges the gap without adding any credential handling to the CLI.

## Scope
- New `emit-intake-payload` command + shared `_build_intake_payload` helper (DRY with `intake-package`).
- Operator doc `docs/operations/package-intake.md`.
- No server / proxy / auth change; authority approvals remain browser fetches.

Design + plan: `docs/superpowers/specs/2026-07-10-offline-intake-payload-emit-design.md`, `docs/superpowers/plans/2026-07-10-emit-intake-payload.md`.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 5: Report the PR URL to Devon.** Do not merge — Devon merges after the named `Quality` check is green.

---

## Self-Review

**Spec coverage:** command (`emit-intake-payload`) → Task 2; local verification reused → Task 1/2 via `_load_intake_payload`; stdout + `--out` → Task 2; verification-failure-before-browser → Task 2 test 3; deterministic JSON → Task 2 test 4; zero credential surface → no `request()`/token in the command (Task 2); operator workflow + first-401 retry + idempotency → Task 3 doc; out-of-scope items untouched (no server/proxy/authority changes). Covered.

**Placeholder scan:** none — all steps carry concrete code/commands. (The doc's `/* paste … */` is an intentional operator instruction, not a plan placeholder.)

**Type consistency:** `_build_intake_payload(path, source_repository, idempotency_key) -> JsonObject` is defined in Task 1 and consumed identically in `intake_package` (Task 1) and `emit_intake_payload` (Task 2). `_run`, `_emit`, `CliError`, `JsonOption`, `Any`, `Annotated`, `typer`, `json`, `Path` are all already imported in `cli.py`.
