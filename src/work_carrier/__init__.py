"""The carry: an approved change-manager record becomes a ready orchestrator intake (ADR-0026).

A SEPARATE program (ADR-0002's shape), out of process, on a schedule, exactly as the deploy
watcher, the change proposer and the estate lander are. It composes nothing the orchestrator
owns and it decides nothing: change-manager decides whether the work is wanted, and the package
checkout decides whether it can be expressed as an intake.
"""
