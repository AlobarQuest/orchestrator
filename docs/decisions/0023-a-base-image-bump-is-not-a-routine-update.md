# ADR-0023 — A base-image bump is not a routine update

- **Status:** Accepted
- **Date:** 2026-08-15
- **Decided by:** Devon
- **Constrains:** ADR-0018 (the auto-merge gate is a cascade, not a disjunction)

## Decision

**The `docker` package ecosystem is excluded from the Dependabot auto-merge cascade, at every
update type.** A base-image bump routes to a human, as a major does.

ADR-0018 settled *which updates are routine enough to land unattended*. This settles *which
ecosystems that reasoning applies to*, and the answer is: not this one.

## Why — the argument is ADR-0018's own

The cascade arms on two distinct grounds, and a docker bump satisfies **neither**.

**1. "The intent suffices," which ADR-0018 asserts holds for patch and minor "in every
ecosystem."** That is true where minor means backward-compatible *by contract*. **Docker tags are
not semver.** `python:3.14` is a language version that happens to occupy the minor digit; 3.12 →
3.14 removes standard-library modules and changes the C-API. Dependabot maps the digits onto semver
positions mechanically, so it reports `version-update:semver-minor` for a change that is a major
runtime replacement. The claim "in every ecosystem" was the generalisation that did not survive
contact with one where version numbers are not a compatibility promise.

**2. "The gate exercises it."** This is why the cascade permits github_actions **majors** — passing
means, in ADR-0018's words, *"the new version has been exercised exactly as it will be used."* For a
base image that premise fails. Measured on `orchestrator`: `quality.yml` runs on `pull_request` and
does `docker build` over the real `Dockerfile`, so the image **is** built and `uv sync --frozen`
would fail on any locked dependency lacking a wheel for the new interpreter — a real check. But
nothing **runs** the image: no container is started, and the suite executes on `setup-python`
**3.12**, never on the interpreter the image ships. A dependency that installs cleanly and fails at
import on a removed module passes every gate.

## What this costs, measured

Almost nothing. **`orchestrator` is the only repository declaring the `docker` ecosystem** (`uv`,
`github-actions`, `docker`), and none of the five repositories that currently carry the cascade
declares it — so this exclusion is a **no-op the day it ships** and takes effect only when the lane
is vendored to `orchestrator`. The live subject is a single pull request, `orchestrator#3`
(`python 3.12-slim → 3.14-slim`), which a person merges after reading what it is.

Note the blast radius is already bounded there: landing on `orchestrator` is **inert** — the release
image is a separate manual `workflow_dispatch` — so the failure mode was never an outage. It was
that the next release image would silently be built on a different interpreter, with no signal at
the moment someone triggered it.

## What would earn the permission back

**Run the image in CI.** Start the container and exercise it — a `/health/live` probe, or the suite
executed *inside* the image rather than beside it. That makes ADR-0018's second ground true for this
ecosystem, and the exclusion could then be revisited on its merits.

It is deliberately **not** a prerequisite for this decision: building a container smoke test to
justify auto-merging roughly one pull request a year is the wrong order, and the smoke test is worth
having for reasons that have nothing to do with Dependabot.

## Where it is enforced

In the `if:` condition of each repository's `.github/workflows/dependabot-auto-merge.yml`. The
workflow is **not** vendored by `code-standards`, so this is one edit per repository — including for
every repository onboarded to the lane after this date, which is the clause most likely to be
forgotten.
