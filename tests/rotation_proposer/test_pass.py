"""The whole pass, over a fake infraops and lifecycle and stubbed change-manager and orchestrator.

**THE TWO-PASS ORDER IS THE SUBJECT.** A pass that finds a credential due revises its standing
package to review, publishes it and proposes NOTHING; passes while the revision waits for Devon
write nothing at all; the pass after he approves it proposes, naming the observation the first pass
filed. Every claim is asserted by COUNTING acts -- lifecycle commands, observations posted,
proposals sent, commits published -- because the final state is identical for a producer that did
the right thing once and one that did it twice.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace

import httpx
import pytest

from bump_proposer import standing as shared
from bump_proposer.change_manager import ChangeManagerClient
from bump_proposer.cli import WORK_RECORD_FIELDS
from bump_proposer.orchestrator_client import OrchestratorClient
from rotation_proposer import cli
from rotation_proposer.cli import EXIT_FINDINGS, EXIT_OK, EXIT_UNUSABLE, run
from rotation_proposer.findings import Due, FindingsError, Unrecognised
from rotation_worker.registry import RegistryEntry, RegistryError
from sds_members.client import SERVICES, Member, MembersUnreadable
from tests.bump_proposer.test_pass import _Estate, _Spine
from tests.rotation_proposer.test_standing import DESTINATIONS, FakeLifecycle, write_package

CREDENTIAL = "openrouter-generic"
REQUESTED = Due(CREDENTIAL, "openrouter-key", "requested", "2026-10-07", "2026-10-07", True)
AGE = Due(CREDENTIAL, "openrouter-key", "age", "2026-10-08", "2026-10-08", True)


@dataclass(frozen=True)
class _Entry(RegistryEntry):
    """A registry row whose destinations the test states outright."""

    listed: list[str] = field(default_factory=list)

    @property
    def destinations(self) -> list[str]:
        return list(self.listed)


class _Rig:
    def __init__(self, root, lifecycle):
        self.root = root
        self.lifecycle = lifecycle
        self.estate = _Estate()
        self.spine = _Spine()
        self.due: list[Due] = [REQUESTED]
        self.unrecognised: list[Unrecognised] = []
        self.published: list[str] = []
        self.user_agents: set[str] = set()
        # Every `sync_alerts` call this pass made, as (outcomes, token, state_dir).
        self.synced: list[tuple[list, str, object]] = []
        # The registry's destinations for the credential; a list differing from the package's
        # is drift. `None` makes the registry unreadable.
        self.registry: list[str] | None = list(DESTINATIONS)
        # Each credential's registry class, where it isn't OpenRouter's.
        self.classes: dict[str, str] = {}
        self.registry_reads = 0
        # The repositories the member record grants `credential_rotation` on, or `None` for an
        # unreadable record; `member_reads` counts how often it was asked.
        self.granted: list[str] | None = ["AlobarQuest/infraops-mcp-server"]
        self.member_reads = 0

    def entry(self, credential_id: str) -> RegistryEntry:
        credential = {
            "id": credential_id,
            "class": self.classes.get(credential_id, "openrouter-key"),
            "bws_uuid": "11111111-1111-4111-8111-111111111111",
        }
        return _Entry(
            "AlobarQuest/infraops-mcp-server", "c" * 40, credential, list(self.registry or [])
        )

    @property
    def package_yaml(self):
        return self.root / "packages" / f"rotation-{CREDENTIAL}" / "package.yaml"

    def approve_by_name(self) -> None:
        """Devon's act, which this program never performs: the tip becomes approved."""
        text = self.package_yaml.read_text()
        self.package_yaml.write_text(text.replace("status: ready_for_review", "status: approved"))

    def acts(self) -> tuple[list[str], int, int, int]:
        return (
            [call[0] for call in self.lifecycle.calls],
            len(self.spine.posted),
            len(self.estate.proposals),
            len(self.published),
        )


