"""Part 1a: the sweep's exit 2 reaches a person as keyed alerts, one state file per subcommand."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from activation_sweep import alerts
from activation_sweep.bind import UNSATISFIED
from activation_sweep.binding import content_digest
from activation_sweep.cli import (
    BINDING_TOKEN_VARIABLE,
    EXIT_FINDINGS,
    EXIT_INCOMPLETE,
    EXIT_OK,
    TOKEN_VARIABLE,
    app,
)
from tests.activation_sweep.conftest import Estate, Hub, git
from tests.activation_sweep.test_bind_cli import Binder, _row

runner = CliRunner()


class _Sink:
    def __enter__(self) -> _Sink:
        return self

    def __exit__(self, *_: object) -> None: ...

    def record_observation(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"id": "x"}


@pytest.fixture(autouse=True)
def _sweep_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("activation_sweep.cli.open_client", lambda **_: _Sink())
    monkeypatch.setenv(TOKEN_VARIABLE, "bearer")


def _sweep(*paths: Path | str, extra: tuple[str, ...] = ()) -> Any:
    args = ["sweep", *extra]
    for path in paths:
        args += ["--checkout", str(path)]
    return runner.invoke(app, args)


def _member(estate: Estate) -> str:
    return estate.local.name


def _key(condition: str, estate: Estate) -> str:
    return alerts.checkout_key(condition, _member(estate))


def test_a_behind_checkout_fires_then_resolves(estate: Estate, hub: Hub) -> None:
    estate.land_upstream()
    assert _sweep(estate.local).exit_code == EXIT_FINDINGS
    assert hub.keys("firing") == [_key("behind", estate)]
    assert hub.remembered(alerts.SWEEP_FILE) == {_key("behind", estate)}
    git(estate.local, "pull", "-q")
    hub.sent.clear()
    assert _sweep(estate.local).exit_code == EXIT_OK
    assert hub.keys("resolved") == [_key("behind", estate)]
    assert hub.remembered(alerts.SWEEP_FILE) == set()


def test_a_dirty_checkout_fires_then_resolves(estate: Estate, hub: Hub) -> None:
    estate.modify_tracked()
    assert _sweep(estate.local).exit_code == EXIT_FINDINGS
    assert hub.keys("firing") == [_key("dirty", estate)]
    estate.restore_tracked()
    hub.sent.clear()
    assert _sweep(estate.local).exit_code == EXIT_OK
    assert hub.keys("resolved") == [_key("dirty", estate)]


def test_a_parked_checkout_fires_then_resolves(estate: Estate, hub: Hub) -> None:
    estate.park()
    assert _sweep(estate.local).exit_code == EXIT_FINDINGS
    assert hub.keys("firing") == [_key("parked", estate)]
    estate.return_to_default()
    hub.sent.clear()
    assert _sweep(estate.local).exit_code == EXIT_OK
    assert hub.keys("resolved") == [_key("parked", estate)]


def test_a_standing_condition_is_told_once(estate: Estate, hub: Hub) -> None:
    estate.land_upstream()
    _sweep(estate.local)
    hub.sent.clear()
    _sweep(estate.local)
    assert hub.sent == []


def test_an_unavailable_checkout_leaves_its_key_under_the_spelling_it_fired_with(
    estate: Estate, hub: Hub, tmp_path: Path
) -> None:
    """The measured pass keys on the member (the directory name), not the remote URL; the pass that
    cannot measure that checkout must leave exactly that key alone."""
    estate.land_upstream()
    _sweep(estate.local)
    fired = _key("behind", estate)
    assert fired in hub.remembered(alerts.SWEEP_FILE)
    hub.sent.clear()
    git(estate.local, "remote", "set-url", "origin", "not a url")  # the checkout stops measuring
    git(estate.local, "config", "--unset", f"url.{estate.origin}.insteadOf")
    result = _sweep(estate.local)
    assert result.exit_code == EXIT_INCOMPLETE
    assert json.loads(result.stdout)[0]["unavailable"] is True
    assert hub.sent == []
    assert fired in hub.remembered(alerts.SWEEP_FILE)


def test_a_failed_send_exits_incomplete_after_the_work(estate: Estate, hub: Hub) -> None:
    estate.land_upstream()
    hub.status = 500
    result = _sweep(estate.local)
    assert result.exit_code == EXIT_INCOMPLETE
    assert json.loads(result.stdout)[0]["recorded"] is True


def test_an_unusable_state_directory_exits_incomplete(
    estate: Estate, hub: Hub, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setenv(alerts.STATE_DIR_ENV, str(blocker / "sub"))
    assert _sweep(estate.local).exit_code == EXIT_INCOMPLETE


def test_a_dry_run_sends_nothing_and_needs_no_alert_token(
    estate: Estate, hub: Hub, monkeypatch: pytest.MonkeyPatch
) -> None:
    estate.land_upstream()
    monkeypatch.delenv(alerts.TOKEN_ENV)
    result = _sweep(estate.local, extra=("--dry-run",))
    assert result.exit_code == EXIT_FINDINGS
    assert hub.sent == []
    assert not hub.state.exists()


def test_an_acting_pass_without_the_alert_token_is_the_tool_failing(
    estate: Estate, hub: Hub, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(alerts.TOKEN_ENV)
    assert _sweep(estate.local).exit_code == 1


# ---- bind ----------------------------------------------------------------------------------------


def _bind(
    estate: Estate, monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, Any]], *args: str
) -> Any:
    binder = Binder(rows)

    class Client(_Sink):
        def candidates(self, repository: str) -> list[dict[str, Any]]:
            return binder.candidates(repository)

        def bind(self, work_unit_id: str, payload: dict[str, Any]) -> dict[str, Any]:
            return binder.bind(work_unit_id, payload)

        def observe(self, binding_id: str, payload: dict[str, Any]) -> dict[str, Any]:
            return binder.observe(binding_id, payload)

    monkeypatch.setattr("activation_sweep.cli.open_binding_client", lambda **_: Client())
    monkeypatch.setenv(BINDING_TOKEN_VARIABLE, "system")
    return runner.invoke(app, ["bind", "--no-fetch", "--checkout", str(estate.local), *args])


def _unsatisfied_row(estate: Estate) -> dict[str, Any]:
    row = _row("c" * 40, binding_id="binding-1")
    row["binding_artifact_digest"] = content_digest(estate.local)
    return row


def _bind_key(estate: Estate) -> str:
    return alerts.alert_key(alerts.ACTIVATION_INCOMPLETE, _member(estate))


def test_an_unsatisfied_activation_fires_then_resolves(
    estate: Estate, hub: Hub, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _bind(estate, monkeypatch, [_unsatisfied_row(estate)])
    assert result.exit_code == EXIT_FINDINGS
    assert json.loads(result.stdout)[0]["units"][0]["activation"]["outcome"] == UNSATISFIED
    assert hub.keys("firing") == [_bind_key(estate)]
    hub.sent.clear()
    result = _bind(estate, monkeypatch, [])
    assert result.exit_code == EXIT_OK
    assert hub.keys("resolved") == [_bind_key(estate)]


def test_a_superseded_and_a_waiting_unit_fire_nothing(
    estate: Estate, hub: Hub, monkeypatch: pytest.MonkeyPatch
) -> None:
    superseded = _row("c" * 40, binding_id="binding-1")
    superseded["binding_artifact_digest"] = "sha256:" + "e" * 64
    waiting = _row("d" * 40)
    assert _bind(estate, monkeypatch, [superseded, waiting]).exit_code == EXIT_OK
    assert hub.sent == []


def test_a_member_with_an_unanswered_unit_is_left_unread(
    estate: Estate, hub: Hub, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bind(estate, monkeypatch, [_unsatisfied_row(estate)])
    hub.sent.clear()
    unanswered = _row("c" * 40, binding_id="binding-2")
    unanswered["binding_artifact_digest"] = None  # a narrowed contract: no answer
    result = _bind(estate, monkeypatch, [unanswered])
    assert result.exit_code == EXIT_INCOMPLETE
    assert hub.sent == []
    assert hub.remembered(alerts.BIND_FILE) == {_bind_key(estate)}


def test_a_failed_bind_send_exits_incomplete_after_the_work(
    estate: Estate, hub: Hub, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub.status = 500
    assert _bind(estate, monkeypatch, [_unsatisfied_row(estate)]).exit_code == EXIT_INCOMPLETE


def test_a_bind_dry_run_sends_nothing_and_needs_no_alert_token(
    estate: Estate, hub: Hub, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(alerts.TOKEN_ENV)
    result = _bind(estate, monkeypatch, [_unsatisfied_row(estate)], "--dry-run")
    assert result.exit_code == EXIT_FINDINGS
    assert hub.sent == []


# ---- the two subcommands share a launcher, never a state file ------------------------------------


def test_one_subcommands_sync_never_resolves_the_others_keys(
    estate: Estate, hub: Hub, monkeypatch: pytest.MonkeyPatch
) -> None:
    estate.land_upstream()
    _sweep(estate.local)  # sweep-alerts: checkout-behind
    _bind(estate, monkeypatch, [_unsatisfied_row(estate)])  # bind-alerts: activation-incomplete
    hub.sent.clear()
    # A clean bind pass resolves its own key only; the sweep's behind key is still standing.
    _bind(estate, monkeypatch, [])
    assert hub.keys("resolved") == [_bind_key(estate)]
    assert hub.remembered(alerts.SWEEP_FILE) == {_key("behind", estate)}
    hub.sent.clear()
    # And a clean sweep resolves only its own.
    _bind(estate, monkeypatch, [_unsatisfied_row(estate)])
    hub.sent.clear()
    git(estate.local, "pull", "-q")
    _sweep(estate.local)
    assert hub.keys("resolved") == [_key("behind", estate)]
    assert hub.remembered(alerts.BIND_FILE) == {_bind_key(estate)}
