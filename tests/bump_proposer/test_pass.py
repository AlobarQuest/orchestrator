"""The whole pass, driven through the real readers over a stubbed GitHub and change-manager.

**THE IDEMPOTENCY PROOF LIVES HERE, in both directions.** A first pass advances a standing
package and proposes a record; a second pass over the same estate advances nothing and replays
the proposal. Both halves are asserted by COUNTING the acts -- the lifecycle commands run and
the proposals sent -- rather than by reading the final state, because the final state is
identical either way and is exactly what a producer that revised twice would also show.
"""

from __future__ import annotations

import json
import os
import pathlib

import httpx
import pytest

from bump_proposer import cli, standing
from bump_proposer.cli import EXIT_FINDINGS, EXIT_OK, EXIT_UNUSABLE, run

GATE = "a4a4b8da035292fe434badd007607d8a69bc54e2"  # the cascade as installed on that repository
REPOSITORY = "AlobarQuest/infraops-mcp-server"

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
    return tmp_path


def _github(
    title: str = "build(deps): bump zod from 3.25.76 to 4.4.3",
    update_type: str | None = "version-update:semver-major",
):
    trailer = (
        "build(deps): bump zod\n\n---\nupdated-dependencies:\n- dependency-name: zod\n"
        f"  update-type: {update_type}\n...\n"
        if update_type
        else "build(deps): bump zod\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == f"/repos/{REPOSITORY}":
            return httpx.Response(200, json={"default_branch": "main"})
        if path.endswith("/contents/.github/workflows/dependabot-auto-merge.yml"):
            return httpx.Response(200, json={"sha": GATE})
        if path == f"/repos/{REPOSITORY}/pulls":
            return httpx.Response(200, json=[{"number": 71, "user": {"login": "dependabot[bot]"}}])
        if path == f"/repos/{REPOSITORY}/pulls/71":
            return httpx.Response(
                200,
                json={
                    "number": 71,
                    "title": title,
                    "created_at": "2026-08-01T00:00:00+00:00",
                    "auto_merge": None,
                    "head": {"sha": "b" * 40, "ref": "dependabot/npm_and_yarn/zod-4.4.3"},
                },
            )
        if path == f"/repos/{REPOSITORY}/commits/{'b' * 40}":
            return httpx.Response(200, json={"commit": {"message": trailer}})
        if path == f"/repos/{REPOSITORY}/actions/runs":
            return httpx.Response(200, json={"workflow_runs": []})
        return httpx.Response(404, json={})

    return handler


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


@pytest.fixture
def rig(checkout, monkeypatch):
    estate = _Estate()
    calls: list[tuple[str, ...]] = []

    def lifecycle(root, *args):
        calls.append(args)
        path = checkout / "packages" / "infraops-mcp-server-npm-zod" / "package.yaml"
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

    monkeypatch.setattr(standing, "_lifecycle", lifecycle)
    # Patched on `cli` as well as on `standing`: the CLI imports these by NAME, so a patch on
    # the defining module alone leaves the caller holding the original.
    for module in (standing, cli):
        monkeypatch.setattr(module, "require_clean", lambda root: None)
        monkeypatch.setattr(module, "require_publishable", lambda root: None)
        monkeypatch.setattr(module, "commit", lambda package, bump, root: "c" * 40)
    monkeypatch.setattr(
        cli,
        "GitHubReader",
        lambda **kw: __import__("landing_ledger.github", fromlist=["GitHubReader"]).GitHubReader(
            token="gh", transport=httpx.MockTransport(_github())
        ),
    )
    monkeypatch.setattr(
        cli,
        "ChangeManagerClient",
        lambda token, base_url=None: __import__(
            "bump_proposer.change_manager", fromlist=["ChangeManagerClient"]
        ).ChangeManagerClient(token, transport=httpx.MockTransport(estate.handler)),
    )
    return estate, calls


def test_a_first_pass_advances_the_package_and_proposes_the_record(rig, capsys) -> None:
    estate, calls = rig
    assert run(["--submit"]) == EXIT_OK
    assert [c[0] for c in calls] == ["transition", "approve", "hash"]
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

    assert [c[0] for c in calls] == ["transition", "approve", "hash"]
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


def test_a_dry_run_writes_nothing_and_touches_no_credential(rig, capsys, monkeypatch) -> None:
    estate, calls = rig
    monkeypatch.delenv("BUMP_PROPOSER_CHANGE_MANAGER_TOKEN")
    assert run([]) == EXIT_OK
    assert calls == [] and estate.proposals == []
    assert "would-advance" in capsys.readouterr().out


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

    assert [c[0] for c in calls] == ["revise", "transition", "approve", "hash"]
    assert [r["package_revision"] for r in estate.records] == [1, 2]
    out = capsys.readouterr().out
    assert "superseded" in out and "record 1" in out


def test_an_unclassifiable_bump_is_reported_by_name_and_is_not_a_finding(
    rig, capsys, monkeypatch
) -> None:
    """Kills: putting `skipped` in FINDING_STATUSES.

    A requirement range can never be classified by any rule about update types, so a control
    that reported it every night would be permanently red -- which is how a control stops being
    read. It is named in the output and it is not a finding.
    """
    estate, calls = rig
    monkeypatch.setattr(
        cli,
        "GitHubReader",
        lambda **kw: __import__("landing_ledger.github", fromlist=["GitHubReader"]).GitHubReader(
            token="gh",
            transport=httpx.MockTransport(
                _github(
                    title="chore(deps): update zod requirement from >=3.0.0 to >=4.0.0",
                    update_type=None,
                )
            ),
        ),
    )
    assert run(["--submit"]) == EXIT_OK
    assert calls == [] and estate.proposals == []
    assert "unclassifiable" in capsys.readouterr().out


def test_an_untranscribed_gate_refuses_the_whole_repository(rig, capsys, monkeypatch) -> None:
    """Fail closed on a gate revision nobody classified: which bumps it refuses is unknown, so
    every pull request on that repository is unclassifiable by this producer, not permitted."""
    estate, calls = rig

    def handler(request):
        if request.url.path.endswith("/contents/.github/workflows/dependabot-auto-merge.yml"):
            return httpx.Response(200, json={"sha": "0" * 40})
        return _github()(request)

    monkeypatch.setattr(
        cli,
        "GitHubReader",
        lambda **kw: __import__("landing_ledger.github", fromlist=["GitHubReader"]).GitHubReader(
            token="gh", transport=httpx.MockTransport(handler)
        ),
    )
    assert run(["--submit"]) == EXIT_FINDINGS
    assert calls == [] and estate.proposals == []
    assert "gate-not-transcribed" in capsys.readouterr().out


def test_a_repository_with_no_cascade_says_so_rather_than_untranscribed(
    rig, capsys, monkeypatch
) -> None:
    """ "Not applicable" is a different answer from "not measured", and this producer can give
    both. Two repositories in the estate deliberately carry no gate; reporting one of them as an
    untranscribed gate accuses somebody of not having transcribed a file that is not there."""
    estate, calls = rig

    def handler(request):
        if request.url.path.endswith("/contents/.github/workflows/dependabot-auto-merge.yml"):
            return httpx.Response(404, json={})
        return _github()(request)

    monkeypatch.setattr(
        cli,
        "GitHubReader",
        lambda **kw: __import__("landing_ledger.github", fromlist=["GitHubReader"]).GitHubReader(
            token="gh", transport=httpx.MockTransport(handler)
        ),
    )
    assert run(["--submit"]) == EXIT_FINDINGS
    out = capsys.readouterr().out
    assert "no-cascade" in out and "gate-not-transcribed" not in out
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
