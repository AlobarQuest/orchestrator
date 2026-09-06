"""The tool installer: the last step of the fork-sync lane, which until now was a person.

WHAT THIS CLOSES. `AlobarQuest/rtk` is a fork whose upstream sync runs end to end -- sync,
security review, `cargo audit`, hardening, pull request, the fork's own required check, the inert
landing lane. **Landing does not install.** The binary on the operator machine is whatever was
last installed by hand, so a merged fix reaches the machine only when somebody remembers to run
`cargo install`. Every other lane in this estate ends in an observation; this one ended in an
intention.

WHY IT IS A LOCAL LANE AND NOT A WORK UNIT. factory-runner executes on GitHub-hosted runners, and
they cannot touch this machine. There is no arrangement of the factory that installs a binary
here, so "what runs the install" was never a choice -- it is a scheduled local pass, the tenth
alongside the nine that exist.

THE SHARP RISK, AND WHAT ANSWERS IT. `rtk` filters every Bash command in every agent session on
this machine, so replacing it is replacing something in use. Three things stand against that.
`cargo install --force` writes the new binary only on SUCCESS, so a failed build leaves the
working one untouched. The change window -- `operator_machine`, read from the deployed policy
rather than from any file here -- means the swap happens while nobody is working. And the pass
proves the new binary before it walks away: `--version` reporting the expected version, `gain`,
and `proxy` on a trivial command, because a build that succeeded and a binary that answers
`--version` is a weak bar for a tool that intercepts every command.

TWO TERMS MUST BOTH HOLD BEFORE ANYTHING IS WRITTEN. `--install` says the operator permits this
pass to act; the window says not now. There is deliberately NO window override: an operator who
wants an out-of-hours install runs `cargo install` by hand, which is one command and honest about
being a human act.
"""
