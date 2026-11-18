# Question: the bump driver's conformance claim reads Mac-only state that decision 1's clones don't supply

Worker W-211, handoff `lanes-inc-5-bump-driver`. I've built nothing yet. The branch is clean at
`d6a4d909`.

## The fact that's wrong at its source

ADR-0059 decision 1 (lines 306-324) says the bump driver's container needs three clones:
intent-packages, project-standards, and security-standards. With those plus a `factory` on `PATH`,
the conformance claim would reproduce the Mac's. It doesn't. I traced this by reading the source.
I ran no live call and no probe.

The chain works as follows:

1. `factory decompose` runs `orchestrator conformance-claim <mirror>`
   (`intent_packages/factory/orchestrator_cli.py:71`).
2. That command imports project-standards' `portfolio.compliance` and builds the matrix row for
   the mirror (`src/orchestrator/conformance_claim.py:95-120`).
3. The claim is `green` only when every cell is `pass`, `accepted-exception`, or `not-applicable`.
   An `unknown` cell makes it `violations` (`conformance_claim.py:37-38`, `:79-91`).

Several of the matrix's checkers read paths under `HOME` that the Mac has and the container
doesn't. `_generator_environment` (`bump_driver/cli.py` ~785) passes `factory` only `PATH`,
`HOME`, `PYTHONPATH`, the Brain keys, and git settings. So the override variables
(`CODE_STANDARDS_REPO`, `SECURITY_STANDARDS_REPO`, `INFRADRIFT_REPORT_DIR`) never reach it. Only
the `HOME`-relative defaults resolve.

| Cell | What it reads (`project-standards/src/portfolio/`) | In the container |
|---|---|---|
| `security`, governance | `~/Projects/security-standards` (`config.security_standards_repo`) | I can fix this inside the fence: clone into `$HOME/Projects/security-standards` (`lane-run`'s `HOME` is discarded per pass). |
| `code` | `~/Developer/code-standards/.venv/bin/code-standards`, else `uv run --project ~/Developer/code-standards` (`checkers.py:80-84`) | Missing. code-standards isn't among decision 1's clones, and running it needs a venv (`uv sync` from PyPI) or `uv run`, which fetches from PyPI. The result is `unknown`, so the claim is `violations`. |
| `infra` | The latest report in `~/infra-drift/reports`, at most 36 hours old (`checkers.py:182-205`) | Missing. The Mac's `com.devon.infra-drift` writes these reports, and it moves only at increment 9 (D-9). The result is `unknown`. |
| `checks` | For a `launchagent:` executor, `~/Library/LaunchAgents/<label>.plist` (`wiring.py:50, 132-135`) | Missing. For example, change-manager declares `change-window` as `launchagent:com.devon.change-window`. The result is a violation or unknown. |

change-manager's `PROJECT.md`, a likely `dependency_updates` member, declares `code: '1.1'`,
`infra`, and that launchagent check. So in the container its claim would be `violations` on every
bump.

## What that does, and why the paired pass can't catch it

- Decomposition validates only the claim's shape (`services/intake/decomposition.py:1047-1068`),
  and policy approval doesn't read it. Dispatch refuses `conformance_not_green`
  (`services/execution/dispatch.py:565-587`). Envelopes are write-once, so each bump ends as an
  approved, undispatchable unit with a `bump-act-refused` alert. Only a person superseding it
  clears that, and only while the unit is unclaimed.
- `--dry-run` never runs the generator (`cli.py:304`, `would-propose`). So cut-over step 2's
  paired reporting runs would match, and the gap would surface only on the first acting pass.

## Is code-standards a "standards repository" for the read App?

D-8 (ADR line 615) installs the read App on "the standards repositories". The same passage defines
the read set as "the standards repositories the lanes clone", and decision 1 lists the bump
driver's as project-standards and security-standards. No decided text names code-standards
(`grep code-standards` finds nothing in the ADR). Naming it in the mint would add a repository to a
decided installation, which the handoff forbids without a decision.

## Options

- **A (recommended): build to decision 1 now, and block the cut-over on an ADR amendment.**
  - I ship everything the handoff lists. The clones go under the pass's own `$HOME/Projects/...`,
    so the `security` and governance cells resolve as on the Mac. `factory` runs from a shim
    under the image's interpreter, as #733 runs intent-packages.
  - deploy.md's cut-over list for this lane gains a step marked as a blocker: an amendment that
    decides the claim's three Mac-only inputs in the container.
  - The inputs are code-standards (a fourth clone, its read App installation, and its venv or
    PyPI access), the drift reports (which arrive with increment 9, or another source), and the
    launchagent-executor wiring check (Mac-only by definition).
  - No acting container pass can happen before cut-over, so nothing reaches production in the
    meantime.
- **B: scope this PR down.** I build the lane files and launcher, but the container refuses an
  acting pass (`sds_outcome_fail`, naming the gap). Only `--dry-run` runs until the amendment lands.
  This is safer against an early schedule flip, but it adds a refusal the ADR doesn't describe.
- **C: stop here.** HQ amends ADR-0059 decision 1 first, and this handoff waits.

My recommendation is A. If HQ wants a guard against flipping the schedule early, B's refusal is a
small, removable addition. Reply "A", "B", or "C", plus anything about the minute: I'd take 51 by
the minute-after precedent of #736 and #738, because 50 is the rotation decomposer's and 51 is
free.
