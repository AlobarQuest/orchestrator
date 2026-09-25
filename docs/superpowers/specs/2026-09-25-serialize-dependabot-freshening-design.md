# The lane edits one Dependabot branch per repository at a time

**Status:** draft for review, 2026-09-25. **Repos:** `AlobarQuest/orchestrator`. **Records as:**
ADR-0045 (the number is free on `main`; the superseded draft never merged an ADR).

**Supersedes:** `docs/superpowers/specs/2026-09-20-dependabot-branch-freshening-design.md` and its
plan `docs/superpowers/plans/2026-09-20-dependabot-branch-freshening.md`, both on `main` since #286.
Their probe findings (§1–§2 of that spec) stand and are restated below. Their decision — delete the
estate lane's branch-update act and withhold the inert lane's for `dependabot[bot]` — is replaced.
Those two files are not edited; this document is where the supersession is recorded.

**Format note.** File and symbol references were read on `origin/main` at `6df1d09`. Read the code,
not these references, once the tree moves. Every claim below is labelled **measured** (with the file
or command it came from) or **inferred**. The data directories are
`$TMPDIR/dependabot-rebase-measure/` and `$TMPDIR/deadlock-precondition/`.

## 1. What stays from the superseded spec

These were measured on 2026-09-20 and nothing since contradicts them.

- **The update-branch call merges the base into the head under the App's identity**, and Dependabot
  then disowns the branch: asked to rebase, it answers that the PR "has been edited by someone other
  than Dependabot" and offers only `@dependabot recreate`.
- **The deadlock:** an edited branch is conflicted by a competing landing; Dependabot will not
  rebase it because it was edited; the lane will not freshen it because `qualifies_for_branch_update`
  refuses a conflicted head. Neither party can act.
- **The App cannot recover it.** `@dependabot recreate` posted as `alobar-sds-dispatch[bot]` gets
  201 and then Dependabot's reply "only users with push access can use that command". The same
  command from a user identity recreated both stuck pull requests in place (same number,
  `DIRTY` → `CLEAN`). No App-driven recovery exists, and this design does not try to build one.
- **No PAT in the landing path**, for the reason that spec gave: the acting identity is also the
  recorded attribution.

## 2. What the two new measurements say

### Dependabot does not maintain a merely-behind branch (measured, `dependabot-rebase-measure/`)

- 580 pushes to default branches that did not touch a dependency file produced **1** Dependabot
  rebase. Across 51 weekly Dependabot runs where an open, non-conflicted pull request under 30 days
  old was behind its base, Dependabot rebased **0** of them. Stretches behind reach about 20 days
  (`stretch.json`, `table.txt`).
- Dependabot **does** push to a branch it owns: on conflict, about 2 minutes after the conflicting
  push; when a newer version is published; on an `@dependabot` request from a user; and in 43 of 90
  cases after a push that touched the same dependency file.
- Documentation (`mng.md`): Dependabot stops rebasing once someone else has pushed commits to the
  branch, and stops rebasing a pull request left unmerged for 30 days.

**Consequence:** the lane cannot hand freshening back to Dependabot. The superseded spec's §7 said
it did not know how long landing would be delayed by that. It is measured now: indefinitely, for a
branch nothing conflicts. That is why the lane keeps freshening.

### The deadlock has one precondition, and the lane creates it (measured, `deadlock-precondition/`)

- **42** pull requests were freshened by the App (brain 19, change-manager 14, factory-runner 3,
  orchestrator 6), across **72** update events (`table.txt`, `rows.json`). 35 landed clean. 4 got
  stuck: `brain#70` and `change-manager#87` (recreated by a user, then landed by the lane),
  `factory-runner#71` (closed by a user), and **`change-manager#93`, stuck now**.
- **In all four, the conflict came from a sibling Dependabot pull request that the lane itself
  landed.** Each pair shared `pyproject.toml`/`uv.lock` or `requirements-dev.txt`, with changed lines
  next to each other. **In 3 of the 4, the lane freshened both members of the pair seconds apart in
  one pass and then landed one of them.**
