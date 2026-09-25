# ADR-0045: The lane edits one Dependabot branch per repository at a time

- Status: Accepted
- Date: 2026-09-25
- Deciders: Devon
- Supersedes: the unmerged 2026-09-20 decision to stop freshening Dependabot's branches
  (`docs/superpowers/specs/2026-09-20-dependabot-branch-freshening-design.md` and its plan, both on
  `main` since #286). No ADR was ever merged for that decision, so this is where its reversal is
  recorded. Those two files are not edited.
- Amends: ADR-0019 Increment 6 (the estate lane's branch-update act) and ADR-0038 part 2 (the inert
  lane's). Each act gains one conjunct. ADR-0024's shared predicate, `qualifies_for_branch_update`,
  is untouched.
- Spec: `docs/superpowers/specs/2026-09-25-serialize-dependabot-freshening-design.md`. Plan:
  `docs/superpowers/plans/2026-09-25-serialize-dependabot-freshening.md`. Evidence:
  `~/docs/software-delivery-system/2026-09-25-dependabot-freshening-evidence.md`.

## Context

Both landing lanes bring a Dependabot pull request up to date with its base when being behind is
its only remaining obstacle (ADR-0019 Increment 6, ADR-0038 part 2, the "sole remaining obstacle"
rule of ADR-0024). They do it with GitHub's update-branch call, which **merges the base into the
head under the App's identity**. Dependabot then disowns the branch. Asked to rebase it, it answers
that the pull request "has been edited by someone other than Dependabot" and offers only
`@dependabot recreate`.

That produces a deadlock nobody can leave:

1. A landing moves `main`; every sibling in the repository falls behind.
2. The lane freshens them. Each now carries an App commit.
3. The lane lands one of them, and that landing conflicts another.
4. Dependabot will not rebase the conflicted one, because it was edited.
5. The lane will not freshen it, because `qualifies_for_branch_update` refuses a conflicted head
   (correctly: the call fails at the remote).

**The App cannot recover it.** `@dependabot recreate` posted as `alobar-sds-dispatch[bot]` gets
201 and then Dependabot's reply, "only users with push access can use that command". The same
command under a user identity recreated both stuck pull requests in place (same number,
`DIRTY` → `CLEAN`). No PAT goes in the landing path, because the acting identity is also the
recorded attribution (CLAUDE.md, the arming-credential bullet).

Four pull requests have been stuck this way: `brain#70` and `change-manager#87` (recreated by a
user on 2026-09-20, then landed by the lane), `factory-runner#71` (closed by a user), and
**`change-manager#93`, stuck at the time of writing** — `DIRTY`, open, conflicted by `#92`'s
landing on 2026-09-24.

### The first decision, and why it was reversed

On 2026-09-20 the answer was **stop creating the condition**: delete the estate lane's
branch-update act outright (its whole freshening population is Dependabot, since
`landing_author_not_the_update_bot` is never subtracted), and withhold the inert lane's act for
`dependabot[bot]` only, keeping it for the fork-sync bot. That spec said, in §3, *"a
behind-but-clean Dependabot pull request now waits for Dependabot to freshen it"*, and admitted in
§7 that how long it would wait was **unmeasured** — the estate had never watched Dependabot freshen
an unedited branch, because the lane always got there first. The spec and plan merged as #286.
Code was built on the `stop-freshening` branch (`d966cc2`, `38746ab`, `f66125f`, `632a388`,
`b2f90d5`) and never merged. Its §9 promised an ADR, which was never written.

The open question in §7 was then measured, and it killed the decision:

- **580** pushes to default branches that did not touch a dependency file produced **1**
  Dependabot rebase.
- Across **51** weekly Dependabot runs where an open, non-conflicted pull request under 30 days
  old was behind its base, Dependabot rebased **0**.
- Stretches behind with no Dependabot push reach **about 20 days** (the longest re-read:
  `orchestrator#2`, 19.83 days; `brain#31`, 19.63).

Dependabot does push to a branch it owns — on conflict (about two minutes after the conflicting
push), on a newer version, on a user's request — but **it does not maintain a branch that is merely
behind.** Handing freshening back to it would have left every such branch waiting indefinitely.
**Devon's ruling, 2026-09-25: hold the first design and redesign.** Keep freshening, narrowed to
the deadlock's precondition.

### The precondition, measured

42 pull requests have been freshened by the App (brain 19, change-manager 14, factory-runner 3,
orchestrator 6) across 72 update events; 35 landed clean, 4 stuck. (On 2026-09-20 the same census
read 26 / 50 / 24 / 2 — the lane kept running between the drafts.) In **all four** stuck cases:

- the conflict came from a sibling Dependabot pull request **the lane itself landed**, sharing
  `pyproject.toml`/`uv.lock` or a requirements file;
- the lane had freshened **both members of the pair seconds apart in one pass**, then landed one:
  `brain#69`/`#70` (09-19 06:15:34/:38), `change-manager#86`/`#87` (09-17 06:15:32/:36),
  `change-manager#92`/`#93` (09-22 06:15:33/:37), `factory-runner#70`/`#71` (09-04 02:35:21/:24).
  In the last two, **both** members got their FIRST edit in that pass.

And in the four observed cases where a landing conflicted a sibling that had **not** been edited,
Dependabot rebased it within about two minutes. So the precondition is exact: **two branches in one
repository are both not owned by Dependabot, and the lane lands one of them.** One edited branch
beside Dependabot-owned siblings is safe.

## Decision

> **The lane never makes a Dependabot-owned branch edited while another open Dependabot pull
> request in the same repository is edited and still queued to land.** A branch that is already
> edited may always be freshened again.

In practice: at most one lane- or human-edited, landable Dependabot pull request per repository —
the one the lane will land next. Every sibling stays Dependabot's, so Dependabot rebases it when a
landing conflicts it.

### Ownership: GitHub and the event log together

Each commit on a Dependabot pull request (`GET /repos/{r}/pulls/{n}/commits`, paginated to the end)
is exactly one of:

- **Dependabot's** — author `dependabot[bot]`, committer `web-flow`, and GitHub-verified;
- **foreign** — an author present and not `dependabot[bot]`, or a committer present and not
  `web-flow` (catches a person's local rewrite, which keeps the author);
- **unclassified** — anything else.

A pull request is **edited** when (a) it carries a foreign commit, or (b) the event log holds a
branch update for it (`estate_pr_branch_update.updated` / `inert_pr_branch_update.updated`) whose
recorded `head_sha` is the pull request's **current** head and whose `occurred_at` is under **ten
minutes** old. It is **owned** when every commit is Dependabot's, the list was read completely, its
last SHA equals the head the open-list call returned, and (b) does not hold. Anything else is
**unestablished**, and is treated as neither: on the target that forbids updating it beside a
holding sibling, and on a sibling it forbids releasing.

Why both sources: **GitHub alone** misses the edit just made — update-branch answers 202 and does
the work afterwards, and the lander asks about siblings seconds apart, which is exactly the two
"first edit in one pass" pairs above. **The event log alone** never forgets — a user's recreate
hands the branch back with a new head, and keying (b) on the current head retires the event as soon
as the head moves.

**The ten-minute bound.** Without it, a 202 GitHub accepts and never delivers keeps the pre-update
head current forever and holds the repository forever. Delivery measured about 12 seconds
(CLAUDE.md, 2026-08-14); sibling acts in a pass are 3–4 seconds apart. Ten minutes is over 40× the
delivery, longer than a lander pass, shorter than either lander's hourly cadence. The trade: a
delivery slower than ten minutes reopens the window for one pass.

### The three refusal classes

A sibling that is not owned has its own lane's admission composed, and every refusal it can name is
in exactly one class:

- **Holding** — it is still queued and the condition clears without anyone acting:
  `DELIBERATE_REFUSALS` (pace, window), the freshness-derived refusals (behind; a rollout pin that
  differs because behind), and the three post-update transients `landing_checks_in_flight`,
  `landing_checks_awaiting_verdict`, `landing_mergeability_unknown`. Without the transients the
  branch just freshened would stop holding in the very pass that freshened it. An empty refusal list
  holds too: that sibling is next.
- **Read failure** — its state was not established: the eighteen `…_unreadable`, `…_unconfigured`,
  `…_ambiguous`, `…_unidentified`, `landing_estate_unknown`, `landing_mergeability_unrecognised` and
  `landing_app_credentials_missing` codes, one set for both lanes.
- **Releasing** — everything else, at runtime as the complement. Red checks and a conflicted head
  release on purpose: neither clears on its own, and holding behind them would stall the repository
  on a condition already reported on its own line. On any positively named condition the rule never
  withholds more than the lane did before it.

A test spells the releasing set out as a literal and asserts the three together equal every refusal
constant both admission modules define, disjoint — so a new code reds CI until somebody classifies
it, while the runtime still releases on it.

### The outcomes, and the two codes

At each act, after `qualifies_for_branch_update` says yes, and only for a Dependabot target (a
sync-bot pull request is never withheld and never holds):

1. **Release** — the target is already edited. Freshening it again costs nothing.
2. **Withhold, deliberately** — another Dependabot pull request is edited and holding, and the
   target is owned: `estate_branch_update_sibling_holding` / `inert_branch_update_sibling_holding`.
3. **Withhold, as a finding** — that could not be established (a failed or truncated read, a
   sibling whose answer carries a read-failure code or whose composition raised, an unestablished
   sibling whose answer would hold, or a holding sibling beside an unestablished target):
   `estate_branch_update_siblings_unreadable` / `inert_branch_update_siblings_unreadable`.
4. **Release** — otherwise.

Two codes because the landers must treat them oppositely: outcome 2 clears when the branch ahead
lands, outcome 3 is the system not knowing. Neither code contains the other. Each lander's
`_UPDATE_SELF_CLEARING` gains its lane's `…_sibling_holding` and deliberately not
`…_siblings_unreadable`.

### The served fact, and the `waiting` status

Both admission responses gain **`branch_update_withheld_for_sibling: bool`**, true exactly when the
act would reach outcome 2 — never on outcome 3, and never when the branch does not qualify. A scan
that fails leaves the admission read answering, with the field false; the act then meets the same
failure and refuses as a finding. It is a fact about an **observed holding sibling**, not a record
that "the lane declined", because keying on the declining is the fail-open CLAUDE.md's durability
ruling names.

Both landers read it. Their update passes skip a subject the answer marks withheld (no update line;
the landing line already said why). Their classifiers, when the key is `True`, subtract the
freshness-derived refusals exactly as they already do beside an exception; if nothing is left
unexplained the line reads **`waiting`** — a new status in both `_NOT_A_FINDING` sets and report
orders. An exception still outranks it, and a missing or false key removes nothing, so an older
orchestrator leaves the line a finding. It is kept separate from `deliberate` and `exception`
because collapsing categories loses which is which: a deliberate refusal clears on a clock, an
exception never clears, a waiting sibling clears when the branch ahead of it lands.

**The field and the status are spelled differently on purpose.** The status was built as
`withheld` and renamed `waiting` (`3c3bb67`), because `withheld` contains `held`, and a status that
contains another breaks every substring reader of the report — an operator's `grep held`, and any
test asserting `held` is absent (CLAUDE.md, the finding-kind superstring bullet). Both landers now
carry a test asserting no reported status is a substring of another. The **field** keeps
`withheld`: it is a pinned cross-boundary name, matched whole, which nothing reads by substring.

### Where it lives

One module, `services/branch_update_serialization.py`, called by both acts (as a conjunct after
`branch_update_qualifies` and the head checks, immediately before `update_branch`, inside the act's
transaction and under its per-repository advisory lock) and by both admission routes. Neither
admission module imports it, so composing a sibling's answer can never recurse into composing that
sibling's siblings. The act always works the rule out again and never trusts the served copy.

## Why it costs no landing latency

Landings are already serialized per repository: the estate lane lands one per repository per
window occurrence (`_pace_term`), and the inert lane requires a current head, so after one landing
every sibling is behind for the rest of that pass. Freshening anything other than the next to land
was always wasted — it would be staled again before its turn. The queue drains at the same rate with
fewer builds.

**The one case where it costs time:** the edited branch at the front fails its checks. It then
releases, and the sibling behind it is freshened on the first pass after that is visible rather than
in advance — one hourly pass later in the inert lane, usually nothing in the estate lane, where pace
holds any landing to the next window anyway.

## Alternatives rejected

- **Stop freshening Dependabot's branches** (the 2026-09-20 decision). Rejected by measurement:
  Dependabot does not rebase a merely-behind branch (0 of 51), so it would wait indefinitely.
- **Strict: any edited open sibling holds.** `change-manager#93` would freeze every future first
  edit in that repository until someone recreated it, and a red branch would freeze its repository.
  Neither clears on its own, so the waiting siblings could not honestly be called non-findings.
- **Holding means `mergeable_state != dirty`.** Handles `#93`, not a red branch, and is a second
  hand-written definition of "landable" beside the cascade.
- **Only siblings sharing a changed file hold (G\*).** Replayed at 22 of 72 events withheld against
  29 of 72 for the strict form of this rule; both catch 4 of 4. Its extra precision buys nothing in
  steady state (any landing stales every sibling) and costs a files read per pull request.
- **Withhold any update while an older sibling is queued.** No ownership reads, and it closes queue
  jumping by construction — but it forbids re-freshening an already-edited branch (which day one
  depends on), adds a stall behind an older owned branch with abandoned runs, and makes the
  orchestrator re-derive each lander's order.
- **This rule plus an "older sibling about to land" clause.** Not adopted: queue jumping has 0
  observed occurrences and the clause brings the order re-derivation and a restricted stall with it.
  **Trigger to revisit:** the first observed instance of the full queue-jumping path.

29 of 72 is a strict **upper** bound, not the rule's cost: the replay has no holding gate, because
the check outcomes over time are not in the data.

## Corrections the code made against the spec

The code wins in each case.

1. **The holding and read-failure sets are not registered in `VOCABULARY_REGISTRY`**, though the
   spec said to. The scanner only discovers collections of literal strings; house style builds
   refusal sets from imported names, which it does not see, and CLAUDE.md lists such derived sets as
   structural exclusions. The completeness test pins the vocabulary instead.
2. **A new `SiblingReadGateway(EstateReadGateway, Protocol)`**, not new methods on
   `EstateReadGateway` — the unit-bound landing path's fakes would have grown methods nothing calls.
   It and the two read shapes (`OpenPullRequest`, `PullRequestCommit`) live in
   `estate_landing_admission.py`, **not** the new module, because putting them there is an import
   cycle (`estate_pr_merge` → new module → `inert_landing_admission` → `estate_pr_merge`).
3. **The served field cannot be computed where `rollout_base_matches_pin` is** — inside the
   admission module, which the rule may not live in. It is a dataclass field with no default, set
   `False` at each admission function's single constructor and filled by the route with
   `dataclasses.replace`, so a new constructor cannot forget it silently.
4. **The inert act gains `clock: Clock | None = None`**, used only by the ten-minute bound, so the
   bound is testable. The routes pass nothing.
5. **Pagination needed its own page loop**, because the existing `_get` drops headers and reads a
   404 as absent; a 404 on a list read raises. **The commits endpoint returns at most 250 commits
   however it is paged**, so a list at 250 is not believed complete and reads as unread.
6. **The two branch-update action strings moved into the new module** (`BRANCH_UPDATE_ACTION`,
   `INERT_BRANCH_UPDATE_ACTION`), since it reads them from the event log and the act modules import
   it; each act re-imports its own under the same name.
7. **`landing_rollout_moved` holds only beside `landing_head_not_current_with_base`**, through
   `freshness_derived_refusals` — when the base carries the pinned bytes and the head is behind. A
   genuinely moved rollout (the pull request's own diff edits it) releases.

And four made while building, which the plan did not settle:

8. **One composer per lane**, `estate_sibling_composer` and `inert_sibling_composer`, called by both
   the act and the route, so the served fact and the act ask about a sibling in exactly the same
   words.
9. **Each lander gained a `_wants_update` helper** — qualifies and not withheld — so its update pass
   and any dry run report `would-update` only for what a live pass would act on.
10. **`withheld` became `waiting`** (above).
11. **The inert lander's trailing `return "held"` was removed as unreachable**: past the exception
    branch, the unexplained set can only have emptied because the sibling key subtracted the
    freshness refusal from a set containing nothing else, so the re-test always held. It is a single
    `return "waiting"`.

## Known residuals

- **A holding branch whose checks never finish stalls its repository** until a person acts:
  `landing_checks_awaiting_verdict` on a current head (runs cancelled, e.g. by the Actions quota,
  and nothing re-runs them), a run in flight that never gets a runner or approval, or
  `landing_mergeability_unknown` that never resolves. All three stay in the holding set because the
  window just after an update needs them. It is reported once, as `held` on the holding branch's own
  line; the siblings behind it read `waiting` and do not multiply it. No test may assert that every
  holding member clears on its own.
- **Queue jumping** (0 observed). The lane freshens X while an older sibling Y is conflicted by the
  landing just made; Dependabot rebases Y, which stays Dependabot's and lands first at the next
  window; if Y conflicts X, X is stuck and needs a user's recreate. Serialization makes the first
  step more common. Declined with a trigger, above.
- **Any other landing conflicts the one edited branch** — a person's merge, a sync-bot landing, a
  non-Dependabot commit to the default branch. Exposure drops from every edited sibling to one
  branch per repository; that branch is not made safe.
- **A lost response on the update call.** A timeout after GitHub accepted the 202 rolls back, so no
  event is written, and arm (a) sees the commit only about 12 seconds later — inside the 3–4-second
  spacing of the next sibling's act. Writing the event before the call would close it and is
  rejected: `_record` deliberately prefers a lost record of a real act over a record of an act that
  then failed.
- **Two edited branches after the queue moves past a red one.** If a person later makes the first
  landable again, the two can deadlock. It takes human action on a branch already reported nightly.
- **Dependabot's 30-day cutoff.** A waiting sibling over 30 days old that a landing conflicts will
  not be rebased and sits conflicted — a finding on its own line.
- **`[dependabot skip]` is untested.** Documented to let Dependabot force-push over a commit
  carrying it, which would dissolve the ownership problem. Update-branch gives no control over the
  message, and the endpoint that does is a different write act. A follow-up probe on a public
  disposable repository; not part of this design.
- **GitHub's "Update branch → rebase" button** probably produces commits indistinguishable from
  Dependabot's (author kept, committer `web-flow`, signed), so a person's server-side rebase would
  read as owned. Not measured. Probe: use it on a disposable repository's Dependabot pull request,
  read the commits, ask `@dependabot rebase`. It cannot fail worse than the lane before this rule.
- **Scan cost grows with queue depth**: about 12 extra calls per act at five open Dependabot pull
  requests, run under the advisory lock with the transaction open; cache per pass if queues grow.
- **The rule has not yet run in production.** Deploy is the plan's Task 13.

## Consequences

- **The day-one population is not recovered by this rule, and needs a user's
  `@dependabot recreate`.** As of 2026-09-25T15:11Z: `brain#73`, `#74`, `#75` are all already edited
  (the lane owns three branches in `brain` at once), and `change-manager#93` is edited and
  conflicted. The rule does not withhold any of them — outcome 1 — and does not clean them up; it
  stops more being created. `#93` does not hold (conflicted releases), so it blocks nothing, but it
  stays stuck until a user recreates it. A recreate on `brain#74`, `#75` and `change-manager#93`
  returns them to Dependabot, after which `brain#73` is the only edited branch there and the steady
  state begins. The App cannot post that command.
- A queued Dependabot sibling reads `waiting`, not `held`, on every pass until its turn. The estate
  lane's nightly exit code was already 3 on any night with a queued sibling; what changes is how many
  finding lines a queue contributes.
- `…_siblings_unreadable` is a finding every pass until the reads succeed.
- No migration, no new write act, credential or permission. The rule only ever does less than the
  lane did before it.

## Evidence

`~/docs/software-delivery-system/2026-09-25-dependabot-freshening-evidence.md` holds the summary
tables, copied verbatim from the two measurement directories
(`$TMPDIR/dependabot-rebase-measure/`, `$TMPDIR/deadlock-precondition/`). **Those directories were
ephemeral and the raw data behind them (`data.json`, `gql.json`, `rows.json`, `files.json`, the
scripts) was not persisted.** The 580 → 1 and 51 → 0 figures are quoted from the spec's §2; the
~20-day maximum, the 42/72/35/4 census, the pair timestamps, the replay (29 and 22 of 72), and the
68-of-68 owned probe are in that file.
