"""The Mac's conformance facts in the lane's container (ADR-0059 Amendment 13, D-62 C).

What the driver does with `--conformance-facts`: which members read the event, when a bump is held
before its decomposition and with which keyed alert, and the files it writes under the pass's
`HOME` so project-standards' matrix gives the cells the Mac's own inputs would. The selection the
file carries is `orchestrator.factory_chain_read.mac_conformance`'s, so the facts here are built
through it, from events shaped as security-standards #101's adapter appends them.

The matrix-equivalence tests run project-standards' own readers (`portfolio.checkers.check_infra`
and `portfolio.wiring.check_required_checks`) from a local checkout, `PROJECT_STANDARDS_SRC` or
`~/Projects/project-standards/src`; project-standards isn't in CI, so there they skip, and
`test_the_written_files_are_the_shapes_the_matrix_reads` holds the files' exact form instead.
"""

from __future__ import annotations

import importlib
import json
import os
import plistlib
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import httpx
import pytest

from bump_driver import cli, conformance
from bump_driver.alerts import CONFORMANCE_FACTS_KEY, conformance_wiring_key, sync_alerts
from bump_driver.conformance import (
    MATRIX_MAX_AGE_HOURS,
    MAX_REPORT_AGE_HOURS,
    Facts,
    declared,
    load,
    materialize,
)
from orchestrator.factory_chain_read import mac_conformance
from tests.bump_driver.test_alerts import Hub, _sent
from tests.bump_driver.test_pass import (
    GRANTED,
    RECORD,
    REPOSITORY,
    FakeGitHub,
    FakeOrchestrator,
    Generator,
    _results,
)
from tests.cli.test_factory_chain_read_conformance import WIRING, proposal, readable, unreadable

NOW = datetime(2026, 11, 18, 12, 0, 0, tzinfo=UTC)
GENERATED = "2026-11-18T08:00:39Z"
CHANGE_MANAGER_UUID = "re45tafypao3nly3qa9a79dp"


def _facts(*events: dict[str, Any], now: datetime = NOW) -> Facts:
    """The driver's reading of a verified walk over `events`, in `seq` order, judged at `now`."""
    selection = mac_conformance(list(enumerate(events, start=1)), now)
    return Facts(selection["event"], selection.get("reason") or "", lambda: now)


def _manifest(checkout: Path, front: str) -> Path:
    checkout.mkdir(parents=True, exist_ok=True)
    (checkout / "PROJECT.md").write_text(f"---\n{front}\n---\n\n## Backlog\n")
    return checkout


INFRA_MEMBER = """name: change-manager
foundation_contract: 1
applicable_standards:
  project: '1.0'
  code: '1.1'
  infra: null
required_checks:
- id: quality
  executor: github-actions:quality.yml
- id: change-window
  executor: launchagent:com.devon.change-window
coolify_resources:
- change-manager
- re45tafypao3nly3qa9a79dp"""
WIRING_MEMBER = """name: security-standards
applicable_standards: [project, code, security]
required_checks:
- id: scan-gate
  executor: hook:bws-scan-gate.sh
- id: scan
  executor: launchagent:com.devon.security-scan"""
PLAIN_MEMBER = """name: orchestrator
applicable_standards: [project, code, security]
required_checks:
- id: quality
  executor: github-actions:quality.yml"""


# ---- which members read the event ------------------------------------------------------------


def test_a_member_declaring_infra_or_a_launchagent_or_hook_check_reads_the_event(
    tmp_path: Path,
) -> None:
    assert declared(_manifest(tmp_path / "cm", INFRA_MEMBER)) == conformance.Declared(
        True, ("launchagent:com.devon.change-window",)
    )
    assert declared(_manifest(tmp_path / "ss", WIRING_MEMBER)) == conformance.Declared(
        False, ("hook:bws-scan-gate.sh", "launchagent:com.devon.security-scan")
    )
    plain = declared(_manifest(tmp_path / "o", PLAIN_MEMBER))
    assert plain == conformance.Declared(False, ()) and not plain.reads_event


