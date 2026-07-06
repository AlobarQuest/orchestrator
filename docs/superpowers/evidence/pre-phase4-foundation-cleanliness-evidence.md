# Pre-Phase-4 Foundation Cleanliness Evidence

Date: 2026-07-06

Governing intent package:

- Package: `pre-phase4-foundation-cleanliness`
- Revision: `1`
- Approved hash: `b66d56799e8e0709d73e57b36d56f344f54b9a8bb6cb5c0b0f4e2823042a1824`
- Approval recorded in `intent-packages/packages/pre-phase4-foundation-cleanliness/lineage.yaml`

## Scope Boundary

This cleanup did not implement Phase 4 runtime dispatch, GitHub Actions worker execution,
production deployment, live factory-events mutation, Coolify mutation, Phase-5 verifier logic,
tracker canonicalization, brain learning/promotion, automatic merge, or infrastructure mutation.

## Repository Evidence

| Repo | Branch / base | Dirty state | Gate evidence |
| --- | --- | --- | --- |
| `code-standards` | `codex/pre-phase4-foundation-cleanliness-code-standards` from `f08d1c4` | Template and test changes only | `make check`: ruff clean, pyright `0 errors, 0 warnings, 0 informations`, `216 passed` |
| `project-standards` | `codex/pre-phase4-foundation-cleanliness-project` from `ad3e36d` | `pyproject.toml` only | `make check`: ruff clean, pyright `0 errors, 0 warnings, 0 informations`, `227 passed` |
| `security-standards` | `codex/pre-phase4-foundation-cleanliness-security` from `d8110cb` | Makefile, pyproject, factory-events README, integration test changes | `make check`: ruff clean, pyright `0 errors, 0 warnings, 0 informations`, `173 passed, 5 deselected`; `FACTORY_TEST_DSN= make check-integration` exits `2` with explicit DSN prerequisite |
| `change-manager` | `codex/pre-phase4-foundation-cleanliness-change-manager` from `923278a` | Makefile, `pyproject.toml`, `uv.lock` | `make check`: ruff clean, pyright `0 errors, 0 warnings, 0 informations`, `105 passed` |
| `brain` | `codex/pre-phase4-foundation-cleanliness-brain` from `3bf3678` | `requirements-dev.txt` only | `make check`: ruff clean, pyright `0 errors, 0 warnings, 0 informations`, `210 passed` |
| `infraops-mcp-server` | `codex/pre-phase4-foundation-cleanliness-infraops` from `38db0bb` | Makefile only | `make check && npm test`: prettier clean, Vitest `56 passed`, `475 passed`; local `node_modules/.bin` tools are on PATH |
| `intent-packages` | `codex/pre-phase4-foundation-cleanliness-intent` from `6a42e33` | package, pyright config, validator tests and warning behavior | `validate --all`: every package OK with no warning lines; `make check`: pyright `0 errors, 0 warnings, 0 informations`, `159 passed` |
| `orchestrator` | `codex/pre-phase4-foundation-cleanliness-orchestrator` from `1990c41` | `pyproject.toml`, `uv.lock` | `PATH="$PWD/.venv/bin:$PATH" TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@192.168.97.2:5432/orchestrator_test make check`: ruff clean, pyright `0 errors, 0 warnings, 0 informations`, `668 passed` |
| `alobar-id` | `codex/pre-phase4-foundation-cleanliness-alobar-id` from `3d806f9` | `PROJECT.md` now declares required check | No local Makefile. Required check is `portfolio-scan` via `launchagent:com.devon.portfolio-scan`; covered by `portfolio foundation` required-check conformance. |
| `vps-backup` | `main...origin/main` at `a4e561c` | Clean | No local Makefile. Required checks are `backup-run` and `backup-verify` via launchagents; covered by `portfolio foundation` required-check conformance. |

## Resolved Findings

- `orchestrator`, `change-manager`, and `brain` no longer emit the Starlette/httpx pytest warning.
- `project-standards`, `security-standards`, and `intent-packages` pyright gates report `0 errors, 0 warnings, 0 informations`.
- `security-standards` factory-store tests are no longer default skips; they are explicit integration tests deselected by the default gate and exposed through `make check-integration`.
- `intent-packages validate --all` no longer prints the legacy `ws-2.3-intent-authoring-skill` profile warning; active unprofiled packages with profile evidence tags still warn.
- `change-manager make check` no longer resolves Homebrew Python for the Alembic migration test.
- `infraops-mcp-server make check` now prepends `node_modules/.bin`, so local `tsc` and prettier are found instead of silently skipped when Node itself is available.
- `brain/uv.lock` was an untracked three-line generated stub; Devon approved deleting it because no active development was using it.
- `alobar-id/PROJECT.md` dirty state is now attributed as the missing required-check declaration.

## Foundation Matrix

Final command:

```bash
cd /Users/devon/Projects/project-standards
uv run portfolio foundation
```

Observed result:

```text
foundation: 10 repos · violations=0 accepted=0 unknown=0
```

## Remaining Human Actions

- Devon reviews and merges per-repo cleanup PRs manually.
- Launchagent-backed operational checks for `alobar-id` and `vps-backup` remain external operational evidence, not local code gates.
