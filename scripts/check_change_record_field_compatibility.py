#!/usr/bin/env python3
"""Refuse a pull request whose change-record vocabulary change-manager cannot accept or serve.

The signal->work contract, clause C7's enforcement. Three of this repository's out-of-process
programs speak to change-manager over HTTP: two PROPOSE records and one READS them back. Both
proposal schemas on the far side are `extra="forbid"`, so a field this repository sends that
change-manager does not declare is an HTTP 422 and the proposal is simply not made -- the whole
reason the contract's increments had to ship change-manager first. In the other direction a key
the carry reads that change-manager does not serve comes back absent, and the carry either
refuses a record it could have acted on or acts on one it could not fully read.

Neither failure is visible here. change-manager is a separate repository on its own release
cadence, its schemas are not importable from this one, and nothing compared the two vocabularies
until this check existed.

WHAT IS COMPARED, AND IN WHICH DIRECTION

  1. the work lane's proposal  -> `WorkChangeIn`      (sent must be declared)
  2. the other lane's proposal -> `DeployChangeIn`    (sent must be declared)
  3. the carry's row parse     -> `_item_dict`        (read must be served)

SUBSET, NEVER EQUALITY. change-manager legitimately declares fields no producer here sends
(`note` is optional on both proposal schemas) and serves keys the carry does not read -- it has
other callers. The failure this check exists to catch is asymmetric: a name on THIS side that the
far side does not know.

THE THIRD COMPARISON READS A DIFFERENT DOCUMENT FROM THE FIRST TWO, and that is the correction
this check made to its own specification. The plan and the design both say to parse `WorkChangeIn`
and `DeployChangeIn` and fail if either fails to declare a field the producers send "or
`work_carrier` reads". That is unsatisfiable: the carry reads a RESPONSE row off `GET /api/items`,
not a request body, and four of the nine keys it reads -- `source`, `status`, `id`, `decided_by` --
exist only on the response. Held to the request models, the reading half would have reported four
divergences on the day it shipped, every one of them false. `_item_dict` is the honest counterpart,
and it costs a second fetch.

WHY NOTHING IS RETYPED HERE. Each of the three local vocabularies is IMPORTED from the program
that owns it -- `PROPOSAL_FIELDS` in each producer, `RECORD_FIELDS` in the carry -- so this script
holds no copy of any field name it compares. The declarations exist for exactly this reason, and
each is pinned by its own suite to the payload or parse it describes, so it cannot drift from the
behaviour it stands for. A list of names retyped into this file would be a second copy of a
vocabulary, which is the defect this repository has now re-learned in four of them, where only the
copies that run get corrected.

Two premises are asserted rather than assumed, both because a silent wrong answer here reads
exactly like a pass:

  - each far-side proposal class inherits `BaseModel` DIRECTLY. The parse reads a class BODY, so
    a field moved to a shared base would be invisible and would surface as a divergence that does
    not exist. (`PackageIntakeRegistration` in this repository is already such a class: its
    `model_fields` carry two names its body never mentions.)
  - `list_items` serialises through `_item_dict`. The carry reads what that route returns, so a
    serializer swap on the far side would leave this check vetting a function nothing serves.

RESIDUALS, because the next reader will otherwise take this for more than it is:

  - IT VETS WHAT `main` DECLARES, NOT WHAT PRODUCTION SERVES. `change-mgr.alobar.net/openapi.json`
    answers 302 to the identity provider, so the deployed surface cannot be read from a check at
    all. change-manager redeploys on a push to `main`, so the two converge in minutes -- and a
    producer sending a field inside that window still gets a 422. This is the estate's
    merged-is-not-deployed rule pointed at the check itself.
  - `_item_dict` is private to the far side. A refactor renaming it reds this check as
    `Unresolvable` rather than as a divergence, which is the correct answer: nobody here can say
    what the new shape serves.
  - IT CATCHES SENT-BUT-UNDECLARED, NOT REQUIRED-BUT-UNSENT. A field change-manager makes
    mandatory that no producer here sends is a 422 this check cannot see, because the far side's
    requiredness is a property of the annotation and the default, and reading those is a second
    parser for a failure that has not happened. Named rather than built.

Exit 0: every name this repository sends or reads is one change-manager knows. Exit 1: at least
one is not, or the comparison could not be resolved.
"""

