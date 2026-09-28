"""The terms both landing lanes compose their answers from, and the branch-update criterion.

Each lane's admission ANDs its own terms with these; the refusal a term raises means the same thing
whichever lane raised it. Pure functions of what the gateway reads -- no session, no clock -- so
either admission can call them without importing the other.
"""

from __future__ import annotations

from typing import Final

from orchestrator.services.landing.interfaces import (
    DELIBERATE_REFUSALS,
    LANDING_CHECKS_AWAITING_VERDICT,
    LANDING_CHECKS_IN_FLIGHT,
    LANDING_CHECKS_NOT_CLEAN,
    LANDING_CHECKS_VERDICT_UNREADABLE,
    LANDING_ECOSYSTEM_EXCLUDED,
    LANDING_ECOSYSTEM_UNREADABLE,
    LANDING_FRESHNESS_UNREADABLE,
    LANDING_HEAD_NOT_CURRENT_WITH_BASE,
    LANDING_MERGEABILITY_UNRECOGNISED,
    LANDING_PULL_REQUEST_CONFLICTED,
    LANDING_ROLLOUT_MOVED,
    MERGEABLE_BLOCKED,
    MERGEABLE_CLEAN,
    MERGEABLE_DIRTY,
    MERGEABLE_UNSTABLE,
    NO_VERDICT_CONCLUSIONS,
    RUN_COMPLETED,
    RUN_SUCCEEDED,
    EstateGatewayError,
    EstatePullRequest,
    EstateReadGateway,
    Term,
)

# The update bot's branch naming, from which the ecosystem is read: `dependabot/<ecosystem>/<rest>`.
_BRANCH_PREFIX: Final = "dependabot/"


def freshness_derived_refusals(
    refusals: tuple[str, ...] | frozenset[str] | set[str],
    *,
    rollout_base_matches_pin: bool,
) -> frozenset[str]:
    """Which of these refusals are produced by the head's POSITION relative to its base, and say
    nothing about the change itself? ADR-0024.

    **ONE CONCEPT, TWO CONSUMERS, AND THEY ASK DIFFERENT QUESTIONS OF IT.**
    `qualifies_for_branch_update` below asks *may the lane act on this* -- yes when every obstacle
    is either freshness-derived or deliberate. The reporting agent asks *is this a finding* --
    no, beside a refusal current policy can never clear, when what remains is freshness-derived.
    Expressed once so a fifth member is answered in both places by construction, which is the
    whole reason ADR-0024 rules on the class rather than on the case.

    ## The criterion, and the discriminator that keeps it narrow

    Being behind IS the position, so it is derived whenever it is present. A rollout pin that
    differs is derived only when the BASE carries the pinned bytes: under that condition the head
    simply predates a workflow change and bringing the base's commits in carries the pinned bytes
    with it, while a base that does not carry them means the workflow genuinely moved and no
    amount of freshening puts that right.

    **A failing check is deliberately NOT a member, and it is the case that keeps this honest.**
    Freshening re-runs checks and might turn one green, so *would freshening clear it?* is too
    loose a test and would silence a red build. The discriminator is *does this say anything about
    the change?* -- and a failing check does.

    ## The head-behind conjunct, which is not decoration

    A refusal cannot be caused by a position the head is not in. `qualifies_for_branch_update`
    supplies that fact through its own return condition, so adding it here changes nothing for
    that caller -- but the reporting consumer has no such guard, and without it a pull request
    whose OWN DIFF edits the pinned rollout workflow (base carrying the pinned bytes, head current
    with its base, head blob differing) would be classed as merely stale. That is `_rollout_term`'s
    founding case and it must always report.

    Returned members are intersected with what was actually raised, so the answer describes THESE
    refusals rather than a vocabulary.
    """
    present = set(refusals)
    if LANDING_HEAD_NOT_CURRENT_WITH_BASE not in present:
        return frozenset()
    derived = {LANDING_HEAD_NOT_CURRENT_WITH_BASE}
    if rollout_base_matches_pin:
        derived.add(LANDING_ROLLOUT_MOVED)
    return frozenset(derived & present)


