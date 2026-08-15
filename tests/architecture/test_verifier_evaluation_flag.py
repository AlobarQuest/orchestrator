"""WS-P2.32: a verifier adjudicates only what it evaluated -- and that rests on ONE call site.

`_authorize_outcome` admits a VERIFIER adjudication only when `from_verifier_evaluation` is set,
and the whole guarantee is that exactly one place in the source sets it: `verify_work_unit`, which
derived the outcome from the evidence chain. That property was PROSE in the service's docstring,
and this repo has been bitten by load-bearing prose before -- the conformance anti-tautology rule
is a comment describing a check nobody wrote.

The wire cannot assert the flag (no request schema declares it, and `CommandBase` leaves pydantic's
default `extra="ignore"` in place), so the only way it could spread is a second Python caller. This
makes adding one a decision rather than an accident.
"""

import re
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src" / "orchestrator"
FLAG = "from_verifier_evaluation"

#: The one site that may assert it, and why. A new entry here means a new path by which a verifier
#: adjudication can exist without an evaluation behind it -- which is the defect, not the fix.
AUTHORIZED_SETTERS = {"services/verifier.py"}


def _setters() -> dict[str, int]:
    found: dict[str, int] = {}
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        count = len(re.findall(rf"{FLAG}\s*=\s*True", path.read_text()))
        if count:
            found[str(path.relative_to(SOURCE_ROOT))] = count
    return found


def test_only_the_verify_command_may_assert_its_own_evaluation() -> None:
    setters = _setters()

    assert set(setters) == AUTHORIZED_SETTERS, (
        f"{FLAG}=True is set in {sorted(setters)}. Only the verify command may assert that an "
        "outcome came from the verifier's own evaluation of evidence; every other setter is a "
        "route by which a verifier adjudication can exist without one."
    )
    assert setters["services/verifier.py"] == 1, setters


def test_the_flag_is_still_the_thing_the_guard_reads() -> None:
    """A rename that missed this file would leave both assertions above vacuously true."""
    evidence = (SOURCE_ROOT / "services" / "evidence.py").read_text()

    assert f"{FLAG}: bool" in evidence, f"{FLAG} is no longer a parameter of the adjudication path"
    assert "verifier_evaluation_required" in evidence, "the named refusal is gone"
