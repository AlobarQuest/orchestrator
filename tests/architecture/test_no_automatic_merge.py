from pathlib import Path


def test_workflows_never_merge_deploy_or_push_main() -> None:
    workflow_paths = [
        path
        for path in Path(".github/workflows").glob("*")
        if path.name != "factory-runner-pilot.yml"
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
