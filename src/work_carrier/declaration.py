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

**ABSENCE IS AN ANSWER, AND IT IS CONFIRMED RATHER THAN BELIEVED.** GitHub
answers 404 for a file that is not there, for a repository that does not exist,
and for a private one this credential may not read -- three states behind one
status code, and this is the only branch that returns a definite `False`. A
definite `False` decides through every UNKNOWN beside it, so mistaking one of
the other two for it is not a vaguer answer: it is a refusal manufactured from a
typo or a permission fault. A 404 is therefore followed by ONE read of the
repository itself, and only a repository that answers lets the absence stand.

That is project-standards' `onboard_checks._remote_contents`, whose docstring
states the same rule; it is restated here rather than shared, because the two
have different transports -- it shells `gh`, this speaks `httpx` -- and a module
across that boundary is not on the table. What must agree is the semantics, and
the ORDERING is the half worth naming in both: the contents failure is tested
for 404 FIRST, so a transient fault cannot be turned into an absence by a
repository probe that happens to succeed a moment later.

**THE ARGUMENT THIS REPLACED WAS SOUND WHILE THE ANSWER WAS ONLY REPORTED.** It
ran: no second request is needed, because the same repository has to resolve in
the estate's capability data for the other two constraints, so a name nothing
knows shows up again in the same line. That is true of a LINE a person reads
whole, and it stops holding the moment the answer decides anything by itself --
the refusal then fires on this field alone and says "it has not opted in" when
the truth may be "we cannot see this repository". The same decision with the
wrong diagnosis, at the moment the diagnosis is the whole output.
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
# The media type for the REPOSITORY route, named here because the client's own
# `Accept` was chosen for a different one. Measured 2026-09-12: `/repos/{slug}`
# answers 200 under the raw contents type as well, so this changes no behaviour
# today -- it is here so that a media type picked for the contents route does not
# silently govern the probe, where a 415 would turn every genuine absence into
# "could not tell".
REPOSITORY_ACCEPT: Final = "application/vnd.github+json"

# THE ONE ROUTE COMPOSED FROM A REPOSITORY NAME, anchored, the shape
# `work_carrier.orchestrator_client` already uses for its read. The slug is
# interpolated, so what has to be asserted is a TEMPLATE rather than a membership
# in a fixed set. There is a second route -- the repository itself, read when
# this one answers 404 -- and it is DERIVED from the path this pattern has
# already admitted rather than composed afresh, so this stays the only place a
# name a human typed is judged; see `_confirm_absence`.
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


def _unanswered(slug: str, status: int) -> str:
    """Why a repository's own read did not answer -- which is NOT one thing.

    The verdict is the same for every branch here: nothing was confirmed, so
    nothing is absent. Only the SENTENCE differs, and the sentence is the whole
    reason this module grew a second request. Reporting a `429` or a `502` as
    "the repository does not exist" sends a reader to check a name that is fine --
    the same wrong-diagnosis defect one branch over from where it was fixed.
    """
    if status == 404:
        return (
            f"{slug} itself is not there either, so this is a repository that does not exist "
            "or one this credential cannot see, and not one that has not opted in"
        )
    if status == 429 or status >= 500:
        return (
            f"{slug} itself answered {status}, which is transient -- this pass could not tell "
            "and a later one may, so nothing is absent"
        )
    if 300 <= status < 400:
        return (
            f"{slug} itself answered {status}, so it appears to have been renamed or moved and "
            "this reader does not follow that, so nothing is absent"
        )
    return (
        f"{slug} itself answered {status}, which is not an answer either way, so nothing is absent"
    )


