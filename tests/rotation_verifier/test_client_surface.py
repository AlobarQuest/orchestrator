"""The lane's confined surface: exactly the named-check POST was added, and nothing else."""

from __future__ import annotations

import pytest

from rotation_verifier.client import permits

UNIT = "11111111-1111-4111-8111-111111111111"


def test_the_named_check_post_is_permitted() -> None:
    assert permits("POST", f"/api/v1/work-units/{UNIT}/verifier-evidence/named-check")


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", f"/api/v1/work-units/{UNIT}/verifier-evidence/named-check"),
        ("POST", f"/api/v1/work-units/{UNIT}/verifier-evidence/named-check/"),
        ("POST", f"/api/v1/work-units/{UNIT}/verifier-evidence"),
        ("POST", f"/api/v1/work-units/{UNIT}/verifier-evidence/other"),
        ("POST", f"/api/v1/work-units/{UNIT}/adjudications"),
        ("POST", f"/api/v1/work-units/{UNIT}/evidence"),
        ("POST", "/api/v1/work-units/x/verifier-evidence/named-check"),
        ("PUT", f"/api/v1/work-units/{UNIT}/verifier-evidence/named-check"),
    ],
)
def test_nothing_near_it_is_permitted(method: str, path: str) -> None:
    assert not permits(method, path)
