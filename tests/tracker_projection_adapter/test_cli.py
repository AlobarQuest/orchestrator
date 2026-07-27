from tracker_projection_adapter.cli import project, reconcile
from tracker_projection_adapter.tracker import ItemRef


class FakeClient:
    def __init__(self, units=None, bindings=None):
        self._units = units or []
        self._bindings = bindings or []
        self.upserts = []
        self.reported = []

    def status_ledger(self):
        return self._units

    def tracker_bindings(self):
        return self._bindings

    def upsert_tracker_binding(self, **kwargs):
        self.upserts.append(kwargs)
        return {}

    def report_tracker_reconciliation(self, *, observed_states, idempotency_key):
        self.reported = observed_states
        return {}


class FakeProjector:
    def __init__(self, completed=None):
        self.calls = []
        self._completed = completed or {}

    def create_item(self, unit):
        self.calls.append(("create", unit.work_unit_id))
        return ItemRef("task-new", "https://todoist/app/task/task-new")

    def update_item(self, item_ref, unit):
        self.calls.append(("update", unit.work_unit_id))
        return ItemRef(item_ref.external_item_id, item_ref.external_url)

    def complete_item(self, item_ref):
        self.calls.append(("complete", item_ref.external_item_id))

    def item_completed(self, item_ref):
        self.calls.append(("item_completed", item_ref.external_item_id))
        return self._completed[item_ref.external_item_id]


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


def test_reconcile_reports_observed_completion_for_each_todoist_binding():
    client = FakeClient(
        bindings=[
            {
                "work_unit_id": "u1",
                "tracker_system": "todoist",
                "external_item_id": "tid-1",
                "external_url": None,
                "projected_state": "ready",
            },
        ]
    )
    projector = FakeProjector(completed={"tid-1": True})
    counts = reconcile(client, projector, dry_run=False)
    assert client.reported == [
        {"tracker_system": "todoist", "external_item_id": "tid-1", "observed_completed": True}
    ]
    assert counts == {"reported": 1}


def test_reconcile_dry_run_makes_no_report():
    client = FakeClient(
        bindings=[
            {
                "work_unit_id": "u1",
                "tracker_system": "todoist",
                "external_item_id": "tid-1",
                "external_url": None,
                "projected_state": "ready",
            },
        ]
    )
    projector = FakeProjector(completed={"tid-1": False})
    reconcile(client, projector, dry_run=True)
    assert client.reported == []
