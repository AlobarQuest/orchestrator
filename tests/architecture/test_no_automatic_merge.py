from pathlib import Path


def test_workflows_never_merge_deploy_or_push_main() -> None:
    workflows = "\n".join(path.read_text().lower() for path in Path(".github/workflows").glob("*"))

    forbidden = (
        "gh pr merge",
        "/merges",
        "git push origin main",
        "workflow_dispatch",
        "coolify",
        "deploy",
    )
    assert not any(value in workflows for value in forbidden)
