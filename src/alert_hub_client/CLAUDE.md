# Known Non-obvious Invariants, continued from the root `CLAUDE.md`: The alert-hub client (`src/alert_hub_client/`): keyed alert state

- A `KeyedAlerts` state file has one `sync` caller: `sync` resolves every remembered key that isn't
  firing or unread, so two passes sharing a file resolve each other's alerts. The activation sweep
  keeps one per subcommand (`test_one_subcommands_sync_never_resolves_the_others_keys`).
