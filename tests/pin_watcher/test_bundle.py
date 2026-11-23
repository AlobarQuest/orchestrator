"""change-window's infraops pin: unequal fires, equal resolves, unread holds, on its own key."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from pin_watcher import alerts, bundle
from tests.pin_watcher.conftest import (
    BUNDLE,
    BUNDLE_COMMIT,
    BUNDLE_DIGEST,
    BUNDLE_MAIN_PATH,
    RECOMMENDED,
    Estate,
    Hub,
    Raw,
    behind,
    dockerfile,
    identical,
)
from tests.pin_watcher.test_cli import _run as _invoke

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CALLER_KEY = alerts.repository_key("o/b")
PIN = "b" * 40


@pytest.fixture(autouse=True)
def _environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIN_WATCHER_GITHUB_TOKEN", "gh")
    monkeypatch.setenv("ORCHESTRATOR_API_URL", "https://sds.example.net")
    monkeypatch.setenv("ORCHESTRATOR_API_CREDENTIAL_KEY_ID", "orchestrator-observer")
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "bearer")


def _run(monkeypatch, estate: Estate, orchestrator, *args: str):
    with monkeypatch.context() as patch:
        return _invoke(patch, estate, orchestrator, *args)


def _current() -> Estate:
    return Estate(callers={"o/a": RECOMMENDED}, comparisons={RECOMMENDED: identical()})


def _drifted() -> Estate:
    return Estate(
        callers={"o/a": RECOMMENDED, "o/b": PIN},
        comparisons={RECOMMENDED: identical(), PIN: behind(3)},
        dates={PIN: "2026-08-20T09:00:00Z"},
    )


def _bundle_remembered(hub: Hub) -> set[str]:
    path = hub.state / alerts.BUNDLE_ALERTS_FILE
    return set(json.loads(path.read_text())) if path.exists() else set()


def _resolved_titles(hub: Hub) -> dict[str, str]:
    return {body["key"]: body["title"] for body in hub.sent if body["status"] == "resolved"}


# --- parsing ------------------------------------------------------------------------------------


def test_the_tracked_dockerfile_parses() -> None:
    """A reformat of `lanes/Dockerfile`'s pin lines fails here, not in the live pass."""
    pin = bundle.parse_dockerfile((REPOSITORY_ROOT / "lanes" / "Dockerfile").read_text())
    assert len(pin.commit) == 40
    assert len(pin.digest) == 64
    assert pin.main_path.endswith("/main/dist/bundle/change-mgr-cli.js")
    assert pin.commit not in pin.main_path


def test_the_main_url_is_the_add_line_with_main_for_the_commit() -> None:
    pin = bundle.parse_dockerfile(dockerfile())
    assert pin == bundle.Pin(BUNDLE_COMMIT, BUNDLE_DIGEST, BUNDLE_MAIN_PATH)


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        (dockerfile().replace("ARG INFRAOPS_COMMIT=", "ARG OTHER="), "0 INFRAOPS_COMMIT"),
        (dockerfile() + f"ARG INFRAOPS_COMMIT={'e' * 40}\n", "2 INFRAOPS_COMMIT"),
        (dockerfile().replace("${INFRAOPS_COMMIT}", "main"), "0 pinned bundle"),
        (dockerfile() + dockerfile().split("\n", 1)[1], "2 pinned bundle"),
        (dockerfile().replace("raw.githubusercontent.com", "example.net"), "not on"),
        (dockerfile().replace("sha256:", "sha512:"), "0 pinned bundle"),
    ],
)
def test_a_dockerfile_that_does_not_parse_is_unreadable(text: str, reason: str) -> None:
    with pytest.raises(bundle.BundleUnreadable, match=reason):
        bundle.parse_dockerfile(text)


# --- reading ------------------------------------------------------------------------------------


def test_equal_digests_are_current_and_carry_no_credential(raw: Raw) -> None:
    reading = bundle.read()
    assert not reading.stale
    assert [request.url.path for request in raw.requests] == [
        bundle.DOCKERFILE_PATH,
        BUNDLE_MAIN_PATH,
    ]
    assert all("authorization" not in request.headers for request in raw.requests)


def test_a_changed_bundle_at_main_is_stale(raw: Raw) -> None:
    raw.bundle = b"a newer build"
    assert bundle.read().stale


@pytest.mark.parametrize("path", [bundle.DOCKERFILE_PATH, BUNDLE_MAIN_PATH])
def test_a_non_200_is_unreadable_and_names_the_url_and_status(raw: Raw, path: str) -> None:
    raw.statuses[path] = 503
    with pytest.raises(bundle.BundleUnreadable, match=rf"{bundle.RAW}{path} answered 503"):
        bundle.read()


def test_no_answer_is_unreadable(raw: Raw) -> None:
    raw.unanswered = True
    with pytest.raises(bundle.BundleUnreadable, match="unreachable: ConnectError"):
        bundle.read()


def test_a_dockerfile_that_is_not_utf8_is_unreadable(raw: Raw) -> None:
    raw.dockerfile = b"\xff\xfe"
    with pytest.raises(bundle.BundleUnreadable):
        bundle.read()


