"""Alembic's `fileConfig` (migrations/env.py) disables every logger that already exists in the
process, so a test that runs after a migration on the same worker would capture nothing."""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def alert_hub_logger_enabled() -> Iterator[None]:
    logger = logging.getLogger("alert_hub_client")
    was_disabled, logger.disabled = logger.disabled, False
    try:
        yield
    finally:
        logger.disabled = was_disabled
