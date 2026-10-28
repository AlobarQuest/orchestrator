"""Test doubles for the member record's grant on a target repository (ADR-0058).

The real source reads the member record through the request's session. These are injected at the
call site so admission is exercised without seeding members. `asked` is recorded because "did
admission consult the grant at all?" is a real assertion: a term that short-circuits before the
read is cheap, and one that never reads is absent.
"""

__all__ = ["FakeGrantSource", "granted_source", "ungranted_source"]


class FakeGrantSource:
    def __init__(
        self, granted: set[tuple[str, str]] | None = None, *, default: bool = False
    ) -> None:
        self._granted = set(granted or ())
        self._default = default
        self.asked: list[tuple[str, str]] = []

    def is_granted(self, repository: str, service: str) -> bool:
        self.asked.append((repository, service))
        return (repository, service) in self._granted or self._default


def granted_source() -> FakeGrantSource:
    """Every repository is granted every service, so the existing admission suites keep their
    subject rather than meeting this term first."""
    return FakeGrantSource(default=True)


def ungranted_source() -> FakeGrantSource:
    return FakeGrantSource()
