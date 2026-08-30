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

from collections.abc import Mapping
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
class WorkflowPin:
    """WHICH BYTES a repository's rollout must still be, as change-manager pinned them.

    A pointer, never a transcription: the statement of what a green rollout attests lives with
    the party that holds the policy, and this names the file and the blob that statement was made
    about. Reading it here needs no copy of that judgment, which is why it is a blob sha.
    """

    path: str
    blob_sha: str


@dataclass(frozen=True)
class LandingConditions:
    """What the party holding the policy requires of the ACT, projected onto the record.

    **This process holds no copy.** These values arrive with the record they qualify, so the
    version a record was approved under and the version now in force are read together and cannot
    disagree across two calls. change-manager also serves them standing alone, for a person; this
    is the surface a landing reads.

    `version` is what is IN FORCE, which is deliberately not the record's own `policy_version`.
    Comparing the two is the only mechanism by which narrowing the policy binds an approval that
    already exists -- a narrowing revokes nothing by itself, because a record is re-evaluated only
    when something proposes it again, and a record whose pull request has closed is never proposed
    again at all.
    """

    version: int
    update_types: frozenset[str]
    require_head_current_with_base: bool
    rollout_workflows: Mapping[str, WorkflowPin]
    # ADR-0036. WHICH RULE THIS VERSION DECIDES BY, carried as the presence of the field rather
    # than as a second flag beside it. `None` means the version served no such key, which is every
    # version before the fifth: those decide by `update_types`, and a reader that guessed otherwise
    # would widen an approval nobody granted. A frozenset -- empty or not -- means the version
    # decides on the OUTCOME, and names the ecosystems its required checks do not exercise.
    #
    # ABSENCE FALLS TOWARD THE STRICTER RULE, deliberately. The two sides of this contract ship
    # separately, so a reader that has learned the outcome rule will meet a server that has not,
    # and the only safe reading of "this version said nothing about it" is the rule that version
    # actually declared.
    excluded_ecosystems: frozenset[str] | None = None

    def pin_for(self, repository: str) -> WorkflowPin | None:
        """The pin for a repository, matched case-insensitively as its identity key is.

        `None` means NO PIN WAS DECLARED, which a caller must read as a refusal rather than as a
        waived condition -- the version that predates the field declares none for anything.
        """
        return self.rollout_workflows.get(repository.lower())


@dataclass(frozen=True)
class ChangeRecord:
    """One change record, projected down to what a caller is entitled to decide on.

    **`status` ALONE IS NOT ENOUGH, and that is what this shape exists to say.** There are two
    kinds of approved record and they are not interchangeable inputs to a decision: one approved
    by conformance to a pinned policy version, and one a human approved before any policy existed
    (production item 44). A reader keyed on `status` cannot tell them apart, nor either from a
    record still stored `approved` whose live objections are no longer empty.

    So `approved` stays what it always was -- change-manager's own stored decision -- and the two
    fields that qualify it are carried alongside rather than folded into it. Which of them a
    consumer must require is the consumer's judgment, not this reader's: the factory lane accepts
    either shape because a human's per-unit authority approval gates it separately, while an
    unattended landing binds itself to the current policy version.

    THE IDENTIFIER IS BACK, AND THIS TIME IT HAS A READER. An earlier draft parsed it, REQUIRED it
    for a row to read at all, and read it nowhere -- so its only reachable effect was to turn a
    real approved record into "there is no record". It is now written into the landing commit's
    trailer and into the landing's own row, which is what lets the estate's ledger resolve a
    landing back to the record that permitted it. It is optional here for the same reason it was
    dropped: a row that does not carry a readable one is still a row, and a consumer that needs it
    says so.
    """

    status: str
    target_repository: str
    pull_request_number: int
    record_id: int | None = None
    # The pinned policy version that approved this record, or None for a record no policy decided.
    # None is NOT a weaker version number -- it is a different basis, and Devon's 2026-08-12 ruling
    # names it: the basis is `policy_version` when present, else `decided_by`.
    policy_version: int | None = None
    # Why change-manager says the record does not currently conform, recomputed by it on every
    # read. Explanatory there and load-bearing here: a stored `approved` whose live objections are
    # non-empty is a record whose approval has been overtaken by the policy moving under it.
    policy_objections: tuple[str, ...] = ()
    decided_by: str | None = None
    # What the policy in force requires of the act. `None` means this deployment could not read
    # them -- a change-manager that predates them, or a shape this build does not recognise. It is
    # deliberately NOT fatal to the record: the factory lane's term needs only the stored decision,
    # and poisoning every record because a field is missing would refuse work that has its own
    # per-unit human approval. The landing that DOES need them refuses when they are absent.
    conditions: LandingConditions | None = None

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
        # THREE families, and the totality this module promises is only as complete as this tuple.
        # `InvalidURL` is not an `HTTPError` -- it derives straight from `Exception` -- and
        # `UnicodeError` (a `ValueError`) is raised by IDNA encoding of a malformed HOST before
        # either of them can be, for a doubled dot or a DNS label over 63 characters. None of the
        # three inputs is exotic: they are the ordinary ways a URL in an environment variable gets
        # malformed, and `.rstrip("/")` removes none of them. Adversarial review found the third
        # by probing rather than by reading -- the control written for this class used a trailing
        # newline, which `InvalidURL` already covered, so the mutation guarding it was killed by a
        # test that shared the same incomplete model of what httpx raises.
        except (httpx.HTTPError, httpx.InvalidURL, ValueError):
            return ChangeRecordAnswer(False, reason=SOURCE_UNREADABLE)
        if response.status_code != 200:
            return ChangeRecordAnswer(False, reason=SOURCE_UNREADABLE)
        try:
            body = response.json()
        except ValueError:
            return ChangeRecordAnswer(False, reason=SOURCE_UNREADABLE)
        return _answer_from_body(body, self._pipeline, github_repo, pull_request_number)


