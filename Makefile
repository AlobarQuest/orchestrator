# LOCALLY OWNED (no code-standards managed block). NOT a decision — this is the
# pre-WS-P2.24 template, skip-semantics and all, and it stopped receiving updates
# because sync was all-or-nothing. Adopting the current template (`code-standards
# adopt --file Makefile`) is the intended fix and is a behaviour change to this
# repo's gate, so it belongs to a change that can verify it, not to the migration
# that made it visible.
# code-standards Makefile (vendored). Edit upstream and `code-standards sync`.
# check: full-repo lint/type/test pass for humans and CI.
#        diff-scoping is the hook's job — this runs everything.
# fix:   run all autofixers (ruff, prettier).
#
# Each block is scoped to the LANGUAGE present, gated on the per-language config
# marker that `init` vendors (python → pyproject.toml; ts → eslint.config.mjs /
# tsconfig.json / .prettierrc; shell → .shellcheckrc).  Without this, a TS-only repo still ran
# pytest (exit 5, "no tests") and failed make check (Phase 6 finding L1).
# Within a present language, each tool is `command -v`-guarded so an absent tool
# degrades gracefully; a present tool that exits non-zero propagates its failure
# (real lint errors are NOT swallowed).

.PHONY: check fix

VENV_BIN := $(CURDIR)/.venv/bin
NODE_BIN := $(CURDIR)/node_modules/.bin
export PATH := $(VENV_BIN):$(NODE_BIN):$(PATH)

check:
	@if [ -f pyproject.toml ]; then if command -v ruff >/dev/null 2>&1; then ruff check .; else echo "ruff not installed — skipping ruff check"; fi; fi
	@if [ -f pyproject.toml ]; then if command -v ruff >/dev/null 2>&1; then ruff format --check .; else echo "ruff not installed — skipping ruff format check"; fi; fi
	@if [ -f pyproject.toml ]; then if command -v pyright >/dev/null 2>&1; then pyright; else echo "pyright not installed — skipping pyright"; fi; fi
	@if [ -f eslint.config.mjs ]; then if command -v eslint >/dev/null 2>&1; then eslint .; else echo "eslint not installed — skipping eslint"; fi; fi
	@if [ -f tsconfig.json ]; then if command -v tsc >/dev/null 2>&1; then tsc --noEmit; else echo "tsc not installed — skipping type-check"; fi; fi
	@if [ -f .prettierrc ]; then if command -v prettier >/dev/null 2>&1; then prettier --check .; else echo "prettier not installed — skipping prettier check"; fi; fi
	@if [ -f .shellcheckrc ]; then if command -v shellcheck >/dev/null 2>&1; then find . -name '*.sh' -not -path './.git/*' -exec shellcheck {} +; else echo "shellcheck not installed — skipping shellcheck"; fi; fi
	@if [ -f pyproject.toml ]; then if command -v pytest >/dev/null 2>&1; then pytest; rc=$$?; if [ $$rc -ne 0 ] && [ $$rc -ne 5 ]; then exit $$rc; fi; else echo "pytest not installed — skipping tests"; fi; fi

fix:
	@if [ -f pyproject.toml ]; then if command -v ruff >/dev/null 2>&1; then ruff check --fix .; else echo "ruff not installed — skipping ruff fix"; fi; fi
	@if [ -f pyproject.toml ]; then if command -v ruff >/dev/null 2>&1; then ruff format .; else echo "ruff not installed — skipping ruff format"; fi; fi
	@if [ -f .prettierrc ]; then if command -v prettier >/dev/null 2>&1; then prettier --write .; else echo "prettier not installed — skipping prettier fix"; fi; fi
