import re
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url

from orchestrator import db
from tests._support.database import DEFAULT_ZONE_BENEATH_THE_PIN, TEST_DATABASE_URL

SRC = Path(__file__).resolve().parents[1] / "src"
ENGINE_BUILDERS = re.compile(r"\b(create_engine|engine_from_config)\(")


def test_postgresql_fixture_uses_postgresql(db_session):
    dialect = db_session.bind.dialect.name
    assert dialect == "postgresql"
    assert db_session.scalar(text("select 1")) == 1


def test_the_production_engine_runs_every_session_in_utc(db_session) -> None:
    # `db_session` creates this worker's database; the production engine must be pointed at it.
    assert db.engine.url.database == make_url(TEST_DATABASE_URL).database
    with db.session_factory() as session:
        # Without the pin a session takes the database's DST-observing default, on any server.
        database_defaults = session.scalar(
            text(
                "SELECT setconfig FROM pg_db_role_setting WHERE setrole = 0 AND setdatabase = "
                "(SELECT oid FROM pg_database WHERE datname = current_database())"
            )
        )
        assert f"TimeZone={DEFAULT_ZONE_BENEATH_THE_PIN}" in (database_defaults or [])
        assert session.scalar(text("SHOW TimeZone")) == "UTC"


def test_the_engine_factory_keeps_a_callers_connect_options(db_session) -> None:
    engine = db.make_engine(
        TEST_DATABASE_URL, connect_args={"options": "-c statement_timeout=12345"}
    )
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SHOW TimeZone")) == "UTC"
            assert connection.scalar(text("SHOW statement_timeout")) == "12345ms"
    finally:
        engine.dispose()


def engine_builders(src: Path) -> list[str]:
    """Every `src/` line that calls an engine builder."""
    return [
        f"{path.relative_to(src)}:{number}"
        for path in sorted(src.rglob("*.py"))
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if ENGINE_BUILDERS.search(line)
    ]


def test_every_engine_in_src_is_built_by_the_utc_factory() -> None:
    builders = engine_builders(SRC)
    assert len(builders) == 1, builders
    assert builders[0].startswith("orchestrator/db.py:")


def test_control_every_engine_builder_call_is_reported(tmp_path: Path) -> None:
    (tmp_path / "orchestrator").mkdir()
    (tmp_path / "orchestrator" / "db.py").write_text(
        "from sqlalchemy import create_engine\n"
        "engine = create_engine(url)\n"
        "other = create_engine(url)\n"
    )
    (tmp_path / "lane.py").write_text("engine = sqlalchemy.engine_from_config(config)\n")
    assert engine_builders(tmp_path) == [
        "lane.py:1",
        "orchestrator/db.py:2",
        "orchestrator/db.py:3",
    ]