@pytest.fixture
def rig(tmp_path, monkeypatch):
    write_package(tmp_path, occurrence="'2025-01-02-age'")
    fixture = tmp_path / "tests" / "fixtures"
    fixture.mkdir(parents=True)
    (fixture / "package_hashes.json").write_text("{}\n")
    monkeypatch.setenv("BUMP_PROPOSER_PACKAGES_CHECKOUT", str(tmp_path))
    monkeypatch.setenv("ROTATION_PROPOSER_CHANGE_MANAGER_TOKEN", "cm-propose")
    monkeypatch.setenv("ORCHESTRATOR_API_URL", "https://sds.example.net")
    monkeypatch.setenv("ORCHESTRATOR_API_CREDENTIAL_KEY_ID", "orchestrator-observer")
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "observer")
    monkeypatch.setenv("SDS_BRIDGE_ALERT_TOKEN", "sender")
    monkeypatch.setenv("ROTATION_PROPOSER_STATE_DIR", str(tmp_path / "state"))

    lifecycle = FakeLifecycle(tmp_path)
    monkeypatch.setattr(shared, "lifecycle", lifecycle)
    rig = _Rig(tmp_path, lifecycle)

    def recorded(handler):
        def wrapped(request: httpx.Request) -> httpx.Response:
            rig.user_agents.add(request.headers["user-agent"])
            return handler(request)

        return wrapped

    def synced(outcomes, *, token, state_dir, now):
        rig.synced.append((list(outcomes), token, state_dir))

    monkeypatch.setattr(cli, "sync_alerts", synced)
    monkeypatch.setattr(cli, "read_due", lambda: (list(rig.due), list(rig.unrecognised)))
    monkeypatch.setattr(cli, "default_mirror_root", lambda program: tmp_path / program)

    def registry_entries(_mirror):
        rig.registry_reads += 1
        if rig.registry is None:
            raise RegistryError(
                "AlobarQuest/infraops-mcp-server: .cred-consumers.toml doesn't parse"
            )
        return {item.credential_id: rig.entry(item.credential_id) for item in rig.due}

    monkeypatch.setattr(cli, "registry_entries", registry_entries)

    def read_members(**_kwargs):
        rig.member_reads += 1
        if rig.granted is None:
            raise MembersUnreadable("the member record is unreachable")
        return [
            Member(name, "devon", "member", None, False, (), dict.fromkeys(SERVICES, "granted"))
            for name in rig.granted
        ]

    monkeypatch.setattr(cli, "read_members", read_members)
    monkeypatch.setattr(cli, "require_clean", lambda root: None)
    monkeypatch.setattr(cli, "require_publishable", lambda root: None)

    def commit(package, root, *, generated=False):
        rig.published.append(package.package_id)
        return "c" * 40

    monkeypatch.setattr(cli, "commit", commit)
    monkeypatch.setattr(
        cli,
        "ChangeManagerClient",
        lambda token, *, base_url, user_agent: ChangeManagerClient(
            token,
            base_url=base_url,
            user_agent=user_agent,
            transport=httpx.MockTransport(recorded(rig.estate.handler)),
        ),
    )
    monkeypatch.setattr(
        cli,
        "open_client",
        lambda *, base_url, credential_key_id, token, user_agent: OrchestratorClient(
            base_url=base_url,
            credential_key_id=credential_key_id,
            token=token,
            user_agent=user_agent,
            transport=httpx.MockTransport(recorded(rig.spine.handler)),
        ),
    )
    return rig


def test_the_first_pass_revises_to_review_publishes_and_proposes_nothing(rig, capsys) -> None:
    """Kills: proposing before approval (the carry would refuse the record), approving (the fake
    lifecycle refuses it), and revising without observing first."""
    assert run(["--submit"]) == EXIT_OK
    assert rig.acts() == (["revise", "transition", "hash"], 1, 0, 1)
    assert "status: ready_for_review" in rig.package_yaml.read_text()
    assert "occurrence: '2026-10-07-requested'" in rig.package_yaml.read_text()
    assert "revised" in capsys.readouterr().out


def test_passes_while_the_revision_waits_for_devon_write_nothing(rig, capsys) -> None:
    run(["--submit"])
    before = rig.acts()
    capsys.readouterr()

    assert run(["--submit"]) == EXIT_OK
    assert rig.acts() == before
    assert "awaiting-approval" in capsys.readouterr().out


