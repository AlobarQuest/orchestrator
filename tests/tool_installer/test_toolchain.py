"""Finding cargo, reading cargo's record, and capturing an install completely enough to undo it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tool_installer.toolchain import (
    CRATES_JSON,
    CRATES_TOML,
    ToolchainError,
    backup,
    find_rustup,
    install_artifacts,
    read_installed,
    remove_uncaptured,
    restore,
)

# The line cargo actually wrote for the installed rtk on this machine, 2026-09-06. Kept verbatim
# rather than paraphrased: this parser's whole job is to read what cargo writes, and a fixture
# reworded to suit the parser would test the parser against itself.
REAL_LINE = (
    '"rtk 0.48.0 (git+https://github.com/AlobarQuest/rtk?branch=main'
    '#4be91ebe1fc4eaf73688e28cd62782eadb355379)" = ["rtk"]'
)


def _root(tmp_path: Path, line: str | None = REAL_LINE) -> Path:
    root = tmp_path / "cargo"
    (root / "bin").mkdir(parents=True, exist_ok=True)
    (root / "bin" / "rtk").write_text("#!/bin/sh\necho rtk 0.48.0\n")
    if line is not None:
        (root / CRATES_TOML).write_text(f"[v1]\n{line}\n")
        (root / CRATES_JSON).write_text(json.dumps({"installs": {}}))
    return root


def test_cargos_own_record_names_the_revision_the_binary_was_built_from(tmp_path: Path) -> None:
    """The whole reason this lane needs no state file of its own."""
    installed = read_installed(_root(tmp_path), "rtk")
    assert installed is not None
    assert installed.revision == "4be91ebe1fc4eaf73688e28cd62782eadb355379"
    assert installed.version == "0.48.0"
    assert installed.url == "https://github.com/AlobarQuest/rtk?branch=main"


def test_a_root_that_has_never_installed_the_crate_reads_as_absent(tmp_path: Path) -> None:
    """`None` is a FIRST INSTALL, which is allowed -- deliberately not an error, and deliberately
    not confused with a record naming a different revision."""
    assert read_installed(_root(tmp_path, line=None), "rtk") is None
    assert read_installed(_root(tmp_path), "some-other-crate") is None


@pytest.mark.parametrize(
    "line",
    [
        '"rtk 0.48.0 (registry+https://github.com/rust-lang/crates.io-index)" = ["rtk"]',
        '"rtk 0.48.0 (git+https://github.com/AlobarQuest/rtk?branch=main#short)" = ["rtk"]',
        "not a record line at all",
    ],
)
def test_a_record_that_is_not_a_git_install_reads_as_absent(tmp_path: Path, line: str) -> None:
    """A crates.io install and a truncated revision both mean this lane cannot say what commit the
    binary came from -- which is `absent`, not a revision it guessed at."""
    assert read_installed(_root(tmp_path, line=line), "rtk") is None


def test_an_install_is_three_files_and_the_tuple_is_what_makes_a_rollback_complete() -> None:
    """`cargo install --force` writes the binary AND both records, and writes the records on a
    successful BUILD -- before this lane has probed anything. A rollback that restored one of them
    would leave cargo claiming a revision the machine is not running, after which every later pass
    would compare that claim to the head, find them equal, and never retry."""
    paths = install_artifacts(Path("/root"), "rtk")
    assert paths == (
        Path("/root/bin/rtk"),
        Path("/root") / CRATES_TOML,
        Path("/root") / CRATES_JSON,
    )


def test_a_backup_captures_every_artifact_and_a_restore_puts_all_of_them_back(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    artifacts = install_artifacts(root, "rtk")
    captured = backup(artifacts, tmp_path / "backup")
    assert set(captured) == {str(path) for path in artifacts}

    # Simulate what a successful build followed by a failed probe leaves behind: a new binary AND
    # a record claiming the new revision.
    (root / "bin" / "rtk").write_text("#!/bin/sh\necho rtk 0.99.0\n")
    (root / CRATES_TOML).write_text('[v1]\n"rtk 0.99.0 (git+x#' + "b" * 40 + ')" = ["rtk"]\n')

    restore(captured)
    remove_uncaptured(artifacts, captured)

    assert "0.48.0" in (root / "bin" / "rtk").read_text()
    reread = read_installed(root, "rtk")
    assert reread is not None
    assert reread.revision == "4be91ebe1fc4eaf73688e28cd62782eadb355379"


def test_a_rollback_of_a_FIRST_install_removes_what_was_not_there_before(tmp_path: Path) -> None:
    """The half a "restore the backup" reading misses. On a first install there is no outgoing
    binary, so a probe failure must leave the root as it was FOUND -- not leave behind a binary
    nobody proved."""
    root = tmp_path / "cargo"
    (root / "bin").mkdir(parents=True)
    artifacts = install_artifacts(root, "rtk")
    captured = backup(artifacts, tmp_path / "backup")
    assert captured == {}

    for path in artifacts:
        path.write_text("written by an install that then failed its probe")

    restore(captured)
    remove_uncaptured(artifacts, captured)
    assert [path for path in artifacts if path.exists()] == []


def test_rustup_is_looked_for_beyond_PATH_and_refuses_rather_than_guessing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MEASURED 2026-09-06: under `env -i PATH=/usr/bin:/bin` nothing named rustup is on PATH, and
    `~/.cargo/bin` holds `rtk` alone. A lane that looked only at PATH would report unmeasurable
    every night while working perfectly from a shell -- the `uv` trap one tool over.
    """
    monkeypatch.setattr("shutil.which", lambda _: None)
    candidate = tmp_path / "rustup"
    candidate.write_text("#!/bin/sh\n")
    candidate.chmod(0o755)
    assert find_rustup((tmp_path / "absent", candidate)) == candidate

    # And the control: with no candidate present it REFUSES rather than falling back to a name it
    # hopes is on PATH, which is the same fail-closed direction the window takes.
    with pytest.raises(ToolchainError):
        find_rustup((tmp_path / "absent",))


def test_the_cargo_environment_puts_the_toolchain_bin_FIRST(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MEASURED 2026-09-06 with clean exit codes on a scratch crate: `rustup which cargo` resolves
    a cargo that answers `--version` and then CANNOT BUILD, because it shells out to `rustc`, which
    lives beside it and is likewise not on PATH.

        rustup run stable cargo build            -> rc 101
        PATH=<toolchain bin>:$PATH cargo build   -> rc 0

    `rustup run` is the answer a reader reaches for first and is not the fix.
    """
    from tool_installer.toolchain import Cargo

    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    cargo = Cargo(executable=Path("/tc/bin/cargo"), toolchain_bin=Path("/tc/bin"))
    assert cargo.environment()["PATH"].split(":")[0] == "/tc/bin"
    assert cargo.environment()["PATH"] == "/tc/bin:/usr/bin:/bin"
