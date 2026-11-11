"""`rotation-proposer standing-approval`: ADR-0055 amendment 22 item 2's conditions, as refusals.

The rows are the landing ledger's two registry rows from this repository's `.cred-consumers.toml`,
and each package is the one `generate.render` writes for its row, as the proposer generated the
landing ledger's two packages. The registry and intent-packages are throwaway git origins read
through the real mirror, as the command reads them.
"""

from __future__ import annotations

import copy
import json
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from rotation_proposer import cli, standing_approval
from rotation_proposer.findings import FindingsError
from rotation_proposer.generate import render
from rotation_proposer.standing_approval import (
    AUTHORITY,
    DESTINATIONS,
    GENERATED,
    ISSUE_PLAN,
    LANE_TYPE,
    REVISION_1,
    standard_authority,
)
from rotation_worker.git_mirror import RepoMirror
from rotation_worker.registry import (
    PACKAGES_REPOSITORY,
    REGISTRY_PATH,
    REGISTRY_REPOSITORIES,
    RegistryEntry,
    lineage_path,
    package_path,
)
from tests.rotation_worker.conftest import Origins

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "landing-ledger-bws-token"
PING_URL = "landing-ledger-ping-url"
REGISTRY = "AlobarQuest/orchestrator"
OCCURRENCE = "2026-11-09-requested"
CREATED_AT = "2026-11-09T23:46:20Z"
EVERY = [GENERATED, REVISION_1, ISSUE_PLAN, LANE_TYPE, DESTINATIONS, AUTHORITY]
# Planted where a careless log could reach it; no line may ever carry it.
SENTINEL = "placeholder-secret-value-not-a-token"


def _real_row(credential_id: str) -> dict[str, Any]:
    document = tomllib.loads((ROOT / REGISTRY_PATH).read_text())
    (row,) = [c for c in document["credential"] if c["id"] == credential_id]
    return copy.deepcopy(row)


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    return json.dumps(value)


def _registry_text(row: dict[str, Any]) -> str:
    """The row as a `.cred-consumers.toml`; the values here are strings, booleans and lists."""
    lines = ["version = 1", "", "[[credential]]"]
    lines += [f"{k} = {_toml_value(v)}" for k, v in row.items() if k != "consumer"]
    for consumer in row.get("consumer", []):
        lines += ["", "  [[credential.consumer]]"]
        lines += [f"  {k} = {_toml_value(v)}" for k, v in consumer.items()]
    return "\n".join(lines) + "\n"


def _generated(row: dict[str, Any], *, first_issue: bool = True) -> dict[str, Any]:
    """The package the proposer writes for the row, as it stands in review."""
    package = render(
        RegistryEntry(REGISTRY, "0" * 40, row), OCCURRENCE, CREATED_AT, first_issue=first_issue
    )
    return {**package, "status": "ready_for_review"}


def _lineage(credential_id: str) -> dict[str, Any]:
    return {
        "package_id": f"rotation-{credential_id}",
        "current_state": "ready_for_review",
        "revisions": [
            {"revision": 1, "hash": "0" * 64, "created_at": CREATED_AT, "author": "t"},
        ],
        "transitions": [{"kind": "transition", "from": "draft", "to": "ready_for_review"}],
        "approvals": [],
        "grants": [],
    }


class Rig:
    def __init__(self, origins: Origins, mirror: RepoMirror, monkeypatch: pytest.MonkeyPatch):
        self.origins = origins
        self.monkeypatch = monkeypatch
        self.served: dict[str, str | None] | None = {}
        monkeypatch.setattr(cli, "_mirror", lambda: mirror)
        monkeypatch.setattr(cli, "read_due", self._read_due)

    def _read_due(self) -> tuple[list[Any], list[Any], dict[str, str | None] | None]:
        return [], [], self.served

    def publish(
        self,
        credential_id: str,
        *,
        row: dict[str, Any] | None = None,
        package: dict[str, Any] | None = None,
        lineage: dict[str, Any] | None = None,
        last_rotated: str | None = None,
    ) -> tuple[str, str]:
        """Commit the row, the package and its lineage; return the two commits."""
        row = row if row is not None else _real_row(credential_id)
        registry_commit = ""
        for repository in REGISTRY_REPOSITORIES:
            text = _registry_text(row) if repository == REGISTRY else "version = 1\n"
            committed = self.origins.commit(repository, {REGISTRY_PATH: text})
            registry_commit = committed if repository == REGISTRY else registry_commit
        files = {
            package_path(credential_id): yaml.safe_dump(
                package if package is not None else _generated(row), sort_keys=False
            ),
            lineage_path(credential_id): yaml.safe_dump(
                lineage if lineage is not None else _lineage(credential_id), sort_keys=False
            ),
        }
        packages_commit = self.origins.commit(PACKAGES_REPOSITORY, files)
        self.served = {credential_id: last_rotated}
        return registry_commit, packages_commit


