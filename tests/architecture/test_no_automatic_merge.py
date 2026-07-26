from pathlib import Path


def test_workflows_never_merge_deploy_or_push_main() -> None:
    # factory-runner-pilot.yml and release-image.yml are deliberate, human-triggered
    # (workflow_dispatch) exceptions to the "no dispatch/deploy" guard below: the former
    # invokes the factory runner against an approved work unit, the latter builds and
    # pushes a release image (its SECURITY_STANDARDS_DEPLOY_KEY secret name matches
    # "deploy" as a substring, and the actual runtime cutover stays a separate manual
    # step covered by tests/architecture/test_release_workflow.py's own no-deploy checks).
    manual_dispatch_workflows = {"factory-runner-pilot.yml", "release-image.yml"}
    workflow_paths = [
        path
        for path in Path(".github/workflows").glob("*")
        if path.name not in manual_dispatch_workflows
    ]
    workflows = "\n".join(path.read_text().lower() for path in workflow_paths)

    forbidden = (
        "gh pr merge",
        "/merges",
        "git push origin main",
        "workflow_dispatch",
        "coolify",
        "deploy",
    )
    assert not any(value in workflows for value in forbidden)