- Dependabot never pushed to an App-edited branch except when a user asked for a recreate. It
  refused 3 rebases and 2 newer-version updates, each time saying the branch had been "edited by
  someone other than Dependabot".
- In the 4 observed cases where a landing conflicted a sibling that had **not** been edited,
  Dependabot rebased that sibling within about 2 minutes.

**So the precondition is precise:** *two branches in one repository are both not owned by
Dependabot, and the lane lands one of them.* An edited branch next to Dependabot-owned siblings is
safe, because a sibling that gets conflicted is rebased by Dependabot. The defect is the lane owning
more than one branch in a repository at the same moment.

## 3. The decision

Devon chose to hold the superseded design and keep freshening, narrowed to the deadlock's
precondition. HQ then refined it with the data above:

> **The lane never makes a Dependabot-owned branch edited while another open Dependabot pull request
> in the same repository is edited and still queued to land.** A branch that is already edited may
> always be freshened again.

In practice there is at most one lane-edited, landable Dependabot pull request per repository: the
one the lane will land next. Every sibling stays owned by Dependabot, and so Dependabot rebases it
when a landing conflicts it.

## 4. Why this costs no landing latency (inferred from the code, and it is the argument for doing it)

Landings are already serialized per repository:

- **Estate lane:** one landing per repository per occurrence of the window (`_pace_term`).
- **Inert lane:** the head must be current with its base, so once one pull request lands, every
  sibling is behind for the rest of that pass. The inert admission module's own docstring says this
  ("at most one pull request per repository is landable per pass").

Freshening any branch other than the next to land was therefore always wasted. Take a night where A
lands and the lane freshens B and C. The next night B lands and C is stale again, so the lane
freshens it a second time, and it lands the night after. Under serialization C is freshened once, on
the second night, and still lands on the third. The queue drains at the same rate with fewer builds.

**The one case where it does cost time** is when the edited pull request at the front of the queue
fails its checks. The sibling behind it is freshened on the first pass after the failure is visible
(§6), not in advance. In the inert lane, which runs every hour, that is one pass later than today.
In the estate lane it usually costs nothing (inferred): the failure is normally visible by the next
hourly pass in the same window, and pace holds any landing to the next window anyway.

## 5. The rule, precisely

### Definitions

- **Dependabot pull request:** open, and authored by `dependabot[bot]` with `author_is_bot`. This is
  the same test `_remote_terms` already applies (`UPDATE_BOT_LOGIN`).
- **Edited:** at least one of these holds.
  - **(a)** The pull request carries a commit whose author login is not `dependabot[bot]`. **A
    commit with no linked author, or a commit list that cannot be read, is resolved in whichever
    direction withholds.** On the *target* it counts as owned, so the rule may still withhold. On a
    *sibling* it counts as edited, so the sibling may hold. The same classification applied to both
    would fail open on the target: it would read as already edited, never be withheld, and become a
    second edited branch. Measured, every commit in 68 Dependabot pull requests carried a linked
    author, so this matters only in theory, but the rule must not depend on that.
  - **(b)** The orchestrator's event log holds a branch update for this pull request
    (`estate_pr_branch_update.updated` or `inert_pr_branch_update.updated`, matching `repository`
    and `pr_number` in the payload) whose recorded `head_sha` equals the pull request's **current**
    head.
- **Dependabot-owned:** a Dependabot pull request that is not edited.
- **Holding:** a Dependabot pull request that is edited and is still queued to land (§6).

### The rule

At the branch-update act, after `qualifies_for_branch_update` has said yes, withhold the update when
**all** of these hold:

1. the target is a Dependabot pull request;
2. the target is Dependabot-owned (updating it would be its first edit);
3. some *other* Dependabot pull request in the same repository is holding.

A target that is already edited is never withheld by this rule. It has already lost Dependabot's
ownership, so updating it again costs nothing. That is also what keeps day one workable (§10).

### Where it lives: at the two acts, never in the shared predicate

`qualifies_for_branch_update` is **not** changed. ADR-0024 made it one definition read by two
consumers, the acting lane and the reporting agent, and changing it would move the reporting
consumer's semantics as a side effect. The new check is a separate conjunct in
`update_estate_pull_request_branch` and `update_inert_pull_request_branch`. Each act checks it inside
its own transaction, under the per-repository advisory lock it already takes
(`estate_pr_branch_update:{repository}`, which both lanes share), and refuses with a new code:

