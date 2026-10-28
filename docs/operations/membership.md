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