def test_the_pass_after_devon_approves_proposes_naming_the_first_observation(rig, capsys) -> None:
    """The proposal carries `bump_proposer`'s asserted fields exactly, the revision Devon
    approved, and the observation the FIRST pass filed -- the replay answers the same id."""
    run(["--submit"])
    first_observation = rig.spine.rows.copy()
    rig.approve_by_name()

    assert run(["--submit"]) == EXIT_OK
    assert rig.acts() == (["revise", "transition", "hash"], 2, 1, 1)
    proposal = rig.estate.proposals[0]
    assert tuple(proposal) == (*WORK_RECORD_FIELDS, "change_class")
    assert proposal["change_class"] == "credential-rotation"
    assert (proposal["package_id"], proposal["package_revision"], proposal["actor"]) == (
        f"rotation-{CREDENTIAL}",
        2,
        "rotation-proposer",
    )
    assert proposal["originating_observation_id"] in first_observation.values()
    assert rig.spine.rows == first_observation
    assert "2026-10-07-requested" in proposal["reasoning"]
    assert "proposed" in capsys.readouterr().out


def test_a_later_pass_replays_and_writes_no_new_revision(rig, capsys) -> None:
    run(["--submit"])
    rig.approve_by_name()
    run(["--submit"])
    capsys.readouterr()

    assert run(["--submit"]) == EXIT_OK
    lifecycle, _, proposals, published = rig.acts()
    assert (lifecycle, proposals, published) == (["revise", "transition", "hash"], 2, 1)
    assert len(rig.estate.records) == 1
    assert "replayed" in capsys.readouterr().out


def test_the_next_rotation_revises_again_and_strands_nothing_it_should_report(rig, capsys) -> None:
    """After a rotation completes infraops dates the next one by the new last-rotated date; the
    approved tip is revised for it. An OPEN record for the earlier revision is reported."""
    run(["--submit"])
    rig.approve_by_name()
    run(["--submit"])
    rig.due = [AGE]
    run(["--submit"])
    rig.approve_by_name()
    capsys.readouterr()

    assert run(["--submit"]) == EXIT_FINDINGS
    assert [p["package_revision"] for p in rig.estate.proposals] == [2, 3]
    assert "superseded" in capsys.readouterr().out
    rig.estate.records[0]["status"] = "resolved"
    assert run(["--submit"]) == EXIT_OK


def test_an_open_record_is_reported_while_the_next_revision_awaits_approval(rig, capsys) -> None:
    """Kills: reporting a stranded record only on the proposing path. Between the revision for the
    next rotation and Devon approving it, the earlier record is open and still approvable."""
    run(["--submit"])
    rig.approve_by_name()
    run(["--submit"])
    rig.due = [AGE]
    capsys.readouterr()

    assert run(["--submit"]) == EXIT_FINDINGS
    assert "revised" in (out := capsys.readouterr().out) and "superseded" in out
    assert run(["--submit"]) == EXIT_FINDINGS
    assert "awaiting-approval" in (out := capsys.readouterr().out) and "superseded" in out
    rig.estate.records[0]["status"] = "resolved"
    assert run(["--submit"]) == EXIT_OK


def test_a_different_rotation_arriving_while_one_awaits_review_is_stacked_not_revised(
    rig, capsys
) -> None:
    """Kills: revising over a revision Devon is reviewing, replacing what he is looking at."""
    run(["--submit"])
    before = rig.acts()
    rig.due = [AGE]
    capsys.readouterr()

    assert run(["--submit"]) == EXIT_FINDINGS
    assert rig.acts() == before
    assert "stacked" in capsys.readouterr().out
    assert "occurrence: '2026-10-07-requested'" in rig.package_yaml.read_text()


def test_a_rejected_revision_is_declined_not_revised_again(rig, capsys) -> None:
    run(["--submit"])
    text = rig.package_yaml.read_text()
    rig.package_yaml.write_text(text.replace("status: ready_for_review", "status: rejected"))
    before = rig.acts()
    capsys.readouterr()

    assert run(["--submit"]) == EXIT_OK
    assert rig.acts() == before
    assert "declined" in capsys.readouterr().out


