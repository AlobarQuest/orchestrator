"""Architecture guard: the interpreter version is written once and read everywhere else.

`.python-version` at the repository root is the single source of truth. `uv venv` and
`uv sync` honour it with no flag, and `actions/setup-python` reads it through
`python-version-file:`, so a build worktree, CI and the main tree all resolve one number
from one file rather than agreeing by hand.

Three sites cannot read it and are held here by a CHECK instead of a shared variable.
The Dockerfile's base tag would need an ARG declared before `FROM`, coupling the tag to
the release workflow's argument list; `requires-python` and pyright's `pythonVersion` are
static metadata a build backend, a type checker and -- see below -- a formatter read
without running anything. A check is this repository's paved answer to that shape -- see
`check_profile_budget_agreement.py` and `check_rollout_transcription_currency.py`. All
three sites are held to EQUALITY. `requires-python` was held only to not EXCLUDING the pin
until 2026-09-09, while the floor and the pin deliberately differed; they no longer do,
and the weaker form admitted a floor like `>=3.13` -- a claim about an interpreter nothing
here has ever run, passing a check written to refuse exactly that.

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


def test_the_requires_python_floor_is_the_pinned_version() -> None:
    """The floor IS the pin. Above it is a contradiction; below it is a false claim.

    Above the pin, the interpreter every environment actually runs would not satisfy the
    project's own declared floor -- a contradiction no amount of `.python-version` can fix.
    Below it, the floor asserts that some older interpreter can run this tree, and since
    2026-09-09 that assertion is not merely unexercised but WRONG: the source now carries PEP
    758 `except A, B:`, which is a hard SyntaxError before 3.14. A resolver that honours a
    lagging floor would install this package onto an interpreter that cannot import it, so the
    floor is load-bearing for refusal rather than documentation, and equality is what makes the
    refusal honest.

    EQUALITY IS AFFORDABLE HERE BECAUSE THIS IS AN APPLICATION. `.python-version` holds exactly
    one number and every environment derives from it; there is no supported range and no
    downstream installer to keep on an older interpreter. Note equality constrains where the
    FLOOR sits, not what the specifier admits -- `>=3.14` still admits 3.15 -- so the only thing
    forbidden is a floor below the one number, which is the defect and not a use case.

    WHY MOVING `.python-version` IS A PAIRED OPERATION WITH REAL CONSEQUENCES, MEASURED ON
    2026-09-09 RATHER THAN ESTIMATED. `[tool.ruff]` sets no `target-version`, so ruff derives
    BOTH its lint and its format target from this floor. Raising it from `>=3.12` to `>=3.14`
    rewrote 26 sites across 23 files, by clean differential (`>=3.12` gave `563 files already
    formatted` and zero lint findings):

      * 11 x UP037 -- quotes stripped from forward-reference annotations in five
        `src/orchestrator/` modules and two tests. Those are `NameError` on 3.12; reverting the
        floor afterwards reports all 11 as F821.
      * 15 files reformatted under PEP 758 -- `except (A, B):` becomes `except A, B:`, a hard
        SyntaxError on 3.12 rather than a style difference. Three of the affected modules
        (`deploy_watcher`, `revision_watcher`, `tool_installer`) back scheduled lanes that run
        from the main tree's working copy.

    So the floor is a lever on the source tree. Moving `.python-version` moves it, which rewrites
    the tree into syntax the previous interpreter cannot parse -- do that only when nothing is
    left on the old one, and expect a large mechanical diff in the same commit.
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

    assert declared == pinned, (
        f"requires-python is {floor!r}; expected '>={pinned_version()}'. Above the pin the "
        f"floor excludes the interpreter every environment here runs; below it, the floor "
        f"claims an older interpreter can run a tree that carries 3.14-only syntax. If this "
        f"is a deliberate interpreter move, edit .python-version and let ruff rewrite the "
        f"source in the same commit; see this test's docstring for what that costs."
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


def _step_containers(document: dict) -> list[dict]:
    """Everything in one document that carries a `steps:` list."""
    containers = list(document.get("jobs", {}).values())
    if "runs" in document:  # a composite action puts its steps under `runs:`
        containers.append(document["runs"])
    return containers


def _setup_python_steps() -> list[tuple[Path, dict]]:
    steps = []
    for path in _step_documents():
        document = yaml.safe_load(path.read_text()) or {}
        for container in _step_containers(document):
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


# A `run:` block executes on the RUNNER, so `python`/`python3` there is the machine's system
# interpreter -- on `ubuntu-latest` that is Ubuntu 24.04's Python 3.12.3, whatever this repository
# pins. An absolute path does NOT make it something else: `/usr/bin/python3` is that same
# interpreter, so only `.venv/bin/` is exempted by path, not "has a path".
RUNNER_PYTHON = re.compile(r"(?<![\w.-])python(?:3(?:\.\d+)?)?(?![\w:.-])")

# `uv run`, `uvx` and the project venv resolve through `.python-version` themselves, so they are
# deliberately not required to carry a setup step; `docker` names another machine's interpreter.
DERIVES_ITS_OWN_INTERPRETER = re.compile(r"(?<![\w-])(uvx?|docker)(?![\w-])|\.venv/bin/$")

# A marker governs only the command it introduces, so the line is cut into commands first. Both
# halves of that are load-bearing and each has its own case below: `uv sync && python3 x.py` runs
# a BARE python3 despite the `uv` earlier on the line, and the release workflow builds an image
# and then reads its digest with a runner `python3` in the SAME `run:` block, so a step-level or
# line-level docker exemption would wave that invocation through.
COMMAND_SEPARATOR = re.compile(r"&&|\|\||[;|]")


def runner_python_lines(script: str) -> list[tuple[int, str]]:
    """The lines of a `run:` block that invoke the runner's own interpreter.

    Split out from the test so every clause above can be exercised directly. That is not tidiness:
    once every workflow carries its setup step the scan below short-circuits on the first one and
    stops reaching this function at all, so the real-file scan cannot kill a mutation of these
    patterns. Measured -- four mutations survived the file scan and die here.
    """
    found = []
    for number, line in enumerate(script.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        for command in COMMAND_SEPARATOR.split(line):
            match = RUNNER_PYTHON.search(command)
            if match is None:
                continue
            if DERIVES_ITS_OWN_INTERPRETER.search(command[: match.start()]):
                continue
            found.append((number, line.strip()))
            break
    return found


def test_every_job_running_runner_python_sets_it_up_before_the_first_use() -> None:
    """The sibling guard above is keyed on PRESENCE: it asks whether every `setup-python` step
    reads the file. A workflow with NO such step is not in its population, so it passed for months
    while `release-image.yml` -- the production image build -- ran five `python3` steps against
    files in this tree on the runner's system interpreter. That is this repository's recurring
    shape: a filter reporting clean because the broken thing never entered it.

    This is the inverse, and ORDERING is half of it -- a setup step after the first `python3` line
    is decoration. Both halves were proven against the real file rather than a fixture: at
    `ada8d20` this fires on all five of that workflow's steps, and it goes green on the four-line
    step that fixes it; with that step moved below `Read pin` it fires on `Read pin` alone.
    """
    offenders = []
    for path in _step_documents():
        document = yaml.safe_load(path.read_text()) or {}
        for container in _step_containers(document):
            prepared = False
            for step in container.get("steps", []) or []:
                if str(step.get("uses", "")).startswith("actions/setup-python"):
                    # A CONDITIONAL setup step prepares the job only when its condition names
                    # `.python-version`. The release workflow builds an arbitrary `inputs.ref`,
                    # including revisions older than that file, where `setup-python` throws on the
                    # missing path -- so it runs the step only when the built tree carries a pin,
                    # which is the tree's own answer rather than a second copy of a number. Any
                    # OTHER condition is refused here rather than ignored: silently accepting one
                    # would let `if: false` satisfy this guard, which is the same
                    # never-entered-the-filter defect the guard exists to close.
                    # Read with a sentinel rather than `or ""`: YAML parses `if: false` as the
                    # BOOLEAN False, which `or ""` turns into "no condition" -- so the tidier
                    # spelling accepts the one mutant this clause exists to refuse. Found by
                    # mutating it, not by reading it.
                    condition = step.get("if")
                    if condition is not None and ".python-version" not in str(condition):
                        continue
                    prepared = True
                    continue
                if prepared:
                    continue
                where = step.get("name", step.get("uses", "?"))
                for number, line in runner_python_lines(str(step.get("run", "") or "")):
                    offenders.append(f"{path.name}: {where!r} line {number}: {line}")

    assert not offenders, (
        "these `run:` steps invoke the runner's own interpreter with no `actions/setup-python` "
        "before them in the same job, so they execute on whatever the runner image ships rather "
        "than on .python-version:\n  " + "\n  ".join(offenders)
    )


def test_the_line_classifier_reports_a_bare_invocation_and_exempts_the_rest() -> None:
    """Each case here kills a specific mutation of the two patterns above; see
    `runner_python_lines` for why the file scan cannot."""
    for line in (
        "python3 scripts/shape_registry_context.py --source x",
        'DIGEST=$(python3 -c "import json,sys; print(1)")',
        "python -m scripts.check_something",
        "python3 - <<'PY' >> \"$GITHUB_OUTPUT\"",
        'python3.14 -c "import sys"',
        # An absolute path is not an exemption -- this IS the runner's system interpreter.
        "/usr/bin/python3 scripts/foo.py",
        # A marker governs the command it introduces, not the whole line.
        "uv sync --frozen && python3 scripts/foo.py",
        "docker load < image.tar; python3 scripts/foo.py",
    ):
        assert runner_python_lines(line), f"a bare runner invocation went unreported: {line}"

    for line in (
        ".venv/bin/python scripts/check_profile_budget_agreement.py",
        'uv run python -c "import sys"',
        "uv run alembic upgrade head",
        "uvx ruff@0.16.2 format --check .",
        'docker run --rm python:3.14-slim python -c "import sys"',
        "docker buildx build --build-arg BASE=python:3.14-slim --push .",
        "IMAGE=python:3.14-slim",
        "  # python3 used to run here before the step was added",
        # `--python 3.14` is a flag, not an invocation.
        "uv venv --clear --python 3.14",
        "PATH=$PWD/.venv/bin:$PATH .venv/bin/python -m pytest",
    ):
        assert not runner_python_lines(line), (
            f"reported something that is not runner python: {line}"
        )

    # Per LINE, not per step: the release workflow does exactly this shape.
    after_a_docker_line = (
        'docker buildx build -t ghcr.io/a/b:sha --push .\nDIGEST=$(python3 -c "1")\n'
    )
    assert [number for number, _ in runner_python_lines(after_a_docker_line)] == [2]
