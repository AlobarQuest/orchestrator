"""The act, and above all the ROLLBACK -- demonstrated end to end rather than asserted.

**NOTHING HERE TOUCHES THE LIVE `~/.cargo`.** `install_root` is a parameter precisely so this can
be proven against a scratch root: `rtk` filters every Bash command in the session running these
tests, so exercising the rollback against the real install root would break the tool doing the
proving. The fake cargo below writes exactly what the real one writes -- a binary and both records
-- so the paths under test are the production paths.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from tool_installer.install import (
    ACTION_INSTALL_FAILED,
    ACTION_INSTALLED,
    ACTION_ROLLED_BACK,
    RollbackFailed,
    install_and_prove,
)
from tool_installer.toolchain import CRATES_JSON, CRATES_TOML, Cargo, read_installed
from tool_installer.tools import Tool

OLD_REVISION = "a" * 40
NEW_REVISION = "b" * 40

# A tool whose probes are cheap and real: the binary is asked to answer, and the version is checked
# by VALUE against what cargo recorded.
FAKE = Tool(
    name="faketool",
    repository="AlobarQuest/faketool",
    branch="main",
    crate="faketool",
    probe_commands=(("--version",), ("gain",)),
)


def _executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _seed_installed(root: Path, version: str, revision: str) -> None:
    """The outgoing artifact: a working binary and the records cargo wrote when it built it."""
    _executable(
        root / "bin" / FAKE.name,
        f'#!/bin/sh\nif [ "$1" = "gain" ]; then exit 0; fi\necho "{FAKE.name} {version}"\n',
    )
    (root / CRATES_TOML).write_text(
        f'[v1]\n"{FAKE.crate} {version} '
        f'(git+https://github.com/{FAKE.repository}?branch=main#{revision})" = ["{FAKE.name}"]\n'
    )
    (root / CRATES_JSON).write_text(json.dumps({"installs": {}}))


def _fake_cargo(tmp_path: Path, *, version: str, revision: str, reports: str, rc: int = 0) -> Cargo:
    """A cargo that writes what the real one writes.

    `reports` is what the NEW binary answers to `--version`, so a probe failure can be produced by
    a build that succeeded and installed something that is not what cargo says it is -- which is
    the case a bare exit-status probe cannot catch.
    """
    script = tmp_path / "bin" / "cargo"
    _executable(
        script,
        f"""#!/bin/sh
