from pathlib import Path


def test_factory_runner_pilot_has_no_schedule_or_merge() -> None:
    workflow = Path(".github/workflows/factory-runner-pilot.yml").read_text()
    workflow_lower = workflow.lower()

    assert "workflow_dispatch:" in workflow
    assert "schedule:" not in workflow_lower
    assert "gh pr merge" not in workflow_lower
    assert "merge-method" not in workflow_lower
    assert "/merges" not in workflow_lower
    assert "git push origin main" not in workflow_lower
    assert "coolify" not in workflow_lower
    assert "deploy" not in workflow_lower
    assert "deployments:" not in workflow_lower
