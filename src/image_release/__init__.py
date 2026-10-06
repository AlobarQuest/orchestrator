"""The orchestrator's deploy-time release binder: the image just deployed, bound to what it carries.

SDS 1.1 item 5a, and ADR-0039's amendment of 2026-10-05. The operator-run deploy session calls it
once, AFTER it has read the running container's image digest and revision label on the host and
found both equal to what the `Release image` workflow pushed. Once production is confirmed to
serve the built commit, it does three things for every completed factory unit whose landing
commit the built commit carries and the previous revision did not:

1. binds a `container_image` release artifact naming the pushed digest (SYSTEM);
2. files the `container_image` deployment observation (SYSTEM), which mints one verifier-owned
   post-deploy unit per binding;
3. verifies that post-deploy unit (VERIFIER) -- the same session, not a later lane.

WHY A PROGRAM OF ITS OWN rather than a subcommand of `activation-sweep`. The ADR-0039 guard holds
that no out-of-process program may send a `container_image` binding or observation, because that
observation mints a work unit. This program is the ONE exception, carried in its own row of that
guard, so the sweep's machine-local lane keeps its guarantee whole: nothing it can import or send
can mint a unit.

WHAT IT READS, which is the boundary the amendment names: the orchestrator's own production
endpoints and the operator's verified digest. No content authored outside this estate reaches any
field it writes -- the one free-text field, `status_summary.summary`, is composed here from status
codes and counts.

It is not scheduled and nothing calls it but a person's deploy session.
"""