[ "{rc}" -ne 0 ] && {{ echo "build failed" >&2; exit {rc}; }}
root=""
while [ $# -gt 0 ]; do
  if [ "$1" = "--root" ]; then root="$2"; fi
  shift
done
mkdir -p "$root/bin"
printf '#!/bin/sh\\nif [ "$1" = "gain" ]; then exit 0; fi\\necho "{FAKE.name} {reports}"\\n' \\
  > "$root/bin/{FAKE.name}"
chmod +x "$root/bin/{FAKE.name}"
source="git+https://github.com/{FAKE.repository}?branch=main#{revision}"
record='[v1]\n"{FAKE.crate} {version} ('"$source"')" = ["{FAKE.name}"]\n'
printf "%b" "$record" > "$root/{CRATES_TOML}"
printf '{{"installs": {{}}}}' > "$root/{CRATES_JSON}"
exit 0
""",
    )
    return Cargo(executable=script, toolchain_bin=script.parent)


def _run(tmp_path: Path, cargo: Cargo, root: Path):
    return install_and_prove(
        tool=FAKE, cargo=cargo, install_root=root, backup_root=tmp_path / "backup"
    )


def test_a_good_install_is_kept_and_probed_clean(tmp_path: Path) -> None:
    """THE CONTROL. Without it, a rollback test passes for a harness that never installs."""
    root = tmp_path / "cargo"
    _seed_installed(root, "0.1.0", OLD_REVISION)
    cargo = _fake_cargo(tmp_path, version="0.2.0", revision=NEW_REVISION, reports="0.2.0")

    outcome = _run(tmp_path, cargo, root)

    assert outcome.action == ACTION_INSTALLED
    assert outcome.succeeded
    assert all(result.passed for result in outcome.probes)
    installed = read_installed(root, FAKE.crate)
    assert installed is not None
    assert (installed.revision, installed.version) == (NEW_REVISION, "0.2.0")


def test_a_probe_failure_restores_the_previous_binary_AND_cargos_record(tmp_path: Path) -> None:
    """THE ROLLBACK, end to end.

    The build succeeds and the installed binary reports a version cargo did not build -- so every
    exit status is zero and the artifact is still wrong, which is exactly the case `--version`
    checked by VALUE exists to catch.

    Both halves are asserted, and the SECOND is the one a "restore the backup" reading misses:
    cargo writes its record on a successful build, before any probe, so a rollback that put back
    only the binary would leave the record claiming the new revision -- after which every later
    pass would compare that record to the head, find them equal, report nothing to do, and never
    retry. A rollback that becomes permanent and invisible.
    """
    root = tmp_path / "cargo"
    _seed_installed(root, "0.1.0", OLD_REVISION)
    cargo = _fake_cargo(tmp_path, version="0.2.0", revision=NEW_REVISION, reports="9.9.9")

    outcome = _run(tmp_path, cargo, root)

    assert outcome.action == ACTION_ROLLED_BACK
    assert not outcome.succeeded
    # The binary is the old one, and it still works.
    assert "0.1.0" in (root / "bin" / FAKE.name).read_text()
    # And cargo's record agrees with it, which is what lets the NEXT pass see work still pending.
    restored = read_installed(root, FAKE.crate)
    assert restored is not None
    assert (restored.revision, restored.version) == (OLD_REVISION, "0.1.0")
    # The failing probe is named rather than merely counted.
    failed = [result for result in outcome.probes if not result.passed]
    assert [result.command[0] for result in failed] == ["--version"]
    assert "9.9.9" in failed[0].detail


def test_the_next_pass_after_a_rollback_still_sees_work_pending(tmp_path: Path) -> None:
    """The consequence of restoring all three, stated as a fact about the following pass.

    A standing finding until somebody acts is the honest shape; silence is not.
    """
    root = tmp_path / "cargo"
    _seed_installed(root, "0.1.0", OLD_REVISION)
    cargo = _fake_cargo(tmp_path, version="0.2.0", revision=NEW_REVISION, reports="9.9.9")
    _run(tmp_path, cargo, root)

    installed = read_installed(root, FAKE.crate)
    assert installed is not None
    assert installed.revision != NEW_REVISION


def test_a_failed_BUILD_leaves_the_working_binary_untouched(tmp_path: Path) -> None:
    """`cargo install --force` replaces a binary only on success, so there is nothing to put back
    -- and restoring anyway would be a write this failure does not justify."""
    root = tmp_path / "cargo"
    _seed_installed(root, "0.1.0", OLD_REVISION)
    cargo = _fake_cargo(tmp_path, version="0.2.0", revision=NEW_REVISION, reports="0.2.0", rc=101)

    outcome = _run(tmp_path, cargo, root)

    assert outcome.action == ACTION_INSTALL_FAILED
    assert "101" in outcome.detail
    assert "0.1.0" in (root / "bin" / FAKE.name).read_text()


def test_a_first_install_that_fails_its_probe_leaves_the_root_AS_IT_WAS_FOUND(
    tmp_path: Path,
) -> None:
    """No outgoing binary means a rollback must REMOVE, not restore -- otherwise a first install
    would leave behind an artifact nobody proved."""
    root = tmp_path / "cargo"
    (root / "bin").mkdir(parents=True)
    cargo = _fake_cargo(tmp_path, version="0.2.0", revision=NEW_REVISION, reports="9.9.9")

    outcome = _run(tmp_path, cargo, root)

    assert outcome.action == ACTION_ROLLED_BACK
    assert not (root / "bin" / FAKE.name).exists()
    assert not (root / CRATES_TOML).exists()
    assert read_installed(root, FAKE.crate) is None


def test_a_probe_addresses_the_installed_PATH_and_never_the_name(tmp_path: Path) -> None:
    """A probe that ran `faketool --version` would exercise whatever PATH resolved -- during a
    rollback demonstration, the live binary -- which is a probe that cannot fail for the reason it
    exists to catch. Proven by putting a LYING binary of the same name first on PATH: if the probe
    used the name, this install would pass.
    """
    root = tmp_path / "cargo"
    _seed_installed(root, "0.1.0", OLD_REVISION)
    decoy = tmp_path / "decoy"
    _executable(decoy / FAKE.name, f'#!/bin/sh\necho "{FAKE.name} 0.2.0"\n')

    cargo = _fake_cargo(tmp_path, version="0.2.0", revision=NEW_REVISION, reports="9.9.9")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("PATH", f"{decoy}:/usr/bin:/bin")
        outcome = _run(tmp_path, cargo, root)

    assert outcome.action == ACTION_ROLLED_BACK


def test_a_build_that_reports_success_and_records_NOTHING_is_rolled_back(tmp_path: Path) -> None:
    """cargo exiting zero having written no record is a state this program cannot describe, and
    the alternative to rolling back is walking away from an unproven binary.

    Found by a mutation review: turning this branch into `ACTION_INSTALLED` survived the suite.
    """
    root = tmp_path / "cargo"
    _seed_installed(root, "0.1.0", OLD_REVISION)
    silent = tmp_path / "bin" / "cargo-silent"
    _executable(
        silent,
        f"""#!/bin/sh
root=""
while [ $# -gt 0 ]; do
  if [ "$1" = "--root" ]; then root="$2"; fi
  shift
done
mkdir -p "$root/bin"
echo '#!/bin/sh' > "$root/bin/{FAKE.name}"
chmod +x "$root/bin/{FAKE.name}"
rm -f "$root/{CRATES_TOML}"
exit 0
""",
    )
    outcome = install_and_prove(
        tool=FAKE,
        cargo=Cargo(executable=silent, toolchain_bin=silent.parent),
        install_root=root,
        backup_root=tmp_path / "backup",
    )

    assert outcome.action == ACTION_ROLLED_BACK
    assert "recorded nothing" in outcome.detail
    restored = read_installed(root, FAKE.crate)
    assert restored is not None
    assert restored.revision == OLD_REVISION


def test_a_failed_BUILD_DELETES_NOTHING_it_did_not_create(tmp_path: Path) -> None:
    """A failed build leaves every artifact in the root exactly as it found it.

    **THE HISTORY HERE IS THE USEFUL PART.** A mutation adding a restore to the failed-build
    branch survived this test and was MEASURED equivalent: an ordinary build failure writes
    nothing, so a restore copies identical bytes back and `remove_uncaptured` finds nothing to
    delete. That was a correct measurement of an incomplete model -- `_run` also returns non-zero
    when it KILLS cargo at a timeout, which can land after the binary has been swapped, and on
    that path the two are not equivalent at all. The restore is now unconditional, and
    `test_an_install_KILLED_AFTER_the_swap_still_restores_the_previous_artifact` is the control
    that separates them.

    So this test no longer speaks to the restore at all. What it still pins is narrower and worth
    keeping: a failed install does not tidy away debris from an earlier run that the backup never
    captured.
    """
    root = tmp_path / "cargo"
    (root / "bin").mkdir(parents=True)
    stale = root / CRATES_JSON
    stale.write_text('{"installs": {"left behind by an earlier run": {}}}')
    cargo = _fake_cargo(tmp_path, version="0.2.0", revision=NEW_REVISION, reports="0.2.0", rc=101)

    outcome = install_and_prove(
        tool=FAKE, cargo=cargo, install_root=root, backup_root=tmp_path / "backup"
    )

    assert outcome.action == ACTION_INSTALL_FAILED
    assert stale.exists()
    assert "left behind by an earlier run" in stale.read_text()


def test_a_tool_that_warns_on_STDERR_still_passes_its_version_check(tmp_path: Path) -> None:
    """The version check reads a VALUE, so it must read the stream the value is on.

    A single `stderr or stdout` return would make this binary -- which reports its version
    correctly on stdout and writes an update notice to stderr -- fail its value check, after which
    the lane rolls a perfectly good artifact back every night. Fail-closed, and wrong.
    """
    root = tmp_path / "cargo"
    _seed_installed(root, "0.1.0", OLD_REVISION)
    noisy = tmp_path / "bin" / "cargo-noisy"
    _executable(
        noisy,
        f"""#!/bin/sh
root=""
while [ $# -gt 0 ]; do
  if [ "$1" = "--root" ]; then root="$2"; fi
  shift
done
mkdir -p "$root/bin"
{{
  echo '#!/bin/sh'
  echo 'echo "a new version of this tool is available" >&2'
  echo 'case "$1" in gain) exit 0;; esac'
  echo 'echo "{FAKE.name} 0.2.0"'
}} > "$root/bin/{FAKE.name}"
chmod +x "$root/bin/{FAKE.name}"
source="git+https://github.com/{FAKE.repository}?branch=main#{NEW_REVISION}"
record='[v1]\n"{FAKE.crate} 0.2.0 ('"$source"')" = ["{FAKE.name}"]\n'
printf "%b" "$record" > "$root/{CRATES_TOML}"
printf '{{"installs": {{}}}}' > "$root/{CRATES_JSON}"
""",
    )
    outcome = install_and_prove(
        tool=FAKE,
        cargo=Cargo(executable=noisy, toolchain_bin=noisy.parent),
        install_root=root,
        backup_root=tmp_path / "backup",
    )

    assert outcome.action == ACTION_INSTALLED
    assert all(result.passed for result in outcome.probes)


def test_the_backup_SURVIVES_a_successful_install(tmp_path: Path) -> None:
    """Spec step 6: the outgoing artifact is kept, and keeping it is the whole point.

    The in-pass rollback covers a proof that fails. THIS covers the case no probe can see -- a
    binary that passes every check and misbehaves in use hours later -- and it is only useful the
    next morning if it survived the pass that made it. `~/.local/bin-backups/rtk-0.42.4`, put
    there by hand in June and still there, is the precedent and the shape a person reaches for.
    """
    root = tmp_path / "cargo"
    _seed_installed(root, "0.1.0", OLD_REVISION)
    keep = tmp_path / "bin-backups" / f"{FAKE.name}-0.1.0"
    cargo = _fake_cargo(tmp_path, version="0.2.0", revision=NEW_REVISION, reports="0.2.0")

    outcome = install_and_prove(tool=FAKE, cargo=cargo, install_root=root, backup_root=keep)

    assert outcome.action == ACTION_INSTALLED
    assert (keep / FAKE.name).is_file()
    assert "0.1.0" in (keep / FAKE.name).read_text()
    # All three, so a hand rollback restores a consistent install rather than a lone binary.
    assert (keep / CRATES_TOML).is_file()
    assert (keep / CRATES_JSON).is_file()


def test_an_install_KILLED_AFTER_the_swap_still_restores_the_previous_artifact(
    tmp_path: Path,
) -> None:
    """The timeout path, which is why a failed install restores unconditionally.

    An ordinary build failure writes nothing -- `cargo install --force` builds to a temporary
    directory and moves on success -- so a restore there copies identical bytes and no test can
    see it. But `_run` also returns non-zero when it KILLS cargo at `INSTALL_TIMEOUT_SECONDS`, and
    that can land AFTER the new binary has been moved into place and before `.crates.toml` is
    written. This fake reproduces exactly that: binary swapped, record not written, non-zero exit.

    Without the restore the machine is left running an unproven binary while the pass reports
    "the working artifact was left in place" -- an assertion about the machine that is false.
    A mutation review found the earlier control could not see this.
    """
    root = tmp_path / "cargo"
    _seed_installed(root, "0.1.0", OLD_REVISION)
    killed = tmp_path / "bin" / "cargo-killed"
    _executable(
        killed,
        f"""#!/bin/sh
root=""
while [ $# -gt 0 ]; do
  if [ "$1" = "--root" ]; then root="$2"; fi
  shift
done
mkdir -p "$root/bin"
printf '#!/bin/sh\necho "{FAKE.name} 0.2.0"\n' > "$root/bin/{FAKE.name}"
chmod +x "$root/bin/{FAKE.name}"
exit 124
""",
    )
    outcome = install_and_prove(
        tool=FAKE,
        cargo=Cargo(executable=killed, toolchain_bin=killed.parent),
        install_root=root,
        backup_root=tmp_path / "backup",
    )

    assert outcome.action == ACTION_INSTALL_FAILED
    # The half that discriminates: the swapped binary is gone and the outgoing one is back.
    assert "0.1.0" in (root / "bin" / FAKE.name).read_text()
    restored = read_installed(root, FAKE.crate)
    assert restored is not None
    assert restored.revision == OLD_REVISION


def test_a_rollback_that_cannot_complete_is_NAMED_rather_than_a_bare_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A full disk or a permissions change mid-rollback is the worst state this lane can produce.

    Unguarded, it escapes `main` as a traceback, leaving a partially restored install root and no
    observation -- the one state most needing a record. A mutation review found nothing exercised
    this path.
    """
    root = tmp_path / "cargo"
    _seed_installed(root, "0.1.0", OLD_REVISION)
    cargo = _fake_cargo(tmp_path, version="0.2.0", revision=NEW_REVISION, reports="9.9.9")

    def refuse(*_: object, **__: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("tool_installer.install.restore", refuse)
    with pytest.raises(RollbackFailed, match="could not be restored"):
        install_and_prove(
            tool=FAKE, cargo=cargo, install_root=root, backup_root=tmp_path / "backup"
        )
