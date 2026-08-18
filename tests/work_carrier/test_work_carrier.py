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
    assert "0 approved, 0 prepared, 0 carried, 0 refused, 0 not carried." in report


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
    assert "2 approved, 0 prepared, 0 carried, 2 refused, 0 not carried." in report


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


def test_a_pass_that_was_not_asked_to_register_prints_the_payload(
    checkout_root: Path,
) -> None:
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
    assert "1 approved, 1 prepared, 0 carried, 0 refused, 0 not carried." in report
    assert '"change_record_id": 77' in report or '"change_record_id":77' in report
    assert "not asked to (--register)" in report


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


# ---------------------------------------------------------------------------------------
# Registering — ADR-0027
# ---------------------------------------------------------------------------------------


class _Writer:
    """A stand-in for the orchestrator client that RECORDS what it was asked to do.

    Counting the calls is the point: the property under test is not what the report says but
    whether anything left the process, and a double that only returned a body could not tell a
    pass that wrote nothing from one that wrote and reported badly.
    """

    def __init__(self, error: Exception | None = None) -> None:
        self.payloads: list[dict] = []
        self._error = error

    def register_intake(self, payload: dict) -> dict:
        self.payloads.append(payload)
        if self._error is not None:
            raise self._error
        return {"id": "11111111-1111-1111-1111-111111111111"}


def _run_with_writer(records, root: Path, writer, argv: list[str], runner=None):
    import io

    import work_carrier.cli as cli_module
    import work_carrier.prepare as prepare_module

    original = prepare_module.prepare
    if runner is not None:
        cli_module.prepare = lambda r, **kw: original(r, **kw, runner=runner)
    try:
        out = io.StringIO()
        code = run(
            ["--checkout-root", str(root), *argv],
            source=_Source(records),
            registrar=writer,
            out=out,
        )
    finally:
        cli_module.prepare = original
    return code, out.getvalue()


def test_a_bare_pass_writes_nothing_even_with_a_client_to_hand(checkout_root: Path) -> None:
    """`--register` decides, not the presence of a client.

    Keyed on the flag rather than on whether a credential happened to be configured, so "a bare
    invocation writes nothing" is a property of the branch rather than of the environment the
    caller set up. A writer is handed in here precisely so the test cannot pass by accident.
    """
    rec = record()
    runner, _ = runner_returning(payload_for(rec))
    writer = _Writer()
    code, report = _run_with_writer([rec], checkout_root, writer, [], runner=runner)

    assert code == EXIT_OK, report
    assert writer.payloads == []
    assert "not asked to (--register)" in report


def test_registering_carries_the_emitters_payload_unedited(checkout_root: Path) -> None:
    """The payload is what `orchestrator emit-intake-payload` produced, byte for byte.

    That is the strongest available statement that the carry relaxes nothing: it is the same
    command whose output a human pastes, so there is no second composition to diverge from it.
    """
    rec = record()
    runner, _ = runner_returning(payload_for(rec))
    writer = _Writer()
    code, report = _run_with_writer([rec], checkout_root, writer, ["--register"], runner=runner)

    assert code == EXIT_OK, report
    assert writer.payloads == [payload_for(rec)]
    assert "carried: revision 11111111-1111-1111-1111-111111111111" in report
    assert "1 approved, 1 prepared, 1 carried, 0 refused, 0 not carried." in report


def test_a_refused_record_is_never_registered(checkout_root: Path) -> None:
    """The fixture package is not in the tamper-evident approval chain, so `prepare` refuses it.
    Nothing that was refused may reach the orchestrator, whatever the pass was asked to do."""
    writer = _Writer()
    code, report = _run_with_writer([record()], checkout_root, writer, ["--register"])

    assert code == EXIT_FINDINGS
    assert writer.payloads == []
    assert "[REFUSED]" in report


