"""The pass: both admission terms, and what a bare invocation does not do.

The gate is exercised through the real command, with the two clients and the GitHub reader
replaced. The thing under test is the CONJUNCTION -- `--install` and the window -- and every case
comes in a pair whose answers must differ, so a term that was dropped entirely cannot pass.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from tool_installer import cli as cli_module
from tool_installer.cli import app
from tool_installer.install import InstallOutcome, RollbackFailed
from tool_installer.orchestrator_client import ObservationWriteError
from tool_installer.plugin import Sites
from tool_installer.toolchain import CRATES_JSON, CRATES_TOML, Node, ToolchainError
from tool_installer.tools import OCTO, TOOLS

HEAD = "4be91ebe1fc4eaf73688e28cd62782eadb355379"
OLD = "1111111111111111111111111111111111111111"
POLICY = {
    "reach": [
        {
            "member": "operator_machine",
            "change_window": {
                "timezone": "America/New_York",
                "start": "02:00",
                "end": "06:00",
            },
        }
    ]
}

IN_WINDOW = datetime(2026, 9, 6, 7, 0, tzinfo=UTC)  # 03:00 New York
OUT_OF_WINDOW = datetime(2026, 9, 6, 19, 0, tzinfo=UTC)  # 15:00 New York


class _Recorder:
    """Stands in for both orchestrator clients, and remembers everything it was asked to do."""

    def __init__(self) -> None:
        self.filed: list[dict[str, Any]] = []

    def __enter__(self) -> _Recorder:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def factory_policy(self) -> dict[str, Any]:
        return POLICY

    def record_observation(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.filed.append(payload)
        return {"id": "recorded"}


def _seed_rea(npm_root: Path, revision: str) -> None:
    """The installed package's own manifest, which is the npm row's whole record."""
    package = npm_root / "lib" / "node_modules" / "rea-agents"
    package.mkdir(parents=True, exist_ok=True)
    (package / "package.json").write_text(
        json.dumps({"name": "rea-agents", "version": "6.7.0", "gitHead": revision})
    )


def _seed_n8nac(npm_root: Path, revision: str) -> None:
    """A workspace package's record is its WORKSPACE's manifest, inside the installed checkout."""
    workspace = npm_root / "lib" / "node_modules" / "n8nac" / "packages" / "cli"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "package.json").write_text(
        json.dumps({"name": "n8nac", "version": "2.7.0", "gitHead": revision})
    )


def _seed_plugin(plugins_root: Path, version: str, revision: str) -> None:
    """Claude Code's own record, plus the cache directory it names, for both plugin rows."""
    rows: dict[str, list[dict[str, str]]] = {}
    for plugin in ("octo", "n8n-as-code"):
        cache = plugins_root / "cache" / "devon-plugins" / plugin / version
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "plugin.json").write_text("{}")
        rows[f"{plugin}@devon-plugins"] = [
            {
                "scope": "user",
                "installPath": str(cache),
                "version": version,
                "gitCommitSha": revision,
            }
        ]
    (plugins_root / "installed_plugins.json").write_text(json.dumps({"plugins": rows}))


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    """A scratch install root holding an OLD rtk, and a cargo that must never be reached.

    THE PLUGIN AND NPM ROWS ARE SEEDED CURRENT, so every assertion below stays about rtk. The
    alternative -- leaving their roots at the real defaults -- would have each of these read the
    operator's own `~/.claude/plugins` or npm prefix, which is both a test that depends on the
    machine it runs on and a pass one `--install` away from acting on it. `resolve_claude` and
    `resolve_node` are RAISING stubs for the same reason `resolve_cargo` is: reaching any of them
    is the failure.
    """
    root = tmp_path / "cargo"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "rtk").write_text("#!/bin/sh\necho rtk 0.1.0\n")
    (root / CRATES_TOML).write_text(
        f'[v1]\n"rtk 0.1.0 (git+https://github.com/AlobarQuest/rtk?branch=main#{OLD})" = ["rtk"]\n'
    )
    (root / CRATES_JSON).write_text(json.dumps({"installs": {}}))

    plugins_root = tmp_path / "plugins"
    plugins_root.mkdir()
    _seed_plugin(plugins_root, "11.0.1", HEAD)
    sites = Sites(hub_root=tmp_path / "hub", plugins_root=plugins_root)
    # The hub's catalog names where each plugin's folder is, as the real one does.
    (sites.hub_root / ".claude-plugin").mkdir(parents=True)
    (sites.hub_root / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps(
            {
                "name": "devon-plugins",
                "plugins": [
                    {"name": "octo", "source": "./octo"},
                    {"name": "n8n-as-code", "source": "./n8n-as-code/plugins/claude/n8n-as-code"},
                ],
            }
        )
    )
    npm_root = tmp_path / "npm"
    _seed_rea(npm_root, HEAD)
    _seed_n8nac(npm_root, HEAD)
    monkeypatch.setattr(cli_module, "default_sites", lambda: sites)

    recorder = _Recorder()
    monkeypatch.setattr(cli_module, "open_policy_client", lambda **_: recorder)
    monkeypatch.setattr(cli_module, "open_client", lambda **_: recorder)
    monkeypatch.setattr(
        cli_module, "commit", lambda reader, repository, ref: (HEAD, "2026-09-05T18:00:00Z")
    )
    monkeypatch.setattr(cli_module, "GitHubReader", lambda **_: _Reader())

    def _never(*_: object, **__: object) -> None:
        raise AssertionError("the pass reached the toolchain when it was not permitted to act")

    def _never_claude(*_: object, **__: object) -> None:
        # A DISTINGUISHABLE message, so a test that proves the plugin row reached the thing that
        # would install it cannot be satisfied by the cargo row reaching its own stub first.
        raise AssertionError("the pass reached CLAUDE when it was not permitted to act")

    def _never_node(*_: object, **__: object) -> None:
        raise AssertionError("the pass reached NODE when it was not permitted to act")

    monkeypatch.setattr(cli_module, "resolve_cargo", _never)
    monkeypatch.setattr(cli_module, "resolve_claude", _never_claude)
    monkeypatch.setattr(cli_module, "resolve_node", _never_node)

    for name, value in (
        ("ORCHESTRATOR_API_URL", "https://sds.example.net"),
        ("ORCHESTRATOR_POLICY_CREDENTIAL_KEY_ID", "orchestrator-system"),
        ("ORCHESTRATOR_POLICY_TOKEN", "t"),
        ("ORCHESTRATOR_API_CREDENTIAL_KEY_ID", "orchestrator-observer"),
        ("ORCHESTRATOR_API_TOKEN", "t"),
        ("TOOL_INSTALLER_GITHUB_TOKEN", "t"),
    ):
        monkeypatch.setenv(name, value)
    recorder.root = root  # type: ignore[attr-defined]
    recorder.sites = sites  # type: ignore[attr-defined]
    recorder.npm_root = npm_root  # type: ignore[attr-defined]
    return recorder


