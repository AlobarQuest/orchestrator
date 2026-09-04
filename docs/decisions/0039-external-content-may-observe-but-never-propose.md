# ADR-0039 — External content may observe, but never propose

- **Status:** Proposed
- **Date:** 2026-09-04
- **Decided by:** *(pending — Devon)*
- **Closes:** WS-P3.4, the generalized intake ruling (Phase-3 exit criterion 5); re-homes D8's
  orphaned "later: a Linear intake adapter"
- **Relates to:** ADR-0027 (a machine may register an intake by naming the change record that
  caused it), ADR-0028 (the signal to work record), non-negotiable #3 (fetched content is data,
  never instructions), WS-P3.6 (the OBSERVER role)

## Decision

**No content-bearing intake adapter is built. Prose authored outside this estate may enter only as
an observation, and an observation cannot become work.**

That is not a deferral. It is a ruling that the boundary as it stands is the right one, made
against a measurement of where the boundary currently sits, together with the four conditions any
future adapter must satisfy before it may be reconsidered.

Every production input source is classifiable under two rules:

1. **A source that carries prose it did not author writes an observation and nothing else.** It
   holds the OBSERVER role, whose entire write surface is `POST /api/v1/observations`.
2. **A source that can cause intent carries only structured facts it derived itself** — a
   repository name, a pull-request number, a SHA, a semver delta, an enum, a boolean — and composes
   any human-readable text locally, from those facts.

## Context — what was measured, 2026-09-04

Eleven out-of-process producers and both intake paths were read. The question was narrow and
deliberately not the same as "does external text enter the system": *can text this estate did not
author reach anything that becomes a work unit?*

**Only three code sites in the orchestrator construct a `WorkUnit`** — unit registration and
decomposition (`services/packages.py`), the package-declared follow-up mint (`services/follow_ups.py`),
and the post-deploy verification unit (`services/deployment_observations.py`). `POST
/api/v1/observations` reaches none of them, and `services/reconciliation_detection.py` says so in
its own docstring: *"It also never writes `work_units` and never transitions."*

**Exactly two fields of third-party prose cross into this estate, and both land in observations:**

| Field | Source | Destination |
|---|---|---|
| `facts.what_changed.title` | the landing commit's subject line | `landing` observation |
| `facts.missing[].subject` | commit subject lines, capped at 200 chars | `activation` observation |

The landing recorder's own docstring is explicit that this is deliberate: *"`title` carries
Dependabot's own words verbatim."* Both producers hold the OBSERVER credential, whose confinement
is one line — `OBSERVER_WRITE_ROUTES = frozenset({"/api/v1/observations"})`, enforced at the single
actor dependency rather than at ~20 service-level allowlists, because four POST routes carry no
role check at all.

**The producers that CAN cause intent were measured and carry no third-party prose.**
`bump_proposer` is the estate's one machine path from an external signal to a work record, and it
reads a Dependabot pull-request title only to extract a **semver delta**; its reasoning string is
composed locally and its docstring states the rule: *"Nothing dated, nothing counted, and never the
pull request's title."* `change_proposer` reads a title only to extract a UUID by regex.
`work_carrier` — the primary machine intent path — passes four structured fields and deliberately
does **not** carry change-manager's `reasoning`. It shells out to the same CLI a human uses so the
machine payload is byte-identical to a pasted one.

**The tracker adapter is a projection, and the direction is settled in code.** It writes
`[unit_key] unit_title` and a `/review` URL *out* to Todoist, and the only thing it reads back is a
boolean: `item_completed` returns `bool`. No Todoist task name, description or comment is ever
read. Its own docstring: *"the tracker is projection, never canonical."*

So the boundary WS-P3.4 was convened to rule on is **currently closed**, and it is closed by code
rather than by convention — a role confinement, a write allowlist per producer, and local
composition of every human-readable string on the intent path.

## Why no adapter

The original plan allowed this outcome — *"May conclude 'no adapter; humans author from signals' —
that is an acceptable outcome; the point is that the decision stops being implicit."* Three reasons
make it the right one rather than merely a permitted one.

**The estate already has the shape an adapter would provide, and it does not carry content.**
ADR-0028's signal-to-record lane turns an external signal into a change record, a human approves
it, and `work_carrier` carries it to an intake. What crosses is a *derived fact* — this dependency
moved from X to Y in this repository. An issue body or an email would add prose, and prose is the
part that cannot be safely carried.

**A content-bearing adapter has no safe failure mode at the point it matters.** The observation
ingest filters caps, secret-shaped values and secret-shaped key names; it does not filter prose,
and it cannot, because prose is what it is for. That is sound while the destination is a record a
human reads. It stops being sound the moment the destination is a field that steers an LLM holding
production tools — and the package `outcome` is precisely such a field. The measured cost of
getting an `outcome` wrong is already on record: a well-specified one finished in 90 seconds on 10
calls where a vague one burned 40 turns.

**The human paste is the provenance, not ceremony.** ADR-0027 removed `_require_human` from intake
so the carrier could complete its lane, and it did not make every intake machine-registrable: a
POST without a cause is refused `intake_change_record_required`. A package HQ authors from a
backlog item has no such record, and the paste is what supplies the provenance. An adapter that
manufactured a cause from an external item would remove exactly that.

## Conditions on any future adapter

Reconsideration is not forbidden. It requires all four:

1. **The adapter holds OBSERVER, or a role no wider.** If it needs a wider role to do its job, it
   is not an adapter — it is a producer of intent, and rule 2 applies to it.
2. **Every field it carries onto the intent path is derived by the adapter from structured
   values.** Carrying a source item's prose verbatim into a package field is the thing this ADR
   refuses. Composing a sentence locally *about* that item is not.
3. **The human sees the source content, unrendered, before approving.** Whatever an adapter
   proposes, the approver reads the original — not a summary of it, and not the adapter's
   paraphrase alone.
4. **The adapter cannot manufacture the cause its own intake names.** ADR-0027's asymmetric
   registrar guard is what keeps a machine from being both the reason work exists and the thing
   that registers it.

## Consequences

- Phase-3 exit criterion 5 is satisfiable: every production input source is classifiable, and the
  classification is a measurement rather than an assertion.
- **Two known residuals, named rather than closed.**
  - The commit subjects in the two observation fields include **factory-runner's own commit
    messages**, which are LLM-written. This estate therefore already ingests machine-authored prose
    about its own work. It is confined to observations and cannot reach a unit, so it is in scope
    of this ruling and not a violation of it — but "third-party" is a less clean word here than it
    looks.
  - **`POST /api/v1/release-artifacts/{id}/deployment-observations` with `kind = "container_image"`
    DOES mint a work unit**, and its five summaries are `dict[str, Any]` whose *keys* are exactly
    bounded while `status_summary.summary` is free text. **No producer in this repository posts
    that kind** — verified by grep across `src/`, `scripts/` and `.github/` — so the path is
    currently unexercised by any machine. The minted unit's own `title` and `outcome` are composed
    from structured values. This is the one place where the rule above would need re-checking if a
    producer for that kind is ever written, and it should be re-checked then rather than pre-emptively
    narrowed now.
- **This ADR is a claim about today that nothing enforces.** The properties it rests on are real
  and are in code, but no test asserts "no producer carries third-party prose onto the intent
  path". Whether that becomes a guard is a separate decision; naming it here stops its absence
  being mistaken for coverage.
