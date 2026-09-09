#!/usr/bin/env python3
"""Refuse a rollout workflow this repository has not yet transcribed.

`src/deploy_watcher/workflows.py` says, per BLOB REVISION, what a green run of another
repository's rollout workflow attests. Keying on bytes is what makes the claim honest -- and it
means the claim expires the moment those bytes move, in a repository this one does not control
and cannot see change.

TWO CONSUMERS BREAK WHEN IT EXPIRES, in opposite registers.

  - `change_proposer/criteria.py` REFUSES: it cannot derive a record's acceptance criteria from
    bytes nobody classified, so the next update-bot pull request for that repository is not
    proposed at all. Loud, but only once something arrives to be refused.
  - `deploy_watcher` DOWNGRADES: `level_of` returns `unknown` rather than guessing, which weakens
    or disables everything keyed on the strong attestation, ADR-0044's clause 3 included. Quiet.

THIS HAS HAPPENED TWICE, and the second time is why the check exists rather than the first.
`brain#47` moved the bytes on 2026-08-14 and every hourly proposer pass refused all five open
brain pull requests until the transcription caught up the next day. `brain#62` moved them again
on 2026-09-08 (`c5c08871` -> `7cf6ca2d`) and NOTHING was red for a day and a half, because no new
brain pull request had arrived to be refused -- it was found on 2026-09-09 only because someone
was reading attestation levels for an unrelated reason. A rule that fires only when a third party
happens to open a pull request is not a check.

The rule itself was already written down: grep every repository for a pinned value before merging
something that moves it. It was written by the person who then broke it. A comment naming a
coupling is not a check; this is the check.

THE PREDICATE IS SHARED, AND THIS SCRIPT IS ONE OF ITS TWO CALLERS.
`deploy_watcher.transcription_currency.audit` holds the comparison and the census guarantee it
rests on; `change_proposer`'s hourly scope sweep asks the same question, of the same population,
at a different clock. It lives there rather than here because two copies of a membership test
would drift into two different answers about the same bytes with nothing comparing them -- and
because a `scripts/` module is not importable from `src/`, so this file could never have been the
home. What stays here is the READING: one `urllib` GET, and the shapes a contents API answers that
are not a file.

FAIL DIRECTION. Exit 1 for a revision the registry does not hold, AND exit 1 for a repository
whose workflow could not be read -- named, on the same line, with the HTTP status or the network
error. An unreadable GitHub is never silence and never a pass: a check that goes quiet when it
cannot read is the permanently-quiet twin of a permanently-red one, and this estate has paid for
both. For the same reason the loop never stops at the first bad repository: a second stale
transcription must not hide behind the first, and neither must a stale one hide behind an
unreadable one. Every repository in `ROLLOUT_WORKFLOWS` is reported on every run.

WHAT IT CANNOT SAY, and it is a real limit rather than an omission. `REGISTRY` is flat -- keyed by
revision, carrying no repository association at all, as `test_change_proposer.py`'s
`_any_transcribed_revision` docstring already records. So a failure can name the repository, the
path, the branch the rollout fires on and the revision now there, but it cannot name "the revision
this repository last knew for you". Giving `Attestation` a repository would fix that; it is a
change to the transcription artifact and is deliberately not made here.

IT LEADS ONE CONSUMER AND ONLY PROSPECTIVELY THE OTHER. `change_proposer` reads the blob at the
workflow's `trigger_branch`, which is exactly what this asks for, so a refusal there is fully
anticipated here. `deploy_watcher` reads the blob at each landing's own merge commit -- any
historical revision -- so a rollout observed against bytes that were current months ago is beyond
what any question about `main` today can see.

Usage:
    python3 scripts/check_rollout_transcription_currency.py

Exit 0: every rollout workflow's current revision is transcribed. Exit 1: at least one is not, or
at least one could not be read.
"""

from __future__ import annotations

import http.client
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from deploy_watcher.transcription_currency import audit
from deploy_watcher.workflows import REGISTRY, ROLLOUT_WORKFLOWS

REGISTRY_SOURCE = "src/deploy_watcher/workflows.py"


class Unresolvable(RuntimeError):
    """The check could not read what it needed to compare. Never a silent pass."""


