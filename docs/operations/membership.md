# SDS membership

Which repositories the SDS maintains, and which of seven services each has been granted. Design:
`docs/superpowers/specs/2026-10-26-sds-membership-design.md`; decision: ADR-0058.

Membership is explicit. No lane discovers a repository and enrols it: the member tables change only
when a person confirms a staged proposal on `/review`.

## The services

The wire value is what the API, the database and the code use. The label is for people.

| Wire value | Label |
|---|---|
| `dependency_updates` | Dependency updates |
| `security_patches` | Security patches |
| `app_changes` | App changes |
| `backups` | Backups |
| `monitoring` | Monitoring |
| `rollouts` | Deploys |
| `credential_rotation` | Credential rotation |

`rollouts` is the Deploys service. The word guards (ADR-0051) refuse the other spelling in code under
`src/orchestrator/`, so "Deploys" appears only in the review template and in documents.

`change-proposer` selects the repositories granted `rollouts`, narrowed to those whose rollout
workflow and rollback plan are transcribed; it reads the record once per pass and exits 2 when it
cannot (`scripts/run-change-proposer.sh` fetches the observer bearer on every run, dry or acting).

Grant states: `not_assessed`, `remediating`, `granted`, `not_applicable`, `withdrawn`. Member states:
`onboarding`, `member`, `retired`. A repository is keyed `owner/name` as GitHub spells it, matched
case-insensitively.

## Add a member ("now add this one")

1. Write a TOML file naming the repository, its owner, its state (`onboarding` for a new one), the
   reason, and any apps and grants.
2. Stage it with `sds-members stage <file.toml>`. It posts `POST /api/v1/member-proposals` as SYSTEM
   and prints the `/review/member-proposals/{id}` page. (The `sds-members` command ships with the
   lane changes; until then post the same body, with `idempotency_key` and `expected_version: 0` added, to the API with the SYSTEM bearer, see
   `docs/operations/credentials.md`.)
3. Open the printed page and read each change beside the record it would change. Confirm, or
   withdraw with a reason. Staging changes no member.

## Change a grant

The same way: a proposal whose change names the repository and the service under `grants` with the
new state and a reason. Setting any state is a person's act, so a grant never moves without a
confirmed proposal. A proposal that sets `seeded = true` marks its grants as recording what already
runs rather than a fresh assessment.

## Hold a dependency

A hold is a version ceiling on one dependency of one member: "orchestrator's sqlalchemy stays at or
below 2.0.54". It is keyed by ecosystem, directory and dependency, as Dependabot identifies an
update, and staged like any other change: a `holds` list on the member's change, each entry a
`place` (with a `ceiling`) or a `lift` (with none). The TOML shape is in `src/sds_members/cli.py`.

- The ecosystem is Dependabot's spelling (`npm_and_yarn`, `pip`, `uv`, `docker`,
  `github_actions`). The tooling spellings `npm`, `yarn`, `pnpm` and `github-actions` are refused,
  since a hold typed with one would match no update.
- The ecosystem must be lowercase. No field may have leading or trailing whitespace: staging refuses
  it rather than trimming, so the stored hold is exactly what was confirmed.
- The directory defaults to `/`; any other must start with `/` and not end with one.
- A place needs a ceiling whose release can be read (`2.0.54`, `v3.23.8`; not `latest`, `^1.2` or
  a wildcard such as `1.x` or `1.2.*`), and replaces the ceiling of an existing hold on the same
  key.
- A lift needs an existing hold and deletes it on confirm.
- The hold's reason is the change's `reason`; who set it is the person who confirmed.
- **The ecosystem must be the one Dependabot uses for that repository's manifest**, since that is
  the spelling every path compares. A repository managed by uv gets `uv` updates, so a `pip` hold
  there covers nothing; check the ecosystem in the branch name of a Dependabot pull request
  (`dependabot/uv/...`) or in the repository's `.github/dependabot.yml`.
- **Equivalent spellings of one dependency stage as separate holds.** `Flask_Cors` and `flask-cors`
  are one pip package, and a hold under either applies to it (the matcher compares names by the
  registry's rule), but they are stored as typed: two places make two holds, both of which apply,
  and a lift must name the exact spelling that was staged. Read the member's `holds` before
  lifting.

For example, holding sqlalchemy and lifting an older hold in one change:

```toml
summary = "hold sqlalchemy, lift httpx"

[[changes]]
repository = "AlobarQuest/orchestrator"
reason = "2.0.55 broke the build"   # becomes the hold's reason

[[changes.holds]]
action = "place"
ecosystem = "uv"
directory = "/"                     # optional; "/" when left out
dependency = "sqlalchemy"
ceiling = "2.0.54"                  # the highest release allowed

[[changes.holds]]
action = "lift"                     # names no ceiling
ecosystem = "uv"
dependency = "httpx"
```

To place or lift one: write the TOML, run `sds-members stage <file.toml>` (it prints the
`/review/member-proposals/{id}` page), and confirm on that page as a person. A lift is staged and
confirmed the same way; nothing else removes a hold, so only a person ends one.

What a hold stops: every path that lands or proposes a dependency bump asks it
(`tests/architecture/test_hold_paths.py` lists each path and how). Both landers refuse a Dependabot
pull request past the ceiling, and an acting lander pass closes it with a comment; the factory
merge refuses a dependency-update unit past it; the bump proposer skips it and the work carrier refuses its record.
For a uv dependency the factory's version is a floor, so on those three factory paths a hold on a
uv dependency stops every update of it, whatever its version, until it is lifted. ADR-0028
amendment 2 has the rule and its residuals.

**A hold covers only an update that names the dependency**: Dependabot's `updated-dependencies`
list, or a factory unit's `profile_fields`. A transitive move through a lockfile is never compared,
so an update of fastapi that moves a held starlette, or a `uv lock` re-resolve, lands whatever the
hold says. Hold the direct dependency that pulls it, or pin it in the manifest.

**Deploy before pulling.** The lanes' member client (`sds_members.client`) requires `holds` on every
member, and a record without it is unreadable, so a lane reading it stops. A release that adds a
field the lanes read is deployed to the orchestrator before the main tree is pulled.

`GET /api/v1/members` returns each member's `holds` (an empty list when it has none). Staged member
proposals are listed first on the `/review` queue.

## Where to confirm

`/review/member-proposals/{id}`, as a signed-in person (HUMAN). The confirm button is bound to the
digest of the record the page showed: if the record moved since, the confirm is refused and the page
shows the new comparison.

## Reading the record

`GET /api/v1/members` returns every member, retired included, with a grant for each of the seven
services. Any authenticated role may read it, OBSERVER included.

Outside the orchestrator, project-standards' nightly scan reads it with the OBSERVER bearer. Its Q2
factory-capability sweep measures the members whose app-changes grant is `granted` or
`remediating`, and an unreadable record fails the run (`portfolio-scan`) instead of measuring
nothing quietly.

## The write split

`tests/architecture/test_membership_writers.py` holds two rules: only `services/membership/proposals.py`
constructs, adds or assigns the member models, and the routes that reach the stage, confirm or
withdraw functions are exactly the three above. Role refusal is pinned by the service and web tests.
