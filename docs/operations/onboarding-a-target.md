# Onboarding a factory target

This page explains how to make a repository a factory target: a repository the orchestrator admits
work units for, dispatches to factory-runner, and lands. It lists every step in order, says what
checks each one, and names the refusal you see if a step is missing.

A repository is a target only by its own declaration (ADR-0015). No setting in the orchestrator
lists targets.

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

## Make the repository dispatchable

1. **Declare the repository a target.** Commit `factory-target.toml` at the repository root on its
   default branch:

   ```toml
   factory_target = true
   factory_target_reason = "<why the factory works on this repository>"
   ```

   Both keys are required. A file without a non-empty `factory_target_reason` reads as unreadable,
   not as a declaration. Admission reads the file from GitHub with the dispatch App's token, never
   from a checkout (`services/execution/factory_target.py`).

   - A missing file or `factory_target = false` is refused as `target_repository_not_declared`.
   - Anything else that stops a read, including a malformed file or a repository the App can't
     see, is refused as `target_repository_declaration_unreadable`.

2. **Add the caller workflow.** Copy project-standards'
   `src/portfolio/templates/factory-runner-caller.yml` to `.github/workflows/factory-runner-pilot.yml`.
   Replace `{{FACTORY_RUNNER_WORKFLOW_SHA}}` with the full sha in factory-runner's
   `RECOMMENDED_CALLER_PIN` file, never `@main`. Keep `work_unit_id` as the only input: GitHub
   rejects a dispatch whose inputs don't match with `422 Unexpected inputs provided`.

   The orchestrator doesn't check the caller before dispatching, so a wrong caller first shows up
   as a failed dispatch. The conformance kit's `runner.caller` check requires an exact pin match.

3. **Set the four Actions secrets** that the reusable workflow requires:
   `FACTORY_RUNNER_TOKEN`, `FACTORY_RUNNER_CREDENTIAL_KEY_ID`, `ANTHROPIC_API_KEY` and
   `FACTORY_PR_TOKEN`. Fetch each value from BWS; never paste one into a prompt or a tool argument.
   Use `gh secret set <NAME> -R AlobarQuest/<repo>` with the value on stdin and no `--body` flag.

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

10. **Expect a human authority approval on every unit.** `factory-policy.toml` admission is global,
    and its `known_good` pattern (the one shape that skips a person's approval) names specific
    repositories in `target_repositories`. A new repository isn't in it, so each
    unit waits for an approval at `/review`. Adding the repository to `known_good` is a
    standing-authority change.

11. **Dry-run the envelope's commands against the repository.** Run them twice on a clean clone,
    prove the mutator produces a diff, and make sure the verifier exercises what the change could
    break. Envelopes are write-once, so a command list that fails costs the unit
    (driving-a-unit.md).

12. **Optional: make the repository reachable by the work carrier.** Work that arrives through the
    work carrier, not a direct dispatch, also needs the kit's `runner.caller`, `factory.secrets`,
    `factory.landing_known` and `factory.pat_access` checks to pass in `~/.portfolio/portfolio.json`.
    The nightly sweep measures a repository only when it's checked out under `~/Projects` and its
    `PROJECT.md` declares `delivery_profile`. Otherwise the carrier answers UNKNOWN and holds the
    work.

## Check the result

To check steps 1–5 and 7 in one pass, run the conformance kit from project-standards:

```bash
portfolio onboard <path-to-repo>
```

It reports `runner.caller`, `factory.secrets`, `factory.pat_access`, `factory.app_access` and
`factory.landing_known`. `factory.pat_scope` can only report `unknown`, because a fine-grained token
doesn't expose its permissions through the API; step 6's probe is the real test of it.
