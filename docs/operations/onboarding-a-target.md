# Onboarding a factory target

This page explains how to make a repository a factory target: a repository the orchestrator admits
work units for, dispatches to factory-runner, and lands. It lists every step in order, says what
checks each one, and names the refusal you see if a step is missing.

A repository is a target only by the member record's grant (ADR-0058, which supersedes ADR-0015's
file). No setting in the orchestrator lists targets.

## Before you start

Decide two things first, because they change the amount of work:

- **Whether the repository is inert or redeploys.** A repository is inert when a landing on its
  default branch redeploys nothing. A repository that redeploys also needs a change-manager deploy
  policy entry and lands only inside the `live_estate` change window (step 8). Read the
  repository's workflows, its webhooks and its host's git integration to decide; landing-lanes.md
  #100 shows how a repository redeployed while its deploy target said it couldn't.
- **Whether its default branch is `main`.** Dispatch fires the caller at
  `ORCHESTRATOR_DISPATCH_WORKFLOW_REF`, which defaults to `main` (dispatch.md). Another default
  branch needs that setting looked at first.

## Meet the portfolio standards

A target must first pass the conformance kit's admission checks, which stand for the portfolio's
code, security and project standards. Work from a checkout under `~/Projects` with a GitHub
`origin` remote, and make each of the following checks pass (project-standards'
`readiness_schema.py` lists them):

- **`project.manifest` and `profile.declared`:** a valid `PROJECT.md` whose frontmatter declares a
  registered `delivery_profile`. `portfolio init` scaffolds one; `portfolio lint <repo>` reports
  what to fix.
- **`code.onboarded`:** `.code-standards.toml` and `.github/workflows/quality.yml` exist. Run
  `code-standards init`, then `code-standards sync`, which also vendors `dependabot.yml`. The code
  itself must then follow `~/Developer/code-standards/STANDARDS.md`; the Quality workflow's linters
  enforce it.
- **`ci.executed`:** the latest Quality run on `main` passed and its log shows more than zero tests
  collected. A green run that collected nothing fails this check, so put the test runner where
  `uv sync` installs it (a dependency group, not an optional extra).
- **`security.clean`:** the security-standards scanner reports no BLOCK findings. Run it from the
  repository root:

  ```bash
  PYTHONPATH="$HOME/Projects/security-standards/src" \
    ~/Projects/security-standards/.venv/bin/python -m security_scan.cli . --category security
  ```

  Treat any token it finds in a tracked file or in git history as leaked: rotate it.

The kit also reports four advisory checks that don't fail admission: `standards.pinned` (the
`project`, `code` and `security` versions in `PROJECT.md` are current), `repo.protection`,
`deps.dependabot` and `backlog.hygiene`. Clear them anyway; step 9 needs branch protection.

The kit checks that the standards are wired in and passing, not their content. No check queries
Code Brain's paved roads or infra-brain's deployment patterns, so review a repository against those
by hand before you make it a target.

## Make the repository dispatchable

1. **Grant the service.** On `/review`, confirm a member proposal that grants the repository
   `app_changes` (or `dependency_updates` for dependency-update work only). Admission refuses as
   `target_repository_not_granted` until a person has confirmed it, and for a retired member or a
   grant in any other state.

   Also commit `factory-target.toml` at the repository root on its default branch. Admission no
   longer reads it, but project-standards and the membership consistency check still do, and the
   check reports a repository whose file disagrees with its grant:

   ```toml
   factory_target = true
   factory_target_reason = "<why the factory works on this repository>"
   ```

   Both keys are required. A file without a non-empty `factory_target_reason` reads as unreadable,
   not as a declaration.

2. **Add the caller workflow.** Copy project-standards'
   `src/portfolio/templates/factory-runner-caller.yml` to `.github/workflows/factory-runner-pilot.yml`.
   Replace `{{FACTORY_RUNNER_WORKFLOW_SHA}}` with the full sha in factory-runner's
   `RECOMMENDED_CALLER_PIN` file, never `@main`. Keep `work_unit_id` as the only input: GitHub
   rejects a dispatch whose inputs don't match with `422 Unexpected inputs provided`.

   The orchestrator doesn't check the caller before dispatching, so a wrong caller first shows up
   as a failed dispatch. The conformance kit's `runner.caller` check requires an exact pin match.

3. **Set the four Actions secrets** that the reusable workflow requires:
   `FACTORY_RUNNER_TOKEN`, `FACTORY_RUNNER_CREDENTIAL_KEY_ID`, `ANTHROPIC_API_KEY` and
   `FACTORY_PR_TOKEN`. Actions secrets are write-only, so you can't copy one from another target.
   Take each from its source:

   | Secret | Source |
   |---|---|
   | `FACTORY_RUNNER_TOKEN` | BWS `d2a4c0fc-128b-4bf5-8e25-b481010e1be0` (`Ops / Platform`) |
   | `FACTORY_RUNNER_CREDENTIAL_KEY_ID` | Not a secret value: the runner's key id, recorded in factory-runner's `docs/rollout.md` |
   | `ANTHROPIC_API_KEY` | BWS `b56cf0df-27bc-46a8-8e94-b48201478aff`, the spend-capped runner key (`Ops / Platform`) |
   | `FACTORY_PR_TOKEN` | BWS `a3240c2e-92d7-4b32-a726-b49b0135565a` (`SDS Operator`) |

   Fetch and set each value in one process so it never appears in a prompt, a tool argument or
   the transcript. Pipe it to `gh secret set <NAME> -R AlobarQuest/<repo>` with no `--body` flag;
   `--body -` stores a literal dash. Step 6's probe is what proves the token and key id agree.

   Then add the repository to both rotation inventories, so a rotation reaches the new copies:

   - this repository's `.cred-consumers.toml`, credential `orchestrator-factory-runner-bearer`,
     for `FACTORY_RUNNER_TOKEN`;
   - factory-runner's `.cred-consumers.toml`, for `FACTORY_PR_TOKEN`.

