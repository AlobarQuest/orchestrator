"""The membership seed, applied history, still matches the code lists it was taken from.

`docs/operations/membership-seed.toml` was staged once to fill the record from the lanes' hard-coded
populations (ADR-0058, phase 1). The lanes read the record, so the comparisons here pin what
the seed recorded; a list that changes on purpose is a reason to edit or delete its comparison.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from deploy_watcher.workflows import ROLLOUT_WORKFLOWS
from fork_harden.rules import FORKS
from orchestrator.services.membership.changes import MemberChange, MemberProposalBody
from rotation_worker.registry import REGISTRY_REPOSITORIES
from sds_members import populations
from sds_members.client import SERVICES, App, Member
from tool_installer.tools import TOOLS

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "docs/operations/membership-seed.toml"


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


def test_seed_parses_and_matches_todays_lists() -> None:
    body = _body()
    members = _members()
    assert body.seeded
    assert len(members) == 13
    assert len({m.repository for m in members}) == 13
    assert all(m.owner == "devon" and m.state == "member" for m in members)

    assert set(populations.granted(members, "security_patches")) >= {f.repository for f in FORKS}


def test_every_named_grant_states_its_source() -> None:
    for change in _body().changes:
        for grant in change.grants.values():
            assert grant.state in {"granted", "remediating"}
            assert grant.reason.strip()


def test_seed_grants_match_their_code_sources() -> None:
    members = _members()

    def granted(service: str) -> set[str]:
        return {r.lower() for r in populations.granted(members, service)}

    assert {r.lower() for r in ROLLOUT_WORKFLOWS} | {
        t.repository.lower() for t in TOOLS
    } <= granted("rollouts")
    assert {r.lower() for r in REGISTRY_REPOSITORIES} <= granted("credential_rotation")
    forks = {
        f"alobarquest/{n}" for n in ("rtk", "claude-octopus", "rea", "n8n-as-code", "n8n-manager")
    }
    assert forks <= granted("dependency_updates") & granted("security_patches")