- `estate_branch_update_sibling_holding`
- `inert_branch_update_sibling_holding`

The same fact is also served on both admission responses (§9). The act always works it out again
and never trusts a caller's copy, which is how the `branch_update_qualifies` flag is handled today.

It is implemented as **one function**, called by both routes and both acts. It is parameterized by
which lane's admission it composes when testing whether a sibling is holding. Keep it outside
`estate_landing_admission` and `inert_landing_admission`, so that composing a sibling's answer can
never recurse into composing that sibling's siblings.

### Why ownership comes from GitHub and the event log together, and why neither alone works

**GitHub alone misses the edit the lane has just made.** Update-branch answers 202 and does the work
afterwards (CLAUDE.md, measured 2026-08-14). The lander asks about every subject in one pass, seconds
apart. So a read of the first sibling made right after it was updated can still show only
Dependabot's commits, and the second sibling gets updated too. That is the exact 3-of-4 pattern in
§2. Arm (b) closes the gap. The event is written in the same transaction as the call and under the
same repository lock, so the next act in the pass sees it. Its recorded head is the head *before* the
update, and while the platform has not yet delivered the update, that is still the current head.

**The event log alone never forgets.** A user's `@dependabot recreate` puts the branch back in
Dependabot's hands with a new head (measured on `brain#70` and `change-manager#87`). An event-only
test would call it edited forever and block the repository until the pull request closed. Keying arm
(b) on the *current* head retires the event as soon as the head moves. Once the update is delivered,
arm (a) takes over.

**Human edits count, and they need no special case.** Dependabot disowns a branch whoever pushed to
it. AlobarQuest's update commits on 20 pull requests (per the handoff) disowned those branches just
as surely as the App's did. Arm (a) catches them because it reads authors, not identities the
orchestrator knows about.

**Cross-check (measured, `deadlock-precondition/owned_probe.txt`, 2026-09-25):** across 68
Dependabot pull requests in five repositories, `commits == 1` agreed with "every commit authored by
`dependabot[bot]`" in 68 of 68 cases (32 owned, 36 edited). The count would save no calls, since
either read is one request per pull request. It is also a proxy whose wrong answer on the *target*
fails open: a multi-commit branch that Dependabot still owns would read as already edited and get
updated. So the spec reads authors.

### What it reads, and what that costs

For each act (and each admission read, §9), and only when the target qualifies and is a Dependabot
pull request:

- `GET /repos/{r}/pulls?state=open`: 1 call. It returns each pull request's author and head.
- `GET /repos/{r}/pulls/{n}/commits` for the target and for each other open Dependabot pull request:
  1 call each.
- One event-table query.
- For each sibling that is edited, one admission composition to test whether it is holding. That is
  the ~4–6 reads the existing cascade already makes. After day one there is normally at most one such
  sibling.

With the open queues this estate has had, at most 4–5 Dependabot pull requests per repository, that
comes to roughly 10 calls. Both gateways gain two read methods, `open_pull_requests` and
`pull_request_commit_authors`, on `EstateReadGateway`. Every fake gateway must implement them.

**Failure direction.** If any of these reads fails, the act withholds. A repository whose siblings
cannot be read is one where "no other branch is edited" was not established.

## 6. When the edited branch does not land

The rule counts a sibling only while it is **holding**: edited, open, and its own composed admission
names no refusal outside this set:

```
DELIBERATE_REFUSALS                         (pace, window)
∪ freshness_derived_refusals(...)          (behind; rollout pin that differs because behind)
∪ { landing_checks_in_flight,
    landing_checks_awaiting_verdict,
    landing_mergeability_unknown }
```

Every member but one clears without anyone acting. The pace and window reset on a clock. Being
behind is cleared by the lane itself, because the holding branch is already edited and may always be
freshened again. A run in flight finishes. The last three members are what an edited branch reports
in the minutes after an update. Without them, the branch the lane has just freshened would stop
counting as holding in the very pass that freshened it, and the sibling behind it would be edited
too. That would recreate the defect.

