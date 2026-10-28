"""Each credential type the SDS rotates: what its provider does by machine, and where its
credentials are kept (ADR-0055 amendment 15).

The one place a type is declared. The adapters read their capabilities and store from here
(`adapters/__init__.py`), and the rotation proposer generates a package from here and a registry
row alone (`rotation_proposer/generate.py`), without importing any provider: a type's declaration
names no network client.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from rotation_worker.plans import Capabilities


@dataclass(frozen=True)
class CredentialType:
    capabilities: Capabilities
    # The destination kind of the store its keepers live in: only the executor instance whose
    # store reads that kind takes its units (ADR-0055 amendment 12).
    store_kind: str = "bws-secret"


# By adapter name: each is the `NAME` its adapter module declares, which a test holds equal.
TYPES: Final[dict[str, CredentialType]] = {
    "openrouter": CredentialType(Capabilities(machine_mint=True, machine_revoke=True)),
    "orchestrator": CredentialType(
        Capabilities(machine_mint=True, machine_revoke=True, hosted=True)
    ),
    "github-pat": CredentialType(Capabilities(machine_mint=False, machine_revoke=True)),
    "bitbucket": CredentialType(Capabilities(machine_mint=False, machine_revoke=False)),
    "change-manager": CredentialType(
        Capabilities(machine_mint=True, machine_revoke=True, hosted=True)
    ),
    "bws-machine-token": CredentialType(
        Capabilities(machine_mint=False, machine_revoke=False), store_kind="keychain"
    ),
}

# A registry row's `class`, mapped to its type (increment 4). A class with no entry is a type the
# SDS doesn't rotate: nothing is generated for it.
CLASS_TYPES: Final[dict[str, str]] = {
    "atlassian-api-token": "bitbucket",
    "bws-machine-token": "bws-machine-token",
    "change-manager-m2m-bearer": "change-manager",
    "github-pat-fine-grained": "github-pat",
    "openrouter-key": "openrouter",
    "orchestrator-m2m-bearer": "orchestrator",
}
