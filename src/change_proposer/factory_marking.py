"""Recognise the marking the factory stamps on every pull request it opens.

`_consider` refuses a pull request whose author is a user account, because a human's pull
request is a human's to merge (ADR-0019). factory-runner opens its pull requests with
`FACTORY_PR_TOKEN`, a fine-grained PAT on a USER account, so GitHub reports `type: "User"` and
every factory pull request was refused as human-authored. That filter is not wrong and is not
loosened: a factory pull request is a THIRD case beside a human's and an update bot's, and this
module is how that case is told apart.

**WHY THE TITLE.** factory-runner stamps two unconditional marks -- the title
`f"SDS {work_unit.id}: {work_unit.title}"` and a `## Factory Runner Evidence` body naming the
unit. The title is the one this program can trust, for the reason `update_type_of` already
records one repository over: the estate measured that when a pull request is rewritten in place
the title is the field that tracks the change while the branch ref and the head commit's own
trailers go stale. It is also the field the producer already treats as authoritative.

**FORGERY IS NOT THE THREAT MODEL.** In a single-operator estate a spurious record is a record
awaiting a decision -- something to learn from, not a risk to design against -- and the
alternative (the orchestrator proposing the record itself) would split creation of deploy change
records across two systems that must then agree on the join key, the record shape and which
repositories redeploy. That is the shape of every vocabulary-drift incident this estate has
recorded. One producer.

**THE FORMAT IS FACTORY-RUNNER'S AND NOTHING IN THIS REPOSITORY HOLDS IT.** If it moves, this
recogniser silently stops matching and the lane dies with no signal. That is why
`scripts/check_pr_title_marking_compatibility.py` reads the consumer's own source at the pinned
and recommended revisions and refuses a divergence -- read its docstring for exactly when that
check runs, because it is a consumer-side pin on a producer-side convention.
"""

from __future__ import annotations

import re

from deploy_watcher.orchestrator import WORK_UNIT_ID

# What a factory title begins with, before the identifier. Used to say something USEFUL about a
# title that carries the prefix and no readable unit -- which is what a format drift looks like
# from here -- rather than reporting it as an ordinary human's pull request and nothing more.
FACTORY_TITLE_PREFIX = "SDS "

# Anchored at the very start and requiring the colon, with no tolerance for leading whitespace:
# `gh pr create --title` passes the string through exactly, so anything before the marking means
# the format has moved, and refusing is the polarity this lane argues for everywhere. The
# identifier is `WORK_UNIT_ID`'s lower-case hexadecimal, matching how the estate's two other
# readers of a unit claim spell it rather than inventing a third.
_FACTORY_TITLE = re.compile(rf"^{re.escape(FACTORY_TITLE_PREFIX)}({WORK_UNIT_ID}):")


def factory_unit_id(title: object) -> str | None:
    """The work unit a pull request title says the factory opened it for, or None.

    None for every other pull request in the estate, which is almost all of them -- and None,
    deliberately, for a title that carries the prefix and no well-formed identifier. Such a title
    is REFUSED rather than proposed with an absent unit: a record that says the factory opened it
    and cannot say for what is worse than no record, because a later reader would hold the deploy
    to it.

    **TAKES `object`, AND THE TYPE GUARD IS NOT DECORATION.** `open_pull_requests` projects
    `"title": item.get("title")` straight from the response -- it guards the two nested objects it
    reads and not the scalars -- so a malformed body puts whatever it likes here. `re.match` on a
    non-string raises `TypeError`, which `_pass` does not catch (it catches `ReadError`), so the
    whole scheduled run would die with a traceback rather than reporting a finding. That is the
    escape family this estate has already paid for twice, in a third place.
    """
    if not isinstance(title, str) or not title:
        return None
    match = _FACTORY_TITLE.match(title)
    return match.group(1) if match else None


__all__ = ["FACTORY_TITLE_PREFIX", "factory_unit_id"]
