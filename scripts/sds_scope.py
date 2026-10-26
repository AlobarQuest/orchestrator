"""Which repositories a lane covers, derived from the estate's own declarations.

The scheduled lanes used to carry their repository lists by hand, and the lists drifted: the
landing ledger's eight and the activation sweep's six were written months apart, and nothing held
either to what the estate actually declares. This module answers from the declarations instead:

- **A factory target** is a repository whose `factory-target.toml` on its default branch says
  `factory_target = true`, read with the parser dispatch admission is pinned to
  (`work_carrier/declaration.py`).
- **The inert population** is the set of repositories change-manager's landing policy names as
  ones where landing changes nothing already serving, read with the reader the inert lane itself
  uses (`bump_proposer/landing_policy.py`).
- **The landing ledger covers both**, because the ledger's subject is every repository this estate
  lands code in, and a lane lands code in exactly those.

WHOLE OR NOTHING. A repository whose declaration cannot be read, a policy that cannot be read, or
a name the account does not own makes the whole answer unavailable: the program prints nothing
and exits 3. A shorter list would let a lane pass "clean" over the repositories it silently
dropped, which is the false clean these lanes exist to refuse.

Every name is printed as GitHub spells it (`full_name` from the account's own listing). The
landing ledger's references embed the repository name as given, so a lowercased name would
record every landing a second time under a new reference.
"""

import argparse
import os
import sys
from collections.abc import Iterable, Mapping, Sequence
from typing import Protocol

from bump_proposer.landing_policy import LandingPolicyError, read_inert_landing
from pin_watcher.github import GitHubReader, PinWatcherError, repositories
from work_carrier.declaration import Declaration, GitHubDeclarationSource

GITHUB_TOKEN_VARIABLE = "SDS_SCOPE_GITHUB_TOKEN"
CHANGE_MANAGER_TOKEN_VARIABLE = "SDS_SCOPE_CHANGE_MANAGER_TOKEN"

EXIT_OK = 0
EXIT_UNAVAILABLE = 3


class ScopeUnavailable(Exception):
    """Some input could not be read, so no list is given."""


class DeclarationSource(Protocol):
    def declaration(self, slug: str) -> Declaration: ...


def factory_targets(owned: Iterable[str], source: DeclarationSource) -> list[str]:
    """Every owned repository that declares itself a factory target."""
    targets: list[str] = []
    unread: list[str] = []
    for repository in owned:
        answer = source.declaration(repository)
        if answer.target is None:
            unread.append(f"{repository} ({answer.detail})")
        elif answer.target:
            targets.append(repository)
    if unread:
        raise ScopeUnavailable("unreadable factory-target declarations: " + "; ".join(unread))
    return sorted(targets)


def canonical(names: Iterable[str], owned: Iterable[str]) -> list[str]:
    """`names` respelled as GitHub spells them, refusing any the account does not own."""
    spelling: Mapping[str, str] = {repository.lower(): repository for repository in owned}
    unknown = sorted(name for name in names if name.lower() not in spelling)
    if unknown:
        raise ScopeUnavailable(
            "the landing policy names repositories this account does not own: " + ", ".join(unknown)
        )
    return sorted(spelling[name.lower()] for name in names)


def ledger_repositories(
    targets: Iterable[str], inert: Iterable[str], owned: Iterable[str]
) -> list[str]:
    """The landing ledger's population: factory targets plus the inert population."""
    return sorted(set(targets) | set(canonical(inert, owned)))


def _read_ledger_scope() -> list[str]:
    github_token = os.environ.get(GITHUB_TOKEN_VARIABLE, "")
    change_manager_token = os.environ.get(CHANGE_MANAGER_TOKEN_VARIABLE, "")
    if not github_token:
        raise ScopeUnavailable(f"{GITHUB_TOKEN_VARIABLE} is not set")
    try:
        with GitHubReader(token=github_token) as reader:
            owned = repositories(reader)
        with GitHubDeclarationSource(token=github_token) as source:
            targets = factory_targets(owned, source)
        policy = read_inert_landing(change_manager_token)
    except (PinWatcherError, LandingPolicyError) as error:
        raise ScopeUnavailable(str(error)) from error
    return ledger_repositories(targets, policy.repositories, owned)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print a lane's repositories, one per line.")
    parser.add_argument("population", choices=["ledger"], help="which lane's population")
    parser.parse_args(argv)
    try:
        names = _read_ledger_scope()
    except ScopeUnavailable as error:
        print(f"sds-scope: {error}", file=sys.stderr)
        return EXIT_UNAVAILABLE
    print("\n".join(names))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
