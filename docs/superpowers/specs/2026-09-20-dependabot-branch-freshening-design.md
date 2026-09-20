# The lane stops freshening Dependabot's branches

**Status:** accepted, 2026-09-20. **Repos:** `AlobarQuest/orchestrator`.
**Supersedes in part:** ADR-0019 Increment 6 (the branch-update act), for the estate lane only.

**Format note.** Every file:line below was measured on 2026-09-20 at `0213d9f`. Read the code; do
not trust these numbers after the tree moves.

## 1. The defect, and we built the first half of it

`_branch_updates` brings a pull request's head up to date when being behind is its only remaining
obstacle. It does that by calling GitHub's update-branch endpoint, which **merges the base into the
head**. The resulting commit is authored by the acting identity, not by Dependabot.

Dependabot then disowns the branch. Asked to rebase it, it answers:

> Looks like this PR has been edited by someone other than Dependabot. That means Dependabot can't
> rebase it - sorry! If you're happy for Dependabot to recreate it from scratch, overwriting any
> edits, you can request `@dependabot recreate`.

So the chain is:

1. A landing moves `main`, and every sibling pull request in that repository falls behind.
2. The lane freshens them. Each now carries a commit authored by `alobar-sds-dispatch[bot]`.
3. A competing bump lands and conflicts one of them.
4. Dependabot will not rebase it — we edited it.
5. The lane will not freshen it — `qualifies_for_branch_update` withholds the update for a
   conflicted head, correctly, because that call fails at the remote.

**Neither party can act, and the deadlock is permanent.** It is the shape this repository already
records — *when a refusal can be CAUSED by the condition another rule exists to clear, the two
rules deadlock* — one pair over from the case that rule was written for.

### Measured, not inferred

- **26 distinct pull requests** have been freshened, across **50 update events**. Every one of those
  branches became un-rebaseable by Dependabot at the moment it was touched.
- **24 landed anyway.** The mechanism only bites when a competing bump conflicts a branch before it
  lands.
- The **2 that did not** were `change-manager#87` and `brain#70`, both showing
  `author=alobar-sds-dispatch[bot]` on the head commit, both `DIRTY`, one stuck since 2026-09-17.
- **The control is clean.** `code-standards` is not in the landing lane, so its two open Dependabot
  pull requests have never been freshened. Both still show `author=dependabot[bot]`, and both are
  `CLEAN`.

## 2. The probe that killed the obvious fix

The obvious fix is to have the lane request the recovery itself. It cannot.

Posting `@dependabot recreate` as the orchestrator App succeeds at the HTTP layer — **201**, author
`alobar-sds-dispatch[bot]`, type `Bot`, so `pull_requests: write` does authorise the comment and no
permission grant is needed. Dependabot answered it in **three seconds**:

> Sorry, only users with push access can use that command.

The differential is clean and same-day: the identical command under a **user** identity was obeyed,
and both stuck pull requests recreated in place — same pull request number, `DIRTY` → `CLEAN`, heads
moving to `fc1fa205` and `54dc1c9d`. The App's comment produced no change in 600 seconds.

**This was not discoverable by reading permissions.** The App holds `contents: write` and
`pull_requests: write`, which is push access in GitHub's own permission model; Dependabot's check
evidently does not count an App installation as a user with push. Only asking settled it.

So an App-driven recovery does not exist, and a design resting on one would have recorded "asked"
every night while nothing happened.

## 3. The decision

**Stop creating the condition.** Do not edit a branch Dependabot owns; let Dependabot maintain it.

The cost is accepted and named: a behind-but-clean Dependabot pull request now waits for Dependabot
to freshen it rather than being freshened in seconds. The benefit is that it can never reach a state
only a human can leave.

## 4. What changes, and it is asymmetric

The two lanes have different populations, and the difference is structural rather than a matter of
configuration. **One rule would be wrong for one of them.**

### The estate lane: delete the act

`_remote_terms` appends `LANDING_AUTHOR_NOT_THE_UPDATE_BOT` unless
`pull.author_login == UPDATE_BOT_LOGIN and pull.author_is_bot`, where `UPDATE_BOT_LOGIN` is the
literal `"dependabot[bot]"`. That refusal is in none of the sets `qualifies_for_branch_update`
subtracts, so **a pull request that is not Dependabot's can never qualify for a branch update on
this lane**. Its entire freshening population is Dependabot, by construction.

