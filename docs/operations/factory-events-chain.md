# Factory-events chain from a session

An interactive Claude Code session appends to the factory-events database chain through one
wrapper, `scripts/factory-events-session.sh`. It runs one of two commands: `approve`, on Devon's
word, and `import-file`, the one-time history import. The wrapper reads the append password through
the `factory-events-session` BWS account and gives it, with the three non-secret settings, only to
the child it runs. Nothing secret appears on the session's command line, so nothing secret reaches
its transcript.

For the decisions behind it, see ADR-0055 amendment 25 items 3 and 4 and amendment 26, and the
FE-5 cut-over plan (`docs/superpowers/plans/2026-11-14-fe-5-cutover.md`, "The switch" and P-OR-2).

## What the wrapper needs

- The Keychain item `Claude` / `BWS_ACCESS_TOKEN_FACTORY_EVENTS_SESSION`, the
  `factory-events-session` account's token. The wrapper reads it with `/usr/bin/security`, and
  nothing in the environment overrides it.
- The BWS secret `factory-events-append-password` (`2ce0dbc7-eeac-4bf0-8204-b50200c97516`, project
  `Factory Events`). The account can read only this secret.
- A settings file with exactly three lines, in `KEY=value` form with no quoting:

  ```
  FACTORY_EVENTS_DB_HOST=<host>
  FACTORY_EVENTS_DB_PORT=<port>
  FACTORY_EVENTS_DB_NAME=<database>
  ```

  The file holds no secret. The wrapper parses it with the switch reader in `scripts/sds-bws.sh`
  and never sources it.

The wrapper doesn't use a `BWS_ACCESS_TOKEN` from your environment, and it doesn't need one.

## The switch and staging files

The two files are the same three lines at two paths, and each command reads exactly one of them:

`~/.factory/chain-db.env` (the switch)
: Its presence switches every holder to the database chain. `approve` reads its settings from this
  file, and refuses when it's absent.

`~/.factory/chain-db.env.pending` (the staging copy)
: The FE-5 act writes it before the import (act step 5). Only `import-file` reads it, and only
  while the switch is absent. The act then moves it into place as the switch (act step 9).

The order matters. With the switch present, an `approve` appends to the chain, and `import-file`
refuses a non-empty chain. So the wrapper refuses `import-file` whenever the switch exists, even as
a symlink.

## Approve a package

To approve a package as Devon, on his word, run the following command:

```bash
~/Projects/orchestrator/scripts/factory-events-session.sh approve \
    ~/Projects/intent-packages packages/<package-id>
```

The wrapper runs `<checkout>/.venv/bin/python -m intent_packages approve <package-dir>` in the
checkout's directory, because `approve` records the working directory's `git rev-parse HEAD` as the
approval's commit. A relative package directory is resolved from your current directory before the
wrapper enters the checkout.

No other argument passes through. The approver is the CLI's default, `devon`, and the event's actor
is `FACTORY_AGENT_ID` when set, otherwise `claude-code-interactive`. Those are the values a session's
`approve --approver devon` records without the wrapper. A policy approval (`--by-policy`) isn't a
session's: the cycle and the bump proposer run it under their own accounts.

## Import the history

The import runs once, in the FE-5 act (step 6), with the writers paused:

```bash
~/Projects/orchestrator/scripts/factory-events-session.sh import-file ~/.factory/events.jsonl
```

The wrapper runs `~/Projects/security-standards/.venv-events/bin/python -m factory_events
import-file --path <file>`, which connects as the append role. On success it prints
`imported: <n> events, head <hash>`. The import compares the database's head with the file's
before it commits, so exit 0 means they're equal.

## Verify the chain

A session doesn't verify the chain, and the wrapper has no `verify` (D-44 B; ADR-0055 amendment 25
item 1). The import's own head comparison checks the import, and the Mac nightly's `verify`, under
its own account, checks the chain after it.

## What fails closed

Every refusal prints `factory-events-session: <reason>` on stderr and exits 2. The wrapper never
falls back to the file chain. It refuses in the following cases:

- The first argument isn't `approve` or `import-file`, or the argument count is wrong.
- The package directory or events file is empty or starts with `-`.
- `approve` runs without the switch, or `import-file` runs while the switch exists or without the
  staging copy.
- The settings file isn't a regular file, or has an unknown, missing, repeated or malformed key.
  The message names the file, the line and the key, never a value.
- The child interpreter is missing.
- The Keychain item is missing, or BWS returns no password.

Every refusal in that list except the last item comes before the wrapper reads any credential. When
the child runs, the wrapper exits with the child's code.

The wrapper unsets its own variable names and the five chain variables before it runs, so a value
exported by your shell or by `~/.factory/env` can't reach the child. It leaves a retired
`FACTORY_EVENTS_*_DSN` set, so the child refuses it by name. The token reaches only `bws`, as an
assignment prefix, and the password reaches only the child. Neither appears in an argument list, on
either output stream, or in a file.

The tests are in `tests/scripts/test_factory_events_session.py`.
