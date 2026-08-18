"""The carry (ADR-0026): approved change record -> ready intake payload, or a reason why not.

Three things are proven here and they need different apparatus. That the CARRIER'S COMMAND LINE
is real is proven by running it -- a fabricated `subprocess.run` would accept any spelling of
any flag, and a wrong one is invisible until the first scheduled pass. That the CARRIER'S
VERIFICATION is right is proven with an injected runner, because the success path needs a
payload no test may fabricate through the real emitter: a package that is genuinely approved in
the tamper-evident chain cannot be synthesized, which is the property intake exists to have.
And that the carry FAILS CLOSED is proven by both.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from work_carrier.change_manager import ChangeManagerError, WorkRecord
from work_carrier.cli import EXIT_FINDINGS, EXIT_OK, EXIT_TOOL_FAILURE, EXIT_UNUSABLE, run
from work_carrier.prepare import Prepared, Refused, emit_key, package_path, prepare

FIXTURE_PACKAGE = "ws32-approved-software"


def record(**overrides) -> WorkRecord:
    base = {
        "change_record_id": 77,
        "package_id": FIXTURE_PACKAGE,
        "package_revision": 1,
        "package_source_repository": "AlobarQuest/intent-packages",
        "reasoning": "the estate decided to do this",
        "decided_by": "devon",
    }
    return WorkRecord(**{**base, **overrides})


@pytest.fixture(autouse=True)
def emitter_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """The carrier resolves `orchestrator` from PATH, which is a real deployment requirement.

    `scripts/run-work-carrier.sh` puts the repository venv's `bin` there; a bare `pytest`
    invocation has not. Putting the running interpreter's own directory on PATH is what makes
    the tests below exercise the REAL command rather than skipping to a "not on PATH" refusal,
    and it is the same directory the launcher exports.
    """
    monkeypatch.setenv("PATH", f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}")


@pytest.fixture()
def checkout_root(tmp_path: Path) -> Path:
    """A checkout root laid out the way this machine lays one out.

    The real fixture package is COPIED in rather than symlinked, so the layout the carrier
    resolves (`<root>/<repo>/packages/<package_id>`) is exercised rather than assumed.
    """
    source = Path("tests/fixtures/intent-packages") / FIXTURE_PACKAGE
    target = tmp_path / "intent-packages" / "packages" / FIXTURE_PACKAGE
    target.parent.mkdir(parents=True)
    target.mkdir()
    for path in source.iterdir():
        (target / path.name).write_bytes(path.read_bytes())
    return tmp_path


def payload_for(rec: WorkRecord) -> dict:
    return {
        "package_id": rec.package_id,
        "revision": rec.package_revision,
        "source_repository": rec.package_source_repository,
        "change_record_id": rec.change_record_id,
        "status_at_intake": "approved",
        "verification_mode": "caller_attested_cli_verified",
        "idempotency_key": emit_key(rec),
        "expected_version": 0,
    }


def runner_returning(payload, *, returncode: int = 0, stderr: str = ""):
    seen: list[list[str]] = []

    def runner(command, **kwargs):
        seen.append(command)
        text = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.CompletedProcess(command, returncode, stdout=text, stderr=stderr)

    return runner, seen


# ---------------------------------------------------------------------------------------
# The command line is REAL
# ---------------------------------------------------------------------------------------


def test_the_carrier_invokes_the_emitter_with_options_it_actually_has(checkout_root: Path) -> None:
    """Run the real command and prove it was REJECTED BY THE PACKAGE, not by the parser.

    A misspelled flag exits 2 with "No such option" and never reaches a package at all -- and
    that is exactly the failure an injected `subprocess.run` cannot see, because a double accepts
    any argument list. The fixture package is approved in its own YAML but is not in the
    tamper-evident approval chain, so the honest answer is an approval refusal. Getting that
    answer is what proves every argument name and flag spelling is one the CLI has.
    """
    outcome = prepare(record(), checkout_root=checkout_root)
    assert isinstance(outcome, Refused)
    assert outcome.reason == "package_not_intakeable"
    assert "No such option" not in outcome.detail
    assert "Usage:" not in outcome.detail
    assert "approval" in outcome.detail.lower()


def test_an_unapproved_package_is_refused_and_nothing_is_prepared(checkout_root: Path) -> None:
    """A package whose own YAML says it is a draft. The real command, the real refusal."""
    draft = checkout_root / "intent-packages" / "packages" / "ws32-draft-software"
    draft.mkdir(parents=True)
    source = Path("tests/fixtures/intent-packages/ws32-draft-software")
    for path in source.iterdir():
        (draft / path.name).write_bytes(path.read_bytes())
    outcome = prepare(record(package_id="ws32-draft-software"), checkout_root=checkout_root)
    assert isinstance(outcome, Refused)
    assert outcome.reason == "package_not_intakeable"


# ---------------------------------------------------------------------------------------
# Fail closed
# ---------------------------------------------------------------------------------------


def test_a_record_naming_no_checkout_is_refused(tmp_path: Path) -> None:
    outcome = prepare(record(), checkout_root=tmp_path)
    assert isinstance(outcome, Refused)
    assert outcome.reason == "package_not_on_disk"
    assert "cannot author one" in outcome.detail


def test_a_revision_mismatch_is_refused_rather_than_substituted(checkout_root: Path) -> None:
    """The record's revision is the one a human approved. A checkout holding a different one is
    a different piece of work, and preparing it would carry an approval onto something nobody
    approved."""
    rec = record(package_revision=2)
    runner, _ = runner_returning({**payload_for(rec), "revision": 1})
    outcome = prepare(rec, checkout_root=checkout_root, runner=runner)
    assert isinstance(outcome, Refused)
    assert outcome.reason == "revision_mismatch"


def test_a_package_mismatch_is_refused(checkout_root: Path) -> None:
    """The path resolves on the repository's last segment only, so a record naming another
    owner's fork finds the same checkout. This is the check that makes that safe."""
    rec = record()
    runner, _ = runner_returning({**payload_for(rec), "package_id": "something-else"})
    outcome = prepare(rec, checkout_root=checkout_root, runner=runner)
    assert isinstance(outcome, Refused)
    assert outcome.reason == "package_mismatch"


