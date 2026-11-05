# Known Non-obvious Invariants, continued from the root `CLAUDE.md`: The landing ledger (`src/landing_ledger/`)

- Landing-ledger observations are content-addressed and immutable: a change to what a producer
  derives must, in the same change, handle every stored row whose facts would move, or each write is
  `observation_conflict`. Detail: `docs/operations/landing-lanes.md`.
