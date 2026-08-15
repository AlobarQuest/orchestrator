# Retained wave-exit attestation runs

Each JSON file here is one run of `scripts/attest_wave_exit.py` against
`docs/operations/wave-exit-manifest.toml`, committed rather than left in a scratchpad.

The Wave-3 re-measurement of 2026-08-04 wrote its per-repo JSON to `/tmp` and told the reader to
regenerate it. That is the practice these records replace: **evidence in a scratchpad is not
evidence.** A record here carries, per clause, the command that was run, its exit code, its raw
output, the timestamp, and the digest of the manifest it was measured against — so the
measurement can be re-derived rather than believed.

## Reading one

```
verdict                     attested | failed | inconclusive | pin_broken
bars[].pin                  verified | broken | unverifiable
bars[].body_sha256          which text of the bar this run measured
clauses[].result            pass | fail | not_applicable | unavailable
clauses[].checks[].basis    live     — the fact was re-measured now
                            retained — the historical record of it was shown intact
```

`unavailable` never means `pass` and never means `fail`. A run containing one is **inconclusive**
(exit 2), which is the state the programme's false METs actually occupied.

`pin_broken` (exit 3) means the manifest stopped reconstructing the plan's own sentence. Clause
results are then suppressed rather than printed, because a result measured against a bar that is
not the rule reads as a demonstration of the rule.

## Producing one

```bash
python3 scripts/attest_wave_exit.py --record docs/evidence/exit-manifest/$(date -u +%F).json
```

Run it from a machine that can read the authoritative plan under `~/docs` and holds the SDS
operator credential; a CI runner can reach neither, so the workflow's own runs are structurally
inconclusive and are not a substitute for this one. Take a record before declaring a wave met,
not after someone challenges it.

## What a record does not settle

A record shows what was measured, not that the measurement was the right one. Where a clause's
`checks[].basis` is `retained`, the run established that a historical document is intact — not
that the fact still holds. Those documents live in `~/docs/software-delivery-system/`, which is
not version-controlled; the digest pin detects a change to one but cannot recover it.
