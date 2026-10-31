"""Reading `factory-target.toml`, the file `membership_check` compares with the record.

Moved from `tests/work_carrier` when the carrier stopped reading the file (bridge layer Part 2).
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

from membership_check.declaration import (
    _ALLOWED,
    FILENAME,
    GitHubDeclarationSource,
    is_allowed,
    parse,
)
from work_carrier.change_manager import is_allowed as is_allowed_change_manager
from work_carrier.orchestrator_client import is_allowed_read, is_allowed_write

# -------------------------------------------------------------------------------------
# Reading the declaration itself
# -------------------------------------------------------------------------------------


def test_a_declaration_is_read_as_the_repositorys_own_answer() -> None:
    answer = parse('factory_target = true\nfactory_target_reason = "a target"\n')
    assert answer.target is True


def test_a_declaration_that_says_false_is_a_different_answer_from_one_that_cannot_be_read() -> None:
    assert parse('factory_target = false\nfactory_target_reason = "no"\n').target is False
    assert parse("factory_target = not-toml [[[").target is None


@pytest.mark.parametrize(
    "text",
    [
        "",
        'factory_target = "true"\nfactory_target_reason = "x"\n',
        "factory_target = true\n",
        'factory_target = true\nfactory_target_reason = "   "\n',
    ],
)
def test_a_declaration_missing_either_key_or_carrying_the_wrong_type_cannot_be_read(
    text: str,
) -> None:
    """Both keys are required, including the reason: a bare `true` becomes folklore."""
    assert parse(text).target is None


def _routed(
    contents: httpx.Response | Exception, repository: httpx.Response | Exception
) -> tuple[httpx.MockTransport, list[str]]:
    """A transport that answers the two routes differently, and records what was asked.

    The two are told apart by PATH rather than by call order, so a test cannot pass
    because the reader happened to make the requests in the order the stub expected --
    and the recorded list is what makes "the probe was never made" assertable.
    """

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        answer = repository if request.url.path.endswith("/brain") else contents
        if isinstance(answer, Exception):
            raise answer
        return answer

    return httpx.MockTransport(handler), seen


def _reader(transport: httpx.MockTransport) -> GitHubDeclarationSource:
    return GitHubDeclarationSource(
        token="t", base_url="https://example.invalid", transport=transport
    )


def test_an_absent_declaration_is_an_answer_once_the_repository_itself_answers() -> None:
    """GitHub answers 404 three ways, and this is the one branch that returns a definite NO.

    A definite NO decides through every UNKNOWN beside it, so the two states that are
    otherwise indistinguishable from "has not opted in" -- a repository that does not
    exist, and a private one this credential may not read -- are settled by one read of
    the repository itself rather than named and left to the reader. This is the case
    where that read ANSWERS, so the absence is the repository's own silence.
    """

    transport, seen = _routed(httpx.Response(404, json={}), httpx.Response(200, json={}))
    answer = _reader(transport).declaration("AlobarQuest/brain")
    assert answer.target is False
    assert "no factory-target.toml on" in answer.detail
    assert seen == [
        "/repos/AlobarQuest/brain/contents/factory-target.toml",
        "/repos/AlobarQuest/brain",
    ]


@pytest.mark.parametrize(
    ("repository_status", "expected"),
    [
        (404, "does not exist or one this credential cannot see"),
        (403, "not an answer either way"),
        (429, "transient"),
        (500, "transient"),
        (502, "transient"),
        (301, "renamed or moved"),
    ],
)
def test_an_absence_the_repository_does_not_confirm_is_could_not_tell(
    repository_status: int, expected: str
) -> None:
    """The behaviour change, and the whole reason the probe exists.

    A slug nobody should have asked about and a private repository this credential
    cannot see both 404 on the contents route, and both used to come back as a definite
    "has not opted in" -- a refusal manufactured from a typo or a permission fault. The
    repository does not answer, so there is no absence to report.

    **THE VERDICT IS ONE THING AND THE DIAGNOSIS IS SEVERAL, and the parametrization
    says so rather than pinning one sentence over all of them.** Every row answers
    `None`; a `429` or a `502` is this estate's ordinary weather, and telling a reader
    the repository does not exist would send him to check a name that is fine. That is
    the same wrong-diagnosis defect this module exists to remove, one branch over.
    """
    transport, seen = _routed(
        httpx.Response(404, json={}), httpx.Response(repository_status, json={})
    )
    answer = _reader(transport).declaration("AlobarQuest/brain")
    assert answer.target is None
    assert expected in answer.detail
    assert len(seen) == 2


def test_a_confirmed_absence_does_not_claim_the_credential_could_have_read_the_file() -> None:
    """The one state the probe still cannot tell from an absence, named in the line.

    `GET /repos/{slug}` needs only metadata; the contents route needs contents, and they
    are separate permissions -- so a credential holding the first and not the second
    answers 200 to the probe and 404 above for a PRIVATE repository, which is exactly the
    shape of a confirmed absence. It is left that way deliberately: project-standards'
    `_remote_contents` confirms the same fact by the same request, and a reader that grew
    a third probe on its own would be the two drifting rather than the hole closing. What
    must not happen is the LINE claiming more than was proved.
    """
    transport, _ = _routed(httpx.Response(404, json={}), httpx.Response(200, json={}))
    detail = _reader(transport).declaration("AlobarQuest/brain").detail
    assert "does NOT prove" in detail
    assert "CONTENTS" in detail


def test_a_redirect_on_the_declaration_is_a_rename_rather_than_a_malformed_file() -> None:
    """A renamed repository used to be reported as a file that exists and is broken.

    Redirects are not followed, so a 301 delivers GitHub's redirect envelope rather than
    the file -- and it fell past both the 404 branch and the >= 400 branch into the
    parser, which said `factory_target` must be a bool and got None. The verdict was
    already "could not tell"; this asserts the sentence is true as well, and that the
    reader did not go on to probe a repository it had been told nothing about.
    """
    transport, seen = _routed(httpx.Response(301, json={}), httpx.Response(200, json={}))
    answer = _reader(transport).declaration("AlobarQuest/brain")
    assert answer.target is None
    assert "renamed or moved" in answer.detail
    assert "factory_target" not in answer.detail
    assert seen == ["/repos/AlobarQuest/brain/contents/factory-target.toml"]


def test_the_probe_can_derive_its_path_only_because_the_guard_ends_at_the_file() -> None:
    """The coupling that makes the derivation safe, asserted rather than assumed.

    `_confirm_absence` strips `/contents/<file>` off a path `is_allowed` has admitted,
    and `str.removesuffix` is a NO-OP when the suffix is absent -- so widening the route
    guard past the file (a nested path, an optional directory segment) would silently
    make the probe re-ask the contents route or ask for a directory, turning every
    genuine absence into "could not tell" with nothing red. This reds on that edit, which
    is what a second unfalsifiable pattern beside the first could not do.
    """
    assert _ALLOWED.pattern.endswith(re.escape(f"/contents/{FILENAME}") + "$")


def test_an_unreachable_repository_probe_is_could_not_tell_and_prints_no_request() -> None:
    """The probe's own failure is the third state, not the absence by default."""

    transport, _ = _routed(httpx.Response(404, json={}), httpx.ConnectError("no route"))
    answer = _reader(transport).declaration("AlobarQuest/brain")
    assert answer.target is None
    assert "ConnectError" in answer.detail
    assert "no route" not in answer.detail


