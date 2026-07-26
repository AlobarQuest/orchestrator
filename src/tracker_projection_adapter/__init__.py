"""Out-of-process, outbound-only tracker projection adapter.

Mirrors canonical orchestrator work-unit state onto an external tracker. The orchestrator is
always canonical; this package only reads canonical state and writes a unit's tracker-item
binding back. It imports nothing from the orchestrator and calls no lifecycle surface.
"""