def qualifies_for_branch_update(
    refusals: tuple[str, ...], *, rollout_base_matches_pin: bool
) -> bool:
    """May the lane bring this pull request's head up to date with its base?

    **ONLY WHEN FRESHNESS IS THE SOLE REMAINING OBSTACLE**, and that rule is the whole design
    rather than a precaution. The lane creates this condition itself: a landing moves the base, so
    every sibling pull request in that repository becomes behind it, and the freshness term then
    refuses them all. Nothing else resolves it -- measured, one pull request sat 29 hours behind
    while three windows passed over it.

    So the lane clears what the lane staled. What it must NOT do is bring up to date a pull request
    that could not land anyway: a requirement-range bump states no single version delta and can
    never be classified, a red check is not made green by a fresher base. Each would spend a real
    build on a branch whose answer does not change, and a build running is indistinguishable from
    progress to whoever reads the report.

    The remainder is tested against a CATEGORY and never against a count. A pull request refused on
    freshness alone qualifies; so does one also refused because the day's pace is spent or the hour
    is outside the window, because each of those clears itself and neither says anything about the
    branch. Any other refusal -- present or future, named or not yet invented -- disqualifies,
    save the single carve-out below, which is keyed on two FACTS rather than on membership of a
    set. That polarity is what this lane argues for everywhere else: an unclassified code must fail
    toward refusing rather than toward acting.

    ## The carve-out: a rollout pin that differs BECAUSE the head is stale

    `_rollout_term` compares the pinned workflow's bytes at the base and at the head, so a head
    opened before that file last changed reports `landing_rollout_moved` -- a refusal CAUSED by
    being behind, which is the very condition this rule exists to clear. Read as an obstacle it is
    a deadlock, and it was one: the five `alobarquest/brain` pull requests open on 2026-08-16 were
    each refused for being behind their base and disqualified from the one mechanism that would
    bring them up to date.

    **That carve-out is no longer stated here.** ADR-0024 found the same question being asked by a
    second consumer and made it a criterion -- `freshness_derived_refusals` above -- of which this
    is now one reader. What that function excuses, and why a genuinely moved workflow is not
    excused, is stated there.

    Note what this DID cost, since an earlier version of this docstring argued the opposite:
    the criterion carries the "head is behind" conjunct itself, where this function had left it to
    the return below on the grounds that restating it would give one fact two sources. That
    reasoning held only while this was the sole reader. The two are equivalent HERE -- the return
    requires the same thing -- so nothing about this answer moved.

    **The carve-out is self-limiting rather than trusted.** After an update the term re-evaluates
    against the NEW head: a pull request that does not touch the workflow then carries the pinned
    bytes and proceeds, while one whose own diff edits that file still differs and is still refused
    -- `_rollout_term`'s founding case, untouched. The cost of that ambiguity is one build on a
    pull request that will not land; nothing in the cascade can see a pull request's changed files,
    so no narrower reading is available here.

    ## The third subtraction: a head whose checks reached no verdict

    `landing_checks_awaiting_verdict` does not disqualify, and it is the only refusal here that is
    excused because bringing the branch up to date is what ANSWERS it rather than what tolerates
    it. The two above clear on their own and this one does not: nothing else in the estate re-runs
    a check that was abandoned, so a pull request holding one waits forever while its own report
    says the checks are not clean.

    **It is not folded into the freshness criterion**, though it would qualify at a glance. That
    criterion asks *is this refusal produced by the head's POSITION?* and this one is not -- it is
    produced by what happened to the runs. Folding it in would also excuse it for the reporting
    consumer, which reads the same criterion to decide what is a finding, and there the answer is
    different: an unanswered check beside a permanent exception is still worth saying.

    **A failing check remains disqualifying, and that boundary is the whole value of the split.**
    Freshening cannot turn a red verdict green, so offering it one spends a build to re-learn the
    same answer -- and a build running is indistinguishable from progress to whoever reads the
    report. `checks_term` is where the two are told apart, and it does so by reading the runs
    rather than by trusting a word that covers both.

    ## The shape, because it will recur

    **When a refusal can be CAUSED by the condition another rule exists to clear, the two rules
    deadlock.** This test must be keyed on refusals that are genuinely independent of freshness --
    not merely on the ones that happened to be live when it was written.
    """
    # `False` withholds the carve-out, so every path that did not positively observe a matching
    # base leaves the refusal standing.
    remainder = (
        set(refusals)
        - freshness_derived_refusals(refusals, rollout_base_matches_pin=rollout_base_matches_pin)
        - DELIBERATE_REFUSALS
        - {LANDING_CHECKS_AWAITING_VERDICT}
    )
    return LANDING_HEAD_NOT_CURRENT_WITH_BASE in refusals and not remainder