def test_a_payload_that_does_not_name_the_change_record_is_refused(checkout_root: Path) -> None:
    """The join is the point of the increment. A payload without it is one a human pastes and
    nobody notices is unattributed."""
    rec = record()
    payload = payload_for(rec)
    del payload["change_record_id"]
    runner, _ = runner_returning(payload)
    outcome = prepare(rec, checkout_root=checkout_root, runner=runner)
    assert isinstance(outcome, Refused)
    assert outcome.reason == "join_missing"


def test_unreadable_emitter_output_is_refused(checkout_root: Path) -> None:
    runner, _ = runner_returning("not json at all")
    outcome = prepare(record(), checkout_root=checkout_root, runner=runner)
    assert isinstance(outcome, Refused)
    assert outcome.reason == "emitter_output_unreadable"


def test_a_missing_emitter_is_refused_not_raised(checkout_root: Path) -> None:
    def runner(command, **kwargs):
        raise FileNotFoundError(command[0])

    outcome = prepare(record(), checkout_root=checkout_root, runner=runner)
    assert isinstance(outcome, Refused)
    assert outcome.reason == "emitter_not_on_path"


def test_an_emitter_timeout_is_refused_not_raised(checkout_root: Path) -> None:
    def runner(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 1)

    outcome = prepare(record(), checkout_root=checkout_root, runner=runner)
    assert isinstance(outcome, Refused)
    assert outcome.reason == "emitter_failed"


# ---------------------------------------------------------------------------------------
# The prepared payload
# ---------------------------------------------------------------------------------------


def test_a_prepared_payload_names_the_change_record(checkout_root: Path) -> None:
    rec = record()
    runner, seen = runner_returning(payload_for(rec))
    outcome = prepare(rec, checkout_root=checkout_root, runner=runner)
    assert isinstance(outcome, Prepared)
    assert outcome.payload["change_record_id"] == rec.change_record_id
    assert "--change-record" in seen[0]
    assert seen[0][seen[0].index("--change-record") + 1] == str(rec.change_record_id)


def test_the_emit_key_is_derived_so_two_passes_print_the_same_bytes() -> None:
    """A random key would make two passes over one unchanged queue look like two pieces of work."""
    assert emit_key(record()) == emit_key(record())
    assert emit_key(record()) != emit_key(record(change_record_id=78))


