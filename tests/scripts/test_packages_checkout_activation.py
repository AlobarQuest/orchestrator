"""The lanes that read the intent-packages checkout activate it first (ADR-0031 amendment 1).

`run-work-carrier.sh`, `run-bump-proposer.sh` and `run-rotation-proposer.sh` each read that
checkout, and nothing else fast-forwards it. Each launcher runs from a throwaway tree with fake
programs and `curl`, `bws` and `gh` stubs on PATH; both BWS identities are preset, so the Keychain
is never read and nothing is sent anywhere. HOME is a temporary directory, so the helper the
launchers source is whatever a test puts at `$HOME/.claude/bin/activate-checkout.sh`.

Two layers. The stand-in records its arguments, and runs everywhere: it holds what this repository
owns, which is that each launcher names the directory its program reads, with one argument, before
the program, and only on an acting run. The real helper (copied from the deployed path) holds the
outcomes on real git checkouts; CI has no deployed helper, so those cases skip there, and this
repository keeps no copy of it (ADR-0031 §5: a second copy is a defect).
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

DEPLOYED_HELPER = Path.home() / ".claude" / "bin" / "activate-checkout.sh"

_CURL = """#!/bin/sh
case "$*" in
    *healthcheck.alobar.net/api/v3/checks/*) printf '%s' '{"checks": []}'; exit 0 ;;
esac
exit 1
"""
_BWS = """#!/bin/sh
printf '{"value": "not-a-real-key"}'
"""
_GH = "#!/bin/sh\necho not-a-real-gh-token\n"
# Records the packages checkout's HEAD at the moment the program runs, which is what it would read.
_PROGRAM = """#!/bin/sh
echo "program {name} $(git -C "$PACKAGES" rev-parse HEAD 2>/dev/null)" >> "$EVENTS"
exit 0
"""
_STAND_IN = """activate_checkout() {
    echo "activate $# $*" >> "$EVENTS"
    echo "[activation] stand-in"
}
"""


@dataclass(frozen=True)
class Lane:
    launcher: str
    flag: str
    programs: tuple[str, ...]
    helpers: tuple[str, ...]

    def checkout_env(self, checkout: Path) -> dict[str, str]:
        """The variable this lane's program reads the packages checkout from, set to `checkout`."""
        if self.launcher == "run-work-carrier.sh":
            assert checkout.name == "intent-packages"
            return {"WORK_CARRIER_CHECKOUT_ROOT": str(checkout.parent)}
        return {"BUMP_PROPOSER_PACKAGES_CHECKOUT": str(checkout)}


LANES = [
    Lane(
        "run-work-carrier.sh",
        "--register",
        ("work-watcher", "work-carrier"),
        ("sds-deadman.sh", "sds-bws.sh"),
    ),
    Lane("run-bump-proposer.sh", "--submit", ("bump-proposer",), ("sds-deadman.sh", "sds-bws.sh")),
    Lane(
        "run-rotation-proposer.sh",
        "--submit",
        ("rotation-proposer",),
        ("sds-install.sh", "sds-deadman.sh", "sds-bws.sh"),
    ),
]
ids = [lane.launcher for lane in LANES]


def _executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


def _commit(repo: Path, name: str) -> str:
    (repo / name).write_text(f"{name}\n")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", name)
    return _git(repo, "rev-parse", "HEAD")


def _behind_checkout(tmp_path: Path, checkout: Path) -> tuple[str, str]:
    """`checkout` on `main`, one commit behind its origin: its HEAD, and the commit it lacks."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    seed = tmp_path / "seed"
    _git(tmp_path, "clone", "-q", str(origin), str(seed))
    _git(seed, "checkout", "-q", "-b", "main")
    _commit(seed, "first")
    _git(seed, "push", "-q", "origin", "main")
    checkout.parent.mkdir(parents=True, exist_ok=True)
    _git(tmp_path, "clone", "-q", "-b", "main", str(origin), str(checkout))
    head = _git(checkout, "rev-parse", "HEAD")
    landed = _commit(seed, "second")
    _git(seed, "push", "-q", "origin", "main")
    return head, landed


def _run(
    tmp_path: Path,
    lane: Lane,
    *args: str,
    helper: str | None,
    checkout: Path,
    point_at: bool = True,
    **env: str,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run `lane`'s launcher; its programs record `checkout`'s HEAD. `point_at=False` leaves the
    lane's checkout variable unset, so the launcher falls back to its default."""
    if point_at:
        env = {**lane.checkout_env(checkout), **env}
    root = tmp_path / "tree"
    (root / "scripts").mkdir(parents=True)
    for name in (lane.launcher, *lane.helpers):
        shutil.copy(Path("scripts") / name, root / "scripts" / name)
    venv = root / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").symlink_to((Path.cwd() / ".venv" / "bin" / "python").resolve())
    for program in lane.programs:
        _executable(venv / program, _PROGRAM.format(name=program))
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    _executable(stubs / "curl", _CURL)
    _executable(stubs / "bws", _BWS)
    _executable(stubs / "gh", _GH)
    home = tmp_path / "home"
    (home / ".claude" / "bin").mkdir(parents=True, exist_ok=True)
    if helper is not None:
        (home / ".claude" / "bin" / "activate-checkout.sh").write_text(helper)
    events = tmp_path / "events.txt"
    events.write_text("")
    result = subprocess.run(
        ["/bin/bash", str(root / "scripts" / lane.launcher), *args],
        env={
            "PATH": f"{stubs}:/usr/bin:/bin",
            "HOME": str(home),
            "BWS_ACCESS_TOKEN_BROAD": "not-a-real-identity",
            "BWS_ACCESS_TOKEN_SDS": "not-a-real-identity",
            "EVENTS": str(events),
            "PACKAGES": str(checkout),
            **env,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    return result, events.read_text().splitlines()


def _real_helper() -> str:
    if not DEPLOYED_HELPER.is_file():
        pytest.skip(f"no deployed activation helper at {DEPLOYED_HELPER}")
    return DEPLOYED_HELPER.read_text()


# The stand-in: what each launcher asks for.


@pytest.mark.parametrize("lane", LANES, ids=ids)
def test_an_acting_run_activates_the_directory_its_program_reads_before_the_program(
    tmp_path: Path, lane: Lane
) -> None:
    checkout = tmp_path / "elsewhere" / "intent-packages"
    result, events = _run(tmp_path, lane, lane.flag, helper=_STAND_IN, checkout=checkout)
    assert result.returncode == 0, result.stdout + result.stderr
    # One-argument calls only: the carrier also activates its own tree, with more arguments.
    activations = [line for line in events if line.startswith("activate 1 ")]
    assert activations == [f"activate 1 {checkout}"]
    assert events.index(activations[0]) < events.index(
        next(line for line in events if line.startswith("program "))
    )


@pytest.mark.parametrize("lane", LANES, ids=ids)
def test_with_no_variable_set_it_activates_the_default_checkout(tmp_path: Path, lane: Lane) -> None:
    default = tmp_path / "home" / "Projects" / "intent-packages"
    _, events = _run(tmp_path, lane, lane.flag, helper=_STAND_IN, checkout=default, point_at=False)
    assert [line for line in events if line.startswith("activate 1 ")] == [f"activate 1 {default}"]


def test_the_carrier_expands_a_tilde_checkout_root_as_its_program_does(tmp_path: Path) -> None:
    """`work-carrier` `expanduser`s `--checkout-root`; the launcher must name the same place."""
    default = tmp_path / "home" / "Projects" / "intent-packages"
    _, events = _run(
        tmp_path,
        LANES[0],
        "--register",
        helper=_STAND_IN,
        checkout=default,
        point_at=False,
        WORK_CARRIER_CHECKOUT_ROOT="~/Projects",
    )
    assert [line for line in events if line.startswith("activate 1 ")] == [f"activate 1 {default}"]


@pytest.mark.parametrize("lane", LANES, ids=ids)
def test_a_dry_run_asks_for_no_activation_of_the_checkout(tmp_path: Path, lane: Lane) -> None:
    checkout = tmp_path / "elsewhere" / "intent-packages"
    result, events = _run(tmp_path, lane, helper=_STAND_IN, checkout=checkout)
    assert result.returncode == 0, result.stdout + result.stderr
    # The carrier's own tree is still activated on a bare run, with two or more arguments.
    assert not [line for line in events if line.startswith("activate 1 ")]
    assert "as it is" in result.stdout
    assert any(line.startswith("program ") for line in events)


@pytest.mark.parametrize("lane", LANES, ids=ids)
def test_a_missing_helper_says_so_and_the_run_continues(tmp_path: Path, lane: Lane) -> None:
    checkout = tmp_path / "elsewhere" / "intent-packages"
    result, events = _run(tmp_path, lane, lane.flag, helper=None, checkout=checkout)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "helper missing" in result.stdout
    assert any(line.startswith("program ") for line in events)


# The real helper: what happens to the checkout.


@pytest.mark.parametrize("lane", LANES, ids=ids)
def test_a_behind_checkout_is_fast_forwarded_before_the_program_reads_it(
    tmp_path: Path, lane: Lane
) -> None:
    helper = _real_helper()
    checkout = tmp_path / "packages" / "intent-packages"
    _, landed = _behind_checkout(tmp_path, checkout)
    result, events = _run(tmp_path, lane, lane.flag, helper=helper, checkout=checkout)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "activated" in result.stdout
    assert f"program {lane.programs[-1]} {landed}" in events


@pytest.mark.parametrize("lane", LANES, ids=ids)
def test_a_checkout_that_cannot_fast_forward_is_held_and_the_run_continues(
    tmp_path: Path, lane: Lane
) -> None:
    """Ahead AND behind: an unpublished commit on a behind `main`. Ahead alone reads `current`."""
    helper = _real_helper()
    checkout = tmp_path / "packages" / "intent-packages"
    _behind_checkout(tmp_path, checkout)
    unpublished = _commit(checkout, "unpublished")
    result, events = _run(tmp_path, lane, lane.flag, helper=helper, checkout=checkout)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "HELD" in result.stdout
    assert f"program {lane.programs[-1]} {unpublished}" in events


@pytest.mark.parametrize("lane", LANES, ids=ids)
def test_a_checkout_off_main_is_left_where_it_is_and_the_run_continues(
    tmp_path: Path, lane: Lane
) -> None:
    helper = _real_helper()
    checkout = tmp_path / "packages" / "intent-packages"
    head, _ = _behind_checkout(tmp_path, checkout)
    _git(checkout, "checkout", "-q", "-b", "side")
    result, events = _run(tmp_path, lane, lane.flag, helper=helper, checkout=checkout)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "is on 'side', not main" in result.stdout
    assert f"program {lane.programs[-1]} {head}" in events
    assert _git(checkout, "rev-parse", "main") == head


@pytest.mark.parametrize("lane", LANES, ids=ids)
def test_a_dry_run_leaves_a_behind_checkout_behind(tmp_path: Path, lane: Lane) -> None:
    helper = _real_helper()
    checkout = tmp_path / "packages" / "intent-packages"
    head, _ = _behind_checkout(tmp_path, checkout)
    result, events = _run(tmp_path, lane, helper=helper, checkout=checkout)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"program {lane.programs[-1]} {head}" in events
    assert _git(checkout, "rev-parse", "HEAD") == head
