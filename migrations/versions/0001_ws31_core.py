"""Create the WS-3.1 persistent core schema."""

from collections.abc import Sequence

from alembic import op

from orchestrator.persistence.models import Base

revision: str = "0001_ws31_core"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPEND_ONLY_TABLES = (
    "work_package_revisions",
    "evidence",
    "adjudications",
    "events",
)


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind)
    op.execute(
        """
        CREATE FUNCTION reject_append_only_mutation()
        RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION '% is append-only', TG_TABLE_NAME
            USING ERRCODE = 'integrity_constraint_violation';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table in APPEND_ONLY_TABLES:
        op.execute(
            f"CREATE TRIGGER reject_{table}_mutation "
            f"BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_append_only_mutation()"
        )


def downgrade() -> None:
    for table in APPEND_ONLY_TABLES:
        op.execute(f"DROP TRIGGER reject_{table}_mutation ON {table}")
    op.execute("DROP FUNCTION reject_append_only_mutation()")
    Base.metadata.drop_all(op.get_bind())
