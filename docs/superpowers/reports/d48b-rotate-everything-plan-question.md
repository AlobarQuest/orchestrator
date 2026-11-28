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

## 4. The work carrier's observer bearer

`work-carrier-observer-bearer` files the `standing_approval_check` record that admits a policy
approval (amendment 22). The root already reads its keeper through the broker's token (your D-83
(10) A, for development). A hand-over adds the keeper's move into `Rotation / Keepers` and the
root's standing write on it. D-84's standing answer leaves reach that isn't gated by an approval
record the root can't produce to you.

- **A.** Hand it over, recorded in the threat model (T1).
- **B.** Rotate it by hand in the pass.
- **C.** Leave it hand-rotated at its normal age.

**Recommendation: B.** Amendment 22 gives the carrier's check an identity of its own, never
`orchestrator-system`. A hand
rotation still rotates a credential the move created, and the root's read reach already reopens
in the security phase with D-64.

The EXECUTE bearer isn't asked here: its keeper is already in `Rotation / Keepers`, so a hand-over
adds only the flag. HQ checks under D-84 condition 2 that its scope (change-manager #168) writes
nothing the executor trusts, then hands it over.

## 5. The alert-hub sender's reach

No adapter exists for the four sender tokens (two orchestrator rows, HQ's, and the unregistered
bridge token). At the local clone's `35daeb47`, alert-hub's `/admin/*` routes sit behind
Cloudflare Access (`accessMiddleware`). They can create a sender or revoke one permanently; a
sender `PATCH` can't change its token.

- **A.** The executor gets a Cloudflare Access service token of its own, scoped to
  `/admin/senders`, and reissues by machine. This adds a new identity and path; the alert-hub MCP's
  existing service token is another identity's.
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

## 7. Mac Keychain tokens with a BWS keeper copy

The bump-proposer, factory-events session and factory-events nightly tokens each have a Keychain
item and a copy in `Rotation / Keepers`, which your D-54 A keeps as the keeper. The Keychain
instance can't write that copy. The root already writes the project (decision 4), so it already
reaches these copies.

- **A.** Code task T6: the Keychain instance writes the Keychain item, and a host-instance unit
  writes the copy. No grant is added.
- **B.** The Keychain instance gets a BWS identity with write on the three copies, which is a new
  identity.
- **C.** All three stay hand-rotated, with a person updating both copies, as their rows say.

**Recommendation: A.** It keeps D-54 A's copy and adds no reach. `bws-cred-rotation-token`, which
reads nothing, isn't asked: it stays as its row says until its age finding of 2027-07-02.

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

## 9. The executor's own credentials

Decision 4's hand-rotated set covers the root token (minted for the host at D-75 B), the admin
credentials, the executor's WORKER bearers, the Healthchecks key, the executor's Coolify token and
the rotation App's key. The Keychain instance's inbox token, the helper's key and the Healthchecks
Management key are added here; whether that set names them is unverified.

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
