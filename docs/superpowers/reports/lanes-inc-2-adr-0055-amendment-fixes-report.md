Outcome: done

# Report: lanes-inc-2-adr-0055-amendment-fixes (fix round 1 of 2)

Worker W-37, 2026-11-07. Base: PR #626's head `77fd38d4`. Docs only: the one changed file is
`docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md` (amendment 19), plus
this report. Line numbers are in the file at this branch's head.

## What I built

### Blocking finding 1: the keeper at rest was stated as decided

- **Item 1 (lines 2223-2234).** It now separates the two keepers. The ping URL's keeper is the
  lane's own ping-URL secret, already approved (ADR-0059 decision 3, Q10, D-13). The lane token's
  keeper in `Rotation / Keepers` is marked "proposed; Devon decides (HQ D-24)", and the item names
  items 3, 4, 7 and 10 as depending on it. This also covers the Sonnet follow-up to reword the
  keeper sentence.
- **Item 3 (lines 2242-2267).** A closing paragraph says the token issue's "keeper holds the
  marker" precondition depends on D-24. Under transit-only there's no keeper to read the marker
  from, so that alternative needs a different precondition, which the amendment doesn't design. A
  ping URL's issue is unaffected.
- **Item 4 (lines 2268-2288).** A closing paragraph says the "isn't the keeper's value" and "no
  other keeper holds it" checks depend on D-24. Under transit-only, reach alone can't tell the
  current token from a new one. A fingerprint-only keeper would restore those checks but not
  `retire_old`'s, and that's a different design.
- **Item 7 (lines 2317-2344).** A closing paragraph says `retire_old` and confirm-dead test the
  old token, so a reissue depends on D-24. Under transit-only a reissue needs a different plan.
- **Item 10 (lines 2372-2391)** and the cut-over intro in item 8 (lines 2345-2365) point back to
  D-24. The threat-model consequence (line 2461) is conditional on D-24, and ping URLs are at rest
  either way.

### Blocking finding 2: no lane phase redeployed a lane after a reissue

- **Item 6 (lines 2299-2316).** It now carries a sub-bullet. ADR-0059 increment 2's code adds a
  `--token` lane-phase mode, which amends ADR-0059's lane phases. The mode is carried as a verify
  item in increment 2 and built before `execution-watch` holds a real token. It writes the lane's
  compose at its running tag, as `--schedule` does, and writes no variable. Its only calls are the
  deploy command's existing `coolify_update_service` and `coolify_deploy`, under the same
  quiet-slot reads. Its `lanes-finish` passes when four reads hold: the digest, the schedule, and
  the volume are unchanged, `BWS_ACCESS_TOKEN` is non-empty, and `lane-run --read` reads every
  listed secret `ok`. A failure exits 2, HQ records it, and the revoke stays unresolved.
- Items 7 and 10 now name `--token` as the redeploy. Two more places carry it: "Open before
  building" (line 2448) and "Builds" (line 2470).

### Follow-ups

- **Intro (lines 2191-2198).** D-5 placed the lift in increment 7, and D-10 (b) moved it to
  increment 2. The intro now cites D-10 for the timing.
- **Item 3 (lines 2250-2261).** On a marker-only keeper:
  - Quarantine: `store` quarantines nothing and writes only after it reads the marker. A retried
    `store` whose keeper matches the staged value's fingerprint treats the write as done. Any
    other value fails the unit.
  - New is never old: the marker's fingerprint is the old fingerprint.
  - Staging: the staging secret in `Rotation / Work` is deleted, with the deletion recorded, once
    `verify_consumers` passes.

  The item also cross-references item 7's R10 departure.
- **Item 7's R10 departure** is added to "Questions for Devon" (lines 2451-2458). Item 7 marks the
  departure as proposed.
- **Item 7, non-leak reissue (lines 2328-2333).** While the old token still answers,
  `lane-run --read` proves that the redeployed container works after the `PATCH`'s 2xx. It
  doesn't prove that the container holds the new token. The revoke tells the two apart, and the
  dead-man check reports a failure.
- **Item 5 (lines 2289-2298).** The VM's read-only key gets no `ping_url` or `uuid`, and the
  Management key manages every check. So the bind is by shape alone unless Devon grants a key,
  which would be a new VM credential. The first item of "Open before building" matches, and the
  question list includes the key grant.
