"""The bump-driver lane: moves standing dependency-update work forward with no person acting
(Landing Part 4).

One scheduled pass, as SYSTEM: it decomposes registered bump intakes, dispatches ready bump units
and lands completed ones. Verification stays with the VERIFIER lane. It runs no repository code:
its mirrors only ever run git, and the generator runs with `--no-execute`.
"""