**The exception is `landing_checks_awaiting_verdict` on a head that is current.** `checks_term`
raises it when runs were cancelled or never happened. The shared predicate excuses it only beside
`behind`, and nothing in the estate re-runs an abandoned check (CLAUDE.md). So an edited sibling
whose freshened head had its runs cancelled, for example by the Actions quota (seen 2026-08-17 and
2026-08-22), holds the repository until a person re-runs them. It stays in the set anyway, and that
is deliberate. It is needed for the window just after an update. Whether GitHub reports that window
as `landing_mergeability_unknown` (which `_remote_terms` checks first, before `checks_term` runs)
or as `awaiting_verdict` was not measured and cannot be read from the data. **Residual:** the stall
is visible as a single finding, because `awaiting_verdict` is in no lander set, so the holding
branch's own line reads `held`. That is one finding, not one per withheld sibling. A build session
must not write a test asserting that every member clears on its own.

A sibling with any other refusal does **not** hold, so the queue moves past it. The cases that
matter:

- **Red checks** (`landing_checks_not_clean`). Dependabot will not replace a branch it has disowned
  (it refused newer-version updates twice in the data), so a red edited branch stays red until a
  person acts. Holding siblings behind it would stall the repository behind a condition that does not
  clear on its own. It is already a finding on its own line.
- **Conflicted** (`landing_pull_request_conflicted`). This is the deadlock itself. It needs a user's
  `@dependabot recreate`. `change-manager#93` is in this state today.
- **Any refusal this spec does not list**, including read failures inside the sibling's own
  composition. The polarity here is deliberate: releasing is exactly the pre-change behaviour, so the
  rule can only withhold where it positively recognizes a queue. It can never withhold more than
  today's lane on a code nobody classified.

If composing a sibling's admission raises rather than answering, the act withholds (the §5 read
rule).

**Residual, stated plainly.** After the queue has moved past a red or conflicted edited branch, a
second edited branch exists. If a person later makes the first one landable (for example a re-run
turns its checks green), the two can deadlock. That takes human action on a branch that was already
a nightly finding, and it is no worse than today.

### Alternatives considered and rejected

- **Strict: any edited open sibling holds, whatever its state.** Rejected. `change-manager#93` would
  freeze every future first edit in that repository until someone recreated it, and a red branch
  would freeze its repository until someone closed it. Neither clears on its own, so the withheld
  siblings could not honestly be reported as deliberate (§9). It also turns one repository's defect
  into a stall of everything behind it.
- **Holding means `mergeable_state != dirty` and nothing more.** Rejected. It handles `#93` but not
  a red branch. It would also be a second, hand-written definition of "landable" beside the cascade.
