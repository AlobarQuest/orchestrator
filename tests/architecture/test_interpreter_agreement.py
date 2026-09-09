"""Architecture guard: the interpreter version is written once and read everywhere else.

`.python-version` at the repository root is the single source of truth. `uv venv` and
`uv sync` honour it with no flag, and `actions/setup-python` reads it through
`python-version-file:`, so a build worktree, CI and the main tree all resolve one number
from one file rather than agreeing by hand.

Three sites cannot read it and are held here by a CHECK instead of a shared variable.
The Dockerfile's base tag would need an ARG declared before `FROM`, coupling the tag to
the release workflow's argument list; `requires-python` and pyright's `pythonVersion` are
static metadata a build backend and a type checker read without running anything. A check
is this repository's paved answer to that shape -- see `check_profile_budget_agreement.py`
and `check_rollout_transcription_currency.py`. The Dockerfile and pyright are held to
EQUALITY; `requires-python` is held only to not excluding the pin, for a reason its own
test records.

WHY PYRIGHT KEEPS A LITERAL AT ALL. Omitting `pythonVersion` is derivation only in
appearance. Measured 2026-09-09 against pyright 1.1.411: with the key absent it reports
`Assuming Python version 3.12.13` on a tree whose `.venv` is 3.14 and whose
`requires-python` is `>=3.14` -- it reads `python3` from PATH, not `venvPath`/`venv` and
not the project floor. Under `make check` the Makefile prepends `.venv/bin`, so that one
invocation happens to be right; an editor, or a bare `pyright`, silently checks against
whatever interpreter the machine has. A literal under this check is loud where inference
is quiet.

WHY THE DOCKERFILE LITERAL STILL EARNS ITS PLACE. `tests/architecture/test_container.py`
recorded that asserting the interpreter by value is what an update bot's proposal collides
with, so a language-version replacement cannot reach production without a person editing
it. That property is unchanged and improved: a `docker` ecosystem bump edits the base tag,
Dependabot knows nothing about `.python-version`, and this check goes red until a human
moves the one line. The collision moved to `.python-version`; it did not go away.
"""

import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
VERSION_FILE = ROOT / ".python-version"
DOCKERFILE = ROOT / "Dockerfile"
PYPROJECT = ROOT / "pyproject.toml"
WORKFLOWS = ROOT / ".github/workflows"
ACTIONS = ROOT / ".github/actions"

MAJOR_MINOR = re.compile(r"^\d+\.\d+$")
BARE_FLOOR = re.compile(r"^>=\d+\.\d+$")


def pinned_version() -> str:
    """The one number. Read this rather than writing a literal of your own."""
    return VERSION_FILE.read_text().strip()


def test_the_version_file_holds_exactly_one_bare_major_minor() -> None:
    """A patch component here would defeat the point: `uv` would demand that exact build and
    `setup-python` would pin a release that ages out of the runner image, so the file would
    become a thing to maintain rather than a thing to read."""
    assert MAJOR_MINOR.match(pinned_version()), (
        f".python-version must hold a bare major.minor, not {pinned_version()!r}"
    )


def test_both_dockerfile_base_tags_name_the_pinned_version() -> None:
    tags = re.findall(r"^FROM python:(\S+?)-slim", DOCKERFILE.read_text(), re.MULTILINE)

    assert len(tags) == 2, f"expected a builder and a runtime stage, found {tags}"
    assert set(tags) == {pinned_version()}, (
        f"Dockerfile base tags {sorted(set(tags))} disagree with .python-version "
        f"({pinned_version()}). If this is a deliberate interpreter move, edit "
        f".python-version; it is the site everything else reads."
    )


