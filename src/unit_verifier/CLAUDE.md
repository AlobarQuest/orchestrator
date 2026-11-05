# Known Non-obvious Invariants, continued from the root `CLAUDE.md`: The unit-verifier lane (`src/unit_verifier/`)

- The verifier lane (`src/unit_verifier/`, `scripts/run-unit-verifier.sh`) takes a rotation unit,
  a bump, or a unit with a dispatch and an armed head that isn't `post_deploy_verification`. Each
  `automated_check_ac_ids` criterion gets every required check, sorted, stopping at the first that
  didn't succeed; an unanswered check never reaches `/verify`, and the evidence key names the
  criterion. The server trusts the VERIFIER identity on the check's name, so
  `test_verification_edges.py` pins that shape (decision 2 of the Verification spec). A unit with
  nothing to file goes to `/verify`, which decides what its evidence allows and gives the rest to a
  person, including one whose repository requires no check (a 404 included), unless a check is
  already filed (`named_check_filed`): that app-change unit is held as `verify-refused`, because the
  filed row would pass the criterion alone (`test_a_filed_check_then_a_lost_required_list_holds_the_unit`).
  A bump with no list always alerts instead. A unit with no criteria never completes from
  `/verify`: it goes to `awaiting_review`, and only that human gate completes it
  (`test_a_unit_with_no_criteria_goes_to_a_person`, `_completion_satisfied`). Only an app-change unit reads the member
  record: no grant is `verify-refused`, and an empty or unread record holds the unit silently.
  `image_release` shares the `orchestrator-verifier` identity. Detail: `docs/operations/verifier.md`.
