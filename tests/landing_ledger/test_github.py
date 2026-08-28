"""Reading GitHub: which commits count as landings, and what may be read about each.

Payload shapes are copied from live responses for `AlobarQuest/intent-packages` on 2026-08-07 --
pull request #50 (the gate landed it), #62 (a person landed it) and commit a0563643 (pushed
straight at the branch).
"""

import httpx
import pytest

from landing_ledger.github import (
    ForbiddenMethodError,
    GitHubReader,
    LedgerError,
    factory_claim,
    first_parent_chain,
    landing_shas,
    policy_permission,
    read_landing,
    update_metadata,
)

REPO = "AlobarQuest/intent-packages"
GATE = ".github/workflows/dependabot-auto-merge.yml"
MERGED_AT = "2026-08-07T12:42:04Z"

TRAILER = """chore(deps-dev): bump ruff from 0.15.22 to 0.16.1 (#50)

---
updated-dependencies:
- dependency-name: ruff
  dependency-version: 0.16.0
  dependency-type: direct:development
  update-type: version-update:semver-minor
...
"""


def _commit(sha: str, parents: list[str]) -> dict[str, object]:
    return {"sha": sha, "parents": [{"sha": parent} for parent in parents]}


def _run(run_id: int, path: str, event: str, updated: str, conclusion: str) -> dict[str, object]:
    return {
        "id": run_id,
        "path": path,
        "event": event,
        "updated_at": updated,
        "conclusion": conclusion,
    }


