# Package intake in production

`POST /api/v1/package-intakes` admits **a human or the SYSTEM actor** (ADR-0027), and a
machine-registered intake must name the approved change record that caused it. The guard it
replaced was protecting a transcription: every intake in production was authored by an AI and
typed into a form by a person, so the gate asked a human to retype a machine's work.

The orchestrator does not re-verify the approval server-side
(`verification_mode == "caller_attested_cli_verified"`) — it trusts the CLI's local
verification. So intake is still split: the CLI verifies and emits the request body, and
something POSTs it. There are two things that do.

**The lane**: `work-carrier --register` reads the work proposals a human approved in
change-manager, runs the emitter against the package each names, and registers the result as
`orchestrator-system`. That is the production path, and it needs no person.

**By hand**: a human pastes the emitted payload into `/review/intakes/new`, which POSTs to
`POST /review/intakes` on the `orchestrator-review` router. This is the escape hatch — it is the
only way to register an intake that names no change record, since no standing HUMAN credential
exists (ADR-0006).

> **Routing.** The `orchestrator-intake-human` Traefik router matches
> `Path(/api/v1/package-intakes) && Method(POST)` and applies Alobar ID forward-auth, so a
> machine bearer arriving there draws a **302** and never reaches the app. It must be **removed**
> for the machine lane to work; the browser path is unaffected, because the form posts to
> `/review/intakes`. Until it is removed, only the by-hand path below functions.

## The lane: `work-carrier`

Nothing needs doing per record. The scheduled pass reads every approved work proposal, emits and
verifies a payload for each, and registers it.

```bash
scripts/run-work-carrier.sh              # inspect: prints what it would register, writes nothing
scripts/run-work-carrier.sh --register   # registers
```

Exit codes: 0 clean, 1 tool failure, 2 unusable input, 3 a record needs a person. A record it
carried is not a finding; one it could not prepare, or one the orchestrator refused, is.

A second pass over an unchanged queue is a **replay**, not a second intake — the payload's
idempotency key is derived from the record and its revision. Nothing marks a change record
carried, so a record stays in the approved queue until a person resolves it in change-manager.

## By hand

For an intake with no change record behind it, or when the lane is unavailable.

1. Emit the verified body (offline — no API token, runs the hash / verify-approval
   / factory-chain checks; requires the local package sources at
   `~/Projects/intent-packages`, `~/.factory/events.jsonl`,
   `~/Projects/security-standards`):

   ```bash
   orchestrator emit-intake-payload <package-dir> \
       --source-repository AlobarQuest/intent-packages \
       --idempotency-key <unique-key> \
       --out /tmp/intake.json
   ```

   A package that fails verification exits non-zero and writes nothing. Add
   `--change-record <id>` when a change-manager record caused the work; the payload then carries
   the join ADR-0026 asks for, whichever way it is registered.

   Note this does **not** work from a git worktree: the emitter resolves its
   `intent-packages` sibling relative to its own file, so from `.worktrees/<name>/` it looks in
   `.worktrees/` and every package refuses with `approval verification failed`. Run it from the
   main checkout.

2. Open `https://sds.alobar.net/review/intakes/new` in a browser authenticated to Alobar ID,
   paste the contents of `/tmp/intake.json` into the form, and submit.

   The form takes its idempotency key from its own CSRF-bound field and ignores the pasted one,
   so re-submitting the rendered page is a replay rather than a second intake — reload the page
   to register something genuinely new. Success redirects to the revision's page.

   Do **not** POST to `/api/v1/package-intakes` from the devtools console. That was the
   documented path before the `/review` form existed; it now draws the forward-auth redirect
   described above. (An earlier version of this page also prescribed retrying a "known first-POST
   401 quirk". There is no such quirk — it was speculation, it has never once been observed, and
   it must not be planned around.)
