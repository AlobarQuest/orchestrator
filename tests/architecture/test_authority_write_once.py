"""`WorkUnit.authority` is write-once. This test is what makes deleting `is_expansion()` safe.

`is_expansion()` compared two authority envelopes and fail-closed on capability-level and BUDGET
expansion. It had zero production callers, so WS-P2.15 deleted it. But it is NOT true that the
standing-context classifier covers the same ground -- `classify_context_update()` compares
standing contexts (capability SETS and authority-profile RANK) and never looks at budgets or
capability levels at all. Writing "the classifier enforces envelope expansion" into the ADR would
have been a lie, and specifically the expensive kind: WS-P2.4 (cost controls) is about budgets.

What actually makes the deletion safe is STRUCTURAL, and it is asserted here:

    A work unit's authority envelope is assigned exactly once, at construction. Nothing mutates
    it. So there is no "old envelope vs new envelope" for an expansion check to compare.

(The live budget-raising path -- `retry`, services/claims.py -- raises `unit.max_attempts`, the
COLUMN. It never touched the envelope, and `is_expansion` never saw it. Envelope and enforced
budget already diverge.)

The point of this file is the FUTURE. If WS-P2.4, or anyone, introduces a path that raises a
unit's budget or capabilities by mutating the envelope, this test goes red and they are forced to
bring a fail-closed check with it. A structural invariant that fails loudly beats a latent
function nobody calls -- which is the entire thesis of WS-P2.15.

SCOPING NOTE. An AST scan for `unit.authority = ...` alone would be trivially green today and
blind to the three forms a budget-raiser would actually use. All four are checked:

  1. attribute assignment   unit.authority = {...}
  2. setattr                setattr(unit, "authority", {...})
  3. bulk update            session.execute(update(WorkUnit).values(authority=...))
  4. in-place JSON mutation unit.authority["budgets"]["max_attempts"] = 9

Form 4 is the one a naive scan misses and the one a budget-raiser would most naturally write --
so it is the form the negative control plants.
"""

import ast
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from orchestrator.persistence.models import WorkUnit

SRC = Path("src")

# The ONLY three places a WorkUnit is constructed. Verified by `grep "WorkUnit("` over src/.
# Everything else that mentions `authority=` is assigning it on a DIFFERENT object -- a
# WorkPackageRevision, a ProposedUnit, a DecompositionProposalUnit, a DTO. An earlier draft of
# this guard listed eight "assignment sites" because it grepped for `authority=` without ever
# checking the receiver's type. The conclusion survived; the evidence did not.
CONSTRUCTION_SITES = {
    "orchestrator/services/packages.py",
    "orchestrator/services/deployment_observations.py",
    # WS-P2.8: the package-declared follow-up review unit. Same shape as the release-observation
    # site above -- a frozen envelope built inline, assigned once at construction, never mutated.
    "orchestrator/services/follow_ups.py",
}

AUTHORITY = "authority"