from __future__ import annotations

import ast
import http.client
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass

from bump_proposer import cli as bump_proposer_cli
from change_proposer import cli as change_proposer_cli
from work_carrier import change_manager as work_carrier_change_manager

CHANGE_MANAGER_REPO = "AlobarQuest/change-manager"
SCHEMAS_PATH = "app/schemas.py"
API_PATH = "app/api.py"
# `main`, not a pin. change-manager deploys from its default branch, so its default branch is the
# answer to "what would accept a proposal sent today". A pin would be a second thing to keep
# current, which is the shape of the defect this whole family of checks closes.
REF = "main"

WORK_SCHEMA = "WorkChangeIn"
DEPLOY_SCHEMA = "DeployChangeIn"
SERIALIZER = "_item_dict"
LISTING_ROUTE = "list_items"
BASE_MODEL = "BaseModel"

# The one field name spelled here, and it is not part of any set being compared -- it names the
# contract's own subject so the report can say which producers carry a cause. The sets themselves
# are imported.
CONTRACT_FIELD = "originating_observation_id"


class Unresolvable(RuntimeError):
    """The check could not establish what it needed to compare. Never a silent pass."""


@dataclass(frozen=True)
class Comparison:
    """One side's names, and the far-side declaration they have to fit inside."""

    subject: str
    local: str
    ours: frozenset[str]
    far: str
    theirs: frozenset[str]
    consequence: str

    @property
    def unmet(self) -> tuple[str, ...]:
        return tuple(sorted(self.ours - self.theirs))


def fetch(repo: str, path: str, ref: str) -> str:
    """The file at a revision, read from GitHub. Read-only, one GET."""
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/contents/{path}?ref={ref}",
        headers={
            "Accept": "application/vnd.github.raw",
            "User-Agent": "orchestrator-change-record-field-compatibility/1",
        },
    )
    # Present in Actions; absent locally, where the public repository is readable anyway.
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode()
    except urllib.error.HTTPError as error:
        raise Unresolvable(
            f"cannot read {path} at {repo}@{ref}: HTTP {error.code}. The repository must stay "
            "readable and the ref must name a reachable revision."
        ) from error
    # `URLError` FIRST: it is an `OSError` subclass, so a broad clause above it would swallow the
    # one that carries a `reason`. The two clauses below cover what urllib does NOT wrap -- a
    # failure part-way through `response.read()`, which arrives as a bare `TimeoutError` (an
    # OSError) or as `http.client.IncompleteRead` (which is not one), and a body that is not UTF-8,
    # which `.decode()` raises `UnicodeDecodeError` for. All three escaped as a bare traceback.
    except urllib.error.URLError as error:
        raise Unresolvable(f"cannot reach GitHub to read {path}: {error.reason}") from error
    except (OSError, http.client.HTTPException, UnicodeDecodeError) as error:
        raise Unresolvable(
            f"the connection reading {path} at {repo}@{ref} failed: {error!r}"
        ) from error


def _module(source: str, where: str) -> ast.Module:
    try:
        return ast.parse(source)
    except SyntaxError as error:
        raise Unresolvable(f"{where} does not parse as Python: {error}") from error


def _class_named(source: str, class_name: str, where: str) -> ast.ClassDef:
    for node in _module(source, where).body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return node
    raise Unresolvable(
        f"{where} declares no class {class_name} -- it has moved, been renamed, or is nested, "
        "and a comparison against a class nobody can find is not a comparison."
    )


