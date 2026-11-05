# Known Non-obvious Invariants, continued from the root `CLAUDE.md`: The orchestrator API layer (`src/orchestrator/api/`): observer confinement

- OBSERVER's only write is `POST /api/v1/observations`, confined in one place,
  `api/dependencies.py::_confine_observer`, by route template with unknown routes refused. All
  observe-and-report producers share `orchestrator-observer`. History:
  `docs/history/claude-md-invariants-archive.md` #144.
