# Handoff: change-manager accepts a previous bearer (ADR-0055 increment 6)

change-manager must accept each machine bearer's old value beside its new one for a bounded time,
so the rotation executor can rotate its four bearers without an outage. ADR-0055 decision 7 says
change-manager "gets the equivalent change" of the orchestrator's overlap, and amendment 9,
decision 6 (Devon, 2026-10-16) chose the orchestrator's form over a second variable per bearer.
This handoff builds that change in change-manager, deploys it, cuts production over to it, and
verifies every real consumer still authenticates. It doesn't rotate anything.

HQ is the session that drove the operator bearer's rotation. Report to Devon; questions marked
**Stop and ask** go to him before you build past them.

## Read first

- `docs/superpowers/plans/2026-10-15-handoff-common-rules.md`: its **How to work** and **Closing
  steps** sections apply here in full. Its **Don't touch** section is spent: that rotation
  finished on 2026-10-16.
- ADR-0055 decision 7 and amendment 9 (`docs/decisions/0055-*.md`), and the increment 6 plan's
  "What isn't built", item 1 and item 5 (`docs/superpowers/plans/2026-10-14-rotation-increment-6-plan.md`).
- change-manager's `CLAUDE.md`, `app/auth.py`, `app/config.py` and `app/scopes.py`, in full.
- The orchestrator's overlap, which is the model. Read the shape and rules from the code, never
  from this page:
  - `src/orchestrator/main.py`: the credentials setting's parser, including `_previous_hash`.
  - `src/orchestrator/identity/auth.py`: `previous_hash_valid` and the matching rule beside it.
  - `src/rotation_worker/m2m_setting.py`: how the executor builds a setting with a `previous`.
  - `src/rotation_worker/destinations.py`: the `coolify-env-hash` destination kind.

## What HQ reads decision 6 to mean

"Like the orchestrator's" means each bearer is stored as a **sha256 hash, never plaintext**, with
an optional `previous` hash and its `until`, under the same rules: the previous hash is accepted
only before `until`, `until` is bounded, and boot refuses a setting whose previous hash equals a
current one. A side effect is that change-manager's Coolify settings stop holding plaintext
bearers, which ends the per-class table's stated exception for this class.

If anything you read contradicts this reading, **stop and ask** before building.

## The two design questions

1. **One variable with four entries, or four variables?** The ADR's text says each bearer's
   variable becomes JSON, so four variables (`M2M_TOKEN`, `M2M_TOKEN_READ`, `M2M_TOKEN_PROPOSE`,
   `M2M_TOKEN_OBSERVE`) is HQ's reading. It has a property worth keeping: the executor's write
   for one bearer touches only that bearer's variable, and it can compute the previous hash from
   the quarantined old value, so it doesn't need to read the setting first. Check whether each
   variable can carry the orchestrator's entry-keyed shape with one entry, so the existing
   `coolify-env-hash` kind (with its `entry` field) describes it unchanged. A new or changed
   destination kind is a cross-repository pin change (`tests/fixtures/rotation_destination_format.json`,
   pinned with intent-packages): **stop and ask** before proposing one.
2. **How the executor confirms a write took effect.** The orchestrator's write is checked through
   `GET /api/v1/m2m-identities`. change-manager has no such route. HQ's recommendation is **no new
   route**: the executor proves the new bearer by probe (200, beside a random bearer refused 401)
   and the old one dead by probe after retire. A route would also need a credential for the
   executor to read it. If you find the probe can't distinguish a loaded setting from an unloaded
   one, **stop and ask**.

## Requirements

- **Keep every fail-closed property** documented in `_token_scopes` and `require_m2m`: an unset
  scope grants nothing, an empty bearer grants nothing, a value configured for two scopes resolves
  to the narrower one, and 401 (not recognised) stays distinct from 403 (recognised, not allowed).
- **Prove each new guard fires** on a known-bad input, by mutation: a previous hash after its
  `until` gets 401; a previous hash equal to a current one stops boot; a malformed setting grants
  nothing. Report the mutation count and every survivor.