@pytest.fixture
def origins(tmp_path: Path) -> Origins:
    return Origins(tmp_path / "origins")


@pytest.fixture
def rig(tmp_path: Path, origins: Origins, monkeypatch: pytest.MonkeyPatch) -> Rig:
    mirror = RepoMirror(tmp_path / "mirrors", origin=origins.url)
    return Rig(origins, mirror, monkeypatch)


def _run(
    capsys: pytest.CaptureFixture[str], credential_id: str
) -> tuple[int, list[dict[str, Any]]]:
    code = cli.run(["standing-approval", credential_id])
    out, err = capsys.readouterr()
    assert err == ""
    return code, [json.loads(line) for line in out.splitlines()]


def _refused(lines: list[dict[str, Any]]) -> list[str]:
    (verdict,) = [line for line in lines if line["event"] == "standing_approval.verdict"]
    return verdict["refused"]


# ---- every condition holds ----------------------------------------------------------------------


@pytest.mark.parametrize("credential_id", [TOKEN, PING_URL])
def test_a_generated_revision_1_on_the_issue_plan_refuses_nothing(
    rig: Rig, capsys: pytest.CaptureFixture[str], credential_id: str
) -> None:
    registry_commit, packages_commit = rig.publish(credential_id)
    code, lines = _run(capsys, credential_id)
    assert code == 0
    assert lines[0] == {
        "event": "standing_approval.read",
        "credential_id": credential_id,
        "registry_commit": f"{REGISTRY}@{registry_commit}",
        "package_commit": f"{PACKAGES_REPOSITORY}@{packages_commit}",
    }
    conditions = [line for line in lines if line["event"] == "standing_approval.condition"]
    assert [line["condition"] for line in conditions] == EVERY
    assert all(line["passed"] for line in conditions)
    assert lines[-1] == {
        "event": "standing_approval.verdict",
        "credential_id": credential_id,
        "refused": [],
        "exit_code": 0,
    }


# ---- each condition refuses alone ---------------------------------------------------------------


def test_a_package_edited_after_generation_is_not_proposer_generated(
    rig: Rig, capsys: pytest.CaptureFixture[str]
) -> None:
    package = {**_generated(_real_row(TOKEN)), "title": "Standing rotation, edited"}
    rig.publish(TOKEN, package=package)
    code, lines = _run(capsys, TOKEN)
    assert (code, _refused(lines)) == (1, [GENERATED])


def test_a_package_another_author_wrote_is_not_proposer_generated(
    rig: Rig, capsys: pytest.CaptureFixture[str]
) -> None:
    rig.publish(TOKEN, package={**_generated(_real_row(TOKEN)), "created_by": "devon"})
    code, lines = _run(capsys, TOKEN)
    assert (code, _refused(lines)) == (1, [GENERATED])
    (generated,) = [line for line in lines if line.get("condition") == GENERATED]
    assert generated["reason"].startswith("created_by isn't 'rotation-proposer'")


