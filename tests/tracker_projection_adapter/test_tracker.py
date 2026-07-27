import httpx
import pytest

from tracker_projection_adapter.projection import UnitView
from tracker_projection_adapter.tracker import ItemRef, TodoistProjector


def _projector(handler):
    return TodoistProjector(
        token="t",
        project_id="p",
        review_base_url="https://sds.alobar.net",
        transport=httpx.MockTransport(handler),
    )


def _unit(state="ready"):
    return UnitView(work_unit_id="u1", unit_key="K-u1", unit_title="Title", unit_state=state)


def test_create_item_posts_a_task_and_returns_ref():
    seen = []

    def handler(request):
        seen.append(f"{request.method} {request.url.path}")
        return httpx.Response(200, json={"id": "9", "url": "https://todoist/app/task/9"})

    ref = _projector(handler).create_item(UnitView("u1", "K-1", "Title", "ready"))
    assert ref.external_item_id == "9"
    assert "POST /api/v1/tasks" in seen


def test_complete_item_closes_the_task():
    seen = []

    def handler(request):
        seen.append(f"{request.method} {request.url.path}")
        return httpx.Response(204)

    _projector(handler).complete_item(ItemRef("9", None))
    assert "POST /api/v1/tasks/9/close" in seen


def test_item_completed_true_when_task_missing():
    # A completed Todoist task leaves the active set; GET /tasks/{id} returns 404.
    projector = _projector(lambda r: httpx.Response(404, json={}))
    assert projector.item_completed(ItemRef("tid-1", None)) is True


def test_item_completed_false_for_active_task():
    projector = _projector(
        lambda r: httpx.Response(200, json={"id": "tid-1", "is_completed": False})
    )
    assert projector.item_completed(ItemRef("tid-1", None)) is False


def test_item_completed_true_when_flag_set():
    projector = _projector(
        lambda r: httpx.Response(200, json={"id": "tid-1", "is_completed": True})
    )
    assert projector.item_completed(ItemRef("tid-1", None)) is True


def test_update_item_falls_back_to_existing_url_when_response_has_none():
    projector = _projector(lambda r: httpx.Response(200, json={"id": "tid-1"}))
    ref = projector.update_item(ItemRef("tid-1", "https://old"), _unit(state="ready"))
    assert ref.external_url == "https://old"


def test_update_item_raises_on_non_2xx():
    projector = _projector(lambda r: httpx.Response(500, json={}))
    with pytest.raises(RuntimeError):
        projector.update_item(ItemRef("tid-1", "https://old"), _unit(state="ready"))
