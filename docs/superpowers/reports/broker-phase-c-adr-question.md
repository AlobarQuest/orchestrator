# Questions for Devon: ADR-0055 Amendment 32 (broker Phase C)

Worker W-306, 2026-11-24. The handoff named these as stop points. Amendment 32 records each as an
"Open, for Devon" item and settles none of them. The item numbers are the amendment's decision
items. Each recommendation is the worker's, not a decision.

## 1. The unlock tier's mechanism (Open item 6; amendment 30 item 8)

The design's idea is a Touch ID unlock tier for the keys that mint others (sds-hq
`docs/superpowers/specs/2026-11-20-lane-secrets-broker-design.md`, lines 198-200).

- **A. A named tier with no mechanism, for development.** The registry marks the broker's token (and
  any other key that mints or reads for others) as unlock-tier, and each use stays Devon's browser
  act. Nothing new is built.
- **B. Touch ID on the Mac.** Unlock-tier keys sit in Keychain items with a biometric access
  control. A run that needs one prompts Devon on the Mac. This needs a Mac helper, Devon present
  for each use, and a decision on which keys are in the tier.
- **C. A time-boxed grant.** Devon gives a read grant for one sitting and removes it afterwards.
  BWS has no expiring grant, so the removal is a second act.

**Recommendation: A for development**, with B designed in its own amendment when the security phase
reopens D-64. The broker's token is the only key the lanes depend on after Phase C, and Bitwarden
has no API to mint one, so B adds a prompt to an act that is already Devon's.

## 2. How the broker's own token rotates (Open item 7; amendment 30 item 8)

Every rotation has a mint by Devon, because Bitwarden has no API to create or revoke an access
token.

- **A. By hand, from a runbook.** Devon mints, pastes into `SECRETS_BROKER_BWS_ACCESS_TOKEN` and
  the keeper, a session redeploys the broker and runs one lane's `lane-run --read`, then Devon
  revokes the old token. The row stays `rotated_by_sds = false`.
- **B. Through the executor's `bws-lane-token` adapter.** The row becomes `rotated_by_sds = true`
  with the broker resource as its destination. Devon's act is the mint gate. The executor writes
  the variable and keeper, the session redeploys and proves, and Devon revokes the old token after
  the `loaded` match (amendment 29).
- **C. Not rotated on a schedule.** It rotates only on an exposure (Infra Brain 1032).

**Recommendation: B.** The adapter, gate and proof already exist for lane tokens, and amendment 30's
Phase C text expects `rotated_by_sds` to become true for this row. The broker's restart makes each
lane fail at most one read, which amendment 30's Consequences already accept.

## 3. A `--token` run on a broker lane (Open item 10; amendment 30 item 10)

Phase B didn't settle it. The run is refused at plan time today.

- **A. Stays refused.** A broker lane's read proof is a `--tag` run plus `lane-run --read`,
  reported by the session (the amendment's item 4, step 7).
- **B. A read-proof-only run.** `--token` on a broker lane changes no compose or variable. It runs
  `lane-run --read` over the lane's allowlist list on the running container, and `lanes-finish`
  records the result. That gives the revoke precondition a recorded run, as a token lane has.
- **C. A new verb** (for example `--prove`) with B's behaviour, leaving `--token` refused.

**Recommendation: B or C**, whichever the deploy command's code makes smaller. A recorded proof is
easier for HQ to check before the revoke sitting than a session's report. Amendment 32's
precondition (item 5) doesn't use the run, so either answer leaves it valid.

## 4. Whether lanes may move before the broker's token can rotate (Open item 8)

Each batch widens what the broker's token reaches.

- **A. Yes.** Batches proceed while question 2 is open. The token can still be rotated by hand (2A)
  if it must be.
- **B. No.** Settle question 2 first, then start batch 1.

**Recommendation: A.** The hand route exists today, and each batch removes a lane token, which
shrinks the number of tokens that need a mint sitting.

## 5. The grants outside `Rotation / Keepers` (Open item 9)

Three of the 18 new grants aren't in the project that already takes single-secret grants:

- `factory-events-append-password` (`2ce0dbc7-…`, project `Factory Events`): the write role.
  ADR-0059 Amendment 17 gave the broker the read password by a single-secret grant.
- `coolify-revision-watcher-read-token` (`0bf066e9-…`, project `Ops / Platform`).
- `APP_BRAIN_READ_KEY` (`726a18ba-…`, project `brains`).

Options for each:

- **A. A single-secret read grant** to `lane-secrets-broker`.
- **B. A project grant**, following amendment 30's "Project by project".
- **C. No grant.** The lane stays on its token for that secret, which keeps the lane's token and
  blocks its retirement.

**Recommendation: A for all three.** `Ops / Platform` holds the Anthropic and dispatch-App keys that
no lane reads, so a project grant there would give the broker far more than the lane needs. The
append password is the factory-events write role, so it needs your explicit grant.

The other 15 grants (all in `Rotation / Keepers`: 12 ping URLs, the observer bearer,
`M2M_TOKEN_PROPOSE` and `M2M_TOKEN_OBSERVE`) are gate steps in the amendment's item 3, named and
not given.

## 6. How a broker lane stays registered (Open item 13)

`lanes_in_registry` registers a lane only by its `<LANE>_BWS_ACCESS_TOKEN` registry consumer. So the
four Phase B broker lanes are outside `prepare`'s lag check and `lanes_to_follow` today, and
removing the 13 token rows would remove those lanes too.

- **A. Registration also reads the allowlist.** A lane is registered by its token row or by its
  allowlist table, held equal by a test. This also covers the four Phase B lanes.
- **B. A registry marker per broker lane**, for example on the ping-URL row's consumer.
- **C. Accept it.** Broker lanes follow releases by hand, and the lag check doesn't cover them.

**Recommendation: A**, in its own pull request before batch 1. It needs no new registry shape, and
the allowlist already decides `broker_lane`.

## 7. The lane machine accounts at the revoke (Open item 14)

- **A. Revoke the tokens only.** The accounts and their grants stay, unused.
- **B. Revoke the tokens and delete each account** in the same sitting, which removes its grants.

**Recommendation: B.** An account with grants and no token is unused reach. Deleting it is the same
sitting and no extra mint.

## 8. HQ's unregistered secrets (Open item 11)

The design counts six. No record this worker could read names them.

**Recommendation:** HQ lists them in its next handoff (name, BWS UUID, reader, owning repository),
and a worker registers each in that repository's `.cred-consumers.toml`.

## 9. The factory-events passwords (Open item 12)

The design places "the factory-events passwords turned on" in Phase C. The handoff and amendment
30's Phases list don't, and amendment 24's open item FE-4 (their rotation type) is still open.

- **A. In Phase C**, as its own amendment that decides FE-4.
- **B. Out of Phase C**, with FE-4 left to the factory-events work.

**Recommendation: B.** The lane batches don't depend on it, and FE-4 is a rotation-type decision
with its own record.
