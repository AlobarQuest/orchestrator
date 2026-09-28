from pathlib import Path

SOURCE_ROOT = Path("src/orchestrator")


def _source_files() -> tuple[Path, ...]:
    return tuple(sorted(SOURCE_ROOT.rglob("*.py")))


DISPATCH_VOCABULARY = ("workflow_dispatch", "factory_runner", "github.actions")
# `api/routes.py`, `api/schemas.py` and `config.py` were listed here too until 2026-09-28 and
# spelled none of this vocabulary; they came out because an unneeded exemption is unwatched.
DISPATCH_EXEMPT_PATHS = {
    Path("src/orchestrator/services/execution/dispatch.py"),
}


def test_ws34_adds_no_factory_runner_or_workflow_dispatch_code() -> None:
    matches = [
        f"{path}:{value}"
        for path in _source_files()
        if path not in DISPATCH_EXEMPT_PATHS
        for value in DISPATCH_VOCABULARY
        if value in path.read_text(encoding="utf-8").lower()
    ]

    assert not matches


def test_ws34_dispatch_exemptions_name_only_files_that_exist_and_still_need_them() -> None:
    """The allowlist above is a filter, so a stale entry excuses nothing and reddens nothing. Each
    entry must name a file that exists and still spells the vocabulary it is excused from."""
    missing = sorted(
        str(path)
        for path in DISPATCH_EXEMPT_PATHS
        if not path.is_file() or not path.is_relative_to(SOURCE_ROOT)
    )
    assert not missing, f"the dispatch exemptions name no file inside the tree they scan: {missing}"

    unused = sorted(
        str(path)
        for path in DISPATCH_EXEMPT_PATHS
        if not any(
            value in path.read_text(encoding="utf-8").lower() for value in DISPATCH_VOCABULARY
        )
    )
    assert not unused, (
        f"these files are exempt from the dispatch vocabulary but no longer spell any of it: "
        f"{unused}. Remove them."
    )


def test_ws34_adds_no_production_deploy_coolify_or_automatic_merge_path() -> None:
    forbidden = ("coolify", "gh pr merge", "git push origin main", "merge_to_main")
    matches = [
        f"{path}:{value}"
        for path in _source_files()
        for value in forbidden
        if value in path.read_text(encoding="utf-8").lower()
    ]

    assert not matches
