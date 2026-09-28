"""The two refusal classes both landers' clients raise and the shared body catches.

A LEAF MODULE, importing nothing, so the confined clients (the transport) depend on it without
depending on `lander.core` (the classification). A lane-local copy of either class would be one the
shared body could not catch, and a refusal would escape as a traceback instead of a line.
"""

from __future__ import annotations


class OrchestratorError(Exception):
    """The orchestrator could not be asked, or refused in a way this pass cannot interpret."""


class LandingRefused(OrchestratorError):
    """The orchestrator refused. A fact about the subject, not a broken tool.

    It CARRIES THE REFUSAL CODE as well as the message, because not every refusal means the same
    thing to a reader. Some name a condition somebody must act on; others say only that the answer
    moved between the read and the request, which the next pass re-decides on its own. Classifying
    those apart needs the code -- a `DomainError` reaches the wire nested under `error`, and the
    message is prose that will be reworded.
    """

    def __init__(self, message: str, code: str = "") -> None:
        super().__init__(message)
        self.code = code
