"""What a landing record may assert, per route -- and what it must refuse to assert."""

import json
from datetime import UTC, datetime

from landing_ledger.model import Check, Landing, RuleApplication, UpdateMetadata
from landing_ledger.record import (
    BASIS_HUMAN,
    BASIS_NONE,
    BASIS_RULE,
    BASIS_UNATTRIBUTED,
    MAX_FACT_BYTES,
    basis_of,
    landing_observation,
)

LANDED = datetime(2026, 8, 7, 12, 42, 4, tzinfo=UTC)
REPO = "AlobarQuest/intent-packages"
GATE = ".github/workflows/dependabot-auto-merge.yml"


def _rule(outcome: str = "success") -> RuleApplication:
    return RuleApplication(path=GATE, revision="77ab867d", run=31179223805, outcome=outcome)


def gate_landing(**overrides: object) -> Landing:
    """The auto-merged shape, modelled on intent-packages@e931db8d (2026-08-07)."""
    base = {
        "repository": REPO,
        "base_ref": "main",
        "commit": "e931db8d31debfb08fd8f8410a4778f33c437fc1",
        "landed_at": LANDED,
        "title": "chore(deps-dev): bump ruff from 0.15.22 to 0.16.1 (#50)",
        "files": ("pyproject.toml", "uv.lock"),
        "files_changed": 2,
        "pull_request": 50,
        "head_commit": "4437bc985a55c1aa5ad8488067df594c5c1c676c",
        "landed_by": "github-actions[bot]",
        "checks": (Check(name="Lint, type-check, and test", conclusion="success", run=1),),
        "rule": _rule(),
        "update": UpdateMetadata(
            dependency="ruff", ecosystem="uv", update_type="version-update:semver-minor"
        ),
    }
    return Landing(**{**base, **overrides})  # type: ignore[arg-type]


def human_landing(**overrides: object) -> Landing:
    return gate_landing(landed_by="AlobarQuest", rule=None, update=None, **overrides)


def push_landing(**overrides: object) -> Landing:
    """A commit pushed straight at the branch -- intent-packages@a0563643, which edited the gate."""
    base = {
        "repository": REPO,
        "base_ref": "main",
        "commit": "a0563643d1f92d9c9ce5f5806aaa11c53dca1437",
        "landed_at": LANDED,
        "title": "ci: auto-merge GitHub Actions majors, not just patch and minor",
        "files": (GATE,),
        "files_changed": 1,
    }
    return Landing(**{**base, **overrides})  # type: ignore[arg-type]


def test_an_auto_merged_landing_records_the_rule_that_permitted_it() -> None:
    permitted = landing_observation(gate_landing())["facts"]["permitted_by"]

    assert permitted["basis"] == BASIS_RULE
    assert permitted["rule_path"] == GATE
    assert permitted["rule_revision"] == "77ab867d"
    assert permitted["rule_run"] == 31179223805
    assert permitted["update_type"] == "version-update:semver-minor"
    assert permitted["ecosystem"] == "uv"
    assert permitted["decision"] == "ADR-0016"
    assert permitted["checks"] == [
        {"name": "Lint, type-check, and test", "conclusion": "success", "run": 1}
    ]


def test_a_human_merge_records_the_person_and_invents_no_rule() -> None:
    """The backfill honesty rule. Every merge before 2026-08-07 is this shape, and a record that
    reconstructed conditions for them would assert something nobody checked."""
    permitted = landing_observation(human_landing())["facts"]["permitted_by"]

    assert permitted["basis"] == BASIS_HUMAN
    assert permitted["landed_by"] == "AlobarQuest"
    assert not {"rule_path", "rule_revision", "rule_run", "decision", "update_type"} & set(
        permitted
    )


def test_a_human_merge_of_a_pull_request_the_gate_RAN_on_still_invents_no_rule() -> None:
    """The case the previous test cannot reach, and the one that actually occurs.

    The gate runs on EVERY Dependabot pull request, including the package majors it deliberately
    leaves to a person. Such a landing has a successful gate run AND a human who merged it -- so a
    record keyed on "is there a rule application?" rather than on the basis would stamp
    `decision: ADR-0016` onto a landing the rule declined to permit.
    """
    permitted = landing_observation(gate_landing(landed_by="AlobarQuest"))["facts"]["permitted_by"]

    assert permitted["basis"] == BASIS_HUMAN
    assert not {"rule_path", "rule_revision", "rule_run", "rule_outcome", "decision"} & set(
        permitted
    )
    assert "update_type" not in permitted


