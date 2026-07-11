# Package intake in production (human actor)

`POST /api/v1/package-intakes` is human-actor-only and, in production, reachable
only through the `orchestrator-intake-human` Traefik router (Alobar ID
forward-auth). The orchestrator also does not re-verify the approval server-side
(`verification_mode == "caller_attested_cli_verified"`) — it trusts the CLI's
local verification. So intake is split: the CLI verifies and emits the request
body; a human POSTs it from a logged-in browser.

## Steps

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

   A package that fails verification exits non-zero and writes nothing.

2. In a browser tab already authenticated to `https://sds.alobar.net`, open the
   devtools console and POST the body:

   ```js
   const body = /* paste the contents of /tmp/intake.json */;
   let r = await fetch('/api/v1/package-intakes', {
     method: 'POST',
     headers: { 'Content-Type': 'application/json' },
     body: JSON.stringify(body),
   });
   if (r.status === 401) {  // known first-POST quirk: retry once
     r = await fetch('/api/v1/package-intakes', {
       method: 'POST',
       headers: { 'Content-Type': 'application/json' },
       body: JSON.stringify(body),
     });
   }
   console.log(r.status, await r.json());
   ```

   Success returns the `revision_id`. The idempotency key makes a re-run after an
   ambiguous attempt replay to the same revision rather than double-registering.