So there is no narrowing to make. The act becomes provably dead, and dead is what makes deletion
safe rather than merely tidy: the route, the service module, the lander's pass, and the write
allowlist entry all go.

**Nothing attests it.** The wave-exit manifest, the evidence directory and the operations TOMLs
carry no reference to the branch-update act, so ADR-0040's rule — that a deletion can be bounded by
an attestation — does not bite here. That was checked rather than assumed.

### The inert lane: withhold for Dependabot only

Deploy policy v8 declares **two** permitted authors: `dependabot[bot]` and
`octo-upstream-sync[bot]`. The second is the fork-sync bot on `claude-octopus` and `rtk`, which
rebuilds its own rolling branch daily and has none of Dependabot's disowning behaviour. Freshening
its branch is harmless.

So this lane narrows rather than retires: carry an author-derived fact up from where the pull
request is already read, and withhold the branch update when the author is the update bot.

## 5. What deliberately does NOT change

**`qualifies_for_branch_update` is untouched.** An earlier draft of this design made it
author-aware. That is wrong, for a reason ADR-0024 already settled: it is one definition with two
readers — the acting lane and the reporting agent — and changing it moves the reporting consumer's
semantics as a side effect. The exclusion belongs at the call site of the lane that needs it.

It is also unnecessary. The author is not in scope at either call site: `_RemoteTerms` carries
`term`, `head_sha` and (estate) `rollout_base_matches_pin` / (inert) `merge_method`, and both lanes
call the predicate with `tuple(refusals)` plus one bool. Making it author-aware would mean threading
the author through two dataclasses and two functions to reach a predicate that does not need it.

**The reporting semantics are unchanged.** Being behind stays freshness-derived and stays a
non-finding. What changes is only whether the lane *acts*, so a behind Dependabot pull request stays
quiet and self-clears through Dependabot. `freshness_derived_refusals` keeps two readers after the
estate act is deleted — the inert lane's use of the predicate, and the reporting agent.

## 6. What must be proven, not asserted

- **The estate act is unreachable before it is deleted.** Show that no open or recent pull request
  could have qualified: the refusal is raised for every non-Dependabot author and is not subtracted.
  A deletion justified by "it looks dead" is the thing this repository's unreachable-guard exists to
  refuse.
- **The inert lane still freshens `octo-upstream-sync[bot]`.** A test that passes because nothing
  is freshened any more would be indistinguishable from the defect. Pin both directions: withheld
  for `dependabot[bot]`, granted for the sync bot.
- **The early-return paths default toward withholding.** `_remote_terms` returns from three sites in
  the inert lane, two of them before the author is known. An unknown author must withhold, in
  keeping with this lane's stated polarity that an unclassified value fails toward refusing.
- **Mutation controls** over the new withholding, with every survivor named.

## 7. Residuals

- **Recovery from an already-edited branch needs a user identity.** The App is refused. Of the 26
  branches historically freshened, the merged ones are moot and the two open ones were cleared by
  hand on 2026-09-20. Any future one — there should be none — needs a person.
- **`@dependabot recreate` regenerates in place**, keeping the pull request number, so a change
  record keyed on `(repository, pull_request_number)` follows it. Measured on both specimens.
- **Landing latency for behind-but-clean Dependabot pull requests will rise.** How far is unmeasured:
  the `weekly` interval governs creating pull requests, not maintaining them, and this estate has
  never observed Dependabot freshening an unedited branch here, because the lane always got there
  first. That measurement is worth taking once the change is live.

## 8. Non-goals

- **No rebase-requesting act.** The probe proved our actor cannot perform one.
- **No PAT in the landing path.** Commenting under a user identity would work, and was rejected: it
  puts a user credential where everything else uses the App, and this estate records that the acting
  identity is also the recorded attribution.
- **The inert lane keeps its branch update.** Retiring it there would be right today and wrong the
  day a third author is declared.

## 9. An ADR records this

The finding is about a third party's behaviour, established by a probe, and it reverses part of
ADR-0019 Increment 6. A new ADR is the right home rather than an amendment, because what is durable
is the measurement — an App cannot drive Dependabot — and that outlives this particular act.