def _sources(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def _mutations(root: Path, attribute: str = AUTHORITY) -> list[str]:
    """Every place `attribute` is written OUTSIDE a WorkUnit(...) construction.

    Parameterised because WS-P2.12 added a second write-once column
    (`context_enrichment`) with the same six evasion forms. One scanner, two
    guards: a second copy would drift, and the copy would be the blind one.
    """
    offenders: list[str] = []
    for path in _sources(root):
        rel = str(path.relative_to(root))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            where = f"{rel}:{getattr(node, 'lineno', 0)}"

            # 1. attribute assignment: x.authority = ...
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Attribute) and target.attr == attribute:
                        offenders.append(f"{where} attribute assignment to .{attribute}")
                    # 4. in-place JSON mutation: x.authority["budgets"]["max_attempts"] = 9
                    if isinstance(target, ast.Subscript) and _roots_in_attr(target, attribute):
                        offenders.append(f"{where} in-place mutation of .{attribute}[...]")
            if isinstance(node, ast.AugAssign):
                target = node.target
                if isinstance(target, ast.Attribute) and target.attr == attribute:
                    offenders.append(f"{where} augmented assignment to .{attribute}")
                if isinstance(target, ast.Subscript) and _roots_in_attr(target, attribute):
                    offenders.append(f"{where} in-place mutation of .{attribute}[...]")

            if isinstance(node, ast.Call):
                func = node.func
                # 2. setattr(unit, "authority", ...)
                if (
                    isinstance(func, ast.Name)
                    and func.id == "setattr"
                    and len(node.args) >= 2
                    and isinstance(node.args[1], ast.Constant)
                    and node.args[1].value == attribute
                ):
                    offenders.append(f"{where} setattr(..., '{attribute}', ...)")
                # 3. update(WorkUnit).values(authority=...) -- keyword OR positional dict.
                # `.values({"authority": ...})` is the same statement written the other way, and
                # a guard that only sees the keyword form is a guard you evade by reformatting.
                if isinstance(func, ast.Attribute) and func.attr == "values":
                    if any(kw.arg == attribute for kw in node.keywords):
                        offenders.append(f"{where} bulk update .values({attribute}=...)")
                    if any(
                        isinstance(arg, ast.Dict)
                        and any(
                            isinstance(key, ast.Constant) and key.value == attribute
                            for key in arg.keys
                            if key is not None
                        )
                        for arg in node.args
                    ):
                        offenders.append(f'{where} bulk update .values({{"{attribute}": ...}})')
                # SQLAlchemy's escape hatch for in-place JSON edits. Without flag_modified the ORM
                # does not notice a mutated dict -- so its PRESENCE is the tell that someone is
                # deliberately persisting one.
                if (
                    isinstance(func, ast.Name)
                    and func.id == "flag_modified"
                    and len(node.args) >= 2
                    and isinstance(node.args[1], ast.Constant)
                    and node.args[1].value == attribute
                ):
                    offenders.append(f"{where} flag_modified(..., '{attribute}')")
                # in-place dict API: x.authority.update(...) / .setdefault(...) / .pop(...)
                if (
                    isinstance(func, ast.Attribute)
                    and func.attr in {"update", "setdefault", "pop", "clear"}
                    and isinstance(func.value, ast.Attribute)
                    and func.value.attr == attribute
                ):
                    offenders.append(f"{where} in-place .{attribute}.{func.attr}(...)")
    return offenders


def _roots_in_attr(node: ast.Subscript, attribute: str = AUTHORITY) -> bool:
    """True for `x.<attribute>[...]` at any subscript depth (`x.a['b']['c']`)."""
    current: ast.expr = node
    while isinstance(current, ast.Subscript):
        current = current.value
    return isinstance(current, ast.Attribute) and current.attr == attribute


def test_the_work_unit_authority_envelope_is_never_mutated() -> None:
    offenders = _mutations(SRC)
    assert not offenders, (
        "the work-unit authority envelope is written outside construction:\n  "
        + "\n  ".join(offenders)
        + "\n\nThe envelope is write-once, and that is the ONLY reason it is safe to have no "
        "envelope-expansion detector (is_expansion was deleted in WS-P2.15). If you are raising "
        "a unit's budget or capabilities here, you must introduce a fail-closed expansion check "
        "along with it -- an approved authority fingerprint would otherwise cover an envelope "
        "the human never approved."
    )


def test_the_authority_column_is_not_mutation_tracked() -> None:
    """The AST guard above is only load-bearing while the column is a PLAIN ``JSONB``.

    The `flag_modified` clause works by treating the call's PRESENCE as the tell that someone
    means an in-place edit to stick -- which holds only because the ORM cannot notice the edit
    on its own. Wrap the column in ``MutableDict.as_mutable(JSONB)`` and that inverts: SQLAlchemy
    tracks the dict itself, an in-place alias mutation persists with NO `flag_modified` call
    anywhere, and the guard above goes quietly blind. Worse, the mutation need not even name
    `.authority` -- an alias taken earlier (`env = unit.authority; env["budgets"][...] = 9`)
    defeats every one of the four AST forms.

    So this is the last hole in the write-once guarantee, and with `is_expansion()` deleted
    (WS-P2.15) that guarantee is the *entire* defence against envelope expansion.

    Checked on a TRANSIENT instance on purpose: `as_mutable` coerces at attribute-set time, so no
    database, session or flush is involved, and the assertion cannot be weakened by fixture setup.
    """
    unit = WorkUnit(authority={"capabilities": {}, "budgets": {"max_attempts": 1}})

    assert type(unit.authority) is dict, (
        f"WorkUnit.authority now coerces to {type(unit.authority).__name__}, so the column is "
        "mutation-tracked. tests/architecture/test_authority_write_once.py's AST guard assumes "
        "it is NOT: with tracking on, an in-place mutation through an alias persists without "
        "flag_modified and without ever naming `.authority`, so the guard sees nothing and an "
        "approved authority fingerprint can cover an envelope the human never approved. Either "
        "revert the column to plain JSONB, or ship a fail-closed envelope-expansion check to "
        "replace the write-once guarantee this removes."
    )