def read_blob_sha(repository: str, path: str, ref: str) -> str:
    """The git blob sha of one file at one ref, read from GitHub. Read-only, one GET.

    The BLOB sha, not the commit: the registry keys on the bytes of the workflow, so a commit
    that leaves this file alone must not read as a moved transcription.
    """
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/contents/{urllib.parse.quote(path)}"
        f"?ref={urllib.parse.quote(ref, safe='')}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "orchestrator-rollout-transcription-currency/1",
        },
    )
    # Present in Actions; absent locally, where both repositories are public and readable anyway.
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            document = json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        raise Unresolvable(
            f"HTTP {error.code} reading {path} at {repository}@{ref}. The repository must stay "
            "readable, the branch must name a reachable revision, and the file must not have "
            "been moved or renamed -- which the contents API also answers 404 for."
        ) from error
    # `URLError` FIRST: it is an `OSError` subclass, so a broad clause above it would swallow the
    # one that carries a `reason`. The two broad clauses below cover what urllib does NOT wrap --
    # a failure part-way through `response.read()`, which arrives as a bare `TimeoutError` (an
    # OSError) or as `http.client.IncompleteRead` (which is NOT one). Both were escaping.
    except urllib.error.URLError as error:
        raise Unresolvable(f"cannot reach GitHub to read {path}: {error.reason}") from error
    except (ValueError, UnicodeDecodeError) as error:
        raise Unresolvable(f"GitHub's answer for {path} is not readable JSON: {error}") from error
    except (OSError, http.client.HTTPException) as error:
        raise Unresolvable(
            f"the connection reading {path} at {repository}@{ref} failed: {error!r}"
        ) from error

    return blob_sha_of(document, repository, path, ref)


def blob_sha_of(document: object, repository: str, path: str, ref: str) -> str:
    """The `sha` of a contents-API answer, or a refusal naming what was asked for.

    Separate from the fetch so the shapes that are NOT a file can be exercised: a DIRECTORY answers
    with a JSON list, on which `.get` would raise `AttributeError` rather than refuse. `type` is
    what discriminates the rest, and reading it is not fussiness -- a SUBMODULE answers a dict
    whose `sha` is a commit in another repository entirely, and a SYMLINK a blob holding the link
    target. Both carry a well-formed sha that the registry will never hold, so without this gate
    the run is still red and the reason printed is a lie about what was read.
    """
    if isinstance(document, dict) and document.get("type") not in (None, "file"):
        raise Unresolvable(
            f"{path} at {repository}@{ref} is a {document.get('type')!r}, not a file; its sha is "
            "not the bytes of a workflow, so the path has moved."
        )
    sha = document.get("sha") if isinstance(document, dict) else None
    if not isinstance(sha, str) or not sha:
        raise Unresolvable(
            f"GitHub's answer for {path} at {repository}@{ref} carries no blob sha; a directory "
            "or a submodule answers this shape, so the path may have moved."
        )
    return sha


def main() -> int:
    rows = audit(ROLLOUT_WORKFLOWS, REGISTRY, read_blob_sha)

    for row in rows:
        seen = row.revision or "unread"
        verdict = row.problem or "transcribed"
        print(f"{row.repository} {row.path}@{row.ref} -> {seen} :: {verdict}")
    print(f"registry: {REGISTRY_SOURCE} transcribes {len(REGISTRY)} revisions")

    if not rows:
        print(
            f"\nFAIL: {REGISTRY_SOURCE} declares no rollout workflows, so this check compared "
            "nothing. A pass here would assert agreement about an empty set.",
            file=sys.stderr,
        )
        return 1

    bad = [row for row in rows if row.problem is not None]
    if not bad:
        print(f"\nPASS: all {len(rows)} rollout workflows are transcribed at their current bytes.")
        return 0

    print(
        f"\nFAIL: {len(bad)} of {len(rows)} rollout workflows did not confirm a transcribed "
        "revision:\n"
        + "\n".join(
            f"  {row.repository}  {row.path}@{row.ref}  now {row.revision or '?'}  -- {row.problem}"
            for row in bad
        )
        + f"\n\nUntil each is transcribed in {REGISTRY_SOURCE}, that repository's records cannot "
        "have their acceptance criteria derived and its rollouts record an unknown attestation "
        "level. The registry is keyed by revision and records no repository, so it cannot name "
        "which entry has gone stale -- read the comment blocks, which do.\n"
        "A row reading `unreadable` is a failure for a different reason: nothing was compared "
        "for that repository, which is never the same as agreeing.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