- **Pin the setting's shape across repositories** with a fixture both repositories test against,
  like the orchestrator's other cross-repository contracts. Build the fixture from the
  orchestrator's parser, not from prose.
- **Tests:** `tests/conftest.py` and at least seven test files set `auth.settings.m2m_token`
  directly. Change them deliberately; don't let a shim keep the old plaintext path alive in
  production.
- **Review:** this is authentication on a production service. Run `/code-review` and an
  adversarial review at the strongest tier before merging.

## The cutover

A merge to change-manager's `main` is a production deploy (`.github/workflows/deploy.yml`), and
Dependabot merges land there on their own schedule (#112 landed this week). A Coolify variable edit
is inert until the next restart or deploy. So the cutover must leave change-manager working if it
redeploys at **any** point in the sequence. Writing hashed values before the parser that reads
them ships, or shipping a parser that refuses plaintext before the values are rewritten, breaks all
four consumers at the next unplanned deploy. Design the sequence and state, in the pull request, why
each intermediate state survives a redeploy.

Facts the sequence must respect:

- The Coolify application is `re45tafypao3nly3qa9a79dp` (registry: `.cred-consumers.toml`). Each
  variable has two rows (`is_preview` false and true); write both. PATCH may answer 500; fall back
  to delete and recreate (orchestrator `CLAUDE.md`, the Coolify `/envs` invariant).
- **Computing a hash needs the value in-process.** The four keepers are in BWS project
  `Ops / Platform` (UUIDs in change-manager's `.cred-consumers.toml`). Find which Mac identity reads
  each one, with an empty `HOME`, rather than assuming. Read, hash and write in one process; the
  transcript gets sha256 prefixes and lengths only.
- **Which Coolify token writes.** Not infraops' env tools (their responses carry values), and not
  `Rotation / Coolify` (the executor's, VM only). The production Coolify API token in
  `Ops / Platform` (`bbd71f41-b7df-4ae9-8fdb-b41501447308`) is the candidate: check its abilities
  in-process before relying on it, and print only status codes.
- Verify the deploy by **revision**, not liveness: deploy.yml already polls for the pushed sha.

## Verify on production

After the cutover, every real consumer of each scope must still authenticate:

| Scope | Consumers |
|---|---|
| full | infraops `scripts/drift-audit.sh` (03:00) and `scripts/change-window.sh` (04:00) |
| read | the orchestrator's `run-work-carrier`, `run-bump-proposer`, `run-estate-landing`, `run-inert-landing` |
| propose | `run-work-carrier`, `run-bump-proposer`, `run-change-proposer`, `run-rotation-proposer` |
| observe | `run-deploy-watcher` |

Take this table as a starting point and confirm it from the launchers and the registry. Prove each
scope with an in-process probe (its bearer gets 200 on a route its scope may reach, a random bearer
401), sending a named User-Agent (Cloudflare blocks default ones). Then confirm each scheduled
consumer's next run succeeds, from its Healthchecks check or its log.

## Registry

When production holds hashes, update change-manager's `.cred-consumers.toml`: each bearer's
`coolify-env` consumer becomes what describes it now. Run infraops' `cred-findings` afterwards and
report what it says. Don't set `rotated_by_sds` or `rotate_requested`.

## Out of scope

The executor's change-manager adapter, the change-manager rotation packages, moving the keepers to
`Rotation / Keepers`, and the four rotations themselves. Each comes after this is deployed and
verified.

## Closing steps

- **You merge** your own pull requests once their checks pass. Remember that a change-manager merge
  deploys production; time the merges by the cutover sequence.
- **Tear down** as the common rules say.
- **Report** to Devon: what merged (pull request numbers read from `gh`), the cutover sequence and
  why each step survived a redeploy, the mutation results, each scope's probe and each scheduled
  consumer's next run, and anything found and not fixed.
