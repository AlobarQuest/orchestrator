"""The ONE body of the two landing callers, parameterised by each lane's `Lane`.

`estate_lander` (ADR-0019 increment 5b) and `inert_lander` (ADR-0038 part 2a) stay TWO PROGRAMS:
two console scripts, two launchers, two schedules, two dead-man checks, two credentials, two
confined orchestrator clients and two enumerations. What they shared was a body -- asking about a
pull request, classifying the answer, acting when told yes, bringing stale branches up to date,
and printing the report -- written twice and kept in step by hand. That body lives here (Tier 3
item 27, Devon 2026-09-28), and every place the lanes genuinely differ is a field of `Lane`, pinned
per lane by a test that reddens if the two are collapsed.

**THIS MODULE COMPOSES NOTHING AND DECIDES NOTHING**, exactly as the two programs never did. Every
term is evaluated by the orchestrator, inside the transaction that records the act; this relays
the answer and classifies it for a reader. It imports no other program and no HTTP client: the
orchestrator is reached only through the client a lane passes in, whose surface is that lane's own
three literal paths.

WHAT A LINE MEANS. A HELD pull request is a finding -- somebody has to act on the condition it
names. A landing, a settled subject, a DELIBERATE refusal (the system refusing on purpose, which
clears on a clock), an EXCEPTION (current policy can never clear it; it waits on a person) and a
WAITING sibling (ADR-0045; clears when the edited branch ahead lands) are not. Every refusal is
printed either way, so the line always says what was missed.

EXIT CODES, the lander group's vocabulary: 0 clean, 1 tool failure, 2 unusable input, 3 findings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

EXIT_OK = 0
EXIT_TOOL_FAILURE = 1
EXIT_UNUSABLE = 2
EXIT_FINDINGS = 3


class OrchestratorError(Exception):
    """The orchestrator could not be asked, or refused in a way this pass cannot interpret."""


class LandingRefused(OrchestratorError):
    """The orchestrator refused. A fact about the subject, not a broken tool.

    It CARRIES THE REFUSAL CODE as well as the message, because not every refusal means the same
    thing to a reader. Some name a condition somebody must act on; others say only that the answer
    moved between the read and the request, which the next pass re-decides on its own. Classifying
    those apart needs the code -- a `DomainError` reaches the wire nested under `error`, and the
    message is prose that will be reworded.

    Defined HERE rather than in each lane's client because the body below catches it: the two
    clients raise this class, and a lane-local copy would be a class this body could not see.
    """

    def __init__(self, message: str, code: str = "") -> None:
        super().__init__(message)
        self.code = code


class LandingClient(Protocol):
    """The whole orchestrator surface these passes use: one question and two acts.

    Each lane's client satisfies it with its OWN three literal paths, checked before the transport
    -- which is the control. A shared client taking paths from its caller was rejected for exactly
    that reason: neither program could then state its own surface.
    """

    def admission(self, repository: str, pr_number: int) -> dict[str, Any]: ...

    def land(
        self, repository: str, pr_number: int, *, head_sha: str, idempotency_key: str
    ) -> dict[str, Any]: ...

    def update_branch(
        self, repository: str, pr_number: int, *, head_sha: str, idempotency_key: str
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class Lane:
    """Everything the two lanes do differently inside the shared body. Nothing else may differ.

    `name` prefixes both idempotency keys. The two lanes cannot have the same subject -- each
    requires the opposite answer from the estate about a repository -- but a shared prefix would
    make that a fact a reader has to know rather than one the key states.

    `deliberate`: refusals that are the system refusing ON PURPOSE and clear on a clock. The estate
    lane's are its pace and change window. **The inert lane has none, and that is derived rather
    than omitted**: it has no change window and no pace rule, so no refusal it raises clears on a
    clock. An empty set here is the statement.

    `exception`: refusals CURRENT POLICY can never clear, which wait on a person. Kept separate from
    `deliberate` though today they have the same effect on the exit code, because WHICH ONE a line
    is IS the information: one clears tonight and one never does.

    `update_self_clearing`: refusals the BRANCH-UPDATE act raises that say only *the answer moved
    between the read and the request*. Spelled per lane (`estate_*`, `inert_*`) by the
    orchestrator, which is why this could never have been one set. Each lane's
    `*_branch_update_siblings_unreadable` is DELIBERATELY absent: not knowing clears on nothing.

    `reads_rollout_pin`: whether a rollout-pin refusal can be caused by a stale head (ADR-0024).
    Only the estate lane evaluates a rollout pin; the inert admission answer deliberately does not
    carry the key. **False is load-bearing, not a default**: were the inert lane to read the key,
    a future answer carrying it would silently install a suppression nobody decided for that lane.
    """

    name: str
    deliberate: frozenset[str]
    exception: frozenset[str]
    update_self_clearing: frozenset[str]
    reads_rollout_pin: bool


# Refusals that mean the SUBJECT IS SETTLED rather than that a condition is unmet -- the pull
# request is gone, or this lane has already acted on it and the row it wrote has no delete path.
# Neither is something a person can act on, and reporting them as findings would make one landing
# -- or one pull request a person merged themselves -- a nightly page forever.
#
# TESTED WITH INTERSECTION, deliberately, and this is the one place that polarity is right: a
# settled subject's other refusals are meaningless because there is nothing left to land. The
# same for both lanes.
SETTLED = frozenset({"landing_already_recorded", "landing_pull_request_not_open"})

# The refusal that says only THIS BRANCH IS BEHIND ITS BASE. It belongs to NO set: alone it is a
# FINDING -- transient, and the branch-update pass clears it on a later run -- while beside an
# exception it is not. Membership is a property of a code; this is a property of the company it
# keeps.
#
# KEYED ON AN EXCEPTION BEING PRESENT, NEVER ON THE LANE HAVING DECLINED TO FRESHEN. Those read as
# one rule and are two: a lane declines to freshen anything it cannot clear, INCLUDING a failing
# check, so keying on the declining would silence a red build. The discriminator is DURABILITY --
# red checks can go green, an exception never clears. (Devon's third refusal ruling, 2026-08-14.)
FRESHNESS = "landing_head_not_current_with_base"

# ADR-0024. A rollout pin that differs BECAUSE the head is stale -- the same code the orchestrator
# raises when a workflow genuinely moved, which is why the base comparison below has to arrive with
# the answer rather than being guessed from the code.
ROLLOUT_MOVED = "landing_rollout_moved"

# The key on the orchestrator's answer carrying that comparison. Named once, because reading it by
# a name the server does not serve fails SILENTLY and in the flattering direction: `.get` returns
# `None`, the criterion excuses nothing, and every affected pull request is a finding again with
# nothing saying why.
BASE_MATCHES_PIN = "rollout_base_matches_pin"

# ADR-0045. The key saying the branch update is withheld because another update-bot pull request
# this lane already edited is queued to land. Named once, for the reason above.
WITHHELD_FOR_SIBLING = "branch_update_withheld_for_sibling"

# Statuses that are not findings, stated as the set to EXCLUDE so a status nobody has thought of
# fails toward being reported.
#
# A branch brought up to date is the lane clearing a condition the lane itself caused, which is the
# system working. `would-update` likewise: it is what a dry run has to say to be worth running.
# `waiting` (ADR-0045) is its own category rather than `deliberate` or `exception`, by Devon's
# ruling that collapsing categories loses which is which.
NOT_A_FINDING = frozenset(
    {
        "landed",
        "would-land",
        "settled",
        "deliberate",
        "exception",
        "waiting",
        "updated",
        "would-update",
    }
)

# Named `waiting`, never `withheld`: a status that contains another as a substring (`held`) makes
# every substring reader of the report -- an operator's `grep held`, a test asserting a status is
# absent -- match both. `test_no_reported_status_is_a_substring_of_another` holds it.
#
# Every status a pass can produce, in report order, so the summary's counts sum to what was
# considered. A summary whose parts do not add up leaves the reader to infer the remainder, and the
# remainder is where the findings are.
REPORTED = (
    "landed",
    "would-land",
    "held",
    "deliberate",
    "exception",
    "waiting",
    "settled",
    "unreadable",
    "error",
    "updated",
    "would-update",
)


@dataclass(frozen=True)
class Outcome:
    repository: str
    number: int
    status: str
    detail: str


def landing_key(lane: Lane, repository: str, number: int, head_sha: str) -> str:
    """CONTENT-ADDRESSED over the subject and the head, so a replay is a replay.

    A random key would make every pass a new request for the same act, which the orchestrator
    would refuse as a spent key belonging to a different subject -- turning an ordinary re-run
    into a finding. Naming the head as well as the pull request means a genuinely new attempt
    after a rebase is a genuinely new key.
    """
    return f"{lane.name}-landing:{repository}:{number}:{head_sha[:12]}"


def update_key(lane: Lane, repository: str, number: int, head_sha: str) -> str:
    """Content-addressed over the head, for the reason above and one more that is specific here.

    A successful update CHANGES the head, so the next legitimate update -- after the base moves
    again -- necessarily carries a different key and can never be barred by this one. That is what
    makes an idempotency key safe on an act whose whole nature is that repeating it is right.
    """
    return f"{lane.name}-branch-update:{repository}:{number}:{head_sha[:12]}"


def freshness_derived(refusals: set[str], *, rollout_base_matches_pin: bool) -> frozenset[str]:
    """Which of these refusals are produced by the head's POSITION relative to its base, and say
    nothing about the change itself? ADR-0024.

    **THE SECOND COPY OF ONE CRITERION, and it is a copy because this program may not import the
    orchestrator** -- the isolation is the property that makes a scheduled caller acceptable here.
    The other copy is `orchestrator.services.landing.terms.freshness_derived_refusals`, which reads
    it to decide whether a lane may ACT; this one reads it to decide whether a line is a FINDING.
    They are held equal from outside, by a test that may import both.

    Being behind IS the position. A rollout pin that differs is derived only when the BASE carries
    the pinned bytes: then the head merely predates a workflow change, and bringing the base in
    carries those bytes with it. Where the base does not carry them the workflow genuinely moved,
    freshening cannot put that right, and it must still report. A lane that does not read the pin
    (`Lane.reads_rollout_pin`) always passes False, so being behind is all its position can cause.

    **`landing_checks_not_clean` is deliberately not a member** -- freshening re-runs checks and
    might turn one green, so "would freshening clear it?" is too loose a test and would silence a
    red build. The discriminator is *does this say anything about the change?*, and a failing check
    does.

    **The head-behind conjunct is load-bearing HERE in a way it is not on the other side.** There,
    the caller's own return already requires the head to be behind; here nothing does, and without
    it a pull request whose OWN DIFF edits the pinned rollout workflow -- base carrying the pinned
    bytes, head current, head blob differing -- would read as merely stale and go quiet beside an
    exception. That is the case the pin exists to catch, so it must always report.
    """
    if FRESHNESS not in refusals:
        return frozenset()
    derived = {FRESHNESS}
    if rollout_base_matches_pin:
        derived.add(ROLLOUT_MOVED)
    return frozenset(derived & refusals)


def held_status(
    lane: Lane,
    refusals: list[str],
    *,
    rollout_base_matches_pin: bool,
    withheld_for_sibling: bool = False,
) -> str:
    """`held`, `deliberate`, `exception` or `waiting`, for an answer unsatisfied and not settled.

    SUBSET, never intersection -- and that is the whole of this function. `SETTLED` is tested with
    intersection, correctly: a settled subject's other refusals are meaningless. **A deliberate
    refusal says nothing about the other conditions.** `landing_pace_exhausted` co-occurs on every
    held pull request once the day's landing is spent, so an intersection rule here would silence a
    pull request whose checks are failing because a deliberate refusal happened to sit beside it.
    So a line stops being a finding only when EVERY refusal is one nobody can act on; a code nobody
    has thought of leaves it a finding.

    NO refusals at all is a FINDING, not a vacuous pass: an answer unsatisfied while naming nothing
    is the orchestrator failing to say why, and the subset test alone would call it quiet.

    An exception outranks a deliberate refusal and a waiting sibling when both are present, because
    the exception is the durable fact.

    A FRESHNESS-DERIVED refusal IS SUPPRESSED WHEN, AND ONLY WHEN, AN EXCEPTION IS PRESENT OR A
    SIBLING IS OBSERVED HOLDING (ADR-0045). Conditional, never unconditional: an unconditional
    subtraction would make a branch that is merely behind read as quiet, and an unconditional early
    return would also silence `{behind, checks_not_clean}`. `waiting` is keyed on an OBSERVED
    sibling, never on the lane declining, and a key with no freshness refusal to subtract changes
    nothing. The default is False, so a caller that forgets it gets `held`.

    AN UNANSWERED CHECK (`landing_checks_awaiting_verdict`) IS DELIBERATELY IN NO SET. The
    orchestrator excuses it for ACTING -- freshening re-runs an abandoned check -- but a suppression
    here can never fire: qualifying requires the head to be behind, and being behind is itself
    unexplained, so the line is held whatever this says about the other code. An inert suppression
    is worse than none, because a later change would switch it on with nobody re-deciding it.

    For a lane with no deliberate refusal, `deliberate` is unreachable: with nothing deliberate to
    explain a refusal, the final branch is reached only when the sibling key emptied `unexplained`,
    which returns `waiting` first.
    """
    present = set(refusals)
    unexplained = present - lane.deliberate - lane.exception
    derived = freshness_derived(present, rollout_base_matches_pin=rollout_base_matches_pin)
    if lane.exception & present or withheld_for_sibling:
        unexplained -= derived
    if unexplained or not refusals:
        return "held"
    if lane.exception & present:
        return "exception"
    if withheld_for_sibling and derived:
        return "waiting"
    return "deliberate"


def consider(
    lane: Lane, client: LandingClient, repository: str, number: int, submit: bool
) -> Outcome:
    """Ask about one pull request, and act when told the answer is yes."""
    try:
        answer = client.admission(repository, number)
    except OrchestratorError as error:
        return Outcome(repository, number, "unreadable", str(error))

    refusals = [str(r) for r in (answer.get("refusals") or [])]
    if SETTLED & set(refusals):
        return Outcome(repository, number, "settled", ", ".join(refusals))
    if not answer.get("satisfied"):
        # A MISSING key reads as False, which withholds the criterion's one conditional member and
        # leaves the line a finding. That is the direction to fail in, and it is not hypothetical:
        # the answer comes from a deployed orchestrator, and a release that has not reached
        # production yet serves an answer with no such key.
        status = held_status(
            lane,
            refusals,
            rollout_base_matches_pin=(
                lane.reads_rollout_pin and answer.get(BASE_MATCHES_PIN) is True
            ),
            withheld_for_sibling=answer.get(WITHHELD_FOR_SIBLING) is True,
        )
        return Outcome(repository, number, status, ", ".join(refusals))

    head = answer.get("head_sha")
    if not isinstance(head, str) or not head:
        # Admissible with no head is unreachable through the orchestrator's own cascade, which
        # refuses an unreadable pull request. Stated rather than assumed, because acting without a
        # head would be asking for whatever has been pushed since.
        return Outcome(repository, number, "unreadable", "admissible but names no head")
    if not submit:
        return Outcome(repository, number, "would-land", f"head {head[:12]}")

    try:
        landed = client.land(
            repository,
            number,
            head_sha=head,
            idempotency_key=landing_key(lane, repository, number, head),
        )
    except LandingRefused as error:
        return Outcome(repository, number, "held", str(error))
    except OrchestratorError as error:
        return Outcome(repository, number, "error", str(error))
    return Outcome(repository, number, "landed", f"status={landed.get('status')}")


def landing_pass(
    lane: Lane, subjects: list[tuple[str, int]], client: LandingClient, submit: bool
) -> list[Outcome]:
    """Ask about every subject the lane enumerated."""
    return [consider(lane, client, repository, number, submit) for repository, number in subjects]


def wants_update(answer: dict[str, Any]) -> bool:
    """Does the answer say this branch should be brought up to date now?

    Only when it qualifies AND is not withheld for a sibling (ADR-0045): the orchestrator observed
    an edited update-bot branch queued ahead of this one, and the landing pass has already printed
    why. A missing `branch_update_qualifies` reads as False, which withholds the act -- the
    direction to fail in, and not hypothetical: the estate lane read a key that was not there for
    two days and freshened nothing while reporting zero. A missing withheld key reads as not
    withheld, which is what an older orchestrator serves.
    """
    return bool(answer.get("branch_update_qualifies")) and (
        answer.get(WITHHELD_FOR_SIBLING) is not True
    )


def branch_updates(
    lane: Lane, subjects: list[tuple[str, int]], client: LandingClient, submit: bool
) -> list[Outcome]:
    """Bring up to date the branches whose only remaining obstacle is that they are behind.

    **AFTER the landing pass, and that ordering is load-bearing.** A landing moves the base, so it
    is the act that puts every sibling behind; going first would bring a branch up to date and
    then immediately stale it again by landing something else, spending a real build on a tree
    that is out of date before it finishes.

    IT RUNS ON EVERY PASS, not only on one that landed something. A pull request a person merged
    themselves stales its siblings exactly as ours does, and one staled that way is invisible to
    anything that only reacts to this program's own acts.

    The answer is READ AGAIN rather than carried over from the landing pass, because the landing
    pass may have changed it -- which is the whole reason this runs second.

    WHICH ONES QUALIFY IS NOT DECIDED HERE. The orchestrator says so on the answer, and again
    inside the transaction that acts. A subject that does not qualify gets no line, because the
    landing pass has already printed one naming every condition it misses. Nor does one the answer
    says is withheld for a sibling, for the same reason -- which also keeps a dry run's
    `would-update` to what a live pass would actually update.
    """
    outcomes: list[Outcome] = []
    for repository, number in subjects:
        try:
            answer = client.admission(repository, number)
        except OrchestratorError as error:
            outcomes.append(Outcome(repository, number, "unreadable", str(error)))
            continue
        if not wants_update(answer):
            continue
        head = answer.get("head_sha")
        if not isinstance(head, str) or not head:
            outcomes.append(
                Outcome(repository, number, "unreadable", "qualifies but names no head")
            )
            continue
        if not submit:
            outcomes.append(Outcome(repository, number, "would-update", f"head {head[:12]}"))
            continue
        try:
            answered = client.update_branch(
                repository,
                number,
                head_sha=head,
                idempotency_key=update_key(lane, repository, number, head),
            )
        except LandingRefused as error:
            status = "deliberate" if error.code in lane.update_self_clearing else "held"
            outcomes.append(Outcome(repository, number, status, str(error)))
        except OrchestratorError as error:
            outcomes.append(Outcome(repository, number, "error", str(error)))
        else:
            if answered.get("replayed"):
                # ASKED BEFORE, AT THIS SAME HEAD, AND THE BRANCH HAS NOT MOVED. The key is
                # content-addressed over the head and a success moves it, so this is the platform
                # having accepted the work and not delivered it. Reporting it as an update would
                # describe that as success on every pass, forever.
                outcomes.append(
                    Outcome(
                        repository, number, "held", f"asked before at {head[:12]}, still behind"
                    )
                )
            else:
                outcomes.append(
                    Outcome(repository, number, "updated", f"was behind at {head[:12]}")
                )
    return outcomes


def report(outcomes: list[Outcome], preamble: list[str], deferral_lines: list[str]) -> int:
    """Print the lane's preamble, every act considered, what was left alone, then the summary.

    The PREAMBLE and the DEFERRAL LINES are the lane's own words, because what they describe
    differs: the inert lane prints the policy version its permission came from, and each lane
    names what it left alone in its own terms.

    THE DEFERRAL LINES ARE SEPARATE FROM THE SUMMARY, NOT FOLDED INTO IT. `REPORTED` exists so the
    summary's parts add up to what was considered, and a deferred subject was never considered.
    They do not affect the exit code: deferring is the program working.
    """
    for line in preamble:
        print(line)
    for outcome in outcomes:
        subject = f"{outcome.repository}#{outcome.number}"
        print(f"{subject}  {outcome.status:<12} {outcome.detail}")
    for line in deferral_lines:
        print(line)
    counted = {status: sum(o.status == status for o in outcomes) for status in REPORTED}
    findings = [o for o in outcomes if o.status not in NOT_A_FINDING]
    print(
        f"\n{len(outcomes)} considered, "
        + ", ".join(f"{counted[status]} {status}" for status in REPORTED)
    )
    return EXIT_FINDINGS if findings else EXIT_OK