def test_a_registration_failure_is_a_finding_and_does_not_stop_the_queue(
    checkout_root: Path,
) -> None:
    """A refusal from the orchestrator needs a person, so it is exit 3 -- and per-record
    isolation means the second record is still attempted rather than stranded behind the
    first."""
    from work_carrier.orchestrator_client import OrchestratorError

    first = record()
    second = record(change_record_id=78)
    by_record = {str(rec.change_record_id): payload_for(rec) for rec in (first, second)}

    def runner(command, **kwargs):
        wanted = command[command.index("--change-record") + 1]
        return subprocess.CompletedProcess(
            command, 0, stdout=json.dumps(by_record[wanted]), stderr=""
        )

    writer = _Writer(error=OrchestratorError("the orchestrator answered 409"))
    code, report = _run_with_writer(
        [first, second], checkout_root, writer, ["--register"], runner=runner
    )

    assert code == EXIT_FINDINGS
    assert len(writer.payloads) == 2
    assert report.count("NOT CARRIED") == 2
    assert "2 approved, 2 prepared, 0 carried, 0 refused, 2 not carried." in report


def test_registering_with_no_credential_is_unusable_input(checkout_root: Path) -> None:
    """Named rather than falling through to a pass that silently prints instead of writing.

    The dangerous shape is a scheduled `--register` run whose credential fetch failed reporting
    a clean pass: it would look identical to a queue that was carried.
    """
    import io

    import pytest as _pytest

    monkeypatch = _pytest.MonkeyPatch()
    monkeypatch.delenv("WORK_CARRIER_ORCHESTRATOR_TOKEN", raising=False)
    try:
        out = io.StringIO()
        code = run(
            ["--checkout-root", str(checkout_root), "--register"],
            source=_Source([record()]),
            out=out,
        )
    finally:
        monkeypatch.undo()

    assert code == EXIT_UNUSABLE
    assert "WORK_CARRIER_ORCHESTRATOR_TOKEN is not set" in out.getvalue()
    assert "[PREPARED]" not in out.getvalue()


# ---------------------------------------------------------------------------------------
# What the client does with an answer that is not a 2xx
# ---------------------------------------------------------------------------------------


def _client(handler):
    import httpx

    from work_carrier.orchestrator_client import OrchestratorClient

    return OrchestratorClient(
        "token",
        "orchestrator-system",
        base_url="https://example.invalid",
        transport=httpx.MockTransport(handler),
    )


def test_a_redirect_is_a_refusal_not_a_success() -> None:
    """The production case, and the reason `>= 400` is not the right test.

    `POST /api/v1/package-intakes` sits behind a forward-auth router at the proxy, so a machine
    bearer arriving there draws a 302 to id.alobar.net -- measured against production, not
    inferred. httpx does not follow redirects, so a `>= 400` check waves that through to
    `response.json()`, and the pass reports "the intake response was not JSON" every morning: a
    routing refusal disguised as a response-encoding fault.
    """
    import httpx

    from work_carrier.orchestrator_client import OrchestratorError

    client = _client(
        lambda request: httpx.Response(
            302, headers={"Location": "https://id.example.invalid/authorize"}, text="<a>go</a>"
        )
    )
    with pytest.raises(OrchestratorError) as error:
        client.register_intake({"idempotency_key": "k"})

    assert "302" in str(error.value)
    assert "proxy routing" in str(error.value)
    assert "not JSON" not in str(error.value)


def test_a_2xx_that_is_not_201_is_still_a_success() -> None:
    """The inverse, so the check above cannot pass by refusing everything."""
    import httpx

    client = _client(lambda request: httpx.Response(200, json={"id": "abc"}))
    assert client.register_intake({"idempotency_key": "k"}) == {"id": "abc"}