@pytest.mark.parametrize(
    "edit",
    [
        lambda lineage: lineage.update(approvals=[{"revision": 1, "approved_by": "devon"}]),
        lambda lineage: lineage.update(current_state="approved"),
        lambda lineage: lineage["revisions"].append({"revision": 2, "hash": "1" * 64}),
        lambda lineage: lineage["revisions"][0].update(revision=2),
        lambda lineage: lineage.update(package_id="rotation-another"),
    ],
    ids=["approved", "not-in-review", "two-revisions", "not-revision-1", "another-package"],
)
def test_a_lineage_that_isn_t_one_unapproved_revision_1_refuses(
    rig: Rig, capsys: pytest.CaptureFixture[str], edit: Callable[[dict[str, Any]], None]
) -> None:
    lineage = _lineage(TOKEN)
    edit(lineage)
    rig.publish(TOKEN, lineage=lineage)
    code, lines = _run(capsys, TOKEN)
    assert (code, _refused(lines)) == (1, [REVISION_1])


def test_a_package_revision_other_than_1_refuses_and_fails_the_re_render_too(
    rig: Rig, capsys: pytest.CaptureFixture[str]
) -> None:
    rig.publish(TOKEN, package={**_generated(_real_row(TOKEN)), "revision": 2})
    code, lines = _run(capsys, TOKEN)
    assert (code, _refused(lines)) == (1, [GENERATED, REVISION_1])


def test_a_lane_token_infraops_records_rotated_is_not_on_the_issue_plan(
    rig: Rig, capsys: pytest.CaptureFixture[str]
) -> None:
    """A revision 1 rendered for a reissue, of a credential infraops dates: the amendment's
    default leaves it for Devon."""
    row = _real_row(TOKEN)
    rig.publish(TOKEN, package=_generated(row, first_issue=False), last_rotated="2026-11-10")
    code, lines = _run(capsys, TOKEN)
    assert (code, _refused(lines)) == (1, [ISSUE_PLAN])
    (issue,) = [line for line in lines if line.get("condition") == ISSUE_PLAN]
    assert "infraops records the credential rotated 2026-11-10" in issue["reason"]
    assert "the package's criteria state the rotation plan" in issue["reason"]


def test_a_package_stating_the_issue_plan_for_a_dated_lane_token_refuses_twice(
    rig: Rig, capsys: pytest.CaptureFixture[str]
) -> None:
    """The issue package of a credential since dated (deleted and generated again): the
    re-render under the plan infraops implies differs, and the issue-plan condition refuses."""
    rig.publish(TOKEN, last_rotated="2026-11-10")
    code, lines = _run(capsys, TOKEN)
    assert (code, _refused(lines)) == (1, [GENERATED, ISSUE_PLAN])


def test_a_lane_class_whose_provider_names_another_type_refuses(
    rig: Rig, capsys: pytest.CaptureFixture[str]
) -> None:
    row = {**_real_row(TOKEN), "provider": "another-provider"}
    rig.publish(TOKEN, row=row, package=_generated(row))
    code, lines = _run(capsys, TOKEN)
    assert (code, _refused(lines)) == (1, [LANE_TYPE])


def test_a_class_that_is_no_lane_type_refuses(rig: Rig, capsys: pytest.CaptureFixture[str]) -> None:
    """A type the SDS rotates, but not one the standing answer covers, even where its provider
    destination names its class: its kinds aren't a lane's either."""
    row = {
        "id": TOKEN,
        "class": "github-pat-fine-grained",
        "provider": "github-pat-fine-grained",
        "bws_uuid": "11111111-1111-4111-8111-111111111111",
        "rotated_by_sds": True,
        "consumer": [{"kind": "gh-actions-secret", "repo": "AlobarQuest/example", "name": "T"}],
    }
    rig.publish(TOKEN, row=row, package=_generated(row))
    code, lines = _run(capsys, TOKEN)
    assert code == 1
    assert {LANE_TYPE, DESTINATIONS} <= set(_refused(lines))
    (lane,) = [line for line in lines if line.get("condition") == LANE_TYPE]
    assert lane["reason"] == "class 'github-pat-fine-grained' is no lane type"


def test_a_lane_token_variable_that_redeploys_refuses(
    rig: Rig, capsys: pytest.CaptureFixture[str]
) -> None:
    row = _real_row(TOKEN)
    row["consumer"][1]["redeploy"] = True
    rig.publish(TOKEN, row=row, package=_generated(row))
    code, lines = _run(capsys, TOKEN)
    assert (code, _refused(lines)) == (1, [DESTINATIONS])
    (destinations,) = [line for line in lines if line.get("condition") == DESTINATIONS]
    assert destinations["reason"] == "a coolify-env destination redeploys"