def test_a_state_this_lane_never_writes_is_a_finding(rig, capsys) -> None:
    text = rig.package_yaml.read_text()
    rig.package_yaml.write_text(text.replace("status: approved", "status: in_execution"))
    assert run(["--submit"]) == EXIT_FINDINGS
    assert rig.acts() == ([], 0, 0, 0)
    assert "unexpected-state" in capsys.readouterr().out


LEGACY = Due("openai-project", "openai-key", "age", "2025-01-01", "2025-01-01", False)


def test_a_credential_infraops_still_rotates_is_left_to_it(rig, capsys) -> None:
    """Not handed over and no package: infraops' window owns it. Not a finding."""
    rig.due = [LEGACY]
    assert run(["--submit"]) == EXIT_OK
    assert rig.acts() == ([], 0, 0, 0)
    assert "legacy" in capsys.readouterr().out


def test_a_package_for_a_credential_never_handed_over_is_never_acted_on(rig, capsys) -> None:
    """Kills: acting on a package alone. infraops' window would rotate the same credential."""
    write_package(rig.root, "openai-project")
    rig.due = [LEGACY]
    assert run(["--submit"]) == EXIT_FINDINGS
    assert rig.acts() == ([], 0, 0, 0)
    assert rig.spine.rows == {}
    assert "not-handed-over" in capsys.readouterr().out


def test_a_handed_over_credential_with_no_package_is_a_finding(rig, capsys) -> None:
    """Handed over, so infraops refuses it; with no package nothing rotates it at all."""
    rig.due = [replace(LEGACY, handed_over=True)]
    # An OpenAI key is no type the SDS rotates, so no package can be generated for it.
    rig.classes["openai-project"] = "openai-key"
    assert run(["--submit"]) == EXIT_FINDINGS
    assert rig.acts() == ([], 0, 0, 0)
    assert not (rig.root / "packages" / "rotation-openai-project").exists()
    assert "unlaned" in capsys.readouterr().out


def test_a_check_infraops_reports_that_this_program_cannot_read_is_a_finding(rig, capsys) -> None:
    rig.due = []
    rig.unrecognised = [Unrecognised(CREDENTIAL, "cred.rotation-expiry")]
    assert run(["--submit"]) == EXIT_FINDINGS
    assert "unrecognised" in capsys.readouterr().out


def test_a_cause_that_cannot_be_filed_spends_no_revision(rig, capsys) -> None:
    """Kills: observing after the revision. A revision cannot be unminted."""
    rig.spine.status = 422
    assert run(["--submit"]) == EXIT_FINDINGS
    assert rig.acts() == ([], 1, 0, 0)
    assert "unobserved" in capsys.readouterr().out


def test_a_refused_observer_identity_stops_the_pass_rather_than_reporting_per_credential(
    rig,
) -> None:
    rig.spine.status = 401
    assert run(["--submit"]) == EXIT_UNUSABLE
    assert rig.acts() == ([], 1, 0, 0)


