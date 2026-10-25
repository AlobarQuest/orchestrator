"""Fork hardening (ADR-0057): the version of a fork Devon runs, when its review finds a problem.

A separate program beside the fork-sync lane. fork-sync lands upstream's release on a fork's
`main` and never changes upstream's code; this program writes a `hardened` branch on the same
fork, which is `main` with each rule's fix applied, and the tool installer's row is pointed at
it. The two processes meet only at the fork's landed `main`, and this one meets the installer
only at the row's `branch`.
"""
