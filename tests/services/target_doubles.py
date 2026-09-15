"""Test doubles for a repository's declaration that it is a factory target (ADR-0015).

The real source reads GitHub. It is injected at the route like the estate source, so admission is
exercised here without a network. `asked` is recorded because "did admission read the declaration
at all?" is a real assertion: a term that short-circuits before the read is cheap, and one that
never reads is absent.
"""

from orchestrator.services.factory_target import TargetDeclaration

__all__ = [
    "FakeFactoryTargetSource",
    "TargetDeclaration",
    "declared_source",
    "undeclared_source",
    "unreadable_source",
]


class FakeFactoryTargetSource:
    def __init__(
        self,
        answers: dict[str, TargetDeclaration] | None = None,
        default: TargetDeclaration = TargetDeclaration(True, "declared a target"),
    ) -> None:
        self._answers = dict(answers or {})
        self._default = default
        self.asked: list[str] = []

    def declaration_for(self, repository: str) -> TargetDeclaration:
        self.asked.append(repository)
        return self._answers.get(repository, self._default)


def declared_source() -> FakeFactoryTargetSource:
    """Every repository declares itself a target, so the existing admission suites keep their
    subject rather than meeting this term first."""
    return FakeFactoryTargetSource()


def undeclared_source() -> FakeFactoryTargetSource:
    return FakeFactoryTargetSource(default=TargetDeclaration(False, "not declared a target"))


def unreadable_source() -> FakeFactoryTargetSource:
    return FakeFactoryTargetSource(default=TargetDeclaration(None, "could not be read"))