@pytest.mark.parametrize(
    "front", ["applicable_standards: [infra]", "applicable_standards: {infra: null}"]
)
def test_infra_is_read_from_a_list_or_a_mapping(tmp_path: Path, front: str) -> None:
    assert declared(_manifest(tmp_path, front)).infra


@pytest.mark.parametrize(
    "text", [None, "no frontmatter here", "---\n: [unclosed\n---\n", "---\n- a\n---\n"]
)
def test_a_member_with_no_readable_declaration_reads_nothing(
    tmp_path: Path, text: str | None
) -> None:
    """The matrix reads such a member as `unknown` on both hosts; holding it would hide that."""
    if text is not None:
        (tmp_path / "PROJECT.md").write_text(text)
    assert not declared(tmp_path).reads_event


# ---- when the facts are unusable -------------------------------------------------------------


def test_a_fresh_readable_report_with_every_instance_ok_is_usable() -> None:
    facts = _facts(readable(GENERATED))
    assert facts.unusable() is None
    assert facts.summary() == f"usable: seq 1, report {GENERATED}"


@pytest.mark.parametrize(
    ("facts", "reason"),
    [
        (_facts(), "no mac_conformance.reported event in the chain"),
        (_facts(readable(instances={"prod": True, "dev": False})), "dev not ok"),
        (_facts(readable(GENERATED), unreadable()), "report was unreadable (report is not JSON)"),
        (_facts(readable(result="unknown")), "the result not ok"),
        (
            _facts(readable(GENERATED), now=datetime(2026, 11, 19, 19, 1, tzinfo=UTC)),
            "is 35.0 hours old, past 35",
        ),
    ],
    ids=["missing", "instance-not-ok", "unreadable", "unknown-result", "stale"],
)
def test_each_unusable_reading_names_its_reason(facts: Facts, reason: str) -> None:
    problem = facts.unusable()
    assert problem is not None and reason in problem


def test_the_age_is_judged_when_asked_not_when_loaded() -> None:
    moments = iter([NOW, NOW + timedelta(hours=MAX_REPORT_AGE_HOURS)])
    selection = mac_conformance([(1, readable(GENERATED))], NOW)
    facts = Facts(selection["event"], "", lambda: next(moments))
    assert facts.unusable() is None
    assert facts.unusable() is not None


def test_the_drivers_bound_is_inside_the_matrixs() -> None:
    """The matrix measures again when the generator runs, later than the driver's check, so the
    driver's bound must be strictly inside it: a report the driver passes is never stale there."""
    assert MAX_REPORT_AGE_HOURS < MATRIX_MAX_AGE_HOURS == 36
    portfolio = _project_standards()
    if portfolio is not None:
        assert portfolio["config"].INFRA_MAX_AGE_H == MATRIX_MAX_AGE_HOURS


@pytest.mark.parametrize(
    ("feed", "reason"),
    [
        (
            {"verified": False, "summary": "outcome=broken seq=4 exit=2"},
            "did not verify (outcome=broken seq=4 exit=2)",
        ),
        ({"verified": "yes"}, "did not verify (no record)"),
        ([], "did not verify (no record)"),
        ({"verified": True}, "carried no mac_conformance selection"),
        (
            {"verified": True, "mac_conformance": {"event": None, "reason": "seqs 1, 2 share"}},
            "seqs 1, 2 share",
        ),
    ],
)
def test_a_file_from_a_walk_that_did_not_verify_or_carried_no_event_is_unusable(
    tmp_path: Path, feed: Any, reason: str
) -> None:
    path = tmp_path / "facts.json"
    path.write_text(json.dumps(feed))
    problem = load(path, lambda: NOW).unusable()
    assert problem is not None and reason in problem


def test_an_unreadable_file_is_unusable(tmp_path: Path) -> None:
    missing = load(tmp_path / "absent.json", lambda: NOW).unusable()
    (tmp_path / "bad.json").write_text("{")
    bad = load(tmp_path / "bad.json", lambda: NOW).unusable()
    assert missing == "the chain's facts file is unreadable (FileNotFoundError)"
    assert bad == "the chain's facts file is unreadable (JSONDecodeError)"