def declared_fields(source: str, class_name: str, where: str = SCHEMAS_PATH) -> frozenset[str]:
    """The field names a pydantic model declares, read from its source rather than imported.

    Source, because the model lives in another repository at whatever revision that repository
    is on: installing it would drag change-manager's dependency tree into this repository's
    pull-request gate to read two attributes. The trade is only sound while the parse means the
    same thing as introspection, which `test_change_record_field_compatibility.py` pins against
    a real pydantic model on this side.

    THE BASES ARE CHECKED, not assumed. This reads the class BODY, so a field declared on a
    shared base would be invisible here and would surface as a divergence that does not exist --
    a producer told to stop sending a field change-manager does in fact accept.
    """
    node = _class_named(source, class_name, where)
    bases = [base.id for base in node.bases if isinstance(base, ast.Name)]
    if bases != [BASE_MODEL] or len(node.bases) != 1:
        raise Unresolvable(
            f"{where}: {class_name} no longer inherits {BASE_MODEL} directly, so its fields are "
            "not all in its own body and this parse would under-report them."
        )
    return frozenset(
        statement.target.id
        for statement in node.body
        if isinstance(statement, ast.AnnAssign)
        and isinstance(statement.target, ast.Name)
        # `model_config` is pydantic's own; the `model_` prefix is reserved and cannot be a
        # field name.
        and not statement.target.id.startswith(("_", "model_"))
    )


def served_keys(
    source: str, function_name: str = SERIALIZER, where: str = API_PATH
) -> frozenset[str]:
    """The literal keys a serializer returns, for the half of the comparison that is a response.

    The carry reads a row off a listing route, and change-manager builds that row as a dict
    literal in a plain function rather than through a response model -- so there is no class to
    parse and no `model_fields` to read. The keys are the declaration.
    """
    for node in _module(source, where).body:
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            returns = [child for child in ast.walk(node) if isinstance(child, ast.Return)]
            if len(returns) != 1:
                raise Unresolvable(
                    f"{where}: {function_name} has {len(returns)} return statements; this reads "
                    "the one dict it serves and cannot say which of several is the row."
                )
            value = returns[0].value
            if not isinstance(value, ast.Dict):
                raise Unresolvable(
                    f"{where}: {function_name} does not return a dict literal, so the keys it "
                    "serves are not readable from its source."
                )
            keys = []
            for key in value.keys:
                # A `**spread` has no key. It would hide every name it carries, and reporting the
                # rest as the whole would be a confident under-count.
                if (
                    key is None
                    or not isinstance(key, ast.Constant)
                    or not isinstance(key.value, str)
                ):
                    raise Unresolvable(
                        f"{where}: {function_name} builds a key this cannot read, so the set it "
                        "serves is not knowable from source."
                    )
                keys.append(key.value)
            return frozenset(keys)
    raise Unresolvable(
        f"{where} declares no function {function_name} -- the serializer the carry reads through "
        "has moved or been renamed, and nobody here can say what the listing now serves."
    )


def assert_the_listing_serves_through(source: str, where: str = API_PATH) -> None:
    """The premise under the third comparison, asserted instead of assumed.

    `RECORD_FIELDS` are the keys the carry reads off `GET /api/items`. Comparing them against
    `_item_dict` is only the right comparison while that route serialises through it; were that
    ever changed, this check would go on vetting a function nothing serves -- a wrong answer that
    still reads green, which is the failure mode of having no check at all.
    """
    for node in _module(source, where).body:
        if isinstance(node, ast.FunctionDef) and node.name == LISTING_ROUTE:
            if any(
                isinstance(child, ast.Name) and child.id == SERIALIZER for child in ast.walk(node)
            ):
                return
            raise Unresolvable(
                f"{where}: {LISTING_ROUTE} no longer serialises through {SERIALIZER}, so the keys "
                "that function returns are not the keys the carry reads."
            )
    raise Unresolvable(f"{where} declares no {LISTING_ROUTE} route for the carry to read.")


