# Known Non-obvious Invariants, continued from the root `CLAUDE.md`: The rotation decomposer lane (`src/rotation_decomposer/`)

- The `rotation-decomposer` lane proposes, as `orchestrator-system`, the decomposition of each
  approved rotation record's registered revision that has no units and no proposal: the derived
  plan the intake's own criteria describe (`select_plan`), every envelope one `rotation_known_good`
  recognises, `touches_no_hosted_service` set by `plans.writes_hosted_service` (held equal to the
  executor's `checks._check_destination_fit` by a test), never `host_without_overlap`, and no
  person-act dependency. It never proposes over a proposal a person rejected, sent back or
  superseded; a package on a pre-derivation plan is refused. Detail:
  `docs/operations/rotation-executor.md`, "The rotation decomposer".