def _raise_toolchain(*_: object, **__: object) -> None:
    raise ToolchainError("rustup is not on PATH and is at none of the known locations")


class _Reader:
    def __enter__(self) -> _Reader:
        return self

    def __exit__(self, *_: object) -> None:
        return None


def _invoke(recorder: _Recorder, monkeypatch: pytest.MonkeyPatch, moment: datetime, *args: str):
    monkeypatch.setattr(cli_module, "_now", lambda: moment)
    return CliRunner().invoke(
        app,
        [
            "--install-root",
            str(recorder.root),  # type: ignore[attr-defined]
            "--npm-root",
            str(recorder.npm_root),  # type: ignore[attr-defined]
            *args,
        ],
    )


def test_a_bare_pass_touches_no_binary_and_never_reaches_the_toolchain(
    machine: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--install` is what makes a pass act. Without it the pass measures and reports.

    `resolve_cargo` is replaced with something that RAISES, so "did not install" is proven by the
    pass not reaching the toolchain at all rather than by a binary that happens to be unchanged.
    Note the clock is IN the window: the other term is the only thing refusing.
    """
    before = (machine.root / "bin" / "rtk").read_text()  # type: ignore[attr-defined]
    result = _invoke(machine, monkeypatch, IN_WINDOW)
    assert result.exit_code == 0
    assert (machine.root / "bin" / "rtk").read_text() == before  # type: ignore[attr-defined]
    assert "is not main's head" in result.stdout


def test_the_window_refuses_even_WITH_the_install_flag(
    machine: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of the conjunction, and the pair for the test above: same flag, same
    machine, only the clock differs. A gate that read one term would give these two cases the
    same answer -- and this one would reach the raising `resolve_cargo`."""
    result = _invoke(machine, monkeypatch, OUT_OF_WINDOW, "--install")
    assert result.exit_code == 0
    assert "closed" in result.stdout


def test_the_window_is_reported_open_when_it_is_open(
    machine: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control that keeps the assertion above from passing against a term stuck on closed."""
    result = _invoke(machine, monkeypatch, IN_WINDOW)
    assert "open" in result.stdout


def test_a_pass_files_its_row_even_when_it_was_not_permitted_to_act(
    machine: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The finding's durable home is the observation, not the exit code -- so a machine running an
    old tool is recorded whether or not anybody permitted an install."""
    _invoke(machine, monkeypatch, OUT_OF_WINDOW, "--install")
    # FOUR ROWS: the behind rtk and the current octo, rea and n8nac. The current rows are
    # asserted here rather than ignored, because "every tool is filed every pass" is the
    # property that makes silence mean something -- a pass that filed only the interesting row
    # would be indistinguishable from a pass that stopped measuring the others.
    assert len(machine.filed) == len(TOOLS)
    assert [row["subject_reference"] for row in machine.filed] == [
        "AlobarQuest/rtk",
        "AlobarQuest/claude-octopus",
        "AlobarQuest/rea",
        "AlobarQuest/n8n-as-code",
        "AlobarQuest/n8n-as-code",
    ]
    row = machine.filed[0]
    assert row["source_system"] == "tool_installer"
    assert row["status"] == "degraded"
    assert row["facts"]["action"] == "not_permitted"


def test_dry_run_files_nothing(machine: _Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    result = _invoke(machine, monkeypatch, IN_WINDOW, "--dry-run")
    assert machine.filed == []
    assert "not filed" in result.stdout


def test_a_current_machine_is_filed_as_passed_and_exits_clean(
    machine: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row saying the machine is current is what makes "this lane is watching that tool" a fact
    rather than an inference from silence."""
    root: Path = machine.root  # type: ignore[attr-defined]
    (root / CRATES_TOML).write_text(
        f'[v1]\n"rtk 0.48.0 (git+https://github.com/AlobarQuest/rtk?branch=main#{HEAD})" '
        '= ["rtk"]\n'
    )
    result = _invoke(machine, monkeypatch, IN_WINDOW, "--install")
    assert result.exit_code == 0
    assert machine.filed[0]["status"] == "passed"
    assert machine.filed[0]["facts"]["action"] == "none"


def test_an_unreadable_window_refuses_the_whole_pass(
    machine: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A WINDOW THAT CANNOT BE READ IS A REFUSAL, NEVER A DEFAULT -- exit 2, nothing filed."""
    monkeypatch.setattr(_Recorder, "factory_policy", lambda self: {"reach": []})
    result = _invoke(machine, monkeypatch, IN_WINDOW, "--install")
    assert result.exit_code == 2
    assert machine.filed == []


def test_a_row_that_cannot_be_FILED_exits_2_rather_than_reporting_a_clean_pass(
    machine: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unfiled pass cannot claim it reported anything.

    This is the deploy-ordering failure mode, and it is why it has a test: production refuses
    every row this lane writes until migration 0033 widens the observation CHECK constraints, so
    a lane scheduled before that migration lands does its work and files nothing. It must say so
    -- exit 2, the code this group uses for "could not use its inputs", including the one it
    reports through -- rather than exit 0, which would report a clean pass over a machine nobody
    recorded.

    Its control is `test_a_pass_files_its_row_even_when_it_was_not_permitted_to_act`, which takes
    the SAME inputs and asserts both the row and a clean exit, so this assertion cannot be
    satisfied by a pass that exits 2 whatever happens.
    """

    def refuse(self: _Recorder, payload: dict[str, Any]) -> dict[str, Any]:
        raise ObservationWriteError("orchestrator rejected POST /api/v1/observations: 400")

    monkeypatch.setattr(_Recorder, "record_observation", refuse)
    result = _invoke(machine, monkeypatch, OUT_OF_WINDOW, "--install")
    assert result.exit_code == 2
    assert "row not filed" in result.stderr or "row not filed" in result.output


def test_dry_run_NEVER_ACTS_even_with_the_install_flag_and_an_open_window(
    machine: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--dry-run` is an admission term, not only a filing switch.

    Consulted only at the filing step, `--install --dry-run` inside the window replaced the binary
    and recorded nothing -- and the launcher's dead-man switch does not arm under `--dry-run`
    either, so the one act this lane exists to record happened with neither an observation nor a
    liveness ping. `resolve_cargo` is the raising stub here, so "did not act" is proven by never
    reaching the toolchain rather than by a binary that happens to be unchanged.
    """
    before = (machine.root / "bin" / "rtk").read_text()  # type: ignore[attr-defined]
    result = _invoke(machine, monkeypatch, IN_WINDOW, "--install", "--dry-run")
    assert result.exit_code == 0
    assert (machine.root / "bin" / "rtk").read_text() == before  # type: ignore[attr-defined]
    assert machine.filed == []
    assert "--install is ignored" in result.stdout


def test_the_observer_credential_is_refused_BEFORE_anything_can_be_replaced(
    machine: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Checked at the filing step instead, a pass swaps the tool and then finds it cannot say so.

    The trigger is ordinary: the launcher runs under `set -uo pipefail` with no `-e` and its
    `_bws_value` yields an empty string when a fetch fails, so a transient failure on the SECOND
    of two BWS reads produces exactly this. Proven by the raising `resolve_cargo` stub -- the pass
    must refuse without ever reaching the toolchain.
    """
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "")
    result = _invoke(machine, monkeypatch, IN_WINDOW, "--install")
    assert result.exit_code == 1
    assert machine.filed == []
    # THE EXIT CODE ALONE CANNOT TELL THE TWO APART, which a mutation review proved: with the
    # up-front check removed the pass runs on, hits the raising `resolve_cargo` stub, and the
    # CliRunner reports exit 1 for THAT instead -- so an assertion on the code passes against the
    # very defect it was written to catch. The refusal must be named, and the toolchain must be
    # shown unreached.
    output = result.stdout + (result.stderr or "")
    assert "observer credential is not configured" in output
    assert "reached the toolchain" not in output


