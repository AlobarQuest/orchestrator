"""The rotation decomposer: proposes each carried rotation's decomposition (ADR-0055 amendment 15,
increment 5).

A session wrote each rotation's decomposition by hand once its change record was approved and
carried. This lane generates it from the intake's own criteria and the type's derived plan, and
proposes it as SYSTEM; a person approves it on `/review`, as before.
"""