def test_the_package_path_is_repository_then_packages_then_id(tmp_path: Path) -> None:
    assert package_path(tmp_path, record()) == (
        tmp_path / "intent-packages" / "packages" / FIXTURE_PACKAGE
    )


# ---------------------------------------------------------------------------------------
# The pass
# ---------------------------------------------------------------------------------------


class _Source:
    def __init__(self, records=(), error: Exception | None = None) -> None:
        self._records = tuple(records)
        self._error = error
        self.calls = 0

    def approved_work(self):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._records


def _run(records, root: Path, error: Exception | None = None):
    import io

    out = io.StringIO()
    code = run(
        ["--checkout-root", str(root)],
        source=_Source(records, error),
        out=out,
    )
    return code, out.getvalue()


def test_an_empty_queue_is_a_clean_pass(checkout_root: Path) -> None:
    code, report = _run([], checkout_root)
    assert code == EXIT_OK
    assert "0 approved, 0 prepared, 0 refused." in report


def test_a_refusal_is_a_finding(checkout_root: Path) -> None:
    code, report = _run([record()], checkout_root)
    assert code == EXIT_FINDINGS
    assert "[REFUSED]" in report
    assert "change record 77" in report


def test_one_refusal_does_not_stop_the_other_records(checkout_root: Path) -> None:
    """Per-record isolation. A pass is a report over the whole approved queue or it is not one.

    Both records here refuse (the fixture is not chain-approved), which is what makes the
    ASSERTION about coverage rather than about outcome: two records in, two lines out.
    """
    code, report = _run([record(), record(change_record_id=78)], checkout_root)
    assert code == EXIT_FINDINGS
    assert report.count("[REFUSED]") == 2
    assert "2 approved, 0 prepared, 2 refused." in report


def test_change_manager_being_unreadable_is_a_tool_failure_not_a_finding(
    checkout_root: Path,
) -> None:
    """A finding says somebody must look at a record. A tool failure says the pass did not
    happen. Collapsing them would report the whole queue as clean on a night the listing 500ed."""
    code, report = _run([], checkout_root, error=ChangeManagerError("connection refused"))
    assert code == EXIT_TOOL_FAILURE
    assert "connection refused" in report


def test_a_missing_checkout_root_is_unusable_input(tmp_path: Path) -> None:
    import io

    out = io.StringIO()
    code = run(["--checkout-root", str(tmp_path / "nope")], source=_Source(()), out=out)
    assert code == EXIT_UNUSABLE
    assert "no checkout root" in out.getvalue()


def test_the_pass_prints_the_payload_it_prepared(checkout_root: Path) -> None:
    import io

    rec = record()
    runner, _ = runner_returning(payload_for(rec))
    import work_carrier.cli as cli_module
    import work_carrier.prepare as prepare_module

    original = prepare_module.prepare
    cli_module.prepare = lambda r, **kw: original(r, **kw, runner=runner)
    try:
        out = io.StringIO()
        code = run(["--checkout-root", str(checkout_root)], source=_Source([rec]), out=out)
    finally:
        cli_module.prepare = original
    report = out.getvalue()
    assert code == EXIT_OK, report
    assert "[PREPARED]" in report
    assert "1 approved, 1 prepared, 0 refused." in report
    assert '"change_record_id": 77' in report or '"change_record_id":77' in report
    assert "/review/intakes/new" in report


# ---------------------------------------------------------------------------------------
# The REQUEST, not only the rows it returns
# ---------------------------------------------------------------------------------------


def test_the_listing_request_names_the_source_and_the_status() -> None:
    """The row checks in `_record` cover what came back; this covers what was ASKED.

    Dropping `source` is the dangerous one and it is invisible from the rows alone in
    production: `GET /api/items` WITHHOLDS a proposed source from a caller that does not name
    one, so the carry would read an empty list and report a clean pass having carried nothing --
    success-shaped silence, which is the failure mode this estate keeps finding. Dropping
    `status` is the other direction: the carry would be handed records no human has approved.
    """
    import httpx

    from work_carrier.change_manager import HttpWorkRecordSource

    seen: list[httpx.QueryParams] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params)
        return httpx.Response(200, json=[])

    source = HttpWorkRecordSource(
        base_url="https://example.invalid",
        token="t",
        client=httpx.Client(
            base_url="https://example.invalid", transport=httpx.MockTransport(handler)
        ),
    )
    assert source.approved_work() == ()
    assert seen[0].get("source") == "work"
    assert seen[0].get("status") == "approved"


