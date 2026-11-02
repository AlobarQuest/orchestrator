"""The rotation verifier: runs `/verify` on each submitted rotation unit (ADR-0055 amendment 14).

The orchestrator's verifier completes a rotation unit whose step evidence is clean; nothing ran it
on a schedule, so each unit waited for a session to call it. This lane holds only the VERIFIER
bearer, which may verify and read, and nothing else: the executor never holds it (decision 4).
"""
