Outcome: done

# Report: adr-0055-amendment-23-ping-url-from-api (W-71)

## What I built

I wrote ADR-0055 amendment 23, "The executor reads a lane check's ping URL from the Healthchecks
API", with the status **Proposed**. It quotes Devon's standing answer of 2026-11-10 and follows
the shape of amendments 20 to 22: Status, Context, Decision, Increments, Open before building, and
Consequences. I also added a line for it to the ADR's `Amended:` list. The change is docs only.

- **Amendment number: 23.** My base was `2983cb01`, where `origin/main` ended at amendment 21.
  PR #647, which carries amendment 22, merged at 16:15 UTC while I worked (`6d32e881`). I rebased
  onto that commit before I wrote anything, so the branch doesn't conflict with it. No branch has
  a 23.
- **PR:** #649, a draft, at head `4512f313` for the ADR commit. This report adds one more commit
  on top of it. I didn't merge it or mark it ready.

## Citations for "Read first"

- **ADR-0055 amendments:**
  - Amendment 19: items 1, 3, 5, 8 (cut-over step 5), 9 and 11.
  - Amendment 21: decisions 1 to 4 and Consequences.
  - Amendment 22: decision 6, item 2's type condition, and the open item "Ping URLs as
    configuration".
- **ADR-0059:** "A lane holds only its own check's ping URL" (lines 677-712), and "Cut-over for
  one lane", step 1 (lines 1083-1098).
