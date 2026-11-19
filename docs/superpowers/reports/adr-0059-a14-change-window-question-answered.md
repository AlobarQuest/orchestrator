# Question: who sets change-manager's `M2M_TOKEN_EXECUTE` hash and restarts change-manager?

Worker W-232, handoff `adr-0059-a14-change-window`. PR #750 is open (ready for review, head
`f3fb27df`), because I found this gap after I opened it. The amendment is complete apart from this
one owner. `make check` is green on the head: 15,832 passed, 1 skipped. The security scan shows 0
BLOCK.

## The gap

The `execute` bearer authenticates only once two things happen:

1. Someone writes its sha256 hash, as JSON, into change-manager's Coolify variable
   `M2M_TOKEN_EXECUTE`, in both `is_preview` rows.
2. Someone restarts change-manager.

change-manager's `docs/operations/bearer-settings.md` (at `e57076f2`) gives the procedure. Two
other sources don't cover it:

- D-68 B says "change-manager adds an execute scope ... and you mint its bearer for the lane".
- The standing answer says "Devon still mints every new credential value into BWS and grants its
  read".

Neither names the Coolify variable write or the production restart. change-manager's
`.cred-consumers.toml` shows only the rotation executor rotating the other narrow bearers. It
doesn't say who first issued them. The handoff says anything the design needs beyond D-66 to D-69
and the standing answer goes here, not in the ADR.

## What the amendment says now

Decision item 7 makes the BWS mint Devon's act. It then says the amendment "grants that setting's
write and the restart to no one; whoever makes them, they precede the lane's paired pass, which
reads the bearer."

## The question

Who writes `M2M_TOKEN_EXECUTE` and restarts change-manager? I see three options:

- **(A) Devon, as part of his mint.** This reads D-68's "you mint its bearer" as covering it.
- **(B) HQ, through infraops, after Devon mints the value.** HQ would compute the hash in-process
  and never echo the value. This is a production act, and the handoff names none.
- **(C) The SDS rotation executor**, as a revision 1 issue the way the other narrow bearers were
  handed to it. This would need a rotation package.

Reply with a letter, or with a different owner. I'll write it into decision item 7, re-run the
checks, push, and write the report.

## Changes since the PR opened

The fixes from my self-review are pushed in `f3fb27df`:

- The Coolify token's permission mapping moved to a mint-gate verify item.
- The citations and the "re-read" scope sentence are corrected.
- The table has 16 rows; the PR body now says so.

I withdrew the report from the branch until this is answered. I'll restore it with the answer.
