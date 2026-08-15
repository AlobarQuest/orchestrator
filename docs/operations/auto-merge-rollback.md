# Rolling back the auto-merge lane

Written from a rehearsal, not from reasoning. Every timing and command below was executed
against `project-standards` on 2026-08-08; nothing here is theoretical.

## The two things being rolled back are different

**The RULE** is the gate workflow. Reverting it stops further landings under it — and takes
about two seconds.

**The LANDINGS** are what the rule already let through. Reverting the rule does **not** un-merge
them. They are separate commits in separate repositories and each is its own decision.

Conflating these is the mistake this runbook exists to prevent: reverting the gate and believing
the estate is clean leaves every landing it admitted still in place.

## Step 1 — revert the rule

```
cd ~/Projects/<repo>
git revert --no-edit $(git log --format=%H -1 --grep="<the gate change>")
git push origin main
```

**Measured: 2 seconds**, decision to restored. Verify by blob sha rather than by reading the
file:

```
gh api "repos/AlobarQuest/<repo>/contents/.github/workflows/dependabot-auto-merge.yml?ref=main" --jq .sha
```

**Check the revert target is transcribed first.** The landing ledger keys rules on their blob
sha, and a revision the registry does not know is reported as a finding. `git rev-parse
<commit>^:.github/workflows/dependabot-auto-merge.yml` gives the sha a revert would restore;
if it is in `src/landing_ledger/rules.py`, the rollback is silent to the audit. In the rehearsal
it was `12880ce7`, already transcribed, so the revert manufactured no finding.

**The lane is per-repository.** Five repos carry the gate, and a bad rule is usually in all five.
Reverting one leaves four live.

## Step 2 — find what the rule let through

This is the step the ledger exists for, and it is not answerable any other way.

```python
# every auto-merged landing, grouped by the rule revision that permitted it
armed = [r for r in observations
         if r["observation_type"] == "landing"
         and r["facts"]["permitted_by"]["basis"] == "auto_merge_rule"]
group_by(armed, key=lambda r: r["facts"]["permitted_by"]["rule_revision"])
```

Run against production during the rehearsal, this returned the complete blast radius in one
query — 6 auto-merged landings across 4 rule revisions, naming the repository, dependency and
commit for each. Without it the question "what did this rule admit?" requires reading every
merge in every repository by hand.

Note the denominator: **6 auto-merged of 399 total landings.** Most of what reaches `main` is
not auto-merged, and the query must filter on `basis` or it returns the estate's whole history.

## Step 3 — revert the landings, individually

Each is a normal revert in its own repository. There is no batch operation and there should not
be: a rule being wrong does not make every landing it admitted wrong, and the ledger gives you
the list precisely so each can be judged.

For a **deploying** repository (`change-manager`, `brain`) prefer the image, not the commit:
both push a per-SHA tag alongside the moving one, so re-pointing Coolify at the previous
`:<sha>` and redeploying is faster than a rebuild. **Always follow a re-point with a revert** —
otherwise `main` and production disagree silently and the next unrelated merge re-deploys the
broken code. For `brain` specifically the rollback target must be the **image**: it builds from
`requirements.txt` with no lockfile, so rebuilding the same commit can resolve a different
dependency set.

## Step 4 — restore the rule, when fixed

```
git revert --no-edit HEAD      # revert the revert
git push origin main
```

**Measured: 2 seconds.** Verify the restored file is byte-identical to what shipped —
`/usr/bin/diff`, **never plain `diff`**, which is hook-rewritten to `rtk diff` and reports
"Files are identical" for files that differ (see the `rtk-diff-reports-false-identical`
invariant). In the rehearsal the restored blob was `e849b3a8`, matching the original exactly.

## What the rehearsal recorded about itself

Both rollback commits appear in the ledger as `basis: none` and `basis: human` respectively —
a direct push has no permission basis, and that is the honest record rather than a gap. So the
rollback is itself visible in the same place the incident is, which is the property that makes
the ledger useful during an incident rather than only after one.

## What this does not cover

A dependency that installs cleanly, passes health checks, and misbehaves later under real
traffic. Nothing in the estate detects that — Code Brain records `monitoring-alerting` as
**unpaved** — so there is no alert to trigger this runbook. A rollback procedure you do not know
you need is not a control, and closing that gap matters more than any refinement here.
