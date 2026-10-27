"""The membership seed reproduces today's hard-coded populations exactly.

Delete the comparison for each list as the lane that held it is moved onto the member record.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from fork_harden.rules import FORKS
from orchestrator.services.membership.changes import MemberChange, MemberProposalBody
from revision_watcher.subjects import SUBJECTS
from sds_members import populations
from sds_members.client import SERVICES, App, Member

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "docs/operations/membership-seed.toml"
SWEEP = ROOT / "scripts/run-activation-sweep.sh"


def _body() -> MemberProposalBody:
    return MemberProposalBody.model_validate(tomllib.loads(SEED.read_text()))


def _member(change: MemberChange) -> Member:
    grants = {service: "not_assessed" for service in SERVICES}
    grants.update({service: grant.state for service, grant in change.grants.items()})
    return Member(
        repository=change.repository,
        owner=change.owner or "",
        state=change.state or "",
        working_copy=change.working_copy,
        machine_local=bool(change.machine_local),
        apps=tuple(App(a.name, a.health_url, a.branch) for a in change.apps or ()),
        grants=grants,
    )


def _members() -> list[Member]:
    return [_member(change) for change in _body().changes]


def _shell_array(name: str) -> list[str]:
    """The directory names in one `NAME=( ... )` array of the activation sweep."""
    block = re.search(rf"^{name}=\((.*?)^\)", SWEEP.read_text(), re.S | re.M)
    assert block, name
    return re.findall(r'"\$HOME/Projects/([^"]+)"', block.group(1))


def test_seed_parses_and_matches_todays_lists() -> None:
    body = _body()
    members = _members()
    assert body.seeded
    assert len(members) == 13
    assert len({m.repository for m in members}) == 13
    assert all(m.owner == "devon" and m.state == "member" for m in members)

    assert sorted(populations.working_copies(members)) == sorted(_shell_array("CHECKOUTS"))
    assert sorted(populations.bindable(members)) == sorted(_shell_array("BINDABLE"))
    seeded = sorted(
        (r, a.name, a.health_url, a.branch) for r, a in populations.app_subjects(members)
    )
    assert seeded == sorted((s.repository, s.name, s.health_url, s.branch) for s in SUBJECTS)
    assert set(populations.granted(members, "security_patches")) >= {f.repository for f in FORKS}


def test_every_named_grant_states_its_source() -> None:
    for change in _body().changes:
        for grant in change.grants.values():
            assert grant.state in {"granted", "remediating"}
            assert grant.reason.strip()
