"""Whether the built commit carries a unit's landing, read from a local checkout and never written.

TWO git invocations, both fixed argv and both read-only: `rev-parse --verify` (does the checkout
hold this commit at all) and `merge-base --is-ancestor` (is it in the built commit's history).
Asking the first separately is what keeps "this checkout never fetched that commit" from reading
as an honest "not carried": both are not-carried for the binding, but only one of them is
something the operator can fix with a `git fetch`.

The predicate is reachability of the MERGE COMMIT, which is a real commit on the default branch,
so it genuinely is an ancestor of every build made after it. That is not the squash-merge trap,
which is about a branch tip.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

TIMEOUT_SECONDS = 30

# No prompt, no optional locks, no pager: a read must never wait on a person or contend with a
# concurrent writer in the same checkout.
GIT_ENVIRONMENT = {"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "GIT_PAGER": "cat"}


class AncestryError(RuntimeError):
    """The checkout could not answer. The answer is missing, never negative."""


def _git(checkout: Path, *args: str) -> int:
    try:
        completed = subprocess.run(
            ["git", "-C", str(checkout), *args],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            env={**os.environ, **GIT_ENVIRONMENT},
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise AncestryError(f"git {args[0]} could not run: {type(error).__name__}") from error
    return completed.returncode


def holds_commit(checkout: Path, commit: str) -> bool:
    return _git(checkout, "rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}") == 0


def carries(checkout: Path, merge_commit: str, built_commit: str) -> bool:
    """`merge_commit` is `built_commit` or one of its ancestors. Exit 1 is an honest no."""
    status = _git(checkout, "merge-base", "--is-ancestor", merge_commit, built_commit)
    if status == 0:
        return True
    if status == 1:
        return False
    raise AncestryError(f"git merge-base exited {status}")
