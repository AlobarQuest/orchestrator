"""The model's `IN (...)` CHECKs render the text the migrations wrote, and Postgres agrees.

`f"col IN {VOCAB!r}"` renders a one-element tuple with a trailing comma, which is not what the
migration wrote (`ck_work_package_revisions_verification_mode`). Two tests hold this: the
rendered text equals a fixture, and each constraint, added to a scratch copy of its table, is
stored by Postgres exactly as the migrated one is.
"""

import json
import re
from pathlib import Path

import pytest
from sqlalchemy import CheckConstraint, Engine, text

from orchestrator.persistence.models import Base

FIXTURE = Path(__file__).parent.parent / "fixtures" / "model_in_checks.json"


def _model_in_checks() -> list[tuple[str, str, str]]:
    """(table name, constraint name, rendered text) for every model `IN (` CHECK."""
    # `table.constraints` is a set, so sort: pytest-xdist needs every worker to collect the same
    # parameters in the same order.
    return sorted(
        (table.name, str(constraint.name), str(constraint.sqltext))
        for table in Base.metadata.tables.values()
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint) and " IN (" in str(constraint.sqltext)
    )


def _member_order_ignored(definition: str) -> str:
    """Sort the members of the `ARRAY[...]` list Postgres prints, so only membership is compared.

    Two observation vocabularies list the same members in a different order from the migration
    that created them; the order has no effect on what the CHECK accepts. Any `::<type>` cast is
    accepted, and an ARRAY whose members cannot all be parsed raises rather than comparing empty.
    """

    def sort_members(match: re.Match[str]) -> str:
        body = match.group(1)
        members = re.findall(r"'[^']*'::[\w ]+", body)
        if not members or len(members) != body.count("'") // 2:
            raise AssertionError(f"cannot parse the members of ARRAY[{body}]")
        return "ARRAY[" + ", ".join(sorted(members)) + "]"

    return re.sub(r"ARRAY\[(.*?)\]", sort_members, definition)


def test_the_member_sorter_refuses_an_array_it_cannot_parse() -> None:
    with pytest.raises(AssertionError):
        _member_order_ignored("CHECK ((x = ANY (ARRAY[1, 2])))")


def test_the_member_sorter_still_tells_two_text_member_lists_apart() -> None:
    first = "CHECK ((x = ANY (ARRAY['a'::text, 'b'::text])))"
    second = "CHECK ((x = ANY (ARRAY['a'::text, 'c'::text])))"
    reordered = "CHECK ((x = ANY (ARRAY['b'::text, 'a'::text])))"
    assert _member_order_ignored(first) != _member_order_ignored(second)
    assert _member_order_ignored(first) == _member_order_ignored(reordered)


def test_the_model_renders_the_in_checks_the_fixture_records() -> None:
    expected = json.loads(FIXTURE.read_text())
    rendered = {name: sql for _, name, sql in _model_in_checks()}
    assert rendered == expected


@pytest.mark.parametrize(
    ("table", "name", "sql"), _model_in_checks(), ids=[name for _, name, _ in _model_in_checks()]
)
def test_postgres_stores_the_model_check_as_it_stores_the_migrated_one(
    migrated_engine: Engine, table: str, name: str, sql: str
) -> None:
    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            migrated = connection.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conname = :name AND conrelid = CAST(:table AS regclass)"
                ),
                {"name": name, "table": table},
            ).scalar_one()
            connection.execute(text(f'CREATE TEMP TABLE tmp_x (LIKE "{table}")'))
            connection.execute(text(f'ALTER TABLE tmp_x ADD CONSTRAINT "{name}" CHECK ({sql})'))
            modelled = connection.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conname = :name AND conrelid = CAST('tmp_x' AS regclass)"
                ),
                {"name": name},
            ).scalar_one()
        finally:
            transaction.rollback()
    assert _member_order_ignored(modelled) == _member_order_ignored(migrated)