- **Code at `6d32e881`:**
  - `rotation_worker/types.py`, lines 56-58.
  - `plans.py`, lines 135 and 144.
  - `contract.py`: lines 313-336 (`validate_issue_plan`, including "a person mints and stages an
    issue"), and lines 609-626 (`_mint`).
  - `adapters/healthchecks_ping_url.py`: lines 94-116 (`bind_refusal`) and lines 123-124 (`mint`
    raises).
  - `cli.py`, line 304 (the VM's `stage` refuses with "only the Keychain executor stages by
    prompt").
  - `worker.py`: `_mint_waits` (lines 630-665), `_mid_run_waits` (lines 544-562), and `_take`
    (lines 428-470).
  - `inbox.py`: lines 97-116 (`other_mints`) and lines 129-166 (`bind`).
  - `readers.py`: lines 215-219 (the listing is cached for each pass) and lines 250-255
    (`_permits`).
  - `config.py`, lines 83 and 124.
  - `generate.py`: line 156 (`acceptance`) and line 208 (`credential_create` for any machine
    mint).
- **Which key kinds return `ping_url`:** I confirmed this at the source, in Healthchecks `v4.4`.
  - `hc/api/models.py`, `Check.to_dict`, lines 439-484: a read-only key gets `unique_key` (lines
    471-472), and any other key gets `uuid` and `ping_url` (lines 474-475).
  - `hc/api/views.py`: `get_checks` (lines 414-435) and the single-check reads (lines 486 and 495)
    pass the key's `readonly` flag.
  - The hosted docs say the same, under "List Existing Checks".
  - The HQ fact holds: only a read-write key reads `ping_url`.
- **Option (b):** `hc/front/views.py`, `transfer`, lines 1063-1065. Moving a check keeps its UUID
  and history, but it reassigns the check's notification channels to the target project's.
- **The worked example:** sds-hq `state/progress.md`, the ~12:55 UTC entry for 2026-11-10 (G-20,
  unit `2695999c-…`, change record 198).

## Choices and recommendation

- **(a) The VM executor's mint reads the listing with the Management key. Recommended.** The
  executor already holds every lane's ping-URL keeper. It needs one new read and no new resource.
- **(b) A Healthchecks project that holds only the lane checks, with its own key.** This is
  narrower, but each lane needs a check move, a second read-only key and a new BWS secret. I named
  it as the narrowing to take at the revisit.
- **(c) The lane setup in the deploy command. Not recommended.** Only the root writes
  `Rotation / Keepers`. So the session either keeps the person's mint act in the plan, by pasting
  into the inbox itself, or it gets a write grant and skips the executor's issue invariants. HQ
  also holds no grant on the key.

What replaces the paste:

1. HQ makes the row due, as it does now.
2. Before the claim, the executor reads the Management key's listing and finds the one check named
   by a new `healthchecks_check` field on the row.
   - No answer: the unit waits unclaimed.
   - No key, no field, zero or two matches, the wrong shape, or a URL that another keeper holds:
     the unit is refused before the claim.
3. The claimed `mint` and `stage` steps take the URL from that pass's cached listing.
4. `verify_new` keeps amendment 21's lookup unchanged. I didn't subsume it, so R14 needs no new
   exception. The amendment says why the lookup's second-half gap has no source when nobody types
   the URL.

## What it narrows

All of these are narrowed, not withdrawn:

- Amendment 19 item 5: there's no paste or bind.
- Amendment 19 item 8: the order between the token and the ping URL is no longer needed, and step
  5 is rewritten.
- Amendment 21 decision 1: the bind is retired.
- Amendment 22 decision 6: the ping-URL paste is gone. Its type condition is unchanged, and its
  open item is settled.
- The `rotation-mint` alert is no longer raised for a ping URL.

Afterward, the only person act left for a lane's credentials is the token's mint and paste.

## Increments (not built)

1. **orchestrator:** the type mints by machine, and a capability field keeps the authority at
   `secret_read` and `secret_write`, so the capability vocabulary isn't widened. The same increment
   covers:
   - the machine-minted issue plan, in `derive_issue` and `validate_issue_plan`;
   - the adapter's `mint` from the listing, with `bind_refusal` removed;
   - a client on the Management key that allows only the listing;
   - a `healthchecks_management_key_id` setting;
   - the read before the claim in `_take`;
   - a required `healthchecks_check` field on the row;
   - the `acceptance` criteria;
   - the docs (`rotation-executor.md`, `src/rotation_worker/CLAUDE.md`, and ADR-0059's lines on
     the ping URL).
2. **intent-packages:** the pinned issue fixture and the `create-rotation` scaffold. This ships
   together with increment 1.
3. **VM configuration:** the key id in `config.toml`, and the key's consumer rows.

Increment 1 lands only while no ping-URL package is open.

## Open items (Devon's, or settled in increment 1)

- **The grant.** It's Devon's decision. Should the root (`rotation-executor`) get a direct read on
  `51509d5e-…` in `Ops / Platform`?
- **Whether the generated package names the check.** If it does, the standing approval's re-render
  covers the `healthchecks_check` field.
- **Where each Healthchecks key's registry row lives.** This repo's `.cred-consumers.toml` has
  neither.

## Tests and checks run

- **`make check`:** passed against `orchestrator_test_hq_w_71`. I migrated it to head first and
  exported `TEST_DATABASE_URL`, `ORCHESTRATOR_DATABASE_URL`, `SECURITY_STANDARDS_DIR` (the
  fixtures) and `PGTZ=UTC`. Ruff and pyright are clean, and pytest reported **14207 passed,
  1 skipped**, with 11 warnings, in 386 s.
- **Security scan, from the repo root** (`security_scan.cli . --category security`): **0 BLOCK**,
  2 WARN and 1 INFO. These are the repo's standing findings; this diff changes only Markdown.
- **Ping URL check:** I grepped the diff for the ping base followed by any value and found none.
- **Code review:** I didn't run `/code-review`, because the diff is a docs-only ADR amendment.
  HQ's own review is the gate.

## Files changed

- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`: the `Amended:` line
  and amendment 23.
- `docs/superpowers/reports/adr-0055-amendment-23-ping-url-from-api-report.md`: this report.

## Self-review

- Every "narrowed" item names the amendment and item it narrows, and keeps the rules that still
  stand.
- No grant is stated as decided.
- No secret value was read: I cited the keys by id prefix only.
- The amendment states the threat widening plainly. The standing answer covers the URL, not the
  Management key, and that key can silence every self-hosted check, not only the lanes' checks.

## Concerns

1. **The handoff names the wrong BWS project for the key.** The handoff, and HQ's ~13:05 note,
   place `51509d5e-…` in `Rotation / Healthchecks`. `.bws-secrets.toml` lines 174-177 place it in
   `Ops / Platform`, and `Rotation / Healthchecks` holds the read-only key `f07b1368-…`. I cited
   the manifest. The grant question names `Ops / Platform` and says that Devon's answer settles
   which project is right. I couldn't confirm this in BWS without reading BWS, which was out of
   scope.
2. **ADR-0059 is now partly stale.** Its text still describes the hand paste. Under the
   amendment, increment 1 rewrites it, not this PR, so its text is stale only once that increment
   ships.
3. **Standards the handoff doesn't cover:** none.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** decision item 4 says the later
  increments log through `rotation_worker/logs.py` as structured JSON. It names the events
  `ping_url_read` and `ping_url_unreadable` and the fields each one carries.
- **Code Brain rule 4 (never swallow an exception silently):** a failed API read waits unclaimed
  (`ReadUnavailable`) or is refused before the claim (`ReadError`). Each case is logged, and
  nothing is written or passed. The Consequences section restates this.
- **Code Brain rule 5 (never log secrets):** the amendment names key ids only. The executor logs
  the URL's SHA-256 prefix and length and the check's name, never the URL or the key. The
  listing's other fields are dropped when it's parsed.
- **Infra Brain 591, 592 and 593:** the diff adds no BWS token, no inline `BWS_ACCESS_TOKEN` value
  and no token-shaped example, and the scan found 0 BLOCK.
- **Infra Brain 1032 (exposure means rotate):** I saw no secret value, and I read neither key.
- **Infra Brain 212 (HTTPS for public FQDNs):** the amendment reads the API over HTTPS at
  `https://healthcheck.alobar.net`.
- **Infra Brain 2890 (never commit a ping URL):** no ping URL is committed. The text refers to the
  ping base and UUID only in words.
