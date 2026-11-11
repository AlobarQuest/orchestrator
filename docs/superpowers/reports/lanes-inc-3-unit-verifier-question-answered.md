# Question: lanes-inc-3-unit-verifier

One fact in the handoff is wrong at its source, so I've stopped before opening the PR, as the
handoff's stop clause requires. The branch is committed and `make check` is green. Only the push
and the PR remain.

## The fact

The handoff's failure rule (under "Facts") says that in a pass with no GitHub token, check-filing
units "then wait or go to a person, as the code already does".

The code doesn't do that. With no token, `cli.main` passes `read_required_checks=None`.
`_required_names` then raises `VerifierCallError` ("no GitHub token to read the required checks
with"), and `run_pass` catches it as `REFUSED`. So in a tokenless pass the following happens:

- Every bump unit is held unverified as `verify-refused`, with its keyed alert.
- Every app-change unit with an `automated_check` criterion is held the same way, after its grant
  check.
- Rotation units, and app-change units with no `automated_check` criterion, verify normally.
- The next pass with a token retries them all.

The original launcher's comment says the same ("fails closed on units with checks to file (they
are refused)"). The Mac has this behavior today whenever `gh` has no login.

## What I built

I followed the rule's operative part and left the program untouched:

- A failed derivation or mint logs one line, mints nothing (or discards the failed mint's output),
  and runs the pass with `ROTATION_VERIFIER_GITHUB_TOKEN` empty.
- After the pass the launcher reports 3, which pings `/fail`. A program exit of 0 or 2 becomes 3.
  An exit of 1, or an undescribed code folded to 1, stays 1.
- `deploy.md` and the launcher's comment describe the actual behavior (`verify-refused` with an
  alert), not the handoff's wording.

So a mint outage produces one `/fail` from `sds-unit-verifier` plus one `verify-refused` alert for
each check-filing unit in the queue. Each alert's text blames a refusal, not the missing token.

## The choice

1. **Keep it as built (I recommend this).** The behavior is the code's long-standing behavior,
   already true on the Mac. The `/fail` ping names the cause, and the next good pass resolves the
   alerts. The change stays inside this PR's scope, with no program change.
2. **Change the program so a missing token is `CHECKS_UNREADABLE`.** That's a closer alert kind
   ("verify-checks-unreadable"), and it's what an unreadable required-check list already maps to.
   It changes the program's alert kinds for both hosts, so I'd treat it as a separate change.
3. **Change the program so a missing token is `waiting`.** This matches the handoff's "wait"
   wording. A token outage would then raise no alert of its own and rely on the `/fail` ping alone.

If HQ answers option 1, I'll push, open the PR, mark it ready, and set the report's outcome to
`done`. For option 2 or 3, I'll make the program change in this branch with tests, unless HQ wants
it separate.
