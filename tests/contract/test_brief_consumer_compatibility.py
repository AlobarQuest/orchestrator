"""WS-P2.23 part B: the offline half of the pull-request gate that refuses brief drift.

`scripts/check_brief_consumer_compatibility.py` reads the consumer's brief model at the
revision this repo's caller workflow pins and refuses a change that would serve a field
that revision does not declare. The reading is over the network, so it runs as its own
job rather than in the suite -- but everything it decides WITH is pure, and pure is what
gets tested here:

- the field parser agrees with pydantic, for the model that is importable on this side;
- the pin reader accepts only an immutable, fully-resolved revision;
- the comparison actually reports a served-but-undeclared field.

The last one matters most. A guard built to catch a failure is worth nothing until it has
been shown to fire, and this guard replaces a rule that was prose from WS-P2.12 to
2026-07-30, when it was violated and every dispatch in the estate died at brief-parse.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.api.schemas import RunnerBriefResponse
from scripts import check_brief_consumer_compatibility as check

SCRIPT = Path("scripts/check_brief_consumer_compatibility.py")


def test_the_field_parser_agrees_with_pydantic() -> None:
    """The fidelity pin: source-reading must mean the same thing as introspection.

    The consumer's model is read from source because it lives at an arbitrary revision of
    another repository and installing it would drag that repository's dependency tree into
    this repository's pull-request gate. That trade is only sound while the parse is exact,
    and this is the one place both readings are available for the same class.
    """
    parsed = check.declared_fields(check.SERVED_MODEL_SOURCE.read_text(), "RunnerBriefResponse")

    assert parsed == set(RunnerBriefResponse.model_fields)


def test_the_parser_reads_a_pydantic_model_the_way_pydantic_would() -> None:
    """`model_config` is not a field, and the `model_` prefix is reserved, not declarable."""
    source = (
        "class RunnerBrief(BaseModel):\n"
        '    model_config = ConfigDict(extra="allow")\n'
        "    work_unit: WorkUnitBrief\n"
        "    enrichment: dict[str, Any] | None = None\n"
        '    acceptance_criteria: list["Criterion"]\n'
    )

    assert check.declared_fields(source, "RunnerBrief") == {
        "work_unit",
        "enrichment",
        "acceptance_criteria",
    }


def test_a_renamed_or_moved_consumer_model_is_loud() -> None:
    """Never a silent pass: a parser that found nothing must not read as "nothing served"."""
    with pytest.raises(check.Unresolvable):
        check.declared_fields("class SomethingElse(BaseModel):\n    x: int\n", "RunnerBrief")


def test_the_real_caller_workflow_pins_an_immutable_revision() -> None:
    repo, workflow, ref = check.pinned_consumer(check.CALLER_WORKFLOW.read_text())

    assert repo == "AlobarQuest/factory-runner"
    assert workflow == ".github/workflows/factory-runner.yml"
    assert len(ref) == 40


@pytest.mark.parametrize(
    "uses",
    [
        "AlobarQuest/factory-runner/.github/workflows/factory-runner.yml@main",
        "AlobarQuest/factory-runner/.github/workflows/factory-runner.yml@v1",
        "AlobarQuest/factory-runner/.github/workflows/factory-runner.yml@b804912",
    ],
    ids=["branch", "tag", "short-sha"],
)
def test_a_mutable_pin_leaves_nothing_to_check_against(uses: str) -> None:
    """`@main` resolves to different code tomorrow, so a compatibility answer would expire."""
    workflow = f"jobs:\n  runner:\n    uses: {uses}\n"

    with pytest.raises(check.Unresolvable, match="full 40-character commit"):
        check.pinned_consumer(workflow)


def test_a_consumer_that_stopped_installing_its_own_commit_is_loud() -> None:
    """The premise, asserted instead of assumed.

    One lookup is only correct because the pinned workflow installs itself. Were that ever
    reverted, this check would otherwise go on comparing against a revision no run uses --
    a wrong answer that still reads green, which is the failure mode of the check it
    replaces.
    """
    literal = (
        "jobs:\n  run:\n    steps:\n"
        "      - run: uv tool install "
        '"git+https://github.com/AlobarQuest/factory-runner.git@' + "0" * 40 + '"\n'
    )

    with pytest.raises(check.Unresolvable, match="does not install"):
        check.assert_the_workflow_installs_its_own_commit(literal, "0" * 40)


def test_the_comparison_names_every_field_the_pinned_consumer_cannot_use() -> None:
    """The guard shown firing, on the exact shape of the 2026-07-30 outage.

    The orchestrator declares a field; the pinned consumer does not. Under the old regime
    this shipped and killed every run. Here it is a set difference with a name on it.
    """
    served = "class RunnerBriefResponse(BaseModel):\n    work_unit: X\n    enrichment: Y\n"
    behind = "class RunnerBrief(BaseModel):\n    work_unit: X\n"
    in_step = behind + "    enrichment: Y\n"

    assert check.declared_fields(served, "RunnerBriefResponse") - check.declared_fields(
        behind, "RunnerBrief"
    ) == {"enrichment"}
    assert not check.declared_fields(served, "RunnerBriefResponse") - check.declared_fields(
        in_step, "RunnerBrief"
    )


def test_the_gate_runs_the_script_this_module_tests() -> None:
    """A check nothing invokes is the defect it guards against, wearing a different hat."""
    invocations = [
        path for path in Path(".github/workflows").glob("*.yml") if SCRIPT.name in path.read_text()
    ]

    assert [path.name for path in invocations] == ["quality.yml"]