def test_a_ping_url_with_a_second_copy_is_not_of_its_type_s_kinds(
    rig: Rig, capsys: pytest.CaptureFixture[str]
) -> None:
    row = _real_row(PING_URL)
    row["consumer"].append({"kind": "bws-secret", "uuid": "22222222-2222-4222-8222-222222222222"})
    rig.publish(PING_URL, row=row, package=_generated(row))
    code, lines = _run(capsys, PING_URL)
    assert code == 1
    assert DESTINATIONS in _refused(lines)


def test_a_registry_that_moved_since_generation_refuses_the_destinations(
    rig: Rig, capsys: pytest.CaptureFixture[str]
) -> None:
    """Unequal destinations break the re-render too, so both are named."""
    package = _generated(_real_row(TOKEN))
    row = _real_row(TOKEN)
    row["consumer"][0]["uuid"] = "33333333-3333-4333-8333-333333333333"
    row["bws_uuid"] = "33333333-3333-4333-8333-333333333333"
    rig.publish(TOKEN, row=row, package=package)
    code, lines = _run(capsys, TOKEN)
    assert code == 1
    assert {GENERATED, DESTINATIONS} <= set(_refused(lines))


def test_an_authority_widened_past_the_standard_set_refuses_by_name(
    rig: Rig, capsys: pytest.CaptureFixture[str]
) -> None:
    """The re-render implies it, so `proposer_generated` refuses as well; the command still names
    the authority as its own condition."""
    package = _generated(_real_row(PING_URL))
    authority = copy.deepcopy(package["authority"])
    authority["allowed"].append("infra_mutation")
    authority["prohibited"].remove("infra_mutation")
    rig.publish(PING_URL, package={**package, "authority": authority})
    code, lines = _run(capsys, PING_URL)
    assert (code, _refused(lines)) == (1, [GENERATED, AUTHORITY])


@pytest.mark.parametrize("field", ["allowed", "requires_approval", "prohibited", "budgets"])
def test_the_authority_is_compared_whole(field: str) -> None:
    row = _real_row(TOKEN)
    entry = RegistryEntry(REGISTRY, "0" * 40, row)
    package = _generated(row)
    assert standard_authority(entry, package, first_issue=True).passed
    changed = copy.deepcopy(package)
    changed["authority"][field] = {"max_attempts": 4} if field == "budgets" else ["email_send"]
    assert not standard_authority(entry, changed, first_issue=True).passed


# ---- a read it can't make is never a pass -------------------------------------------------------


def _unreadable(lines: list[dict[str, Any]], code: int) -> dict[str, Any]:
    assert code == 2
    assert lines[-1]["event"] == "standing_approval.unreadable"
    assert lines[-1]["exit_code"] == 2
    assert not [line for line in lines if line["event"] == "standing_approval.verdict"]
    return lines[-1]


def test_a_credential_the_registry_doesn_t_hold_is_unreadable(
    rig: Rig, capsys: pytest.CaptureFixture[str]
) -> None:
    rig.publish(TOKEN)
    code, lines = _run(capsys, "another-credential")
    assert "RegistryError" in _unreadable(lines, code)["error"]


@pytest.mark.parametrize("missing", ["package", "lineage"])
def test_a_package_or_lineage_missing_from_intent_packages_is_unreadable(
    rig: Rig, origins: Origins, capsys: pytest.CaptureFixture[str], missing: str
) -> None:
    rig.publish(TOKEN)
    path = package_path(TOKEN) if missing == "package" else lineage_path(TOKEN)
    (origins.root / f"{PACKAGES_REPOSITORY}.git" / path).unlink()
    origins.commit(PACKAGES_REPOSITORY, {})
    code, lines = _run(capsys, TOKEN)
    assert "MirrorError" in _unreadable(lines, code)["error"]


