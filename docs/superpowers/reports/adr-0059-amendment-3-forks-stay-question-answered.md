# Question: adr-0059-amendment-3-forks-stay

Worker W-67, 2026-11-10. Nothing is committed or pushed yet.

## The contradiction

The handoff's item 4 and D-35's option 1 say: "The forks stay members, so the landing lanes keep
their Dependabot pull requests." The sources say the forks' update pull requests aren't
Dependabot's:

- None of the five forks has `.github/dependabot.yml` on `main` (`gh api
  repos/AlobarQuest/<fork>/contents/.github/dependabot.yml` answers 404 for `rtk`,
  `claude-octopus`, `rea`, `n8n-as-code` and `n8n-manager`). `fork-sync baseline` removes
  upstream's whole `.github/` (`docs/operations/onboarding-a-fork.md`, line 16).
- The forks' update pull requests are the fork-sync lane's rolling sync pull requests, authored by
  `fork-secure-upstream-sync[bot]` (`onboarding-a-fork.md`, step 10; change-manager
  `app/deploy_policy.py`, lines 612-630: the inert policy lists the five forks, with
  `permitted_authors` `{dependabot[bot], fork-secure-upstream-sync[bot]}` and the sync bot in
  `non_ecosystem_authors`).
- A Dependabot pull request on a fork fails the required `security review` check, "which keeps
  such a pull request from landing" (`onboarding-a-fork.md`, line 508; ADR-0056, line 93).

So the lane that keeps acting on the forks because they stay members is the inert landing lane,
and what it lands is the fork-sync pull requests. Option 1's decision (reclassify both lanes as
Stays) doesn't depend on this sentence, but item 4 asks me to state it with citations.

## Proposed answer

Write item 4 as follows: the forks keep their member record grants (`dependency_updates` and
`security_patches`, which `membership_check/consistency.py`, lines 143-144, requires of every
synced fork), so the inert landing lane keeps landing their update pull requests. On the forks,
those are the fork-sync lane's sync pull requests by `fork-secure-upstream-sync[bot]`. The forks
carry no Dependabot configuration, and a Dependabot pull request on a fork fails
`security review`. Correct D-35's wording in the amendment's Context, quoting D-35 unchanged.

Reply "yes" to take the proposed answer, or give the wording you want.