@pytest.mark.parametrize(
    "contents",
    [
        pytest.param(None, id="transport-raised"),
        pytest.param(500, id="server-error"),
        pytest.param(429, id="rate-limited"),
    ],
)
def test_a_transient_contents_failure_never_becomes_an_absence(contents: int | None) -> None:
    """THE ORDERING RULE, and it is asserted by the probe NOT HAVING BEEN MADE.

    project-standards' `_remote_contents` tests its diagnostic for 404 FIRST for this
    reason: a contents call that failed for any other reason says nothing about whether
    the file is there, and a repository probe that succeeds a moment later must not be
    allowed to turn it into an absence. The repository here would answer 200 -- so a
    reader that probed first, or probed on every failure, would report `False`.
    """

    failure = httpx.ConnectError("no route") if contents is None else httpx.Response(contents)
    transport, seen = _routed(failure, httpx.Response(200, json={}))
    answer = _reader(transport).declaration("AlobarQuest/brain")
    assert answer.target is None
    assert seen == ["/repos/AlobarQuest/brain/contents/factory-target.toml"]


def test_the_probe_asks_the_bare_repository_route_and_nothing_else() -> None:
    """What the probe sends, and that a refused slug sends nothing at all.

    It does NOT discriminate the derivation, and saying so is the point: behind the
    route guard, composing `/repos/{slug}` afresh would build the same request for every
    slug that can reach the probe, so a control claiming otherwise would pass either way.
    The derivation is chosen so that argument never has to be made -- on the repository
    route a slug carrying a query builds an admissible PATH with the rest in the query,
    which a path-only check admits, and the contents route being checked first is the
    only reason that cannot happen here.
    """

    transport, seen = _routed(httpx.Response(404, json={}), httpx.Response(404, json={}))
    assert _reader(transport).declaration("AlobarQuest/brain?ref=other").target is None
    assert seen == []

    asked: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        return httpx.Response(404 if "contents" in request.url.path else 200, json={})

    assert _reader(httpx.MockTransport(handler)).declaration("AlobarQuest/brain").target is False
    assert asked[1].url.path == "/repos/AlobarQuest/brain"
    assert asked[1].url.query == b""
    assert asked[1].headers["accept"] == "application/vnd.github+json"


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_a_github_refusal_is_could_not_tell_rather_than_has_not_opted_in(status: int) -> None:

    source = GitHubDeclarationSource(
        token="t",
        base_url="https://example.invalid",
        transport=httpx.MockTransport(lambda request: httpx.Response(status, json={})),
    )
    assert source.declaration("AlobarQuest/brain").target is None


