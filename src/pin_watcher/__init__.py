"""The pin watcher: does every repository the factory can dispatch to run the runner we chose?

WHAT THIS EXISTS BECAUSE OF. On 2026-09-04 five of six caller workflows were pinned at
`5178471a` -- twenty-three commits behind `RECOMMENDED_CALLER_PIN` -- carrying none of that week's
runner fixes and none of ADR-0038. Nothing reported it. `runner.caller` in the conformance kit
measures exactly this and runs when a person runs it; the orchestrator's build gate holds the
RECOMMENDATION to what this repository serves and, by its own module's admission, cannot see a
target repository that has drifted off it. So the property was true, undefended, and stopped being
true without a sound.

WHY IT MATTERS RATHER THAN BEING HYGIENE. The reusable workflow installs the CLI at the caller's
own `uses:` SHA, so a caller's pin IS the runner revision a dispatch executes. A stale caller does
not fail: it runs, and it runs a runner nobody chose, spending a work unit's attempt on a revision
missing fixes that were merged precisely to stop that attempt being wasted.

THIS LANE ONLY READS. It writes one observation per caller and changes no repository, no workflow
and no pin. Advancing a pin stays a person's one-line pull request.
"""
