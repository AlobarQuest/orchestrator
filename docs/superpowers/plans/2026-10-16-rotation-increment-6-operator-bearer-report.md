# Credential rotation increment 6: the operator bearer (run report)

ADR-0055 increment 6 rotated the first self-issued bearer, `orchestrator-operator-bearer`, through
the rotation executor on production. The executor minted the value itself, wrote it into the
orchestrator's `ORCHESTRATOR_M2M_CREDENTIALS` setting through production Coolify, and restarted the
orchestrator twice. Plan: `docs/superpowers/plans/2026-10-14-rotation-increment-6-plan.md`.
Decisions: ADR-0055 amendment 9. The run took place in the `live_estate` change window on
2026-10-16.

## Outcome

The old bearer (`18193828df5beef6`, 64 characters) is refused 401 by the status ledger and is no
longer loaded as the entry's `previous`. The new bearer (`b1477adbecd4ccd7`, 43 characters) answers
200, is what the keeper `bd71bed1` in `Rotation / Keepers` holds, and hashes to the
`orchestrator-operator` entry the running orchestrator loaded (agent `claude-code-interactive`).
`sds-operator` reads the new value with a fresh login and an empty `HOME`. No staging or quarantine
secret is left in `Rotation / Work`. Every fingerprint in this report is a sha256 prefix; no value
or hash appears in the run's evidence or logs.

| Unit | Id | Steps | Criteria | Attempts |
|---|---|---|---|---|
| `mint` | `085ab6d0-381c-54ef-8a95-c3fa0e54491e` | mint, stage, host, verify_new, store, verify_consumers | AC-001 to AC-006, passed | 1 |
| `retire` | `e74a58b7-1123-5d54-961d-3ca65cb9f7c1` | verify_consumers, retire_old, confirm_dead | AC-007 to AC-009, passed | 1 |

The chain: change record 138, approved; intake `c7236f97-d561-4d77-889e-9303b576320c` (package
`rotation-orchestrator-operator-bearer` revision 1, content hash `852ca67e…`, approved in
intent-packages #126); decomposition proposal `1792ae29-af66-40c6-b959-5eb9b2a43a8a`. Both units
completed. The VM ran `a640058f3190`.

## Timeline (America/New_York)

| Time | What happened |
|---|---|
| 01:57 | The executor's pass was refused the claim: the unit touches a hosted service and the window opens at 02:00. |
| 02:03–02:04 | The first pass in the window ran all six mint steps. Host wrote setting `97ed834b`, restarted, and waited until the identities route showed the new hash loaded with the old one as `previous`. |
| about 02:26 | Mint's criteria adjudicated and the unit completed, which made `retire` READY. |
| 02:27–02:28 | The next pass ran retire. Retire-old confirmed the entry's `previous` was the quarantined value, wrote setting `f01b7b13` without it, and restarted. Confirm-dead proved the old value dead and deleted staging and quarantine. |
| about 02:31 | Retire's criteria adjudicated and the unit completed. |

Each restart left the orchestrator answering within the executor's wait; `/health/live` answered
200 after both.

## What shipped

| Repository | Pull request | What |
|---|---|---|
| orchestrator | #412 | The increment 6 plan, from three recon passes |
| orchestrator | #413 | The orchestrator-bearer adapter, the Coolify client, the hosted-write step, and the identities route |
| orchestrator | #414 | Runbook: a failed restart leaves the orchestrator down |
| orchestrator | #415 | Hand-over: `rotated_by_sds`, `rotate_requested`, the manifest and credentials page, and amendment 9's proofs |
| orchestrator | #416 | Work carrier: the package joins the standing operational packages |
| orchestrator | #423 | Amendment 9 decisions 5 and 6 (Devon, 2026-10-16) |
| orchestrator | #424 | Registry: `rotate_requested` cleared |
| intent-packages | #124 | Destination format: `coolify-env-hash` names the entry it rewrites |
| intent-packages | #125, #126 | Package revision 1, and Devon's approval of it |

## Browser-click consent

Devon consented to this session driving the `/review` clicks and the change record for this
rotation only (amendment 9, decision 4). The session approved change record 138, the
decomposition, both units' authority, all nine adjudications and both completions in Devon's
signed-in browser. The orchestrator records Devon as the actor for each, because the clicks came
through his Alobar ID session; every reason and rationale says the session made it under his
consent for this run only. Decision 10's default, that Devon clicks, is unchanged for the next
rotation.

## Verification

Each criterion was adjudicated against the executor's evidence and an independent read made by
the session, all by fingerprint:

- **The identities route**, read through the executor's own client on the VM: after mint, the
  entry loaded `b1477adbecd4ccd7` with `previous` `18193828df5beef6` until 2026-10-23; after
  retire, the same hash with no `previous`.
- **The keeper**, read by the executor's root and by `sds-operator` with a fresh login: `b1477adb`,
  43 characters, matching the loaded entry, after both units.
- **`Rotation / Work`**, listed by the root: after mint, staging held the new value and quarantine
  the old; after retire, neither exists. The only secret left is increment 5's canary keeper.
- **Probes**, from the evidence: the new value 200 and a random value 401 at verify_new; the old
  value 200 before revoke; at confirm_dead, the old value and two random values 401 and the new
  value 200.

## Findings

1. **verify_consumers doesn't itemise its probe of a hosted destination.** `contract.py`'s
   `unverified()` counts the orchestrator as switched only after `accepts(new)` passes a live
   probe, but the evidence row carries only the count ("2 of 2 consumers hold or attest the new
   value"). AC-006 and AC-007 ask for that probe, so adjudicating them needed a code read. Filing
   the probe in the step's evidence would make it self-evident.
2. **The preview row of the setting isn't observable.** The Coolify token has no
   `read:sensitive`, so nothing reads either row's value back. The production row is proved by the
   identities route; the preview row is proved only by `set_variable` refusing a non-2xx PATCH.
   AC-003 and AC-008 were adjudicated on that basis.
3. **confirm_dead files two evidence rows.** It records its proof before deleting, then again with
   the deletion note, so a crash between the two leaves the proof. That's by design, but the pair
   reads as a duplicate on the review page. A note on the first row ("deleting next") would say so.
4. **The change window held.** The 01:57 pass was refused the claim and the 02:03 pass took it,
   the first production evidence that the window gates a hosted rotation unit's start.

## What's next

- **The rotation proposer's schedule** (#420) can be installed now that `rotate_requested` is
  cleared.
- **The observer and drift-reporter**: the inventory, then the split (decision 3), Healthchecks
  reads for scheduled consumers (decision 5), and executor support for a rotation that spans two
  keepers.
- **change-manager**: the JSON `previous` overlap (decision 6), deployed and verified on
  production, then its four bearers.