def test_an_unreachable_github_is_could_not_tell_rather_than_a_traceback() -> None:
    """`httpx` raises three unrelated families and the third is a `ValueError`."""

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    source = GitHubDeclarationSource(
        token="t", base_url="https://example.invalid", transport=httpx.MockTransport(refuse)
    )
    answer = source.declaration("AlobarQuest/brain")
    assert answer.target is None
    assert "unreachable" in answer.detail
    assert "no route" not in answer.detail


def test_a_malformed_host_is_could_not_tell_rather_than_a_unicode_error() -> None:
    """IDNA encoding of a doubled dot raises `UnicodeError`, which is neither httpx family."""
    source = GitHubDeclarationSource(token="t", base_url="https://api..github.invalid")
    answer = source.declaration("AlobarQuest/brain")
    assert answer.target is None
    assert "unreachable" in answer.detail


def test_the_reader_asks_for_the_default_branch_and_the_raw_bytes() -> None:
    """No `ref`, so the branch asked about is the one a change would land on."""

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text='factory_target = true\nfactory_target_reason = "x"\n')

    source = GitHubDeclarationSource(
        token="t", base_url="https://example.invalid", transport=httpx.MockTransport(handler)
    )
    assert source.declaration("AlobarQuest/brain").target is True
    assert seen[0].url.params.get("ref") is None
    assert seen[0].headers["accept"] == "application/vnd.github.raw"
    assert is_allowed(seen[0].url.path)


def test_the_declaration_is_read_from_github_rather_than_from_a_checkout(tmp_path: Path) -> None:
    """A checkout is not a fresh source and must not become one by accident.

    Measured 2026-09-11: seven of eight working trees on this machine did not carry
    `factory-target.toml` hours after every declaration had landed, because nothing
    pulls them — so a reader that fell back to the filesystem would report the five
    repositories that declare `true` as not having opted in. The module names one
    route, and this asserts it is a URL rather than a path.
    """
    source = Path("src/membership_check/declaration.py").read_text(encoding="utf-8")
    assert "api.github.com" in source
    for filesystem in ("Path(", "open(", "read_text", "is_file", "exists()"):
        assert filesystem not in source, filesystem


