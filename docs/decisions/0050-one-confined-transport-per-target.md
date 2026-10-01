# ADR 0050 — The out-of-process programs share one confined transport

**Date:** 2026-10-01
**Status:** Accepted (Devon, Tier 3 item 26, 2026-09-28: "after Tier 2, one PR per target")
**Reverses:** the "deliberately COPIED" rule recorded beside `pin_watcher` and `revision_watcher`
in `tests/architecture/test_out_of_process_isolation.py`, for transport plumbing only.

## Context

Thirteen out-of-process programs under `src/` reach the orchestrator, change-manager or GitHub.
Each built its own `httpx.Client` and repeated the same guards around it: refuse a path the program
may not reach before anything is sent; catch all three exception families `httpx` raises for a
request that got no answer (`HTTPError`, `InvalidURL`, and the `ValueError` that IDNA encoding of a
malformed host raises at request time); guard construction as well as the request; and report the
exception's type, never its text, because an `httpx` error carries the request and its bearer
token. The 2026-09-27 debt survey counted 52 copies of the exception tuple in four variants. The
variants are the defect: `landing_ledger` caught `httpx.HTTPError` alone, so a doubled dot in its
base URL ended the pass with a traceback.

The copies were deliberate. Two rows of the isolation table say a lane that reached into a sibling
for plumbing "would let an unrelated refactor break this lane's schedule". That argument is about
borrowing from another *lane*. A package with no lane of its own, that imports no program, has no
schedule to break; changing it is a change to every lane at once and is reviewed as one.

## Decision

`src/estate_clients/confined.py` holds the transport and nothing else:

- `ConfinedClient` takes the program's `permits(method, path)` predicate and its `refuse` exception
  factory. It asks `permits` before every request, so a client cannot send what its program does
  not allow, and the refusal is the program's own exception class.
- `TRANSPORT_ERRORS` is the three families. A failure at construction or request time raises
  `TransportFailure`, which carries the exception's type name and is raised `from None`.
- `base_url_problem`, `error_detail` and `error_code` are the shared readers of a base URL and of a
  refusal body.

Each program keeps its paths, its exception classes, its wording, its status-code handling and its
User-Agent. Nothing in `estate_clients` names a service path or a program.

The guards treat it as follows:

- `HTTP_CLIENTS` includes `estate_clients`, so a module that imports it is speaking HTTP and must be
  in `OUTBOUND_ALLOWLIST` on its own account. Without this the package would be a way past the
  per-file chokepoint.
- Its isolation row is marked `library`, so a lane's sibling ban does not forbid it. A library row
  must itself forbid sibling programs, so shared plumbing cannot become a path from one lane into
  another.
- Its ADR-0039 row is `NOT_AN_ORCHESTRATOR_WRITER`: it writes nothing; the program that passes it a
  path declares that write in its own row.

Migration is one pull request per target: the orchestrator clients, then the change-manager
clients, then the GitHub readers. Each pull request names the sites whose malformed-URL behaviour
changes from a traceback to the program's own error.

## Consequences

- A fix to the transport guards is made once. A defect in them reaches every program at once, which
  is the price of the first property; `tests/estate_clients/` carries the real-transport control
  for the IDNA case, which a mock transport cannot exercise.
- Three clients stay outside it: `revision_watcher/estate.py`, `tracker_projection_adapter/tracker.py`
  and `deploy_watcher/transcription_currency.py`.
- `src/orchestrator/cli.py` stays outside it. It is an operator's client to any route, not a
  confined program, and it lives inside the orchestrator package.
- The orchestrator may import a `library` row (decided in the change-manager pull request, the
  first to need it). The isolation table's rule that the orchestrator imports nothing from a
  program exists because an out-of-process reading taken from inside the orchestrator is not
  independent; a library takes no reading, so the rule does not reach it. The in-process
  change-manager readers (`services/landing/change_record.py`,
  `services/landing/inert_landing_policy.py`) use it from that pull request on.
- `user_agent` is a required argument (also from the change-manager pull request). change-manager
  sits behind Cloudflare, which refuses Python's default agent with `error code: 1010`; requiring
  the argument makes that refusal impossible to reintroduce. Clients that sent no User-Agent
  before now name themselves.
- Malformed-URL behaviour that moves in the first pull request: `tracker_projection_adapter`'s
  client caught no transport error at all, so a typo in its base URL ended the run with a traceback
  and now raises its own `ProjectionError`; `landing_ledger`'s caught only `httpx.HTTPError`, so a
  malformed host escaped as a bare `ValueError`, which its CLI already absorbed, and now arrives as
  its own `LedgerWriteError` with the exception type named.