# --- the pass -----------------------------------------------------------------------------------


def test_unequal_fires_and_a_later_equal_resolves(monkeypatch, orchestrator, hub: Hub, raw: Raw):
    raw.bundle = b"a newer build"
    first = _run(monkeypatch, _current(), orchestrator)
    assert first.exit_code == 0, first.output
    assert hub.keys("firing") == [bundle.KEY]
    assert _bundle_remembered(hub) == {bundle.KEY}

    raw.bundle = BUNDLE
    second = _run(monkeypatch, _current(), orchestrator)
    assert second.exit_code == 0, second.output
    assert hub.keys("resolved") == [bundle.KEY]
    assert _bundle_remembered(hub) == set()


def test_equal_fires_nothing(monkeypatch, orchestrator, hub: Hub) -> None:
    assert _run(monkeypatch, _current(), orchestrator).exit_code == 0
    assert hub.sent == []


@pytest.mark.parametrize("failure", ["dockerfile-status", "bundle-status", "unanswered", "parse"])
def test_unread_holds_the_key_and_changes_no_exit_code(
    monkeypatch, orchestrator, hub: Hub, raw: Raw, failure: str
) -> None:
    raw.bundle = b"a newer build"
    _run(monkeypatch, _current(), orchestrator)
    raw.bundle = BUNDLE
    if failure == "dockerfile-status":
        raw.statuses[bundle.DOCKERFILE_PATH] = 500
    elif failure == "bundle-status":
        raw.statuses[BUNDLE_MAIN_PATH] = 404
    elif failure == "unanswered":
        raw.unanswered = True
    else:
        raw.dockerfile = "FROM scratch\n"

    result = _run(monkeypatch, _current(), orchestrator)

    assert result.exit_code == 0, result.output
    assert "UNREAD: change-window's infraops pin" in result.output
    assert hub.keys("resolved") == []
    assert _bundle_remembered(hub) == {bundle.KEY}


def test_a_caller_pass_never_resolves_the_bundle_key(
    monkeypatch, orchestrator, hub: Hub, raw: Raw
) -> None:
    raw.bundle = b"a newer build"
    assert _run(monkeypatch, _drifted(), orchestrator).exit_code == 2
    assert hub.keys("firing") == sorted([bundle.KEY, CALLER_KEY])

    assert _run(monkeypatch, _current(), orchestrator).exit_code == 0

    assert hub.keys("resolved") == [CALLER_KEY]
    assert _bundle_remembered(hub) == {bundle.KEY}
    assert hub.remembered() == set()


def test_a_bundle_resolution_never_resolves_a_caller_key(
    monkeypatch, orchestrator, hub: Hub, raw: Raw
) -> None:
    raw.bundle = b"a newer build"
    _run(monkeypatch, _drifted(), orchestrator)
    raw.bundle = BUNDLE

    assert _run(monkeypatch, _drifted(), orchestrator).exit_code == 2

    assert _resolved_titles(hub) == {
        bundle.KEY: "change-window's infraops pin matches infraops main"
    }
    assert hub.remembered() == {CALLER_KEY}
    assert _bundle_remembered(hub) == set()


def test_each_pass_logs_both_digests(monkeypatch, orchestrator, raw: Raw) -> None:
    raw.bundle = b"a newer build"
    output = _run(monkeypatch, _current(), orchestrator).output
    pinned = BUNDLE_DIGEST[:12]
    main = hashlib.sha256(b"a newer build").hexdigest()[:12]
    assert f"infraops pin behind: pinned {pinned} (commit {BUNDLE_COMMIT[:12]}), main {main}" in (
        output
    )


def test_a_dry_run_prints_both_digests_and_sends_nothing(
    monkeypatch, orchestrator, hub: Hub, raw: Raw
) -> None:
    monkeypatch.delenv(alerts.TOKEN_ENV)
    raw.bundle = b"a newer build"
    result = _run(monkeypatch, _current(), orchestrator, "--dry-run")
    assert result.exit_code == 0, result.output
    assert f"pinned {BUNDLE_DIGEST[:12]}" in result.output
    assert hub.sent == []
    assert not (hub.state / alerts.BUNDLE_ALERTS_FILE).exists()


def test_a_failed_bundle_send_alone_is_incomplete(
    monkeypatch, orchestrator, hub: Hub, raw: Raw
) -> None:
    raw.bundle = b"a newer build"
    hub.status = 500
    result = _run(monkeypatch, _current(), orchestrator)
    assert result.exit_code == 3, result.output
    assert hub.keys("firing") == [bundle.KEY]


def test_a_failed_bundle_send_still_tells_the_callers(
    monkeypatch, orchestrator, hub: Hub, raw: Raw
) -> None:
    raw.bundle = b"a newer build"
    hub.status = 500
    assert _run(monkeypatch, _drifted(), orchestrator).exit_code == 3
    assert hub.keys("firing") == sorted([bundle.KEY, CALLER_KEY])


def test_the_alert_token_never_reaches_the_output(monkeypatch, orchestrator, raw: Raw) -> None:
    raw.bundle = b"a newer build"
    assert "alert-token" not in _run(monkeypatch, _current(), orchestrator).output