def test_a_verified_files_event_is_read(tmp_path: Path) -> None:
    path = tmp_path / "facts.json"
    selection = mac_conformance([(1, readable(GENERATED))], NOW)
    path.write_text(json.dumps({"verified": True, "summary": "s", "mac_conformance": selection}))
    assert load(path, lambda: NOW).event == selection["event"]


# ---- the hold, in a pass ---------------------------------------------------------------------


class Rig:
    """One pass over the fakes in `test_pass`, with the member's mirror holding a manifest."""

    def __init__(self, tmp_path: Path, front: str) -> None:
        self.orch = FakeOrchestrator()
        self.github = FakeGitHub()
        self.generator = Generator()
        self.checkout = _manifest(tmp_path / "mirror", front)

    def mirror(self, repository: str) -> Path:
        self.generator.mirror(repository)
        return self.checkout

    def run(self, facts: Facts | None, *, dry_run: bool = False) -> list[cli.Outcome]:
        return cli.run_pass(
            [RECORD],
            cast(cli.Orchestrator, self.orch),
            cast(cli.GitHub, self.github),
            GRANTED,
            self.mirror,
            self.generator.generate,
            dry_run=dry_run,
            now=NOW,
            facts=facts,
        )


UNUSABLE = {
    "missing": _facts(),
    "verify-failed": Facts(None, "the factory-events chain did not verify (x)", lambda: NOW),
    "unreadable": _facts(readable(GENERATED), unreadable()),
    "instance-not-ok": _facts(readable(instances={"prod": False})),
    "stale": _facts(readable("2026-11-16T08:00:39Z")),
}


@pytest.mark.parametrize("front", [INFRA_MEMBER, WIRING_MEMBER], ids=["infra", "wiring"])
@pytest.mark.parametrize("case", sorted(UNUSABLE))
def test_a_bump_that_reads_unusable_facts_is_held_and_proposes_nothing(
    tmp_path: Path, front: str, case: str
) -> None:
    rig = Rig(tmp_path, front)
    outcomes = rig.run(UNUSABLE[case])
    assert _results(outcomes) == ["held"]
    [held] = outcomes
    assert held.key == CONFORMANCE_FACTS_KEY and held.repository == REPOSITORY
    assert rig.generator.calls == [] and rig.orch.posted == []
    assert cli.exit_code(outcomes) == cli.FOUND


@pytest.mark.parametrize("case", sorted(UNUSABLE))
def test_a_member_that_reads_nothing_from_the_event_is_not_held(tmp_path: Path, case: str) -> None:
    rig = Rig(tmp_path, PLAIN_MEMBER)
    assert _results(rig.run(UNUSABLE[case])) == ["approved"]
    assert len(rig.orch.posted) == 1


@pytest.mark.parametrize("front", [INFRA_MEMBER, WIRING_MEMBER, PLAIN_MEMBER])
def test_usable_facts_hold_nothing(tmp_path: Path, front: str) -> None:
    rig = Rig(tmp_path, front)
    assert _results(rig.run(_facts(readable(GENERATED)))) == ["approved"]
    assert rig.generator.calls[0][3] == rig.checkout


def test_on_the_mac_no_facts_hold_nothing(tmp_path: Path) -> None:
    """The Mac passes no facts: its claim reads its own files, and nothing is held for the chain."""
    rig = Rig(tmp_path, INFRA_MEMBER)
    assert _results(rig.run(None)) == ["approved"]


def test_a_declared_wiring_entry_the_event_doesnt_list_holds_with_the_repositorys_key(
    tmp_path: Path,
) -> None:
    """The container would read it as not wired, a claim that isn't green, which only a person
    superseding the approved decomposition clears; holding asks for the WIRING set instead."""
    rig = Rig(tmp_path, WIRING_MEMBER)
    listed = [item for item in WIRING if item["executor"] != "launchagent:com.devon.security-scan"]
    [held] = rig.run(_facts(readable(GENERATED, wiring=listed)))
    assert held.result == "held" and held.key == conformance_wiring_key(REPOSITORY)
    assert "launchagent:com.devon.security-scan" in held.detail
    assert rig.generator.calls == []


