# code-standards:managed:start — everything down to :end is replaced by `code-standards sync`.
# code-standards Makefile (vendored). Edit upstream and `code-standards sync`.
# check: full-repo lint/type/test pass for humans and CI.
#        diff-scoping is the hook's job — this runs everything.
# fix:   run all autofixers (ruff, prettier).
#
# Each block is scoped to the LANGUAGE present, gated on the per-language config
# marker that `init` vendors (python → pyproject.toml; ts → eslint.config.mjs /
# tsconfig.json / .prettierrc; shell → .shellcheckrc; node → package.json).
# Without this, a TS-only repo still ran pytest (exit 5, "no tests") and failed
# make check (Phase 6 finding L1). That language gating stays.
#
# WITHIN a present language the gate REFUSES: a missing tool is a hard error, not
# a "skipping" line. A gate that skips is a gate that attests — it reports success
# for work it did not do, and `make check` exiting 0 stops being evidence that
# anything ran. Measured cost of the old behaviour: infraops-mcp-server carried
# 524 passing tests across 59 files that its declared gate had never once
# executed, CI green throughout (WS-P2.24; the same fix factory-runner made to its
# own copy in WS-P2.20).
#
# Same rule for the runners: a test runner that collected NOTHING is a hard error
# too — unless the repo genuinely contains no tests, which is the one case in
# which "nothing ran" and "nothing to run" are distinguishable. That is what the
# *_TEST_FILES probes below decide.
#
# Tools are invoked by bare name and resolved from the repo-local .venv/bin and
# node_modules/.bin FIRST (see PATH), so "installed" means installed for THIS
# repo wherever a repo-local install exists — a global pytest cannot collect
# against the wrong interpreter while the repo's own environment is empty.

.PHONY: check fix

VENV_BIN := $(CURDIR)/.venv/bin
NODE_BIN := $(CURDIR)/node_modules/.bin
export PATH := $(VENV_BIN):$(NODE_BIN):$(PATH)

# need,<tool>,<how to get it> — shell fragment; hard-fails when <tool> is absent.
need = command -v $(1) >/dev/null 2>&1 || { echo "make check: $(1) not found — $(2)"; exit 1; }

# Test files on disk. "The runner found nothing" is a defect when these exist and
# a plain fact when they do not.
#
# The prune list must cover INSTALLED code, not just build output: a dependency
# ships its own tests, and a repo with a `venv/` (brain) or `.venv/` holds
# hundreds of them under site-packages. Counting those would make the probe
# answer "this repo has tests" for every repo that has dependencies. The
# shellcheck step below shares it for the same reason: a gate that refuses must
# refuse over the repo's OWN code, and infraops-mcp-server carries four vendored
# *.sh files under node_modules that no one in this portfolio can fix.
#
# The JS patterns name explicit script extensions rather than `*.test.*`, which
# would match a data file like `openapi.spec.json` and demand a test script for
# a repo that has no tests.
PRUNE_DIRS := \( -name .git -o -name .venv -o -name venv -o -name site-packages -o -name node_modules -o -name build -o -name dist -o -name coverage -o -name .tox -o -name .worktrees -o -name .pytest_cache -o -name .mypy_cache -o -name .ruff_cache \) -prune
JS_TEST_EXT := -name '*.test.js' -o -name '*.test.jsx' -o -name '*.test.mjs' -o -name '*.test.cjs' -o -name '*.test.ts' -o -name '*.test.tsx' -o -name '*.spec.js' -o -name '*.spec.jsx' -o -name '*.spec.mjs' -o -name '*.spec.cjs' -o -name '*.spec.ts' -o -name '*.spec.tsx'
PY_TEST_FILES := find . $(PRUNE_DIRS) -o \( -name 'test_*.py' -o -name '*_test.py' \) -print
JS_TEST_FILES := find . $(PRUNE_DIRS) -o \( $(JS_TEST_EXT) \) -print

check:
	@if [ -f pyproject.toml ]; then $(call need,ruff,install it with: uv sync); ruff check .; fi
	@if [ -f pyproject.toml ]; then $(call need,ruff,install it with: uv sync); ruff format --check .; fi
	@if [ -f pyproject.toml ]; then $(call need,pyright,install it with: uv sync); pyright; fi
	@if [ -f eslint.config.mjs ]; then $(call need,eslint,install it with: npm ci); eslint .; fi
	@if [ -f tsconfig.json ]; then $(call need,tsc,install it with: npm ci); tsc --noEmit; fi
	@if [ -f .prettierrc ]; then $(call need,prettier,install it with: npm ci); prettier --check .; fi
	@if [ -f .shellcheckrc ]; then $(call need,shellcheck,install it with: brew install shellcheck / apt-get install -y shellcheck); find . $(PRUNE_DIRS) -o -name '*.sh' -exec shellcheck {} +; fi
	@if [ -f pyproject.toml ]; then $(call need,pytest,install it with: uv sync); \
	  pytest; rc=$$?; \
	  if [ $$rc -eq 5 ] && [ -z "$$($(PY_TEST_FILES))" ]; then rc=0; fi; \
	  if [ $$rc -eq 5 ]; then echo "make check: pytest collected no tests, but this repo HAS test files. Fix collection (testpaths, conftest, interpreter) — a gate that collects nothing attests."; fi; \
	  exit $$rc; fi
	@if [ -f package.json ]; then $(call need,node,install Node.js); $(call need,npm,install Node.js); \
	  node -e 'const s = require("./package.json").scripts || {}; process.exit(s.test ? 0 : 3)'; rc=$$?; \
	  if [ $$rc -eq 0 ]; then npm test; \
	  elif [ $$rc -ne 3 ]; then echo "make check: package.json could not be read as JSON."; exit 1; \
	  elif [ -n "$$($(JS_TEST_FILES))" ]; then echo "make check: this repo HAS test files but package.json declares no test script, so the gate cannot run them. Add one."; exit 1; \
	  fi; fi

fix:
	@if [ -f pyproject.toml ]; then $(call need,ruff,install it with: uv sync); ruff check --fix .; fi
	@if [ -f pyproject.toml ]; then $(call need,ruff,install it with: uv sync); ruff format .; fi
	@if [ -f .prettierrc ]; then $(call need,prettier,install it with: npm ci); prettier --write .; fi
# code-standards:managed:end — content OUTSIDE these markers is yours and is preserved. Delete both markers to own the whole file: sync then writes nothing here and says so every run.
