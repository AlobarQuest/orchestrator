"""A worker trailing `main` on the code it runs says so after a week (rotation Part 1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from rotation_worker import worker
from rotation_worker.git_mirror import MirrorError, RepoMirror
from rotation_worker.notify import Act, behind_key
from tests.rotation_worker.conftest import ENV, Origins
from tests.rotation_worker.stores import MemoryStore
from tests.rotation_worker.test_notify import Hub

REPOSITORY = worker.BEHIND_REPOSITORY
NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)


def _commit(origins: Origins, files: dict[str, str], age_days: float) -> str:
    """A commit on the origin's `main` made `age_days` before NOW."""
    when = f"{int((NOW - timedelta(days=age_days)).timestamp())} +0000"
    dates = {"GIT_COMMITTER_DATE": when, "GIT_AUTHOR_DATE": when}
    ENV.update(dates)
    try:
        return origins.commit(REPOSITORY, files)
    finally:
        for key in dates:
            ENV.pop(key)


class Run:
    def __init__(self, origins: Origins, mirror: RepoMirror, state: Path) -> None:
        self.origins, self.mirror, self.state = origins, mirror, state
        self.hub = Hub()

    def setup(self, revision: str) -> worker.Setup:
        return cast(
            worker.Setup,
            SimpleNamespace(
                mirror=self.mirror,
                store=MemoryStore(),
                source_revision=f"rotation-worker@{revision[:12]}",
                client=SimpleNamespace(ready_units=lambda: [], active_units=lambda: []),
                state_dir=self.state,
                notifier=self.hub.notifier(self.state),
            ),
        )


@pytest.fixture
def run(origins: Origins, mirror: RepoMirror, tmp_path: Path) -> Run:
    return Run(origins, mirror, tmp_path)


def test_six_days_behind_raises_nothing(run: Run) -> None:
    installed = _commit(run.origins, {"src/rotation_worker/a.py": "1"}, 20)
    _commit(run.origins, {"src/rotation_worker/a.py": "2"}, 6)
    assert worker.behind(run.setup(installed), NOW) == ({}, set())


def test_eight_days_behind_raises_once(run: Run) -> None:
    installed = _commit(run.origins, {"src/rotation_worker/a.py": "1"}, 20)
    _commit(run.origins, {"src/alert_hub_client/b.py": "2"}, 8)
    _commit(run.origins, {"src/rotation_worker/a.py": "3"}, 1)
    acts, unread = worker.behind(run.setup(installed), NOW)
    key = behind_key("bws-secret")
    assert (list(acts), unread) == ([key], set())
    assert "2026-10-23" in acts[key].message  # the oldest change, not the newest


def test_a_change_outside_the_paths_is_not_behind(run: Run) -> None:
    installed = _commit(run.origins, {"src/rotation_worker/a.py": "1"}, 20)
    _commit(run.origins, {"docs/notes.md": "x", "src/orchestrator/m.py": "y"}, 10)
    assert worker.behind(run.setup(installed), NOW) == ({}, set())


def test_a_revision_the_mirror_lacks_holds_the_key(run: Run) -> None:
    _commit(run.origins, {"src/rotation_worker/a.py": "1"}, 20)
    acts, unread = worker.behind(run.setup("0123456789ab"), NOW)
    assert (acts, unread) == ({}, {behind_key("bws-secret")})


def test_a_mirror_that_cannot_fetch_holds_the_key(
    run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed = _commit(run.origins, {"src/rotation_worker/a.py": "1"}, 20)

    def down(_repository: str) -> str:
        raise MirrorError("git didn't answer")

    monkeypatch.setattr(run.mirror, "sync", down)
    assert worker.behind(run.setup(installed), NOW)[1] == {behind_key("bws-secret")}


def test_a_revision_that_is_not_hex_is_refused_before_git_runs(run: Run) -> None:
    _commit(run.origins, {"src/rotation_worker/a.py": "1"}, 20)
    with pytest.raises(MirrorError, match="not a revision"):
        run.mirror.oldest_change_since(REPOSITORY, "--output=x", ("src",))


def test_the_alert_rides_the_pass_s_one_sync(run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    installed = _commit(run.origins, {"src/rotation_worker/a.py": "1"}, 20)
    _commit(run.origins, {"src/rotation_worker/a.py": "2"}, 8)
    monkeypatch.setattr(worker, "_now", lambda: NOW)
    setup = run.setup(installed)
    worker.run_pass(setup)
    assert [(m["key"], m["status"]) for m in run.hub.sent] == [(behind_key("bws-secret"), "firing")]


@pytest.mark.parametrize("breaks", ["sync", "oldest_change_since"])
def test_a_behind_failure_of_any_kind_still_lets_unit_alerts_out(
    run: Run, monkeypatch: pytest.MonkeyPatch, breaks: str
) -> None:
    def broken(*_arguments: object) -> None:
        raise ValueError("not a timestamp")

    failed = Act("rotation-unit-failed:u1", "A rotation unit failed", "why")
    monkeypatch.setattr(run.mirror, breaks, broken)
    monkeypatch.setattr(worker, "_now", lambda: NOW)
    monkeypatch.setattr(worker, "unit_alerts", lambda _setup: ({failed.key: failed}, set()))
    worker.run_pass(run.setup("0123456789ab"))
    assert [(m["key"], m["status"]) for m in run.hub.sent] == [(failed.key, "firing")]
