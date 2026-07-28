import httpx

from tracker_projection_adapter.cli import project, reconcile
from tracker_projection_adapter.tracker import ItemRef


class FakeClient:
    def __init__(self, units=None, bindings=None):
        self._units = units or []
        self._bindings = bindings or []
        self.upserts = []
        self.reported = []
        self.reported_key = None

    def status_ledger(self):
        return self._units

    def tracker_bindings(self):
        return self._bindings

    def upsert_tracker_binding(self, **kwargs):
        self.upserts.append(kwargs)
        return {}

    def report_tracker_reconciliation(self, *, observed_states, idempotency_key):
        self.reported = observed_states
        self.reported_key = idempotency_key
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
    counts = reconcile(client, projector, dry_run=False, pass_id="p1")
    assert projector.calls == [("item_completed", "tid-1")]
    assert client.reported == [
        {"tracker_system": "todoist", "external_item_id": "tid-1", "observed_completed": True}
    ]
    assert counts == {"reported": 1, "skipped": 0}


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
    reconcile(client, projector, dry_run=True, pass_id="p1")
    assert projector.calls == [("item_completed", "tid-1")]
    assert client.reported == []


class ExplodingProjector(FakeProjector):
    """Raises on one specific item, the way TodoistProjector does on a non-404 >= 400."""

    def __init__(self, completed, explode_on):
        super().__init__(completed)
        self._explode_on = explode_on

    def item_completed(self, item_ref):
        if item_ref.external_item_id == self._explode_on:
            raise RuntimeError("todoist rejected GET /tasks/tid-2: 500")
        return super().item_completed(item_ref)


def _binding(item_id):
    return {
        "work_unit_id": f"u-{item_id}",
        "tracker_system": "todoist",
        "external_item_id": item_id,
        "external_url": None,
        "projected_state": "ready",
    }


def test_one_unreadable_item_does_not_discard_the_rest():
    """The report is a single POST at the end, so an abort mid-loop reports NOTHING about the
    items already read -- not merely the failing item."""
    client = FakeClient(bindings=[_binding("tid-1"), _binding("tid-2"), _binding("tid-3")])
    projector = ExplodingProjector({"tid-1": True, "tid-3": False}, explode_on="tid-2")

    counts = reconcile(client, projector, dry_run=False, pass_id="p1")

    assert counts == {"reported": 2, "skipped": 1}
    assert [row["external_item_id"] for row in client.reported] == ["tid-1", "tid-3"]


def test_each_pass_reports_under_its_own_idempotency_key():
    client = FakeClient(bindings=[_binding("tid-1")])
    projector = FakeProjector(completed={"tid-1": True})

    reconcile(client, projector, dry_run=False, pass_id="p1")

    assert client.reported_key == "tracker-detect:p1"


class TimeoutProjector(FakeProjector):
    """Raises a transport-level httpx error on one item -- no status code was ever received,
    unlike ExplodingProjector's RuntimeError which models TodoistProjector._get's >= 400 case."""

    def __init__(self, completed, explode_on):
        super().__init__(completed)
        self._explode_on = explode_on

    def item_completed(self, item_ref):
        if item_ref.external_item_id == self._explode_on:
            raise httpx.ConnectTimeout("connect timed out")
        return super().item_completed(item_ref)


def test_a_connection_timeout_on_one_item_does_not_discard_the_rest():
    """httpx.ConnectTimeout (and ReadTimeout/ConnectError, etc.) raise from self._client.get(...)
    before any status code exists, so they are not RuntimeError -- a distinct failure mode from
    the >= 400 case ExplodingProjector models, and at least as likely for an external API called
    in a loop."""
    client = FakeClient(bindings=[_binding("tid-1"), _binding("tid-2"), _binding("tid-3")])
    projector = TimeoutProjector({"tid-1": True, "tid-3": False}, explode_on="tid-2")

    counts = reconcile(client, projector, dry_run=False, pass_id="p1")

    assert counts == {"reported": 2, "skipped": 1}
    assert [row["external_item_id"] for row in client.reported] == ["tid-1", "tid-3"]
