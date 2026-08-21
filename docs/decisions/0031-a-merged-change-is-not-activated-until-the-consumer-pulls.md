# ADR-0031 — A merged change is not activated until the consumer pulls

- **Status:** Accepted
- **Date:** 2026-08-21
- **Decided by:** Devon (the shape, 2026-08-21: *"For the dependabot routine bumps … it seems we
  would give the cascade an activation trigger, to complete the bump. It's not complete without a
  git pull."*); the build session (§2–§6, delegated)
- **Relates to:** ADR-0016 (the cascade), ADR-0018 (what it arms on), ADR-0023 (what it excludes),
  ADR-0030 (the same activation, recorded rather than performed)
- **Spec:** `~/docs/software-delivery-system/2026-08-21-completing-the-cascade-activation-spec.md`

## Context

`dependabot-auto-merge.yml` is estate machinery, vendored per repository. It arms GitHub's
auto-merge and stops at the merge.

For a repository whose landing redeploys a hosted application, stopping there is complete. For one
whose code runs from a working copy on this machine, the merge changes nothing until someone pulls
— so the bump is merged and not deployed, and nothing says so.

Measured 2026-08-21: `security-standards#41` and `project-standards#26` were both merged by
`github-actions[bot]`, days apart, and both checkouts were a commit behind while the
`bws-scan-gate.sh` Stop hook and two scheduled scans were executing from them. Both commits touched
`pyproject.toml` and `uv.lock` only, so nothing executing had changed. They demonstrate the
mechanism, not damage.

**GitHub has no inbound path to this machine.** The cascade runs there; the pull has to happen
here. So "give the cascade an activation trigger" cannot mean the workflow performs it.

ADR-0030 decided that a machine-local activation *is* a deployment and is recorded. This decides
how it *happens*. They are independently useful and share vocabulary.

## Decision

### 1. The consumer activates what it executes, immediately before executing it

Each scheduled launcher fast-forwards the working copy whose code it is about to run, as its first
step. Activation and execution become one event, which is what activation means for a periodic
consumer.

The rejected alternative is one local job that pulls the enrolled set. It reintroduces the gap:
pulled at 07:00, `vps-backup` still runs five-hours-stale at 02:00, and the job would report the
repository current.

**The rule is what a launcher EXECUTES, not what it reads as data.** `security-scan.sh` runs
`security_scan.*` out of `~/Projects/security-standards/src`, so it activates that checkout; it
scans `~/.claude` as data, so it does not.

### 2. Activation never gates the job

Every path returns 0. A backup that refuses to run because a tree is dirty is worse than a backup
running slightly old code, and a scan that refuses is worse than a stale scan. Activation is
best-effort; the job's own exit code keeps meaning what its header says it means.

The signal is one `[activation]` line per run, always printed.

### 3. Two conditions, deliberately named differently

A checkout on a branch other than `main` is a build session working in a main tree. That is
ordinary here, and the line says so plainly. A checkout on `main` that cannot fast-forward is
anomalous, and its line says `HELD`.

Neither stops the job, and neither is a finding on its own — this estate has now recorded five
times that a control which reports its own correct behaviour goes permanently red. **Turning a
persistent one into a finding is ADR-0030's sweep, which observes the disk.** Until that sweep is
built and running, the only signal is the log line, and this ADR does not claim otherwise.

### 4. The pull carries `uv sync --frozen`, keyed on the manifest actually moving

A pull alone does not install a newly declared console script, and every launcher invokes its
program by absolute path inside `.venv/bin` — so the failure of skipping this is a missing binary
rather than an import error. The sync runs when the pull moved `pyproject.toml` or `uv.lock`, and
not otherwise: a pull that touches neither cannot have changed what is installed.

A failed sync prints loudly to stderr and still does not fail the job. Most launchers still fold
their exit codes in a way that can read a missing binary as success, so that line is the only
signal that the next absolute-path invocation may find nothing.

### 5. One copy, deployed and sourced cross-repo

`security-standards/scripts/activate-checkout.sh` → `~/.claude/bin/activate-checkout.sh`, a
governed `[[tool]]` deployment, sourced by launchers in six repositories with a no-op fallback that
says the run is not activated.

Callers live in six repositories, and a copy per repository is six copies of one rule.
`observe-run.sh` already has two copies; that is the cautionary tale, not the pattern. **A second
copy of this helper is a defect.**

### 6. It re-execs the caller when HEAD moves

Every launcher's own file lives inside the checkout it activates, and bash reads a script
incrementally by byte offset — rewriting the file underneath a running shell can garble everything
past the current read position. Re-execing also makes the run that pulled the change the run that
uses it.

`SDS_ACTIVATION_REEXEC` bounds it at one extra pass.

## Consequences

**A repository with no scheduled consumer is not reached by this.** `intent-packages` and
`FacelessTT` are per-invocation CLIs, and `~/.claude`'s consumers are hooks and Claude sessions.
ADR-0030 enrols nine working copies; this activates the six that a launcher executes from. The
other three are named here rather than silently omitted.

**A deployed `~/.claude/bin` artifact needs `make install` as well as a pull.** Activating
`~/Projects/security-standards` refreshes the library those two launchers import; the copies of the
launchers themselves in `~/.claude/bin` move only when the governance deploy runs. That is the same
distinction ADR-0030 draws between a working copy and what is loaded, one layer out.

**Two launchers in one repository can race for `index.lock`.** `change-proposer` and
`deploy-watcher` are both hourly. The loser reports it could not fast-forward and runs the code it
has, which self-clears on the next fire. Accepted; no machinery.

**The first activation is manual, and the irony is the point.** The helper reaches
`~/.claude/bin` only when a security-standards main tree has pulled the merge and run
`make install` — a `git pull` performed by hand, which is exactly the gap being closed.
