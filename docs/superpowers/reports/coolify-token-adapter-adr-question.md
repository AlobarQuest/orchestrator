# Question: ADR-0055 Amendment 33, the `coolify-api-token` adapter (W-314)

Amendment 33 (Proposed) plans the adapter. Five items in it are "Open, for Devon", numbered 11 to
15 in its Decision. The amendment settles none of them, and it grants nothing. Each item below has
options and a recommendation. These items don't block the amendment's pull request. They block
the adapter's code pull request, or specific rows' `rotated_by_sds`, as each item says.

The fact they all rest on: deployed Coolify (`4.0.0-beta.473`, source at tag commit `57ea0764`) has
no REST route that creates, lists or revokes an API token. Tokens are created and revoked only in
the web UI (`app/Livewire/Security/ApiTokens.php`, `addNewToken` and `revoke`). So the plan is the
Bitbucket shape: Devon mints into the inbox, the executor proves and stores, Devon revokes after
the executor's proof, and the executor confirms the old token is dead.

## Item 11: the keepers of the estate lander's and the revision watcher's tokens

The executor's BWS identity can't write `SDS Operator` (the estate lander's keeper) or
`Ops / Platform` (the revision watcher's), and `store` writes the keeper. The change window's
keeper is already in `Rotation / Keepers`.

- **A.** Devon moves each keeper into `Rotation / Keepers`, after each consumer account gets a
  single-secret read grant there and is proven to read it (decision 4). For the estate lander, the
  consumer is `lane-secrets-broker`. For the revision watcher, do it after Phase C's revoke sitting,
  when the broker is its only reader, and give amendment 32's planned broker grant on `0bf066e9-…`
  in `Rotation / Keepers` instead of `Ops / Platform`.
- **B.** A direct read and write grant for `rotation-executor` on each secret where it is. That's
  the executor's first write outside the rotation projects.
- **C.** Leave both rows manual. Only the change window's token rotates.

**Recommendation: A.** Decision 4 already says rotated keepers move to `Rotation / Keepers`, and A
adds no new kind of reach. The executor's own Coolify token already has every ability these two
have.

## Item 12: the executor creating or revoking Coolify tokens itself

The API can't do it. The only route is driving the web UI with a signed-in user's session.

- **A.** No. Devon mints and revokes.
- **B.** Give the executor a Coolify user session. That's token-admin reach, a new identity, and a
  credential outside any API.

**Recommendation: A.** Re-ask if a Coolify upgrade adds a token API.

## Item 13: proving a new token lacks `read:sensitive`

`write` and `deploy` can be proven without writing: each has a route whose ability check answers
403 before a handler that refuses without changing anything. `read:sensitive` has no such route.
Only a response body shows it.

- **A.** No machine proof. Devon's mint is the only record.
- **B.** Read one existing application's envs and check that no `value` or `real_value` field is
  present. A wrong mint puts that application's values in the executor's memory, never in a log.
- **C.** As B, against a canary application Devon creates with one variable that holds no secret,
  used only by this probe.

**Recommendation: C.** It proves the revision watcher's token stays read-only without exposing a
secret. A is the fallback if you want no new Coolify resource.

## Item 14: measurement tokens on dev-coolify

The scope probes are read from source and are unmeasured. Measuring them needs three throwaway
tokens Devon mints on `dev-coolify` (`read`; `read` + `write` + `deploy`; `read` +
`read:sensitive`), stored in a BWS project the measuring session reads, and revoked after the run.

**Recommendation:** yes, before the adapter's code pull request merges, as amendment 1 measured
before its decision.

## Item 15: proving the estate lander's read

The lander reads the token on every `--submit` pass, but a failed read isn't fatal, and its check is
pinged every pass. A green ping proves that a pass started after the store, so no consumer can be
left on the old value. It doesn't prove the read succeeded.

- **A.** Carry the gap. The next re-point or restore is its own proof, and a failed read already
  alerts `landing-not-pointed-back`.
- **B.** The executor reads the broker's per-request log lines. That's new reach: the executor has
  no Docker access on the host.
- **C.** The lane files an observation each `--submit` pass with the token's fingerprint, and the
  row names it as `observation_source` (a lane change in its own pull request).

**Recommendation: A** for the first rotation, and C if the gap is to close.