def test_a_dry_run_needs_no_write_credential(
    machine: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control for the test above: the up-front check must not refuse a pass that files
    nothing, or a dry run would need a credential it never uses."""
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "")
    result = _invoke(machine, monkeypatch, IN_WINDOW, "--dry-run")
    assert result.exit_code == 0


def test_a_missing_toolchain_FILES_the_finding_it_already_made(
    machine: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The measurement is complete before the toolchain is ever needed, so it is filed.

    Raising straight out of the loop threw away a finished "this machine is behind" finding at
    exactly the moment the lane cannot fix itself -- and the scheduled pass always passes
    `--install`, so a toolchain drifted out of reach would have made every night exit 2 saying
    nothing at all. The pass still ends at 2; it says what it learned first.
    """
    monkeypatch.setattr(cli_module, "resolve_cargo", _raise_toolchain)
    result = _invoke(machine, monkeypatch, IN_WINDOW, "--install")
    assert result.exit_code == 2
    assert len(machine.filed) == len(TOOLS)
    assert machine.filed[0]["status"] == "degraded"
    assert machine.filed[0]["facts"]["state"] == "behind"


def _octo_behind(machine: _Recorder) -> None:
    """Re-seed the plugin record at a revision the fork's head is not, leaving rtk untouched."""
    _seed_plugin(machine.sites.plugins_root, "9.45.0", OLD)  # type: ignore[attr-defined]


def _rtk_current(machine: _Recorder) -> None:
    """Put rtk at the head, so the PLUGIN row is the only one a permitted pass would act on.

    Without this the cargo row is reached first and raises its own stub, and a test meaning to say
    something about the plugin row would be satisfied by rtk every time.
    """
    (machine.root / CRATES_TOML).write_text(  # type: ignore[attr-defined]
        f'[v1]\n"rtk 0.48.0 (git+https://github.com/AlobarQuest/rtk?branch=main#{HEAD})" '
        '= ["rtk"]\n'
    )


def test_the_octo_row_is_reported_current_when_it_is(
    machine: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare pass writes nothing and says so, and the row saying the machine is current is what
    makes "this lane is watching that plugin" a fact rather than an inference from silence."""
    result = _invoke(machine, monkeypatch, IN_WINDOW)
    assert result.exit_code == 0
    assert "octo 11.0.1 installed on the operator machine is" in result.stdout
    assert "which is main's head" in result.stdout
    assert machine.filed[1]["status"] == "passed"
    assert machine.filed[1]["facts"]["action"] == "none"


@pytest.mark.parametrize(
    ("dirty", "state", "status", "action"),
    [
        (None, "current", "passed", "none"),
        ("untracked", "behind", "degraded", "not_permitted"),
        ("tracked", "behind", "degraded", "not_permitted"),
    ],
)
def test_the_octo_row_is_current_by_the_FOLDER_it_loads_when_the_version_did_not_move(
    machine: _Recorder,
    monkeypatch: pytest.MonkeyPatch,
    dirty: str | None,
    state: str,
    status: str,
    action: str,
) -> None:
    """The measurement agrees with `verify`: the record names the folder's version at an older
    commit (Claude Code rewrites `gitCommitSha` only on a version change), the folder is at the
    fork's head, and the row is current -- so a permitted pass would not act and roll back.

    A DIRTY folder is the pair: Claude Code loads the working tree, which its HEAD no longer
    describes, so the record's older commit stands and the row is behind."""
    clone = machine.sites.clone_root(OCTO)  # type: ignore[attr-defined]
    (clone / ".claude-plugin").mkdir(parents=True)
    (clone / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "octo", "version": "11.0.1"})
    )
    for args in (
        ("init", "-b", OCTO.branch),
        ("-c", "user.email=sds@example.invalid", "-c", "user.name=sds", "add", "-A"),
        (
            "-c",
            "user.email=sds@example.invalid",
            "-c",
            "user.name=sds",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            "folder",
        ),
    ):
        subprocess.run(["git", "-C", str(clone), *args], check=True, capture_output=True)
    folder = subprocess.run(
        ["git", "-C", str(clone), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    if dirty == "untracked":
        (clone / "scratch.md").write_text("an edit nobody committed\n")
    elif dirty == "tracked":
        (clone / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "octo", "version": "11.0.1", "description": "edited"})
        )
    _seed_plugin(machine.sites.plugins_root, "11.0.1", OLD)  # type: ignore[attr-defined]
    monkeypatch.setattr(
        cli_module,
        "commit",
        lambda reader, repository, ref: (
            (folder if repository == OCTO.repository else HEAD),
            "2026-09-05T18:00:00Z",
        ),
    )

    result = _invoke(machine, monkeypatch, IN_WINDOW)
    assert result.exit_code == 0
    octo = machine.filed[1]
    assert octo["status"] == status
    assert octo["facts"]["state"] == state
    assert octo["facts"]["action"] == action


