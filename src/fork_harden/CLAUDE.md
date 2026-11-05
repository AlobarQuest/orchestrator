# Known Non-obvious Invariants, continued from the root `CLAUDE.md`: Fork hardening (`src/fork_harden/`)

- A fork's `hardened` branch (ADR-0057) is written only by `fork-harden`, rebuilt each pass as the
  rules applied to `main` with the previous `hardened` and `main` as parents, and never
  force-pushed: the installer's serving clone pulls it `--ff-only`. Never open a pull request from
  it (`delete_branch_on_merge` would delete it). A tool row moves to or from `hardened` only with
  its plugin serving clone checked out on that branch in the same step. Detail:
  `docs/operations/fork-hardening.md`.
