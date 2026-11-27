"""What a GitHub App key reader's green ping proves, per launcher (ADR-0055 amendment 35 items 5
and 15).

The executor counts a `runtime-fetch` reader switched when its Healthchecks check reports a
successful run that began after the keeper's revision date, and the revoke alert waits on that.
A green ping proves a mint with the new key only from a pass that reached the key's read, and
whether a pass that ends before it pings success differs by launcher (`scripts/CLAUDE.md`: exit
codes 2 and 3 aren't shared). So each reader below is pinned from its launcher's text:

* `success`: the exit codes that ping success. A dead-man launcher's are 0 and its `--finding`
  code (`sds_deadman_finish`); an outcome-alarm launcher's are 0 and its `--told` code
  (`sds_outcome_end`). Anything else pings `/fail` or nothing.
* `early`: each `exit` between arming the ping and the key's read, as written. A literal code
  there never pings success (`test_no_literal_exit_before_the_read_pings_success`). A code passed
  through from a program (`"$scope_rc"`) pings success only if the program exits with the lane's
  finding code: each is named here, so a change to one shows.
* `guard`: the line opening the block the read sits in. A scheduled pass meets every guard
  (`test_every_scheduled_pass_meets_its_read_s_guard`), so a scheduled green ping is a pass that
  reached the read, except through a pass-through code. An outcome-alarm launcher's bare `exit`
  sends no ping at all, and `sds_outcome_fail` pings `/fail`, so it has no early success.

No reader keeps a value between passes (amendment 35 item 5), so a green ping that proves less
leaves no reader on the old key; it only weakens the revoke alert's timing.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest

ROOT = Path(__file__).resolve().parents[2]
CLASS = "github-app-private-key"
# A statement's `exit`, not the word inside a message.
EXIT = re.compile(r"(?:^\s*|\|\|\s*|&&\s*|;\s*|then\s+)exit\s+(\S+)")
SUBMIT = "--submit"


@dataclass(frozen=True)
class Reader:
    success: frozenset[int]
    early: tuple[str, ...]
    guard: str | None


IN_CONTAINER = "if sds_in_lane_container; then"
DEADMAN_2 = frozenset({0, 2})
DEADMAN_3 = frozenset({0, 3})
OUTCOME = frozenset({0})

# By (credential, launcher). Written out, not derived, so a launcher change shows here.
READERS: Final[dict[tuple[str, str], Reader]] = {
    ("lanes-read-app-private-key", "run-landing-ledger.sh"): Reader(
        DEADMAN_2, ("1", '"$scope_rc"', "3"), None
    ),
    ("lanes-read-app-private-key", "run-deploy-watcher.sh"): Reader(
        DEADMAN_2, ("1", "1", '"$derive_rc"', "3"), None
    ),
    ("lanes-read-app-private-key", "run-revision-watcher.sh"): Reader(
        DEADMAN_2, ("1", "1", "1", "1", '"$scope_rc"', "3"), None
    ),
    # The read sits in the `else` of a derivation that failed; that pass runs on with no token
    # and reports 3, which pings `/fail` (`test_the_unit_verifier_without_its_token_reports_3`).
    ("lanes-read-app-private-key", "run-unit-verifier.sh"): Reader(DEADMAN_2, ("1",), "else"),
    ("lanes-read-app-private-key", "run-pin-watcher.sh"): Reader(
        DEADMAN_2, ("1", "1", '"$scope_rc"', "3"), None
    ),
    ("lanes-read-app-private-key", "run-membership-check.sh"): Reader(OUTCOME, (), IN_CONTAINER),
    ("lanes-read-app-private-key", "run-change-proposer.sh"): Reader(OUTCOME, (), IN_CONTAINER),
    ("lanes-read-app-private-key", "run-work-carrier.sh"): Reader(OUTCOME, (), None),
    ("lanes-read-app-private-key", "run-bump-proposer.sh"): Reader(OUTCOME, (), IN_CONTAINER),
    # An empty repository list exits 2, which pings `/fail`: its finding code is 3.
    ("lanes-read-app-private-key", "run-inert-landing.sh"): Reader(
        DEADMAN_3, ("1", "1", '"$derive_rc"', "2"), IN_CONTAINER
    ),
    ("lanes-read-app-private-key", "run-estate-landing.sh"): Reader(
        DEADMAN_3, ("1", "1", '"$derive_rc"', "2"), None
    ),
    ("lanes-read-app-private-key", "run-bump-driver.sh"): Reader(
        frozenset({0, 2}), (), IN_CONTAINER
    ),
    ("lanes-push-app-private-key", "run-bump-proposer.sh"): Reader(
        OUTCOME, (), 'if [ "$SUBMITTING" -eq 1 ]; then'
    ),
    # Pings every pass, and reads the push key only on `--submit`: a bare pass run by hand pings
    # success without reading it. The scheduled pass submits.
    ("lanes-push-app-private-key", "run-rotation-proposer.sh"): Reader(
        DEADMAN_3,
        ("2", "2", "1", "1", "1", "1", "1", '"$registries_rc"', "1", "1", "1"),
        'if [ "$NEEDS_CREDENTIAL" -eq 1 ]; then',
    ),
}
# The guards a scheduled pass meets only by its crontab's `--submit`.
SUBMIT_GUARDS: Final = frozenset(
    {'if [ "$SUBMITTING" -eq 1 ]; then', 'if [ "$NEEDS_CREDENTIAL" -eq 1 ]; then'}
)


def _readers() -> list[tuple[str, str, str, str]]:
    """Each row's `runtime-fetch` reader: credential, keeper UUID, launcher and check."""
    registry = tomllib.loads((ROOT / ".cred-consumers.toml").read_text())
    return [
        (row["id"], row["bws_uuid"], consumer["ref"], consumer["healthchecks_check"])
        for row in registry["credential"]
        if row.get("class") == CLASS
        for consumer in row["consumer"]
        if consumer["kind"] == "runtime-fetch"
    ]


