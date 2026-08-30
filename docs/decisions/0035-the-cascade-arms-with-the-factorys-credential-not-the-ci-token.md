# ADR-0035 — The cascade arms with the factory's credential, not the CI token

- **Status:** Accepted, **IMPLEMENTATION BLOCKED 2026-08-30 — DO NOT BUILD THIS AS WRITTEN.**
  The decision's *problem* stands; its chosen *identity* is defective and the fork replacing it is
  open. See "Blocked" immediately below before reading anything else here.
- **Date:** 2026-08-29
- **Decided by:** Devon
- **Relates to:** ADR-0016 (native auto-merge for routine updates), ADR-0018 (the gate is a
  cascade), ADR-0034 (the split is by outcome, not update type)
- **Supersedes:** the 2026-08-25 ruling to leave the cascade on `secrets.GITHUB_TOKEN`, on the
  reopen trigger that ruling named for itself

## Blocked — the identity choice destroys the record this change exists beside

**Found by the build session, confirmed by measurement, before anything landed.** ADR-0035 guards
one leg of the landing record and breaks the other.

`basis_of` (`landing_ledger/record.py:207`) requires the gate to have succeeded **and**
`is_machine(landed_by)` — and `is_machine` is `login.endswith("[bot]")` (`:161`), fed from
`merged_by.login`. **GitHub attributes an auto-merge to the ARMING identity**, so changing what arms
the cascade changes what the ledger records about every landing:

| armed by | `merged_by` | `is_machine` | basis |
|---|---|---|---|
| `secrets.GITHUB_TOKEN` — all six 2026-08-28 landings | `github-actions[bot]` / Bot | true | `auto_merge_rule` |
| a user PAT — `orchestrator#167`, **the mechanism this ADR adopts** | `AlobarQuest` / **User** | **false** | **`human`** |

So every future cascade landing records as landed by a person, and `audit_landing` then returns
`(), (), ()` for any basis that is not the rule (`audit.py:355`) — **Detector A stops auditing the
native lane, silently**, with its `permitted` denominator at zero. Nothing raises.

**It is not correctable afterwards.** `permitted_by` goes into an immutable observation whose
`source_reference` is not content-addressed and which has no delete route, so a later correction is
an `observation_conflict` — the state the six known-defective rows already exit 3 on nightly.

**The Consequences section below is wrong where it says attribution "is not a regression".** It is
one, for the landing ledger, and this ADR's own coupling argument names losing the
`auto_merge_rule` basis as the failure that coupling exists to prevent. The estate also knew:
CLAUDE.md carries the `[bot]`-suffix bullet, `basis_of`'s own docstring names the residual, and a
backlog item has said so since 2026-08-10 — which HQ read aloud to Devon the day before writing
this ADR.

**The open fork, for Devon.** (a) *Ledger-side* — discriminate on the gate run's ARM STEP conclusion
at the landed head rather than on who pressed merge; changes what every landing asserts, so it needs
its own ADR. (b) *App-side* — arm with `alobar-sds-dispatch[bot]`, which keeps `basis_of` working
untouched because it is type Bot with the suffix; costs two secrets across six repositories plus a
token-mint step, hence a new blob and a new transcription, and its arming behaviour is unmeasured
(~15 minutes on a disposable repository).

**What survives regardless:** the problem statement, the `pull_request_target` secret measurement,
the six-repository coupling, and one defect already fixed in the held `#205` — `_checks_and_gate`
filtered `event != "pull_request"`, so the gate's own run would have been invisible under the new
trigger.

## Decision

**The six cascade gates move to `on: pull_request_target` and arm auto-merge with
`FACTORY_PR_TOKEN` rather than `secrets.GITHUB_TOKEN`.** No credential is created and none is
copied into a second store.

## The problem, which has been a P1 since 2026-08-11

An auto-merge armed with `GITHUB_TOKEN` fires **no `on: push` workflow** — GitHub's recursion
guard. So every cascade landing reaches `main` with the repository's own gate never having run on
the result. The backlog has said so since 2026-08-11: *"main can be red in intent-packages,
security-standards, project-standards, infraops-mcp-server and factory-runner with nothing
reporting it."*

The 2026-08-25 ruling left it alone, and its reasoning was measured rather than lazy: the cascade
*"drains roughly one item per Dependabot cycle, so daily verification is already close to per-merge
cadence."* **ADR-0034 falsified that premise four days later** — six landings in one evening, three
of them inside twelve minutes, none with a `push` run. The ruling named its own reopen trigger, and
the fact that changed is the drain rate.

## Why it could not simply be done before

