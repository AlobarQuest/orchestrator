"""Give a release artifact binding a KIND, and make the registry columns conditional on it.

ADR-0030 ruled that a machine-local activation is a release artifact and reused this table rather
than adding a second one, so that every traceability hop lights up without learning a new source.
The table was shaped for one model, and its CHECK required a registry, a registry repository and
an image name that a working copy on the operator machine does not have. Writing "local" into
those three because the CHECK insisted would make the two models indistinguishable in exactly the
columns a reader would separate them by, so they become conditional instead.

`artifact_digest` is untouched and still required for both kinds. A machine-local binding supplies
a real content digest over real content, so the service validator that refuses a bare commit sha
keeps its invariant literally true rather than being relaxed for a second case.

NULLS NOT DISTINCT ON THE SOURCE TUPLE IS PART OF THE SAME EDIT, not an improvement bolted on.
Postgres treats NULLs in a unique constraint as distinct, so making three of its eight columns
nullable would silently stop it deduplicating every machine-local row -- an existing guarantee
weakened by a migration whose subject was something else. Requires Postgres 15 or later.

BACKWARD-COMPATIBLE WITH THE RUNNING IMAGE, which is what makes the standing migrate-first rule
safe here. An INSERT from the old code omits `kind`; the server default fills `container_image`,
and that row's non-empty registry columns satisfy the conditional CHECK. So the window between
this migration and the image swap holds no shape the old code can produce and the new database
would refuse.

The downgrade restores the original constraints and FAILS rather than deleting rows if any
machine-local binding exists -- narrowing a constraint under live rows would either destroy them
or leave the database describing something untrue.
"""

from alembic import op

revision = "0030_adr30_binding_kind"
down_revision = "0029_adr30_activation"
branch_labels = None
depends_on = None

# A FROZEN COPY of the model constants rather than an import: a migration describes the database
# at one point in history, and importing the live constant would make this file silently
# re-describe itself the next time a member is added.
_CONTAINER = "container_image"
_MACHINE_LOCAL = "machine_local"
_KINDS = (_CONTAINER, _MACHINE_LOCAL)

_TABLE = "release_artifact_bindings"
_SOURCE_TUPLE = (
    "work_package_revision_id",
    "work_unit_id",
    "source_repository",
    "merge_commit",
    "source_commit",
    "artifact_registry",
    "artifact_repository",
    "artifact_name",
)
_TUPLE_NAME = "uq_release_artifact_source_tuple"
_REQUIRED_TEXT = "ck_release_artifact_required_text"
_REGISTRY_BY_KIND = "ck_release_artifact_registry_by_kind"
_KIND_CHECK = "ck_release_artifact_kind"

_OLD_REQUIRED_TEXT = (
    "package_revision_hash <> '' AND source_repository <> '' "
    "AND source_commit <> '' AND merge_commit <> '' "
    "AND artifact_registry <> '' AND artifact_repository <> '' "
    "AND artifact_name <> '' AND artifact_digest <> ''"
)
_NEW_REQUIRED_TEXT = (
    "package_revision_hash <> '' AND source_repository <> '' "
    "AND source_commit <> '' AND merge_commit <> '' AND artifact_digest <> ''"
)
_REGISTRY_CONDITION = (
    f"(kind = '{_CONTAINER}' AND artifact_registry <> '' "
    "AND artifact_repository <> '' AND artifact_name <> '') "
    f"OR (kind = '{_MACHINE_LOCAL}' AND artifact_registry IS NULL "
    "AND artifact_repository IS NULL AND artifact_name IS NULL)"
)


def _columns() -> str:
    return ", ".join(_SOURCE_TUPLE)


def _members() -> str:
    return ", ".join(f"'{kind}'" for kind in _KINDS)


def upgrade() -> None:
    op.execute(f"ALTER TABLE {_TABLE} ADD COLUMN kind VARCHAR NOT NULL DEFAULT '{_CONTAINER}'")
    op.create_check_constraint(_KIND_CHECK, _TABLE, f"kind IN ({_members()})")
    for column in ("artifact_registry", "artifact_repository", "artifact_name"):
        op.execute(f"ALTER TABLE {_TABLE} ALTER COLUMN {column} DROP NOT NULL")
    op.drop_constraint(_REQUIRED_TEXT, _TABLE, type_="check")
    op.create_check_constraint(_REQUIRED_TEXT, _TABLE, _NEW_REQUIRED_TEXT)
    op.create_check_constraint(_REGISTRY_BY_KIND, _TABLE, _REGISTRY_CONDITION)
    op.drop_constraint(_TUPLE_NAME, _TABLE, type_="unique")
    op.execute(
        f"ALTER TABLE {_TABLE} ADD CONSTRAINT {_TUPLE_NAME} "
        f"UNIQUE NULLS NOT DISTINCT ({_columns()})"
    )


def downgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM {_TABLE} WHERE kind = '{_MACHINE_LOCAL}') THEN
                RAISE EXCEPTION
                    'machine-local release artifact bindings exist; narrowing this table would '
                    'either destroy them or leave it describing something untrue';
            END IF;
        END $$;
        """
    )
    op.drop_constraint(_TUPLE_NAME, _TABLE, type_="unique")
    op.execute(f"ALTER TABLE {_TABLE} ADD CONSTRAINT {_TUPLE_NAME} UNIQUE ({_columns()})")
    op.drop_constraint(_REGISTRY_BY_KIND, _TABLE, type_="check")
    op.drop_constraint(_REQUIRED_TEXT, _TABLE, type_="check")
    op.create_check_constraint(_REQUIRED_TEXT, _TABLE, _OLD_REQUIRED_TEXT)
    for column in ("artifact_registry", "artifact_repository", "artifact_name"):
        op.execute(f"ALTER TABLE {_TABLE} ALTER COLUMN {column} SET NOT NULL")
    op.drop_constraint(_KIND_CHECK, _TABLE, type_="check")
    op.execute(f"ALTER TABLE {_TABLE} DROP COLUMN kind")
