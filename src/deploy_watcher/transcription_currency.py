"""Is each rollout workflow's CURRENT revision one `workflows.REGISTRY` transcribes?

ONE PREDICATE, TWO CALLERS, AND THE POINT IS THAT THEY ASK AT DIFFERENT CLOCKS.

`REGISTRY` says, per BLOB REVISION, what a green run of another repository's rollout workflow
attests. Keying on bytes is what makes the claim honest, and it means the claim expires the moment
those bytes move -- in a repository this one does not control and cannot see change. The question
"has that happened yet" has two consumers who need it answered at different moments:

  - `scripts/check_rollout_transcription_currency.py`, at PULL-REQUEST time in this repository. It
    refuses a change here that leaves a transcription stale, which is the right clock for a change
    somebody is making.
  - `change_proposer`'s scope sweep, HOURLY. That is the right clock for the failure itself, which
    is caused by a merge in another repository and arrives on nobody's schedule. `brain#62` moved
    the bytes on 2026-09-08 and nothing was red for 27 hours, because a rule that fires only when a
    third party opens a pull request is not a check.

The two must not diverge, so the predicate lives here rather than in either of them: this
estate's standing lesson is that N copies of a value is a lower bound on the copies you will find,
and two copies of a membership test would drift into two different answers about the same bytes
with nothing comparing them.

WHAT IS HERE AND WHAT IS NOT. The reading is over the network and the reader is injected, so this
module imports no HTTP client and each caller brings its own -- the CI script a plain `urllib`
GET, the proposer the `GitHubReader` it already holds for everything else. Everything this module
DECIDES with is pure, which is also what makes it testable without a transport.

THREE ANSWERS, NOT TWO. Transcribed; not transcribed; and could not be read. The third is never
folded into either of the others: a repository whose bytes could not be read was not compared, and
nothing that was not compared may be reported as agreeing. Both callers treat it as a failure,
for the same reason and by their own vocabularies.
"""

from __future__ import annotations

from collections.abc import Callable, Container, Mapping
from dataclasses import dataclass

from deploy_watcher.workflows import RolloutWorkflow

# The two verdicts a row can carry, named once. `problem is None` is the third.
NOT_TRANSCRIBED = "not transcribed"
UNREADABLE_PREFIX = "unreadable: "


@dataclass(frozen=True)
class Row:
    """One repository's verdict. `problem` is None exactly when its rollout is transcribed."""

    repository: str
    path: str
    ref: str
    revision: str | None
    problem: str | None


def audit(
    workflows: Mapping[str, RolloutWorkflow],
    registry: Container[str],
    read: Callable[[str, str, str], str],
) -> list[Row]:
    """One row per repository, in a stable order, whatever goes wrong on the way.

    Pure apart from `read`, which is the only thing that touches the network. Every failure is a
    row rather than a raise: the point of reporting all of them is defeated by an early exit, and
    an early exit is what a raise here would be. A second stale transcription must not hide behind
    the first, and neither must a stale one hide behind an unreadable one.

    `Exception`, not a named type, and that width is the point rather than laziness. The census
    guarantee is a property of THIS loop; making it depend on the reader raising the right type
    means every future reader silently owns it too, and the first one to leak a `TimeoutError` out
    of a body read -- or the `ValueError` an IDNA-malformed host raises from `httpx`, which is
    neither an `HTTPError` nor an `InvalidURL` -- takes the guarantee away with nothing saying so.
    Nothing is swallowed: the row carries the exception's repr and every caller reports it.
    """
    rows: list[Row] = []
    for repository in sorted(workflows):
        workflow = workflows[repository]
        try:
            revision = read(repository, workflow.path, workflow.trigger_branch)
        except Exception as error:
            branch = workflow.trigger_branch
            rows.append(Row(repository, workflow.path, branch, None, f"{UNREADABLE_PREFIX}{error}"))
            continue
        problem = None if revision in registry else NOT_TRANSCRIBED
        rows.append(Row(repository, workflow.path, workflow.trigger_branch, revision, problem))
    return rows
