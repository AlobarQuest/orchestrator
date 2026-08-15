from pathlib import Path

WORKFLOW_ROOT = Path(".github/workflows")

FORBIDDEN = (
    "gh pr merge",
    "/merges",
    "git push origin main",
    "workflow_dispatch",
    "coolify",
    "deploy",
)

# factory-runner-pilot.yml and release-image.yml are deliberate, human-triggered
# (workflow_dispatch) exceptions to the "no dispatch/deploy" guard: the former
# invokes the factory runner against an approved work unit, the latter builds and
# pushes a release image (its SECURITY_STANDARDS_DEPLOY_KEY secret name matches
# "deploy" as a substring, and the actual runtime cutover stays a separate manual
# step covered by tests/architecture/test_release_workflow.py's own no-deploy checks).
# attest-exit-criteria.yml is a third, weaker exception: it is read-only (one unauthenticated
# GET of production's public OpenAPI document) and carries workflow_dispatch so the guard can
# be re-run on demand after a production image swap. It merges nothing and writes nothing.
# attest-wave-exit.yml (WS-P2.39) is the same weakest kind for the same reason: read-only,
# one unauthenticated GET, workflow_dispatch so a wave bar can be re-attested on demand.
MANUAL_DISPATCH_WORKFLOWS = {
    "attest-exit-criteria.yml",
    "attest-wave-exit.yml",
    "factory-runner-pilot.yml",
    "release-image.yml",
}

# ADR-0016/0018/0023: the native auto-merge lane, vendored here 2026-08-15. Unlike the four
# above it is exempt from ONE string rather than from the scan -- it arms GitHub's own
# auto-merge and must still never build, never write to main directly, never be triggered by
# hand. Narrow rather than whole-file because the guard's own message is the point: the
# exemption is taken openly, never by finding a verb this list does not cover. `gh api graphql
# enablePullRequestAutoMerge` would spell none of these strings and arm exactly the same thing.
#
# Its twin is NATIVE_AUTO_MERGE_SEQUENCES in tests/architecture/test_ws33_scope_guards.py,
# which scans this same directory with a different vocabulary and its own separate allowlist.
# Neither used to name the other, and an addition that updates one leaves the other red.
NATIVE_AUTO_MERGE_WORKFLOW = "dependabot-auto-merge.yml"
NATIVE_AUTO_MERGE_EXEMPT = frozenset({"gh pr merge"})


def _violations(path: Path, exempt: frozenset[str]) -> list[str]:
    text = path.read_text(encoding="utf-8").lower()
    return [value for value in FORBIDDEN if value not in exempt and value in text]


def _exemptions_for(name: str) -> frozenset[str]:
    return NATIVE_AUTO_MERGE_EXEMPT if name == NATIVE_AUTO_MERGE_WORKFLOW else frozenset()


def test_workflows_never_merge_deploy_or_push_main() -> None:
    for path in WORKFLOW_ROOT.glob("*"):
        if path.name in MANUAL_DISPATCH_WORKFLOWS:
            continue
        assert not _violations(path, _exemptions_for(path.name)), (
            f"{path.name} carries a forbidden string. If it is a deliberate exception, "
            "name it here with a reason -- openly, never by rewording."
        )


def test_the_native_auto_merge_exemption_is_load_bearing_and_scoped() -> None:
    """Both halves, because an exemption that has stopped being needed is drift, and one that
    is keyed too widely stops guarding a file nobody meant to exempt."""
    gate = WORKFLOW_ROOT / NATIVE_AUTO_MERGE_WORKFLOW

    assert _violations(gate, frozenset()) == ["gh pr merge"]
    assert _violations(gate, _exemptions_for(gate.name)) == []
    assert _violations(gate, _exemptions_for("quality.yml")) == ["gh pr merge"]


def test_the_exempted_command_only_ever_arms() -> None:
    """The exemption's whole justification, asserted rather than described.

    `gh pr merge --auto` asks GitHub to land the pull request once the required checks pass;
    GitHub enforces the waiting, so this workflow cannot get it wrong. `gh pr merge --squash`
    with no `--auto` lands it immediately and is the thing every guard here exists to refuse --
    and the exemption above cannot tell the two apart, because they differ by a flag and not by
    any string either scanner knows.
    """
    text = (WORKFLOW_ROOT / NATIVE_AUTO_MERGE_WORKFLOW).read_text(encoding="utf-8")
    commands = [line.strip() for line in text.splitlines() if "gh pr merge" in line]

    assert commands, "the exempted workflow no longer runs the command it is exempted for"
    for command in commands:
        assert "--auto" in command, f"{command!r} lands a pull request rather than arming one"