def _answer_from_body(
    body: Any, pipeline: str, github_repo: str, pull_request_number: int
) -> ChangeRecordAnswer:
    """The matching record in change-manager's listing, or no answer when it does not read as one.

    Defensive about a shape this repository does not own. A body that is not a list, or a matching
    row whose status does not read as one, reads as NO ANSWER -- never as the nearest recognisable
    thing, and never as "there is no record", which is a claim about the estate rather than about a
    reading. A well-formed row describing some other pull request is simply not a match.

    **Matching and reading are separated on purpose.** A row is a match on the three fields that
    identify one: the pipeline, the repository and the number. Everything else is read only from a
    row that already matched -- so a duplicate that is malformed in some other field still COUNTS,
    and cannot drop the tally below two and hand the surviving row through as unambiguous. That was
    the shape adversarial review found: the ambiguity guard defeated by the malformed twin of the
    record it was guarding.
    """
    if not isinstance(body, list):
        return ChangeRecordAnswer(False, reason=SOURCE_UNREADABLE)
    wanted = github_repo.lower()
    matches: list[dict[str, Any]] = []
    for row in body:
        if not isinstance(row, dict):
            return ChangeRecordAnswer(False, reason=SOURCE_UNREADABLE)
        if _matches(row, pipeline, wanted, pull_request_number):
            matches.append(row)
    if len(matches) > 1:
        return ChangeRecordAnswer(False, reason=RECORD_AMBIGUOUS)
    if not matches:
        return ChangeRecordAnswer(True)
    row = matches[0]
    status = row.get("status")
    if not isinstance(status, str) or not status:
        return ChangeRecordAnswer(False, reason=SOURCE_UNREADABLE)
    qualifiers = _qualifiers(row)
    if qualifiers is None:
        return ChangeRecordAnswer(False, reason=SOURCE_UNREADABLE)
    record_id, policy_version, objections, decided_by = qualifiers
    return ChangeRecordAnswer(
        True,
        record=ChangeRecord(
            status=status,
            target_repository=github_repo,
            pull_request_number=pull_request_number,
            record_id=record_id,
            policy_version=policy_version,
            policy_objections=objections,
            decided_by=decided_by,
            conditions=_conditions(row),
        ),
    )


