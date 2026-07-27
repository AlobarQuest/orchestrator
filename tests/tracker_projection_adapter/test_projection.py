from tracker_projection_adapter.projection import (
    BindingView,
    UnitView,
    plan_actions,
)


def _unit(uid, state):
    return UnitView(work_unit_id=uid, unit_key=f"K-{uid}", unit_title="t", unit_state=state)


def _binding(uid, projected):
    return BindingView(
        work_unit_id=uid,
        tracker_system="todoist",
        external_item_id=f"task-{uid}",
        external_url=None,
        projected_state=projected,
    )


def test_new_active_unit_is_created():
    actions = plan_actions([_unit("u1", "ready")], [])
    assert [(a.kind, a.unit.work_unit_id) for a in actions] == [("create", "u1")]


def test_new_terminal_unit_without_binding_is_skipped():
    actions = plan_actions([_unit("u1", "completed")], [])
    assert actions[0].kind == "skip"


def test_changed_active_unit_is_updated():
    actions = plan_actions([_unit("u1", "executing")], [_binding("u1", "ready")])
    assert actions[0].kind == "update"


def test_unchanged_unit_is_skipped():
    actions = plan_actions([_unit("u1", "ready")], [_binding("u1", "ready")])
    assert actions[0].kind == "skip"


def test_terminal_unit_with_stale_binding_is_completed():
    actions = plan_actions([_unit("u1", "completed")], [_binding("u1", "executing")])
    assert actions[0].kind == "complete"


def test_already_completed_binding_is_skipped():
    actions = plan_actions([_unit("u1", "completed")], [_binding("u1", "completed")])
    assert actions[0].kind == "skip"
