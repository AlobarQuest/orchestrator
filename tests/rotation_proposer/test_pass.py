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

import httpx
import pytest

from bump_proposer import standing as shared
from bump_proposer.change_manager import ChangeManagerClient
from bump_proposer.cli import PROPOSAL_FIELDS
from bump_proposer.orchestrator_client import OrchestratorClient
from rotation_proposer import cli
from rotation_proposer.cli import EXIT_FINDINGS, EXIT_OK, EXIT_UNUSABLE, run
from rotation_proposer.findings import Due, FindingsError, Unrecognised
from tests.bump_proposer.test_pass import _Estate, _Spine
from tests.rotation_proposer.test_standing import FakeLifecycle, write_package

CREDENTIAL = "openrouter-generic"
REQUESTED = Due(CREDENTIAL, "openrouter-key", "requested", "2026-10-07", "2026-10-07")
AGE = Due(CREDENTIAL, "openrouter-key", "age", "2026-10-08", "2026-10-08")


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

    lifecycle = FakeLifecycle(tmp_path)
    monkeypatch.setattr(shared, "lifecycle", lifecycle)
    rig = _Rig(tmp_path, lifecycle)

    def recorded(handler):
        def wrapped(request: httpx.Request) -> httpx.Response:
            rig.user_agents.add(request.headers["user-agent"])
            return handler(request)

        return wrapped

    monkeypatch.setattr(cli, "read_due", lambda: (list(rig.due), list(rig.unrecognised)))
    monkeypatch.setattr(cli, "require_clean", lambda root: None)
    monkeypatch.setattr(cli, "require_publishable", lambda root: None)

    def commit(package, root):
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
    assert tuple(proposal) == PROPOSAL_FIELDS
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


def test_a_credential_with_no_standing_package_is_reported_and_skipped(rig, capsys) -> None:
    """Never authored here; and not a finding, because only authoring a package clears it."""
    rig.due = [Due("openai-project", "openai-key", "age", "2025-01-01", "2025-01-01")]
    assert run(["--submit"]) == EXIT_OK
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
    rig.due = [REQUESTED, Due("openai-project", "openai-key", "age", "2025-01-01", "2025-01-01")]
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