def _conditions(row: dict[str, Any]) -> LandingConditions | None:
    """The conditions the policy in force puts on the act, or None when they do not read as any.

    Every unrecognised shape answers None, and the caller that needs them refuses. That is the
    same posture the estate answer's reader takes toward App Brain: a newer authoring side naming
    something this build predates must not be guessed at, and here it must not be waived either.
    """
    version = row.get("landing_policy_version")
    served = row.get("landing_conditions")
    if not isinstance(version, int) or isinstance(version, bool) or not isinstance(served, dict):
        return None
    update_types = served.get("update_types")
    fresh = served.get("require_head_current_with_base")
    if not isinstance(update_types, list) or not all(isinstance(t, str) for t in update_types):
        return None
    if not isinstance(fresh, bool):
        return None
    pins = served.get("rollout_workflows")
    if not isinstance(pins, dict):
        return None
    excluded = _excluded_ecosystems(served)
    if excluded is _UNREADABLE:
        return None
    parsed: dict[str, WorkflowPin] = {}
    for repository, pin in pins.items():
        if not isinstance(repository, str) or not isinstance(pin, dict):
            return None
        path, blob = pin.get("path"), pin.get("blob_sha")
        if not isinstance(path, str) or not path or not isinstance(blob, str) or not blob:
            return None
        parsed[repository.lower()] = WorkflowPin(path=path, blob_sha=blob)
    return LandingConditions(
        version=version,
        update_types=frozenset(update_types),
        require_head_current_with_base=fresh,
        rollout_workflows=parsed,
        excluded_ecosystems=excluded,  # type: ignore[arg-type]
    )


# A third answer, because ABSENT and MALFORMED are different facts about this key and only one of
# them is survivable. Absent is a server that predates ADR-0036 and is read as the rule it does
# declare; malformed is a shape nobody can act on, which refuses the whole record exactly as a
# malformed pin does.
_UNREADABLE: Final = object()


def _excluded_ecosystems(served: dict[str, Any]) -> frozenset[str] | None | object:
    """Which ecosystems the outcome rule excludes, `None` for a version that does not use it.

    Tolerating the absent key is what makes the two repositories shippable in either order: this
    reader meets a server serving the older shape whenever it is deployed first, and must answer
    about that shape rather than refusing it.
    """
    if "excluded_ecosystems" not in served:
        return None
    value = served["excluded_ecosystems"]
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        return _UNREADABLE
    return frozenset(value)


def _qualifiers(
    row: dict[str, Any],
) -> tuple[int | None, int | None, tuple[str, ...], str | None] | None:
    """The fields that qualify `status`, or None when the row does not read as one.

    **A field of the wrong TYPE is no answer at all, never an absent one**, and the difference is
    the whole reason this is separated out. `policy_version: "1"` read as `None` would present a
    policy-approved record as a human-approved one, which is the third indistinguishable row this
    module's own type docstring names -- so an unreadable qualifier refuses the whole record
    rather than degrading it to the nearest recognisable shape.

    `bool` is an `int` in Python, so a boolean version is rejected rather than read as 1.
    """
    record_id = row.get("id")
    if record_id is not None and (not isinstance(record_id, int) or isinstance(record_id, bool)):
        return None
    version = row.get("policy_version")
    if version is not None and (not isinstance(version, int) or isinstance(version, bool)):
        return None
    # `null` is ABSENT, not malformed -- and the difference matters to a consumer that never reads
    # this field. `.get(key, default)` returns `None` for an explicit null, so a service serving
    # `"policy_objections": null` would have made the whole record unreadable and started refusing
    # the FACTORY lane, which only ever reads `status`. A wrong TYPE stays fatal.
    objections = row.get("policy_objections")
    objections = [] if objections is None else objections
    if not isinstance(objections, list) or not all(isinstance(o, str) for o in objections):
        return None
    decided_by = row.get("decided_by")
    if decided_by is not None and not isinstance(decided_by, str):
        return None
    return record_id, version, tuple(objections), decided_by


def _matches(row: dict[str, Any], pipeline: str, wanted: str, pull_request_number: int) -> bool:
    """Whether this row is the record for that pipeline, repository and pull request.

    **The pipeline is checked HERE and not only in the query**, which is the one scoping dimension
    the first implementation took on trust. change-manager filters correctly today, and FastAPI
    ignores an unknown query parameter silently -- so a renamed parameter, or a listing route that
    stops scoping, would have handed admission a record belonging to a pipeline this term knows
    nothing about. The other two dimensions were already re-checked here; this makes the set
    complete rather than the two the server did not filter on.

    The repository is compared LOWER-CASED, mirroring change-manager's own identity key, which
    folds it. `bool` is an `int` in Python and `True == 1`, so a boolean number is rejected rather
    than matched against pull request one.
    """
    number = row.get("pull_request_number")
    repository = row.get("target_repository")
    return (
        row.get("source") == pipeline
        and isinstance(repository, str)
        and repository.lower() == wanted
        and isinstance(number, int)
        and not isinstance(number, bool)
        and number == pull_request_number
    )
