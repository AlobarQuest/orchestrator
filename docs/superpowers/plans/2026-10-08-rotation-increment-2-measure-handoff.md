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
4. **Every throwaway resource deleted**, with the deletion confirmed by a probe, not assumed.

## Read first

- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`: decisions 2, 4 and 7,
  the per-class table, the threat model and the increment plan.
- `docs/superpowers/specs/2026-10-08-credential-rotation-requirements.md`: Q1's OrbStack
  measurements and "Facts to measure before the design commits".
- `docs/superpowers/specs/2026-10-08-credential-rotation-research.md`: provider capabilities.
- `docs/operations/credentials.md`: how the dispatch App's installation token is minted, and the
  Coolify `/envs` rule.
- `~/Projects/security-standards/docs/build-agent-secrets.md`: the secrets rules of the road.

## The seven questions

The "Consumed by" column names what in ADR-0055 depends on the answer, so a surprising answer is
traced to the increment it changes.

| # | Question | Consumed by |
|---|---|---|
| 1 | Can a Coolify API token with `write` but not `read:sensitive` PATCH an application's env var, and does it see `real_value` when it reads envs? | The Coolify decision; increments 6 and 7 |
| 2 | Can an OpenRouter management key carry an expiry? Does the key-info endpoint return the id needed to revoke a key, given only the key's value? | Increment 5's adapter; containment of the management key |
| 3 | Does a disabled OpenRouter key probe 401? Does a deleted one? | Increment 5's revoke verification |
| 4 | What does `bws` return for a revoked machine token: exit code and message? | The executor's error handling (increment 4); increment 8 |
| 5 | Does moving a BWS secret to another project keep its UUID? | Every keeper move (increments 5 to 7); the `.bws-secrets.toml` updates |
| 6 | Can a GitHub App installation token write Actions secrets? | Increment 7: App tokens or a PAT secret writer |
| 7 | Does `orb -m <machine> -u root` work without a password on an isolated machine? | Decision 2's residual-risk statement |

## How to measure each one safely

Rules that apply to all seven:

- **No live value moves.** Every key, token, project, machine and app is a throwaway made for this
  increment and named with a `probe-inc2-` prefix, so the cleanup can find them all.
- **Devon makes what needs a console or web UI** (an OpenRouter management key, a BWS machine
  account, a Coolify token). He stores each one in a Keychain item whose service name starts with
  `probe-inc2-`, by running `security add-generic-password -s <service> -a devon -T /usr/bin/security -w`
  in his own terminal. With `-w` last and no value, `security` prompts for the value, so it never
  appears on screen or in shell history. He never pastes it into chat. The session tells him
  exactly what to create, with which scope, and which service name to store it under.
- **Values stay in one process.** Read a secret into a variable inside a script, use it, and print
  only status codes, field names, sha256 prefixes and lengths. Never pass a value as a tool argument
  or echo it.
- **No infraops MCP tool for anything that could return a secret.** Call the narrow API directly
  and project the fields in-process.
- **Every parsed `bws` call passes `--color no`.**

### 1. Coolify token scope

- Measure on the **dev Coolify** in the OrbStack `ubuntu` machine (`ssh orb`), never production,
  against a throwaway application with a throwaway placeholder variable. Record both instances'
  Coolify versions. If they differ, the dev answer is a hypothesis: say so when putting the decision
  to Devon, and make confirming it on production the first step of the increment that first uses
  the token.
- Devon creates a dev token with `write` and without `read:sensitive`. Read the permission names
  from that Coolify version's UI or source, not from this handoff.
- Measure three things: whether PATCH of the placeholder variable succeeds (read it back in-process
  and compare its sha256 prefix), whether a GET of the throwaway application's envs returns `real_value` or a masked value (parse it
  in-process and print only whether the field is present and masked, because a token that turns out
  to be wider than expected returns real values), and
  whether the same token can trigger a restart. The control is a token with `read` only, which must
  fail the PATCH.

### 2. OpenRouter management key

- Devon creates a throwaway management key with the smallest scope the console offers, and an
  expiry if the console offers one. That answers half of question 2 by observation.
- With it, mint one throwaway API key that has a credit limit of the smallest allowed value. Record
  whether the create call accepts an expiry. Then call the key-info endpoint with the throwaway key's
  value and record whether the response carries the identifier the delete call needs. Read the
  endpoint paths from OpenRouter's documentation at run time.

### 3. Disabled and deleted keys

- Using the throwaway key from question 2: probe it (expect 200), disable it and probe, then delete
  it and probe. Run a deliberately malformed key through the same probe as the control. Record each
  status code with a timestamp. If a disabled key still answers 200 for some time, record how long.

### 4. Revoked BWS machine token

- Devon creates a throwaway machine account with read on one throwaway project, and its token.
- Run `bws project list --color no` with that token (expect success). Pass the token through the
  script's environment from the Keychain read, never as an `--access-token` flag, which would put
  it in `ps` and in the tool call's arguments. Then Devon revokes the token. Run
  it again and record the exit code and the message's shape. The control is the same token
  with its secret part altered in-process, which shows how a malformed token fails differently, if
  it does.

### 5. Moving a BWS secret between projects

- Use two throwaway projects and a secret holding a random placeholder that isn't a credential.
  Move the secret with `bws secret edit --project-id` or the equivalent, and compare the UUID
  before and after. Then read it by the old UUID with the throwaway machine account, and record
  whether that account still reads it after the move when it has read on only the first project.

### 6. GitHub App installation token and Actions secrets

- Mint an installation token for the dispatch App as `docs/operations/credentials.md` describes, and
  read the `permissions` from the mint response (never from `/app`). If `secrets: write` is absent,
  the answer is "not without a permission change", and the session stops there. Granting the App
  a permission is a standing-authority change that's Devon's, not part of this increment.
- If it's present, write one throwaway Actions secret to a repository the installation response
  already lists, and delete it. Don't install the App on a new repository: widening its installation
  is a standing-authority change that's Devon's.
- This is the one stated exception to "no live credential": the mint uses the App's live private
  key through the documented path, and the token is used only to read its permissions and, if
  allowed, write one throwaway secret.

### 7. Root access to an isolated OrbStack machine

- Create `probe-inc2-root` with `orb create --isolated`. Run `orb -m probe-inc2-root -u root id -u`
  with stdin closed and a timeout, so a password prompt can't hang the session. Expect `0` for
  "works without a password". The control is the same command as the machine's default user,
  which must print a nonzero uid, so `0` is shown to come from `-u root` and not from the command.
- Delete the machine afterwards.

## The decision for Devon

After the measurements, put the Coolify token to Devon as a standing-authority decision. Lead with
the option that adds the least machinery (`memory: devon-prefers-less-new-machinery`). Show each
option with what it exposes and how many human steps it removes from the per-class table:

- **No Coolify token in the executor.** Every hosted deploy and restart stays a human step, which
  is ten of the eighteen live credentials (ADR-0055 consequences).
- **A write-only token, if question 1 shows one exists**, held in its own `Rotation / Coolify`
  project that only the executor reads. Name what it can still do: restarts, env writes to every
  application, and anything else that version's `write` covers.
- **Any narrower holder the measurements reveal**, such as a token scoped to some applications,
  if that Coolify version supports one.

Record his answer, quoted, in the amendment. If the answer changes the threat model's asset table,
update the table in the same change.

## Constraints

- **Don't build rotation machinery,** don't change `factory-policy.toml`, and don't create the
  `Rotation /` projects. Those are increments 3 and 4.
- **Don't touch a live credential,** and don't read one, except question 6's stated exception. A probe that needs a real credential is out
  of scope; record the question as unmeasured.
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
   - the BWS machine account, its token and both projects;
   - the dev Coolify token and the throwaway application;
   - any throwaway repository and Actions secret;
   - the OrbStack machine;
   - every `probe-inc2-` Keychain item.

   Confirm each deletion with a probe or a listing, and list the confirmations in the final report.
2. Devon decides the Coolify question and accepts the amendment. The session that wrote the
   amendment merges it.
3. Resolve this increment's backlog item, noting the amendment's PR.
4. Check the main tree's `git status` for stray files, then remove the worktree and its branch
   after confirming the PR is merged.
5. Write the increment 3 handoff, the orchestrator and package changes, if Devon asks for it.
