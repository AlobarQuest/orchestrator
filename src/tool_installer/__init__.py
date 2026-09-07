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

A SECOND ROW ARRIVED, AND IT IS A PLUGIN. `octo` (Claude Octopus) reaches this machine as a
Claude Code plugin and nothing updated it -- it sat 72 days, 323 commits and two major versions
behind the fork, because the routine that updates it fires only after a sync pull request merges
and a person has to remember. `tools.py` kept its table and grew a per-row install/probe strategy;
`plugin.py` is that strategy, and its docstring carries the two things a reader must have: Claude
Code loads a SEPARATE versioned copy that a `git pull` does not touch, and what a plugin can be
proven to be is much weaker than what a binary can.

**THIS LANE NOW PUSHES A COMMIT, and it is named here rather than left to be discovered.** Moving
`octo`'s version means moving the pin in `AlobarQuest/devon-plugins`, and leaving that edit
uncommitted every night is the dirty-working-copy state another lane exists to report. So the pass
commits the pin and publishes it -- one line of one file, after the install has been proven, with
the exemption that requires taken openly in the repository-wide merge guard's own register.

TWO TERMS MUST BOTH HOLD BEFORE ANYTHING IS WRITTEN. `--install` says the operator permits this
pass to act; the window says not now. There is deliberately NO window override: an operator who
wants an out-of-hours install runs `cargo install` by hand, which is one command and honest about
being a human act.
"""
