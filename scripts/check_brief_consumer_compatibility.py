#!/usr/bin/env python3
"""Refuse a pull request that serves a runner-brief key the recommended consumer cannot use.

WS-P2.23 part B. The orchestrator deploys continuously; the worker that reads its runner
brief is SHA-pinned by every target repository's caller workflow. On 2026-07-30 the
orchestrator began serving an `enrichment` key against a consumer that had never heard of
it, and every run in the estate died at brief-parse for a full day. Nothing noticed: the
conformance check compares SHAs, and a SHA tells you nothing about whether the revision
behind it can read what you serve.

The ordering rule -- the consumer merges first -- has been stated in prose since WS-P2.12
and enforced by nothing. This is the enforcement, and it belongs in the pull-request gate
because the pull request that adds the field is the thing that must not merge. A later
release check would be a report on a decision already taken.

WHICH REVISION IS VETTED, and why this repository no longer answers that question from a
file of its own. Until ADR-0015's amendment of 2026-09-11 the answer came from this
repository's own caller workflow, which is gone: this repository declares
`factory_target = false` and hosts no caller. That file was never the source of the value
in any case -- it was a copy of `RECOMMENDED_CALLER_PIN`, which factory-runner declares and
every target repository's caller is held to by the conformance kit's `runner.caller` check.
Reading the recommendation directly removes a hop rather than changing an answer: the two
agreed at the moment of the change, and the sibling capability check had already been
reading both and reporting them as one line whenever they coincided.

What it does, once the consumer's reusable workflow installs its own commit (part A):

1. read the revision every caller in the estate is expected to be pinned to, from the
   consumer's own `RECOMMENDED_CALLER_PIN`;
2. assert that the workflow at that revision still installs its own commit -- so the
   revision named is the CLI revision a run would execute -- rather than trusting it;
3. read the consumer's brief model at that revision and take its declared field names;
4. require every field this repo's brief response declares to be one of them.

Deliberately keyed on DECLARED fields, not on whether the consumer would tolerate an
undeclared one. Part C makes the consumer tolerant precisely so an escape is survivable --
if this check asked "would it parse?" then part C would silently switch it off, and the
ordering rule would go back to being prose. A field the consumer does not declare is a
field it cannot use, which is a feature that does not work.

Residual, stated rather than papered over, and it is the same one the two sibling checks
carry: a target repository whose caller has drifted off the recommendation runs a revision
this never read. `runner.caller` is what sees that, per repository.

Usage:
    python3 scripts/check_brief_consumer_compatibility.py

Exit 0: every served field is declared by the recommended consumer. Exit 1: at least one is
not, or the revision could not be resolved.
"""

from __future__ import annotations

import ast
import http.client
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVED_MODEL_SOURCE = REPO_ROOT / "src/orchestrator/api/schemas.py"
SERVED_MODEL = "RunnerBriefResponse"
CONSUMER_REPOSITORY = "AlobarQuest/factory-runner"
CONSUMER_MODEL_PATH = "src/factory_runner/models.py"
CONSUMER_MODEL = "RunnerBrief"
CONSUMER_WORKFLOW_PATH = ".github/workflows/factory-runner.yml"
RECOMMENDED_PIN_PATH = "RECOMMENDED_CALLER_PIN"

FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


class Unresolvable(RuntimeError):
    """The check could not establish what it needed to compare. Never a silent pass."""


def recommended_revision(repo: str = CONSUMER_REPOSITORY) -> str:
    """The revision every caller in the estate is expected to be pinned to.

    Read from the consumer's default branch, which is where the estate declares it -- this
    repository hosts no caller of its own (ADR-0015, 2026-09-11) and recovering the value
    from one would have been indirection around a number declared elsewhere.
    """
    ref = fetch(repo, RECOMMENDED_PIN_PATH, "HEAD").strip()
    if not FULL_SHA.match(ref):
        raise Unresolvable(
            f"{repo}'s {RECOMMENDED_PIN_PATH} reads {ref!r}, which is not a full 40-character "
            "commit, so there is no revision to check compatibility against. A branch or tag "
            "is mutable, and an answer about one would expire."
        )
    return ref


