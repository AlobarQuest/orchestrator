# ADR-0018 — The auto-merge gate asks one question at a time

- **Status:** Accepted
- **Date:** 2026-08-08
- **Decided by:** Devon
- **Relates to:** ADR-0016 (native auto-merge for routine updates), `STANDARDS.md`
  "One question per predicate", Code Brain rule #68

## Context

ADR-0016 gave routine dependency updates to GitHub's native auto-merge. The rule that
decides which updates qualify accumulated as three clauses `OR`ed together:

```yaml
update-type == 'version-update:semver-patch' ||
update-type == 'version-update:semver-minor' ||
package-ecosystem == 'github_actions'
```

Read as three peers, that says "here are three independent reasons to allow." It is not
what the code does, and tracing it took a truth table.

**The first two clauses answer "what kind of change is this?" The third answers "what kind
of thing is being changed?"** Answers to different questions cannot constrain one another,
so what the rule permits is a cross product nobody enumerated. Three consequences, none of
them intended by anyone:

1. **The third clause admits its entire category.** Its step was named "patch, minor, and
   GitHub Actions **major** updates" and the reasoning written for it was *"an Actions major
   is exercised by the check that gates it."* The code says `package-ecosystem ==
   'github_actions'` — any update at all, of any intent, in that ecosystem.
2. **Inside that category the first two clauses are dead code.** Delete them and
   `github_actions` behaviour is bit-for-bit unchanged; only the other four ecosystems change.
3. **Refusing an unmatched update is emergent, not stated.** Nothing says "no acceptable
   intent → refuse"; three positive clauses simply fail to match. A fourth clause matching
   an empty string would open it silently and no test would notice.

A `docker` patch or minor arms today under clause 1 or 2, because those clauses never
consult the ecosystem. Nobody decided that; it has not happened only because Docker tags
rarely parse as semver.

## Decision

**Write the rule as the cascade it has always been.**

> **Q1 — is the declared intent sufficient on its own?**
> `patch` or `minor` → allow.
>
> **Q2 — asked only when Q1 says no. The intent is `major`, which we do not accept on its
> own. Is there another reason this one is safe?**
> For GitHub Actions there is: the required check that gates the pull request *is* the thing
> being bumped, so passing it means the new version has been exercised exactly as it will be
> used. A package major has no such property.
>
> **Neither answers yes → refuse, explicitly.**

## Why this is safe to change

**It changes no decision the gate has ever made.** Every `github_actions` pull request in
estate history — 26 across eight repositories, all states — is `semver-major`, so clause 3
has only ever admitted majors. Q1 has always returned a definite answer before Q2 was
reached. The disjunction has *behaved* as a cascade for its entire operating life.

Structurally that is not luck: **a tag-pinned action can only ever produce a major.**
`actions/checkout@v4` follows a moving major tag, so patch and minor releases move `v4` in
place and generate no pull request at all — you receive them with no PR, no check and no
gate. Only `v4 → v7` changes the ref, and that is a major.

**Exactly one of twenty cells changes**: an absent `update-type` in `github_actions` armed
before and refuses now. It has never occurred, and refusing is what we want — Dependabot
omits `update-type` for requirement-range bumps and for tags it cannot parse, so the absence
covers several unlike situations and asserts nothing about any of them.

## The naming trap, recorded because it cost several exchanges

**`package-ecosystem` names the manifest a dependency lives in. It does not describe how the
pull request is processed.** Every Dependabot PR is evaluated by a GitHub Actions workflow —
if the field meant that, it would match everything and the other clauses would be dead.

It is populated from **the second segment of the branch name**, not from `dependabot.yml`:

| PR | branch | `package-ecosystem` | what it edits |
|---|---|---|---|
| ruff bump | `dependabot/uv/ruff-0.16.0` | `uv` | `pyproject.toml`, `uv.lock` |
| checkout bump | `dependabot/github_actions/actions/checkout-7` | `github_actions` | `.github/workflows/quality.yml` |

So `github_actions` sits in the same list as `uv`, `npm_and_yarn`, `pip` and `docker`: a
workflow file is just another manifest whose declared dependencies happen to be Actions.

Two spellings follow from the same fact and have both drawn blood. `dependabot.yml` declares
`github-actions` and `npm`; the output emits **`github_actions`** and **`npm_and_yarn`**. The
first mismatch shipped a clause to five repositories that matched nothing and cleared none of
the eight pull requests it was written for. It failed closed, which is the only reason it was
cheap.

## Consequences

- The unmatched case is now a stated refusal, so a future clause cannot open it by accident.
- Adding a rule means answering "which question does this answer?" — and if it is neither Q1
  nor Q2, that is a third question and the cascade needs a new step, not another `OR`.
- The next SHA-pinned Actions release will be the first `github_actions` **minor** this estate
  has seen (`dependabot/fetch-metadata` is pinned by commit in four repositories). Under the
  cascade it is admitted at Q1 on its intent, which is correct and now legible; under the
  disjunction it would have been ambiguous which clause governed.

## What this deliberately does not do

It does not decide whether requirement-range bumps or Docker tags should ever arm. Those
carry no declared intent, so under this structure they fail Q1, find no answer at Q2, and are
refused — which is the current behaviour, now expressed rather than incidental. Whether Q2
should grow an answer for them is a separate decision requiring evidence about what the update
does, not about what it is called.
