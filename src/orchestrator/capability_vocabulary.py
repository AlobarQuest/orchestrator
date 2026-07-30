"""The capability vocabulary -- one source of truth for the capability strings a work unit's
authority envelope may name.

A work unit's ``required_capability`` and every key in its ``authority.capabilities`` must be one
of these strings. The runner (the worker repo named in the contract test) enforces the SAME
six-term runner vocabulary in its own ``capability_vocabulary`` module, from which its supported
set is derived, and both sides are pinned to the byte-identical
``tests/fixtures/runner_authority_envelope.json``. The contract test
``test_runner_envelope_contract`` asserts ``RUNNER_CAPABILITIES`` here is *derived from* that
golden envelope rather than being a
second, independently hand-maintained copy -- a hash pin would prove the file matches without
proving anyone consumes it, which is the exact unread-permission defect WS-P2.16 exists to close.

The orchestrator's accepted set is a strict SUPERSET of the runner's: it adds the capabilities for
work no runner performs -- its own WS-5.1 post-hoc release-observation verification units, and the
non-software operational units of WS-P2.13. Enforcing the runner's six-term set at orchestrator
ingress would make the orchestrator reject both its own generated units and every unit of a
delivery profile that has no repository at all.

This module is a plain Python module rather than a data file so it ships by construction -- inside
the wheel and the image -- with no packaging metadata to forget. A fixture under ``tests/`` is NOT
in the image (Dockerfile copies ``src`` only); production ingress reads THIS module.
"""

from collections.abc import Iterable
from typing import Final

from orchestrator.errors import DomainError

# The runner-executable capabilities, in the order the golden envelope sorts them. Byte-pinned
# across repos through the authority envelope fixture; the contract test asserts
# ``RUNNER_CAPABILITIES == frozenset(golden_envelope()["capabilities"])`` so a divergence here or in
# the envelope is loud, and hardcoding a term reds the derivation.
CAPABILITY_VOCABULARY: Final[dict[str, tuple[str, ...]]] = {
    "runner": (
        "command.run",
        "github.pr.create",
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


def validate_unit_capabilities(required_capability: str, capabilities: Iterable[str]) -> None:
    """Reject any unit capability outside the orchestrator's accepted set with a named error.

    Applies to UNIT fields only -- ``required_capability`` and ``authority.capabilities`` keys.
    Never to the package/revision ``authority`` (which legitimately speaks the registry
    vocabulary), and never inside ``normalize_authority`` or the kernel (post-deploy units are
    constructed directly and would self-reject). Enforced at both unit-ingress paths
    (``register_approved_unit`` and the decomposition proposal gate).

    The prior failure mode was a SILENT one: ``level_for`` returns ``"prohibited"`` for an unknown
    capability, so dispatch already fails closed as ``capability_not_authorized`` -- but late, and
    without pointing at the offending key. This turns that into a named error at the gate.
    """
    for capability in (required_capability, *capabilities):
        if capability not in ORCHESTRATOR_CAPABILITIES:
            raise DomainError(
                "unknown_capability",
                f"unit capability {capability!r} is not a recognised orchestrator capability",
                "declare one of: " + ", ".join(sorted(ORCHESTRATOR_CAPABILITIES)),
            )
