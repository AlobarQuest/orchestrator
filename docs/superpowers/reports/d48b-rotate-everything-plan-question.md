# D-48 B pass: ten open items for Devon

Source: ADR-0055 amendment 38 (Proposed, this branch), "Open items". It plans your D-48 B answer of
2026-11-14: "make sure it can rotate all new credentials … making any changes we have to make to
enable the capability, and also rotates all the creds." Each item is yours because it adds reach,
reverses part of decision 4, or sets the pass's scope. The standing answers of 2026-11-06,
2026-11-18, 2026-11-26 (D-82, D-83 and D-84) already settle the rest, and the amendment says
where.

## 1. Credentials that predate the move

Of the 29 rows not handed over, 7 record a `created` before the lanes moved (2026-11-06), for
example the SYSTEM and VERIFIER bearers from July; 8 record one on or after it, and 14 record none.
Most unregistered credentials predate it too.

- **A.** Only credentials the move created, plus the factory-events passwords.
- **B.** Every credential, whatever its age.
- **C.** The tooling is made to cover every class the pass meets, but only the move's credentials
  are rotated now; older rows of a proven type are handed over and rotate at their age or on
  request.

**Recommendation: C.** Your words ask for two things: tooling that can rotate the new
credentials, and rotating them. C does both and makes the older rows rotatable as a side effect,
without a large sitting of mints and revokes for credentials nothing exposed.

## 2. The SYSTEM and VERIFIER bearers

Decision 4 keeps them hand-rotated: rotating them by machine would give the root standing write
over identities that outrank the executor. They join only by a separate decision.

- **A.** Rotate both by hand in the pass.
- **B.** Hand them to the executor: their keepers move into `Rotation / Keepers`, which reverses
  that part of decision 4.
- **C.** Leave them hand-rotated at their normal age.

**Recommendation: C**, with item 1 C. They predate the move, decision 4's reason still holds, and a
hand rotation proves nothing about the tooling. If you answer item 1 B, choose A.

## 3. Registries outside the executor's four

The executor reads only infraops-mcp-server, orchestrator, change-manager and factory-runner
(`REGISTRY_REPOSITORIES`). sds-hq (seven in-scope rows), security-standards (one) and localops
(one) are outside it.

- **A.** Add sds-hq and security-standards, each with a `credential_rotation` grant you give on
  its member record; localops stays hand-rotated, because your 2026-11-19 answer keeps the fork
  chain on the Mac.
- **B.** Move their rows into orchestrator's registry.
- **C.** Every row in those three registries stays hand-rotated.

**Recommendation: A.** It's a small code change (task T1) plus your grant, and each repository keeps
owning its own rows.

## 4. Two bearers whose identity could reach approvals

`work-carrier-observer-bearer` files the `standing_approval_check` record that admits a policy
approval (amendment 22). D-84's standing answer excludes reach that isn't gated by a record the root
can't produce, so it's yours. `change-manager-execute-bearer` carries the EXECUTE scope.

- **A.** Hand over both.
- **B.** Hand over the EXECUTE bearer only, after a check that its scope (change-manager #168) is
  within the full bearer's, which the root already rotates; the observer bearer stays
  hand-rotated.
- **C.** Neither.

**Recommendation: B.** If the check holds, the EXECUTE bearer adds no reach the root doesn't already
have. The observer bearer would let whoever holds the root forge the carrier's check.

## 5. The alert-hub sender's reach

No adapter exists for the four sender tokens (two orchestrator rows, HQ's, and the unregistered
bridge token). alert-hub's admin routes sit behind Cloudflare Access, and at the local clone's
`35daeb47` they can create a sender or revoke one permanently, not reissue a token.

- **A.** The executor gets a Cloudflare Access service token scoped to `/admin/senders` and reissues
  by machine. This adds a new identity and path.
- **B.** A person reissues in the dashboard and pastes into the inbox; the executor proves, stores
  and waits for readers (the shape of amendments 33, 35 and 36).
- **C.** Every sender token stays hand-rotated.

**Recommendation: B.** It adds no identity, and it matches the three adapters you approved this
week. The adapter's amendment also decides whether alert-hub needs a reissue route that keeps the
sender's id.

## 6. The factory-events passwords' type (FE-4)

`postgres-role` rotates one Coolify service's role through a restart. These two roles' readers are
broker lanes and Mac processes that fetch fresh each run.

- **A.** Extend `postgres-role` to a database with no Coolify consumer (a new helper target on
  `prod-vps`).
- **B.** A new type, in its own amendment.
- **C.** Both rotate by hand in the pass.

**Recommendation: B.** Amendment 24 already says a new type is its own amendment, and the reach to
the chain database is easier to judge there than as an extension of a restart-shaped type.

## 7. Mac Keychain tokens with a second keeper copy, and idle tokens

The bump-proposer, factory-events session and factory-events nightly tokens each have a Keychain
item and a copy in `Rotation / Keepers`. The Keychain instance can't write that copy.
`bws-bump-proposer-token` and `bws-cred-rotation-token` have no reader.

- **A.** You revoke the two idle tokens instead of rotating them, and delete the
  `Rotation / Keepers` copies of the session and nightly tokens, so the Keychain item is each
  one's keeper; the Keychain instance then rotates them.
- **B.** Keep the copies; a code task gives the Keychain instance a BWS write identity.
- **C.** All four stay hand-rotated.

**Recommendation: A.** A revoked token is safer than a rotated one nobody reads. One keeper per
token is decision 4's shape, and B adds a write identity on the Mac.

## 8. HQ's credentials with no type

These are the Slack bot token, the sds-report writer key, the three Brain contributor keys and the
Claude OAuth token.

- **A.** Each gets a class of its own and is rotated by hand in the pass.
- **B.** As A for the Slack and OAuth tokens. An adapter is planned for the Brain contributor keys
  now, and one for the sds-report writer key later: the estate runs both services.
- **C.** They rotate at their normal age.

**Recommendation: B.** Your answer says to make the changes the capability needs. The estate owns
both services, so it can make them rotatable. Slack and Claude are third parties with no rotation
API this session confirmed.

## 9. Decision 4's hand-rotated set

This covers the root token, the admin credentials, the executor's WORKER bearers, the Healthchecks
keys, the executor's Coolify token, the rotation App's key and the helper key.

- **A.** Rotate each by hand in the pass, last, limited by item 1's answer.
- **B.** Leave each at its normal age.
- **C.** Graduate some to machine rotation, each by its own decision.

**Recommendation: A.** "Also rotates all the creds" includes the executor's own. A hand rotation
from a runbook keeps decision 4's rule that the executor never rotates them.

## 10. Which registry takes each unregistered credential

- **A.** orchestrator takes the bridge sender token, `APP_BRAIN_READ_KEY`, the dispatch App key and
  `anthropic-key-sys-orchestrator`; infraops-mcp-server takes `prod-coolify-api-token`; sds-hq takes
  the Claude OAuth token; the two Healthchecks keys follow your 3e-pua-rows facts.
- **B.** As A, but each credential's row goes to the repository whose code mints it.
- **C.** No new rows; they stay off the registries.

**Recommendation: A.** Each goes where its readers' manifests already list it, which is how the
existing rows were placed.

## Reply

Reply line, items 1 to 10 in order:

`D-n: C, C, A, B, B, B, A, B, A, A`
