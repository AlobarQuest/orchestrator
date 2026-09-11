"""Does a repository say the factory may be dispatched work in it? (ADR-0015.)

The declaration lives in `factory-target.toml` at the repository root and is
owned by the repository itself -- that is the whole point of ADR-0015, which
rejected a list inside the kit "that the affected repository cannot see". This
module reads it and nothing else.

**IT IS READ FROM GITHUB, NOT FROM THE CHECKOUT, AND THAT IS NOT A PREFERENCE.**
Measured 2026-09-11, hours after the declarations landed: seven of the eight
working trees on this machine did not carry the file at all, because nothing
pulls them. A reader that took the checkout's word would have reported every one
of the five repositories that declare `true` as "has not opted in" -- the answer
inverted for the entire population, and inverted in the direction that looks
like a working refusal. The estate already records the same hazard for
`portfolio.json`, whose nightly producer measures working trees it does not
update; the difference is that a declaration has a fresh source one request
away, so it is read from there.

**THE THREE ANSWERS ARE DECLARED-TRUE, DECLARED-FALSE-OR-ABSENT, AND COULD NOT
TELL.** The third is not a failure to be smoothed over: a rate limit, an absent
credential, a network fault or a file that exists and does not parse are all
states in which this program does not know, and reporting any of them as "has
not opted in" manufactures a refusal out of a fault. `factory_target.py` in
project-standards -- the canonical reader, and the one the conformance kit uses
-- raises on an unreadable file for exactly this reason and leaves each consumer
to name its own "could not tell"; this is ours.

**ABSENCE IS AN ANSWER, and a mistyped repository is indistinguishable from it.**
GitHub answers 404 both for a repository that does not exist and for a file that
is not in one, so `absent` alone cannot tell a repository that never declared
from a slug nobody should have asked about. The report does not need a second
request to separate them: the same repository has to resolve in the estate's
capability data for the other two constraints, so a name nothing knows shows up
there as well, in the same line.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from typing import Final, Protocol

import httpx

API: Final = "https://api.github.com"
FILENAME: Final = "factory-target.toml"
USER_AGENT: Final = "work-carrier/1 (+AlobarQuest/orchestrator)"
TIMEOUT_SECONDS: Final = 30.0
TOKEN_ENV: Final = "WORK_CARRIER_GITHUB_TOKEN"

# ONE route, anchored, the shape `work_carrier.orchestrator_client` already uses
# for its read. The slug is interpolated, so what has to be asserted is a
# TEMPLATE rather than a membership in a fixed set.
#
# **IT IS ALSO THE ONLY CHECK ON THE SLUG, deliberately.** The obvious design is
# two guards -- a `^owner/repo$` regex on the name and this one on the composed
# path -- and the second is then UNFALSIFIABLE, because a name the first admits
# always composes a path the second admits. A clause nothing can falsify sitting
# beside clauses that can is how a test suite comes to report a green it did not
# earn, so there is one predicate, and what it reads is the path the TRANSPORT
# WILL SEND rather than the one this module composed.
#
# **THAT DISTINCTION IS THE WHOLE GUARD, and reading the composed string instead
# is a hole rather than a shortcut.** `.` is a legal character in a repository
# name (`.github`, `foo.bar`), so `[A-Za-z0-9._-]+` matches the segment `..` --
# and `httpx` resolves dot-segments when it builds the request. Measured:
# `../..` composes `/repos/../../contents/factory-target.toml`, which this
# pattern ADMITS, and leaves as `/contents/factory-target.toml`, which it does
# not. So the composed path and the sent path are different strings and only one
# of them is the request; checking the built request closes the class rather than
# the instance. A name carrying a traversal segment, a query, a fragment or a
# second slash all normalise to something this refuses.
_ALLOWED = re.compile(rf"^/repos/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+/contents/{re.escape(FILENAME)}$")


def is_allowed(path: str) -> bool:
    return bool(_ALLOWED.fullmatch(path))


@dataclass(frozen=True)
class Declaration:
    """A repository's answer, or the reason there is not one.

    `target` is `None` when nothing could be read -- never `False`. The two are
    different answers with opposite remedies: `False` needs a person to change
    their mind, and `None` needs somebody to find out why the read failed.
    """

    target: bool | None
    detail: str


class DeclarationSource(Protocol):
    def declaration(self, slug: str) -> Declaration: ...


def parse(text: str) -> Declaration:
    """The declaration in one file's bytes, or why it is not one.

    The two required keys and their types are project-standards'
    `factory_target.factory_target_declaration`, restated rather than imported:
    this is a separate program (ADR-0002's shape) and importing a conformance
    kit would be the first dependency of its kind. Restating carries the usual
    cost of a second copy, and it is bounded -- two key names and two types, in
    a file whose whole job is to answer one question -- and the divergence that
    matters is caught the first time a repository's declaration reads one way
    here and another in the kit's own report.

    A REASON IS REQUIRED ON BOTH ANSWERS, for the reason the canonical reader
    gives: a bare `factory_target = true` becomes folklore.
    """
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError) as error:
        return Declaration(None, f"{FILENAME} exists and cannot be read: {error}")
    target = data.get("factory_target")
    if not isinstance(target, bool):
        return Declaration(
            None, f"{FILENAME} must declare `factory_target` as a bool, got {target!r}"
        )
    reason = data.get("factory_target_reason")
    if not isinstance(reason, str) or not reason.strip():
        return Declaration(
            None,
            f"{FILENAME} must declare `factory_target_reason` as a non-empty string, "
            f"got {reason!r} -- a bare `factory_target = {str(target).lower()}` becomes folklore",
        )
    return Declaration(target, f"{FILENAME} declares factory_target = {str(target).lower()}")


class GitHubDeclarationSource:
    """`factory-target.toml` on a repository's default branch. READ-ONLY by shape.

    One method, one route, and the route is checked before the request leaves --
    so a slug that composed a path to somewhere else never reaches the transport.
    The default branch is whatever GitHub serves for a `contents` request with no
    `ref`, which is the branch a dispatch would land on.
    """

    def __init__(
        self,
        *,
        token: str,
        base_url: str = API,
        timeout_seconds: float = TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {token}",
                # The raw representation rather than the JSON envelope, so there
                # is no base64 hop and no second shape to get wrong.
                "Accept": "application/vnd.github.raw",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": USER_AGENT,
            },
            timeout=timeout_seconds,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GitHubDeclarationSource:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def declaration(self, slug: str) -> Declaration:
        """Total: it returns a `Declaration`, including for every failure.

        A repository name this program may not compose a route from is a refusal
        it RETURNS rather than raises -- unlike its sibling clients, whose paths
        are composed from ids the program itself minted. This one's name is
        authored text in somebody's `package.yaml`, so a bad one is an ordinary
        finding about one record and must not take the pass down with it.
        """
        path = f"/repos/{slug}/contents/{FILENAME}"
        try:
            # BUILD IT, THEN JUDGE WHAT WAS BUILT. The request is what reaches
            # the network, and its path is not always the string composed above
            # -- see the note on `_ALLOWED`. Nothing has left this process here:
            # `build_request` composes and raises, and a name that cannot even be
            # composed into a URL is a name this program may not ask about, which
            # is why the refusal below is worded as that and not as a fault of
            # GitHub's. Measured: a control character in the slug raises
            # `InvalidURL`, which is not an `HTTPError`.
            request = self._client.build_request("GET", path)
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
            return Declaration(
                None,
                f"{slug!r} is not a repository name this program may ask about; it reads "
                f"one route, /repos/<owner>/<repo>/contents/{FILENAME} "
                f"({type(error).__name__})",
            )
        if not is_allowed(request.url.path):
            return Declaration(
                None,
                f"{slug!r} is not a repository name this program may ask about; it reads "
                f"one route, /repos/<owner>/<repo>/contents/{FILENAME}",
            )
        try:
            response = self._client.send(request)
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
            # Three families, because `httpx` raises three: an `InvalidURL` and
            # an `HTTPError` are the documented two, and IDNA encoding of a
            # malformed host raises `UnicodeError`, which is a `ValueError` and
            # neither of the others. The type name only -- an exception from a
            # client carries the request, and a diagnostic that prints what it
            # was given is how a credential reaches a transcript.
            return Declaration(None, f"github is unreachable for {slug}: {type(error).__name__}")
        if response.status_code == 404:
            # ABSENCE IS AN ANSWER (ADR-0015): a repository that has said nothing
            # has not opted in. TWO OTHER STATES ANSWER IDENTICALLY and the
            # detail names both rather than hiding them, because this is the one
            # branch that returns a definite `False` and a definite `False`
            # decides through every UNKNOWN beside it: GitHub answers 404 for a
            # repository that does not exist, and 404 rather than 403 for a
            # private one this credential may not read. The first is a typo and
            # shows up again as a lookup miss in the capability data; the second
            # would be a refusal manufactured from a permission fault, so a
            # reader who sees this line on a repository he believes exists should
            # check the credential's reach before believing the verdict.
            return Declaration(
                False,
                f"no {FILENAME} on {slug}'s default branch, so it has not opted in "
                "-- a repository that does not exist, and a private one this credential "
                "may not read, both answer the same way",
            )
        if response.status_code >= 400:
            return Declaration(
                None, f"github rejected the read of {slug}'s {FILENAME}: {response.status_code}"
            )
        try:
            text = response.text
        except (UnicodeDecodeError, httpx.HTTPError) as error:
            return Declaration(None, f"{slug}'s {FILENAME} is not readable text: {error}")
        return parse(text)


def from_environment(
    *, base_url: str = API, transport: httpx.BaseTransport | None = None
) -> GitHubDeclarationSource | None:
    """The reader this machine can build, or `None` when it has no credential.

    `None` is not an error here. The bare invocation of this lane is advertised
    to work on any machine, including one with no GitHub credential, and every
    repository then reports "could not tell" -- which is the honest answer and
    not a refusal.
    """
    token = os.environ.get(TOKEN_ENV, "")
    if not token:
        return None
    return GitHubDeclarationSource(token=token, base_url=base_url, transport=transport)
