# Known Non-obvious Invariants, continued from the root `CLAUDE.md`: Reporting services (`src/orchestrator/services/reporting/`): traceability, evidence packs and dead letters

- The evidence-pack markdown is redacted for public PR comments (no approver ids, rationale, actor
  ids); new sections must redact by hand. A `text/markdown` route needs a `NON_JSON_SUCCESS_PATHS`
  entry. History: `docs/history/claude-md-invariants-archive.md` #46.
- The traceability observation hop reads unit-scoped observations plus the landing ledger's record
  of each release binding's merge commit, joined on `source_system`, `subject_type` and
  `observation_type` (`github`, `repo`, `landing`), the repository case-folded and the commit
  exact; other repository-, service- or environment-scoped observations appear in no chain. With
  no factory PR binding, the `pr` hop comes from that landing only when the binding's
  `implementation_pr_number` agrees, marked `source="landing_ledger"`. A `pr` anchor with
  `source_repository` matches the binding's repository case-folded too (`_resolve_pr`).
  History: `docs/history/claude-md-invariants-archive.md` #60.
- In `services/reporting/traceability.py::_resolve_pr`, a `pr_number` anchor with
  `source_repository` reads `ReleaseArtifactBinding`, without it `UnitPrBinding`; try the bare form
  before concluding a chain is absent. History: `docs/history/claude-md-invariants-archive.md` #224.
