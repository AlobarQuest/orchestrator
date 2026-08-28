"""The machine-activation sweep: what the operator machine's working copies will execute next.

A SEPARATE program (ADR-0002's shape), sibling to `landing_ledger` and `deploy_watcher`. It reads
local git and records one observation per enrolled working copy. It shares no import path with
`src/orchestrator/`, and its write surface is one endpoint.

ADR-0030 names two lanes and BOTH now live in this package, under separate commands and separate
credentials.

* `sweep` -- what this module's docstring describes. One generic observation per enrolled working
  copy, under the OBSERVER credential, on a clock, about REPOSITORIES rather than units. A routine
  pull has no work unit, so this lane structurally cannot write a `ReleaseArtifactBinding` and does
  not try.
* `bind` -- the unit-caused lane (`bind.py`). A `ReleaseArtifactBinding` carrying a real content
  digest, for a COMPLETED unit whose landing commit this machine has actually pulled, under the
  SYSTEM credential.

They share a program because they share the only expensive thing: knowing which working copy is
which repository. They share nothing else. Separate commands, separate bearers, separate confined
HTTP surfaces, separate exit-code meanings -- and separate enrolled sets, because two of the six
SDS targets become live by a hosted application swapping an image and must never be described as
running from a working copy on this machine.

WHAT IT IS FOR, in the order the arguments actually weigh:

1. It is the only control over ADR-0031. Activation is best-effort by construction and its
   failures are silent by design -- `activate-checkout.sh` prints one line and returns 0 whatever
   it finds, so a job whose helper went missing keeps running old code and keeps exiting 0.
   Nothing else watches that.
2. *"Is this machine current?"* stops being a question a person happens to notice.

It never pulls. ADR-0030 stops at recording; making the machine self-update is a separate decision
with its own authority argument.
"""
