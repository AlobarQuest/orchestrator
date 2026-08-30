"""Turn a cascade-refused dependency bump into proposed work (ADR-0028).

**WHAT THIS IS THE HEAD OF.** ADR-0016 gave routine dependency updates to GitHub's own
auto-merge and assigned the remainder -- the majors the cascade correctly refuses -- to the
factory. ADR-0026 built the record and the carry. ADR-0028 made the package a standing one,
revised per bump. Nothing produced the record, so the lane held zero of them. This is that
producer:

    Dependabot opens a major bump
      -> the transcribed cascade refuses it
      -> HERE: revise the standing package, approve the revision by policy, publish the
         commit that carries it (ADR-0033), propose a record
      -> a human approves the record in change-manager
      -> the carry registers an intake -> decomposition -> envelope -> dispatch

**WHY THIS PRODUCER IS MECHANICAL AND OTHERS ARE NOT.** A cascade-refused bump IS its own work
statement: the title states the delta, and ADR-0016 already decided that such bumps go to the
factory. There is no diagnosis step, which is exactly what ADR-0026 says no producer of the
other kind has. Do not generalise this to signals that need interpreting; a producer that
guessed would be a worse thing than no producer.

**IT REPRODUCES THE CASCADE'S ANSWER RATHER THAN RE-DECIDING IT.** Whether the gate refuses a
bump is read from `landing_ledger.rules`, the hand-transcribed registry keyed on the gate
workflow's git blob sha -- the same registry the landing audit uses, and the same fail-closed
property: a gate revision nobody has transcribed is a finding, not a guess. Re-implementing the
gate's condition would be a second interpreter of a GitHub expression language nobody owns.

**IT APPROVES A PACKAGE REVISION AND NOTHING ELSE.** It cannot approve the change record it
writes: the record is created `pending` by construction, change-manager's `propose` scope
refuses every status-moving route, and this program's own client asserts a two-path surface.
The human decision ADR-0028 keeps is the record, and this producer is on the other side of it.

**A REPEAT PASS IS A REPLAY, in both halves.** A standing package whose tip revision already
carries this bump is not revised again; change-manager answers 200 for an identical proposal.
So the pass is safe to schedule, and a second run over an unchanged estate writes nothing.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from bump_proposer.change_manager import (
    DEFAULT_BASE_URL,
    ChangeManagerClient,
    ChangeManagerError,
    ProposalRefused,
)
from bump_proposer.standing import (
    PACKAGES_REPOSITORY,
    StandingError,
    StandingPackage,
    advance,
    checkout_root,
    commit,
    discover,
    require_clean,
    require_published,
    snapshot_hash,
)
from landing_ledger.audit import REFUSING_CONCLUSIONS, is_green
from landing_ledger.github import (
    GitHubReader,
    LedgerError,
    current_rule_revision,
    default_branch,
    read_pending_updates,
)
from landing_ledger.model import PendingUpdate
from landing_ledger.rules import Rule, rule_for
from landing_ledger.titles import Bump, bump_of

EXIT_OK: Final = 0
EXIT_UNUSABLE: Final = 2
EXIT_FINDINGS: Final = 3

ACTOR: Final = "bump-proposer"
RISK: Final = "caution"

# How long a concluded failure must stand before this producer reads it as the cascade's answer.
#
# IT GUARDS AN IRREVERSIBLE ACT, which is why it exists at all and why it is not borrowed from
# `landing_ledger.audit.SETTLE_SECONDS` despite the two sharing a magnitude. That one bounds how
# long an armed-and-green pull request may sit before the fact is worth REPORTING, where a wrong
# answer is a noisy line. This one decides whether to mint a package revision, and a package
# revision cannot be unminted: take a failure that a re-run then clears and the record is
# stranded as superseded, having spent a human approval. Two questions with two costs, and one
# name would let either be tuned for the other.
#
# `PendingUpdate.checks` holds only jobs that have CONCLUDED, so a pull request whose checks are
# still running is indistinguishable from one whose checks are finished except by how recently
# the last of them concluded. That makes this interval the only instrument available for "has
# the gate finished deciding?", rather than a nicety on top of one.
FAILURE_SETTLE_SECONDS: Final = 3600

# What makes a pass a FINDING rather than a clean run.
#
# `unclassifiable` and `unlaned` are deliberately ABSENT. A requirement-range bump states no
# single delta -- the estate has already ruled that a refusal nothing can ever clear is an
# exception rather than a finding, and the control that reports it forever stops being read.
# NOTE WHY it cannot be cleared, because that reason changed on 2026-08-28 and the outcome did
# not: the cascade now PERMITS a range (ADR-0034), so it is no longer refused there -- but a
# revision of a standing package carries two versions, and a range names none, so this producer
# still cannot describe one. `unlaned` is the same shape one level up: a bump for a dependency
# nobody has authored a standing package for is outside this lane by construction, and authoring
# one is the act that changes it.
FINDING_STATUSES: Final = frozenset(
    {
        "error",
        "unreadable",
        "ambiguous",
        "no-cascade",
        "gate-not-transcribed",
        "superseded",
        "refused",
    }
)


@dataclass(frozen=True)
class Outcome:
    repository: str
    number: int
    status: str
    detail: str


def _refused_by_the_checks(pending: PendingUpdate, now: datetime) -> bool:
    """Whether the required checks have finished deciding, and decided against this update.

    THREE STATES ARRIVE HERE AND ONLY ONE OF THEM IS THIS LANE'S SUBJECT. Nothing concluded yet,
    or something concluded a moment ago with more still to come: the gate has not answered, so
    neither lane acts. Everything concluded and none of it failed: the cascade lands it. Settled,
    with at least one failure: the cascade will not land it, and that remainder is what ADR-0016
    assigns to the factory.

    FAIL-CLOSED MEANS `False` HERE, and the asymmetry is the point. Answering "refused" wrongly
    mints a package revision that cannot be unminted and spends a human approval on a bump that
    was about to land by itself; answering "not refused" wrongly costs a pass, and the next pass
    picks it up. So an unreadable or unsettled state waits.

    THAT IS WHY IT ASKS `REFUSING_CONCLUSIONS` AND NOT `FAILING_CONCLUSIONS`. The wider set exists
    for `is_green`, where calling a cancelled run not-green is conservative. Here it would be
    fail-OPEN: a required job cancelled by hand, or gone `stale` because the Actions quota ran
    out, is no verdict about the change at all -- and reading it as one mints a revision while the
    gate's arming stays live, so the bump lands the moment somebody re-runs the job green.

    IT DOES NOT KNOW WHICH JOBS WERE REQUIRED, and `Check`'s own docstring says as much. Every
    `pull_request` job in all six lane repositories is a required context today, measured, so a
    non-required failure cannot reach this yet; adding one advisory job anywhere makes it
    reachable, with no signal. Answering it needs the branch's protection settings, which the
    ledger deliberately does not reconstruct.
    """
    if pending.last_concluded_at is None:
        return False
    if (now - pending.last_concluded_at).total_seconds() < FAILURE_SETTLE_SECONDS:
        return False
    return any(check.conclusion in REFUSING_CONCLUSIONS for check in pending.checks)


def _consider(
    pending: PendingUpdate,
    rule: Rule,
    packages: dict[tuple[str, str], StandingPackage],
    now: datetime,
) -> tuple[StandingPackage, Bump, str] | tuple[None, None, str]:
    """Decide what this open pull request is, or say why it is nothing to do here."""
    bump = bump_of(pending.title)
    declared_by_title = bump.declared if bump is not None else None
    declared_by_bot = pending.update.update_type if pending.update is not None else None

    if declared_by_title != declared_by_bot:
        # THE TWO SOURCES DISAGREE, and they answer different halves of one question: the
        # cascade evaluates the bot's own trailer, while the versions a revision must carry are
        # only reliable in the title (the bot rewrites a pull request in place, and the branch
        # and the trailer go stale). When they disagree, the pull request either states a delta
        # the gate did not see or the reverse, and one of those two directions means the gate
        # is about to merge something this producer is proposing as work.
        return (
            None,
            None,
            (
                f"ambiguous: the title declares {declared_by_title or 'no delta'} and the "
                f"update trailer declares {declared_by_bot or 'no delta'}"
            ),
        )
    if bump is None:
        return None, None, "unclassifiable: the title states no single version delta"

    assert pending.update is not None  # implied by declared_by_bot == declared_by_title != None
    ecosystem = pending.update.ecosystem
    # PERMITTED IS NOT THE SAME QUESTION AS LANDED, and it stopped being a usable proxy for it on
    # 2026-08-28. While the gate's condition was a cascade over update types, "the cascade
    # permits this" and "the cascade lands this" picked out the same set, because everything it
    # refused it refused on the declaration alone. Revision 3457db3c permits anything it does not
    # exclude and leaves the rest to the required checks (ADR-0034), so a bump whose checks FAIL
    # is now permitted and unlandable at once -- and this lane's subject is the second half.
    # Reading `permits` alone here would EMPTY the factory's queue rather than narrow it to the
    # failures ADR-0034 assigns to it, which is the opposite of what that decision says.
    if rule.permits(bump.declared, ecosystem) and not _refused_by_the_checks(pending, now):
        answer = "have passed" if is_green(pending) else "have not concluded against it"
        return (
            None,
            None,
            f"the installed gate permits a {bump.kind} of a {ecosystem} dependency "
            f"and its checks {answer}",
        )

    key = (pending.repository.lower(), pending.update.dependency)
    package = packages.get(key)
    if package is None:
        return None, None, f"unlaned: no standing package covers {pending.update.dependency}"
    return package, bump, f"{pending.update.dependency} {bump.from_version} to {bump.to_version}"


def _reasoning(package: StandingPackage, bump: Bump, pending: PendingUpdate) -> str:
    """Why this work exists -- and a FROZEN string, like every asserted field on a record.

    change-manager compares the asserted fields against the stored ones and refuses a
    difference with a terminal 409, so this must say only what stays true for the life of the
    record. That is safe here in a way it was not for the deploy producer, whose subject was a
    pull request whose title drifts: this record's identity is the package REVISION, one
    revision carries exactly one bump, and a bump that moves produces a new revision and a new
    record rather than rewriting this one. So the two versions may be named. Nothing dated,
    nothing counted, and never the pull request's title.
    """
    return (
        f"{package.target_repository} carries an open dependency update of "
        f"{package.dependency} from {bump.from_version} to {bump.to_version} "
        f"(pull request {pending.number}), and the auto-merge cascade installed on that "
        f"repository does not permit a {bump.kind} of this kind. ADR-0016 assigns that "
        f"remainder to the factory. Revision {package.revision} of the standing package "
        f"{package.package_id} carries this bump, approved by conformance to "
        f"approval-policy.toml rather than by a named human (ADR-0028). Approving this record "
        f"is the decision to have the factory make the change; decomposition and the authority "
        f"envelope are each approved separately after it."
    )


def _proposal(package: StandingPackage, bump: Bump, pending: PendingUpdate) -> dict[str, Any]:
    return {
        "package_id": package.package_id,
        "package_revision": package.revision,
        "package_source_repository": PACKAGES_REPOSITORY,
        "risk": RISK,
        "reasoning": _reasoning(package, bump, pending),
        "actor": ACTOR,
    }


def _superseded(records: list[dict[str, Any]], package: StandingPackage) -> list[Outcome]:
    """Records for an EARLIER revision of this package that nothing can carry any more.

    Only the tip revision is on disk, and the carry refuses a record whose revision does not
    match the checkout -- so advancing a standing package strands any record still standing for
    the revision before it. That happens for one reason: the update bot rewrote the pull request
    in place and the bump this lane is carrying changed under a stable pull request number.
    Reported HERE rather than left for the carry to discover the next morning, because this pass
    is what caused it and is the only thing that knows why.
    """
    stranded = []
    for row in records:
        if row.get("package_id") != package.package_id:
            continue
        revision = row.get("package_revision")
        if not isinstance(revision, int) or revision >= package.revision:
            continue
        if row.get("status") in {"resolved", "wontfix"}:
            continue
        stranded.append(
            Outcome(
                package.target_repository,
                0,
                "superseded",
                f"record {row.get('id')} names {package.package_id} revision {revision}, which "
                f"is no longer the revision on disk; the bump it describes was replaced",
            )
        )
    return stranded


def _act(
    package: StandingPackage,
    bump: Bump,
    pending: PendingUpdate,
    client: ChangeManagerClient | None,
    records: list[dict[str, Any]],
    root: Path,
) -> list[Outcome]:
    subject = pending.repository
    if client is None:
        state = "would-replay" if package.carries(bump) and package.approved else "would-advance"
        return [
            Outcome(
                subject,
                pending.number,
                state,
                f"{package.package_id}: {bump.from_version} to {bump.to_version}",
            )
        ]

    published: str | None = None
    if not (package.carries(bump) and package.approved):
        package = advance(package, bump, root)
        snapshot_hash(package, root)
        published = commit(package, bump, root)

    record, created = client.propose(_proposal(package, bump, pending))
    # THE SHA IS REPORTED BECAUSE THE PASS IS THE ONLY THING THAT KNOWS IT. `source_commit` on
    # the intake this record eventually causes is that commit, and until ADR-0033 the pass
    # discarded it -- so a reader wanting to know what had been written to the authoring
    # repository had to go and look. Only on a pass that wrote one: a replay names no sha
    # because it published nothing, and printing the checkout's HEAD would be a different
    # claim wearing the same words.
    detail = (
        f"item {record.get('id')} {package.package_id} rev {package.revision} "
        f"status={record.get('status')}"
    )
    if published is not None:
        detail = f"{detail} published {published[:12]}"
    outcomes = [Outcome(subject, pending.number, "proposed" if created else "replayed", detail)]
    outcomes.extend(_superseded(records, package))
    return outcomes


def _repository_pass(
    reader: GitHubReader,
    repository: str,
    packages: dict[tuple[str, str], StandingPackage],
    client: ChangeManagerClient | None,
    records: list[dict[str, Any]],
    root: Path,
    now: datetime,
) -> list[Outcome]:
    try:
        base = default_branch(reader, repository)
        revision = current_rule_revision(reader, repository, base)
    except LedgerError as error:
        return [Outcome(repository, 0, "unreadable", str(error))]
    if revision is None:
        # NOT THE SAME ANSWER as an untranscribed gate, and conflating them would report a
        # falsehood. Two repositories in this estate deliberately carry no cascade at all, and
        # on one of those "the cascade refuses this bump" has no meaning -- every bump is
        # outside a lane whose subject is defined by what the cascade declines. Still a
        # finding, because a standing package targeting such a repository was authored for a
        # producer that does not exist.
        return [
            Outcome(
                repository,
                0,
                "no-cascade",
                "this repository has no auto-merge cascade, so which bumps it refuses is not a "
                "question with an answer; a standing package targeting it needs a different "
                "producer",
            )
        ]
    rule = rule_for(revision)
    if rule is None:
        return [
            Outcome(
                repository,
                0,
                "gate-not-transcribed",
                f"the auto-merge gate at blob {revision[:12]} is not transcribed, so which "
                "bumps it refuses is unknown; refusing to guess",
            )
        ]
    try:
        pending_updates = read_pending_updates(reader, repository, base)
    except LedgerError as error:
        return [Outcome(repository, 0, "unreadable", str(error))]

    outcomes: list[Outcome] = []
    for pending in sorted(pending_updates, key=lambda p: p.number):
        package, bump, detail = _consider(pending, rule, packages, now)
        if package is None or bump is None:
            status = "ambiguous" if detail.startswith("ambiguous") else "skipped"
            outcomes.append(Outcome(repository, pending.number, status, detail))
            continue
        try:
            outcomes.extend(_act(package, bump, pending, client, records, root))
        except StandingError as error:
            outcomes.append(Outcome(repository, pending.number, "error", str(error)))
        except ProposalRefused as error:
            outcomes.append(Outcome(repository, pending.number, "refused", str(error)))
        except ChangeManagerError as error:
            outcomes.append(Outcome(repository, pending.number, "error", str(error)))
    return outcomes


def _lane(
    root: Path, *, submit: bool, requested: list[str] | None
) -> tuple[dict[tuple[str, str], StandingPackage], list[str]] | None:
    """The standing packages and the repositories they cover, or None when there is no lane.

    **SCOPE IS THE AUTHORED SET.** A repository is in this lane because somebody authored a
    standing package targeting it, exactly as the deploy producer's scope is the set somebody
    transcribed. There is no list here to edit and none to forget to edit.
    """
    try:
        packages = discover(root)
        if submit:
            require_clean(root)
            # ADR-0033. Both refusals answer "is this checkout one this program may write to?",
            # and they are asked together for that reason: an uncommitted change is somebody
            # else's work this pass would sweep up, and an unpublished commit is this program's
            # own work it failed to finish. Neither is a state a further revision may be built
            # on, and both need a person.
            require_published(root)
    except StandingError as error:
        print(str(error), file=sys.stderr)
        return None

    scope = sorted({package.target_repository for package in packages.values()})
    if requested:
        wanted = {name.lower() for name in requested}
        unknown = wanted - {name.lower() for name in scope}
        if unknown:
            print(f"no standing package targets {sorted(unknown)}", file=sys.stderr)
            return None
        scope = [name for name in scope if name.lower() in wanted]
    if not scope:
        print("no standing packages in the checkout; nothing is in this lane", file=sys.stderr)
        return None
    return packages, scope


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--submit",
        action="store_true",
        help=(
            "actually revise, approve and propose. Without it the pass is a dry run: it "
            "writes nothing, commits nothing and touches no credential that could."
        ),
    )
    parser.add_argument(
        "--repository", action="append", help="limit to one target repository (repeatable)"
    )
    args = parser.parse_args(argv)

    github_token = os.environ.get("BUMP_PROPOSER_GITHUB_TOKEN", "")
    cm_token = os.environ.get("BUMP_PROPOSER_CHANGE_MANAGER_TOKEN", "")
    cm_url = os.environ.get("BUMP_PROPOSER_CHANGE_MANAGER_URL", "")
    if not github_token:
        print("BUMP_PROPOSER_GITHUB_TOKEN is unset", file=sys.stderr)
        return EXIT_UNUSABLE
    if args.submit and not cm_token:
        print("BUMP_PROPOSER_CHANGE_MANAGER_TOKEN is unset; --submit needs it", file=sys.stderr)
        return EXIT_UNUSABLE

    root = checkout_root()
    resolved = _lane(root, submit=args.submit, requested=args.repository)
    if resolved is None:
        return EXIT_UNUSABLE
    packages, scope = resolved

    client = None
    outcomes: list[Outcome] = []
    now = datetime.now(UTC)
    try:
        if args.submit:
            client = ChangeManagerClient(cm_token, base_url=cm_url or DEFAULT_BASE_URL)
        records = client.work_records() if client is not None else []
        with GitHubReader(token=github_token) as reader:
            for repository in scope:
                outcomes.extend(
                    _repository_pass(reader, repository, packages, client, records, root, now)
                )
    except ChangeManagerError as error:
        print(str(error), file=sys.stderr)
        return EXIT_UNUSABLE
    finally:
        if client is not None:
            client.close()

    for outcome in outcomes:
        subject = f"{outcome.repository}#{outcome.number or '-'}"
        print(f"{subject}  {outcome.status:<19} {outcome.detail}")
    findings = [o for o in outcomes if o.status in FINDING_STATUSES]
    proposed = [o for o in outcomes if o.status in {"proposed", "would-advance"}]
    print(f"\n{len(outcomes)} considered, {len(proposed)} to propose, {len(findings)} findings")
    return EXIT_FINDINGS if findings else EXIT_OK


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