def test_an_absent_entry_is_not_held_it_reads_as_the_mac_reads_it(tmp_path: Path) -> None:
    """`absent` is the Mac's own fact: the matrix reads `violation` on both hosts."""
    rig = Rig(tmp_path, WIRING_MEMBER)
    wiring = [{**item, "status": "absent"} for item in WIRING]
    assert _results(rig.run(_facts(readable(GENERATED, wiring=wiring)))) == ["approved"]


def test_a_dry_run_reads_no_mirror_and_reports_what_it_would_propose(tmp_path: Path) -> None:
    rig = Rig(tmp_path, INFRA_MEMBER)
    assert _results(rig.run(UNUSABLE["missing"], dry_run=True)) == ["would-propose"]
    assert rig.generator.mirrors == []


def test_a_bump_with_a_proposal_already_is_not_held(tmp_path: Path) -> None:
    rig = Rig(tmp_path, INFRA_MEMBER)
    rig.orch.proposal_rows = [{"state": "proposed", "proposed_by": "devon"}]
    assert _results(rig.run(UNUSABLE["missing"])) == ["awaiting-approval"]


def test_every_finding_word_is_keyed() -> None:
    assert "held" in cli.FINDINGS


# ---- the alert -------------------------------------------------------------------------------


def _sync(hub: Hub, tmp_path: Path, outcomes: list[cli.Outcome], facts: Facts | None) -> None:
    kept = frozenset({CONFORMANCE_FACTS_KEY}) if facts and facts.unusable() else frozenset()
    problem = sync_alerts(
        outcomes,
        token="t",
        state_dir=tmp_path / "state",
        kept=kept,
        transport=httpx.MockTransport(hub),
    )
    assert problem is None


def test_the_facts_alert_fires_while_held_and_resolves_on_the_first_usable_pass(
    tmp_path: Path,
) -> None:
    hub = Hub()
    rig = Rig(tmp_path, INFRA_MEMBER)
    stale = UNUSABLE["stale"]
    _sync(hub, tmp_path, rig.run(stale), stale)
    _sync(hub, tmp_path, rig.run(stale), stale)
    assert _sent(hub) == [(CONFORMANCE_FACTS_KEY, "firing")]
    usable = _facts(readable(GENERATED))
    _sync(hub, tmp_path, rig.run(usable), usable)
    assert _sent(hub) == [(CONFORMANCE_FACTS_KEY, "firing"), (CONFORMANCE_FACTS_KEY, "resolved")]


def test_a_pass_that_holds_nothing_while_the_facts_are_unusable_keeps_the_alert(
    tmp_path: Path,
) -> None:
    """With the held bump unread this pass, or decomposed by a person, the facts are still unusable,
    so the alert stays until a pass reads usable facts."""
    hub = Hub()
    rig = Rig(tmp_path, INFRA_MEMBER)
    stale = UNUSABLE["stale"]
    _sync(hub, tmp_path, rig.run(stale), stale)
    _sync(hub, tmp_path, [], stale)
    _sync(hub, tmp_path, [cli.Outcome("record 170", "unread", "503", REPOSITORY)], stale)
    assert _sent(hub) == [(CONFORMANCE_FACTS_KEY, "firing")]
    _sync(hub, tmp_path, [], _facts(readable(GENERATED)))
    assert _sent(hub)[-1] == (CONFORMANCE_FACTS_KEY, "resolved")


def test_the_silent_case_unusable_facts_no_bump_needs_raise_nothing(tmp_path: Path) -> None:
    hub = Hub()
    rig = Rig(tmp_path, PLAIN_MEMBER)
    stale = UNUSABLE["stale"]
    _sync(hub, tmp_path, rig.run(stale), stale)
    assert _sent(hub) == []


def test_the_wiring_alert_is_held_while_its_repository_is_unread(tmp_path: Path) -> None:
    hub = Hub()
    key = conformance_wiring_key(REPOSITORY)
    held = cli.Outcome("record 170", "held", "x", REPOSITORY, key)
    usable = _facts(readable(GENERATED))
    _sync(hub, tmp_path, [held], usable)
    _sync(hub, tmp_path, [cli.Outcome("record 170", "unread", "503", REPOSITORY)], usable)
    assert _sent(hub) == [(key, "firing")]
    _sync(hub, tmp_path, [], usable)
    assert _sent(hub) == [(key, "firing"), (key, "resolved")]


