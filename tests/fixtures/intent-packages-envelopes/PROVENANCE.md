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

`dependency_update_proposals.json` is the output of intent-packages'
`factory.decompose.build_proposal` at `02bebf5`, called directly so no dry run ran and nothing was
installed, one proposed unit per entry, beside the `profile_fields` and standing package id it was
built for. Throwaway checkouts as above, plus a single-site dev-group uv pin, a uv pin at two
sites, and an npm repository with no build script. Tests hold `bump_binding.py`'s two functions to
it. Regenerate it when the profile's commands or `_outcome` or the title change: until then a
changed generator's bumps wait for a person.
