"""A reasoned statement that one supervised act may start outside the hours policy declares.

The hours exist to keep an unattended change from interrupting something somebody is relying on.
That is an argument about work nobody is watching, and it says nothing about work a person is
sitting in front of. Until this existed the estate had no way to say so: the only routes around
the window were to rebuild the image with wider hours, or to write policy bytes into the running
container, and neither leaves a record anybody can read afterwards.

**One override CONCEPT, applied per ACT, and neither act's override implies the other's.** Starting
a coding run produces a pull request, which changes nothing outside a repository until something
separately lands it; landing one changes what is already serving. A single flag covering both would
let a decision about writing code now silently grant changing production now, so each act carries
its own and this type is never shared between two of them in one call.

**It suppresses ONE NAMED refusal and nothing else.** Not the declared reach, not the repository
allowlist, not the human authority approval, not the change record, not whether the criteria were
decided by the verifier -- and not a policy artifact this process could not read, which is a fault
here rather than a statement about the hour. Callers key the suppression on the exact refusal;
keying it on "the window term said something" is the same check aimed at the wrong noun.

**A reason is required, and the requirement is structural.** An override that could be carried
without one would be clicked through, and the reason is the whole of what makes the record worth
reading later. The invalid shape is therefore unrepresentable: construction raises, so no caller --
including one written later against the command objects directly -- can hand a service an override
nobody justified.

**Attribution is INHERITED, not captured (ADR-0031).** The flag rides a machine-credentialed call,
so the record shows a machine. What makes it honest is that no unit reaches either act without a
named human having approved its authority envelope -- bound to the exact fingerprint, naming the
target repository, the capabilities, the change class and the budget. The override does not decide
whether this work may happen; it decides only whether it may start now. So the record carries the
reason together with enough to reach the approval it rests on, and a reader asking who decided this
could run at this hour can answer it without leaving the data.
"""

import uuid
from dataclasses import dataclass
from typing import Any, Final

from orchestrator.errors import DomainError
from orchestrator.factory_policy import OUTSIDE_CHANGE_WINDOW

REASON_REQUIRED: Final = "change_window_override_reason_required"


@dataclass(frozen=True)
class ChangeWindowOverride:
    """One act's override, with the reason its operator gave for it.

    Validated at construction rather than inside the services that honour it. A service-level
    guard would be a second rule set over a type that can already be built wrong, and -- since
    both call sites build this from a request body before anything else happens -- it would also
    be a branch no test could reach. Raising here fires for every caller, and it fires before an
    idempotency replay could answer a malformed request with a stale record.
    """

    reason: str

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise DomainError(
                REASON_REQUIRED,
                "an override of the change window must state why this run is supervised",
                "supply a reason describing the supervision",
            )


def override_record(
    override: ChangeWindowOverride | None,
    *,
    applied: bool,
    authority_approval_id: uuid.UUID | None,
    authority_fingerprint: str | None,
) -> dict[str, Any] | None:
    """What one act writes down about an override it was handed. ``None`` when none was.

    ``applied`` is whether the override actually suppressed a refusal, and it is separate from
    being carried on purpose. An act inside the declared hours needs no override, and an act
    refused by a term ordered above the window never reached the window at all -- recording either
    as though the override had done something would assert a suppression that never happened.
    Recording the reason in both cases is what answers "an override was offered here".

    The approval and the fingerprint are the inheritance made legible. They are not a second copy
    of anything a reader could otherwise reconstruct: the envelope is write-once and the approval
    is bound to its fingerprint, so naming both is what turns the reason into a claim somebody can
    check rather than a sentence somebody typed.
    """
    if override is None:
        return None
    return {
        "reason": override.reason,
        "applied": applied,
        "authority_approval_id": (
            str(authority_approval_id) if authority_approval_id is not None else None
        ),
        "authority_fingerprint": authority_fingerprint,
    }


def suppressed(
    refusal: str | None, override: ChangeWindowOverride | None
) -> tuple[str | None, bool]:
    """The window term's answer after an override, and whether the override changed it.

    **Keyed on the EXACT refusal, never on the term having said something.** The same term also
    reports that this process could not read the policy artifact at all, and -- on the act that
    lands a pull request -- that no hours are declared for it. Both are faults somebody has to fix
    here; an operator declaring a run supervised has answered neither. Suppressing whatever the
    term returned would be the same check aimed at the wrong noun, and it would turn a broken read
    into permission.

    One copy, read by both acts, so the rule cannot come to mean two things. What is deliberately
    NOT shared is the override itself: each act is handed the one supplied on its own call.
    """
    if override is None or refusal != OUTSIDE_CHANGE_WINDOW:
        return refusal, False
    return None, True
