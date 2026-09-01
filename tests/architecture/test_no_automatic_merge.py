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

# factory-runner-pilot.yml and release-image.yml are deliberate, human-triggered
# (workflow_dispatch) exceptions to the "no dispatch/deploy" guard: the former
# invokes the factory runner against an approved work unit, the latter builds and
# pushes a release image (its SECURITY_STANDARDS_DEPLOY_KEY secret name matches
# "deploy" as a substring, and the actual runtime cutover stays a separate manual
# step covered by tests/architecture/test_release_workflow.py's own no-deploy checks).
# attest-exit-criteria.yml is a third, weaker exception: it is read-only (one unauthenticated
# GET of production's public OpenAPI document) and carries workflow_dispatch so the guard can
# be re-run on demand after a production image swap. It merges nothing and writes nothing.
# attest-wave-exit.yml (WS-P2.39) is the same weakest kind for the same reason: read-only,
# one unauthenticated GET, workflow_dispatch so a wave bar can be re-attested on demand.
MANUAL_DISPATCH_WORKFLOWS = {
    "attest-exit-criteria.yml",
    "attest-wave-exit.yml",
    "factory-runner-pilot.yml",
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
