"""Metadata rules over `Base.metadata` and the migration chain.

Each rule is a small predicate shared by the real test and a control that feeds it a
known-bad input, so a predicate that stopped refusing would fail its control.
"""

import re
from collections.abc import Iterable
from typing import cast

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import (
    BigInteger,
    Column,
    Index,
    Integer,
    MetaData,
    SmallInteger,
    String,
    Table,
    UniqueConstraint,
    text,
)
from sqlalchemy.sql import visitors
from sqlalchemy.sql.elements import ClauseElement, TextClause

from orchestrator.persistence.models import Base

REVISION_ID_LIMIT = 32  # alembic_version.version_num is varchar(32)

# Unique constraints over a nullable column that deliberately keep NULLs distinct, by name,
# with the reason. Empty today.
NULLABLE_UNIQUE_REASONS: dict[str, str] = {}


# --- run ids ---------------------------------------------------------------------------


def narrow_run_id_columns(metadata: MetaData) -> list[str]:
    """Columns ending `run_id` typed Integer or SmallInteger: GitHub run ids exceed 2^31."""
    return sorted(
        f"{table.name}.{column.name}"
        for table in metadata.tables.values()
        for column in table.columns
        if column.name.endswith("run_id")
        and isinstance(column.type, (Integer, SmallInteger))
        and not isinstance(column.type, BigInteger)
    )


def test_run_id_columns_are_not_narrow_integers() -> None:
    assert narrow_run_id_columns(Base.metadata) == []


def test_run_id_rule_sees_the_real_columns() -> None:
    names = {
        column.name
        for table in Base.metadata.tables.values()
        for column in table.columns
        if column.name.endswith("run_id")
    }
    assert names, "the rule matched no column; its suffix test is wrong"


def test_run_id_control_refuses_an_integer_column() -> None:
    metadata = MetaData()
    Table("bad", metadata, Column("github_run_id", Integer))
    Table("small", metadata, Column("failed_run_id", SmallInteger))
    Table("good", metadata, Column("a_run_id", BigInteger), Column("b_run_id", String))
    assert narrow_run_id_columns(metadata) == ["bad.github_run_id", "small.failed_run_id"]


# --- nullable UNIQUE -------------------------------------------------------------------


def _expression_columns(table: Table, expression: object) -> list[Column]:
    """Columns an index expression references, including those named only inside `text()`."""
    if isinstance(expression, TextClause):
        words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expression.text))
        return [column for column in table.columns if column.name in words]
    return [
        element
        for element in visitors.iterate(cast(ClauseElement, expression))
        if isinstance(element, Column)
    ]


def _unique_targets(metadata: MetaData) -> Iterable[tuple[str, Table, list[Column], object]]:
    for table in metadata.tables.values():
        for constraint in table.constraints:
            if isinstance(constraint, UniqueConstraint):
                yield (
                    str(constraint.name or f"{table.name}:{[c.name for c in constraint.columns]}"),
                    table,
                    list(constraint.columns),
                    constraint.dialect_options["postgresql"]["nulls_not_distinct"],
                )
        for index in table.indexes:
            if index.unique:
                columns: list[Column] = []
                for expression in index.expressions:
                    columns.extend(_expression_columns(table, expression))
                yield (
                    str(index.name or f"{table.name}:ix"),
                    table,
                    columns,
                    index.dialect_options["postgresql"]["nulls_not_distinct"],
                )


def unguarded_nullable_uniques(metadata: MetaData, reasons: dict[str, str]) -> list[str]:
    """Unique constraints or indexes over a nullable column with neither the NULLS NOT
    DISTINCT flag nor a reasoned entry: NULL rows would all be distinct."""
    return sorted(
        name
        for name, _table, columns, nulls_not_distinct in _unique_targets(metadata)
        if any(column.nullable for column in columns)
        and nulls_not_distinct is not True
        and name not in reasons
    )


def test_nullable_unique_constraints_say_how_they_treat_null() -> None:
    assert unguarded_nullable_uniques(Base.metadata, NULLABLE_UNIQUE_REASONS) == []


def test_nullable_unique_rule_sees_the_real_uniques() -> None:
    names = {name for name, *_ in _unique_targets(Base.metadata)}
    assert "uq_release_artifact_source_tuple" in names
    assert "uq_sds_members_repository_lower" in names
    columns = {
        name: [c.name for c in cols] for name, _t, cols, _flag in _unique_targets(Base.metadata)
    }
    assert columns["uq_sds_members_repository_lower"] == ["repository"]


def _control_metadata(*, flagged: bool) -> MetaData:
    metadata = MetaData()
    flag: bool | None = True if flagged else None
    table = Table(
        "ctl",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("maybe", String, nullable=True),
        Column("sure", String, nullable=False),
    )
    table.append_constraint(
        UniqueConstraint("maybe", "sure", name="uq_ctl", postgresql_nulls_not_distinct=flag)
    )
    Index("uq_ctl_ix", table.c.maybe, unique=True, postgresql_nulls_not_distinct=flag)
    return metadata


def test_nullable_unique_control_refuses_an_unflagged_constraint_and_index() -> None:
    assert unguarded_nullable_uniques(_control_metadata(flagged=False), {}) == [
        "uq_ctl",
        "uq_ctl_ix",
    ]
    assert unguarded_nullable_uniques(_control_metadata(flagged=True), {}) == []
    assert (
        unguarded_nullable_uniques(
            _control_metadata(flagged=False), {"uq_ctl": "r", "uq_ctl_ix": "r"}
        )
        == []
    )


def test_nullable_unique_control_resolves_a_text_expression_index() -> None:
    metadata = MetaData()
    table = Table("expr", metadata, Column("name", String, nullable=True))
    Index("uq_expr_lower", text("lower(name)"), unique=True, _table=table)
    assert unguarded_nullable_uniques(metadata, {}) == ["uq_expr_lower"]


def test_nullable_unique_control_catches_column_level_unique() -> None:
    metadata = MetaData()
    Table("col", metadata, Column("code", String, nullable=True, unique=True))
    assert unguarded_nullable_uniques(metadata, {}) == ["col:['code']"]


# --- revision ids ----------------------------------------------------------------------


def overlong_revision_ids(revision_ids: Iterable[str]) -> list[str]:
    return sorted(rid for rid in revision_ids if len(rid) > REVISION_ID_LIMIT)


def test_revision_ids_fit_alembic_version_num() -> None:
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    revisions = [revision.revision for revision in script.walk_revisions()]
    assert revisions, "no migrations were read"
    assert overlong_revision_ids(revisions) == []


def test_revision_id_control_refuses_a_33_character_id() -> None:
    assert overlong_revision_ids(["a" * 32, "b" * 33]) == ["b" * 33]