def test_the_window_refuses_the_OCTO_row_with_the_install_flag(
    machine: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The window's first half FOR THIS ROW: permitted to act, told not now.

    `resolve_claude` is the raising stub, so "did not act" is proven by never reaching the thing
    that would install rather than by a record that happens to be unchanged.
    """
    _octo_behind(machine)
    _rtk_current(machine)
    result = _invoke(machine, monkeypatch, OUT_OF_WINDOW, "--install")
    assert result.exit_code == 0
    assert "closed" in result.stdout
    assert machine.filed[1]["facts"]["action"] == "not_permitted"
    assert machine.filed[1]["facts"]["state"] == "behind"


def test_the_window_ADMITS_the_OCTO_row_when_it_is_open(
    machine: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE PAIR FOR THE TEST ABOVE, and the reason the pair exists.

    Same flag, same machine, same seeded plugin -- only the clock differs. A window term that had
    been dropped entirely, or one stuck on `closed`, would give both cases the same answer; here
    the open clock reaches the raising `resolve_claude`, which is the proof that the ONLY thing
    refusing in the case above is the window.
    """
    _octo_behind(machine)
    _rtk_current(machine)
    result = _invoke(machine, monkeypatch, IN_WINDOW, "--install")
    assert isinstance(result.exception, AssertionError)
    assert "reached CLAUDE" in str(result.exception)


def test_a_failed_rollback_is_FILED_and_does_not_cost_the_other_row_its_record(
    machine: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE COMPOUNDING HALF OF THE WORST DEFECT, and the reason the catch is per row.

    `RollbackFailed` was caught nowhere: it reached typer as a traceback, so the pass that most
    needed to say what happened filed nothing. And because `rows` is filed AFTER the loop, a
    defect in the NEW plugin row cost the pre-existing cargo row its observation too -- this PR
    would have made a working lane's record conditional on an untested one not crashing.

    Both halves are asserted: the octo row is filed as `rollback_failed`, and the rtk row is
    filed at all.
    """
    _octo_behind(machine)
    _rtk_current(machine)

    def explode(*args: object, **kwargs: object) -> None:
        raise RollbackFailed("the previous plugin could not be restored: claude exited 9")

    # The fixture's `resolve_claude` raises to prove non-arrival elsewhere; here the pass MUST
    # arrive, so it is let through and the failure is moved to the act itself.
    monkeypatch.setattr(cli_module, "resolve_claude", lambda: Path("/usr/bin/true"))
    monkeypatch.setattr(cli_module, "install_and_prove_plugin", explode)

    result = _invoke(machine, monkeypatch, IN_WINDOW, "--install")

    # Exit 3 is this lane's FINDING code -- the pass ran and reported. What must not happen is a
    # traceback, which is what an uncaught `RollbackFailed` produced (exit 1, reading as "the tool
    # itself failed", with nothing filed).
    assert result.exit_code == 3, f"expected the finding code, got {result.exit_code}"
    assert not isinstance(result.exception, RollbackFailed), "it threw instead of filing"
    filed = {row["subject_reference"]: row for row in machine.filed}
    assert "AlobarQuest/claude-octopus" in filed
    assert filed["AlobarQuest/claude-octopus"]["facts"]["action"] == "rollback_failed"
    assert "AlobarQuest/rtk" in filed, "the working row lost its record to the other row's crash"


def _rea_behind(machine: _Recorder) -> None:
    _seed_rea(machine.npm_root, OLD)  # type: ignore[attr-defined]


def test_the_window_refuses_the_REA_row_with_the_install_flag(
    machine: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The npm row's first half: permitted to act, told not now, and node never resolved."""
    _rea_behind(machine)
    _rtk_current(machine)
    result = _invoke(machine, monkeypatch, OUT_OF_WINDOW, "--install")
    assert result.exit_code == 0
    filed = {row["subject_reference"]: row for row in machine.filed}
    assert filed["AlobarQuest/rea"]["facts"]["action"] == "not_permitted"
    assert filed["AlobarQuest/rea"]["facts"]["state"] == "behind"
    assert filed["AlobarQuest/rea"]["facts"]["installed"]["version"] == "6.7.0"


def test_the_window_ADMITS_the_REA_row_when_it_is_open(
    machine: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pair for the test above: only the clock differs, and the open clock reaches node."""
    _rea_behind(machine)
    _rtk_current(machine)
    result = _invoke(machine, monkeypatch, IN_WINDOW, "--install")
    assert isinstance(result.exception, AssertionError)
    assert "reached NODE" in str(result.exception)


def test_the_N8NAC_row_reads_its_record_from_the_workspace_manifest(
    machine: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Behind is measured from `packages/cli/package.json` inside the installed checkout, and the
    window still decides: refused out of hours, node reached in them."""
    _seed_n8nac(machine.npm_root, OLD)  # type: ignore[attr-defined]
    _rtk_current(machine)
    result = _invoke(machine, monkeypatch, OUT_OF_WINDOW, "--install")
    assert result.exit_code == 0
    # Two rows share n8n-as-code's repository (the CLI and its plugin), so a row is found by name.
    filed = {row["facts"]["tool"]["name"]: row for row in machine.filed}
    facts = filed["n8nac"]["facts"]
    assert facts["action"] == "not_permitted"
    assert facts["state"] == "behind"
    assert facts["installed"]["version"] == "2.7.0"

    result = _invoke(machine, monkeypatch, IN_WINDOW, "--install")
    assert isinstance(result.exception, AssertionError)
    assert "reached NODE" in str(result.exception)


def test_an_admitted_rea_row_is_handed_to_the_npm_act_with_the_head_it_measured(
    machine: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The act builds the revision the pass READ, never whatever the branch is by the time it
    fetches -- so the head is passed through, and the npm root is the one the pass was given."""
    _rea_behind(machine)
    _rtk_current(machine)
    seen: dict[str, object] = {}

    def act(**kwargs: object) -> InstallOutcome:
        seen.update(kwargs)
        return InstallOutcome(action="install_failed", installed=None, detail="stub")

    monkeypatch.setattr(cli_module, "resolve_node", lambda: Node(executable=Path("/usr/bin/true")))
    monkeypatch.setattr(cli_module, "install_and_prove_npm", act)
    result = _invoke(machine, monkeypatch, IN_WINDOW, "--install")
    assert result.exit_code == 3
    assert seen["revision"] == HEAD
    assert seen["install_root"] == machine.npm_root  # type: ignore[attr-defined]
