"""AC-010: the drills must stay safe, and must stay honest.

A recovery drill runs destructive scenarios -- it kills processes, expires leases, and drives a
unit through failure. Two properties keep that from being dangerous or dishonest, and neither
survives on good intentions:

* SAFE: every drill owns a throwaway Postgres container and a throwaway database, and tears them
  down. It must never touch production, never touch `orchestrator_test` (which the test fixtures
  drop and recreate), and never merge or push anything.
* HONEST: a drill that reaches around the public API into the service layer or straight into SQL
  can pass over a production path that never runs. That is not hypothetical -- the PR-binding
  writers had no production caller at all, and ten green unit tests said nothing about it.

These tests pin both. They are cheap, and they fail loudly the moment a drill starts lying.
"""

import re
import stat
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
DRILLS = sorted(SCRIPTS.glob("drill-*.sh"))
HARNESS = SCRIPTS / "drill_common.sh"

# Actions that would reach a system the drill does not own.
#
# Deliberately about ACTIONS, not vocabulary. An earlier version of this list banned the string
# "coolify" and fired on `deployer: "coolify"` inside a deployment-observation payload -- which is
# data describing a deploy that never happened, in a database that is thrown away seconds later.
# Banning words teaches the next author to spell around the guard. What actually matters is that a
# drill makes no outbound call and lands no change, and that is what is pinned here and below.
FORBIDDEN = {
    "orchestrator_test": "the test database -- the fixtures drop and recreate it",
    "gh pr merge": "merging is Devon's gate, never a drill's",
    "gh pr create": "a drill opens nothing",
    "git push": "a drill pushes nothing",
    "sudo": "a drill needs no privilege",
}


def test_there_are_four_drills() -> None:
    """AC-010 promises four scripted drills. A drill that is deleted must be noticed."""
    assert len(DRILLS) == 4, [drill.name for drill in DRILLS]


@pytest.mark.parametrize("drill", DRILLS, ids=lambda p: p.name)
def test_a_drill_is_executable_and_fails_loudly(drill: Path) -> None:
    source = drill.read_text()
    assert source.startswith("#!/bin/bash"), "a drill must declare its shell"
    assert drill.stat().st_mode & stat.S_IXUSR, f"{drill.name} is not executable"
    # Without `set -e` a failed setup step scrolls past and the drill goes on to "pass".
    assert "set -euo pipefail" in HARNESS.read_text()
    assert "summarize " in source, "a drill must report a verdict, not just run"


@pytest.mark.parametrize("drill", DRILLS, ids=lambda p: p.name)
def test_a_drill_touches_nothing_it_does_not_own(drill: Path) -> None:
    source = drill.read_text()
    for needle, why in FORBIDDEN.items():
        assert needle not in source, f"{drill.name} references {needle} -- {why}"


def test_no_drill_can_call_anything_but_its_own_throwaway_server() -> None:
    """The real containment property, stated as an action rather than a vocabulary.

    Every HTTP call a drill makes must target `$API_URL`, which the harness pins to
    127.0.0.1 on a port it started itself. A drill that could reach a hostname could reach
    production -- and a "recovery drill" that mutates production is not a drill, it is an outage.
    """
    for source_file in [HARNESS, *DRILLS]:
        for call in re.findall(r"curl[^\n|]*", source_file.read_text()):
            targets = re.findall(r'"?(https?://[^\s"\']+)', call)
            for target in targets:
                assert target.startswith("$API_URL") or target.startswith("http://127.0.0.1"), (
                    f"{source_file.name} curls {target} -- a drill may only call the server "
                    "it started itself"
                )


def test_the_harness_tears_itself_down() -> None:
    source = HARNESS.read_text()
    assert "trap 'cleanup' EXIT" in source, "a crashed drill must still remove its container"
    assert "docker rm -f" in source
    assert "--keep" in source, "an operator must be able to keep the scratch to inspect a failure"


def test_the_harness_refuses_to_run_against_the_test_database() -> None:
    """The drill database is `orchestrator_drill`. Pointing a drill at `orchestrator_test` would
    erase a concurrent test run's database, and the failure would look like a flaky test."""
    source = HARNESS.read_text()
    assert 'DRILL_DB="orchestrator_drill"' in source
    assert "orchestrator_test" not in source.replace("orchestrator_test, which", "").replace(
        "at it would erase", ""
    )


@pytest.mark.parametrize("drill", DRILLS, ids=lambda p: p.name)
def test_a_drill_changes_state_only_through_the_public_api(drill: Path) -> None:
    """THE honesty guard.

    Every state change must go through `api ...` -- the same HTTP surface an operator or a runner
    uses. SQL is allowed only to READ (assertions may look at canonical state) and, in the two
    documented cases, to age a lease: LEASE_DURATION is a hardcoded 15 minutes with no override,
    so a drill cannot make one lapse through any public surface without waiting 15 real minutes.
    That is environment setup standing in for elapsed wall clock -- the same latitude the repo
    already grants its protocol smoke tests -- and it is confined to `expire_lease`.

    A drill that could INSERT or UPDATE its way to a state would prove nothing about the code
    that has to produce that state in production.
    """
    source = drill.read_text()
    mutations = re.findall(
        r"^\s*(?:scratch_sql|docker exec).*\b(INSERT|UPDATE|DELETE|TRUNCATE|ALTER|DROP)\b",
        source,
        re.IGNORECASE | re.MULTILINE,
    )
    assert not mutations, (
        f"{drill.name} mutates state with SQL ({mutations}). Drive it through the API instead; "
        "a drill that writes its own preconditions cannot vouch for the code that must write them."
    )


def test_only_the_lease_helper_may_write_sql() -> None:
    """The single exception, named and contained.

    `expire_lease` is the only SQL writer in the harness. If a second one appears, it must be a
    deliberate act with the same justification -- so this test makes adding one a decision rather
    than a habit.
    """
    source = HARNESS.read_text()
    writers = re.findall(
        r"^(\w+)\(\) \{(?:[^}]*?)(?:UPDATE|INSERT|DELETE)", source, re.MULTILINE | re.DOTALL
    )
    assert writers == ["expire_lease"], writers
