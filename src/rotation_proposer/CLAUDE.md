# Known Non-obvious Invariants, continued from the root `CLAUDE.md`: The rotation proposer lane (`src/rotation_proposer/`)

- `rotation_proposer` never approves a package revision. A pass that revises a standing rotation
  package to `ready_for_review` proposes nothing, and the first pass after a named human approves
  it proposes. "Due" is infraops' rule, read from `security-drift-cli cred-findings` and never
  re-implemented here. The occurrence comes from that answer's structured fields and never
  contains today's date. It acts only on a credential its registry entry marks `rotated_by_sds`; a
  package without that flag is a finding and is never acted on. Each `--submit` pass also walks rotation records, due or not, through its own GET-only
  client on the observer bearer (`rotation_proposer/client.py`): an approved record of the current
  revision with a `failed` or `cancelled` unit reports `stalled`. Its one write to infraops is
  `security-drift-cli record-rotation --date <day>`, made only for a credential's newest record
  `resolved` by `work-watcher` when the trigger isn't an exposure and the UTC day its last unit
  completed (from unit history, since change-manager serves no record date) is later than
  infraops' `last_rotated`. That field is the age anchor, served for every handed-over row and
  `null` for one with no date, and the comparison alone stops a repeat moving an age clock, because
  infraops accepts an earlier date: a handed-over id missing from it is an `error`, and an absent
  field skips recording. The exposure test reads the occurrence from the record's `reasoning`, so
  that sentence's wording is a contract with `record_occurrence`. A due credential nothing will
  move is a finding (`record-wontfix`, `due-after-rotation`), never a quiet pass.
  A revision it writes carries the registry's destinations; an approved revision that
  differs from the registry is revised, never proposed; and a due, handed-over credential with no
  package gets revision 1 generated from its row and its `class`'s type (`generate.py`,
  `CLASS_TYPES`), validated and written by intent-packages' `create-rotation`. A revision of a
  package it generated re-renders the wording only when the plan its row derives isn't the plan
  its criteria state (`generate.reissue_body`, ADR-0055 amendment 20); a hand-authored one never.
  The work
  carrier carries a standing rotation package only when its credential's registry row sets
  `rotated_by_sds` and the member record grants `credential_rotation` on that registry's
  repository. The proposer reads the same grant before it observes, revises, generates or
  proposes, never to record a finished rotation or report, and an unread record is that
  credential's `error`; the decomposer reads it before proposing. Detail:
  `docs/operations/rotation-proposer.md`.
