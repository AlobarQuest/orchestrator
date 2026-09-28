"""The offline half of the gate that holds derived rollout criteria to change-manager's policy.

`scripts/check_rollout_policy_ratification.py` reads change-manager's committed
`contracts/ratified_rollout_policy.json` over the network, as data, and compares it with what this
repository derives. Everything it decides with is exercised here against a synthetic document of
the same schema, built from this repository's own derivation so that the agreeing case is
agreement by construction and each control moves exactly one side.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from change_proposer.criteria import acceptance_criteria, rollback_for
from deploy_watcher.workflows import attestation_for
from scripts import check_rollout_policy_ratification as check

BRAIN = "alobarquest/brain"
CHANGE_MANAGER = "alobarquest/change-manager"
BRAIN_BLOB = "7cf6ca2d2a508b1643cdb5ac0d5390357f397d54"
CM_BLOB = "a47d4b187c93971a5b5915ce87a963bd4ef35e30"


def _entry(repository: str, blob: str, path: str) -> dict[str, Any]:
    plan = rollback_for(repository)
    return {
        "acceptance_criteria": list(acceptance_criteria(repository, attestation_for(blob))),
        "rollback_plan": {"steps": list(plan.steps), "target": plan.target},
        "rollout_workflow": {"blob_sha": blob, "path": path},
    }


def _document() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "policy_version": 8,
        "repositories": {
            BRAIN: _entry(BRAIN, BRAIN_BLOB, ".github/workflows/ci.yml"),
            CHANGE_MANAGER: _entry(CHANGE_MANAGER, CM_BLOB, ".github/workflows/deploy.yml"),
        },
    }


def _run(document: object, *, raw: str | None = None) -> int:
    text = raw if raw is not None else json.dumps(document)
    return check.main([], fetcher=lambda repo, path, ref: text)


def test_the_comparison_passes_when_the_two_sides_agree(capsys) -> None:
    assert _run(_document()) == 0
    assert "PASS" in capsys.readouterr().out


def test_it_fires_when_the_ratified_criteria_move_alone(capsys) -> None:
    """The 2026-09-27 shape: one side's text moved and the other's did not."""
    document = _document()
    document["repositories"][BRAIN]["acceptance_criteria"][1] += " (reworded)"

    assert _run(document) == 1
    assert "acceptance criteria differ" in capsys.readouterr().err


def test_it_fires_when_only_the_rollback_plan_moves(capsys) -> None:
    document = _document()
    document["repositories"][BRAIN]["rollback_plan"]["steps"].pop()

    assert _run(document) == 1
    assert "rollback plan differs" in capsys.readouterr().err


def test_it_fires_when_the_pinned_revision_is_not_transcribed_here(capsys) -> None:
    document = _document()
    document["repositories"][BRAIN]["rollout_workflow"]["blob_sha"] = "0" * 40

    assert _run(document) == 1
    assert "not transcribed" in capsys.readouterr().err


def test_it_fires_for_a_repository_this_side_has_no_rollback_plan_for(capsys) -> None:
    document = _document()
    document["repositories"]["alobarquest/elsewhere"] = copy.deepcopy(
        document["repositories"][BRAIN]
    )

    assert _run(document) == 1
    assert "no rollback plan" in capsys.readouterr().err


def test_it_compares_the_revision_the_policy_pins_not_the_newest_transcription() -> None:
    """Keyed on change-manager's pin: ratifying the OLDER brain revision with its own criteria
    agrees, because the transcription registry still holds that revision."""
    older = "c5c088719cd340f0071b875c6a82439292ed8756"
    document = _document()
    document["repositories"][BRAIN] = _entry(BRAIN, older, ".github/workflows/ci.yml")

    assert _run(document) == 0


def _broken(mutate) -> dict[str, Any]:
    document = _document()
    mutate(document)
    return document


@pytest.mark.parametrize(
    ("document", "raw"),
    [
        (None, "not json {"),
        (None, "[]"),
        (None, ""),
        (_broken(lambda d: d.update(schema_version=2)), None),
        (_broken(lambda d: d.pop("schema_version")), None),
        (_broken(lambda d: d.update(policy_version="8")), None),
        (_broken(lambda d: d.update(repositories={})), None),
        (_broken(lambda d: d["repositories"][BRAIN].pop("rollout_workflow")), None),
        (_broken(lambda d: d["repositories"][BRAIN].update(acceptance_criteria=[])), None),
        (_broken(lambda d: d["repositories"][BRAIN].update(acceptance_criteria=[1])), None),
        (_broken(lambda d: d["repositories"][BRAIN].pop("rollback_plan")), None),
        (_broken(lambda d: d["repositories"][BRAIN]["rollback_plan"].pop("steps")), None),
    ],
    ids=[
        "not-json",
        "not-an-object",
        "empty",
        "unknown-schema",
        "no-schema",
        "version-not-int",
        "no-repositories",
        "no-pin",
        "no-criteria",
        "criteria-not-strings",
        "no-rollback",
        "rollback-no-steps",
    ],
)
def test_a_malformed_document_fails_closed_and_says_what_is_wrong(
    document: object, raw: str | None, capsys
) -> None:
    assert _run(document, raw=raw) == 1
    err = capsys.readouterr().err
    assert err.startswith("FAIL: ") and check.POLICY_PATH in err


def test_a_missing_document_fails_closed() -> None:
    """A 404 from the contents API arrives as Unresolvable from the shared fetcher."""

    def missing(repo, path, ref):
        raise check.Unresolvable(f"cannot read {path} at {repo}@{ref}: HTTP 404")

    assert check.main([], fetcher=missing) == 1


def test_the_ref_is_passed_through_to_the_fetch() -> None:
    seen: list[str] = []

    def fetcher(repo, path, ref):
        seen.append(ref)
        return json.dumps(_document())

    assert check.main(["--ref", "some-branch"], fetcher=fetcher) == 0
    assert check.main([], fetcher=fetcher) == 0
    assert seen == ["some-branch", "main"]


def test_the_script_never_executes_or_imports_what_it_fetches() -> None:
    """Fetched content is data. A future edit reaching for exec/eval/import on it reds here."""
    from pathlib import Path

    source = Path(check.__file__).read_text(encoding="utf-8")
    code = source.split('"""', 2)[2]  # everything after the module docstring
    for forbidden in ("exec(", "eval(", "compile(", "__import__", "importlib", "types.ModuleType"):
        assert forbidden not in code, forbidden


def test_the_gate_runs_the_script_this_module_tests() -> None:
    from pathlib import Path

    workflows = [
        path.name
        for path in Path(".github/workflows").glob("*.yml")
        if "check_rollout_policy_ratification" in path.read_text(encoding="utf-8")
    ]

    assert workflows == ["quality.yml"]