def test_the_keys_are_named() -> None:
    assert CONFORMANCE_FACTS_KEY == "bump-conformance-facts"
    assert conformance_wiring_key(REPOSITORY) == "bump-conformance-wiring:alobarquest/orchestrator"


# ---- the files written under HOME ------------------------------------------------------------


def test_the_written_files_are_the_shapes_the_matrix_reads(tmp_path: Path) -> None:
    facts = _facts(
        readable(
            GENERATED,
            proposals=[
                proposal("prod", CHANGE_MANAGER_UUID, "change-manager"),
                proposal("dev", None, None, "p-2"),
            ],
        )
    )
    assert facts.event is not None
    materialize(facts.event, tmp_path)
    assert sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file()) == [
        ".claude/settings.json",
        "Library/LaunchAgents/com.devon.change-window.plist",
        "Library/LaunchAgents/com.devon.factory-events.plist",
        "Library/LaunchAgents/com.devon.security-scan.plist",
        "infra-drift/reports/2026-11-18.json",
    ]
    assert json.loads((tmp_path / "infra-drift/reports/2026-11-18.json").read_text()) == {
        "generated_at": GENERATED,
        "instances": {
            "prod": {
                "ok": True,
                "proposals": [
                    {"id": "p-1", "target": {"uuid": CHANGE_MANAGER_UUID, "name": "change-manager"}}
                ],
            },
            "dev": {
                "ok": True,
                "proposals": [{"id": "p-2", "target": {"uuid": None, "name": None}}],
            },
        },
    }
    assert json.loads((tmp_path / ".claude/settings.json").read_text()) == {
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "bws-scan-gate.sh"}]}]}
    }


def test_a_file_already_under_home_is_never_overwritten(tmp_path: Path) -> None:
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text("the real one")
    facts = _facts(readable(GENERATED))
    assert facts.event is not None
    with pytest.raises(FileExistsError):
        materialize(facts.event, tmp_path)
    assert (tmp_path / ".claude" / "settings.json").read_text() == "the real one"


def _project_standards() -> dict[str, ModuleType] | None:
    root = Path(
        os.environ.get("PROJECT_STANDARDS_SRC", Path.home() / "Projects/project-standards/src")
    )
    if not (root / "portfolio" / "checkers.py").is_file():
        return None
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return {
        name: importlib.import_module(f"portfolio.{name}")
        for name in ("config", "checkers", "wiring", "contract", "manifest")
    }


@pytest.fixture
def portfolio() -> dict[str, ModuleType]:
    modules = _project_standards()
    if modules is None:
        pytest.skip("project-standards isn't checked out here (it isn't in CI)")
    return modules


def _mac_inputs(home: Path, *, ok: bool = True) -> None:
    """The Mac's own inputs, with the free text the event never carries."""
    reports = home / "infra-drift" / "reports"
    reports.mkdir(parents=True)
    (reports / "2026-11-17.json").write_text(
        json.dumps({"generated_at": "2026-11-17T08:00:12Z", "instances": {}})
    )
    (reports / "2026-11-18.json").write_text(
        json.dumps(
            {
                "generated_at": GENERATED,
                "instances": {
                    "prod": {
                        "ok": ok,
                        "errors": [],
                        "proposals": [
                            {
                                "id": "p-1",
                                "description": "change-manager's health check is off",
                                "reasoning": "a free-text reason",
                                "target": {"uuid": CHANGE_MANAGER_UUID, "name": "change-manager"},
                            }
                        ],
                    },
                    "dev": {"ok": True, "proposals": []},
                },
            }
        )
    )
    agents = home / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    for label in ("com.devon.change-window", "com.devon.factory-events", "com.devon.security-scan"):
        (agents / f"{label}.plist").write_bytes(plistlib.dumps({"Label": label}))
    (home / ".claude").mkdir()
    (home / ".claude" / "settings.json").write_text(
        json.dumps(
            {
                "env": {"X": "y"},
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "bash ~/.claude/hooks/bws-scan-gate.sh",
                                }
                            ]
                        }
                    ],
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [{"type": "command", "command": "~/.claude/hooks/other.sh"}],
                        }
                    ],
                },
            }
        )
    )


