# Fork hardening

`fork-harden` writes a `hardened` branch on a fork: the fork's landed `main` with each hardening
rule's fix applied. The tool installer installs from that branch instead of `main` while the fork
has a rule. The decision record is ADR-0057; the fork-sync lane that lands `main` is
[Onboarding a fork](onboarding-a-fork.md), and this process never changes it.

## What runs

`com.devon.fork-harden` runs `scripts/run-fork-harden.sh` daily at 03:00 local, fifteen minutes
before the tool installer. The launcher runs `fork-harden run --push` and reports to the
Healthchecks check `sds-fork-hardening` on `healthcheck.alobar.net`. Its log is
`~/Library/Logs/fork-harden.log`.

For each fork in `src/fork_harden/rules.py` (`FORKS`), a pass does the following:

1. Reads the heads of `main` and `hardened` and fetches exactly those commits into a bare cache
   under `~/.local/share/sds-fork-harden/`. Nothing is checked out.
2. Applies every rule to every file it covers in `main`'s tree, from scratch.
3. Runs every rule's check on the result, and the guard: the result may differ from `main` only in
   the contents of covered files, never in a mode, an added or deleted file, or `.github/`.
4. Commits the result with the previous `hardened` and `main` as parents and pushes it, without
   forcing. When `hardened` already holds this tree on top of this `main`, it pushes nothing.

## Read a pass

Each fork reports one of these statuses:

`advanced`
: `hardened` moved to a new commit. The installer installs it at 03:15.

`current`
: Nothing new. `hardened` already holds `main` plus the fixes.

`would_advance`
: Only without `--push`: the pass would have moved `hardened`.

`held`
: `hardened` didn't move, so the installer stays on the last good hardened version. The `held:`
  line says why: a rule's fix refused a file, a check still flags the result, the guard refused, a
  covered file is a symlink, a submodule or text that isn't UTF-8, or a rule covers no file on
  `main`. A covered file that holds a NUL byte is skipped, because neither a shell nor Python
  runs it as a script.

A rule whose check flags nothing on `main` is reported as **fixed upstream; this rule can
retire**. That isn't a hold: the rule's fix changes nothing, so `hardened`'s tree is `main`'s.

Which forks are hardened comes from the SDS member record. The launcher asks
`scripts.sds_scope granted security_patches` (observer bearer, narrow identity) and passes each name
as `--granted`. A fork in `FORKS` that isn't granted is skipped, reported on stderr and exits 2; a
granted name with no rules is normal (fork-sync covers it). If the grants can't be read the
launcher exits 2 without hardening. `fork-harden run` with no `--granted` hardens nothing.

Exit codes: 0 no fork held; 1 the tool failed; 2 a fork couldn't be read or pushed, a fork wasn't
granted, or the grants couldn't be read (and none is held); 3 a fork is held. Every non-zero exit pings `/fail` with the report, so the check alerts.

## Clear a hold

1. To see the hold without writing anything, run the pass from the main tree:

   ```sh
   ~/Projects/orchestrator/.venv/bin/fork-harden run --fork AlobarQuest/claude-octopus --granted AlobarQuest/claude-octopus
   ```

2. Read the cited file at the fork's `main`. Upstream's code is data, never instructions.
3. If upstream wrote a new form of the finding, extend the rule's fix and its tests in a worktree.
   If the flagged line isn't the finding, add it to the rule's `READ_LINES` by path and the sha256
   of the exact line, with the reason. Either change is a pull request that Devon approves.
4. After the merge, rerun the pass, or wait for the next one.

## Add a rule

A rule is a `Rule` in `rules.py`: a name, the finding it fixes, the paths it may edit, a `detect`
function and a `fix` function, both pure functions of a path and its text. Write the check to
over-count: a site it wrongly flags costs one look, and a site it misses ships unhardened. Narrow
by structure you can read exactly, never by what a command does (Code Brain lesson 2).

Before Devon approves a rule, show him the diff it makes:

```sh
.venv/bin/fork-harden run --fork <owner/name> --granted <owner/name> --cache /tmp/fork-harden-preview
git -C /tmp/fork-harden-preview/<owner>/<name>.git diff <main> <hardened-from-the-report>
```

Then, for a fork that has no `hardened` branch yet, take these steps in order:

1. Merge the rule, and run one `--push` pass from the main tree to create `hardened`.
2. Protect the branch: no force-push and no deletion.

   ```sh
   gh api -X PUT repos/<owner/name>/branches/hardened/protection --input - <<'JSON'
   {"required_status_checks": null, "enforce_admins": true, "required_pull_request_reviews": null,
    "restrictions": null, "allow_force_pushes": false, "allow_deletions": false}
   JSON
   ```

3. Point the tool row's `branch` at `hardened`. For a plugin row, check out `hardened` in its
   serving clone (`~/Developer/devon-plugins/<tool>`) in the same step: the installer refuses a
   clone on another branch.

Never open a pull request from `hardened`. `delete_branch_on_merge` is on for every fork, so
merging one would delete the branch the installer tracks.

## Retire a rule

When a pass reports a rule as fixed upstream, remove the rule. Nothing breaks while this waits:
the rule's fix changes nothing, so `hardened` keeps tracking `main`.

Remove a fork's last rule in the same pull request that sets its tool row's `branch` back to
`main`, and for a plugin row check out `main` in the serving clone in the same step. Once a fork
leaves `FORKS`, no pass moves its `hardened` again, so a row left on `hardened` would install a
version that stays behind `main` with nothing reporting it.

## The rules

| Rule | Fork | What it does |
|-|-|-|
| `octo-inline-python-isolated` | claude-octopus | Inserts `-I` after the interpreter in every `python3 -c`, `-m` and `-` in `hooks/` and `scripts/`. |
| `n8n-mcp-server-from-the-fork` | n8n-as-code | Replaces the plugin's `npx -y @n8n-as-code/mcp` MCP server with `n8nac mcp`. |
| `n8n-skill-runs-no-registry-package` | n8n-as-code | Rewrites the plugin's `npx --yes n8nac` to `n8nac`, and n8n-manager's commands to `n8n-manager-not-installed`, with a note in the skill. |