def checks_term(
    repository: str,
    pull: EstatePullRequest,
    gateway: EstateReadGateway,
) -> Term:
    """Do the checks at this head say NO, say NOTHING YET, or say nothing AT ALL?

    Three answers where the platform's composite offers one word. `clean` is the only value that
    permits, and every other value used to raise a single refusal naming a failing check -- which
    is true of one cause and false of the other two, and false in the direction that matters: the
    remedy for a check that never reported is to run it, and this lane owns the act that does so.

    ## The second read, and why it is not optional

    `mergeable_state` is a scalar. Measured against one repository with one required check, a
    genuinely failing gate, a gate abandoned mid-run and a gate still running ALL answer `blocked`,
    and three live pull requests in this estate's own ledger repositories answer `blocked` with
    every run at their head abandoned. No amount of care with the composite recovers the
    difference, so the runs at the head are read.

    ## `blocked` AND `unstable` are inquired into; the rest are named, not guessed at

    Both of those mean some run at this head has not said yes -- required or not -- so both get the
    second read, and which of the three causes holds is a question about the RUNS either way.

    Everything else is a statement about the BRANCH rather than about a verdict, and none is made
    right by a fresher base. **Until 2026-09-05 they all raised the failing-check refusal**, so a
    conflicted branch was reported as having unclean checks while its checks were green -- two
    `Quality` runs at `success` on a head diverged from its base, measured in this estate. A
    conflict now says so, and a value this lane does not recognise says THAT rather than borrowing
    a cause from a check that may never have run. Both still refuse, and both still keep a
    conflicted branch away from an update that would fail at the remote anyway; only the name a
    reader is sent to investigate has changed.

    ## The order of the three questions is the safety

    A failing run outranks one still going, which outranks the absence of any verdict: a head
    carrying one red run and one still running has said no, whatever else is pending. Reading
    those in the other order would let an in-flight sibling excuse a failure.

    ## Residual, named rather than implied

    This reads every run at the head, not the REQUIRED ones -- the required-context list needs a
    permission this estate's App does not hold. So an unrelated failing workflow holds a pull
    request that branch protection would have let through. That is the conservative direction and
    it is the same residual the composite's own note already carries.
    """
    if pull.mergeable_state == MERGEABLE_CLEAN:
        return Term(True, ())
    if pull.mergeable_state == MERGEABLE_DIRTY:
        return Term(False, (LANDING_PULL_REQUEST_CONFLICTED,))
    if pull.mergeable_state not in (MERGEABLE_BLOCKED, MERGEABLE_UNSTABLE):
        return Term(False, (LANDING_MERGEABILITY_UNRECOGNISED,))
    try:
        runs = gateway.head_check_runs(repository=repository, head_sha=pull.head_sha)
    except EstateGatewayError:
        return Term(False, (LANDING_CHECKS_VERDICT_UNREADABLE,))
    if any(
        run.status == RUN_COMPLETED
        and run.conclusion != RUN_SUCCEEDED
        and run.conclusion not in NO_VERDICT_CONCLUSIONS
        for run in runs
    ):
        return Term(False, (LANDING_CHECKS_NOT_CLEAN,))
    if any(run.status != RUN_COMPLETED for run in runs):
        return Term(False, (LANDING_CHECKS_IN_FLIGHT,))
    return Term(False, (LANDING_CHECKS_AWAITING_VERDICT,))