- **Only siblings that share a changed file hold** (HQ's G*). HQ reported that G* catches 4 of 4 and
  withholds 21 of 62. The script behind that figure is not in the data directory, so I did not
  reproduce it. My own replay of the same condition gives 22 of 72 over all events, against 29 of 72
  for the rule chosen here (`replay_serialize.py`/`.out`). Both catch 4 of 4. G* was rejected because
  its extra precision buys nothing in steady state (§4: any landing stales every sibling, so
  freshening a non-sharing sibling early is wasted too). It also costs a files read per pull request,
  and in the repositories where the deadlock happens every bump touches the same lockfile. Its only
  benefit is liveness behind a red branch, and the holding gate above already provides that for every
  sibling, not just the ones that don't share a file.

**About the replays.** Both are replays over the *historical* event stream, not simulations. Under
either rule the landing sequence itself would have been different. The replay cannot model the
holding gate, because check outcomes over time are not in the data. What it shows is that the rule
is never *less* protective than it needs to be on the four stuck cases.

## 7. The inert lane, and the second author

Deploy policy v8 declares two authors for the inert lane: `dependabot[bot]` and
`octo-upstream-sync[bot]` (`claude-octopus`, `rtk`). The sync bot rebuilds its rolling branch every
day with `checkout -B` and a force-push. An edit does not disown it, and the next rebuild overwrites
the edit anyway.

- **A sync-bot pull request is never withheld.** Rule condition 1 is false for it.
- **A sync-bot pull request never counts as a holding sibling.** The rule's premise is Dependabot
  refusing to maintain a branch, and the sync bot refuses nothing.

The one interaction left over: a sync-bot landing can conflict the single edited Dependabot branch.
That belongs to the general residual in §13 ("any other landing"), not to this rule.

The author is available at the act without adding anything to `_RemoteTerms`. The sibling scan's
list call already carries every pull request's author, including the target's.

## 8. Which pull request gets freshened first (inferred from the code)

The orchestrator does not choose; the lander's order does.

- **Estate lander:** `_subjects` sorts by `(target_repository, record id)`. The landing pass then the
  update pass walk that list in that order (`run`: `_pass` then `_branch_updates`). So the first
  qualifying record in record-id order gets the grant. The sibling scan refuses the rest in that
  pass, through arm (b). **Effectively: oldest change record first.**
- **Inert lander:** `_subjects` sorts repositories and then pull request numbers. **Oldest pull
  request first.**

These are also the orders the landing pass lands in. So the branch freshened is the branch that lands
next, with one exception, which is a residual (§13, "queue jumping").

No ordering code changes. This spec deliberately does not make the orchestrator re-derive the
lander's order.

## 9. Reporting

### What the withheld sibling looks like today and without a change

Both landers classify each landing-pass line with `_held_status` (estate) or `_unsatisfied_status`
(inert). There, `landing_head_not_current_with_base` on its own is **held**, which is a finding.

- Estate lane today: siblings staled by a landing read `held (pace, behind)` for one pass, get
  freshened, and read `deliberate (pace)` from then on.
- Under serialization with no reporting change: every queued sibling reads `held` on all four
  passes of each night until its turn, instead of on one.

**The nightly exit code does not change either way.** Today the estate lane already exits 3 on
every night that has a queued sibling. The pass right after a landing reports those siblings `held`
(measured: `4 held` on the 06:15Z pass in `~/Library/Logs/estate-landing.log`), and during any drain
the exit code already cannot say anything new. The dead-man switch is unaffected in both cases,
because the finding code pings success (CLAUDE.md). What serialization changes is how many `held`
lines each queued sibling contributes per night: about four instead of one.

**So "no reporting change" is a viable option, not a dismissed one.** It means the same nightly
exit code, three more `held` lines per sibling per night, and no new field, pin, or lander edit. The
served fact below is recommended for honesty line by line, since a sibling that is waiting its turn
is not a condition anyone needs to act on. It is a cheap choice, and HQ can reverse it before
building.

### Is the withholding deliberate? Only under the holding gate, and that is what earns it

Devon's ruling is that a **deliberate** refusal must clear on its own. Under §6's gate, a sibling is
withheld only while the holding branch's own condition lasts. For every member of §6's set except an
abandoned check on a current head, that condition clears on its own. In the estate lane the branch
lands at the next window by pace; in the inert lane it lands on the next pass. In the one exception,
the stall is still reported, on the holding branch's own `held` line (§6). So the withholding clears
on its own everywhere except in that one named case, which is visible. Under the rejected strict rule it would not (`#93`), and there the
withheld line would have to stay a finding. **The gate and the reporting are one decision.**

### The mechanism: a served fact, the way `rollout_base_matches_pin` is served

- **Both admission responses** (`EstateLandingAdmissionResponse` and `InertLandingAdmissionResponse` in
  `api/schemas.py`) gain `branch_update_withheld_for_sibling: bool`. It is true only when every rule
  condition holds *and* the target qualifies. So it can never co-occur with a failing check, which
  disqualifies. It is a fact about an observed holding sibling. It is **not** a record of "the lane
  declined", because keying on the declining is the fail-open that CLAUDE.md's durability ruling
  names. The schema docstring must say so, and the response-model field-set pins in both schema tests
  must be updated in the same change.
- **Estate lander `_held_status`:** when the key is present and `True`, remove freshness-derived
  refusals from the unexplained set, just as it does beside an exception today. A missing or false
  key removes nothing, so the line stays a finding. That is the right direction when the
  orchestrator in production is older than the lander. A mirror constant and a pin test hold the key
  name equal across the isolation boundary, the way `_BASE_MATCHES_PIN` is held.
- **Inert lander `_unsatisfied_status`: unchanged.** That lane records its design as having *no*
  deliberate refusal ("this lane has no clock"). The inert queue drains one pull request per hourly
  pass, so a queued sibling reads `held` for one or two passes. Adding the lane's first suppression to
  save two hourly lines is not worth the vocabulary it adds. This is a judgment call; it can be
  revisited if the inert queues turn out to be long.
- **Both landers' `_branch_updates`:** act only when `branch_update_qualifies` is true **and**
  `branch_update_withheld_for_sibling` is not true. A withheld subject prints no update line, since
  its landing line has already said why. As a result a dry run reports `would-update` only for what a
  live pass would actually update, which is more accurate than today. Each lander's
  `_UPDATE_SELF_CLEARING` also gains its lane's `…_sibling_holding` code, for the race where the
  served answer and the act disagree.

This gives the new field three readers: two update passes and one classifier. It is not a dead knob.

## 10. Day-one population (measured, read-only `gh api`, 2026-09-25T15:11Z)

The open pull requests across the ten repositories checked (`brain`, `change-manager`,
`orchestrator`, `factory-runner`, `intent-packages`, `security-standards`, `project-standards`,
`infraops-mcp-server`, `claude-octopus`, `rtk`) are exactly these four:

| PR | state | commits (author) | files | reads under the rule |
|---|---|---|---|---|
| `brain#73` | open, `clean` | dependabot 09-24 08:17; **App 09-25 07:15** | `requirements.txt` | edited |
| `brain#74` | open, `clean` | dependabot 09-24 07:24; App 09-24 08:15; App 09-25 06:15 | `requirements.txt` | edited |
| `brain#75` | open, `clean` | dependabot 09-24 07:24; App 09-24 08:15; App 09-25 06:15 | `requirements.txt` | edited |
| `change-manager#93` | open, **`dirty`** | dependabot 09-22 05:45; App 09-22 06:15; App 09-23 06:15 | `pyproject.toml`, `uv.lock` | edited, conflicted |

The last estate-landing log passes show `brain#73/#74/#75` as `deliberate (landing_pace_exhausted)`
and `change-manager#93` as `held (landing_pull_request_conflicted, landing_head_not_current_with_base)`.

**What the rule does on day one:**

- **brain.** All three branches are already edited, so the rule never withholds an update to any of
  them. Condition 2 is false for each, and they keep being freshened as today. The rule does not
  clean up this pre-existing state, where the lane owns three branches at once. The rule is about not
  creating more edited branches, not about recovering existing ones. Tonight's window lands one of
  them. All three change `requirements.txt`, so the landing may conflict one or both of the others.
  A conflicted one is stuck and needs a user's `@dependabot recreate`, exactly as today. **Exposure
  that exists before deploy and is not closed by it.** Any *new* Dependabot pull request in `brain`
  is withheld from its first edit while one of the three is holding. Once they drain, the rule is in
  its steady state.
- **change-manager.** `#93` is edited but does not hold, because it is conflicted. So it does not
  block future siblings. It is not recovered by this design. **It needs a user's
  `@dependabot recreate`** (§1: the App's request is refused). After a recreate it is
  Dependabot-owned again, and the lane lands it in the usual way.
- **Every other repository** has no open Dependabot pull request, so the rule has nothing to act on.

## 10a. Is the exception case live? (measured)

The estate lander's `_EXCEPTION` is `{landing_update_type_unparseable}`, and `_bump_term` only raises
that under a policy version that decides by update type, which means versions before the fifth. Under
v8 the outcome rule decides. `brain#75`, a requirement-range bump, reports only
`landing_pace_exhausted`, which confirms it. So on current policy no edited branch can be held up by
an exception, and this spec does not design for that case. If the lane ever runs against an older
policy version, an unparseable branch would not hold (it is outside §6's set). The queue would move
past it, and it would stay an exception on its own line.

## 11. What must be proven

**Tests (unit level, fake gateways):**

1. **The discriminating control.** A target that qualifies, is Dependabot-owned, and has one holding
   sibling (edited by arm (a), refusals `{pace, behind}`) is **withheld** at the act. The gateway's
   `update_branch` is never called, and the same fixture with the sibling owned by Dependabot is
   **updated**. This pair of cases only has different answers if the sibling rule is in effect. It is
   the control that fails if the rule is deleted.
2. **Re-freshening an edited target is never withheld**, including when a holding sibling exists.
   This is the day-one guarantee.
3. **Arm (b) alone.** A sibling whose commits are all Dependabot's, but whose current head equals an
   update event's recorded head, is edited. The same sibling whose head has moved (a recreate) is
   owned. This covers the 202 race and the recreate case separately.
4. **Arm (a) catches a human author.** A commit by `AlobarQuest` means edited. **A commit with no
   linked author, or an unreadable commit list, is split by polarity:** the target counts as owned,
   so it is withheld when a holding sibling exists, and a sibling counts as edited, so it holds. Each
   direction needs its own control. A single classification passes one of the two and fails open on
   the other.
5. **The holding gate, row by row.** For each member of §6's set, the sibling holds. For
   `landing_checks_not_clean`, `landing_pull_request_conflicted`, and a made-up code, it does not.
   A composition that raises means withhold.
6. **Inert:** a sync-bot target is never withheld, and a sync-bot edited sibling never holds. A
   Dependabot target beside a holding Dependabot sibling is withheld.
7. **Read failures withhold:** the list call fails, a commits call fails, or the event query fails.
8. **Served fact:** `branch_update_withheld_for_sibling` is true exactly when the act would refuse on
   this rule, and false whenever `branch_update_qualifies` is false. The field-set pins on both
   response models are updated.
9. **Lander:** `_held_status` suppresses freshness only when the key is `True`. The same answer with
   the key absent, or `False`, stays held. `{behind, checks_not_clean}` stays held with the key
   `True`. That last case cannot be produced by the orchestrator, so it guards the lander on its own.
   The mirror constant is pinned to the orchestrator's field name.
10. **Add-a-term hygiene.** Every existing "qualifies → acts" fixture in
    `tests/services/test_estate_pr_branch_update.py`, `tests/services/test_inert_pr_branch_update.py`,
    and both landers' suites (`tests/estate_lander/`, `tests/inert_lander/`) must set up a repository with **no** edited sibling. Without that, the new conjunct is
    true by accident and those controls stop testing the shared predicate. This is the
    add-a-term-to-a-conjunction rule in CLAUDE.md.

**Mutation controls, with every survivor reported:** delete condition 1, 2, or 3; flip arm (a)'s
login test; delete arm (b), or key it on "any event" instead of the current head; drop each member of
§6's set in turn; make read failures release instead of withhold; unconditionally subtract in
`_held_status`; key the served fact on `qualifies` alone. Restore from git after each mutation, set
`PYTHONDONTWRITEBYTECODE=1`, and read the kills rather than the count.

**Whole-repo gates a per-task loop will not see:** `test_unreachable_guards` (the new function needs
a production caller, meaning the routes and acts), the ws32/ws33 word guards (a docstring about this
must not use the bare words `dispatch`, `deploy`, or `merges`), the parametrized invariant-scan cases
a new module adds, and the idempotency matrix if any route changes.

## 12. Deploy ordering

Build, then swap, then verify. There is **no migration**: the rule reads the existing `events` table
and adds no column. Verification reads `openapi.json` and asserts that
`branch_update_withheld_for_sibling` is present on **both** admission response schemas, since health,
digest, and revision label cannot see a change to the served surface.

**Either order is safe. Orchestrator first is preferred.**

- **New orchestrator, old lander.** The old lander ignores the new key. It calls the act for a
  withheld sibling, and the act refuses with `…_sibling_holding`, which the old lander's
  `_UPDATE_SELF_CLEARING` does not know. So it prints `held`, which is a finding. That is the safe
  direction, and it lasts only until the main tree pulls, which the activation helper does on the next
  scheduled pass.
- **Old orchestrator, new lander.** The key is absent, so the lander treats it as false and calls the
  act, and the old orchestrator freshens exactly as today. Nothing gets worse.

The landers run from the main tree's working copy, so merging is what deploys them (CLAUDE.md). No
`[project.scripts]` entry changes, so no `uv sync` is needed.

## 13. Residuals

- **Queue jumping** (inferred; measured 0 times, but its first step has been seen). The lane
  freshens X while an older sibling Y is conflicted by the landing that has just happened. Dependabot
  rebases Y about 2 minutes later (seen 4 times), so Y is fresh and still Dependabot-owned. At the
  next window Y is older, so it lands first, and if Y conflicts X, X is stuck. X then stops holding,
  because it is conflicted, and the queue moves on. X needs a user's recreate. The history contains
  no instance of this whole path, but under serialization more siblings stay owned by Dependabot and
  the first step becomes more common. It could be closed by withholding every first edit while an
  older owned sibling is conflicted. That was not specified: the orchestrator does not know the
  lander's order, and that closure stalls behind a conflicted sibling Dependabot never rebases (below).
- **Any other landing conflicts the one edited branch.** A person's merge, a sync-bot landing, or a
  non-Dependabot commit to the default branch can all do it. Serialization cuts the lane's exposure
  from "every edited sibling" to one branch per repository. It does not make that one branch safe.
- **Dependabot's 30-day cutoff.** A withheld sibling more than 30 days old that gets conflicted by a
  landing will not be rebased by Dependabot, so it sits conflicted. That is a finding on its own line,
  and it needs a recreate or a close. A queue drains one pull request per window per repository, so
  only an unusual backlog reaches this, but pull requests can be old for other reasons.
- **`[dependabot skip]` is untested.** The documentation says a commit carrying that marker in its
  message lets Dependabot force-push over it, which would dissolve the ownership problem completely.
  Update-branch gives no control over the commit message. The endpoint that does, the repository
  commit-message endpoint for combining a base into a branch, is a different write act. It has not
  been probed, and naming it in `src/` would trip the ws33 word guard. It is recorded as a follow-up
  probe, run on a public disposable repository per CLAUDE.md, and is not part of this design.
- **An abandoned check on the holding branch stalls its repository** until a person re-runs it. It
  is reported as one `held` finding on that branch's own line (§6).
- **The pre-existing edited population** in `brain` and `change-manager#93`, per §10.
- **Two edited branches after the queue moves past a red one**, per §6.

## 14. The `stop-freshening` branch: what to keep

Start fresh from `origin/main`. Keep nothing.

| commit | what it does | disposition |
|---|---|---|
| `d966cc2` | moves the shared predicate's tests out of the act's test file | **Optional; recommended skip.** Its reason was that the act's file was about to be deleted. Under this design the act stays, so moving tests for a surviving module is churn. |
| `38746ab` | deletes the estate branch-update act | **Superseded.** This design keeps and narrows the act. |
| `f66125f` | stops serving estate `branch_update_qualifies` | **Superseded.** The field keeps its reader, the estate lander's update pass. |
| `632a388` | inert: withholds the update for `dependabot[bot]` altogether | **Superseded.** It stops all Dependabot freshening, which §2 shows leaves a behind branch waiting indefinitely. Its diff is still useful as a map of where the author is in scope at the inert act. |
| `b2f90d5` | drops `LandingRefused.code` from the estate lander | **Superseded, and it must not be carried over.** This design reads `.code` again (`_UPDATE_SELF_CLEARING` gains `…_sibling_holding`). |

Tear down the `stop-freshening` worktree, its test database `orchestrator_test_stopfresh`, and the
branch, locally and on the remote, once this design is accepted. The remote branch has no pull
request, so `delete_branch_on_merge` will not remove it.

## 15. Open questions

- **None of this changes standing authority.** No new write act, credential, or permission is
  introduced. The rule only ever does less than today's lane.
- **For Devon, only if he wants it:** whether to probe `[dependabot skip]` through the
  commit-message-capable endpoint (§13). If that probe works, it would be a different and larger
  design: a new write act that relies on a third party's documented escape hatch. That is why it is
  presented as a fork rather than decided here.
