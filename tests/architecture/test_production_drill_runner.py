"""AC-010 production runner guardrails."""

import json
import stat
import subprocess
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
RUNNER = SCRIPTS / "run-production-drills.sh"
COMMON = SCRIPTS / "production_drill_common.sh"


def production_runner_source() -> str:
    return RUNNER.read_text() + COMMON.read_text()


def test_production_runner_is_an_executable_fail_closed_entrypoint() -> None:
    source = production_runner_source()

    assert source.startswith("#!/bin/bash")
    assert RUNNER.stat().st_mode & stat.S_IXUSR
    assert "set -euo pipefail" in source
    assert "--run-id" in source
    assert "RUN_ID is required" in source
    assert "--approve-live-restart" in source


def test_production_runner_is_pinned_to_the_single_live_target() -> None:
    source = production_runner_source().lower()

    assert 'api_base_url="https://sds.alobar.net"' in source
    assert "http://" not in source
    assert "https://" in source


def test_production_runner_has_no_private_or_host_control_path() -> None:
    source = production_runner_source().lower()

    for forbidden in ("psql", "sql", "docker", "kill ", "pkill", "killall"):
        assert forbidden not in source


def test_production_runner_requires_openapi_preflight_and_unique_keys() -> None:
    source = production_runner_source().lower()

    assert "/openapi.json" in source
    assert "required_openapi_paths" in source
    assert "idempotency_prefix" in source
    assert "uuidgen" in source


def test_production_runner_fetches_its_credential_at_runtime_and_redacts_logs() -> None:
    source = production_runner_source().lower()

    assert "bws secret get" in source
    assert "orchestrator_production_drill_token" in source
    assert "x-credential-key-id" in source
    assert "authorization: bearer" in source
    assert "redact" in source
    assert "authorization: bearer <redacted>" in source


def test_live_restart_needs_explicit_approval_and_readiness_checks() -> None:
    source = production_runner_source()

    assert "run_crash_recovery_drill" in source
    assert "APPROVE_LIVE_RESTART" in source
    assert "preflight_readiness" in source
    assert "restart_live_application" in source
    assert "--approve-live-restart is required" in source


def test_runner_writes_redacted_machine_readable_evidence_and_attempts_closeout() -> None:
    source = production_runner_source().lower()

    assert "evidence_file" in source
    assert "application/json" in source
    assert "attempt_failure_closeout" in source
    assert "/close" in source
    assert "failed" in source


def test_runner_executes_against_a_mock_http_transport(tmp_path: Path) -> None:
    """The runner's HTTP contract can be exercised without reaching its fixed live URL."""
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    evidence = tmp_path / "evidence.json"
    restart = tmp_path / "approved-restart"
    run_id = "00000000-0000-0000-0000-000000000001"
    credential = json.dumps(
        {
            "ORCHESTRATOR_PRODUCTION_DRILL_TOKEN": "test-token",
            "credential_key_id": "test-key",
        }
    )
    bws_response = json.dumps({"value": credential})
    openapi = json.dumps(
        {
            "paths": {
                "/health/ready": {},
                "/api/v1/production-drills/{run_id}": {},
                "/api/v1/production-drills/{run_id}/state": {},
                "/api/v1/production-drills/{run_id}/close": {},
            }
        }
    )

    (mock_bin / "bws").write_text(f"#!/bin/bash\nprintf '%s\\n' '{bws_response}'\n")
    (mock_bin / "uuidgen").write_text(
        '#!/bin/bash\nprintf "00000000-0000-0000-0000-000000000002\\n"\n'
    )
    (mock_bin / "curl").write_text(
        "#!/bin/bash\n"
        'case "$*" in\n'
        f"  *'/openapi.json'*) printf '%s\\n' '{openapi}' ;;\n"
        "  *'/health/ready'*) printf '%s\\n' '{}' ;;\n"
        f'  *\'/state\'*) printf \'%s\\n\' \'{{"run_id":"{run_id}","status":"open"}}\' ;;\n'
        "  *) exit 1 ;;\n"
        "esac\n"
    )
    restart.write_text("#!/bin/bash\nexit 0\n")
    for path in (*mock_bin.iterdir(), restart):
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    result = subprocess.run(
        [
            str(RUNNER),
            "--run-id",
            run_id,
            "--approve-live-restart",
            "--evidence-file",
            str(evidence),
        ],
        check=False,
        capture_output=True,
        env={
            "PATH": f"{mock_bin}:{Path('/usr/bin')}:{Path('/bin')}",
            "ORCHESTRATOR_PRODUCTION_DRILL_SECRET_UUID": "test-uuid",
            "ORCHESTRATOR_PRODUCTION_DRILL_RESTART_COMMAND": str(restart),
        },
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert '"status": "observed"' in evidence.read_text()
    assert "test-token" not in evidence.read_text()
