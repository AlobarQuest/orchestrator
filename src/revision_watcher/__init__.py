"""Is every deployed application serving the commit its repository's default branch names?

Nothing in this estate asked that question until 2026-09-08. Three surfaces report a rollout that
went wrong -- the target repository's own CI, `deploy_watcher`, and the landing ledger's
`default_branch_not_green` -- and every one of them reports a MOMENT. On 2026-09-06 all three fired
correctly and `app-brain` served a build behind `main` for a day and a half anyway, because a
moment that has passed is not a condition anyone can still see.

This lane reports the CONDITION. It is cause-independent by construction: a Coolify deployment that
failed and rolled back, an image that never built, a rotated webhook secret, a manual swap nobody
performed -- each of them ends with production serving something other than what was landed, and
that is the only thing measured here.
"""