def test_a_refused_proposal_is_a_finding(rig, capsys, monkeypatch) -> None:
    run(["--submit"])
    rig.approve_by_name()
    listing = rig.estate.handler

    def conflict(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return listing(request)
        return httpx.Response(409, json={"detail": "differs"})

    monkeypatch.setattr(rig.estate, "handler", conflict)
    assert run(["--submit"]) == EXIT_FINDINGS
    assert "refused" in capsys.readouterr().out


def test_both_services_see_this_program_under_its_own_name(rig) -> None:
    run(["--submit"])
    rig.approve_by_name()
    run(["--submit"])
    assert rig.user_agents == {cli.USER_AGENT}
    assert cli.USER_AGENT.startswith("rotation-proposer/")


def test_a_dry_run_needs_no_credential_and_writes_nothing(rig, capsys, monkeypatch) -> None:
    for name in (
        "ROTATION_PROPOSER_CHANGE_MANAGER_TOKEN",
        "ORCHESTRATOR_API_URL",
        "ORCHESTRATOR_API_CREDENTIAL_KEY_ID",
        "ORCHESTRATOR_API_TOKEN",
        "SDS_BRIDGE_ALERT_TOKEN",
    ):
        monkeypatch.delenv(name)
    before = rig.package_yaml.read_text()

    assert run([]) == EXIT_OK
    assert rig.acts() == ([], 0, 0, 0)
    assert rig.package_yaml.read_text() == before
    assert "would-advance" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ROTATION_PROPOSER_CHANGE_MANAGER_TOKEN", ""),
        ("ORCHESTRATOR_API_TOKEN", ""),
        ("SDS_BRIDGE_ALERT_TOKEN", ""),
        # The route admits SYSTEM too, and `recorded_by` is permanent.
        ("ORCHESTRATOR_API_CREDENTIAL_KEY_ID", "orchestrator-system"),
    ],
)
def test_a_writing_pass_without_its_credentials_refuses_before_anything(
    rig, monkeypatch, name, value
) -> None:
    monkeypatch.setenv(name, value)
    assert run(["--submit"]) == EXIT_UNUSABLE
    assert rig.acts() == ([], 0, 0, 0)


def test_infraops_that_cannot_answer_stops_the_pass(rig, monkeypatch) -> None:
    def refuse():
        raise FindingsError("infraops refused cred-findings: unknown command")

    monkeypatch.setattr(cli, "read_due", refuse)
    assert run(["--submit"]) == EXIT_UNUSABLE
    assert rig.acts() == ([], 0, 0, 0)


def test_a_checkout_this_program_may_not_write_to_stops_the_pass(rig, monkeypatch) -> None:
    def dirty(root):
        raise shared.StandingError("the packages checkout has uncommitted changes")

    monkeypatch.setattr(cli, "require_clean", dirty)
    assert run(["--submit"]) == EXIT_UNUSABLE
    assert rig.acts() == ([], 0, 0, 0)


def test_the_proposal_and_observation_never_carry_a_value_the_registry_does_not(rig) -> None:
    """Nothing this program sends is read from a secret: every value is a registry name, a
    package name, a date or a fixed string. Asserted over the wire, not the source."""
    run(["--submit"])
    rig.approve_by_name()
    run(["--submit"])
    sent = json.dumps([rig.spine.posted, rig.estate.proposals])
    for credential_value in ("cm-propose", "observer"):
        assert f'"{credential_value}"' not in sent