def reader_for(routes: dict[str, object]) -> GitHubReader:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path not in routes:
            return httpx.Response(404, json=None)
        return httpx.Response(200, json=routes[request.url.path])

    return GitHubReader(token="fixture", transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------------------------
# A landing is a first-parent commit.
# ---------------------------------------------------------------------------------------------


def test_a_merge_commits_side_branch_did_not_land_on_its_own() -> None:
    """intent-packages carries 12 true merge commits in its last 100, and each drags its whole
    branch into `GET /commits?sha=main`. Those commits did not land; the merge did."""
    listing = [
        _commit("tip", ["merge"]),
        _commit("merge", ["base", "branch2"]),
        _commit("branch2", ["branch1"]),
        _commit("branch1", ["base"]),
        _commit("base", []),
    ]

    chain = [commit["sha"] for commit in first_parent_chain(listing, "tip")]

    assert chain == ["tip", "merge", "base"]
    assert {"branch1", "branch2"}.isdisjoint(chain)


def test_the_walk_starts_at_the_branchs_own_tip_not_the_newest_commit_listed() -> None:
    routes = {
        f"/repos/{REPO}": {"default_branch": "main"},
        f"/repos/{REPO}/branches/main": {"commit": {"sha": "tip"}},
        f"/repos/{REPO}/commits": [
            _commit("branch1", ["base"]),  # listed first, but not the branch tip
            _commit("tip", ["base"]),
            _commit("base", []),
        ],
    }

    assert landing_shas(reader_for(routes), REPO, "main", "2026-08-01T00:00:00+00:00", 1) == [
        "tip",
        "base",
    ]


def test_an_empty_window_is_empty_rather_than_an_error() -> None:
    routes: dict[str, object] = {f"/repos/{REPO}/commits": []}

    assert landing_shas(reader_for(routes), REPO, "main", "2027-01-01T00:00:00+00:00", 1) == []


# ---------------------------------------------------------------------------------------------
# The update metadata comes from the trailer the gate itself reads.
# ---------------------------------------------------------------------------------------------


def test_update_metadata_is_read_from_the_trailer_and_the_branch_name() -> None:
    metadata = update_metadata(TRAILER, "dependabot/uv/ruff-0.16.1")

    assert metadata is not None
    assert metadata.dependency == "ruff"
    assert metadata.update_type == "version-update:semver-minor"
    # The second segment of the BRANCH name, which is where fetch-metadata takes it from -- hence
    # `github_actions` with an underscore where dependabot.yml writes a hyphen.
    assert metadata.ecosystem == "uv"
    actions = update_metadata(TRAILER, "dependabot/github_actions/x-2")
    assert actions is not None
    assert actions.ecosystem == "github_actions"


def test_a_commit_with_no_trailer_yields_no_metadata_rather_than_a_guess() -> None:
    assert update_metadata("chore: bump ruff from 0.15.22 to 0.16.1", "feat/x") is None


def test_a_trailer_stating_no_update_type_keeps_the_two_values_it_does_state() -> None:
    """A REQUIREMENT RANGE IS THIS SHAPE, and it is the ordinary one rather than a half-written
    anything: Dependabot omits `update-type` whenever the bump states no single version delta.

    Until 2026-08-28 this returned None wholesale, discarding an ecosystem the trailer never
    carried in the first place -- the branch does -- and which the gate itself therefore always
    has. That was harmless only while every transcribed rule refused an absent update type at
    Q1. Revision 3457db3c reads the ecosystem and nothing else, so the conflation would have
    reported `python 3.12-slim -> 3.14-slim` as permitted by a rule that excludes `docker`.

    The update type is still never GUESSED. It is the value the gate's condition is written
    against, and deriving it from a title would let the ledger say the rule permitted something
    on a value the rule never saw -- Dependabot's own fields do not agree with each other either
    (intent-packages #50 carries `dependency-version: 0.16.0` in the message whose subject says
    0.16.1). `None` here asserts that the bot declared none, which is a fact, not a fallback.
    """
    ranged = (
        "chore(deps-dev): update setuptools requirement from >=83.0.0 to >=84.0.0\n\n"
        "updated-dependencies:\n- dependency-name: setuptools\n"
    )

    metadata = update_metadata(ranged, "dependabot/uv/setuptools-gte-84.0.0")

    assert metadata is not None
    assert metadata.dependency == "setuptools"
    assert metadata.ecosystem == "uv"
    assert metadata.update_type is None


def test_a_commit_with_no_dependency_name_yields_no_metadata_at_all() -> None:
    """The other side of the distinction the reader now draws. No `dependency-name` means
    nothing here could read the trailer, which `audit_landing` reports as a finding -- as
    against a trailer that states no delta, which several revisions permit deliberately."""
    assert update_metadata("chore: unrelated\n\nupdated-dependencies:\n", "dependabot/uv/x") is None


# ---------------------------------------------------------------------------------------------
# The factory's claim comes from the same place, and for the same reason.
# ---------------------------------------------------------------------------------------------

FACTORY_MESSAGE = """feat: implement SDS unit 0c0002c6-9869-59bc-84c6-654e6fc57d9e (#66)

Factory-runner attempt 1.

SDS-Unit: 0c0002c6-9869-59bc-84c6-654e6fc57d9e
SDS-Package-Rev: 1

Co-authored-by: factory-runner <factory-runner@users.noreply.github.com>
"""


def test_the_claim_is_read_from_the_landing_commits_own_trailers() -> None:
    """Verbatim from intent-packages@b3f1522f, the first landing the factory made. The commit
    message is chosen over the pull-request body, which carries the same values and can be edited
    after the landing -- and a fact that can change is a conflicting row on every later pass."""
    claim = factory_claim(FACTORY_MESSAGE)

    assert claim is not None
    assert claim.work_unit == "0c0002c6-9869-59bc-84c6-654e6fc57d9e"
    assert claim.package_revision == 1


def test_an_ordinary_commit_carries_no_claim() -> None:
    assert factory_claim(TRAILER) is None
    assert factory_claim("feat: implement SDS unit 0c0002c6 by hand") is None


def test_a_trailer_that_is_not_a_unit_id_is_no_claim_rather_than_a_claim_of_nothing() -> None:
    """The unit id is what the audit resolves. A claim carrying something that cannot name a unit
    selects nothing to check, so it must not become a basis that promises a check."""
    assert factory_claim("SDS-Unit: not-a-uuid\nSDS-Package-Rev: 1\n") is None
    assert factory_claim("SDS-Unit: 0c0002c6\n") is None


def test_the_revision_is_optional_where_the_unit_is_not() -> None:
    claim = factory_claim("SDS-Unit: 0c0002c6-9869-59bc-84c6-654e6fc57d9e\n")

    assert claim is not None
    assert claim.package_revision is None


def test_a_landing_commit_without_the_trailer_falls_back_to_the_pull_requests_head() -> None:
    """What the landing commit contains is NOT the orchestrator's to decide. It sends no
    `commit_message` with its squash, so the body is governed by the repository's own
    `squash_merge_commit_message` setting -- a web form, not a literal in a merge call. All eight
    repositories the ledger covers write `COMMIT_MESSAGES` today, and a first draft used that to
    justify having no fall-back. Without one, flipping that setting makes every factory landing
    claimless, hence `unattributed`, hence read by no detector: silent, which is the one failure
    mode this basis exists to avoid.
    """
    routes = gate_routes()
    routes[f"/repos/{REPO}/commits/e931db8d"] = {
        "sha": "e931db8d31debfb08fd8f8410a4778f33c437fc1",
        "commit": {"message": "feat: implement SDS unit (#50)", "committer": {"date": MERGED_AT}},
        "files": [{"filename": "uv.lock"}],
    }
    routes[f"/repos/{REPO}/commits/4437bc98"] = {"commit": {"message": FACTORY_MESSAGE}}

    landing = read_landing(reader_for(routes), REPO, "main", "e931db8d")

    assert landing.claim is not None
    assert landing.claim.work_unit == "0c0002c6-9869-59bc-84c6-654e6fc57d9e"


# ---------------------------------------------------------------------------------------------
# Assembling one landing.
# ---------------------------------------------------------------------------------------------


def gate_routes(**overrides: object) -> dict[str, object]:
    routes: dict[str, object] = {
        f"/repos/{REPO}/commits/e931db8d": {
            "sha": "e931db8d31debfb08fd8f8410a4778f33c437fc1",
            "commit": {"message": TRAILER, "committer": {"date": MERGED_AT}},
            "files": [{"filename": "uv.lock"}, {"filename": "pyproject.toml"}],
        },
        f"/repos/{REPO}/commits/e931db8d31debfb08fd8f8410a4778f33c437fc1/pulls": [
            {
                "number": 50,
                "merge_commit_sha": "e931db8d31debfb08fd8f8410a4778f33c437fc1",
                "merged_at": MERGED_AT,
            }
        ],
        f"/repos/{REPO}/pulls/50": {
            "number": 50,
            "merged_at": MERGED_AT,
            # Populated ONLY here. The list endpoint returns it null for every row.
            "merged_by": {"login": "github-actions[bot]"},
            "head": {"sha": "4437bc98", "ref": "dependabot/uv/ruff-0.16.1"},
        },
        f"/repos/{REPO}/actions/runs": {
            "workflow_runs": [
                _run(31179223805, GATE, "pull_request", "2026-08-07T12:41:11Z", "success"),
                _run(31179223856, "q.yml", "pull_request", "2026-08-07T12:42:02Z", "success"),
                # Same head, `push` event: the identical workflow on the branch push. Counting it
                # would report every check twice.
                _run(31179220691, "q.yml", "push", "2026-08-07T12:41:49Z", "success"),
                # Concluded AFTER the landing, so it cannot have informed it.
                _run(99, "late.yml", "pull_request", "2026-08-07T13:00:00Z", "failure"),
            ]
        },
        f"/repos/{REPO}/actions/runs/31179223856/jobs": {
            "jobs": [
                {"name": "Lint, type-check, and test", "conclusion": "success"},
                {"name": "still running", "conclusion": None},
            ]
        },
        f"/repos/{REPO}/actions/runs/99/jobs": {
            "jobs": [{"name": "late", "conclusion": "failure"}]
        },
        # Each of the three excluded runs is given a readable jobs list. Without one, a mutation
        # that stopped excluding it would produce no check anyway and the exclusion would be
        # untested -- the blind spot that let three mutants survive the first battery.
        f"/repos/{REPO}/actions/runs/31179220691/jobs": {
            "jobs": [{"name": "Lint, type-check, and test", "conclusion": "success"}]
        },
        f"/repos/{REPO}/actions/runs/31179223805/jobs": {
            "jobs": [{"name": "Dependabot auto-merge", "conclusion": "success"}]
        },
        f"/repos/{REPO}/contents/{GATE}": {"sha": "77ab867d"},
    }
    routes.update(overrides)
    return routes


def test_a_gate_landing_is_read_whole() -> None:
    landing = read_landing(reader_for(gate_routes()), REPO, "main", "e931db8d")

    assert landing.commit == "e931db8d31debfb08fd8f8410a4778f33c437fc1"
    assert landing.pull_request == 50
    assert landing.landed_by == "github-actions[bot]"
    assert landing.landed_at.isoformat() == "2026-08-07T12:42:04+00:00"
    assert landing.files == ("pyproject.toml", "uv.lock")
    assert landing.rule is not None
    assert (landing.rule.run, landing.rule.revision) == (31179223805, "77ab867d")
    assert landing.update is not None
    assert landing.update.update_type == "version-update:semver-minor"


def test_only_pull_request_runs_that_concluded_before_the_landing_are_checks() -> None:
    landing = read_landing(reader_for(gate_routes()), REPO, "main", "e931db8d")

    # Job names, never workflow names: branch protection matches jobs, and here the workflow is
    # `q.yml` while the job is `Lint, type-check, and test`.
    assert [check.name for check in landing.checks] == ["Lint, type-check, and test"]
    # Exactly one, though the same job name also concluded on the `push` run at this head:
    # counting both would report every check twice.
    assert [check.run for check in landing.checks] == [31179223856]
    # The gate is recorded as the rule, never counted among the things that gated it.
    assert all(check.name != "Dependabot auto-merge" for check in landing.checks)
    # And the run that concluded after the landing is not there either.
    assert all(check.name != "late" for check in landing.checks)


def test_the_record_takes_the_full_sha_from_github_not_the_argument() -> None:
    """An abbreviated argument would otherwise become the landing's identity, and two spellings
    of one landing are two rows that never dedup."""
    landing = read_landing(reader_for(gate_routes()), REPO, "main", "e931db8d")

    assert landing.commit != "e931db8d"


def test_a_landing_with_no_gate_run_carries_no_rule() -> None:
    """Every merge before 2026-08-07 is this shape."""
    routes = gate_routes(
        **{
            f"/repos/{REPO}/actions/runs": {
                "workflow_runs": [
                    _run(31179223856, "q.yml", "pull_request", "2026-08-07T12:42:02Z", "success")
                ]
            },
            f"/repos/{REPO}/pulls/50": {
                "number": 50,
                "merged_at": MERGED_AT,
                "merged_by": {"login": "AlobarQuest"},
                "head": {"sha": "4437bc98", "ref": "dependabot/uv/ruff-0.16.1"},
            },
        }
    )

    landing = read_landing(reader_for(routes), REPO, "main", "e931db8d")

    assert landing.rule is None
    assert landing.landed_by == "AlobarQuest"


def test_a_direct_push_has_no_pull_request_no_checks_and_no_rule() -> None:
    sha = "a0563643d1f92d9c9ce5f5806aaa11c53dca1437"
    routes = {
        f"/repos/{REPO}/commits/{sha}": {
            "sha": sha,
            "commit": {
                "message": "ci: auto-merge GitHub Actions majors, not just patch and minor",
                "committer": {"date": "2026-08-07T16:25:36Z"},
            },
            "files": [{"filename": GATE}],
        },
        # No pull request lists this commit as its own landing commit.
        f"/repos/{REPO}/commits/{sha}/pulls": [],
    }

    landing = read_landing(reader_for(routes), REPO, "main", sha)

    assert landing.pull_request is None
    assert landing.checks == ()
    assert landing.rule is None
    assert landing.landed_by is None


def test_an_open_pull_request_touching_the_commit_does_not_make_it_a_landing() -> None:
    """`commits/{sha}/pulls` answers "which pull requests touch this commit", which is a wider
    question than "which pull request landed it"."""
    sha = "a0563643d1f92d9c9ce5f5806aaa11c53dca1437"
    routes = {
        f"/repos/{REPO}/commits/{sha}": {
            "sha": sha,
            "commit": {"message": "ci: a change", "committer": {"date": "2026-08-07T16:25:36Z"}},
            "files": [],
        },
        f"/repos/{REPO}/commits/{sha}/pulls": [
            {"number": 71, "merge_commit_sha": "somethingelse", "merged_at": None},
            {"number": 72, "merge_commit_sha": sha, "merged_at": None},
        ],
    }

    assert read_landing(reader_for(routes), REPO, "main", sha).pull_request is None


# ---------------------------------------------------------------------------------------------
# The reader reads.
# ---------------------------------------------------------------------------------------------


def test_the_reader_refuses_anything_that_is_not_a_path() -> None:
    with pytest.raises(ForbiddenMethodError):
        reader_for({}).get("https://elsewhere.invalid/repos")


def test_a_github_failure_is_a_named_error_carrying_no_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "boom"})

    reader = GitHubReader(token="fixture", transport=httpx.MockTransport(handler))

    with pytest.raises(LedgerError) as raised:
        read_landing(reader, REPO, "main", "e931db8d")
    assert "boom" not in str(raised.value)


