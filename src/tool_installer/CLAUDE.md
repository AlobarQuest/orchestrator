# Known Non-obvious Invariants, continued from the root `CLAUDE.md`: The tool installer (`src/tool_installer/`): plugin and command rows

- A plugin row's installed revision is `plugin.loaded_entry`: the serving folder's `HEAD` when
  Claude Code's record names the folder's version and the folder is clean and on the branch,
  else the record's `gitCommitSha`, which Claude Code rewrites only on a version change. The
  measurement and `verify` must both use it. Detail: ADR-0042 amendment 3.
- The `n8nac` row owns `<npm prefix>/bin/n8nac` (`/opt/homebrew/bin/n8nac`): `npx` resolves a
  command there and downloads from the registry when it's missing, so never remove that link
  without the row's replacement. Detail: ADR-0042 amendment 2.
- The `modern-web-guidance-cli` row owns `<npm prefix>/bin/modern-web-guidance`: the hardened
  skill runs that bare name, and the lane's entry it points at is the wrapper that turns the CLI's
  telemetry off. Never point it anywhere else or remove it without the row's replacement. Row
  names must stay unique (alerts, backups and observations are keyed by them), so a command that
  shares its plugin's name is named in `RegistryInstall.binary`. Detail: ADR-0042 amendment 5.