def test_the_requires_python_floor_admits_the_pinned_version() -> None:
    """The floor may sit BELOW the pin; it may never sit above it.

    Above it, the interpreter every environment actually runs would not satisfy the project's
    own declared floor -- a contradiction no amount of `.python-version` can fix. At or below
    it, the floor is a claim about older interpreters that nothing here executes.

    THAT WEAKER CLAIM IS A DEFERRAL, NOT AN OVERSIGHT, AND THE COST OF CLOSING IT WAS MEASURED
    ON 2026-09-09 RATHER THAN ESTIMATED. Raising the floor to the pin is the obviously right
    end state and it is not a one-line change, because `[tool.ruff]` sets no `target-version`
    and ruff derives BOTH its lint and its format target from `requires-python`. Raising it to
    `>=3.14` produced, by clean differential (`>=3.12` gives `563 files already formatted` and
    zero lint findings):

      * 11 x UP037 -- quotes stripped from forward-reference annotations in five
        `src/orchestrator/` modules and two tests. Those become `NameError` on 3.12; reverting
        the floor afterwards reports all 11 as F821.
      * 15 files reformatted under PEP 758 -- `except (A, B):` becomes `except A, B:`, which is
        a hard SyntaxError on 3.12, not a style difference. Three of the affected modules
        (`deploy_watcher`, `revision_watcher`, `tool_installer`) back scheduled lanes that run
        from the main tree's working copy.

    So the floor is a lever on the source tree, and pulling it while any environment is still
    on an older interpreter breaks that environment. Both cascades are free once nothing is on
    3.12 -- which is why this was sequenced after the main tree's venv moves rather than
    dropped. Backlogged; do not close it by editing this test alone.
    """
    floor = tomllib.loads(PYPROJECT.read_text())["project"]["requires-python"]

    # The shape is asserted before it is parsed, so a specifier this check cannot reason about
    # fails with its own message rather than a ValueError from `int()` several lines later. A
    # compound specifier (`>=3.12,<4.0`) is refused rather than accepted-and-ignored: an upper
    # bound could exclude the pin, and silently dropping it is how a guard stops guarding.
    assert BARE_FLOOR.match(floor), (
        f"requires-python is {floor!r}; this check understands only a bare '>=X.Y' floor. "
        f"Teach it what an upper bound means before writing one."
    )

    pinned = tuple(int(part) for part in pinned_version().split("."))
    declared = tuple(int(part) for part in floor.removeprefix(">=").split("."))

    assert declared <= pinned, (
        f"requires-python is {floor!r}, which excludes the pinned interpreter "
        f"{pinned_version()} that every environment here runs"
    )


def test_pyright_checks_the_pinned_version() -> None:
    pyright = tomllib.loads(PYPROJECT.read_text())["tool"]["pyright"]

    assert pyright.get("pythonVersion") == pinned_version(), (
        f"[tool.pyright] pythonVersion is {pyright.get('pythonVersion')!r}; expected "
        f"{pinned_version()!r}. Do not delete the key to make this pass -- absence is not "
        f"derivation here; see this module's docstring."
    )


def _step_documents() -> list[Path]:
    """Every file that can carry a `uses:` step.

    `.yaml` is scanned as well as `.yml` because GitHub accepts both, and composite actions
    under `.github/actions/` are scanned because they carry steps too. Neither exists here
    today, and that is exactly why: the guard has to see the file somebody adds later, and a
    partial miss is invisible -- the emptiness assertion below catches the whole set going
    missing and cannot catch one workflow being skipped.
    """
    documents = [path for suffix in ("*.yml", "*.yaml") for path in WORKFLOWS.glob(suffix)]
    documents += [
        path for suffix in ("action.yml", "action.yaml") for path in ACTIONS.rglob(suffix)
    ]
    return sorted(documents)


def _setup_python_steps() -> list[tuple[Path, dict]]:
    steps = []
    for path in _step_documents():
        document = yaml.safe_load(path.read_text()) or {}
        containers = list(document.get("jobs", {}).values())
        if "runs" in document:  # a composite action puts its steps under `runs:`
            containers.append(document["runs"])
        for container in containers:
            for step in container.get("steps", []) or []:
                if str(step.get("uses", "")).startswith("actions/setup-python"):
                    steps.append((path, step))
    return steps


def test_every_setup_python_step_reads_the_version_file() -> None:
    """A step that names its own version is a second copy of the number, and a step LABEL
    that names it is a third -- the one nobody thinks to update, because nothing executes it.
    `python-version-file:` is preferred over dropping the input entirely: measured against the
    action's source at `v7`, the explicit input throws when the file is missing while the
    implicit fallback logs a warning and carries on with no version at all."""
    steps = _setup_python_steps()
    assert steps, "no setup-python steps found; this guard would pass vacuously"

    for path, step in steps:
        where = f"{path.name}: {step.get('name', step['uses'])}"
        assert step.get("with", {}).get("python-version-file") == ".python-version", (
            f"{where} does not read .python-version"
        )
        assert "python-version" not in step.get("with", {}), (
            f"{where} names a version as well as the file; the action then ignores the file"
        )
        assert not re.search(r"\d", str(step.get("name", ""))), (
            f"{where} carries a version in its label, which nothing executes and nobody updates"
        )
