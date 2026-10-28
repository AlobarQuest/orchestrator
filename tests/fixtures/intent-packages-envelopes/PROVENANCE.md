# How these envelopes were generated

Each `dependency_update_<tooling>_envelope.json` is the output of intent-packages'
`intent_packages.profiles.dependency_update.build_envelope` (branch `bridge-p3`, where
`CAPABILITIES` carries `github.pr.merge`), run in a scratch process against a throwaway checkout
holding the files that tooling's generators look at:

- `npm`: `package.json` (scripts `build`, `test`), `package-lock.json`, `eslint.config.mjs`,
  `.prettierrc`, so every gate component appears.
- `pip`: `requirements.txt`.
- `uv` / `uv_split`: `pyproject.toml`; `uv_split` has the pin at two sites, which adds `uv lock`.

Arguments: target `AlobarQuest/orchestrator` (an inert repository), a green conformance claim
that waives nothing, and a pin bump of a single package. The only edit afterwards is that
`constraints.work_unit_id` is the placeholder `__WORK_UNIT_ID__`, which the orchestrator stamps
at registration; the tests substitute a unit id. Regenerate by re-running `build_envelope` the
same way when the profile's command generators change.