def test_the_pin_would_catch_a_mutation_tracked_column() -> None:
    """The control. Without this, the assertion above passes for any model that happens to hold
    a plain dict, and would keep passing if the check itself stopped discriminating."""

    class Base(DeclarativeBase):
        pass

    class TrackedUnit(Base):
        __tablename__ = "planted_tracked_unit"

        id: Mapped[int] = mapped_column(primary_key=True)
        authority: Mapped[dict[str, Any]] = mapped_column(MutableDict.as_mutable(JSONB))

    planted = TrackedUnit(id=1, authority={"budgets": {"max_attempts": 1}})
    assert type(planted.authority) is not dict
    assert isinstance(planted.authority, MutableDict)


def test_the_named_construction_sites_still_exist() -> None:
    """If a WorkUnit is constructed somewhere new, this guard's premise needs re-checking."""
    constructed_in = {
        str(path.relative_to(SRC))
        for path in _sources(SRC)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "WorkUnit"
    }
    assert constructed_in == CONSTRUCTION_SITES, (
        f"WorkUnit is now constructed in {sorted(constructed_in)}, "
        f"not {sorted(CONSTRUCTION_SITES)}. Re-verify that the new site assigns the envelope "
        "once and never mutates it, then update CONSTRUCTION_SITES."
    )


# -------------------------------------------------------------------------------------------
# The guard must be shown to fail -- on the form a naive scan misses.
# -------------------------------------------------------------------------------------------


@pytest.fixture
def fake_src(tmp_path: Path) -> Path:
    root = tmp_path / "src"
    (root / "orchestrator").mkdir(parents=True)
    return root


def _plant(root: Path, body: str) -> list[str]:
    (root / "orchestrator" / "planted.py").write_text(body, encoding="utf-8")
    return _mutations(root)


def test_the_guard_flags_in_place_json_mutation_of_the_budget(fake_src: Path) -> None:
    """THE control: the form WS-P2.4 would most naturally write, and the one a scan for
    `unit.authority = ...` cannot see."""
    offenders = _plant(
        fake_src,
        'def raise_budget(unit, n):\n    unit.authority["budgets"]["max_attempts"] = n\n',
    )
    assert any("in-place mutation" in offender for offender in offenders), offenders


def test_the_guard_flags_attribute_assignment(fake_src: Path) -> None:
    offenders = _plant(fake_src, "def overwrite(unit, env):\n    unit.authority = env\n")
    assert any("attribute assignment" in offender for offender in offenders), offenders


def test_the_guard_flags_setattr(fake_src: Path) -> None:
    offenders = _plant(fake_src, 'def overwrite(unit, env):\n    setattr(unit, "authority", env)\n')
    assert any("setattr" in offender for offender in offenders), offenders


def test_the_guard_flags_a_bulk_update(fake_src: Path) -> None:
    offenders = _plant(
        fake_src,
        "def overwrite(session, env):\n"
        "    session.execute(update(WorkUnit).values(authority=env))\n",
    )
    assert any("bulk update" in offender for offender in offenders), offenders


def test_the_guard_flags_in_place_dict_api(fake_src: Path) -> None:
    offenders = _plant(
        fake_src, 'def overwrite(unit):\n    unit.authority.update({"budgets": {}})\n'
    )
    assert any("in-place" in offender for offender in offenders), offenders


def test_the_guard_flags_a_positional_dict_bulk_update(fake_src: Path) -> None:
    """The same statement written the other way. A guard you evade by reformatting is not one."""
    offenders = _plant(
        fake_src,
        "def overwrite(session, env):\n"
        '    session.execute(update(WorkUnit).values({"authority": env}))\n',
    )
    assert any("bulk update" in offender for offender in offenders), offenders


def test_the_guard_flags_flag_modified(fake_src: Path) -> None:
    """SQLAlchemy's escape hatch for persisting an in-place JSON edit.

    Without `flag_modified` the ORM never notices a mutated dict, so its presence is the tell
    that someone means the mutation to stick.
    """
    offenders = _plant(
        fake_src,
        'def persist(unit):\n    unit.authority["budgets"]["max_attempts"] = 9\n'
        '    flag_modified(unit, "authority")\n',
    )
    assert any("flag_modified" in offender for offender in offenders), offenders


def test_the_guard_is_quiet_on_an_unrelated_assignment(fake_src: Path) -> None:
    """A `authority=` kwarg on a DIFFERENT object is not a mutation. This is the false positive
    the earlier draft's evidence list was built on."""
    offenders = _plant(
        fake_src,
        "def build(env):\n    return WorkPackageRevision(authority=env)\n",
    )
    assert not offenders, offenders
