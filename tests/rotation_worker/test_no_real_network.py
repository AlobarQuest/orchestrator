"""The suite's network refusal fires on the path that once reached production Coolify.

The address is a closed local port, so this test reaches nothing even without the refusal.
"""

from __future__ import annotations

import pytest

from rotation_worker.coolify import CoolifyClient
from rotation_worker.values import Value
from tests.rotation_worker.conftest import RealNetworkRefused


def test_a_client_built_without_a_fake_transport_is_refused() -> None:
    client = CoolifyClient("https://127.0.0.1:9", Value("placeholder"), "application", "app")
    with pytest.raises(RealNetworkRefused):
        client.restart()
