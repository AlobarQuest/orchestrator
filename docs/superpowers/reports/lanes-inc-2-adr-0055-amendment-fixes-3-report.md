Outcome: done

# Report: lanes-inc-2-adr-0055-amendment-fixes-3

Fix round 3 on PR #626, branched from `e67c4273`. One file changed:
`docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`, amendment 19 only.
Line numbers are at this commit.

## What I built

### The blocker: the ping URL's proof

- **Item 5 (lines 2282-2295).** The text now says cut-over step 4 proves the URL, not the `--add`.
  It cites ADR-0059 "Cut-over for one lane": `--add` runs with `LANE_SCHEDULE_ENABLED=false` and
  the launcher's `--dry-run` pings nothing (step 1, ADR-0059 line 1046; step 2's first bullet,
  lines 1069-1070). The container's first scheduled pass pings the check (step 3, lines 1094-1095),
  and step 4 verifies the check's last ping came from the container (line 1096). A wrong URL shows
  as the lane's own check going overdue (its dead-man alarm), so a bad paste fails visibly. I
  checked each cited line at `e67c4273`; they match the handoff's.
- **Item 5's title** read "binds by shape and by its check". The bind is now by shape alone, so it
  reads "binds by shape, never by a ping".
- **"Open before building", the ping URL's check read (lines 2434-2441).** It replaces "the lane
  phases' first `--add` proves the URL" with cut-over steps 3 and 4 as the proof, and the overdue
  check as the visible failure.

### Devon's answers

- **D-27.** Item 7 (lines 2347-2349) states "Devon accepted the departure, for lane tokens only
  (2026-11-07, HQ D-27: "Accepted")", replacing "proposed; Devon decides it". The round-2
  conditional about what increment 7's amendment restores is unchanged (lines 2346-2347). Item 3's
  cross-reference (line 2264) reads "Devon accepted it (D-27)".
- **D-28.** Item 5 (lines 2285-2288) states the bind is by shape alone (Devon, 2026-11-07, HQ D-28:
  "No Key"), keeps the shape rule (ping base plus one UUID, held by no other keeper), and names
  cut-over step 4 as the proof. The "unless Devon grants", "Devon decides whether to grant one",
  "either way" and "Without one" wording is gone.
- **Questions for Devon (line 2465).** The list is replaced by one line: none open, D-24 to D-28
  answered and recorded in this amendment.
- **The header (line 2187): left as `**Status: Proposed.**`.** Earlier decided amendments use
  `**Status: accepted by Devon on <date>** ("<his words>")` (amendments 11-15) or
  `**Status:** accepted (Devon, <date>: "<words>")` (amendment 16), quoting his acceptance of the
  amendment as a whole. D-24 to D-28 answer its open questions, but none of them quotes an
  acceptance of amendment 19 itself, so I had no words to record. HQ can change the header once
  Devon accepts the amendment. The `### Decision proposed` subheading stays, as accepted amendments
  11, 12, 13 and 15 keep theirs.

## Tests and checks run

- `grep -cE '0\.[0-9a-f]{8}-'` over the ADR: 0 matches.
- `git diff --name-only`: only the ADR (and this report, in its own commit).
- Searched amendment 19 for "decides", "either way", "Without one" and "proposed": none left on the
  D-27 or D-28 decisions. Remaining hits are "increment 7's amendment decides whether...", D-26's
  quote "I approve the proposed answer", and the `### Decision proposed` subheading.
- No new line over 100 characters. The lines over 100 are the ones that were already there.
- No `make check`: docs-only change, and the handoff limits me to the one file.

## Files changed

- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md` (amendment 19)
- `docs/superpowers/reports/lanes-inc-2-adr-0055-amendment-fixes-3-report.md` (this report)

## Self-review

- The edit says the `--add` doesn't prove the URL, instead of only leaving it out, so a reader of
  the round-2 text sees the correction.
- For estate landing, inert landing and the rotation proposer, step 2's reporting runs do ping the
  real check (ADR-0059 lines 1073-1074), so for those lanes a ping arrives before step 4. That
  doesn't change the proof: step 4's last-ping check covers every lane. I didn't add this to the
  text, to keep it short.
- I changed nothing outside amendment 19 and took up no other review follow-up.

## Concerns

- The header stays `Proposed` (see "The header" earlier in this report). HQ needs Devon's
  acceptance of the amendment as a whole to change it.

## Standards followed

- **Code Brain rule 5 (never log secrets).** Item 11's logging text (UUID, destination,
  fingerprint, never the value) is unchanged. The text holds no ping URL, only its shape.
- **Code Brain rule 4 (never silently swallow an exception).** The ping-URL proof now ends in a
  visible failure: a wrong URL shows as the lane's check going overdue, and step 4's last-ping
  verification fails.
- **Code Brain rule 3 (structured JSON logs).** Item 11's reference to `rotation_worker/logs.py` is
  unchanged.
- **Infra Brain 591, 592, 593 (no token-shaped strings).** The grep over the ADR found 0 matches. I
  wrote no token, in the text or the commit.
- **Infra Brain 1032 `cred.exposure-rotate`.** Item 10's leak path (revoke first, then reissue) is
  unchanged.
- **Infra Brain 212 (HTTPS).** Doesn't apply: no endpoint changes.
- Code Brain has no `secrets` road; nothing in this round needed one.
