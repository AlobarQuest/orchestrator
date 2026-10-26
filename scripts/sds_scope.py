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

INCOMPLETE IS SAID OUT LOUD, NEVER IMPLIED BY A SHORTER LIST. When part of the answer can't be read
(one repository's declaration, the landing policy, a policy name that isn't an active repository),
the program still prints every name it could determine, writes each gap to stderr, and exits 3.
The launcher records and audits what is known and keeps the 3, so the pass reads as incomplete
rather than clean, and one unreadable source doesn't cost every other repository its day. Only
the account's repository listing is required: without it nothing is known, and nothing is printed.

Every name is printed as GitHub spells it (`full_name` from the account's own listing). The
landing ledger's references embed the repository name as given, so a lowercased name would
record every landing a second time under a new reference.
"""

import argparse
import os
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from bump_proposer.landing_policy import LandingPolicyError, read_inert_landing
from pin_watcher.github import GitHubReader, PinWatcherError, repositories
from work_carrier.declaration import DeclarationSource, GitHubDeclarationSource

GITHUB_TOKEN_VARIABLE = "SDS_SCOPE_GITHUB_TOKEN"
CHANGE_MANAGER_TOKEN_VARIABLE = "SDS_SCOPE_CHANGE_MANAGER_TOKEN"

EXIT_OK = 0
EXIT_INCOMPLETE = 3


@dataclass(frozen=True)
class Scope:
    """The names that could be determined, and why any others couldn't be."""

    names: list[str]
    gaps: list[str] = field(default_factory=list)


def factory_targets(owned: Iterable[str], source: DeclarationSource) -> Scope:
    """Every owned repository that declares itself a factory target."""
    targets: list[str] = []
    gaps: list[str] = []
    for repository in owned:
        answer = source.declaration(repository)
        if answer.target is None:
            gaps.append(f"factory-target declaration unreadable: {repository} ({answer.detail})")
        elif answer.target:
            targets.append(repository)
    return Scope(sorted(targets), gaps)


def canonical(names: Iterable[str], owned: Iterable[str]) -> Scope:
    """`names` respelled as GitHub spells them; any that isn't an active repository is a gap."""
    spelling: Mapping[str, str] = {repository.lower(): repository for repository in owned}
    known = sorted(spelling[name.lower()] for name in names if name.lower() in spelling)
    gaps = [
        f"the landing policy names {name}, which isn't an active repository of this account "
        "(archived, renamed or not owned)"
        for name in sorted(names)
        if name.lower() not in spelling
    ]
    return Scope(known, gaps)


def ledger_scope(targets: Scope, inert: Scope) -> Scope:
    """The landing ledger's population: factory targets plus the inert population."""
    return Scope(sorted(set(targets.names) | set(inert.names)), targets.gaps + inert.gaps)


def _read_ledger_scope() -> Scope:
    github_token = os.environ.get(GITHUB_TOKEN_VARIABLE, "")
    if not github_token:
        return Scope([], [f"{GITHUB_TOKEN_VARIABLE} is not set"])
    try:
        with GitHubReader(token=github_token) as reader:
            owned = repositories(reader)
    except PinWatcherError as error:
        return Scope([], [f"the account's repositories couldn't be listed: {error}"])
    with GitHubDeclarationSource(token=github_token) as source:
        targets = factory_targets(owned, source)
    try:
        policy = read_inert_landing(os.environ.get(CHANGE_MANAGER_TOKEN_VARIABLE, ""))
    except LandingPolicyError as error:
        inert = Scope([], [f"the landing policy couldn't be read: {error}"])
    else:
        inert = canonical(policy.repositories, owned)
    return ledger_scope(targets, inert)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print a lane's repositories, one per line.")
    parser.add_argument("population", choices=["ledger"], help="which lane's population")
    parser.parse_args(argv)
    scope = _read_ledger_scope()
    for gap in scope.gaps:
        print(f"sds-scope: {gap}", file=sys.stderr)
    if scope.names:
        print("\n".join(scope.names))
    return EXIT_INCOMPLETE if scope.gaps or not scope.names else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
