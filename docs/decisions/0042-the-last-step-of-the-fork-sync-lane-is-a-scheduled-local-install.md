# ADR-0042 — The last step of the fork-sync lane is a scheduled local install

- **Status:** Accepted
- **Date:** 2026-09-06
- **Decided by:** Devon
- **Relates to:** ADR-0002 (out-of-process, report-only programs), ADR-0030 (the second deployment
  model: a change is live on the operator machine when the code is there and the next start picks
  it up), ADR-0009 (`reach`, of which `operator_machine` is the member whose change window governs
  this lane).

## Decision

`AlobarQuest/rtk`'s upstream sync runs end to end — sync, security review, `cargo audit`,
hardening, pull request, the fork's own required check, the inert landing lane — and **landing does
not install.** The binary on the operator machine is whatever was last installed by hand.

A tenth scheduled local lane closes that step. It acts only when **both** of two independent terms
hold, and there is no override for either.

Devon, 2026-09-05: *"we have two repos that we clone, run security scans on, and then SHOULD be
merging them into production on this machine. The last step is not functional yet and I think it
should run through SDS."*

## Why it cannot be a work unit

**The factory cannot do this.** factory-runner executes on GitHub-hosted runners, and they cannot
touch this machine. So "what runs the install" was never a choice between a scheduled lane and a
work unit — no arrangement of the factory reaches the binary. This is the same constraint ADR-0030
met from the other side, and it is why the answer is a local pass rather than a dispatch.

## The two terms, and why neither substitutes for the other

`--install` says the operator permits this pass to act. The change window says not now. **Both must
hold.** They are different claims: one is about authority, the other about timing, and a design
that collapsed them would let a permission granted once act at any hour.

There is deliberately **no window override.** An operator who wants an out-of-hours install runs
`cargo install` by hand, which is one command and is honest about being a human act. An override
would be the same act wearing the lane's clothes, and the record would then say the lane installed
something the lane's own rule forbade.

**The window is asked of production, never parsed from the file.** Two reasons. The lane then obeys
the *deployed* policy, which is the one the factory obeys — a file read would obey whatever is on
disk, which is a different thing the moment they differ. And `factory-policy.toml` has a one-reader
guard whose spirit is that the artifact has a single consumer; a second parser is the second copy
that guard exists to prevent, even though this tree sits outside the one it scans.

**A window that cannot be read is a refusal, never a default.** There are no fallback hours. A
default would be this program deciding, from a policy it could not read, that now is a fine time to
replace the tool that filters every command on the machine.

## Why replacing this particular binary is safe to automate

`rtk` filters every Bash command in every agent session here, so replacing it is replacing
something in use. Four things stand against that, and only the first is this lane's own work:

1. **`cargo install --force` writes the new binary only on SUCCESS.** A failed build leaves the
   working binary untouched, and the lane never reaches its rollback. This is the single property
   that makes the whole thing automatable.
2. **The change window** puts the swap at an hour when nobody is working.
3. **The pass proves the artifact before walking away.** `--version` checked *by value* against what
   cargo recorded, `gain`, and `proxy` on a trivial command. A build that succeeded and a binary
   that answers `--version` is a weak bar for a tool that intercepts every command — and measured
   during the build, a binary that lied about its version passed all three exit-status probes and
   was caught only by the value check.
4. **A probe failure restores the previous artifact**, and the restore is verified.

## The rollback covers three files, and a rollback that covered one would be worse than none

`cargo install --force` writes the binary **and** `.crates.toml` **and** `.crates2.json`, and it
writes the metadata on a successful *build* — which is before this lane has probed anything.

Restoring only the binary would leave the machine running the old tool while cargo's record claimed
the new revision. The next pass would compare that record against the fork's head, find them equal,
report nothing to do, and never retry: **a rollback that becomes permanent and invisible.** All
three are captured and restored, and artifacts a first install created that were not there before
are removed rather than left behind.

After a restore the machine is genuinely behind, so every later pass reports it. A standing finding
until somebody acts is the honest shape; silence is not.

## What is installed, and what is not generalised

`rtk` only, declared as a table row rather than hard-coded, so a second tool is a row and not a
rewrite. Nothing is generalised beyond what one row proves — an abstraction fitted to a single case
is a guess wearing the shape of a design.

## The record

One observation per pass, whether or not anything was installed, under a new `source_system`
(`tool_installer`) and a new `observation_type` (`tool_revision`); migration 0033 admits both.
`machine_activation` / `activation` is the near miss and is wrong: it asserts what a *working copy*
will execute at its next start, where this asserts what a compiled *binary* on the machine is.
Reusing a near-miss would write false provenance into rows that have no supersession model and no
delete route.

`observed_at` is the installed revision's committer date, never a wall clock — the orchestrator's
replay check hashes the whole command, so a wall clock makes the second pass over unchanged reality
an `observation_conflict`, permanently. The reference is content-addressed over the **whole composed
record** rather than over `facts` alone, which is the defect two adversarial reviews found in the
activation sweep: because the reference is also the idempotency key, the server compares the entire
stored command, so digesting `facts` alone would make a reworded summary a conflict for every
machine whose tool had not moved.

## What this does not decide

It does not decide that a landed pull request may install itself, and it does not give the factory a
path to this machine. The lane reads a revision the fork already holds; getting a change into that
fork stays the sync lane's job and a person's merge.

It does not add a second tool. The table has one row.
