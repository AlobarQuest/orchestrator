"""The two numbers that bound how long a claim holds a work unit (WS-P2.18 Increment 6).

A lease is the period during which this orchestrator **refuses to hand the unit to anybody else**.
It was never stall control and this module must not be read as though it were: nothing here detects
a worker that has hung, and lapsing merely makes the unit available to be reclaimed by a SYSTEM
actor who asks. Bounding a hung worker is a real, open hole and it belongs to WS-P2.19.

Both constants live in the kernel rather than in the policy artifact because the artifact may only
ever move the number BETWEEN them. That is what keeps a duration compatible with ADR-0010's
guarantee that policy can only refuse: a declared lease can lengthen the period in which
reassignment is refused, never shorten it below what this build grants by default and never past a
ceiling this build owns. See ADR-0013.
"""

import hashlib
from datetime import timedelta

# What a unit whose reach nobody declared -- or whose declaration this build cannot read -- is
# granted, and, being the shortest hold the system will ever apply, also the floor a declared lease
# must exceed. One number with two readings, deliberately: "the least this build ever refuses for"
# is the same fact asked from either side. It is the value every claim in this system's history has
# been granted, so a unit nobody has described keeps exactly the hold it has always had.
DEFAULT_LEASE = timedelta(minutes=15)

# The longest lease the artifact may declare. A bound rather than an unbounded field, for the
# reason `dead_letter_stalled_approval_seconds` is capped: without one, the claim that policy
# cannot switch reassignment off is true of the type and false of the values an operator can
# actually write, and a lease of a year silences reclaim as completely as disabling it would. Two
# hours sits an hour above the longest reach anybody has had a reason to declare, which leaves room
# to decide a longer one without leaving room to decide a meaningless one.
LEASE_CEILING = timedelta(hours=2)

DEFAULT_MAX_ATTEMPTS = 3


def hash_lease_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
