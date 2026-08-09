"""The capability vocabulary -- one source of truth for the capability strings a work unit's
authority envelope may name.

A work unit's ``required_capability`` and every key in its ``authority.capabilities`` must be one
of these strings. The runner (the worker repo named in the contract test) enforces the SAME
runner vocabulary in its own ``capability_vocabulary`` module, from which its supported
set is derived, and both sides are pinned to the byte-identical
``tests/fixtures/runner_envelope_contract.json``. The contract test
``test_runner_envelope_contract`` asserts ``RUNNER_CAPABILITIES`` here is *derived from* that
declaration rather than being a
second, independently hand-maintained copy -- a hash pin would prove the file matches without
proving anyone consumes it, which is the exact unread-permission defect WS-P2.16 exists to close.
The two golden ENVELOPE fixtures are specimens of dispatched work and are asserted to be subsets
of this set; until WS-P3.7 the vocabulary was derived from one of them, which meant a name the
factory had never dispatched had to be written into a record of what it did.

The orchestrator's accepted set is a strict SUPERSET of the runner's: it adds the capabilities for
work no runner performs -- its own WS-5.1 post-hoc release-observation verification units, and the
non-software operational units of WS-P2.13. Enforcing the runner's set at orchestrator
ingress would make the orchestrator reject both its own generated units and every unit of a
delivery profile that has no repository at all.

Adding a name here is a CROSS-REPO act with a fixed order, because the runner's envelope model
forbids unrecognised capabilities outright: it must be merged there and pinned by every caller
before this module may say it, or every dispatch of a unit carrying the new name dies at envelope
parse. ``scripts/check_capability_consumer_compatibility.py`` refuses the pull request that gets
that order wrong; it was prose until WS-P3.7.

This module is a plain Python module rather than a data file so it ships by construction -- inside
the wheel and the image -- with no packaging metadata to forget. A fixture under ``tests/`` is NOT
in the image (Dockerfile copies ``src`` only); production ingress reads THIS module.
"""

from collections.abc import Mapping
from typing import Final

from orchestrator.errors import DomainError
from orchestrator.kernel.authority import AuthorityBudgets, AuthorityEnvelope
from orchestrator.kernel.runner_authority import runner_capability_level_violation

# The runner-executable capabilities, sorted. Byte-pinned across repos through the contract
# fixture; the contract test asserts
# ``RUNNER_CAPABILITIES == frozenset(golden_contract()["capabilities"])`` so a divergence here or
# in the declaration is loud, and hardcoding a term reds the derivation.
CAPABILITY_VOCABULARY: Final[dict[str, tuple[str, ...]]] = {
    "runner": (
        "command.run",
        "github.pr.create",
        # ADR-0020, WS-P3.7 Increment 3. The name exists so that landing a pull request can be an
        # authority term a human approves per unit, in the envelope, the way every other
        # capability is -- rather than an ambient property of the factory. NOTHING reads it yet:
        # no code in either repository derives a permission from it, and no envelope has ever
        # carried it. Making the name sayable is the whole of this increment.
        #
        # An envelope that does carry it matches no known-good authority pattern -- totality --
        # so it falls to the human authority gate, which is where ADR-0020 puts the decision.
        "github.pr.merge",
        "orchestrator.claim",
        "orchestrator.evidence.write",
        "repo.edit",
        "repo.read",
    ),
}

RUNNER_CAPABILITIES: Final[frozenset[str]] = frozenset(CAPABILITY_VOCABULARY["runner"])

# The orchestrator additionally accepts capabilities that no runner executes. This set means
# exactly "capabilities a unit may be AUTHORED with that the runner does not execute", and the
# orchestrator vocabulary is therefore a strict superset of the runner's.
#
# `post_deploy_verification` (WS-5.1) is minted by the orchestrator for its own post-hoc release
# verification units, which never traverse a runner.
#
# `operational_action` (WS-P2.13) is for a unit whose work is not software: a credential rotation,
# an operational procedure, anything the `non-software-operational` delivery profile describes.
# Such a unit has no repository, opens no pull request and runs no command, so before this term
# existed the only way to pass ingress was to declare a runner capability the work never used --
# which the 2026-07-27 production drill did, declaring `repo.edit` on units that touched no
# repository. An envelope that attests a capability the work does not exercise is a false
# attestation, and a human approving that envelope is approving a claim nobody checked. The term
# is accepted at ingress and is deliberately absent from `RUNNER_CAPABILITIES`, so a unit carrying
# it can never be handed to a runner: `level_for` returns "prohibited" for every runner capability
# such a unit declares, and the WS-P2.16 pull-request binding guard keys on `github.pr.create`,
# which an operational envelope does not grant.
#
# WS-P2.8's `follow_up_review` is deliberately NOT here. Unlike the two above, the follow-up
# minting pass constructs its unit directly, bypassing `validate_unit_capabilities` exactly as
# post-deploy minting does. Listing it would add nothing the feature needs while letting an
# ordinary authored unit carry the marker -- which, when the marker was capability-only, voided
# that unit's real acceptance criteria. Identity (the derived `follow_up_unit_id`) is the marker.
ORCHESTRATOR_ONLY_CAPABILITIES: Final[frozenset[str]] = frozenset(
    {"post_deploy_verification", "operational_action"}
)

# What orchestrator unit ingress accepts for ``required_capability`` and ``authority.capabilities``
# keys: the runner set plus the orchestrator-only additions.
ORCHESTRATOR_CAPABILITIES: Final[frozenset[str]] = (
    RUNNER_CAPABILITIES | ORCHESTRATOR_ONLY_CAPABILITIES
)


def validate_unit_capabilities(required_capability: str, capabilities: Mapping[str, str]) -> None:
    """Reject any unit capability, or capability level, the orchestrator does not accept.

    Applies to UNIT fields only -- ``required_capability`` and ``authority.capabilities``.
    Never to the package/revision ``authority`` (which legitimately speaks the registry
    vocabulary), and never inside ``normalize_authority`` or the kernel (post-deploy units are
    constructed directly and would self-reject). Enforced at both unit-ingress paths
    (``register_approved_unit`` and the decomposition proposal gate).

    Both failure modes were SILENT before they were named here, and in opposite directions. An
    unknown NAME already failed closed at dispatch -- ``level_for`` returns ``"prohibited"`` for
    a capability it does not know, so admission refused it as ``capability_not_authorized``, late
    and without pointing at the offending key. An unknown LEVEL failed OPEN: ``level_for``
    compares against ``"allowed"``, so a mistyped level on a capability the work does not need
    reads as a prohibition and admission is happy -- while the runner, which validates every
    level it is handed, refuses the whole envelope.
    """
    for capability in (required_capability, *capabilities):
        if capability not in ORCHESTRATOR_CAPABILITIES:
            raise DomainError(
                "unknown_capability",
                f"unit capability {capability!r} is not a recognised orchestrator capability",
                "declare one of: " + ", ".join(sorted(ORCHESTRATOR_CAPABILITIES)),
            )
    # The level rule itself lives in the kernel, beside the other things the runner refuses, and
    # is applied again at approval and admission for envelopes authored before it existed. One
    # definition, three call sites -- a second copy here is exactly the divergence this
    # workstream exists to close.
    violation = runner_capability_level_violation(
        AuthorityEnvelope(capabilities=dict(capabilities), budgets=AuthorityBudgets(None, None))
    )
    if violation is not None:
        raise DomainError(violation.code, violation.message, violation.remediation)