def test_a_direct_push_records_that_nothing_permitted_it() -> None:
    permitted = landing_observation(push_landing())["facts"]["permitted_by"]

    assert permitted["basis"] == BASIS_NONE
    assert "no pull request" in permitted["reason"]
    # No checks either: a push-triggered run concludes AFTER the push, so it cannot have
    # permitted it. Recording one here would read as a gate that was never there.
    assert "checks" not in permitted
    assert "landed_by" not in permitted


def test_the_rule_basis_needs_both_halves_and_is_never_rounded_down() -> None:
    """Either half alone is satisfiable by something that is not the lane.

    The gate runs on every Dependabot pull request, including the major bumps it deliberately
    leaves to a person -- so a successful gate run does not by itself mean the gate merged it.
    """
    assert basis_of(gate_landing(landed_by="AlobarQuest")) == BASIS_HUMAN
    assert basis_of(gate_landing(rule=None)) == BASIS_UNATTRIBUTED
    assert basis_of(gate_landing(rule=_rule(outcome="skipped"))) == BASIS_UNATTRIBUTED
    assert basis_of(gate_landing(landed_by=None)) == BASIS_UNATTRIBUTED


def test_an_unattributed_landing_says_so_rather_than_claiming_a_basis() -> None:
    permitted = landing_observation(gate_landing(rule=None))["facts"]["permitted_by"]

    assert permitted["basis"] == BASIS_UNATTRIBUTED
    assert "no gate run" in permitted["reason"]
    assert "rule_path" not in permitted


def test_the_observation_type_splits_because_no_member_means_landing() -> None:
    """`github_pr` is true where a pull request exists and would be a plain falsehood for a push.

    `OBSERVATION_TYPES` has no member meaning "a commit reached the default branch"; adding one is
    a schema decision, so the record takes the two members that assert nothing untrue.
    """
    assert landing_observation(gate_landing())["observation_type"] == "github_pr"
    assert landing_observation(human_landing())["observation_type"] == "github_pr"
    assert landing_observation(push_landing())["observation_type"] == "inventory"


def test_the_landing_identity_is_the_commit_and_the_key_is_the_facts() -> None:
    """The reference must NOT move with the facts, and the key MUST.

    A commit on a branch is immutable, so one landing is one row forever -- content-addressing the
    reference would let drifted facts open a quiet second row. The key is content-addressed so an
    unchanged re-run replays and a changed fact reaches the same-source/different-facts branch.
    """
    first = landing_observation(gate_landing())
    again = landing_observation(gate_landing())
    drifted = landing_observation(gate_landing(files=("pyproject.toml",), files_changed=1))

    assert first == again
    assert drifted["source_reference"] == first["source_reference"]
    assert drifted["idempotency_key"] != first["idempotency_key"]


def test_observed_at_is_upstreams_clock() -> None:
    """With a wall-clock timestamp every pass would recompute a different fact hash for unchanged
    reality and conflict, forever."""
    assert landing_observation(gate_landing())["observed_at"] == LANDED.isoformat()


def test_a_large_landing_is_trimmed_to_fit_and_still_reports_its_true_size() -> None:
    files = tuple(f"src/orchestrator/services/{'x' * 90}_{index}.py" for index in range(30))
    body = landing_observation(gate_landing(files=files, files_changed=len(files)))
    facts = body["facts"]

    encoded = json.dumps(facts, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert len(encoded) <= MAX_FACT_BYTES
    assert facts["what_changed"]["files_changed"] == 30
    assert len(facts["what_changed"]["files"]) < 30
    # Deterministic, or the same landing would encode differently on the next pass and conflict.
    assert body == landing_observation(gate_landing(files=files, files_changed=len(files)))


def test_a_landing_whose_own_fields_are_maximal_still_fits() -> None:
    body = landing_observation(
        gate_landing(
            title="t" * 4000,
            files=tuple(f"path-{index}" for index in range(30)),
            files_changed=30,
            checks=tuple(
                Check(name=f"check-{index}" * 8, conclusion="success", run=31179223805 + index)
                for index in range(30)
            ),
        )
    )
    encoded = json.dumps(body["facts"], sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert len(encoded) <= MAX_FACT_BYTES
    # Trimming the permission evidence must be visible, not silent.
    permitted = body["facts"]["permitted_by"]
    assert permitted["checks_observed"] == 30
    assert len(permitted["checks"]) < 30
    assert body["facts"]["what_changed"]["files"] == []