- **Item 8 (lines 2345-2365).**
  - It states that the order rests on HQ making the ping-URL row due after the token's `store`.
    A mistake fails safe: `other_mints` refuses both binds, and the pasted value stays in the
    inbox with its reason until HQ fails and cancels the ping URL's unit.
  - The cut-over gains steps 1 and 2 before the grant: the keeper command, then the registry rows
    in two changes.
- **ADR-0059 Consequences "staged by hand"** gets a note that item 10 amends it (lines
  2388-2390).
- **"Open before building" (lines 2426-2458).** The section opens with "Each item is settled in
  ADR-0059 increment 2", and each item names increment 2.
- **`_REACH` citation** is now `bws_machine_token.py`, lines 100-116 (line 2276). I checked it
  against the source: `_REACH` starts at line 100, and its string closes at 116.

## Tests and checks run

- `git diff` reviewed in full. Two commits: `eecd8653` and the wording follow-up.
- Token-shape grep: `grep -nE '0\.[0-9a-f]{8}-'` over the ADR returns nothing.
- Line-width check over amendment 19: every line I wrote is 100 characters or fewer. I also
  reflowed four overlong lines that were already in items 3 and 9.
- `make check` not run. The change is ADR prose only, with no code, config, or test changes.

## Files changed

- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md` (amendment 19 only)
- `docs/superpowers/reports/lanes-inc-2-adr-0055-amendment-fixes-report.md` (this file)

## Self-review

- The `--token` sub-bullet in item 6 runs past the "one or two sentences" the handoff asked for.
  I kept it. The finding was about `lanes-finish` passing only "the change and no other", so the
  mode's pass criterion had to be stated.
- The ping URL's keeper "in `Rotation / Keepers`" (item 1) follows amendment 6's pattern and
  `adopt-keeper`'s `keepers_project_id`. ADR-0059 decision 3 approves the secret and its grant but
  names no project.
- Item 8's fail-safe exit ("HQ fails and cancels the ping URL's unit, as a stranded unit is")
  follows the repository's invariant for stranded units. I didn't read code for a rotation-unit
  path that retires a unit this way.

## Concerns

- **ADR-0059's own text isn't updated.** Three passages still leave the redeploy to the executor
  at increment 7: the lane-phase bullets, increment 7, and the Consequences at about line 1299.
  This round was docs-only to ADR-0055, so ADR-0055 records the amendment, and a follow-on change
  edits ADR-0059 to match.
- **Pre-existing overlong lines.** Lines 2403 and 2405 in "What the code settles" are 104 and 101
  characters. They predate this round, and I left them.
- **Out of the Standards section's scope.** Healthchecks credential scope (item 5's possible key
  grant) isn't covered by any listed standard.

## Standards followed

- **Code Brain rule 5 (never log secrets).** Item 11's logging text is unchanged: it names the
  UUID, the destination, and the fingerprint, never the value. The new text logs nothing. The
  `--token` mode reads only that `BWS_ACCESS_TOKEN` is non-empty, never its value.
- **Code Brain rule 4 (never silently swallow an exception).** Each new failure path ends in a
  recorded failure:
  - item 3's `store` on an unexpected keeper value fails the unit as its failure;
  - item 6's `--token` failure exits 2, and HQ records it in its report;
  - item 8's mis-ordered rows leave the value in the inbox with its reason, then HQ fails and
    cancels the unit;
  - the token row's 404 at create fails with `CoolifyRefused`, unchanged.
- **Code Brain rule 3 (structured JSON logs to stdout).** Item 11's reference to
  `rotation_worker/logs.py` is unchanged.
- **Infra Brain 591, 592, 593 (no token-shaped string in text, history, or inline).** The
  token-shape grep over the ADR is empty. Neither commit contains a value, and secrets appear only
  by role or by the BWS UUID prefixes that were already in the file.
- **Infra Brain 1032 `cred.exposure-rotate`.** Item 10's leak path still ends in revoke and
  reissue. The `--token` redeploy completes the reissue, and nothing purges.
- **Infra Brain 212 (HTTPS).** Doesn't apply: no endpoint changes.
- **Code Brain `secrets` road.** None exists, as the handoff notes.