def _event_of_mac_inputs(ok: bool = True) -> dict[str, Any]:
    """What the producer appends for `_mac_inputs` (its "The event's layout")."""
    return readable(
        GENERATED,
        instances={"prod": ok, "dev": True},
        proposals=[proposal("prod", CHANGE_MANAGER_UUID, "change-manager")],
    )


def _cells(portfolio: dict[str, ModuleType], home: Path, members: dict[str, Path]) -> dict:
    """project-standards' `infra` and `checks` cells for each member, with `home`'s inputs."""
    env = {
        "INFRADRIFT_REPORT_DIR": str(home / "infra-drift" / "reports"),
        "LAUNCHAGENTS_DIR": str(home / "Library" / "LaunchAgents"),
        "CLAUDE_SETTINGS_JSON": str(home / ".claude" / "settings.json"),
    }
    saved = {name: os.environ.get(name) for name in env}
    os.environ.update(env)
    try:
        resources = {"change-manager": ["change-manager", CHANGE_MANAGER_UUID], "brain": ["brain"]}
        infra = portfolio["checkers"].check_infra(resources, NOW)
        cells: dict[str, Any] = {
            f"infra:{repo}": (result.status, [d.get("id") for d in result.details])
            for repo, result in infra.items()
        }
        for name, checkout in members.items():
            front = portfolio["manifest"].read_manifest(checkout).frontmatter
            entries = portfolio["contract"].parse_contract(front).required_checks
            result = portfolio["wiring"].check_required_checks(checkout, entries, False)
            cells[f"checks:{name}"] = (result.status, [d.get("id") for d in result.details])
        return cells
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _members(tmp_path: Path) -> dict[str, Path]:
    change_manager = _manifest(tmp_path / "mirrors" / "cm", INFRA_MEMBER)
    workflows = change_manager / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "quality.yml").write_text("jobs: {quality: {}}\n")
    return {
        "change-manager": change_manager,
        "security-standards": _manifest(tmp_path / "mirrors" / "ss", WIRING_MEMBER),
        "project-standards": _manifest(
            tmp_path / "mirrors" / "ps",
            "required_checks:\n- id: scan\n  executor: launchagent:com.devon.portfolio-scan",
        ),
    }


def test_the_written_files_give_the_cells_the_macs_inputs_give(
    tmp_path: Path, portfolio: dict[str, ModuleType]
) -> None:
    """The matrix's own readers, on the Mac's inputs and on the files the container writes from
    the event, agree for every member that reads them: status and finding ids."""
    readers = portfolio
    members = _members(tmp_path)
    mac = tmp_path / "mac"
    _mac_inputs(mac)
    container = tmp_path / "container"
    container.mkdir()
    facts = _facts(_event_of_mac_inputs())
    assert facts.unusable() is None and facts.event is not None
    materialize(facts.event, container)

    on_the_mac = _cells(readers, mac, members)
    in_the_container = _cells(readers, container, members)

    assert in_the_container == on_the_mac
    # The fixture exercises each outcome: a matched proposal, a clean member, wired and not wired.
    assert on_the_mac["infra:change-manager"] == ("violation", ["p-1"])
    assert on_the_mac["infra:brain"] == ("pass", [])
    assert on_the_mac["checks:change-manager"] == ("pass", [])
    assert on_the_mac["checks:security-standards"] == ("pass", [])
    assert on_the_mac["checks:project-standards"] == ("violation", ["checks.not-wired"])


def test_where_the_mac_reads_unknown_the_container_holds(
    tmp_path: Path, portfolio: dict[str, ModuleType]
) -> None:
    """An instance not ok is `unknown` on the Mac; the container proposes nothing instead."""
    readers = portfolio
    mac = tmp_path / "mac"
    _mac_inputs(mac, ok=False)
    assert _cells(readers, mac, {})["infra:change-manager"][0] == "unknown"
    facts = _facts(_event_of_mac_inputs(ok=False))
    rig = Rig(tmp_path, INFRA_MEMBER)
    assert _results(rig.run(facts)) == ["held"]


