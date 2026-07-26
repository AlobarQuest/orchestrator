"""The tracker-agnostic projection seam + the Todoist implementation.

TrackerProjector is the interface. TodoistProjector is the first concrete tracker; a Linear
implementation would be a second class behind the same protocol, with zero orchestrator change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from tracker_projection_adapter.projection import UnitView

TODOIST_API_BASE = "https://api.todoist.com/rest/v2"


@dataclass(frozen=True)
class ItemRef:
    external_item_id: str
    external_url: str | None


class TrackerProjector(Protocol):
    def create_item(self, unit: UnitView) -> ItemRef: ...
    def update_item(self, item_ref: ItemRef, unit: UnitView) -> ItemRef: ...
    def complete_item(self, item_ref: ItemRef) -> None: ...


class TodoistProjector:
    def __init__(
        self,
        *,
        token: str,
        project_id: str,
        review_base_url: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._project_id = project_id
        self._review_base_url = review_base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=TODOIST_API_BASE,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
            transport=transport,
        )

    def _content(self, unit: UnitView) -> str:
        return f"[{unit.unit_key}] {unit.unit_title}"

    def _description(self, unit: UnitView) -> str:
        return f"{self._review_base_url}/review/units/{unit.work_unit_id}"

    def create_item(self, unit: UnitView) -> ItemRef:
        data = self._post(
            "/tasks",
            {
                "content": self._content(unit),
                "description": self._description(unit),
                "project_id": self._project_id,
                "labels": [f"sds:{unit.unit_state}"],
            },
        )
        return ItemRef(external_item_id=str(data["id"]), external_url=data.get("url"))

    def update_item(self, item_ref: ItemRef, unit: UnitView) -> ItemRef:
        data = self._post(
            f"/tasks/{item_ref.external_item_id}",
            {
                "content": self._content(unit),
                "description": self._description(unit),
                "labels": [f"sds:{unit.unit_state}"],
            },
        )
        url = data.get("url") if isinstance(data, dict) else None
        return ItemRef(
            external_item_id=item_ref.external_item_id,
            external_url=url or item_ref.external_url,
        )

    def complete_item(self, item_ref: ItemRef) -> None:
        self._post(f"/tasks/{item_ref.external_item_id}/close", None)

    def _post(self, path: str, payload: dict[str, Any] | None) -> Any:
        response = (
            self._client.post(path, json=payload)
            if payload is not None
            else self._client.post(path)
        )
        if response.status_code >= 400:
            raise RuntimeError(f"todoist rejected POST {path}: {response.status_code}")
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()