def test_a_change_manager_failure_on_one_proposal_is_that_credentials_error(
    rig, capsys, monkeypatch
) -> None:
    """A 5xx on the proposal is one credential's failure, reported and counted as a finding --
    not a whole-pass "could not use its inputs", which would hide which record failed."""
    run(["--submit"])
    rig.approve_by_name()
    listing = rig.estate.handler

    def failing(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return listing(request)
        return httpx.Response(500, json={"detail": "boom"})

    monkeypatch.setattr(rig.estate, "handler", failing)
    assert run(["--submit"]) == EXIT_FINDINGS
    assert "error" in capsys.readouterr().out


def test_a_refused_publish_stops_the_next_credential_from_stacking_on_it(
    rig, capsys, monkeypatch
) -> None:
    """Kills: checking the checkout only once per pass. A publish refused for the first credential
    leaves a local commit; the second credential must be refused rather than build a further
    revision on top of it and commit again."""
    write_package(rig.root, "openai-project")
    rig.due = [REQUESTED, replace(LEGACY, handed_over=True)]
    residue: list[str] = []

    def refused(package, root):
        residue.append(package.package_id)
        raise shared.StandingError(f"{package.package_id} rev 2 is committed and unpublished")

    def publishable(root):
        if residue:
            raise shared.StandingError("the packages checkout carries 1 commit(s) origin does not")

    monkeypatch.setattr(cli, "commit", refused)
    monkeypatch.setattr(cli, "require_publishable", publishable)

    assert run(["--submit"]) == EXIT_FINDINGS
    assert residue == ["rotation-openrouter-generic"]
    revised = [call[1] for call in rig.lifecycle.calls if call[0] == "revise"]
    assert len(revised) == 1
    assert capsys.readouterr().out.count("error") == 2


EXPOSED = Due(CREDENTIAL, "openrouter-key", "exposure", "exp-7", "2026-10-07", True)


def _proposed_and_built(rig, monkeypatch, *, decided_by: str = "work-watcher") -> list[str]:
    """Through Devon's approval and the proposal, then the work watcher retiring the record."""
    recorded: list[str] = []
    monkeypatch.setattr(cli, "record_rotation", recorded.append)
    run(["--submit"])
    rig.approve_by_name()
    run(["--submit"])
    rig.estate.records[0].update(status="resolved", decided_by=decided_by)
    return recorded


def test_a_finished_rotation_is_recorded_in_infraops_instead_of_replayed(
    rig, capsys, monkeypatch
) -> None:
    recorded = _proposed_and_built(rig, monkeypatch)
    capsys.readouterr()

    assert run(["--submit"]) == EXIT_OK
    assert recorded == [CREDENTIAL]
    assert len(rig.estate.proposals) == 1
    assert "recorded" in capsys.readouterr().out


def test_a_record_a_person_resolved_records_nothing_and_is_a_finding(
    rig, capsys, monkeypatch
) -> None:
    """Only the work watcher retires a record because its work completed; anyone else's
    resolution would otherwise leave the credential due with nothing reported."""
    recorded = _proposed_and_built(rig, monkeypatch, decided_by="devon")
    capsys.readouterr()

    assert run(["--submit"]) == EXIT_FINDINGS
    assert recorded == []
    assert "resolved-by-person" in capsys.readouterr().out


def test_a_retired_record_for_another_revision_records_nothing(rig, capsys, monkeypatch) -> None:
    recorded = _proposed_and_built(rig, monkeypatch)
    rig.estate.records[0]["package_revision"] = 1
    capsys.readouterr()

    run(["--submit"])
    assert recorded == []


def test_infraops_refusing_the_record_is_that_credentials_error(rig, capsys, monkeypatch) -> None:
    _proposed_and_built(rig, monkeypatch)

    def refuse(credential_id: str) -> None:
        raise FindingsError("infraops refused record-rotation: unknown credential id")

    monkeypatch.setattr(cli, "record_rotation", refuse)
    capsys.readouterr()

    assert run(["--submit"]) == EXIT_FINDINGS
    out = capsys.readouterr().out
    assert "error" in out and "unknown credential id" in out


def test_a_finished_exposure_rotation_is_left_to_a_person_and_never_recorded(
    rig, capsys, monkeypatch
) -> None:
    """record-rotation doesn't close an exposure, so the credential stays due: recording on every
    pass would move lastRotated forward every day."""
    rig.due = [EXPOSED]
    recorded = _proposed_and_built(rig, monkeypatch)
    capsys.readouterr()

    assert run(["--submit"]) == EXIT_FINDINGS
    assert run(["--submit"]) == EXIT_FINDINGS
    out = capsys.readouterr().out
    assert recorded == []
    assert "exposure-open" in out and "exp-7" in out


def test_the_next_rotation_revises_rather_than_recording_the_last_one_again(
    rig, capsys, monkeypatch
) -> None:
    recorded = _proposed_and_built(rig, monkeypatch)
    run(["--submit"])
    rig.due = [AGE]
    capsys.readouterr()

    run(["--submit"])
    assert recorded == [CREDENTIAL]
    assert "revised" in capsys.readouterr().out


def test_only_a_resolved_record_records(rig, capsys, monkeypatch) -> None:
    recorded = _proposed_and_built(rig, monkeypatch)
    rig.estate.records[0]["status"] = "approved"
    capsys.readouterr()

    run(["--submit"])
    assert recorded == []
    assert "replayed" in capsys.readouterr().out


def test_a_retired_record_for_another_package_at_the_same_revision_records_nothing(
    rig, capsys, monkeypatch
) -> None:
    recorded = _proposed_and_built(rig, monkeypatch)
    rig.estate.records[0]["package_id"] = "rotation-another-credential"
    capsys.readouterr()

    run(["--submit"])
    assert recorded == []


def test_the_retirement_actor_is_the_work_watchers() -> None:
    from work_watcher.change_manager import RETIREMENT_ACTOR

    assert cli.RETIREMENT_ACTOR == RETIREMENT_ACTOR


# ---- the registry's destinations, and a generated package (ADR-0055 amendment 15, increment 4) --

DRIFTED = [*DESTINATIONS, 'runtime-fetch repo="orchestrator" ref="scripts/run-new-lane.sh"']


def test_a_due_revision_carries_the_registry_s_destinations(rig, capsys) -> None:
    rig.registry = list(DRIFTED)
    assert run(["--submit"]) == EXIT_OK
    text = rig.package_yaml.read_text()
    assert all(f"'{destination}'" in text for destination in DRIFTED)
    assert "revised" in capsys.readouterr().out


def test_an_approved_revision_that_drifted_is_revised_not_proposed(rig, capsys) -> None:
    """Kills: proposing a revision the executor would refuse at its first unit."""
    run(["--submit"])
    rig.approve_by_name()
    rig.registry = list(DRIFTED)
    capsys.readouterr()

    assert run(["--submit"]) == EXIT_OK
    assert rig.acts() == (["revise", "transition", "hash"] * 2, 2, 0, 2)
    assert "run-new-lane.sh" in rig.package_yaml.read_text()
    assert "revised" in capsys.readouterr().out
    rig.approve_by_name()
    assert run(["--submit"]) == EXIT_OK
    assert [p["package_revision"] for p in rig.estate.proposals] == [3]


def test_a_registry_that_can_t_be_read_writes_nothing(rig, capsys) -> None:
    rig.registry = None
    assert run(["--submit"]) == EXIT_FINDINGS
    assert rig.acts() == ([], 0, 0, 0)
    assert "the registry:" in capsys.readouterr().out


GENERATED = "github-finegrained-new"


def test_a_handed_over_credential_with_no_package_gets_one_generated(rig, capsys) -> None:
    """Revision 1 from the registry row, observed first, written through intent-packages, taken
    to review and published -- and nothing proposed until a named human approves it."""
    rig.due = [Due(GENERATED, "github-pat-fine-grained", "age", "2026-07-01", "2026-07-01", True)]
    rig.classes[GENERATED] = "github-pat-fine-grained"
    assert run(["--submit"]) == EXIT_OK

    lifecycle, observations, proposals, published = rig.acts()
    assert (lifecycle, observations, proposals) == (["create-rotation", "transition", "hash"], 1, 0)
    assert published == 1
    text = (rig.root / "packages" / f"rotation-{GENERATED}" / "package.yaml").read_text()
    assert "status: ready_for_review" in text and "created_by: rotation-proposer" in text
    assert "generated" in capsys.readouterr().out


def test_a_dry_run_says_it_would_generate_and_writes_nothing(rig, capsys) -> None:
    rig.due = [Due(GENERATED, "github-pat-fine-grained", "age", "2026-07-01", "2026-07-01", True)]
    rig.classes[GENERATED] = "github-pat-fine-grained"
    assert run([]) == EXIT_OK
    assert rig.acts() == ([], 0, 0, 0)
    assert not (rig.root / "packages" / f"rotation-{GENERATED}").exists()
    assert "would-generate" in capsys.readouterr().out


def test_generation_refuses_an_unpublishable_checkout_before_writing_anything(
    rig, monkeypatch, capsys
) -> None:
    """Kills: creating the package before asking whether its commit could be published."""

    asked: list[object] = []

    def unpublishable(root) -> None:
        # The pass asks once before it reads anything; this refuses the ask before generating.
        asked.append(root)
        if len(asked) > 1:
            raise shared.StandingError("the checkout is on a topic branch")

    monkeypatch.setattr(cli, "require_publishable", unpublishable)
    rig.due = [Due(GENERATED, "github-pat-fine-grained", "age", "2026-07-01", "2026-07-01", True)]
    rig.classes[GENERATED] = "github-pat-fine-grained"
    assert run(["--submit"]) == EXIT_FINDINGS
    assert [call[0] for call in rig.lifecycle.calls] == []
    assert not (rig.root / "packages" / f"rotation-{GENERATED}").exists()
    assert "topic branch" in capsys.readouterr().out


def test_a_finished_rotation_is_recorded_even_when_the_registry_changed_since(
    rig, capsys, monkeypatch
) -> None:
    """Kills: drift revising a revision whose rotation already completed, which would skip
    record-rotation and rotate the credential a second time."""
    recorded = _proposed_and_built(rig, monkeypatch)
    rig.registry = list(DRIFTED)
    capsys.readouterr()

    assert run(["--submit"]) == EXIT_OK
    assert recorded == [CREDENTIAL]
    assert len(rig.estate.proposals) == 1
    assert "run-new-lane.sh" not in rig.package_yaml.read_text()


def test_a_revision_with_an_open_record_is_never_revised_from_under_it(rig, capsys) -> None:
    run(["--submit"])
    rig.approve_by_name()
    run(["--submit"])
    rig.registry = list(DRIFTED)
    capsys.readouterr()

    assert run(["--submit"]) == EXIT_OK
    assert rig.acts()[0] == ["revise", "transition", "hash"]
    assert "run-new-lane.sh" not in rig.package_yaml.read_text()
    assert "replayed" in capsys.readouterr().out


def test_the_registry_is_read_once_per_pass(rig) -> None:
    rig.due = [
        REQUESTED,
        Due(
            "bitbucket-mirror-token", "atlassian-api-token", "age", "2026-07-01", "2026-07-01", True
        ),
    ]
    rig.classes["bitbucket-mirror-token"] = "atlassian-api-token"
    run([])
    assert rig.registry_reads == 1


def test_a_malformed_row_is_that_credential_s_finding_and_the_pass_goes_on(rig, capsys) -> None:
    rig.due = [
        Due(GENERATED, "github-pat-fine-grained", "age", "2026-07-01", "2026-07-01", True),
        REQUESTED,
    ]
    rig.classes[GENERATED] = "github-pat-fine-grained"
    rig.registry = ["not a destination"]
    assert run([]) == EXIT_FINDINGS
    out = capsys.readouterr().out
    assert f"{GENERATED}  error" in out and "the registry:" in out
    assert f"{CREDENTIAL}  would-advance" in out


def test_a_writing_pass_syncs_its_outcomes_with_the_sender_token(rig, tmp_path) -> None:
    assert run(["--submit"]) == EXIT_OK
    [(outcomes, token, state_dir)] = rig.synced
    assert [o.status for o in outcomes] == ["revised"]
    assert token == "sender"
    assert state_dir == tmp_path / "state"


def test_a_dry_run_sends_nothing(rig) -> None:
    assert run([]) == EXIT_OK
    assert rig.synced == []


def test_an_unusable_pass_syncs_nothing(rig, monkeypatch) -> None:
    def refuse():
        raise FindingsError("infraops could not say")

    monkeypatch.setattr(cli, "read_due", refuse)
    assert run(["--submit"]) == EXIT_UNUSABLE
    assert rig.synced == []


def test_a_change_manager_that_cannot_list_records_syncs_nothing(rig, monkeypatch) -> None:
    monkeypatch.setattr(rig.estate, "handler", lambda request: httpx.Response(503))
    assert run(["--submit"]) == EXIT_UNUSABLE
    assert rig.synced == []


@pytest.mark.parametrize("unrecognised", [False, True], ids=["clean", "findings"])
def test_a_sync_failure_keeps_the_exit_code(rig, monkeypatch, capsys, unrecognised) -> None:
    if unrecognised:
        rig.unrecognised = [Unrecognised(CREDENTIAL, "weird-check")]
    monkeypatch.setattr(cli, "sync_alerts", lambda *args, **kwargs: "alert-hub said no")
    assert run(["--submit"]) == (EXIT_FINDINGS if unrecognised else EXIT_OK)
    assert "alert-hub said no" in capsys.readouterr().err
