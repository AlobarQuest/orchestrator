"""Give a deployment observation a KIND, so a machine-local activation can be recorded as one.

ADR-0030 reused `release_artifact_bindings` for a machine-local artifact and left the sixth
traceability hop -- `deployment` -- empty, on the argument that no honest check existed. That
argument was wrong: the sweep already fetches from GitHub and compares the working copy against
`origin/main`, and the binding lane already proves the landing commit is an ancestor of `HEAD`.
Two further facts are measurable on the machine and nowhere else -- every declared console entry
point is installed, and the environment matches the lockfile -- and the second of those is the
one with a recorded failure: the launchers invoke by absolute path, a `git pull` alone does not
install a new `[project.scripts]` entry, and the job then dies at a missing binary.

WHAT THE TABLE COULD NOT HOLD. It validated five summaries unconditionally, each requiring
non-empty content, and every one describes probing a URL: probe, route, auth, dispatch, status.
A working copy has no endpoint, no route table and no 401 to observe, so recording one would have
meant inventing all five. The shapes become conditional on `kind` instead -- the same answer the
sibling table reached one migration ago, for the same reason, rather than a second mechanism.

NULLS NOT DISTINCT IS DELIBERATELY ABSENT, and saying so matters because the sibling migration
needed it. That flag was required there because three columns OF THE UNIQUE TUPLE became
nullable. Here the two unique constraints are `idempotency_key` and
`(release_artifact_binding_id, environment)`, and no column of either becomes nullable under
either kind -- so both keep deduplicating exactly as before. Copying the flag anyway would have
been a mechanism transplanted without its reason.

THE ENVIRONMENT IS PINNED IN THE DATABASE for a machine-local row. `environment` is otherwise
free-form under a regex, so one mistaken payload naming `production` would put a working copy
into the answer for "what is serving production" -- the collapse the discriminator exists to
prevent, arriving through the one column the discriminator does not cover.

BACKWARD-COMPATIBLE WITH THE RUNNING IMAGE, which is what makes the standing migrate-first rule
safe. An INSERT from the old code omits `kind` and `activation_summary`; the server defaults fill
`container_image` and `{}`, and that row's non-empty URL columns and its post-deploy unit satisfy
the conditional CHECK. So the window between this migration and the image swap holds no shape the
old code can produce and the new database would refuse.

The downgrade restores the original constraints and FAILS rather than deleting rows if any
machine-local observation exists.
"""

from alembic import op

revision = "0031_adr30_activation_obs"
down_revision = "0030_adr30_binding_kind"
branch_labels = None
depends_on = None

# A FROZEN COPY of the model constants rather than an import: a migration describes the database
# at one point in history, and importing the live constant would make this file silently
# re-describe itself the next time a member is added.
_CONTAINER = "container_image"
_MACHINE_LOCAL = "machine_local"
_KINDS = (_CONTAINER, _MACHINE_LOCAL)
_OPERATOR_MACHINE = "operator_machine"

_TABLE = "deployment_observations"
_REQUIRED_TEXT = "ck_deployment_observations_required_text"
_KIND_CHECK = "ck_deployment_observations_kind"
_BY_KIND = "ck_deployment_observations_by_kind"

_NULLABLE = (
    "base_url",
    "deployment_url",
    "deployer",
    "post_deploy_work_unit_id",
    "post_deploy_event_id",
)
_HOSTED_SUMMARIES = (
    "probe_summary",
    "route_summary",
    "auth_summary",
    "dispatch_summary",
    "status_summary",
)

_OLD_REQUIRED_TEXT = (
    "package_revision_hash <> '' AND environment <> '' AND base_url <> '' "
    "AND observed_artifact_digest <> '' AND deployment_ref <> '' "
    "AND deployment_url <> '' AND deployer <> ''"
)
_NEW_REQUIRED_TEXT = (
    "package_revision_hash <> '' AND environment <> '' "
    "AND observed_artifact_digest <> '' AND deployment_ref <> ''"
)


def _members() -> str:
    return ", ".join(f"'{kind}'" for kind in _KINDS)


def _hosted_empty() -> str:
    return " AND ".join(f"{column} = '{{}}'::jsonb" for column in _HOSTED_SUMMARIES)


def _by_kind() -> str:
    return (
        f"(kind = '{_CONTAINER}' AND base_url <> '' "
        "AND deployment_url <> '' AND deployer <> '' "
        "AND post_deploy_work_unit_id IS NOT NULL AND post_deploy_event_id IS NOT NULL "
        "AND activation_summary = '{}'::jsonb) "
        f"OR (kind = '{_MACHINE_LOCAL}' AND environment = '{_OPERATOR_MACHINE}' "
        "AND base_url IS NULL AND deployment_url IS NULL AND deployer IS NULL "
        "AND post_deploy_work_unit_id IS NULL AND post_deploy_event_id IS NULL "
        "AND activation_summary <> '{}'::jsonb "
        f"AND {_hosted_empty()})"
    )


def upgrade() -> None:
    op.execute(f"ALTER TABLE {_TABLE} ADD COLUMN kind VARCHAR NOT NULL DEFAULT '{_CONTAINER}'")
    op.execute(
        f"ALTER TABLE {_TABLE} ADD COLUMN activation_summary JSONB NOT NULL DEFAULT '{{}}'::jsonb"
    )
    op.create_check_constraint(_KIND_CHECK, _TABLE, f"kind IN ({_members()})")
    for column in _NULLABLE:
        op.execute(f"ALTER TABLE {_TABLE} ALTER COLUMN {column} DROP NOT NULL")
    op.drop_constraint(_REQUIRED_TEXT, _TABLE, type_="check")
    op.create_check_constraint(_REQUIRED_TEXT, _TABLE, _NEW_REQUIRED_TEXT)
    op.create_check_constraint(_BY_KIND, _TABLE, _by_kind())


def downgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM {_TABLE} WHERE kind = '{_MACHINE_LOCAL}') THEN
                RAISE EXCEPTION
                    'machine-local deployment observations exist; narrowing this table would '
                    'either destroy them or leave it describing something untrue';
            END IF;
        END $$;
        """
    )
    op.drop_constraint(_BY_KIND, _TABLE, type_="check")
    op.drop_constraint(_REQUIRED_TEXT, _TABLE, type_="check")
    op.create_check_constraint(_REQUIRED_TEXT, _TABLE, _OLD_REQUIRED_TEXT)
    for column in _NULLABLE:
        op.execute(f"ALTER TABLE {_TABLE} ALTER COLUMN {column} SET NOT NULL")
    op.drop_constraint(_KIND_CHECK, _TABLE, type_="check")
    op.execute(f"ALTER TABLE {_TABLE} DROP COLUMN activation_summary")
    op.execute(f"ALTER TABLE {_TABLE} DROP COLUMN kind")
