"""The whole pass, driven through the real readers over a stubbed GitHub and change-manager.

**THE IDEMPOTENCY PROOF LIVES HERE, in both directions.** A first pass advances a standing
package and proposes a record; a second pass over the same estate advances nothing and replays
the proposal. Both halves are asserted by COUNTING the acts -- the lifecycle commands run and
the proposals sent -- rather than by reading the final state, because the final state is
identical either way and is exactly what a producer that revised twice would also show.
"""

from __future__ import annotations

import functools
import json
import os
import pathlib
from dataclasses import dataclass, replace
from datetime import UTC, datetime

import httpx
import pytest

from alert_hub_client import KeyedAlerts
from bump_proposer import alerts, cli, standing
from bump_proposer.cli import EXIT_FINDINGS, EXIT_OK, EXIT_UNUSABLE, run
from bump_proposer.landing_policy import (
    LandingPolicyError,
    parse,
    read_landing_policy,
)
from sds_members.client import SERVICES, Hold, Member, MembersUnreadable
from tests.bump_proposer.test_landing_policy import LIVE

REPOSITORY = "AlobarQuest/infraops-mcp-server"
# Taken before any rig replaces it, for the test that needs a real clean-checkout refusal.
REAL_REQUIRE_CLEAN = standing.require_clean

# The zod pull request's checks CONCLUDED AGAINST IT, long enough ago to have settled. Since
# ADR-0034 that is the only thing that keeps a bump in this lane -- the declared rule permits an
# npm major on its type -- so a rig whose stub reported no concluded checks would exercise a pass
# that classifies its one subject as the estate's business and does nothing at all.
FAILED_LONG_AGO = "2026-01-01T00:00:00+00:00"

SHELL = """\
schema_version: 1
package_id: infraops-mcp-server-npm-zod
title: t
revision: 1
status: draft
profile: dependency-update
profile_fields:
  target_repo: AlobarQuest/infraops-mcp-server
  package: zod
  from_version: unassigned
  to_version: unassigned
  standing: true
"""


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    directory = tmp_path / "packages" / "infraops-mcp-server-npm-zod"
    directory.mkdir(parents=True)
    (directory / "package.yaml").write_text(SHELL, encoding="utf-8")
    fixture = tmp_path / "tests" / "fixtures"
    fixture.mkdir(parents=True)
    (fixture / "package_hashes.json").write_text("{}\n")
    monkeypatch.setenv("BUMP_PROPOSER_PACKAGES_CHECKOUT", str(tmp_path))
    monkeypatch.setenv("BUMP_PROPOSER_GITHUB_TOKEN", "gh")
    monkeypatch.setenv("BUMP_PROPOSER_CHANGE_MANAGER_TOKEN", "cm")
    monkeypatch.setenv("BUMP_PROPOSER_CHANGE_MANAGER_READ_TOKEN", "cm-read")
    # G1+G2. The OBSERVER credential, in the estate-wide spelling every observe-and-report lane
    # reads it from -- a writing pass files the cause before it proposes, and refuses to propose
    # at all without it.
    monkeypatch.setenv("ORCHESTRATOR_API_URL", "https://sds.example.net")
    monkeypatch.setenv("ORCHESTRATOR_API_CREDENTIAL_KEY_ID", "orchestrator-observer")
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "observer")
    # Bridge Part 3. The sds-bridge sender token, and where the keys this lane fired are kept.
    monkeypatch.setenv("SDS_BRIDGE_ALERT_TOKEN", "sender")
    monkeypatch.setenv("BUMP_PROPOSER_STATE_DIR", str(tmp_path / "state"))
    return tmp_path


@dataclass(frozen=True)
class Pull:
    """One open update-bot pull request as the stub serves it."""

    number: int = 71
    title: str = "build(deps): bump zod from 3.25.76 to 4.4.3"
    dependency: str = "zod"
    ecosystem: str = "npm_and_yarn"
    update_type: str | None = "version-update:semver-major"
    conclusion: str = "failure"
    completed_at: str = FAILED_LONG_AGO


def _github(
    title: str = "build(deps): bump zod from 3.25.76 to 4.4.3",
    update_type: str | None = "version-update:semver-major",
    *,
    pulls: list[Pull] | None = None,
    unreadable: bool = False,
):
    served = pulls if pulls is not None else [Pull(title=title, update_type=update_type)]
    by_number = {pull.number: pull for pull in served}

    def trailer(pull: Pull) -> str:
        return (
            f"build(deps): bump {pull.dependency}\n\n---\nupdated-dependencies:\n"
            f"- dependency-name: {pull.dependency}\n  update-type: {pull.update_type}\n...\n"
            if pull.update_type
            else f"build(deps): bump {pull.dependency}\n\n---\nupdated-dependencies:\n"
            f"- dependency-name: {pull.dependency}\n...\n"
        )

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == f"/repos/{REPOSITORY}":
            return httpx.Response(200, json={"default_branch": "main"})
        # DELIBERATELY NOT SERVED. Until ADR-0038 this stub answered the gate workflow's blob
        # sha, because the pass read it. It reads change-manager's declaration instead, so the
        # path falls through to the 404 below -- which is the assertion: a pass that went back to
        # asking GitHub for that file would fail here rather than quietly working.
        if path == f"/repos/{REPOSITORY}/pulls":
            if unreadable:
                return httpx.Response(500, json={})
            return httpx.Response(
                200,
                json=[{"number": n, "user": {"login": "dependabot[bot]"}} for n in by_number],
            )
        for number, pull in by_number.items():
            head = f"{number:040d}"
            if path == f"/repos/{REPOSITORY}/pulls/{number}":
                return httpx.Response(
                    200,
                    json={
                        "number": number,
                        "title": pull.title,
                        "created_at": "2026-08-01T00:00:00+00:00",
                        "auto_merge": None,
                        "head": {
                            "sha": head,
                            "ref": f"dependabot/{pull.ecosystem}/{pull.dependency}-x",
                        },
                    },
                )
            if path == f"/repos/{REPOSITORY}/pulls/{number}/commits":
                return httpx.Response(
                    200,
                    json=[
                        {
                            "author": {"login": "dependabot[bot]"},
                            "commit": {
                                "message": trailer(pull),
                                "verification": {"verified": True},
                            },
                        }
                    ],
                )
            if path == f"/repos/{REPOSITORY}/actions/runs" and request.url.params.get(
                "head_sha"
            ) in (head, None):
                return httpx.Response(
                    200,
                    json={
                        "workflow_runs": [
                            {
                                "id": number,
                                "event": "pull_request",
                                "path": ".github/workflows/quality.yml",
                            }
                        ]
                    },
                )
            if path == f"/repos/{REPOSITORY}/actions/runs/{number}/jobs":
                return httpx.Response(
                    200,
                    json={
                        "jobs": [
                            {
                                "name": "Lint, type-check, and test",
                                "conclusion": pull.conclusion,
                                "completed_at": pull.completed_at,
                            }
                        ]
                    },
                )
        return httpx.Response(404, json={})

    return handler


def _serve(monkeypatch, **kwargs) -> None:
    """Point the pass's GitHub reader at a stub serving `kwargs` (see `_github`)."""
    monkeypatch.setattr(
        cli,
        "GitHubReader",
        lambda **kw: __import__("landing_ledger.github", fromlist=["GitHubReader"]).GitHubReader(
            token="gh", transport=httpx.MockTransport(_github(**kwargs))
        ),
    )


class _Estate:
    """A change-manager that behaves as the real one does: 201 then 200 for the same proposal."""

    def __init__(self):
        self.proposals: list[dict] = []
        self.records: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/items":
            return httpx.Response(200, json=self.records)
        payload = json.loads(request.content)
        self.proposals.append(payload)
        key = (payload["package_id"], payload["package_revision"])
        for record in self.records:
            if (record["package_id"], record["package_revision"]) == key:
                return httpx.Response(200, json=record)
        record = {
            "id": len(self.records) + 1,
            "status": "pending",
            "source": "work",
            "package_id": payload["package_id"],
            "package_revision": payload["package_revision"],
        }
        self.records.append(record)
        return httpx.Response(201, json=record)