def freshness_term(
    repository: str,
    pull: EstatePullRequest,
    gateway: EstateReadGateway,
    *,
    required: bool,
) -> Term:
    """Is the head current with the base it would be squashed onto?

    The condition exists because required checks are not required to be up to date on these
    repositories -- a deliberate estate-wide choice -- so a check can be green against a head that
    is behind, and a squash of that head produces a tree nothing has executed. Where landing
    changes something already serving, that tree is what starts serving.

    It is a POLICY condition rather than a branch setting because a branch setting serialises
    what a person lands too, applies estate-wide behaviour nobody versions, and blocks silently
    where
    this produces a named refusal.

    **`required` ARRIVES AS A BOOLEAN rather than as the object that carries it**, because the two
    lanes that ask this question are told by two different documents: the deploying lane reads the
    conditions projected onto a change record, and the lane serving repositories where landing
    changes nothing already serving reads a block of the same policy that has no record to be
    projected onto. Passing the object would have made this function know about a shape only one
    of its callers has, and the alternative -- a second copy -- is what this repository keeps
    paying for.
    """
    if not required:
        return Term(True, ())
    try:
        behind = gateway.commits_behind_base(
            repository=repository, base_ref=pull.base_ref, head_sha=pull.head_sha
        )
    except EstateGatewayError:
        return Term(False, (LANDING_FRESHNESS_UNREADABLE,))
    if behind > 0:
        return Term(False, (LANDING_HEAD_NOT_CURRENT_WITH_BASE,))
    return Term(True, ())


def ecosystem_of(head_ref: str) -> str | None:
    """Which package ecosystem the update bot says this branch belongs to, or None.

    The second segment of `dependabot/<ecosystem>/<rest>`, which is the same fact the estate's
    landing ledger reads and the same one the update bot's own metadata action derives. Read from
    the BRANCH rather than from the title, unlike the version delta
    (`update_type_of`, in `estate_landing_admission`), and the two are not in
    tension: the branch goes stale about the VERSION when the bot rewrites a pull request in place,
    and it cannot go stale about the ecosystem, because an update never moves between them.

    None for any name that is not that shape. What that means is the caller's to decide, and it
    decides refuse.
    """
    if not head_ref.startswith(_BRANCH_PREFIX):
        return None
    rest = head_ref[len(_BRANCH_PREFIX) :]
    ecosystem, separator, remainder = rest.partition("/")
    if not separator or not ecosystem or not remainder:
        return None
    return ecosystem


def ecosystem_exclusion_term(pull: EstatePullRequest, excluded: frozenset[str]) -> Term:
    """Is this bump in an ecosystem the required checks do not exercise?

    **ONE COPY, TWO LANES, AND THE EXCLUDED SETS ARE DIFFERENT ON PURPOSE.** Both halves of the
    estate exclude on the same principle -- exclude where the required checks do not exercise what
    changed -- and each names a different ecosystem, because what goes unexercised differs. So the
    SET is per-lane and arrives as an argument, and the reading of it is shared: which segment of
    the branch names the ecosystem, what an unreadable name means, and the case fold.

    UNREADABLE IS ITS OWN ANSWER AND REFUSES, for the reason `LANDING_ECOSYSTEM_UNREADABLE`
    records: a name this cannot read is never "the bot named no ecosystem", it is this program
    failing to read what the exclusion is about.

    CASE-FOLDED ON BOTH SIDES, which is exactly what `pin_for` does with the other identity key
    crossing this boundary -- the parser folds what it stores AND the lookup folds what it asks.
    Folding only at the parser would leave this correct for a served document and wrong for any
    other constructor of these values, and the direction of that failure is PERMISSIVE: a member
    differing in case is not `in` the set, and not-in means admitted.
    """
    ecosystem = ecosystem_of(pull.head_ref)
    if ecosystem is None:
        return Term(False, (LANDING_ECOSYSTEM_UNREADABLE,))
    if ecosystem.lower() in {name.lower() for name in excluded}:
        return Term(False, (LANDING_ECOSYSTEM_EXCLUDED,))
    return Term(True, ())