def test_the_declaration_read_surface_is_one_route_and_no_more() -> None:
    """One route, anchored on the filename as well as on the shape of a repository name.

    The slug is interpolated from a package's own `profile_fields.target_repo`, so the
    template is what has to be asserted: a traversal segment, a query, a second path
    segment or a different file all have to fail. It may not reach the other two
    services' routes either, and neither of their allowlists may admit this one.
    """
    assert is_allowed("/repos/AlobarQuest/brain/contents/factory-target.toml")
    for forbidden in (
        "/repos/AlobarQuest/brain/contents/factory-target.toml/",
        "/repos/AlobarQuest/brain/contents/.env",
        "/repos/AlobarQuest/brain/contents/factory-target.toml?ref=main",
        "/repos/AlobarQuest/../../contents/factory-target.toml",
        "/repos/AlobarQuest/brain/actions/secrets",
        "/repos/AlobarQuest/brain",
        "/api/items",
        "/api/v1/package-intakes",
    ):
        assert not is_allowed(forbidden), forbidden
    assert not is_allowed_change_manager("/repos/AlobarQuest/brain/contents/factory-target.toml")
    assert not is_allowed_read("/repos/AlobarQuest/brain/contents/factory-target.toml")
    assert not is_allowed_write("/repos/AlobarQuest/brain/contents/factory-target.toml")


def test_the_declaration_reader_asks_and_can_do_nothing_else() -> None:
    """Two public read methods, each decided: the declaration, and (Rotation Part 2) whether a
    repository holds the credential registry file. A third is a surface nobody decided on."""
    public = {
        name
        for name in vars(GitHubDeclarationSource)
        if not name.startswith("_") and name not in {"close"}
    }
    assert public == {"declaration", "holds_registry"}


def test_a_repository_name_this_program_may_not_ask_about_never_reaches_the_transport() -> None:
    """The refusal is BEFORE the request, and it is returned rather than raised.

    A name is authored text in somebody's `package.yaml`, so a bad one must be a finding
    about one record rather than an exception that ends the pass. The positive case is
    here too: without it this asserts only that the reader refuses things, which a reader
    that refused everything would also satisfy.
    """
    import httpx

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, text='factory_target = true\nfactory_target_reason = "x"\n')

    source = GitHubDeclarationSource(
        token="t",
        base_url="https://example.invalid",
        transport=httpx.MockTransport(handler),
    )
    # `../..` IS THE CASE THAT MATTERS and every other entry here would pass
    # without it. `.` is legal in a repository name, so the path pattern matches
    # the segment `..`; the composed string is admitted and the path `httpx`
    # actually builds is `/contents/factory-target.toml`, outside the route. The
    # guard reads the built request for exactly this, so a control that only
    # feeds it names with too many segments proves the wrong property.
    for refused in (
        "../..",
        "a/..",
        "AlobarQuest/br\x00ain",
        "AlobarQuest/brain/../../secrets",
        "brain",
        "AlobarQuest/brain?ref=main",
        "",
    ):
        answer = source.declaration(refused)
        assert answer.target is None, refused
        assert "may ask about" in answer.detail, refused
    assert seen == []

    assert source.declaration("AlobarQuest/brain").target is True
    assert seen == ["/repos/AlobarQuest/brain/contents/factory-target.toml"]


def test_a_registry_file_is_present_absent_or_could_not_tell() -> None:
    present = _reader(_routed(httpx.Response(200, text="[[credential]]"), httpx.Response(200))[0])
    assert present.holds_registry("AlobarQuest/brain").target is True

    transport, seen = _routed(httpx.Response(404), httpx.Response(200, json={}))
    assert _reader(transport).holds_registry("AlobarQuest/brain").target is False
    assert seen == [
        "/repos/AlobarQuest/brain/contents/.cred-consumers.toml",
        "/repos/AlobarQuest/brain",
    ]

    for contents, repository in (
        (httpx.Response(404), httpx.Response(404)),
        (httpx.Response(500), httpx.Response(200)),
        (httpx.Response(403), httpx.Response(200)),
    ):
        answer = _reader(_routed(contents, repository)[0]).holds_registry("AlobarQuest/brain")
        assert answer.target is None, (contents.status_code, repository.status_code)


def test_an_absent_registry_file_says_nothing_about_opting_in() -> None:
    transport, _ = _routed(httpx.Response(404), httpx.Response(200, json={}))
    answer = _reader(transport).holds_registry("AlobarQuest/brain")
    assert ".cred-consumers.toml" in answer.detail
    assert "opted in" not in answer.detail


def test_a_registry_file_read_refuses_a_name_that_composes_another_route() -> None:
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.path)
        return httpx.Response(200)

    reader = _reader(httpx.MockTransport(handler))
    assert reader.holds_registry("../..").target is None
    assert reader.holds_registry("o/r?x=1").target is None
    assert sent == []