def _statements(launcher: str) -> list[tuple[int, str]]:
    text = (ROOT / launcher).read_text().splitlines()
    return [
        (number, line)
        for number, line in enumerate(text, 1)
        if line.strip() and not line.lstrip().startswith("#")
    ]


def derive(uuid: str, launcher: str, check: str) -> Reader:
    """The reader's pings and exits, read from its launcher's text."""
    lines = _statements(launcher)
    (variable,) = [
        line.split("=", 1)[0] for _, line in lines if line.strip().endswith(f'="{uuid}"')
    ]
    read, read_line = next((n, line) for n, line in lines if f'sds_bws_value "${variable}"' in line)
    indent = len(read_line) - len(read_line.lstrip())
    guard = next(
        (
            line.strip()
            for n, line in reversed(lines)
            if n < read and len(line) - len(line.lstrip()) < indent
        ),
        None,
    )
    armed = [(n, line) for n, line in lines if line.startswith("sds_deadman_arm ")]
    if armed:
        start, arm = armed[0]
        assert arm.split()[1] == check
        finding = re.search(r"--finding (\d+)", arm)
        success = frozenset({0, int(finding.group(1))} if finding else {0})
        early = tuple(
            found.group(1)
            for n, line in lines
            if start < n < read and (found := EXIT.search(line)) is not None
        )
    else:
        start = next(n for n, line in lines if line.startswith("sds_outcome_begin "))
        assert any(line.strip() == f'CHECK_NAME="{check}"' for _, line in lines)
        ends = " ".join(line for _, line in lines if line.lstrip().startswith("sds_outcome_end "))
        told = re.search(r"--told (\d+)", ends)
        success = frozenset({0, int(told.group(1))} if told else {0})
        assert not any(
            line.lstrip().startswith("sds_outcome_end ") for n, line in lines if n < read
        )
        # A bare `exit` before the end sends no ping, and `sds_outcome_fail` pings `/fail`.
        early = ()
    return Reader(success, early, guard)


def _key(credential: str, launcher: str) -> tuple[str, str]:
    return credential, Path(launcher).name


def test_every_reader_is_pinned() -> None:
    assert {_key(c, launcher) for c, _, launcher, _ in _readers()} == set(READERS)


@pytest.mark.parametrize(
    ("credential", "uuid", "launcher", "check"), _readers(), ids=lambda v: str(v)[:40]
)
def test_each_reader_s_pings_and_exits_before_the_read_are_as_pinned(
    credential: str, uuid: str, launcher: str, check: str
) -> None:
    assert derive(uuid, launcher, check) == READERS[_key(credential, launcher)]


@pytest.mark.parametrize(("credential", "launcher"), sorted(READERS))
def test_no_literal_exit_before_the_read_pings_success(credential: str, launcher: str) -> None:
    reader = READERS[(credential, launcher)]
    literal = {int(code) for code in reader.early if code.isdigit()}
    assert not literal & reader.success


@pytest.mark.parametrize(("credential", "launcher"), sorted(READERS))
def test_every_scheduled_pass_meets_its_read_s_guard(credential: str, launcher: str) -> None:
    reader = READERS[(credential, launcher)]
    lane = launcher.removeprefix("run-").removesuffix(".sh")
    crontab = (ROOT / "lanes" / lane / "crontab").read_text().splitlines()
    scheduled = [line for line in crontab if line.strip() and not line.startswith("#")]
    assert scheduled
    for line in scheduled:
        assert f"lane-run {lane}" in line
        if reader.guard in SUBMIT_GUARDS:
            assert SUBMIT in line.split()


def test_the_unit_verifier_without_its_token_reports_3() -> None:
    """Its read sits in an `else`; a pass that skips it reports 3, which pings `/fail`."""
    text = (ROOT / "scripts/run-unit-verifier.sh").read_text()
    assert (
        'if [ "$GITHUB_TOKEN_MISSING" -eq 1 ] && { [ "$rc" -eq 0 ] || [ "$rc" -eq 2 ]; }; then\n'
        "  rc=3\n"
    ) in text
    assert 3 not in READERS[("lanes-read-app-private-key", "run-unit-verifier.sh")].success


def test_control_a_read_moved_after_an_early_success_exit_is_seen(tmp_path: Path) -> None:
    """The derivation sees an `exit 0` placed before the read, and skips `exit` in a message."""
    launcher = tmp_path / "run-x.sh"
    launcher.write_text(
        'KEY_UUID="u-1"\n'
        'sds_deadman_arm sds-x --finding 2 "$@"\n'
        'echo "it failed (exit $rc)" >&2\n'
        '[ -n "$x" ] || exit 0\n'
        'TOKEN="$(sds_bws_value "$KEY_UUID" | mint)"\n'
    )
    reader = derive("u-1", str(launcher), "sds-x")
    assert reader == Reader(frozenset({0, 2}), ("0",), None)
    assert {int(code) for code in reader.early} & reader.success