def comparisons(schemas: str, api: str) -> tuple[Comparison, ...]:
    """The three, built from the local declarations and the far side's two documents."""
    serves = served_keys(api)
    return (
        Comparison(
            subject="the work lane proposes",
            local="bump_proposer.cli.PROPOSAL_FIELDS",
            ours=frozenset(bump_proposer_cli.PROPOSAL_FIELDS),
            far=WORK_SCHEMA,
            theirs=declared_fields(schemas, WORK_SCHEMA),
            consequence=(
                "that schema forbids extra keys, so change-manager answers 422 and no record is "
                "proposed at all -- the work lane stops feeding the factory, silently"
            ),
        ),
        Comparison(
            subject="the other lane proposes",
            local="change_proposer.cli.PROPOSAL_FIELDS",
            ours=frozenset(change_proposer_cli.PROPOSAL_FIELDS),
            far=DEPLOY_SCHEMA,
            theirs=declared_fields(schemas, DEPLOY_SCHEMA),
            consequence=(
                "that schema forbids extra keys, so change-manager answers 422 and no record is "
                "proposed -- and this producer's refusals are what the nightly lander reads"
            ),
        ),
        Comparison(
            subject="the carry reads",
            local="work_carrier.change_manager.RECORD_FIELDS",
            ours=frozenset(work_carrier_change_manager.RECORD_FIELDS),
            far=f"{SERIALIZER} (the row `{LISTING_ROUTE}` serves)",
            theirs=serves,
            consequence=(
                "a key that is not served reads as absent, so the carry either refuses a record "
                "it could have carried or carries one it could not fully read"
            ),
        ),
    )


def _contract_coverage(schemas: str) -> str:
    """Which producers name a cause, derived rather than asserted.

    NOT A VERDICT, deliberately. The contract's own design records that the second lane does not
    conform -- it posts no observation, so it has no cause to name -- and says the check will see
    it from the day it ships. Seeing it is this line. Failing on it would red every pull request
    over a non-conformance that was decided, and the decision is not this check's to reverse.
    """
    declared = {
        lane: CONTRACT_FIELD in fields
        for lane, fields in (
            ("bump_proposer", frozenset(bump_proposer_cli.PROPOSAL_FIELDS)),
            ("change_proposer", frozenset(change_proposer_cli.PROPOSAL_FIELDS)),
        )
    }
    naming = sorted(lane for lane, has in declared.items() if has)
    silent = sorted(lane for lane, has in declared.items() if not has)
    accepted = sorted(
        schema
        for schema in (WORK_SCHEMA, DEPLOY_SCHEMA)
        if CONTRACT_FIELD in declared_fields(schemas, schema)
    )
    return (
        f"contract: {CONTRACT_FIELD} accepted by {accepted or 'neither schema'}; "
        f"named by {naming or 'no producer'}; not named by {silent or 'none'}"
    )


def main() -> int:
    try:
        schemas = fetch(CHANGE_MANAGER_REPO, SCHEMAS_PATH, REF)
        api = fetch(CHANGE_MANAGER_REPO, API_PATH, REF)
        assert_the_listing_serves_through(api)
        checks = comparisons(schemas, api)
        contract = _contract_coverage(schemas)
    except Unresolvable as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1

    # EVERY comparison is reported before any verdict. A first divergence that hid a second would
    # make the second discoverable only after the first was fixed.
    for check in checks:
        print(
            f"{check.subject:24} {check.local} ({len(check.ours)}) "
            f"-> {CHANGE_MANAGER_REPO}@{REF} {check.far} ({len(check.theirs)}) "
            f":: {'ok' if not check.unmet else 'UNKNOWN ' + ', '.join(check.unmet)}"
        )
    print(f"\n{contract}")
    # Flushed before anything reaches stderr: stdout is block-buffered when piped and stderr is
    # not, so without this a CI log prints the verdict above the evidence it was drawn from.
    sys.stdout.flush()

    failing = [check for check in checks if check.unmet]
    if not failing:
        print(
            f"\nPASS: every name this repository sends or reads is one {CHANGE_MANAGER_REPO} knows."
        )
        return 0

    print(
        f"\nFAIL: {len(failing)} of {len(checks)} comparisons name something "
        f"{CHANGE_MANAGER_REPO}@{REF} does not.",
        file=sys.stderr,
    )
    for check in failing:
        print(
            f"\n  {check.local} carries {list(check.unmet)}, which {check.far} does not:\n"
            f"    {check.consequence}.",
            file=sys.stderr,
        )
    print(
        "\nchange-manager ships first. That ordering is not advice -- both proposal schemas "
        "forbid extra keys, so a producer that runs ahead of the declaration proposes nothing "
        "and reports a refusal, which is why the contract's increments were strictly sequential.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
