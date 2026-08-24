"""The machine-activation sweep: what the operator machine's working copies will execute next.

A SEPARATE program (ADR-0002's shape), sibling to `landing_ledger` and `deploy_watcher`. It reads
local git and records one observation per enrolled working copy. It shares no import path with
`src/orchestrator/`, and its write surface is one endpoint.

ADR-0030 names two lanes and this is the second of them. The first -- a `ReleaseArtifactBinding`
carrying a content digest -- needs a COMPLETED work unit, and a routine pull has none, so this
sweep structurally cannot write bindings and does not try. What it writes is a generic observation
under the OBSERVER credential, on a clock, about repositories rather than units.

WHAT IT IS FOR, in the order the arguments actually weigh:

1. It is the only control over ADR-0031. Activation is best-effort by construction and its
   failures are silent by design -- `activate-checkout.sh` prints one line and returns 0 whatever
   it finds, so a job whose helper went missing keeps running old code and keeps exiting 0.
   Nothing else watches that.
2. *"Is this machine current?"* stops being a question a person happens to notice.

It never pulls. ADR-0030 stops at recording; making the machine self-update is a separate decision
with its own authority argument.
"""
