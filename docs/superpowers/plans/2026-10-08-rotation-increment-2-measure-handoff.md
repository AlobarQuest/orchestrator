# Credential rotation increment 2: measure, then decide (session handoff)

ADR-0055 (accepted 2026-10-08) designs credential rotation as a pull-based executor in an isolated
OrbStack VM. Its increment 2 measures seven facts the later increments depend on, using throwaway
keys and no live secret. It ends with Devon deciding the Coolify token's scope and holder.

This session measures and records. It builds no rotation machinery, rotates no live credential, and
grants no standing authority.

## What the session must produce

1. **One measured answer per question** in the following table, each from a probe shown to
   discriminate: the probe gives a different answer on a known-bad input under the same conditions
   (`memory: probe-must-discriminate`). An answer the session couldn't measure is recorded as
   unmeasured, with the reason. It's never inferred from documentation.
2. **ADR-0055 amendment 1**, "Increment 2 measurements", that records each answer, the command
   shape that produced it (no values), and the date. For each answer that contradicts an assumption
   in ADR-0055, the amendment names the decision or increment it changes and how.
3. **The Coolify decision, put to Devon** with the measured facts and the options in "The decision
   for Devon". His answer is recorded in the amendment, quoted and dated.
4. **Devon's GitHub secret-writer decision, recorded in the amendment.** On 2026-10-09 he chose a
   dedicated rotation App for increment 7: "I think 1 +3, and we create a dedicated rotation app".
   If question 6 measures that an installation token can write Actions secrets, the amendment
   records the decision and updates ADR-0055 to match: the threat model's "GitHub secret-writer
   PAT" asset row becomes the rotation App's private key, and increment 7 names the App. If it
   measures that one can't, the decision goes back to Devon with the measurement.
5. **Every throwaway resource deleted**, with the deletion confirmed by a probe, not assumed.

## Read first

- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`: decisions 2, 3, 4, 7
  and 9, the per-class table, the threat model and the increment plan.
- `docs/superpowers/specs/2026-10-08-credential-rotation-requirements.md`: Q1's OrbStack
  measurements and "Facts to measure before the design commits".
- `docs/superpowers/specs/2026-10-08-credential-rotation-research.md` and
  `docs/superpowers/specs/2026-10-08-credential-rotation-provider-docs.md`: provider capabilities,
  including Coolify's token scope table.
- `docs/operations/credentials.md`: the Coolify `/envs` rule, and the dispatch App's measured
  permissions (sections #101 and #274).
- `~/Projects/security-standards/docs/build-agent-secrets.md`: the secrets rules of the road.

## The seven questions

The "Consumed by" column names what in ADR-0055 depends on the answer, so a surprising answer is
traced to the increment it changes.

| # | Question | Consumed by |
|---|---|---|
| 1 | Can a Coolify API token with `write` and `deploy` but not `read:sensitive` change an application's env var and restart it, and does it see `real_value` when it reads envs? | The Coolify decision; decision 7's retire-old restart; increments 6 and 7 |
| 2 | Can an OpenRouter management key carry an expiry? Does the key-info endpoint return the id needed to revoke a key, given only the key's value? | Increment 5's adapter; containment of the management key |
| 3 | Does a disabled OpenRouter key probe 401? Does a deleted one? | Increment 5's revoke verification |
| 4 | What does a revoked BWS machine token return (exit code and message, and how long after the revoke)? | The executor's error handling (increment 4); decision 9's split confirm-dead unit; increment 8 |
| 5 | Does moving a BWS secret to another project keep its UUID? | Every keeper move (increments 5 to 7); the `.bws-secrets.toml` updates |
| 6 | Can a GitHub App installation token write Actions secrets when the App has the `secrets` permission? | Increment 7's dedicated rotation App |
| 7 | Does `orb -m <machine> -u root` work without a password on an isolated machine? | Decision 2's residual-risk statement |

## How to measure each one safely

Rules that apply to all seven:

- **No live value moves.** Every key, token, project, machine, app and repository is a throwaway
  made for this increment and named with a `probe-inc2-` prefix, so the cleanup can find them all.
- **Devon makes what needs a console or web UI** (an OpenRouter management key, the BWS projects
  and machine accounts, the Coolify tokens, the GitHub App). He stores each token in a Keychain
  item whose service name starts with `probe-inc2-`, by running
  `security add-generic-password -s <service> -a devon -T /usr/bin/security -w` in his own
  terminal. With `-w` last and no value, `security` prompts for the value, so it never appears on
  screen or in shell history. He never pastes it into chat. The session tells him exactly what to
  create, with which scope, and which service name to store it under.
- **Values stay in one process.** Read a secret into a variable inside a script, use it, and print
  only status codes, field names, sha256 prefixes and lengths. Never pass a value as a tool argument
  or echo it.
- **No infraops MCP tool for anything that could return a secret.** Call the narrow API directly
  and project the fields in-process.
- **Every parsed `bws` call passes `--color no`.**
- **Every expected failure is named before the probe runs** (for example 401 or 403). A 5xx, a
  timeout or any unnamed status is indeterminate: record it as unmeasured, never as a refusal.

### 1. Coolify token scope

- Measure on the **dev Coolify** in the OrbStack `ubuntu` machine (`ssh orb`), never production,
  against a throwaway application with a throwaway placeholder variable. Record both instances'
  Coolify versions. If they differ, the dev answer is a hypothesis: say so when putting the decision
  to Devon, and make confirming it on production the first step of the increment that first uses
  the token.
- Coolify's documented scopes put restart under `deploy`, not `write` (provider-docs, "Token
  scopes"). Decision 7's retire-old step writes an env var and then restarts, so the candidate
  token needs both. Read the permission names from that Coolify version's UI or source, not from
  this handoff.
- Devon creates three dev tokens:
  - **Candidate:** `write` and `deploy`, without `read:sensitive`.
  - **Negative control:** `read` only.
  - **Positive control:** `read` and `read:sensitive`.
- Measure four things with the candidate:
  - **The env write.** PATCH the placeholder variable. The `/envs` rule says PATCH may return 500
    whatever the scope; if it does, measure the delete-and-recreate fallback instead, which is what
    the executor would use. The `read`-only token must fail the same write with 401 or 403.
  - **The readback.** Read the placeholder with the positive-control token and compare its sha256
    prefix with the written value's, in-process. The candidate may not be able to read it back.
  - **Masking.** GET the throwaway application's envs with the candidate and print only whether
    `real_value` is present and whether it's masked. Run the same GET with the positive-control
    token, which must show the field unmasked; without that control, a masked answer proves
    nothing. Parse in-process: a token wider than expected returns real values.
  - **The restart.** Restart the throwaway application with the candidate. The `read`-only token
    must fail the same call with 401 or 403.

### 2. OpenRouter management key

- Devon creates a throwaway management key with the smallest scope the console offers, and an
  expiry if the console offers one. That answers half of question 2 by observation.
- The key is unscoped on Devon's real account and can delete any key, including the brains' key.
  The probe script must refuse any PATCH or DELETE whose hash isn't the one from its own create
  response.
- With it, mint one throwaway API key that has a credit limit of the smallest allowed value. Record
  whether the create call accepts an expiry. Then call the key-info endpoint with the throwaway key's
  value and record whether the response carries the identifier the delete call needs. Read the
  endpoint paths from OpenRouter's documentation at run time.
- The legacy executor uses `/api/v1/auth/key`. Record whether it answers like `/api/v1/key` for the
  same key (requirements, "Facts to measure", item 2).

### 3. Disabled and deleted keys

- Using the throwaway key from question 2: probe it (expect 200), disable it and probe, then delete
  it and probe. Run a deliberately malformed key through the same probe as the control. Record each
  status code with a timestamp.
- Make the delete call with the identifier the key-info endpoint returned in question 2. A
  successful delete shows that identifier is the one revoke needs, instead of judging it by field
  names.
- If a disabled or deleted key still answers 200, re-probe every minute for up to 30 minutes and
  record when it flips, or that it didn't.

### 4. Revoked BWS machine token

- **Run question 5 first.** It reads with this question's token, which this question revokes.
- Devon creates a throwaway machine account, `probe-inc2-reader`, with read on one throwaway
  project, and its token.
- Run `bws project list --color no` with that token (expect success). Pass the token through the
  script's environment from the Keychain read, never as an `--access-token` flag, which would put
  it in `ps` and in the tool call's arguments. Then Devon revokes the token.
- Decision 9 says a revoked BWS token can read live for up to an hour. Probe straight after the
  revoke, then every 5 minutes for up to 75 minutes. Record each result's exit code, message shape
  and timestamp, and when it first fails.
- The control is the same token with its secret part altered in-process, which shows how a
  malformed token fails differently, if it does.
- Decision 3 says the executor talks to BWS through the SDK, not the CLI. If the Bitwarden SDK
  installs in a scratch environment, run the same probes through it. If it doesn't, record that the
  CLI answer may not carry over.

### 5. Moving a BWS secret between projects

- Devon creates two throwaway projects and a second throwaway machine account,
  `probe-inc2-writer`, with write on both. `probe-inc2-reader` has read on only the first project.
- The writer creates a secret in the first project holding a random placeholder that isn't a
  credential. `bws secret create` takes the value as an argument; that's acceptable only because
  the placeholder isn't a secret.
- Move the secret with `bws secret edit --project-id` or the equivalent, as the writer. Confirm
  the result by listing both projects: the secret's UUID appears under the second project, and not
  under the first.
- Then read it by its UUID with `probe-inc2-reader`. BWS answers 404 both for a secret the account
  may not read and for one that doesn't exist, so read a UUID known not to exist beside it, with the
  same account. The answer is "no longer readable" only if the moved secret's result matches that
  control and differs from the reader's result before the move.

### 6. GitHub App installation tokens and Actions secrets

- **The dispatch App is not used.** Its installation lacked the `secrets` permission when last
  measured (`credentials.md` #274, 2026-09-02), and minting its token needs the live private key in
  the production container. Record that measurement, dated, as the dispatch App's answer.
- **The capability is measured with a throwaway App**, the shape of the dedicated rotation App
  Devon chose for increment 7:
  - The session creates a private throwaway repository, `probe-inc2-secrets`, with `gh repo create`.
  - Devon creates a GitHub App, `probe-inc2-app`, with the repository permissions **Secrets: write**
    and **Metadata: read** and nothing else, and installs it on that repository only. He downloads
    its private key to `~/.probe-inc2/app.pem` with mode 600. The key is a throwaway, revoked when
    the App is deleted.
  - The script reads the key in-process, signs the App JWT, and mints an installation token
    limited to that repository. It reads the token's `permissions` from the mint response.
  - It writes one placeholder Actions secret: it fetches the repository's public key, seals the
    value with it, and PUTs the secret. Then it lists the repository's secrets and confirms the name
    is there with a fresh `updated_at`.
  - **The control** is a second token from the same App, minted with its `permissions` narrowed to
    `metadata: read`. The same PUT must fail with 403.
- Sealing needs libsodium (PyNaCl) and signing needs PyJWT. Run them with `uv run --with` from a
  scratch directory, never in this repository's environment.
- Granting `secrets` to the dispatch App, or installing any App on a repository other than the
  throwaway, is a standing-authority change that's Devon's and isn't part of this increment.

### 7. Root access to an isolated OrbStack machine

- Create `probe-inc2-root` with `orb create --isolated --isolate-network`, the shape decision 2
  specifies. Run `orb -m probe-inc2-root -u root id -u` with stdin closed and a timeout, so a
  password prompt can't hang the session. macOS has no `timeout` command; use `gtimeout` if it's
  installed, or a background process that the script stops after 30 seconds. Expect `0` for "works
  without a password".
- The control is the same command as the machine's default user, which must print a nonzero uid,
  so `0` is shown to come from `-u root` and not from the command.
- Delete the machine afterwards.

## The decision for Devon

After the measurements, put the Coolify token to Devon as a standing-authority decision. Lead with
the option that adds the least machinery (`memory: devon-prefers-less-new-machinery`). Show each
option with what it exposes and how many human steps it removes from the per-class table:

- **No Coolify token in the executor.** Every hosted deploy and restart stays a human step, which
  is ten of the eighteen live credentials (ADR-0055 consequences).
- **A `write` and `deploy` token without `read:sensitive`, if question 1 shows one works**, held in
  its own `Rotation / Coolify` project that only the executor reads. Name what it can still do: env
  writes and restarts on every application in the team, deletes, and anything else that version's
  `write` and `deploy` cover.
- **Any narrower holder the measurements reveal**, such as a token scoped to some applications,
  if that Coolify version supports one.

Record his answer, quoted, in the amendment. If the answer changes the threat model's asset table,
update the table in the same change.

## Constraints

- **Don't build rotation machinery,** don't change `factory-policy.toml`, and don't create the
  `Rotation /` projects or the real rotation App. Those are increments 3, 4 and 7.
- **Don't touch a live credential,** and don't read one. A probe that needs a real credential is
  out of scope; record the question as unmeasured.
- **Edit only in a worktree** (`.worktrees/rotation-inc2`); the scheduled lanes run from the main
  tree.
- **If any probe surfaces a value** in a transcript, log or file, stop. Disclose it to Devon at
  once and backlog its rotation as P1 (`memory: no-manual-cred-rotations`); deletion isn't enough.
- **Send the amendment through an independent review before Devon reads it.** The reviewer checks
  that each recorded answer follows from a discriminating probe, and that each "changes increment N"
  claim is right.

## Closing steps

1. Delete every `probe-inc2-` resource:
   - OpenRouter keys, including the management key, which Devon deletes in the console;
   - both BWS machine accounts, their tokens and both projects;
   - the three dev Coolify tokens and the throwaway application;
   - the `probe-inc2-app` GitHub App (Devon, in the UI), its key file `~/.probe-inc2/app.pem` and
     the directory, and the `probe-inc2-secrets` repository (with `gh repo delete` if the session's
     login has the `delete_repo` scope, otherwise Devon deletes it in the UI);
   - the OrbStack machine;
   - every `probe-inc2-` Keychain item.

   Confirm each deletion with a probe or a listing, and list the confirmations in the final report.
2. Devon decides the Coolify question and accepts the amendment. The session that wrote the
   amendment merges it.
3. Resolve this increment's backlog item, noting the amendment's PR.
4. Check the main tree's `git status` for stray files, then remove the worktree and its branch
   after confirming the PR is merged.
5. Write the increment 3 handoff, the orchestrator and package changes, if Devon asks for it.