def test_a_package_that_doesn_t_parse_is_unreadable(
    rig: Rig, origins: Origins, capsys: pytest.CaptureFixture[str]
) -> None:
    rig.publish(TOKEN)
    origins.commit(PACKAGES_REPOSITORY, {package_path(TOKEN): "- a list, not a package\n"})
    code, lines = _run(capsys, TOKEN)
    assert "RegistryError" in _unreadable(lines, code)["error"]


@pytest.mark.parametrize(
    "served",
    [None, {"another-credential": None}],
    ids=["infraops-predating-the-field", "credential-not-served"],
)
def test_a_last_rotated_infraops_doesn_t_serve_is_unreadable_never_null(
    rig: Rig, capsys: pytest.CaptureFixture[str], served: dict[str, str | None] | None
) -> None:
    rig.publish(TOKEN)
    rig.served = served
    code, lines = _run(capsys, TOKEN)
    assert "FindingsError" in _unreadable(lines, code)["error"]


def test_infraops_refusing_is_unreadable(rig: Rig, capsys: pytest.CaptureFixture[str]) -> None:
    rig.publish(TOKEN)

    def refuse() -> None:
        raise FindingsError("infraops refused cred-findings: exit 1")

    rig.monkeypatch.setattr(cli, "read_due", refuse)
    code, lines = _run(capsys, TOKEN)
    assert "infraops refused" in _unreadable(lines, code)["error"]


def test_a_ping_url_infraops_records_issued_is_unreadable(
    rig: Rig, capsys: pytest.CaptureFixture[str]
) -> None:
    """The type has no rotation plan, so `plan_for` can't choose one: exit 2, not a pass."""
    rig.publish(PING_URL, last_rotated="2026-11-10")
    code, lines = _run(capsys, PING_URL)
    assert "GenerateError" in _unreadable(lines, code)["error"]


def test_an_unforeseen_exception_is_unreadable_not_a_refusal(
    rig: Rig, capsys: pytest.CaptureFixture[str]
) -> None:
    rig.publish(TOKEN)

    def broken(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise RuntimeError("the generator broke")

    rig.monkeypatch.setattr(standing_approval, "render", broken)
    code, lines = _run(capsys, TOKEN)
    assert _unreadable(lines, code)["error"] == "RuntimeError: the generator broke"


# ---- it reads only, and carries no value --------------------------------------------------------


def test_the_command_takes_no_submit(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.run(["--submit", "standing-approval", TOKEN])
    assert raised.value.code == 2
    assert "takes no --submit" in capsys.readouterr().err


_KEYS = {
    "standing_approval.read": {"event", "credential_id", "registry_commit", "package_commit"},
    "standing_approval.condition": {"event", "credential_id", "condition", "passed", "reason"},
    "standing_approval.verdict": {"event", "credential_id", "refused", "exit_code"},
    "standing_approval.unreadable": {"event", "credential_id", "error", "exit_code"},
}


@pytest.mark.parametrize("last_rotated", [None, "2026-11-10"], ids=["exit-0", "exit-2"])
@pytest.mark.parametrize("edited", [False, True], ids=["as-generated", "edited"])
def test_no_line_carries_a_value(
    rig: Rig,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    last_rotated: str | None,
    edited: bool,
) -> None:
    """Every credential the process could hold is planted with a placeholder, and the row's free
    text carries it too; no line on stdout or stderr carries it, on a pass, a refusal or an
    unreadable read, and every line is one of the four shapes."""
    for variable in ("BWS_ACCESS_TOKEN", "ORCHESTRATOR_API_TOKEN", "ALERT_HUB_TOKEN"):
        monkeypatch.setenv(variable, SENTINEL)
    row = _real_row(PING_URL)
    row["consumer"][0]["note"] = SENTINEL
    package = _generated(row)
    if edited:
        package["outcome"] = {**package["outcome"], "why": SENTINEL}
    rig.publish(PING_URL, row=row, package=package, last_rotated=last_rotated)
    code = cli.run(["standing-approval", PING_URL])
    out, err = capsys.readouterr()
    assert code == (2 if last_rotated else 1 if edited else 0)
    assert SENTINEL not in out + err
    for line in map(json.loads, out.splitlines()):
        assert set(line) == _KEYS[line["event"]]
