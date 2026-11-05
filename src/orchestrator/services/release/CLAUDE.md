# Known Non-obvious Invariants, continued from the root `CLAUDE.md`: Release services (`src/orchestrator/services/release/`): deployment observations

- `deployment_observation` summaries are strict `extra="forbid"` models in
  `services/release/deployment_observations.py`, published in OpenAPI and held to each producer's
  copy by `tests/contract`. `container_image` needs four summaries plus an optional dispatch one,
  `machine_local` only `activation_summary`; the secret detector rejects any key name containing
  a part in `kernel/secret_metadata.py::SECRET_KEY_PARTS` (`token`, `credential`, `log`, `body`,
  `response` among them). Detail: `docs/operations/post-deploy-verification.md`. History:
  `docs/history/claude-md-invariants-archive.md` #63.