4. **Give `FACTORY_PR_TOKEN` access to the repository.** The token is a fine-grained personal
   access token with an explicit repository list. Setting the secret copies a token that can't
   reach the new repository, and the run fails at `actions/checkout` with a 403. Add the repository
   on the token's GitHub settings page; no API does it. The token also needs Workflows
   read-and-write, or a push that touches `.github/workflows/**` is rejected (credentials.md #152
   and #153).

5. **Confirm the dispatch App reaches the repository.** The installation covers all repositories,
   so this step is normally already done. If the installation is ever narrowed, add the repository
   to it. Read the App's permissions from a token mint response, never from `/app`
   (credentials.md #274).

6. **Probe the caller.** Dispatch it with a well-formed work-unit ID that doesn't exist:

   ```bash
   gh workflow run factory-runner-pilot.yml -R AlobarQuest/<repo> \
     -f work_unit_id=00000000-0000-4000-8000-000000000000
   ```

   A healthy setup fails at "Prepare scoped run" with `404 work_unit_not_found`. A 401 means the
   runner credential is wrong. A 403 at checkout means step 4 is missing. The probe changes
   nothing, because the runner claims a unit before it does any work. It's the only check that
   tells *configured* from *working*.

## Make the result landable

7. **Record the landing answer in App Brain.** Call `record_default_branch_landing` with `inert` or
   `redeploys` for the repository. Without an answer, admission refuses a unit whose reach leaves
   out `live_estate` (`reach_estate_unknown`), and the landing lane refuses to merge its pull
   request (`merge_target_estate_unknown`). See landing-lanes.md for how the answer is read.

8. **If the repository redeploys, add it to change-manager's deploy policy.** That is a new policy
   version in change-manager's `app/deploy_policy.py`, with ratified acceptance criteria, a
   rollback plan and the pinned rollout workflow, plus the matching transcription in this
   repository's `src/deploy_watcher/workflows.py`. Until then, policy can't approve the
   repository's change records (objection `repository_not_in_policy`), so a person must approve the
   record for every pull request before it lands. Skip this step for an inert repository.

9. **Set branch protection and merge settings.** Nothing in the code checks these, so read each one
   back after setting it (landing-lanes.md #139 and #142):

   - At least one required status check, named by its **job** name and verified against a real
     open pull request.
   - No `required_approving_review_count`. A solo account can't approve its own pull requests.
   - `strict: false`.
   - Squash merging allowed. The landing lane merges by squash, and a repository that disallows it
     answers 405.
   - `squash_merge_commit_message` set to `COMMIT_MESSAGES`, so the landing commit keeps
     factory-runner's `SDS-Unit:` trailer. Without it the landing ledger can't attribute the
     landing.
   - `allow_auto_merge: true`, enabled only after reading the required checks back. Auto-merge
     with no required checks merges instantly.

## Prepare the first unit

10. **A dependency-update envelope needs no authority approval in any repository.**
    `factory-policy.toml` carries one `known_good` pattern each for uv, npm and pip, and none names
    a repository: which repositories the change class may reach is decided by the member record's
    grant (`dependency_updates`) at admission, not by a list in the policy. Anything else (another
    change class, a command that is not one of the pattern's exact command shapes, a different capability or budget)
    still waits for an approval at `/review`. Widening a pattern remains a standing-authority
    change.

11. **Dry-run the envelope's commands against the repository.** Run them twice on a clean clone,
    prove the mutator produces a diff, and make sure the verifier exercises what the change could
    break. Envelopes are write-once, so a command list that fails costs the unit
    (driving-a-unit.md).

12. **Declare a `delivery_profile` and check the repository out under `~/Projects`.** The
    conformance kit measures its factory checks only for a repository whose `PROJECT.md` declares
    `delivery_profile`, and the nightly sweep reads only checkouts under `~/Projects`. Without
    both, the check in the following section reports every factory check as out of scope, and
    work that arrives through the work carrier, not a direct dispatch, is held as UNKNOWN. The
    carrier needs `runner.caller`, `factory.secrets`, `factory.landing_known` and
    `factory.pat_access` to pass in `~/.portfolio/portfolio.json`.

## Check the result

To check every step on this page at once, run the SDS readiness command from project-standards
against the checkout:

```bash
set -a; . ~/.portfolio/credentials.env; set +a
~/Projects/project-standards/.venv/bin/portfolio sds-ready <path-to-repo>
```

It runs every `portfolio onboard` check plus the SDS checks: Devon's decisions file, the
declaration, the rotation inventories, branch settings, the before-and-after evidence and an inert
landing. It sorts each failing check by owner and writes, under
`~/.portfolio/onboarding/<owner>__<repository>/`:

- `HANDOFF.md`: the repository work, for a build agent;
- `TASKS.md`: the steps only Devon can do, including the decisions file;
- `readiness.json`: every check result.

It exits 0 when the repository is ready to onboard, 1 when work remains, 2 when the kit couldn't
run and 3 when the repository is refused (Devon declined it, or landing on it redeploys). Checks it
couldn't measure, usually for a missing credential, are listed separately and never read as ready.
`factory.pat_scope` is left out, because a fine-grained token doesn't expose its permissions through
the API; step 6's probe is the real test of it.

`portfolio onboard <path-to-repo>` remains the conformance kit's general readiness check. Its
output is the `portfolio-readiness/v1` document that intent-packages consumes.
