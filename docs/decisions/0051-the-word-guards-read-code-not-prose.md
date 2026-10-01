# ADR 0051 — The word guards read code, not prose

**Date:** 2026-10-01
**Status:** Accepted (Devon, Tier 3 item 20, 2026-09-28: "keep identifier/import checks, stop
scanning prose, drop ws33 bare `merges`, replace ws52/53/61")

## Context

`test_ws32_scope_guards.py`, `test_ws33_scope_guards.py` and `test_ws34_scope_guards.py` refuse
words such as `dispatch`, `deploy`, `coolify` and `merges` in `src/orchestrator/`. They were
written when the orchestrator dispatched nothing, deployed nothing and merged nothing. That premise
has been false since ADR-0020: the orchestrator now dispatches work and lands pull requests, inside
named, separately guarded paths.

The guards read every string in a module, docstrings included, and ws33 and ws34 matched raw file
text. The 2026-09-27 debt survey counted fourteen recorded incidents of a guard going red on a
sentence that described code, each answered by rewording the sentence. A guard that is satisfied by
rewording a comment protects nothing.

## Decision

The three guards read code and not prose. `tests/architecture/code_terms.py` defines the boundary
once:

- They read identifiers (names, attributes, definitions, parameters, keyword arguments, and
  imported names), import paths, and string constants that are not docstrings and contain no
  whitespace once their ends are stripped — route paths, header names, literal identifiers such as
  `"workflow_dispatch"`.
- A docstring is prose whatever it contains. A string containing whitespace is prose: a message, a
  description, a sentence.
- ws33 no longer refuses the bare word `merges`, which only ever matched prose.
- ws34 no longer lists the spaced phrases `gh pr merge` and `git push origin main`, which a code
  term cannot contain.

Each guard carries paired controls: the same words as prose pass and as code fail.

The merge commands are not left unguarded. `test_wsp21_invariant_scan.py` reads raw file text for
`gh pr merge` and `git push origin main` and refuses any `.merge(` method call, with its own
exemption list, and `test_no_automatic_merge.py` covers the workflows. Neither is changed. No
equivalent raw-text scan exists for the dispatch vocabulary, so a spaced command string such as
`"gh workflow run factory-runner.yml"` is not read by these guards. That residual is accepted:
dispatch reaches GitHub only through `services/execution/dispatch.py`, which is the one module
allowed to name it.

`test_ws52_scope_guards.py`, `test_ws53_scope_guards.py` and `test_ws61_scope_guards.py` are
deleted. They checked that the release and observation routes call no lifecycle mutator, that
their services spell no merge or deploy word, and that they hold no literal header or token
template. One of those checks is behaviour rather than vocabulary and is kept: the release-artifact
routes call no lifecycle or worker mutator
(`tests/architecture/test_release_routes_call_no_mutator.py`). The rest are replaced by:

- **What an observation producer may do**: the OBSERVER role's confinement
  (`api/dependencies.py::_confine_observer`, tested by `tests/api/test_observer_role_confinement.py`),
  which refuses every write except `POST /api/v1/observations`.
- **What each out-of-process program may write**: the ADR-0039 guard
  (`tests/architecture/test_external_content_observes_only.py`).
- **Credentials in source**: `test_wsp21_invariant_scan.py::test_no_tracked_source_carries_a_secret`,
  which looks for credential shapes rather than header templates.

## Consequences

- Two exemptions lost their only reason to exist and were removed by the guards' own rot checks:
  `services/github_app.py` from ws32's file allowlist and `api/schemas/intake.py` from ws33's.
- Reading keyword arguments surfaced one call the old scan could not see:
  `services/verifier/verifier.py` passes `allow_generated_post_deploy=True`. It joins its verifier
  siblings in ws32's post-deploy allowlist.
- A single word with no whitespace in a non-docstring string is still read as code. `"Dispatch."`
  as a bare string constant still matches; that is accepted rather than special-cased.
- The `Known Non-obvious Invariants` bullets in `CLAUDE.md` that say a docstring reddens these
  guards are true of the code before this change and carry a dated correction.