class _Spine:
    """An orchestrator that behaves as the real one does: one row per reference, then replayed.

    `record_observation` returns the EXISTING row for a repeat of the same key rather than filing
    a second one, and that is the half of this lane a first pass cannot show. `posted` counts the
    ACTS and `rows` the distinct facts, because the two are equal on a first pass and diverge on
    every one after it -- which is exactly what the replay property is about.
    """

    def __init__(self):
        self.posted: list[dict] = []
        self.rows: dict[str, str] = {}
        self.status = 201

    def handler(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        self.posted.append(payload)
        if self.status >= 400:
            return httpx.Response(self.status, json={"detail": "no"})
        identifier = self.rows.setdefault(
            payload["idempotency_key"], f"0bbe0000-0000-4000-8000-{len(self.rows):012d}"
        )
        return httpx.Response(self.status, json={"id": identifier, **payload})


def _member(
    repository: str,
    dependency_updates: str = "granted",
    state: str = "member",
    holds: tuple[Hold, ...] = (),
) -> Member:
    return Member(
        repository=repository,
        owner="devon",
        state=state,
        working_copy=None,
        machine_local=False,
        apps=(),
        grants={service: "withdrawn" for service in SERVICES}
        | {"dependency_updates": dependency_updates},
        holds=holds,
    )


def _grant(monkeypatch, *repositories: str) -> None:
    """The member record the pass reads, granting `dependency_updates` to exactly these."""
    members = [_member(name) for name in repositories]
    monkeypatch.setattr(cli, "read_members_from_environment", lambda **_: members)


@pytest.fixture
def spine():
    return _Spine()


class _Hub:
    """alert-hub: records every alert sent, answering 202 (or `status`)."""

    def __init__(self):
        self.sent: list[dict] = []
        self.status = 202

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/alerts"
        assert request.headers["Authorization"] == "Bearer sender"
        self.sent.append(json.loads(request.content))
        return httpx.Response(self.status, json={})

    def keys(self, status: str) -> list[str]:
        return [alert["key"] for alert in self.sent if alert["status"] == status]


@pytest.fixture
def hub():
    return _Hub()


def _create_standing(checkout, args) -> str:
    """What `create-standing` writes: a draft standing package at revision 1 carrying the bump."""
    options = dict(zip(args[1::2], args[2::2], strict=True))
    name = options["--target-repo"].rsplit("/", 1)[-1].lower()
    slug = options["--package"].lower().replace("/", "-").replace("@", "").replace(".", "-")
    directory = checkout / options["--out"] / f"{name}-{options['--ecosystem']}-{slug}"
    directory.mkdir(parents=True)
    (directory / "package.yaml").write_text(
        SHELL.replace("infraops-mcp-server-npm-zod", directory.name)
        .replace("AlobarQuest/infraops-mcp-server", options["--target-repo"])
        .replace("package: zod", f"package: {options['--package']}")
        .replace("from_version: unassigned", f"from_version: '{options['--from']}'")
        .replace("to_version: unassigned", f"to_version: '{options['--to']}'"),
        encoding="utf-8",
    )
    return f"{options['--out']}/{directory.name}\n"


@pytest.fixture
def rig(checkout, monkeypatch, spine, hub):
    estate = _Estate()
    calls: list[tuple[str, ...]] = []

    def lifecycle(root, *args):
        calls.append(args)
        if args[0] == "create-standing":
            return _create_standing(checkout, args)
        path = pathlib.Path(args[1]) / "package.yaml"
        if args[0] == "hash":
            return "a" * 64 + "\n"
        text = path.read_text()
        status = text.split("status: ")[1].split("\n")[0]
        if args[0] == "revise":
            revision = int(text.split("revision: ")[1].split("\n")[0])
            text = text.replace(f"revision: {revision}", f"revision: {revision + 1}")
            text = text.replace(f"status: {status}", "status: draft")
        elif args[0] == "transition":
            text = text.replace(f"status: {status}", "status: ready_for_review")
        elif args[0] == "approve":
            text = text.replace(f"status: {status}", "status: approved")
        path.write_text(text, encoding="utf-8")
        return ""

    monkeypatch.setattr(standing, "lifecycle", lifecycle)
    # Patched on `cli` as well as on `standing`: the CLI imports these by NAME, so a patch on
    # the defining module alone leaves the caller holding the original.
    for module in (standing, cli):
        monkeypatch.setattr(module, "require_clean", lambda root: None)
        monkeypatch.setattr(module, "require_publishable", lambda root: None)
        monkeypatch.setattr(
            module, "commit", lambda package, bump, root: calls.append(("commit",)) or "c" * 40
        )
    monkeypatch.setattr(
        cli,
        "GitHubReader",
        lambda **kw: __import__("landing_ledger.github", fromlist=["GitHubReader"]).GitHubReader(
            token="gh", transport=httpx.MockTransport(_github())
        ),
    )
    monkeypatch.setattr(cli, "read_landing_policy", lambda token, base_url: parse(LIVE))
    _grant(monkeypatch, "AlobarQuest/infraops-mcp-server")
    monkeypatch.setattr(
        cli,
        "open_client",
        lambda *, base_url, credential_key_id, token: __import__(
            "bump_proposer.orchestrator_client", fromlist=["OrchestratorClient"]
        ).OrchestratorClient(
            base_url=base_url,
            credential_key_id=credential_key_id,
            token=token,
            transport=httpx.MockTransport(spine.handler),
        ),
    )
    monkeypatch.setattr(
        cli,
        "ChangeManagerClient",
        lambda token, base_url=None: __import__(
            "bump_proposer.change_manager", fromlist=["ChangeManagerClient"]
        ).ChangeManagerClient(token, transport=httpx.MockTransport(estate.handler)),
    )
    monkeypatch.setattr(
        alerts,
        "KeyedAlerts",
        functools.partial(KeyedAlerts, transport=httpx.MockTransport(hub.handler)),
    )
    return estate, calls


def test_a_first_pass_advances_the_package_and_proposes_the_record(rig, capsys) -> None:
    estate, calls = rig
    assert run(["--submit"]) == EXIT_OK
    assert [c[0] for c in calls] == ["transition", "approve", "hash", "commit"]
    assert len(estate.proposals) == 1
    assert estate.proposals[0]["package_revision"] == 1
    assert "proposed" in capsys.readouterr().out


def test_a_second_pass_replays_and_advances_nothing(rig, capsys) -> None:
    """THE IDEMPOTENCY PROOF. Counted, not read off the final state: a producer that revised on
    every pass would leave exactly the same package approved and carrying the same bump."""
    estate, calls = rig
    assert run(["--submit"]) == EXIT_OK
    calls.clear()
    capsys.readouterr()

    assert run(["--submit"]) == EXIT_OK

    assert calls == []
    assert len(estate.records) == 1
    assert len(estate.proposals) == 2  # proposed once, replayed once
    assert "replayed" in capsys.readouterr().out


def test_a_replay_onto_a_record_a_human_declined_is_not_a_finding(rig, capsys) -> None:
    """PRE-WORK RECOVERY, the rejection half. A person set the record `wontfix`; the bump is still
    open, so the next pass proposes it again. That replays onto the declined record -- the same
    package revision is the same record -- and is neither a finding nor a new revision: the
    decline stands until the world offers a DIFFERENT bump, which mints a new revision and a new
    pending record. Change-manager pins the other half: a replay does not move the status.
    """
    estate, calls = rig
    assert run(["--submit"]) == EXIT_OK
    estate.records[0]["status"] = "wontfix"
    calls.clear()
    capsys.readouterr()

    assert run(["--submit"]) == EXIT_OK

    out = capsys.readouterr().out
    assert calls == []
    assert len(estate.records) == 1
    assert "replayed" in out
    assert "status=wontfix" in out
    assert "superseded" not in out


def test_a_package_carrying_the_bump_but_not_yet_approved_is_still_advanced(
    rig, capsys, monkeypatch
) -> None:
    """Kills: replaying on `carries(bump)` alone.

    A pass that crashed after writing the versions and before approving leaves a package that
    CARRIES this bump and is unapproved. Replaying it would propose a record for a revision the
    carry cannot take -- `emit-intake-payload` refuses a package whose current revision has no
    matching approval -- so the work would sit approved by a person and be undeliverable.
    """
    estate, calls = rig
    path = (
        pathlib.Path(os.environ["BUMP_PROPOSER_PACKAGES_CHECKOUT"])
        / "packages"
        / "infraops-mcp-server-npm-zod"
        / "package.yaml"
    )
    path.write_text(
        path.read_text()
        .replace("from_version: unassigned", "from_version: '3.25.76'")
        .replace("to_version: unassigned", "to_version: '4.4.3'"),
        encoding="utf-8",
    )

    assert run(["--submit"]) == EXIT_OK

    assert [c[0] for c in calls] == ["transition", "approve", "hash", "commit"]
    assert len(estate.proposals) == 1


def test_a_record_at_the_current_revision_is_not_reported_superseded(rig, capsys) -> None:
    """Kills: `_superseded` dropping the revision comparison.

    The ordinary steady state is exactly this: one record, at the revision on disk. Reporting
    it would make every scheduled pass a finding for as long as the lane works.
    """
    estate, calls = rig
    assert run(["--submit"]) == EXIT_OK
    capsys.readouterr()
    assert run(["--submit"]) == EXIT_OK
    assert "superseded" not in capsys.readouterr().out


def test_another_packages_record_is_not_reported_superseded(rig, capsys, monkeypatch) -> None:
    """Kills: `_superseded` dropping the package-id filter.

    Every standing package's records arrive in ONE listing and revision numbers restart at 1 per
    package, so an unfiltered scan reports another lane's live record as stranded by this one.
    The case has to be a lane that has ADVANCED -- at revision 1 nothing can be lower, so a
    first-pass fixture cannot tell the two filters apart, which is how this survived once.
    """
    estate, calls = rig
    estate.records.append(
        {
            "id": 99,
            "status": "pending",
            "source": "work",
            "package_id": "infraops-mcp-server-npm-eslint",
            "package_revision": 1,
        }
    )
    assert run(["--submit"]) == EXIT_OK
    capsys.readouterr()

    monkeypatch.setattr(
        cli,
        "GitHubReader",
        lambda **kw: __import__("landing_ledger.github", fromlist=["GitHubReader"]).GitHubReader(
            token="gh",
            transport=httpx.MockTransport(
                _github(title="build(deps): bump zod from 3.25.76 to 4.5.0")
            ),
        ),
    )
    run(["--submit"])

    reported = [line for line in capsys.readouterr().out.splitlines() if "superseded" in line]
    assert len(reported) == 1
    assert "infraops-mcp-server-npm-zod" in reported[0]


def test_a_dry_run_writes_nothing_and_touches_no_credential_that_could(
    rig, capsys, monkeypatch
) -> None:
    """THE PROPERTY `run-bump-proposer.sh` STATES, and ADR-0038 changed its SHAPE, not its truth.

    A dry run now MUST read change-manager -- the rule it reports against is declared there
    rather than transcribed into this repository -- so "touches no credential" became false as
    written. What is preserved, and what the launcher's header is actually about, is that it
    touches no credential that could WRITE: the read is a second, READ-scoped bearer, measured
    2026-08-31 as 403 on the proposal route where the propose one reaches request validation.
    """
    estate, calls = rig
    monkeypatch.delenv("BUMP_PROPOSER_CHANGE_MANAGER_TOKEN")

    assert run([]) == EXIT_OK

    assert calls == [] and estate.proposals == []
    assert "would-advance" in capsys.readouterr().out


def test_a_dry_run_without_the_read_credential_is_unusable_rather_than_a_clean_pass(
    rig, monkeypatch
) -> None:
    """THE CONTROL for the test above. Without it, that one passes for a producer that never
    reads the declaration at all -- and a pass reporting nothing found on a rule it never asked
    for is the quiet failure this whole increment is built to avoid."""
    estate, calls = rig
    monkeypatch.delenv("BUMP_PROPOSER_CHANGE_MANAGER_READ_TOKEN")
    # The rig stubs the reader; this case needs the REAL one, which refuses an empty credential
    # before it constructs a client -- so nothing leaves the process either.
    monkeypatch.setattr(cli, "read_landing_policy", read_landing_policy)

    assert run([]) == EXIT_UNUSABLE

    assert calls == [] and estate.proposals == []


def test_the_bump_moving_under_a_stable_pull_request_advances_and_reports_the_stranded_record(
    rig, capsys, monkeypatch
) -> None:
    """The update bot rewrites a pull request in place when a newer version appears.

    The lane follows the CURRENT bump: revision 2 carries it and gets its own record. Revision
    1's record can no longer be carried -- only the tip revision is on disk -- so it is reported
    HERE, by the pass that caused it, rather than surfacing in the carry the next morning.
    """
    estate, calls = rig
    assert run(["--submit"]) == EXIT_OK
    calls.clear()
    capsys.readouterr()

    monkeypatch.setattr(
        cli,
        "GitHubReader",
        lambda **kw: __import__("landing_ledger.github", fromlist=["GitHubReader"]).GitHubReader(
            token="gh",
            transport=httpx.MockTransport(
                _github(title="build(deps): bump zod from 3.25.76 to 4.5.0")
            ),
        ),
    )
    assert run(["--submit"]) == EXIT_FINDINGS

    assert [c[0] for c in calls] == ["revise", "transition", "approve", "hash", "commit"]
    assert [r["package_revision"] for r in estate.records] == [1, 2]
    out = capsys.readouterr().out
    assert "superseded" in out and "record 1" in out


def test_an_unclassifiable_bump_is_reported_by_name_and_is_not_a_finding(
    rig, capsys, monkeypatch
) -> None:
    """Kills: putting `skipped` in FINDING_STATUSES.

    A requirement range of more than one clause states no single delta, so a control that
    reported it every night would be permanently red -- which is how a control stops being read.
    It is named in the output and it is not a finding.
    """
    estate, calls = rig
    monkeypatch.setattr(
        cli,
        "GitHubReader",
        lambda **kw: __import__("landing_ledger.github", fromlist=["GitHubReader"]).GitHubReader(
            token="gh",
            transport=httpx.MockTransport(
                _github(
                    title="chore(deps): update zod requirement from >=3.0,<4 to >=3.0,<5",
                    update_type=None,
                )
            ),
        ),
    )
    assert run(["--submit"]) == EXIT_OK
    assert calls == [] and estate.proposals == []
    assert "unclassifiable" in capsys.readouterr().out


def test_repository_in_neither_population_is_not_declared_landing(rig, capsys, monkeypatch) -> None:
    """THE SUCCESSOR TO `no-cascade`, and it is the same fact asked of the holder.

    A repository the landing policy declares in neither population is one where nobody has said
    what landing on its default branch does -- so what lands there unattended has no answer, and
    this producer will not guess. It is a finding rather than a quiet skip because a standing
    package targeting such a repository was authored for a lane that does not cover it.
    """
    estate, calls = rig
    document = json.loads(json.dumps(LIVE))
    document["inert_landing"]["repositories"].remove("alobarquest/infraops-mcp-server")
    monkeypatch.setattr(cli, "read_landing_policy", lambda token, base_url: parse(document))

    assert run(["--submit"]) == EXIT_FINDINGS
    out = capsys.readouterr().out
    assert "not-declared-landing" in out
    assert calls == [] and estate.proposals == []


def test_a_declaration_that_cannot_be_read_stops_the_pass_before_it_reads_github(
    rig, capsys, monkeypatch
) -> None:
    """UNUSABLE INPUT, NOT A FINDING, and it stops everything rather than one repository.

    One declaration covers every repository at once, so a rule this pass cannot read is not a
    fact about any single one of them -- reporting it per repository would be N copies of one
    thing. Nothing may be written on the strength of a rule nobody could read, which is why the
    lifecycle commands and the proposals are asserted empty and not merely unchanged.
    """
    estate, calls = rig

    def refuse(token, base_url):
        raise LandingPolicyError("change-manager answered 503 for the landing policy")

    monkeypatch.setattr(cli, "read_landing_policy", refuse)

    assert run(["--submit"]) == EXIT_UNUSABLE
    assert calls == [] and estate.proposals == []


def test_a_declaration_that_stopped_permitting_the_update_bot_refuses_rather_than_flooding(
    rig, capsys, monkeypatch
) -> None:
    """THE EXPENSIVE DIRECTION, and the reason the author is checked at all.

    `read_pending_updates` returns pull requests by `dependabot[bot]` and no others. If the
    declaration stopped permitting that author, every one of them would be a bump the lane
    refuses -- so every open update in the estate would become this lane's subject at once, each
    minting a package revision that cannot be unminted and spending a human approval. Refusing
    costs a pass; the next one picks it up if a person meant something else.
    """
    estate, calls = rig
    without = replace(parse(LIVE), permitted_authors=frozenset({"app/some-other-bot"}))
    monkeypatch.setattr(cli, "read_landing_policy", lambda token, base_url: without)

    assert run(["--submit"]) == EXIT_UNUSABLE
    assert calls == [] and estate.proposals == []


def test_a_missing_github_token_is_unusable_rather_than_a_clean_pass(checkout, monkeypatch) -> None:
    monkeypatch.delenv("BUMP_PROPOSER_GITHUB_TOKEN")
    assert run([]) == 2


def test_submitting_without_the_change_manager_credential_is_unusable(
    checkout, monkeypatch
) -> None:
    monkeypatch.delenv("BUMP_PROPOSER_CHANGE_MANAGER_TOKEN")
    assert run(["--submit"]) == 2


# --- publishing (ADR-0033) --------------------------------------------------------


def test_a_refused_publish_is_a_finding_and_no_record_is_proposed(rig, monkeypatch, capsys) -> None:
    """Kills: reporting a refused publish as a warning, and proposing the record anyway.

    ADR-0033 says a failed publish is a finding rather than a warning, and the proposal is
    withheld with it for a reason of its own: the intake this record eventually causes records
    `source_commit` as that checkout's HEAD, so a record for a revision nobody else can fetch
    sends the carry after a commit only one machine holds.
    """
    estate, _ = rig

    def refuse(package, bump, root):
        raise standing.StandingError("p rev 1 is committed as abcdef123456 and unpublished")

    for module in (standing, cli):
        monkeypatch.setattr(module, "commit", refuse)

    assert run(["--submit"]) == EXIT_FINDINGS
    assert estate.proposals == []
    assert "unpublished" in capsys.readouterr().out


def test_a_checkout_carrying_an_unpublished_commit_stops_the_pass(rig, monkeypatch) -> None:
    """Kills: running on past the residue a refused publish leaves.

    THE REPLAY PATH SKIPS THE PUBLISHING STEP. A pass allowed to continue here would find the
    package already carrying the bump and approved, propose the record, and exit clean -- with
    the previous pass's commit still unpublished and nothing left saying so. That is the
    finding reported once and then gone quiet, which is not a finding.
    """
    estate, calls = rig

    def refuse(root):
        raise standing.StandingError("the packages checkout carries 1 commit(s) origin does not")

    for module in (standing, cli):
        monkeypatch.setattr(module, "require_publishable", refuse)

    assert run(["--submit"]) == EXIT_UNUSABLE
    assert calls == []
    assert estate.proposals == []


def test_the_published_commit_is_named_in_the_pass_output(rig, capsys) -> None:
    """Kills: discarding what `commit` returns, which is what the pass did until ADR-0033.

    It is the `source_commit` the intake this record causes will record, and the pass is the
    only thing that ever knows it.
    """
    assert run(["--submit"]) == EXIT_OK
    assert "published cccccccccccc" in capsys.readouterr().out


def test_a_replay_names_no_published_commit(rig, capsys) -> None:
    """Kills: naming a sha regardless. A replay publishes nothing, so a sha beside `replayed`
    would be a claim about a commit this pass did not make."""
    assert run(["--submit"]) == EXIT_OK
    capsys.readouterr()

    assert run(["--submit"]) == EXIT_OK

    out = capsys.readouterr().out
    assert "replayed" in out
    assert "published" not in out


def test_a_dry_run_does_not_ask_whether_the_checkout_is_published(rig, monkeypatch, capsys) -> None:
    """Kills: asking the publication question outside the submit path.

    A bare invocation reports what a writing pass WOULD do and writes nothing, so it is exactly
    the tool an operator reaches for when the checkout is in a state a writing pass must refuse.
    A guard asked here answers exit 2 and prints no lines -- withdrawing the diagnostic at the
    moment it is most wanted, and doing so for a question no dry run can be the cause of.

    The sibling `require_clean` has always been inside `if submit:` for the same reason; nothing
    pinned it there, which is why the misplacement survived the first mutation run.
    """
    estate, calls = rig

    def refuse(root):
        raise standing.StandingError("the packages checkout carries 1 commit(s) origin does not")

    for module in (standing, cli):
        monkeypatch.setattr(module, "require_publishable", refuse)

    assert run([]) == EXIT_OK

    assert "would-advance" in capsys.readouterr().out
    assert calls == []
    assert estate.proposals == []


def test_a_refused_publish_stops_the_pass_minting_a_further_revision(
    rig, monkeypatch, capsys
) -> None:
    """Kills: asking the publishable question only once, at the top of the pass.

    A refused publish is caught PER PULL REQUEST and the pass goes on, so the residue it just
    created is invisible to a guard consulted only in `_lane`. Every later unit would then run
    the ladder again -- minting a revision number that cannot be unminted and committing another
    local commit refused the same way.

    The raiser answers the pass-level call and refuses the per-unit one, which is the only
    arrangement that isolates the second call site: a guard that raised on both would fail at
    `_lane` and never reach the acting path at all.
    """
    estate, calls = rig
    seen = 0

    def refuse_after_the_first_call(root):
        nonlocal seen
        seen += 1
        if seen > 1:
            raise standing.StandingError(
                "the packages checkout carries 1 commit(s) origin does not"
            )

    for module in (standing, cli):
        monkeypatch.setattr(module, "require_publishable", refuse_after_the_first_call)

    assert run(["--submit"]) == EXIT_FINDINGS

    assert seen == 2
    assert calls == []
    assert estate.proposals == []
    assert "origin does not" in capsys.readouterr().out


# ---------------------------------------------------------------------------------------------
# THE CAUSE (G1+G2). A machine-proposed record names the observation that caused it, and the
# producer files that observation first.
#
# THESE ASSERT ON THE PROPOSAL, not on what the client returned. Twice in one day this estate
# shipped a change fully tested in the module that computes a value and untested in the module
# that consumes it -- and the consumer here is the payload, which is the only thing change-manager
# ever sees.
# ---------------------------------------------------------------------------------------------


def test_the_proposal_CARRIES_the_id_of_the_observation_just_filed(rig, spine) -> None:
    estate, _calls = rig

    assert run(["--submit"]) == EXIT_OK

    assert len(spine.posted) == 1
    assert len(estate.proposals) == 1
    filed = spine.posted[0]["idempotency_key"]
    assert estate.proposals[0]["originating_observation_id"] == spine.rows[filed]


def test_the_observation_is_filed_BEFORE_anything_is_minted(rig, spine, capsys) -> None:
    """The order is the contract's, and it is asserted at the expensive end rather than the cheap
    one. A package revision cannot be unminted and a published commit cannot be unpublished, so
    filing before the ladder means a pass that cannot state the fact has spent neither -- where
    observing merely before the PROPOSAL would leave a revision standing for work nothing
    proposed, and a human approval spent on it.
    """
    estate, calls = rig
    spine.status = 500

    assert run(["--submit"]) == EXIT_FINDINGS

    assert spine.posted != []
    assert calls == []
    assert estate.proposals == []
    assert "unobserved" in capsys.readouterr().out


def test_a_bump_that_cannot_be_OBSERVED_is_a_finding_rather_than_a_quiet_skip(
    rig, spine, capsys
) -> None:
    """The bump is real and unproposed, and this line is the only thing that says so. No later
    pass repairs it on its own, which is what makes it a finding rather than a skip."""
    _estate, _calls = rig
    spine.status = 500

    assert run(["--submit"]) == EXIT_FINDINGS
    assert "1 findings" in capsys.readouterr().out


def test_a_REFUSED_OBSERVER_CREDENTIAL_stops_the_pass_rather_than_marking_each_bump(
    rig, spine, capsys
) -> None:
    """THE PAIR WITH THE 500 CASE ABOVE IS THE POINT, and either alone proves nothing.

    A 500 is an orchestrator that would not take THIS bump: the pull request loses its row, the
    pass reports a finding, and a later pass may well succeed. A 401 is the CREDENTIAL, which is
    every bump and every pass after it -- and reported per bump it exits with this lane's finding
    code, blaming every bump. The lane would then propose nothing for as long as the bearer stayed
    revoked behind a report that names the bumps, not the credential.
    """
    _estate, _calls = rig
    spine.status = 401

    assert run(["--submit"]) == EXIT_UNUSABLE
    assert "unobserved" not in capsys.readouterr().out


def test_a_NON_OBSERVER_key_id_refuses_BEFORE_anything_is_written(rig, monkeypatch, capsys) -> None:
    """The three `ORCHESTRATOR_API_*` names are the estate-wide ones every orchestrator client
    reads, and the route admits SYSTEM as well as OBSERVER. A hand-run `--submit` from a shell
    already carrying the SYSTEM bearer -- which is how the factory is driven by hand -- would file
    observations that SUCCEED, attributed to `orchestrator-system`, into an append-only table with
    no delete route. The attribution is permanent, so the refusal has to come first.
    """
    _estate, _calls = rig
    monkeypatch.setenv("ORCHESTRATOR_API_CREDENTIAL_KEY_ID", "orchestrator-system")

    assert run(["--submit"]) == EXIT_UNUSABLE
    assert "orchestrator-observer" in capsys.readouterr().err


def test_a_second_pass_names_the_SAME_observation_and_files_no_second_row(rig, spine) -> None:
    """The replay property at the producer end. The orchestrator returns the existing row for a
    repeated reference, so the pass names what it already filed -- and this asserts it through
    the ACTS rather than the final state, which is identical either way.
    """
    estate, _calls = rig

    assert run(["--submit"]) == EXIT_OK
    assert run(["--submit"]) == EXIT_OK

    assert len(spine.posted) == 2
    assert len(spine.rows) == 1
    causes = {proposal["originating_observation_id"] for proposal in estate.proposals}
    assert len(causes) == 1


def test_a_dry_run_files_NOTHING(rig, spine) -> None:
    """Filing an observation is a write, and this launcher's standing property is that a dry run
    touches no credential that could write. A dry run proposes nothing, so it has no cause to
    file."""
    estate, calls = rig

    assert run([]) == EXIT_OK

    assert spine.posted == []
    assert calls == []
    assert estate.proposals == []


def test_submit_without_the_observer_credential_is_UNUSABLE_rather_than_a_record_with_no_cause(
    rig, spine, monkeypatch, capsys
) -> None:
    """Fail closed, and before anything is minted. Refusing per pull request instead would spend a
    package revision discovering it."""
    estate, calls = rig
    monkeypatch.delenv("ORCHESTRATOR_API_TOKEN")

    assert run(["--submit"]) == EXIT_UNUSABLE

    assert spine.posted == []
    assert calls == []
    assert estate.proposals == []
    assert "observer credential is unset" in capsys.readouterr().err


def test_the_declared_field_set_is_EXACTLY_what_the_proposal_emits(rig, spine) -> None:
    """The declaration exists so the cross-repo field check has one local thing to compare
    change-manager's schema against, instead of a list retyped into the check script. A
    declaration that drifted from the payload would let the check vet the wrong vocabulary while
    passing -- so it is pinned to the real call rather than to a handwritten dict.
    """
    estate, _calls = rig

    assert run(["--submit"]) == EXIT_OK

    assert set(estate.proposals[0]) == set(cli.PROPOSAL_FIELDS)
    assert len(cli.PROPOSAL_FIELDS) == len(set(cli.PROPOSAL_FIELDS))


def _second_package(checkout, repository: str, name: str) -> None:
    directory = checkout / "packages" / name
    directory.mkdir(parents=True)
    (directory / "package.yaml").write_text(
        SHELL.replace("AlobarQuest/infraops-mcp-server", repository), encoding="utf-8"
    )


def test_the_population_is_granted_repositories_with_a_standing_package(
    rig, checkout, monkeypatch, capsys
) -> None:
    """Packages for A and B, the record grants A and C: only A is considered. C has no package and
    B is not granted; both are told, and neither is a finding."""
    _second_package(checkout, "AlobarQuest/ungranted-b", "ungranted-b-npm-zod")
    _grant(monkeypatch, "AlobarQuest/infraops-mcp-server", "AlobarQuest/granted-c")

    assert run([]) == EXIT_OK

    out = capsys.readouterr().out
    assert "[NO PACKAGE] AlobarQuest/granted-c" in out
    assert "[NOT GRANTED] AlobarQuest/ungranted-b" in out
    assert "ungranted-b#" not in out
    assert "infraops-mcp-server#" in out


@pytest.mark.parametrize("submit", [[], ["--submit"]])
def test_an_unreadable_member_record_stops_the_pass(rig, monkeypatch, capsys, submit) -> None:
    estate, calls = rig

    def unreadable(**_):
        raise MembersUnreadable("SDS_MEMBERS_OBSERVER_TOKEN is not set")

    monkeypatch.setattr(cli, "read_members_from_environment", unreadable)

    assert run(submit) == EXIT_UNUSABLE

    assert "[UNUSABLE]" in capsys.readouterr().err
    assert calls == [] and estate.proposals == [] and estate.records == []


def test_naming_an_ungranted_repository_refuses(rig, checkout, monkeypatch, capsys) -> None:
    estate, calls = rig
    _second_package(checkout, "AlobarQuest/ungranted-b", "ungranted-b-npm-zod")

    assert run(["--submit", "--repository", "AlobarQuest/ungranted-b"]) == EXIT_UNUSABLE

    assert "does not grant dependency_updates" in capsys.readouterr().err
    assert calls == [] and estate.proposals == []


def test_the_grant_check_folds_case(rig, monkeypatch, capsys) -> None:
    _grant(monkeypatch, "alobarquest/INFRAOPS-mcp-server")

    assert run(["--repository", "ALOBARQUEST/infraops-mcp-server"]) == EXIT_OK

    assert "infraops-mcp-server#" in capsys.readouterr().out


def test_a_remediating_grant_leaves_an_empty_population_and_exits_unusable(
    rig, monkeypatch, capsys
) -> None:
    members = [_member("AlobarQuest/infraops-mcp-server", dependency_updates="remediating")]
    monkeypatch.setattr(cli, "read_members_from_environment", lambda **_: members)

    assert run([]) == EXIT_UNUSABLE

    assert "[NOT GRANTED]" in capsys.readouterr().out


# ---------------------------------------------------------------------------------------------
# Bridge Part 3. A failed bump with tooling and no standing package gets one created; a bump the
# factory can't route sends one keyed alert, which resolves when its pull request is gone.
# ---------------------------------------------------------------------------------------------

LEFT_PAD = Pull(
    number=72,
    title="build(deps): bump left-pad from 1.0.0 to 2.0.0",
    dependency="left-pad",
)
CREATED_ID = "infraops-mcp-server-npm-left-pad"


def _key(ecosystem: str, dependency: str) -> str:
    return f"dependency-bump:alobarquest/infraops-mcp-server:{ecosystem}:{dependency}"


def _record_proposals_in(calls, estate, monkeypatch) -> None:
    handler = estate.handler

    def recording(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            calls.append(("propose",))
        return handler(request)

    monkeypatch.setattr(estate, "handler", recording)


def test_a_failed_bump_without_a_package_creates_one_then_proposes(
    rig, monkeypatch, capsys
) -> None:
    estate, calls = rig
    _record_proposals_in(calls, estate, monkeypatch)
    _serve(monkeypatch, pulls=[LEFT_PAD])

    assert run(["--submit"]) == EXIT_OK

    assert [c[0] for c in calls] == [
        "create-standing",
        "transition",
        "approve",
        "hash",
        "commit",
        "propose",
    ]
    assert calls[0] == (
        "create-standing",
        "--target-repo",
        "AlobarQuest/infraops-mcp-server",
        "--package",
        "left-pad",
        "--ecosystem",
        "npm",
        "--from",
        "1.0.0",
        "--to",
        "2.0.0",
        "--out",
        "packages",
        "--reach",
        "source_repository",
    )
    assert [(p["package_id"], p["package_revision"]) for p in estate.proposals] == [(CREATED_ID, 1)]
    out = capsys.readouterr().out
    assert "created" in out and CREATED_ID in out


def test_a_created_package_is_not_created_twice(rig, monkeypatch) -> None:
    """The second pass finds the package the first one created and replays."""
    estate, calls = rig
    _serve(monkeypatch, pulls=[LEFT_PAD])
    assert run(["--submit"]) == EXIT_OK
    calls.clear()

    assert run(["--submit"]) == EXIT_OK

    assert calls == []
    assert len(estate.records) == 1


def _range_pull(ecosystem: str, operator: str, dependency: str = "left-pad") -> Pull:
    return Pull(
        number=73,
        title=(
            f"chore(deps): update {dependency} requirement from {operator}1.2.0 to {operator}1.3.0"
        ),
        dependency=dependency,
        ecosystem=ecosystem,
        update_type=None,
    )


@pytest.mark.parametrize("operator", [">=", "=="])
def test_a_failed_uv_range_it_can_carry_creates_a_package_with_its_bare_versions(
    rig, monkeypatch, operator
) -> None:
    estate, calls = rig
    _serve(monkeypatch, pulls=[_range_pull("uv", operator)])

    assert run(["--submit"]) == EXIT_OK

    assert calls[0][calls[0].index("--ecosystem") + 1] == "uv"
    assert calls[0][calls[0].index("--from") + 1] == "1.2.0"
    assert calls[0][calls[0].index("--to") + 1] == "1.3.0"
    assert len(estate.proposals) == 1


@pytest.mark.parametrize(
    ("ecosystem", "tooling", "operator"),
    [
        # uv's discovery reads `==` and `>=` pins only.
        ("uv", "uv", "~="),
        # npm installs `--save-exact`, so any range would become an exact pin.
        ("npm_and_yarn", "npm", ">="),
        ("npm_and_yarn", "npm", "^"),
        ("npm_and_yarn", "npm", "~"),
        # pip's mutation rewrites `==` lines only.
        ("pip", "pip", ">="),
    ],
)
def test_a_range_its_tooling_cannot_carry_alerts(
    rig, monkeypatch, hub, ecosystem, tooling, operator
) -> None:
    estate, calls = rig
    _serve(monkeypatch, pulls=[_range_pull(ecosystem, operator)])

    assert run(["--submit"]) == EXIT_OK

    assert calls == [] and estate.proposals == []
    assert hub.keys("firing") == [_key(tooling, "left-pad")]


def test_a_pip_range_bump_alerts(rig, monkeypatch, hub, capsys) -> None:
    """Review focus 5: pip's mutation command matches `==` lines only, so a range never becomes a
    package whose mutation can't apply."""
    estate, calls = rig
    _serve(
        monkeypatch,
        pulls=[
            Pull(
                number=74,
                title="chore(deps): update requests requirement from >=2.31.0 to >=2.32.0",
                dependency="requests",
                ecosystem="pip",
                update_type=None,
            )
        ],
    )

    assert run(["--submit"]) == EXIT_OK

    assert calls == [] and estate.proposals == []
    assert hub.keys("firing") == [_key("pip", "requests")]
    assert "alerted" in capsys.readouterr().out


@pytest.mark.parametrize("conclusion", ["success", "failure"])
def test_a_docker_bump_alerts_whether_or_not_it_passes(rig, monkeypatch, hub, conclusion) -> None:
    """Ruling 6: the cascade excludes docker and nothing else handles it."""
    estate, calls = rig
    _serve(
        monkeypatch,
        pulls=[
            Pull(
                number=75,
                title="chore(deps): bump python from 3.12-slim to 3.14-slim",
                dependency="python",
                ecosystem="docker",
                update_type=None,
                conclusion=conclusion,
            )
        ],
    )

    assert run(["--submit"]) == EXIT_OK

    assert calls == [] and estate.proposals == []
    assert hub.keys("firing") == [_key("docker", "python")]


def _actions(conclusion: str) -> Pull:
    return Pull(
        number=76,
        title="chore(actions): bump actions/checkout from 4 to 7",
        dependency="actions/checkout",
        ecosystem="github_actions",
        conclusion=conclusion,
    )


def test_a_github_actions_failure_alerts(rig, monkeypatch, hub) -> None:
    estate, calls = rig
    _serve(monkeypatch, pulls=[_actions("failure")])

    assert run(["--submit"]) == EXIT_OK

    assert calls == [] and estate.proposals == []
    assert hub.keys("firing") == [_key("github__actions", "actions/checkout")]


def test_a_passing_github_actions_bump_is_left_to_the_cascade(rig, monkeypatch, hub) -> None:
    """The control: no tooling is not by itself an alert, a bump the cascade lands is nothing."""
    _serve(monkeypatch, pulls=[_actions("success")])

    assert run(["--submit"]) == EXIT_OK

    assert hub.sent == []


def test_a_second_ecosystem_for_one_dependency_alerts_not_creates(
    rig, monkeypatch, hub, capsys
) -> None:
    """Review focus 2: discovery keys on (repository, dependency) and refuses a duplicate, so a
    second package for zod would stop every later pass."""
    estate, calls = rig
    _serve(monkeypatch, pulls=[Pull(number=77, dependency="zod", ecosystem="uv")])

    assert run(["--submit"]) == EXIT_OK

    assert calls == [] and estate.proposals == []
    assert hub.keys("firing") == [_key("uv", "zod")]
    assert "infraops-mcp-server-npm-zod" in capsys.readouterr().out


def test_the_proposal_carries_target_repository_and_change_class(rig) -> None:
    estate, _calls = rig

    assert run(["--submit"]) == EXIT_OK

    proposal = estate.proposals[0]
    assert proposal["target_repository"] == "AlobarQuest/infraops-mcp-server"
    assert proposal["change_class"] == "dependency-update"


def test_a_dry_run_reports_would_create_and_would_alert_and_writes_nothing(
    rig, checkout, monkeypatch, hub, spine, capsys
) -> None:
    estate, calls = rig
    monkeypatch.delenv("SDS_BRIDGE_ALERT_TOKEN")
    _serve(monkeypatch, pulls=[LEFT_PAD, _actions("failure")])

    assert run([]) == EXIT_OK

    out = capsys.readouterr().out
    assert "would-create" in out and "would-alert" in out
    assert calls == [] and estate.proposals == [] and spine.posted == [] and hub.sent == []
    assert not (checkout / "packages" / CREATED_ID).exists()
    assert not (checkout / "state").exists()


def test_an_alert_fires_once_and_resolves_when_its_pull_request_closes(
    rig, monkeypatch, hub
) -> None:
    _serve(monkeypatch, pulls=[_actions("failure")])
    assert run(["--submit"]) == EXIT_OK
    assert run(["--submit"]) == EXIT_OK
    assert hub.keys("firing") == [_key("github__actions", "actions/checkout")]

    _serve(monkeypatch, pulls=[])
    assert run(["--submit"]) == EXIT_OK

    assert hub.keys("resolved") == [_key("github__actions", "actions/checkout")]


def test_a_failure_that_has_not_settled_holds_its_alert(rig, monkeypatch, hub) -> None:
    """Kills: resolving an open alert while a re-run fails again but hasn't settled -- it would
    re-fire once settled, and alert-hub opens a second alert for one condition."""
    _serve(monkeypatch, pulls=[_actions("failure")])
    assert run(["--submit"]) == EXIT_OK

    just_now = datetime.now(UTC).isoformat()
    _serve(monkeypatch, pulls=[replace(_actions("failure"), completed_at=just_now)])
    assert run(["--submit"]) == EXIT_OK

    assert hub.keys("resolved") == []
    assert hub.keys("firing") == [_key("github__actions", "actions/checkout")]


PIP_RANGE = Pull(
    number=74,
    title="chore(deps): update requests requirement from >=2.31.0 to >=2.32.0",
    dependency="requests",
    ecosystem="pip",
    update_type=None,
)
SECOND_ECOSYSTEM = Pull(number=77, dependency="zod", ecosystem="uv")


@pytest.mark.parametrize(
    ("pull", "key"),
    [(PIP_RANGE, _key("pip", "requests")), (SECOND_ECOSYSTEM, _key("uv", "zod"))],
    ids=["pip-range", "second-ecosystem"],
)
def test_an_unsettled_rerun_holds_every_kind_of_alert(rig, monkeypatch, hub, pull, key) -> None:
    """The hold is one rule after routing, not a no-tooling special case."""
    _serve(monkeypatch, pulls=[pull])
    assert run(["--submit"]) == EXIT_OK

    just_now = datetime.now(UTC).isoformat()
    _serve(monkeypatch, pulls=[replace(pull, completed_at=just_now)])
    assert run(["--submit"]) == EXIT_OK
    _serve(monkeypatch, pulls=[pull])
    assert run(["--submit"]) == EXIT_OK

    assert hub.keys("resolved") == []
    assert hub.keys("firing") == [key]


def test_a_rerun_that_passes_resolves_its_alert(rig, monkeypatch, hub) -> None:
    """The control: a green re-run is the condition cleared."""
    _serve(monkeypatch, pulls=[_actions("failure")])
    assert run(["--submit"]) == EXIT_OK

    _serve(monkeypatch, pulls=[_actions("success")])
    assert run(["--submit"]) == EXIT_OK

    assert hub.keys("resolved") == [_key("github__actions", "actions/checkout")]


def test_an_unreadable_repository_keeps_its_alerts(rig, monkeypatch, hub) -> None:
    """Kills: resolving every remembered key the pass did not see. A GitHub failure is not the
    pull request closing."""
    _serve(monkeypatch, pulls=[_actions("failure")])
    assert run(["--submit"]) == EXIT_OK

    _serve(monkeypatch, unreadable=True)
    assert run(["--submit"]) == EXIT_FINDINGS

    assert hub.keys("resolved") == []


def _remember(checkout, *keys: str) -> None:
    state = checkout / "state"
    state.mkdir(exist_ok=True)
    (state / alerts.STATE_FILE).write_text(json.dumps(sorted(keys)))


OTHER_KEY = "dependency-bump:alobarquest/factory-runner:docker:python"


def test_a_run_narrowed_to_one_repository_leaves_the_others_alerts(
    rig, checkout, monkeypatch, hub
) -> None:
    _remember(checkout, OTHER_KEY)
    _serve(monkeypatch, pulls=[])

    assert run(["--submit", "--repository", "AlobarQuest/infraops-mcp-server"]) == EXIT_OK

    assert hub.keys("resolved") == []


def test_a_full_run_resolves_a_key_no_repository_raises_any_more(
    rig, checkout, monkeypatch, hub
) -> None:
    """The control for the narrowed run: the same remembered key IS resolved by a full pass."""
    _remember(checkout, OTHER_KEY)
    _serve(monkeypatch, pulls=[])

    assert run(["--submit"]) == EXIT_OK

    assert hub.keys("resolved") == [OTHER_KEY]


def test_an_alert_hub_failure_is_a_finding(rig, monkeypatch, hub, capsys) -> None:
    hub.status = 500
    _serve(monkeypatch, pulls=[_actions("failure")])

    assert run(["--submit"]) == EXIT_FINDINGS

    assert "alert-hub" in capsys.readouterr().out


def test_submitting_without_the_sender_token_is_unusable(rig, monkeypatch, hub, capsys) -> None:
    estate, calls = rig
    monkeypatch.delenv("SDS_BRIDGE_ALERT_TOKEN")

    assert run(["--submit"]) == EXIT_UNUSABLE

    assert calls == [] and estate.proposals == [] and hub.sent == []
    assert "SDS_BRIDGE_ALERT_TOKEN" in capsys.readouterr().err


def test_a_granted_inert_repository_with_no_package_is_in_the_lane(
    rig, checkout, monkeypatch, capsys
) -> None:
    """Scope is the grant now that a package can be created: removing the only package leaves the
    repository in the lane, and its failed bump creates one."""
    estate, calls = rig
    for path in sorted((checkout / "packages").rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()

    assert run(["--submit"]) == EXIT_OK

    assert calls[0][0] == "create-standing"
    assert "[NO PACKAGE]" not in capsys.readouterr().out


def test_a_granted_repository_in_neither_population_with_no_package_is_told_not_found(
    rig, monkeypatch, capsys
) -> None:
    """A granted repository the landing policy declares in neither population, with no package,
    is out of the lane and is not a finding (with a package it is `not-declared-landing`)."""
    _grant(monkeypatch, "AlobarQuest/infraops-mcp-server", "AlobarQuest/rtk")

    assert run([]) == EXIT_OK

    out = capsys.readouterr().out
    assert "[NO PACKAGE] AlobarQuest/rtk" in out
    assert "rtk#" not in out


def _git(cwd, *args: str) -> str:
    import subprocess

    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def test_a_refused_approval_after_create_leaves_a_checkout_the_next_pass_accepts(
    rig, checkout, monkeypatch, capsys
) -> None:
    """Kills: leaving the created directory behind. Untracked, it would make `require_clean`
    refuse every later `--submit` pass until a person cleaned the checkout."""
    estate, calls = rig
    _git(checkout, "init", "-q", "-b", "main")
    for setting in (("user.name", "t"), ("user.email", "t@example.invalid")):
        _git(checkout, "config", *setting)
    (checkout / ".gitignore").write_text("state/\n")
    _git(checkout, "add", "-A")
    _git(checkout, "commit", "-q", "-m", "seed")
    for module in (standing, cli):
        monkeypatch.setattr(module, "require_clean", REAL_REQUIRE_CLEAN)
    _serve(monkeypatch, pulls=[LEFT_PAD])
    stub = standing.lifecycle

    def refuse_approval(root, *args):
        if args[0] == "approve":
            raise standing.StandingError("approve --by-policy refused: member record unreadable")
        return stub(root, *args)

    monkeypatch.setattr(standing, "lifecycle", refuse_approval)
    assert run(["--submit"]) == EXIT_FINDINGS
    assert not (checkout / "packages" / CREATED_ID).exists()
    assert _git(checkout, "status", "--porcelain") == ""
    assert estate.proposals == []

    monkeypatch.setattr(standing, "lifecycle", stub)
    capsys.readouterr()
    assert run(["--submit"]) == EXIT_OK
    assert [(p["package_id"], p["package_revision"]) for p in estate.proposals] == [(CREATED_ID, 1)]


def test_a_repository_not_declared_landing_keeps_its_alerts(
    rig, checkout, monkeypatch, hub
) -> None:
    """A repository with a package that the landing policy stops declaring in either population
    is not read, so its remembered keys stay as they were."""
    remembered = _key("docker", "python")
    _remember(checkout, remembered)
    document = json.loads(json.dumps(LIVE))
    document["inert_landing"]["repositories"].remove("alobarquest/infraops-mcp-server")
    monkeypatch.setattr(cli, "read_landing_policy", lambda token, base_url: parse(document))

    assert run(["--submit"]) == EXIT_FINDINGS

    assert hub.keys("resolved") == []


# ---------------------------------------------------------------------------------------------
# Bridge Part 4. A failed bump past a hold in the member record is skipped: nothing is created,
# observed, proposed or alerted, and an alert raised for it before the hold resolves.
# ---------------------------------------------------------------------------------------------


def _hold(monkeypatch, *holds: Hold) -> None:
    members = [_member(REPOSITORY, holds=holds)]
    monkeypatch.setattr(cli, "read_members_from_environment", lambda **_: members)


def test_a_held_bump_is_skipped_and_nothing_is_written(
    rig, monkeypatch, spine, hub, capsys
) -> None:
    estate, calls = rig
    _hold(monkeypatch, Hold("npm_and_yarn", "/", "zod", "3.99"))
    _serve(monkeypatch, pulls=[Pull(), LEFT_PAD])

    assert run(["--submit"]) == EXIT_OK

    out = capsys.readouterr().out
    assert "ceiling-exceeded" in out
    # Only left-pad, which no hold names, is created and proposed.
    assert [p["package_id"] for p in estate.proposals] == [CREATED_ID]
    assert len(spine.posted) == 1
    assert hub.keys("firing") == []


def test_a_hold_applies_whatever_the_case_of_the_members_name(
    rig, monkeypatch, spine, hub, capsys
) -> None:
    """Kills: the holds keyed by the record's spelling uncased. The package names
    `AlobarQuest/infraops-mcp-server` and the scope takes its spelling; the record's differs."""
    estate, calls = rig
    members = [_member(REPOSITORY.upper(), holds=(Hold("npm_and_yarn", "/", "zod", "3.99"),))]
    monkeypatch.setattr(cli, "read_members_from_environment", lambda **_: members)
    _serve(monkeypatch, pulls=[Pull(), LEFT_PAD])

    assert run(["--submit"]) == EXIT_OK

    assert "ceiling-exceeded" in capsys.readouterr().out
    assert [p["package_id"] for p in estate.proposals] == [CREATED_ID]


def test_a_hold_resolves_the_alert_its_bump_raised(rig, monkeypatch, hub) -> None:
    """Kills: a held skip that names the alert's key (holding it open) or raises it again."""
    _serve(monkeypatch, pulls=[Pull(number=77, dependency="zod", ecosystem="uv")])
    assert run(["--submit"]) == EXIT_OK
    assert hub.keys("firing") == [_key("uv", "zod")]

    _hold(monkeypatch, Hold("uv", "/", "zod", "99"))
    assert run(["--submit"]) == EXIT_OK

    assert hub.keys("firing") == [_key("uv", "zod")]
    assert hub.keys("resolved") == [_key("uv", "zod")]


# ---------------------------------------------------------------------------------------------
# Widening to repositories that deploy on landing (ADR-0028 amendment 3). The stub GitHub serves
# one repository, so these tests move it from the inert population to the deploying one rather
# than serving another: what changes is which block the landing policy declares it in.
# ---------------------------------------------------------------------------------------------


def _deploying(monkeypatch, *, also_inert: bool = False) -> None:
    """Declare the rig's repository deploying (and, with `also_inert`, inert as well)."""
    document = json.loads(json.dumps(LIVE))
    if not also_inert:
        document["inert_landing"]["repositories"].remove("alobarquest/infraops-mcp-server")
    document["repositories"].append("alobarquest/infraops-mcp-server")
    monkeypatch.setattr(cli, "read_landing_policy", lambda token, base_url: parse(document))


def test_failed_bump_in_deploying_repository_creates_live_estate_package(
    rig, monkeypatch, capsys
) -> None:
    estate, calls = rig
    _deploying(monkeypatch)
    _serve(monkeypatch, pulls=[LEFT_PAD])

    assert run(["--submit"]) == EXIT_OK

    assert calls[0][0] == "create-standing"
    assert calls[0][-2:] == ("--reach", "live_estate")
    assert [(p["package_id"], p["package_revision"]) for p in estate.proposals] == [(CREATED_ID, 1)]
    out = capsys.readouterr().out
    assert "created" in out and "proposed" in out


def test_failed_bump_in_inert_repository_still_creates_source_repository_package(
    rig, monkeypatch
) -> None:
    estate, calls = rig
    _serve(monkeypatch, pulls=[LEFT_PAD])

    assert run(["--submit"]) == EXIT_OK

    assert calls[0][0] == "create-standing"
    assert calls[0][-2:] == ("--reach", "source_repository")


def test_a_dry_run_names_the_reach_it_would_create(rig, monkeypatch, capsys) -> None:
    _deploying(monkeypatch)
    _serve(monkeypatch, pulls=[LEFT_PAD])

    assert run([]) == EXIT_OK

    assert "would-create" in (out := capsys.readouterr().out) and "live_estate" in out


def test_passing_bump_in_deploying_repository_is_skipped(rig, monkeypatch, hub, capsys) -> None:
    """Review focus 3: the estate lander has it; it never becomes a package."""
    estate, calls = rig
    _deploying(monkeypatch)
    _serve(monkeypatch, pulls=[replace(LEFT_PAD, conclusion="success")])

    assert run(["--submit"]) == EXIT_OK

    assert calls == [] and estate.proposals == [] and hub.sent == []
    assert "skipped" in capsys.readouterr().out


def test_github_actions_bump_in_deploying_repository_is_this_lanes(rig, monkeypatch, hub) -> None:
    """The `landing` block excludes `github_actions`, so even a passing one is this lane's -- and
    with no tooling, an alert. The inert control is
    `test_a_passing_github_actions_bump_is_left_to_the_cascade`."""
    estate, calls = rig
    _deploying(monkeypatch)
    _serve(monkeypatch, pulls=[_actions("success")])

    assert run(["--submit"]) == EXIT_OK

    assert calls == [] and estate.proposals == []
    assert hub.keys("firing") == [_key("github__actions", "actions/checkout")]


def test_docker_bump_in_deploying_repository_is_left_to_the_lander(rig, monkeypatch, hub) -> None:
    """The `landing` block does not exclude docker, so a passing docker bump is the lander's. The
    inert control is `test_a_docker_bump_alerts_whether_or_not_it_passes`."""
    estate, calls = rig
    _deploying(monkeypatch)
    _serve(
        monkeypatch,
        pulls=[
            Pull(
                number=75,
                title="chore(deps): bump python from 3.12-slim to 3.14-slim",
                dependency="python",
                ecosystem="docker",
                update_type=None,
                conclusion="success",
            )
        ],
    )

    assert run(["--submit"]) == EXIT_OK

    assert calls == [] and estate.proposals == [] and hub.sent == []


@pytest.mark.parametrize("deploying", [[], None], ids=["empty", "missing"])
def test_policy_without_deploying_population_is_unreadable(rig, monkeypatch, deploying) -> None:
    """Review focus 4: exit 2, never a lane silently shrunk to inert."""
    estate, calls = rig
    document = json.loads(json.dumps(LIVE))
    if deploying is None:
        del document["repositories"]
    else:
        document["repositories"] = deploying
    monkeypatch.setattr(cli, "read_landing_policy", lambda token, base_url: parse(document))

    assert run(["--submit"]) == EXIT_UNUSABLE

    assert calls == [] and estate.proposals == []


@pytest.mark.parametrize("landing", [None, {"update_types": []}], ids=["missing", "update-type"])
def test_policy_whose_landing_block_declares_no_exclusions_is_unreadable(
    rig, monkeypatch, capsys, landing
) -> None:
    """A `landing` block without `excluded_ecosystems` decides by update type, which this
    producer does not evaluate: exit 2, not "nothing excluded"."""
    estate, calls = rig
    document = json.loads(json.dumps(LIVE))
    if landing is None:
        del document["landing"]
    else:
        document["landing"] = landing
    monkeypatch.setattr(cli, "read_landing_policy", lambda token, base_url: parse(document))

    assert run(["--submit"]) == EXIT_UNUSABLE

    assert calls == [] and estate.proposals == []
    assert "by update type" in capsys.readouterr().err


def test_repository_in_both_populations_deploys(rig, monkeypatch) -> None:
    """Review focus 2: the more restrained reach."""
    estate, calls = rig
    _deploying(monkeypatch, also_inert=True)
    _serve(monkeypatch, pulls=[LEFT_PAD])

    assert run(["--submit"]) == EXIT_OK

    assert calls[0][-2:] == ("--reach", "live_estate")
