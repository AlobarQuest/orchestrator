"""The work lane's watcher: an approved record whose work was built is retired (ADR-0029).

A SEPARATE program (ADR-0002's shape), out of process, exactly as the carry, the rollout watcher,
the change proposer and the estate lander are. It runs in the carry's pass rather than on a
schedule of its own, and BEFORE it -- a record whose work is done must leave the approved queue
before the carry reads that queue, or the carry re-registers it and draws the very refusal this
program exists to remove.

WHY IT IS NOT THE CARRY. ADR-0022's rule: the producer's remit is what MAY happen, the watcher's
is what DID. The carry's whole subject is work that has not been done yet. Keeping them apart is
what lets `work_carrier/change_manager.py` go on truthfully asserting that it holds no write to
change-manager at all, while the one write the lane now needs lives here, behind this module's own
allowlist.

**Remit is a property of a MODULE and its asserted surface, not of a process.** Two programs in
this repository already hold the same change-manager scope and each asserts a narrower surface
than the scope permits. Sharing a pass with the carry costs nothing that matters and saves a
scheduled job on a lane that has none.
"""