class GitHubDeclarationSource:
    """`factory-target.toml` on a repository's default branch. READ-ONLY by shape.

    One method and TWO routes -- the declaration's, and the repository beneath it
    when the first answers 404 -- of which only the first is composed from a name
    this program was given. That one is checked before the request leaves, so a
    slug that composed a path to somewhere else never reaches the transport, and
    the second is derived from it afterwards (`_confirm_absence`). The default
    branch is whatever GitHub serves for a `contents` request with no `ref`,
    which is the branch a change would land on.
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
                f"{slug!r} is not a repository name this program may ask about; the one "
                f"route it composes from a name is /repos/<owner>/<repo>/contents/{FILENAME} "
                f"({type(error).__name__})",
            )
        if not is_allowed(request.url.path):
            return Declaration(
                None,
                f"{slug!r} is not a repository name this program may ask about; the one "
                f"route it composes from a name is /repos/<owner>/<repo>/contents/{FILENAME}",
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
            # has not opted in. TWO OTHER STATES ANSWER IDENTICALLY -- a
            # repository that does not exist, and a private one this credential
            # may not read, which GitHub answers 404 rather than 403 -- so the
            # absence is confirmed against the repository itself before it is
            # believed. **THE ORDER IS THE RULE.** This branch is reached only
            # after a contents request that SUCCEEDED and said 404: a transport
            # fault returned above and every other status returns below, so no
            # transient failure can be turned into an absence by a probe that
            # happens to answer a moment later.
            return self._confirm_absence(slug, request.url.path)
        if 300 <= response.status_code < 400:
            # A RENAMED REPOSITORY, and it used to fall past both branches into
            # `parse`. Redirects are not followed, so what arrives is GitHub's
            # redirect envelope rather than the file -- which came out as
            # "`factory_target` must be a bool, got None", a sentence asserting
            # that the file is there and malformed about a repository that has
            # merely moved. The verdict was already "could not tell"; this makes
            # the diagnosis true as well.
            return Declaration(
                None,
                f"{slug}'s {FILENAME} answered {response.status_code}: the repository appears "
                "to have been renamed or moved, and this reader does not follow that",
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

    def _confirm_absence(self, slug: str, contents_path: str) -> Declaration:
        """A contents 404, read again against the repository itself.

        `False` when the repository answers -- the file is genuinely not on its
        default branch, so it has said nothing and has not opted in. `None` when
        the repository does not answer, because then the 404 was about the NAME
        and not about the file: a slug nobody should have asked about, or one
        this credential cannot see, neither of which is a repository declining to
        be a target.

        **THE PROBE'S PATH IS DERIVED FROM THE PATH THAT WAS JUDGED, never
        composed from the slug a second time, and that is the guard rather than a
        convenience.** `is_allowed` fullmatched the built contents path against
        `^/repos/SEG/SEG/contents/FILENAME$`, so removing that suffix leaves
        `/repos/SEG/SEG` by arithmetic -- there is nothing left to check, and a
        second pattern here would be a clause nothing can falsify sitting beside
        one that can. Composing `/repos/{slug}` afresh would NOT be equivalent
        and the difference is measurable: a slug of `o/r?x=1` builds a contents
        request whose path is `/repos/o/r`, which the guard above refuses, while
        the same slug on the repository route builds `/repos/o/r` with the rest
        in the QUERY -- a path-only check admits it. So the one route already
        checked is the one this reuses.
        """
        try:
            probe = self._client.send(
                self._client.build_request(
                    "GET",
                    contents_path.removesuffix(f"/contents/{FILENAME}"),
                    headers={"Accept": REPOSITORY_ACCEPT},
                )
            )
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
            # The type name only, for the reason given one branch up.
            return Declaration(
                None,
                f"no {FILENAME} on {slug}'s default branch, and {slug} itself could not "
                f"be read either ({type(error).__name__}), so this is not an absence",
            )
        if not 200 <= probe.status_code < 300:
            # STRICTER THAN THE KIT, deliberately and in the fail-closed
            # direction: `gh` follows redirects, so a renamed repository succeeds
            # there and is "could not tell" here. Narrowing the one branch that
            # returns a definite `False` costs a held record, where the
            # alternative costs a manufactured refusal.
            why = _unanswered(slug, probe.status_code)
            return Declaration(None, f"no {FILENAME} on {slug}'s default branch, and {why}")
        # WHAT THIS DOES NOT PROVE IS NAMED IN THE LINE, because the line is what
        # a person acts on. Metadata and contents are separate permissions, so a
        # credential holding the first and not the second answers 200 here and
        # 404 above for a PRIVATE repository -- the one state still
        # indistinguishable from an absence. It is left that way ON PURPOSE: the
        # kit confirms the same fact by the same request, and a reader that
        # quietly grew a third probe would be the two drifting rather than the
        # hole closing. Whoever wants it closed should close it in both.
        return Declaration(
            False,
            f"no {FILENAME} on {slug}'s default branch, so it has not opted in -- {slug} "
            "itself answers, so this is not a name nothing knows; what it does NOT prove is "
            "that this credential may read the repository's CONTENTS, which is a separate "
            "permission from its metadata and answers exactly this way when it is missing",
        )


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
