"""The estate's record of a landing that changes something already serving (ADR-0019).

A repository the estate calls ``redeploys`` is one where landing on the default branch changes
something already serving. ADR-0019's rule is that such a change is routed through change-manager
first, so that a window, acceptance criteria and a remedy are captured before it happens. This
module is how the orchestrator asks whether that record exists and has been approved.

**change-manager is the authority and it is asked, never second-guessed.** Nothing here derives a
record from anything else, and nothing here writes: the two questions this module can ask are
answered by one read of a listing route.

**Nothing raises.** ``record_for`` is total: a timeout, a refusal, a malformed body and an absent
configuration all come back as an answer that says it has none. Only ``DomainError`` and
``APIAuthenticationError`` have registered handlers, so an escaping HTTP exception would surface as
a bare 500 from the admission path -- and a gate that 500s is one that has stopped deciding.
Returning is what keeps the caller able to fail closed rather than fail over.

**The listing is NOT filtered by status, and that is load-bearing.** change-manager applies a
``status`` query as a SQL filter, so asking it for approved records only means a record awaiting its
human gate is simply absent from the response -- and the caller would then report "there is no
record" about a record that exists. Pending is the ordinary steady state of a record waiting for a
person, so that mistake would be the common case rather than an edge one. The status is read from
the row instead, and the two situations get different answers.

**The pipeline name is INJECTED rather than written here.** It is a vocabulary member owned by
change-manager (``app/sources.py``), and the route is where this deployment's cross-boundary
configuration is resolved -- the same place the base URL and the credential come from.

The credential is change-manager's one shared bearer, which covers its whole ``/api`` router. That
bounds what this term can attest and the bound is recorded in ADR-0019 rather than implied here:
the secret that reads a record can also approve one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Protocol

import httpx

# Why THIS process has no answer -- distinct from change-manager answering that it holds no record,
# which is a statement about the estate. These need different people: one sets an environment
# variable, the other looks at why a service is refusing.
SOURCE_UNCONFIGURED: Final = "source_unconfigured"
SOURCE_UNREADABLE: Final = "source_unreadable"

# More than one row claims one repository and pull request. change-manager holds a unique identity
# per pair, so this is a guard over a FOREIGN repository's constraint rather than a case with a
# next step of its own -- the posture the estate answer's reader already takes toward App Brain's
# vocabulary. Ambiguity is reported as ambiguity; it never resolves to the first row.
RECORD_AMBIGUOUS: Final = "record_ambiguous"

# change-manager's own approved state, mirrored. Its status column is a plain string with no
# vocabulary object behind it, so this is the one member that matters here rather than a copy of a
# set: every other value, known or not, reads as not approved.
STATUS_APPROVED: Final = "approved"

_ROUTE: Final = "/api/items"
_USER_AGENT: Final = "orchestrator-change-record-check/1 (+AlobarQuest/orchestrator)"


@dataclass(frozen=True)
class ChangeRecord:
    """One change record, projected down to what admission is entitled to decide on."""

    identifier: int
    status: str
    target_repository: str
    pull_request_number: int

    @property
    def approved(self) -> bool:
        return self.status == STATUS_APPROVED


@dataclass(frozen=True)
class ChangeRecordAnswer:
    """What change-manager said, or the reason this process has nothing to report.

    ``answered`` is a separate field rather than an inference from ``record`` because a successful
    read that finds nothing and a read that did not happen are different facts with different
    remedies, and they would otherwise share one shape. A caller that forgets the distinction gets
    ``answered=False``, which no predicate treats as permission.
    """

    answered: bool
    record: ChangeRecord | None = None
    reason: str | None = None


class ChangeRecordSource(Protocol):
    """Asked once per admission decision that reaches it."""

    def record_for(self, github_repo: str, pull_request_number: int) -> ChangeRecordAnswer: ...


class HttpChangeRecordSource:
    """Reads change-manager over HTTP, and converts every failure into an answer.

    Injected at the route, the same way the estate answer's reader is, so the admission path can be
    exercised without a network. It goes through an ``httpx.Client`` with an INJECTABLE transport
    rather than a module-level call: a module-level call can only be tested by patching, and the
    request itself is then unobservable -- which is what lets a test prove the credential and the
    query on the wire are the ones intended.

    The timeout is deliberately shorter than the estate reader's. Both are consulted inside a
    transaction that holds a row lock on the work unit, and this one is the second of the two.
    """

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        pipeline: str,
        timeout_seconds: float = 5.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._pipeline = pipeline
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    def record_for(self, github_repo: str, pull_request_number: int) -> ChangeRecordAnswer:
        if not self._base_url or not self._token or not self._pipeline:
            return ChangeRecordAnswer(False, reason=SOURCE_UNCONFIGURED)
        try:
            with httpx.Client(transport=self._transport, timeout=self._timeout_seconds) as client:
                response = client.get(
                    f"{self._base_url}{_ROUTE}",
                    params={"source": self._pipeline},
                    headers={
                        "authorization": f"Bearer {self._token}",
                        "user-agent": _USER_AGENT,
                    },
                )
        # `InvalidURL` is NOT an `HTTPError` -- it derives straight from `Exception` -- so catching
        # the latter alone would leave the totality this module promises untrue. The input that
        # reaches it is not exotic: a trailing newline on the configured base URL, which
        # `.rstrip("/")` does not remove, and which is the ordinary way an environment variable
        # gets malformed.
        except (httpx.HTTPError, httpx.InvalidURL):
            return ChangeRecordAnswer(False, reason=SOURCE_UNREADABLE)
        if response.status_code != 200:
            return ChangeRecordAnswer(False, reason=SOURCE_UNREADABLE)
        try:
            body = response.json()
        except ValueError:
            return ChangeRecordAnswer(False, reason=SOURCE_UNREADABLE)
        return _answer_from_body(body, github_repo, pull_request_number)


def _answer_from_body(body: Any, github_repo: str, pull_request_number: int) -> ChangeRecordAnswer:
    """The matching record in change-manager's listing, or no answer when it does not read as one.

    Defensive about a shape this repository does not own. A body that is not a list, or a row whose
    fields are missing or the wrong type, reads as NO ANSWER -- never as the nearest recognisable
    thing, and never as "there is no record", which is a claim about the estate rather than about a
    reading. A row that is well formed but describes some other pull request is simply not a match.
    """
    if not isinstance(body, list):
        return ChangeRecordAnswer(False, reason=SOURCE_UNREADABLE)
    wanted = github_repo.lower()
    matches: list[ChangeRecord] = []
    for row in body:
        if not isinstance(row, dict):
            return ChangeRecordAnswer(False, reason=SOURCE_UNREADABLE)
        record = _record_from_row(row)
        if record is None:
            continue
        if record.target_repository.lower() == wanted and (
            record.pull_request_number == pull_request_number
        ):
            matches.append(record)
    if len(matches) > 1:
        return ChangeRecordAnswer(False, reason=RECORD_AMBIGUOUS)
    return ChangeRecordAnswer(True, record=matches[0] if matches else None)


def _record_from_row(row: dict[str, Any]) -> ChangeRecord | None:
    """One listing row, or ``None`` when it does not carry the fields a match is made of.

    A row missing a repository or a number cannot match anything, so skipping it is the same answer
    as reading it and finding it does not match. `bool` is an `int` in Python, so a boolean pull
    request number is rejected rather than read as 1.
    """
    repository = row.get("target_repository")
    number = row.get("pull_request_number")
    identifier = row.get("id")
    status = row.get("status")
    if not isinstance(repository, str) or not repository:
        return None
    if not isinstance(number, int) or isinstance(number, bool):
        return None
    if not isinstance(identifier, int) or isinstance(identifier, bool):
        return None
    if not isinstance(status, str) or not status:
        return None
    return ChangeRecord(
        identifier=identifier,
        status=status,
        target_repository=repository,
        pull_request_number=number,
    )