def test_a_row_from_another_pipeline_is_refused_even_though_it_was_asked_for() -> None:
    """FastAPI ignores an unknown query parameter silently, so a renamed one would hand this
    program another pipeline's records with no error anywhere. The row check is the second
    reading that catches it."""
    import httpx

    from work_carrier.change_manager import ChangeManagerError, HttpWorkRecordSource

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"id": 1, "source": "drift", "status": "approved"}])

    source = HttpWorkRecordSource(
        base_url="https://example.invalid",
        token="t",
        client=httpx.Client(
            base_url="https://example.invalid", transport=httpx.MockTransport(handler)
        ),
    )
    with pytest.raises(ChangeManagerError) as raised:
        source.approved_work()
    assert "'drift' record" in str(raised.value)


def test_a_row_that_is_not_approved_is_refused() -> None:
    import httpx

    from work_carrier.change_manager import ChangeManagerError, HttpWorkRecordSource

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"id": 1, "source": "work", "status": "pending"}])

    source = HttpWorkRecordSource(
        base_url="https://example.invalid",
        token="t",
        client=httpx.Client(
            base_url="https://example.invalid", transport=httpx.MockTransport(handler)
        ),
    )
    with pytest.raises(ChangeManagerError):
        source.approved_work()


@pytest.mark.parametrize(
    ("missing", "message"),
    [
        ("package_id", "names no package"),
        ("package_revision", "names no package revision"),
        ("package_source_repository", "names no package repository"),
        ("id", "carries no usable id"),
    ],
)
def test_a_row_missing_any_locator_field_is_refused_rather_than_guessed_at(
    missing: str, message: str
) -> None:
    """Guessing would produce an intake naming a package nobody chose.

    ONE FIELD IS REMOVED AT A TIME, and that is the whole design of this test. A first version
    sent a row missing all three and asserted `"names no package" in str(error)` -- which passes
    on the REVISION guard's message, because "names no package revision" contains it. Deleting
    the package-id guard left it green: correct about the wrong noun, and mutation is what found
    it. Each case now leaves every other field valid, so exactly one guard can fire, and the
    match is anchored at the END of the message so no expectation is a prefix of another.
    """
    import httpx

    from work_carrier.change_manager import ChangeManagerError, HttpWorkRecordSource

    row = {
        "id": 9,
        "source": "work",
        "status": "approved",
        "package_id": FIXTURE_PACKAGE,
        "package_revision": 1,
        "package_source_repository": "AlobarQuest/intent-packages",
    }
    del row[missing]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[row])

    source = HttpWorkRecordSource(
        base_url="https://example.invalid",
        token="t",
        client=httpx.Client(
            base_url="https://example.invalid", transport=httpx.MockTransport(handler)
        ),
    )
    with pytest.raises(ChangeManagerError) as raised:
        source.approved_work()
    assert str(raised.value).endswith(message), str(raised.value)


def test_a_complete_row_is_accepted() -> None:
    """The control for the parametrized refusals above: without it they pass on a reader that
    refuses everything."""
    import httpx

    from work_carrier.change_manager import HttpWorkRecordSource

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "id": 9,
                    "source": "work",
                    "status": "approved",
                    "package_id": FIXTURE_PACKAGE,
                    "package_revision": 1,
                    "package_source_repository": "AlobarQuest/intent-packages",
                }
            ],
        )

    source = HttpWorkRecordSource(
        base_url="https://example.invalid",
        token="t",
        client=httpx.Client(
            base_url="https://example.invalid", transport=httpx.MockTransport(handler)
        ),
    )
    assert source.approved_work()[0].change_record_id == 9


def test_a_malformed_base_url_is_a_tool_failure_not_a_traceback() -> None:
    """`httpx` raises THREE unrelated families for a malformed URL and the third is a
    `ValueError`: IDNA encoding of a bad HOST raises `UnicodeError`, which is neither an
    `HTTPError` nor an `InvalidURL`. A doubled dot in an environment variable is an ordinary
    typo, and this program's whole interface is its exit code."""
    from work_carrier.change_manager import ChangeManagerError, HttpWorkRecordSource

    source = HttpWorkRecordSource(base_url="https://host..example", token="t")
    with pytest.raises(ChangeManagerError):
        source.approved_work()
