from tracker_projection_adapter.cli import project
from tracker_projection_adapter.tracker import ItemRef


class FakeClient:
    def __init__(self, units, bindings):
        self._units = units
        self._bindings = bindings
        self.upserts = []

    def status_ledger(self):
        return self._units

    def tracker_bindings(self):
        return self._bindings

    def upsert_tracker_binding(self, **kwargs):
        self.upserts.append(kwargs)
        return {}


class FakeProjector:
    def __init__(self):
        self.calls = []

    def create_item(self, unit):
        self.calls.append(("create", unit.work_unit_id))
        return ItemRef("task-new", "https://todoist/app/task/task-new")

    def update_item(self, item_ref, unit):
        self.calls.append(("update", unit.work_unit_id))
        return ItemRef(item_ref.external_item_id, item_ref.external_url)

    def complete_item(self, item_ref):
        self.calls.append(("complete", item_ref.external_item_id))


def test_dry_run_makes_no_writes():
    client = FakeClient(
        units=[{"unit_id": "u1", "unit_key": "K-1", "unit_title": "t", "unit_state": "ready"}],
        bindings=[],
    )
    projector = FakeProjector()
    counts = project(client, projector, dry_run=True)
    assert counts["create"] == 1
    assert projector.calls == []
    assert client.upserts == []


def test_create_flow_projects_then_writes_binding():
    client = FakeClient(
        units=[{"unit_id": "u1", "unit_key": "K-1", "unit_title": "t", "unit_state": "ready"}],
        bindings=[],
    )
    projector = FakeProjector()
    project(client, projector, dry_run=False)
    assert ("create", "u1") in projector.calls
    assert client.upserts[0]["external_item_id"] == "task-new"
    assert client.upserts[0]["projected_state"] == "ready"


def test_complete_flow_closes_task_and_writes_binding():
    client = FakeClient(
        units=[{"unit_id": "u1", "unit_key": "K-1", "unit_title": "t", "unit_state": "completed"}],
        bindings=[
            {
                "work_unit_id": "u1",
                "tracker_system": "todoist",
                "external_item_id": "task-9",
                "external_url": None,
                "projected_state": "executing",
            }
        ],
    )
    projector = FakeProjector()
    project(client, projector, dry_run=False)
    assert ("complete", "task-9") in projector.calls
    assert client.upserts[0]["projected_state"] == "completed"