@pytest.mark.parametrize("status", [403, 409])
def test_a_role_refusal_carries_the_hint_whatever_its_status(status: int) -> None:
    """Keyed on the REFUSAL, not on the transport — and the 409 case is the discriminating one.

    The hint used to key on 403, and that was wrong for exactly the credentials it names: the
    intake role refusal was a 409 until `main.py`'s 403 set learned about it, so a worker bearer
    got no hint at the moment it most needed one. Fixing the status made the old keying work
    again by coincidence, which is precisely why this test pins the intent instead: a status is
    a thing that has already moved once in this increment, and the hint must not move with it.
    """
    import httpx

    from work_carrier.orchestrator_client import OrchestratorError

    client = _client(
        lambda request: httpx.Response(
            status, json={"error": {"code": "intake_registrar_invalid", "message": "no"}}
        )
    )
    with pytest.raises(OrchestratorError) as error:
        client.register_intake({"idempotency_key": "k"})

    assert "not the system one" in str(error.value)


def test_an_unrelated_403_carries_no_credential_hint() -> None:
    """The inverse. Without it the rule above passes on "always hint", which would attach the
    wrong diagnosis to every refusal that happens to share a status."""
    import httpx

    from work_carrier.orchestrator_client import OrchestratorError

    client = _client(
        lambda request: httpx.Response(
            403, json={"error": {"code": "csrf_rejected", "message": "no"}}
        )
    )
    with pytest.raises(OrchestratorError) as error:
        client.register_intake({"idempotency_key": "k"})

    assert "not the system one" not in str(error.value)


def test_a_refusal_carries_its_code_for_the_report_to_classify() -> None:
    import httpx

    from work_carrier.orchestrator_client import IntakeRefused

    client = _client(
        lambda request: httpx.Response(
            409, json={"error": {"code": "package_intake_conflict", "message": "no"}}
        )
    )
    with pytest.raises(IntakeRefused) as error:
        client.register_intake({"idempotency_key": "k"})

    assert error.value.code == "package_intake_conflict"


def test_a_malformed_orchestrator_url_is_a_tool_failure_not_a_traceback(
    checkout_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CONSTRUCTOR raises for some malformed URLs and request time for others.

    Catching only the second leaves an environment-variable typo crashing the pass with a
    traceback, which is exactly what the constructor's own guard exists to prevent. Its sibling
    `HttpWorkRecordSource` has always reported this class as a tool failure.
    """
    import io

    monkeypatch.setenv("WORK_CARRIER_ORCHESTRATOR_TOKEN", "t")
    out = io.StringIO()
    code = run(
        [
            "--checkout-root",
            str(checkout_root),
            "--register",
            "--orchestrator-url",
            "https://exa\nmple.invalid",
        ],
        source=_Source([record()]),
        out=out,
    )

    assert code == EXIT_TOOL_FAILURE
    assert "[TOOL FAILURE]" in out.getvalue()
    assert "unusable" in out.getvalue()


def test_a_conflict_names_the_act_that_ends_it(checkout_root: Path) -> None:
    """`package_intake_conflict` reads "already registered with different content", and for the
    case this lane produces that is false: only the registrar and the change record differ.

    It is also the one refusal that repeats -- nothing marks a change record carried, so an
    approved record is re-attempted every pass. So the report has to name the human act that
    takes it out of the queue, or the morning log accuses the content of a divergence that is
    not there and offers nothing to do about it.
    """
    from work_carrier.orchestrator_client import IntakeRefused

    rec = record()
    runner, _ = runner_returning(payload_for(rec))
    writer = _Writer(error=IntakeRefused("conflict", "package_intake_conflict"))
    code, report = _run_with_writer([rec], checkout_root, writer, ["--register"], runner=runner)

    assert code == EXIT_FINDINGS
    assert "resolving the record in change-manager" in report


def test_an_ordinary_refusal_carries_no_guidance(checkout_root: Path) -> None:
    """The inverse, so the guidance is attached to one refusal rather than printed on all."""
    from work_carrier.orchestrator_client import IntakeRefused

    rec = record()
    runner, _ = runner_returning(payload_for(rec))
    writer = _Writer(error=IntakeRefused("nope", "intake_registrar_invalid"))
    code, report = _run_with_writer([rec], checkout_root, writer, ["--register"], runner=runner)

    assert code == EXIT_FINDINGS
    assert "resolving the record in change-manager" not in report
