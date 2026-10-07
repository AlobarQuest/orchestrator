from pathlib import Path

WORKFLOW_ROOT = Path(".github/workflows")

FORBIDDEN = (
    "gh pr merge",
    "/merges",
    "git push origin main",
    "workflow_dispatch",
    "coolify",
    "deploy",
)

# release-image.yml is a deliberate, human-triggered (workflow_dispatch) exception to the
# "no dispatch/deploy" guard: it builds and pushes a release image (its
# SECURITY_STANDARDS_DEPLOY_KEY secret name matches "deploy" as a substring, and the actual
# runtime cutover stays a separate manual step covered by
# tests/architecture/test_release_workflow.py's own no-deploy checks).
# attest-exit-criteria.yml is a second, weaker exception: it is read-only (one unauthenticated
# GET of production's public OpenAPI document) and carries workflow_dispatch so the guard can
# be re-run on demand after a production image swap. It merges nothing and writes nothing.
# attest-wave-exit.yml (WS-P2.39) is the same weakest kind for the same reason: read-only,
# one unauthenticated GET, workflow_dispatch so a wave bar can be re-attested on demand.
#
# `factory-runner-pilot.yml` is the fourth: the factory caller, dispatched by the orchestrator for
# one approved work unit at a time (ADR-0015, amendment of 2026-10-07). It is exempt because its
# `uses:` line names the reusable workflow; `test_factory_runner_pilot_scope.py` holds it to no
# schedule, no merge and no deploy. This allowlist is a `continue`, so a stale entry is SILENT by
# itself; `test_the_exemptions_name_only_workflows_that_exist_and_still_need_them` reddens one.
MANUAL_DISPATCH_WORKFLOWS = {
    "factory-runner-pilot.yml",
    "attest-exit-criteria.yml",
    "attest-wave-exit.yml",
    "release-image.yml",
}

# THERE IS NO EXEMPTION HERE ANY MORE, and its removal is the point rather than a tidy-up.
# `dependabot-auto-merge.yml` was exempt from ONE string, `gh pr merge`, because it armed
# GitHub's own auto-merge. ADR-0038 deleted that workflow from this repository on 2026-09-01 --
# the rule it carried moved to change-manager's deploy policy as `inert_landing`, and the
# orchestrator's inert landing lane applies it -- so the exemption named a file that no longer
# exists and the assertions built on it read a missing path.
#
# The scan is now unconditional, which is strictly TIGHTER: no workflow in this repository may
# carry any of the forbidden strings, on any grounds. Restoring an exemption means restoring
# both this constant and its twin in tests/architecture/test_ws33_scope_guards.py, which scans
# the same directory with a different vocabulary and its own separate allowlist -- an addition
# that updates one leaves the other red.


def _violations(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8").lower()
    return [value for value in FORBIDDEN if value in text]


def test_workflows_never_merge_deploy_or_push_main() -> None:
    for path in WORKFLOW_ROOT.glob("*"):
        if path.name in MANUAL_DISPATCH_WORKFLOWS:
            continue
        assert not _violations(path), (
            f"{path.name} carries a forbidden string. If it is a deliberate exception, "
            "name it here with a reason -- openly, never by rewording."
        )


def test_the_exemptions_name_only_workflows_that_exist_and_still_need_them() -> None:
    """Each exempt workflow must exist and still carry a forbidden string. Otherwise the entry
    excuses nothing today and silently excuses whatever a later edit adds to that file."""
    missing = sorted(
        name for name in MANUAL_DISPATCH_WORKFLOWS if not (WORKFLOW_ROOT / name).is_file()
    )
    assert not missing, f"MANUAL_DISPATCH_WORKFLOWS names workflows that no longer exist: {missing}"

    unused = sorted(
        name for name in MANUAL_DISPATCH_WORKFLOWS if not _violations(WORKFLOW_ROOT / name)
    )
    assert not unused, (
        f"these workflows are exempt but no longer carry a forbidden string: {unused}. Remove them."
    )
