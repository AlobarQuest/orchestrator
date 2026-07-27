import httpx

from tracker_projection_adapter.projection import UnitView
from tracker_projection_adapter.tracker import ItemRef, TodoistProjector


def _projector(seen):
    def handler(request):
        seen.append(f"{request.method} {request.url.path}")
        if request.url.path == "/api/v1/tasks":
            return httpx.Response(200, json={"id": "9", "url": "https://todoist/app/task/9"})
        if request.url.path.endswith("/close"):
            return httpx.Response(204)
        if request.url.path.startswith("/api/v1/tasks/"):
            return httpx.Response(200, json={"id": "9", "url": "https://todoist/app/task/9"})
        return httpx.Response(404)

    return TodoistProjector(
        token="tok",
        project_id="proj-1",
        review_base_url="https://sds.alobar.net",
        transport=httpx.MockTransport(handler),
    )


def test_create_item_posts_a_task_and_returns_ref():
    seen = []
    ref = _projector(seen).create_item(UnitView("u1", "K-1", "Title", "ready"))
    assert ref.external_item_id == "9"
    assert "POST /api/v1/tasks" in seen


def test_complete_item_closes_the_task():
    seen = []
    _projector(seen).complete_item(ItemRef("9", None))
    assert "POST /api/v1/tasks/9/close" in seen
