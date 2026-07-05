from sqlalchemy import text


def test_postgresql_fixture_uses_postgresql(db_session):
    dialect = db_session.bind.dialect.name
    assert dialect == "postgresql"
    assert db_session.scalar(text("select 1")) == 1