The cascade runs `on: pull_request`, and for an event **triggered by Dependabot** GitHub resolves
`secrets.*` from the **Dependabot** secret store. Measured 2026-08-25 and again on 2026-08-29: all
six repositories have **zero** Dependabot secrets, while five carry `FACTORY_PR_TOKEN` in the
**Actions** store. The credential existed and was invisible from where it was needed, so every
alternative meant six new copies in a second store — against an estate already bitten by a rotation
that missed copies and left two tokens dead for a fortnight.

## What unblocked it, measured

**Dependabot secret treatment follows the TRIGGERING ACTOR, not the pull request's author — and
`pull_request_target` resolves the Actions store even when Dependabot triggers it.** Probed on
`orchestrator#3`, the inert docker bump, with a control that read a name present in no store so the
probe could report absence:

| run | triggering actor | head | `FACTORY_PR_TOKEN` | control |
|---|---|---|---|---|
| `33265253593` | `AlobarQuest` (a human reopen) | `0a7a2ed5` | RESOLVED | EMPTY |
| `33265327300` | **`dependabot[bot]`** (a real rebase) | `69c7d38a` | **RESOLVED** | EMPTY |

**The first run is the cautionary half and is recorded deliberately.** HQ triggered it by reopening
the pull request and briefly read it as the answer; a human-triggered event on a Dependabot pull
request is not a Dependabot-triggered event. `github.actor` was in the probe's own output. A probe
can discriminate perfectly and still answer a different question: control for the VARIABLE, not
only for the instrument.

## Why `pull_request_target` is safe for THIS workflow

The standing objection to `pull_request_target` is that it runs with the base repository's secrets
against a pull request's content. **This workflow never reads that content.** It reads metadata
through `dependabot/fetch-metadata` pinned by SHA (`25dd0e34`, v3.1.0), which asks the API, and
then calls `gh pr merge --auto --squash`. There is no `actions/checkout` and nothing from the pull
request is executed. Moving the trigger also means the gate is read from the **base branch**, which
is stronger for a gate, not weaker.

**The author guard stays and is a different question from the triggering actor.** The job's
`if: github.event.pull_request.user.login == 'dependabot[bot]'` asks who WROTE the pull request; the
probe measured who TRIGGERED the event. Both must hold and neither implies the other.

## Consequences

- **It is SIX repositories, not seven.** A first draft of this ADR and its handoff both said seven,
  double-counting `orchestrator` — which is itself one of the six cascade repositories as well as
  the home of `rules.py`, so its pull request carries both halves of the change. The build session
  produced six and was right to.

- **The gate blobs move, so `landing_ledger/rules.py` must be re-transcribed in the SAME
  operation.** `rule_for` is keyed by blob sha and fails closed. Split them and `bump_proposer`
  refuses every repository with `gate-not-transcribed`, and the landing ledger loses the
  `auto_merge_rule` basis — the record of *why* each bump was permitted, which ADR-0034 exists to
  preserve. Same seven-repo coupling, same reason. The six gates are one identical blob
  (`3457db3cee85`) as of this decision, so it is one new transcription rather than three.
- **`factory-runner` holds no Actions secrets at all** and needs `FACTORY_PR_TOKEN` added. It is
  also the one repository where `enforce_admins` is true, so a bad merge there stops every dispatch
  in the estate.
- **The rotation surface does not grow.** No new secret, no second store, and the token already
  exists in five of the six. `FACTORY_PR_TOKEN` has a BWS record (`a3240c2e-…`, `SDS Operator`) and
  is documented in factory-runner's `.bws-secrets.toml`; a rotation already means re-setting every
  copy, and this adds one repository to that list.
- **Attribution stays untrue, and this ADR does not fix it.** `FACTORY_PR_TOKEN` is a fine-grained
  PAT on Devon's own account, so autonomous landings continue to be attributed to a person. That is
  already true of every factory pull request, so it is not a regression — but the identity that
  would make it true is the `Alobar SDS Dispatch` App, deliberately not taken here because it needs
  two secrets per repository and **its arming behaviour is unmeasured**. Choosing the cheaper
  identity makes the wrong one cheaper to keep; that is the trade, taken knowingly.
- **One clause is inferred rather than measured, and it is the point of the whole change.** That an
  auto-merge armed with `FACTORY_PR_TOKEN` fires `push` CI follows from 2026-08-15, where `#167`
  armed under a user identity fired a push `Quality` on its merge commit while three
  `GITHUB_TOKEN`-armed merges the same day fired none. `FACTORY_PR_TOKEN` is a user-account PAT, so
  the inference is short — but it is an inference, and it is the acceptance test for the
  implementation rather than a settled fact.
