from alembic import command
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from tests.conftest import TEST_DATABASE_URL
from tests.persistence.conftest import alembic_config


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
