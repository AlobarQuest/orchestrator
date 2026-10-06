# Package intake in production

There are three ways in. A machine that names the change record behind the work registers
directly (the lane). A machine that does not stages the intake for a person to confirm (staged).
And a person can still paste a payload (by hand), which is the escape hatch.

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

## Staged: the CLI stages, a person confirms

ADR-0006 amendment 1. The CLI posts the emitted payload as `orchestrator-system`:

```
POST /api/v1/staged-intakes        (SYSTEM only; the same body as /api/v1/package-intakes)
```

The response's `review_path` is the page a person opens, `/review/staged-intakes/{id}`, and every
staged row is also first on `/review`. The page shows the three decision facts before anything
else: what it does (the package outcome), what it affects (the declared reach, then the repository
from the profile fields, and the bump for a dependency-update), and whether it can be backed out
(the declared rollback plan or the profile's change class, plus what App Brain says landing that
repository's default branch does, read when the page renders). Under them is one button, then the
package content.

The button registers the intake as the person who pressed it, under the idempotency key the
payload was staged with, so a second press or a resubmitted page lands on the same revision. The
intake event records the staged row's id beside the command. A staged row registers nothing until
then, and no machine credential can confirm it.

Staging refuses whatever registering would refuse, with the same error code: approved status,
`caller_attested_cli_verified`, evidence types, reach, an unknown originating observation, an
idempotency key an intake already used, and a package revision that is already registered. Staging
the same key with the same body replays the row; with a different body it is
`idempotency_conflict`.

The confirm re-runs every check, because things can change after staging. If a staged row can no
longer be registered (the confirm returns 409), or should not be, open "Withdraw instead" on its
page, give a reason, and withdraw it. The row records who withdrew it, when and why, leaves
`/review`, and can never be confirmed. To bring it back, stage it again under a new key.

The intent-packages CLI still copies the payload for the paste form. Moving it to staging is a
separate change, after this route is deployed.

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

The escape hatch: for an intake with no change record behind it that was not staged, or when
neither the lane nor staging is available.

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
