"""The two facts only the machine has: does this working copy hold that commit, and what is in it.

ADR-0030's unit-caused lane. A release artifact binding for a machine-local target asserts that
the change a work unit produced is present in a working copy on this machine, and names a content
digest over what that copy holds. Both halves are measured here, by the same read-only git surface
`checkout.py` already enforces.

THE PREDICATE IS REACHABILITY, NOT EQUALITY. `HEAD == merge_commit` would be true for about as
long as it takes the next thing to land, and would report a machine that is perfectly up to date
as never having activated anything. What must be true is that the merge commit is IN the history
the working copy holds, which is what `git merge-base --is-ancestor` answers.

**READ THE NEXT PARAGRAPH BEFORE REACHING FOR THAT COMMAND ANYWHERE ELSE.** This estate's standing
rule is that `--is-ancestor` is the WRONG tool for "did this branch land": squash-merge collapses a
branch into one new commit, so a branch's own commits are never ancestors of the default branch
even when their content is fully landed, and `git branch -d` refuses those branches for exactly
that reason. That rule is about a BRANCH TIP. This asks about the MERGE COMMIT, which is a real
commit on the default branch, so it genuinely is an ancestor of anything pulled after it.
Measured 2026-08-24 in `~/Projects/infraops-mcp-server`: the landing commit of pull request #81
IS an ancestor of HEAD, and that pull request's own head sha is NOT -- the same checkout answering
both ways, which is why the distinction has to be stated rather than assumed.

THE DIGEST IS OVER `HEAD`, NOT OVER THE MERGE COMMIT, and that is ADR-0030's decision rather than
this module's. The artifact IS the activated working copy: what the next start executes is HEAD,
not the commit that happened to introduce this unit's change. Two consequences worth knowing.
`git archive` is stable on one machine but not guaranteed across git versions, so a digest
compared across machines is out of scope; and because HEAD moves, a unit's binding must be written
ONCE, at the first pass that finds its commit activated -- which is why the orchestrator reports an
existing binding and this lane skips rather than rewrites.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from activation_sweep.checkout import (
    GIT_ENVIRONMENT,
    TIMEOUT_SECONDS,
    GitError,
    run_git,
)

# The prefix `services/release_artifacts.py::_validate_digests` requires. `shasum` and `hashlib`
# both emit bare hex, and a digest without this is refused as "not an immutable sha256 digest" --
# a validator this lane deliberately does not relax.
DIGEST_PREFIX = "sha256:"

# `git archive` writes a tar to stdout, so it is read as BYTES and streamed rather than decoded.
# It is not on `checkout.READ_ONLY`, which is a text-only surface, so it is run here with the same
# environment and the same refusal to inherit anything else -- see `content_digest`.
ARCHIVE = "archive"

_ARCHIVE_CHUNK = 1 << 20


class BindingError(GitError):
    """This working copy could not answer. The answer is missing, never negative."""


@dataclass(frozen=True)
class Activation:
    """What one working copy says about one unit's landing commit."""

    activated: bool
    head: str
    digest: str


def has_activated(path: Path, commit: str) -> bool:
    """Whether `commit` is in the history this working copy holds.

    A commit the checkout has never fetched makes `merge-base` exit non-zero in the same way an
    honest "no" does, so both are reported as not activated. That is the correct collapse here:
    either way the machine is not running this change, and the pass says WAITING rather than
    finding anything.
    """
    try:
        run_git(path, "merge-base", "--is-ancestor", commit, "HEAD")
    except GitError:
        return False
    return True


def content_digest(path: Path) -> str:
    """`git archive HEAD`, hashed. The digest ADR-0030 named, with the prefix the table requires.

    Run directly rather than through `run_git` because that helper decodes stdout as text and a
    tar stream is not text. The subcommand is still read-only in the sense that matters -- it
    writes nothing to the repository, touches neither HEAD nor the index nor a tracked file -- and
    it carries the same prompt-free, lock-free environment for the same reason.
    """
    resolved = path.expanduser().resolve()
    digest = hashlib.sha256()
    try:
        with subprocess.Popen(
            ["git", "-C", str(resolved), ARCHIVE, "HEAD"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={**os.environ, **GIT_ENVIRONMENT},
        ) as process:
            stream = process.stdout
            if stream is None:  # pragma: no cover - stdout=PIPE always gives one
                raise BindingError(f"git {ARCHIVE} produced no output stream in {resolved}")
            for chunk in iter(lambda: stream.read(_ARCHIVE_CHUNK), b""):
                digest.update(chunk)
            status = process.wait(timeout=TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as error:
        raise BindingError(
            f"git {ARCHIVE} could not run in {resolved}: {type(error).__name__}"
        ) from error
    if status != 0:
        raise BindingError(f"git {ARCHIVE} exited {status} in {resolved}")
    return DIGEST_PREFIX + digest.hexdigest()
