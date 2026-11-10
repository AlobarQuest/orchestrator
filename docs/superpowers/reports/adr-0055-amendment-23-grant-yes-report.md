Outcome: done

# Report: adr-0055-amendment-23-grant-yes (W-74)

## What I built

I recorded Devon's answer to the grant (sds-hq D-36, "yes", from Slack, recorded 2026-11-10 17:14
UTC) in ADR-0055 amendment 23 on draft PR #649. The PR stays a draft.

- Content commit: `824c0ba6`, on `cacc8ef6`, pushed fast-forward to
  `hq/adr-0055-amendment-23-ping-url-from-api`.
- New head: this report's commit, which follows `824c0ba6` on the same branch.

The diff in `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md` has two
parts:

1. Lines 3231-3233, "Open before building", "The grant (Devon's)": I replaced W-73's "stays open,
   asked as D-36" line. It now says the grant was answered yes, with the source and time. It also
   says the grant is a person's act in BWS, done before increment 3 reads the key, and that the
   item's own check (confirm the key's project in BWS first) still applies. The question text is
   unchanged.
2. Lines 3252-3253, Consequences, "Threat model": "That widening is what Devon's grant accepts or
   refuses" now says Devon accepted the widening by answering yes (sds-hq D-36). This was the only
   other statement in amendment 23 that treated the grant as undecided.

I left the following statements unchanged, because each is still true once the grant is answered:

- Status line: it doesn't mention the grant.
- Decision 1: "grants nothing ('Open before building' asks Devon)" describes the amendment itself.
- Increments: "They ship after Devon's grant" and increment 3's "After Devon's grant" refer to the
  BWS act, which hasn't happened yet.

PR body: the summary's top line now says Devon answered the grant yes (D-36), and that the BWS
grant is a person's act done before increment 3. The follow-up sections are unchanged.

## Tests and checks

- `make check` against `orchestrator_test_hq_w_74`, with `PGTZ=UTC` and `SECURITY_STANDARDS_DIR`
  set to the fixture: exit 0. Result: 14207 passed, 1 skipped.
- Security scan from the repo root (`--category security`): 0 BLOCK, 2 WARN, 1 INFO. These are the
  same counts the PR reported before this change.

## Files changed

- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md` (amendment 23 only).
- `docs/superpowers/reports/adr-0055-amendment-23-grant-yes-report.md` (this report).

## Self-review

- The diff touches only amendment 23. The ADR body and the other amendments are unchanged.
- The diff names a BWS secret UUID, which is not a secret value, and contains no ping URL.
- I didn't merge the PR or force-push, and I didn't touch the main tree.

## Concerns

- The PR body's "Grant:" bullet still says the grant is "asked under 'Open before building' and
  isn't decided here". I didn't change it, because the handoff limited the body edit to the top
  line. The bullet is true of the amendment's text, but HQ may want to reword it.
- The ADR's Status history line (line 35) for amendment 23 says "accepted" and doesn't mention the
  grant, so I left it unchanged.

## Standards followed

- Code Brain rule 3 (structured JSON logs to stdout): doesn't apply, because this is a docs-only
  change.
- Code Brain rule 4 (never swallow an exception silently): doesn't apply, because this is a
  docs-only change.
- Code Brain rule 5 (never log secrets): the diff names no secret value. The only identifier it
  uses is the BWS secret UUID that was already in the text.
- Infra Brain 591, 592, 593: no BWS token is in any tracked file or in the commit, and there's no
  inline `BWS_ACCESS_TOKEN` value. The security scan reports 0 BLOCK.
- Infra Brain 1032 (exposure means rotate): I saw no secret value during the work.
- Infra Brain 212 (HTTPS for public FQDNs): doesn't apply, because this is a docs-only change.
- Infra Brain 2890 (never commit a ping URL): the diff contains no ping URL.
