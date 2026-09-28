"""Deciding a decomposition proposal the way a person does: through its /review form.

Approving and rejecting a breakdown have had no /api route since Tier 3 item 24b, so a test that
needs one decided renders the proposal page, lifts the form's CSRF token and idempotency key, and
posts the confirmed form -- the production path, not a service shortcut. The proposal is then read
back through the /api read route, which returns the same projection the deleted routes returned.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi.testclient import TestClient


def decide_decomposition(
    client: TestClient,
    proposal_id: object,
    action: str,
    reason: str,
    *,
    headers: dict[str, str],
) -> dict[str, Any]:
    """Post the confirmed ``approve`` or ``reject`` form and return the decided proposal."""
    page = client.get(f"/review/decomposition-proposals/{proposal_id}", headers=headers)
    assert page.status_code == 200, page.text
    form = re.search(
        rf'action="/review/decomposition-proposals/{proposal_id}/{action}"[\s\S]*?</form>',
        page.text,
    )
    assert form is not None, f"no {action} form on the proposal page"
    token = re.search(r'name="csrf_token" value="([^"]+)"', form.group(0))
    key = re.search(r'name="idempotency_key" value="([^"]+)"', form.group(0))
    assert token is not None and key is not None
    decided = client.post(
        f"/review/decomposition-proposals/{proposal_id}/{action}",
        headers=headers,
        data={
            "csrf_token": token.group(1),
            "idempotency_key": key.group(1),
            "reason": reason,
            "confirm": "yes",
        },
        follow_redirects=False,
    )
    assert decided.status_code == 303, decided.text
    detail = client.get(f"/api/v1/decomposition-proposals/{proposal_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    return detail.json()
