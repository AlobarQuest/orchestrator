from alembic import command
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from tests.conftest import TEST_DATABASE_URL
from tests.persistence.conftest import alembic_config


def column_default(engine, table: str, column: str) -> str | None:
    return next(
        item["default"] for item in inspect(engine).get_columns(table) if item["name"] == column
    )


def test_alembic_upgrades_empty_database() -> None:
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))

    config = alembic_config()
    command.upgrade(config, "head")

    with engine.connect() as connection:
        current_revision = MigrationContext.configure(connection).get_current_revision()
    head_revision = ScriptDirectory.from_config(config).get_current_head()
    engine.dispose()

    assert current_revision == head_revision


def test_alembic_downgrade_removes_schema_and_can_reupgrade(migrated_engine) -> None:
    config = alembic_config()

    command.downgrade(config, "base")

    inspector = inspect(migrated_engine)
    assert not {
        "work_packages",
        "work_package_revisions",
        "work_units",
        "dependencies",
        "claims",
        "approvals",
        "evidence",
        "adjudications",
        "events",
    }.intersection(inspector.get_table_names())
    with migrated_engine.connect() as connection:
        remaining_functions = connection.scalars(
            text(
                "SELECT proname FROM pg_proc WHERE proname IN "
                "('reject_append_only_mutation', 'enforce_work_unit_revision_immutable', "
                "'set_work_unit_updated_at')"
            )
        ).all()
    assert remaining_functions == []

    command.upgrade(config, "head")

    assert set(inspect(migrated_engine).get_table_names()) >= {
        "work_packages",
        "work_package_revisions",
        "work_units",
        "dependencies",
        "claims",
        "approvals",
        "evidence",
        "adjudications",
        "events",
    }


def test_default_attempt_budget_migration_is_reversible(migrated_engine) -> None:
    config = alembic_config()

    assert column_default(migrated_engine, "work_units", "max_attempts") == "3"

    command.downgrade(config, "0001_ws31_core")
    assert column_default(migrated_engine, "work_units", "max_attempts") == "1"

    command.upgrade(config, "head")
    assert column_default(migrated_engine, "work_units", "max_attempts") == "3"


def test_ws32_tables_exist_after_upgrade(migrated_session) -> None:
    tables = {
        row[0]
        for row in migrated_session.execute(
            text("select tablename from pg_tables where schemaname = 'public'")
        )
    }
    assert "package_acceptance_criteria" in tables
    assert "decomposition_proposals" in tables
    assert "decomposition_proposal_units" in tables
    assert "decomposition_proposal_dependencies" in tables
    assert "decomposition_proposal_ac_mappings" in tables
    assert "decomposition_proposal_retained_acs" in tables
    assert "approved_decompositions" in tables