# ---------------------------------------------------------------------------
# ADR-0019 increment 5b: the change-record trailers.
# ---------------------------------------------------------------------------


def test_the_change_record_trailers_are_read_from_a_landing_commit() -> None:
    permission = policy_permission(
        "build(deps): bump alembic from 1.18.5 to 1.19.0 (#50)\n"
        "\n"
        "SDS-Change-Record: 52\n"
        "SDS-Policy-Version: 2\n"
    )

    assert permission is not None
    assert permission.change_record == 52
    assert permission.policy_version == 2


def test_the_spellings_match_the_only_writer_of_them() -> None:
    """A LITERAL on each side rather than a shared constant, because this program imports nothing
    from the orchestrator -- its isolation test says so. Naming the literal in both places is what
    turns a rename into a red test instead of a landing silently recorded with no basis.

    The writer is `orchestrator/services/estate_pr_merge.py`, and its own test asserts the same
    two strings appear in the body it composes.
    """
    body = "SDS-Change-Record: 7\nSDS-Policy-Version: 3\n"
    permission = policy_permission(body)

    assert permission is not None and (permission.change_record, permission.policy_version) == (
        7,
        3,
    )


def test_half_a_claim_is_no_claim() -> None:
    """A record with no version names something that cannot be re-evaluated; a version with no
    record selects nothing to check."""
    assert policy_permission("SDS-Change-Record: 52\n") is None
    assert policy_permission("SDS-Policy-Version: 2\n") is None
    assert policy_permission("nothing here\n") is None


def test_a_non_numeric_trailer_is_no_claim() -> None:
    assert policy_permission("SDS-Change-Record: fifty-two\nSDS-Policy-Version: 2\n") is None