def fetch(repo: str, path: str, ref: str) -> str:
    """The file at a revision, read from GitHub. Read-only, one GET."""
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/contents/{path}?ref={ref}",
        headers={
            "Accept": "application/vnd.github.raw",
            "User-Agent": "orchestrator-brief-compatibility/1",
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
            f"cannot read {path} at {repo}@{ref[:8]}: HTTP {error.code}. "
            "The repository must stay public and the pin must name a reachable commit."
        ) from error
    # `URLError` FIRST: it is an `OSError` subclass, so a broad clause above it would swallow the
    # one that carries a `reason`. The two clauses below cover what urllib does NOT wrap -- a
    # failure part-way through `response.read()`, which arrives as a bare `TimeoutError` (an
    # OSError) or as `http.client.IncompleteRead` (which is not one), and a body that is not UTF-8,
    # which `.decode()` raises `UnicodeDecodeError` for. All three escaped as a bare traceback.
    except urllib.error.URLError as error:
        raise Unresolvable(
            f"cannot reach GitHub to read {path} at {ref[:8]}: {error.reason}"
        ) from error
    except (OSError, http.client.HTTPException, UnicodeDecodeError) as error:
        raise Unresolvable(
            f"the connection reading {path} at {repo}@{ref} failed: {error!r}"
        ) from error


def declared_fields(source: str, class_name: str) -> set[str]:
    """The field names a pydantic model declares, read from its source.

    Source rather than introspection because the consumer's model lives at an arbitrary
    revision of another repository: installing it would pull that repository's whole
    dependency tree into this repository's pull-request gate to read one attribute. The
    parse is exact for a model whose fields are literal annotations, and
    `test_brief_consumer_compatibility.py` pins it against the real model for the half of
    the comparison that IS importable here -- so a parser that stopped agreeing with
    pydantic is caught before it can vet anything wrongly.
    """
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                statement.target.id
                for statement in node.body
                if isinstance(statement, ast.AnnAssign)
                and isinstance(statement.target, ast.Name)
                # `model_config` is pydantic's own; the `model_` prefix is reserved and
                # cannot be a field name.
                and not statement.target.id.startswith(("_", "model_"))
            }
    raise Unresolvable(f"no class {class_name} found -- the model has moved or been renamed")


def assert_the_workflow_installs_its_own_commit(workflow_text: str, ref: str) -> None:
    """The recommendation is the consumer revision only because the workflow installs itself.

    That is what makes this one lookup instead of two. Asserting it costs one GET and
    converts a silent wrong answer -- vetting a revision the run would never use -- into a
    loud one, should the consumer ever go back to naming a literal.
    """
    document = yaml.safe_load(workflow_text)
    # Parsed, not grepped: this very workflow carries a COMMENT naming `job.workflow_sha`,
    # so a substring check over the raw text would pass on the prose alone.
    runs = "\n".join(
        str(step.get("run", ""))
        for job in document.get("jobs", {}).values()
        for step in job.get("steps", [])
    )
    if "job.workflow_sha" not in runs:
        raise Unresolvable(
            f"the pinned workflow at {ref[:8]} does not install `job.workflow_sha`, so the "
            "pinned revision is not the revision it installs and this check would be "
            "comparing against the wrong one."
        )


def main() -> int:
    repo = CONSUMER_REPOSITORY
    try:
        ref = recommended_revision(repo)
        assert_the_workflow_installs_its_own_commit(fetch(repo, CONSUMER_WORKFLOW_PATH, ref), ref)
        accepted = declared_fields(fetch(repo, CONSUMER_MODEL_PATH, ref), CONSUMER_MODEL)
    except Unresolvable as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1

    served = declared_fields(SERVED_MODEL_SOURCE.read_text(), SERVED_MODEL)
    undeclared = sorted(served - accepted)

    print(
        f"consumer:  {repo}@{ref[:8]} (recommended to every caller) -- "
        f"{CONSUMER_MODEL} declares {len(accepted)}"
    )
    print(f"served:    {SERVED_MODEL} declares {len(served)}")
    if not undeclared:
        print(f"\nPASS: every served brief field is declared by {repo}@{ref[:8]}.")
        return 0

    print(
        f"\nFAIL: {SERVED_MODEL} serves {len(undeclared)} field(s) the recommended consumer "
        f"does not declare: {undeclared}\n\n"
        f"The consumer at {repo}@{ref[:8]} cannot use them, so shipping this would add a "
        "field no worker reads.\n"
        f"Merge the field into the consumer first, then advance {RECOMMENDED_PIN_PATH} and "
        "every caller, then re-run.\n"
        "That ordering is not advice: it is the rule this check exists to enforce, and it "
        "was prose until it cost the estate a day of dead dispatches on 2026-07-30.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