# ---- the pass's own handling of the file -----------------------------------------------------


def _pass_rig(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Callable[..., int], list[Any], Path]:
    stub: Any = SimpleNamespace(close=lambda: None, fresh=None, work_records=lambda: [])
    for name in ("ChangeManagerReader", "Orchestrator", "GitHub", "Mirrors", "Generator"):
        monkeypatch.setattr(cli, name, lambda *a, **k: stub)
    monkeypatch.setattr(cli, "read_members", lambda **k: {})
    monkeypatch.setattr(cli, "granted_repositories", lambda *a: [])
    monkeypatch.setattr(cli, "factory_binary", lambda home: "factory")
    monkeypatch.setattr(cli, "_generator_environment", lambda *a: {})
    handed: list[Any] = []
    monkeypatch.setattr(cli, "run_pass", lambda *a, **k: handed.append(k["facts"]) or [])
    monkeypatch.setattr(cli, "sync_alerts", lambda *a, **k: None)
    environ = {
        "ORCHESTRATOR_API_TOKEN": "x",
        "ORCHESTRATOR_API_CREDENTIAL_KEY_ID": "x",
        "CHANGE_MANAGER_TOKEN": "x",
        "BUMP_DRIVER_GITHUB_TOKEN": "x",
        cli.TOKEN_ENV: "x",
    }
    home = tmp_path / "home"
    home.mkdir()

    def run(facts: Facts | None, *, dry_run: bool = False) -> int:
        return cli._pass(environ, tmp_path / "state", home, facts, dry_run=dry_run)

    return run, handed, home


def test_an_acting_pass_writes_usable_facts_under_home_before_the_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run, handed, home = _pass_rig(tmp_path, monkeypatch)
    facts = _facts(readable(GENERATED))
    assert run(facts) == 0
    assert handed == [facts]
    assert (home / "infra-drift" / "reports" / "2026-11-18.json").is_file()
    assert f"conformance facts: usable: seq 1, report {GENERATED}" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("facts", "dry_run"),
    [(None, False), (UNUSABLE["stale"], False), (_facts(readable(GENERATED)), True)],
    ids=["mac", "unusable", "dry-run"],
)
def test_nothing_is_written_on_the_mac_for_unusable_facts_or_on_a_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, facts: Facts | None, dry_run: bool
) -> None:
    run, handed, home = _pass_rig(tmp_path, monkeypatch)
    assert run(facts, dry_run=dry_run) == 0
    assert handed == [facts]
    assert list(home.iterdir()) == []


def test_a_home_that_already_holds_the_files_fails_the_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run, handed, home = _pass_rig(tmp_path, monkeypatch)
    (home / ".claude").mkdir()
    (home / ".claude" / "settings.json").write_text("{}")
    assert run(_facts(readable(GENERATED))) == 1
    assert handed == []
    assert "could not be written under HOME: FileExistsError" in capsys.readouterr().err


def test_main_reads_the_file_only_when_it_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On the Mac the launcher names no file, so the pass gets no facts."""
    seen: list[Any] = []
    monkeypatch.setattr(
        cli, "_pass", lambda environ, state, home, facts, **k: seen.append(facts) or 0
    )
    for name in (
        "ORCHESTRATOR_API_TOKEN",
        "ORCHESTRATOR_API_CREDENTIAL_KEY_ID",
        "CHANGE_MANAGER_TOKEN",
        "BUMP_DRIVER_GITHUB_TOKEN",
    ):
        monkeypatch.setenv(name, "x")
    assert cli.main(["--dry-run"]) == 0
    path = tmp_path / "facts.json"
    path.write_text(json.dumps({"verified": False, "summary": "s"}))
    assert cli.main(["--dry-run", "--conformance-facts", str(path)]) == 0
    assert seen[0] is None
    assert isinstance(seen[1], Facts) and seen[1].unusable() is not None
