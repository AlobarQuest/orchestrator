"""A packages checkout every membership-check test reads instead of the real one."""

import pytest


@pytest.fixture(autouse=True)
def packages_checkout(tmp_path, monkeypatch):
    """An empty, readable packages checkout; never the real one."""
    root = tmp_path / "intent-packages"
    (root / "packages").mkdir(parents=True)
    monkeypatch.setenv("BUMP_PROPOSER_PACKAGES_CHECKOUT", str(root))
    return root
