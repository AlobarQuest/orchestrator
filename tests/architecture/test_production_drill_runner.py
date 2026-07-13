"""AC-010 production runner guardrails and public-contract execution."""

import json
import stat
import subprocess
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
RUNNER = SCRIPTS / "run-production-drills.sh"
COMMON = SCRIPTS / "production_drill_common.sh"
SCENARIOS = (
    "crash_recovery",
    "evidence_recovery",
    "external_pr_conflict",
    "deploy_split_brain",
    "stalled_approval",
)


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
    assert "--resume-after-restart" in source


def test_production_runner_is_pinned_to_the_single_live_target() -> None:
    source = production_runner_source().lower()

    assert 'api_base_url="https://sds.alobar.net"' in source
    assert "http://" not in source


def test_production_runner_has_no_private_or_arbitrary_host_control_path() -> None:
    source = production_runner_source().lower()

    for forbidden in (
        "psql",
        "sql",
        "docker",
        "kill ",
        "pkill",
        "killall",
        "orchestrator_production_drill_restart_command",
        "/close",
    ):
        assert forbidden not in source


def test_production_runner_preflights_fixed_scenario_and_failure_routes() -> None:
    source = production_runner_source()

    assert "/openapi.json" in source
    assert '"GET /api/v1/production-drills/{run_id}/state"' in source
    assert '"POST /api/v1/production-drills/{run_id}/scenarios/{scenario}"' in source
    assert '"POST /api/v1/production-drills/{run_id}/fail"' in source
    assert "preflight_openapi" in source
    assert "idempotency_prefix" in source
    assert "uuidgen" in source


def test_production_runner_keeps_bearer_material_out_of_process_arguments() -> None:
    source = production_runner_source()

    assert "bws secret get" in source
    assert "curl --config" in source
    assert "Authorization: Bearer ${DRILL_TOKEN}" not in source
    assert "redact" in source
    assert "authorization: bearer <redacted>" in source.lower()


def test_live_restart_is_an_explicit_two_phase_operator_handoff() -> None:
    source = production_runner_source()

    assert "APPROVE_LIVE_RESTART" in source
    assert "RESUME_AFTER_RESTART" in source
    assert "preflight_readiness" in source
    assert "Coolify operator handoff is required" in source
    assert "restart_live_application" not in source


def test_runner_rejects_a_non_uuid_run_id_before_any_network_request(tmp_path: Path) -> None:
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    network_log = tmp_path / "network.log"
    (mock_bin / "curl").write_text(
        "#!/bin/bash\nprintf 'unexpected network request\\n' >>\"$MOCK_NETWORK_LOG\"\nexit 1\n"
    )
    (mock_bin / "uuidgen").write_text(
        '#!/bin/bash\nprintf "00000000-0000-0000-0000-000000000002\\n"\n'
    )
    for path in mock_bin.iterdir():
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    result = subprocess.run(
        [str(RUNNER), "--run-id", 'not-a-uuid"\nurl = "https://unsafe.example'],
        check=False,
        capture_output=True,
        env={
            "PATH": f"{mock_bin}:{Path('/usr/bin')}:{Path('/bin')}",
            "MOCK_NETWORK_LOG": str(network_log),
        },
        text=True,
    )

    assert result.returncode == 2
    assert not network_log.exists()


def test_runner_posts_all_fixed_scenarios_and_records_run_scoped_assertions(tmp_path: Path) -> None:
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    evidence = tmp_path / "evidence.json"
    request_log = tmp_path / "requests.log"
    run_id = "00000000-0000-0000-0000-000000000001"
    credential = json.dumps(
        {"ORCHESTRATOR_PRODUCTION_DRILL_TOKEN": "test-token", "credential_key_id": "test-key"}
    )
    bws_response = json.dumps({"value": credential})
    openapi = json.dumps(
        {
            "paths": {
                "/health/ready": {"get": {}},
                "/api/v1/production-drills/{run_id}/state": {"get": {}},
                "/api/v1/production-drills/{run_id}/scenarios/{scenario}": {"post": {}},
                "/api/v1/production-drills/{run_id}/fail": {"post": {}},
            }
        }
    )

    (mock_bin / "bws").write_text(f"#!/bin/bash\nprintf '%s\\n' '{bws_response}'\n")
    (mock_bin / "uuidgen").write_text(
        '#!/bin/bash\nprintf "00000000-0000-0000-0000-000000000002\\n"\n'
    )
    (mock_bin / "curl").write_text(
        "#!/bin/bash\n"
        'config="${2:?expected curl --config FILE}"\n'
        'url=$(sed -n \'s/^url = "\\(.*\\)"$/\\1/p\' "$config")\n'
        'printf \'%s\\n\' "$url" >>"$MOCK_REQUEST_LOG"\n'
        'case "$url" in\n'
        f"  *'/openapi.json') printf '%s\\n' '{openapi}' ;;\n"
        "  *'/health/ready') printf '%s\\n' '{}' ;;\n"
        "  *'/scenarios/crash_recovery') printf '%s\\n' '{\"run_id\":\""
        + run_id
        + '","status":"asserting","units":[{"unit_key":"production-drill-crash_recovery","state":"claimed","attempt_count":1,"active_claim":{"attempt":1}}],"evidence":[],"observations":[],"deployment_observations":[],"conditions":[]}\' ;;\n'  # noqa: E501 - shell mock response must remain one JSON line
        "  *'/scenarios/evidence_recovery') printf '%s\\n' '{\"run_id\":\""
        + run_id
        + '","status":"asserting","units":[{"unit_key":"production-drill-evidence_recovery","state":"executing"}],"evidence":[{"is_head":false},{"is_head":true,"supersedes_evidence_id":"x"}],"observations":[],"deployment_observations":[],"conditions":[]}\' ;;\n'  # noqa: E501 - shell mock response must remain one JSON line
        "  *'/scenarios/external_pr_conflict') printf '%s\\n' '{\"run_id\":\""
        + run_id
        + '","status":"asserting","units":[{"unit_key":"production-drill-external_pr_conflict","state":"submitted"}],"evidence":[],"observations":[{},{}],"deployment_observations":[],"conditions":[{"condition_type":"external_merge_alarm","is_open":true}]}\' ;;\n'  # noqa: E501 - shell mock response must remain one JSON line
        "  *'/scenarios/deploy_split_brain') printf '%s\\n' '{\"run_id\":\""
        + run_id
        + '","status":"asserting","units":[{"unit_key":"production-drill-deploy_split_brain","state":"completed"}],"evidence":[],"observations":[],"deployment_observations":[{}],"conditions":[{"condition_type":"deploy_split_brain","is_open":true}]}\' ;;\n'  # noqa: E501 - shell mock response must remain one JSON line
        "  *'/scenarios/stalled_approval') printf '%s\\n' '{\"run_id\":\""
        + run_id
        + '","status":"asserting","units":[{"unit_key":"production-drill-stalled_approval","state":"awaiting_approval","active_claim":null}],"evidence":[],"observations":[],"deployment_observations":[],"conditions":[]}\' ;;\n'  # noqa: E501 - shell mock response must remain one JSON line
        "  *'/state') printf '%s\\n' '{\"run_id\":\""
        + run_id
        + '","status":"asserting","units":[],"evidence":[],"observations":[],"deployment_observations":[],"conditions":[]}\' ;;\n'  # noqa: E501 - shell mock response must remain one JSON line
        "  *) exit 1 ;;\n"
        "esac\n"
    )
    for path in mock_bin.iterdir():
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
            "MOCK_REQUEST_LOG": str(request_log),
        },
        text=True,
    )

    assert result.returncode == 4, result.stderr
    requests = request_log.read_text()
    assert "/scenarios/crash_recovery" in requests
    for scenario in SCENARIOS[1:]:
        assert f"/scenarios/{scenario}" not in requests
    payload = json.loads(evidence.read_text())
    assert payload["status"] == "restart_pending"
    assert [item["name"] for item in payload["assertions"]] == ["crash_recovery_prepared"]
    assert "test-token" not in evidence.read_text()


def test_runner_rejects_a_missing_openapi_operation_before_authenticated_mutation(
    tmp_path: Path,
) -> None:
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    evidence = tmp_path / "evidence.json"
    request_log = tmp_path / "requests.log"
    run_id = "00000000-0000-0000-0000-000000000001"
    openapi = json.dumps(
        {
            "paths": {
                "/health/ready": {"get": {}},
                "/api/v1/production-drills/{run_id}": {"get": {}},
                "/api/v1/production-drills/{run_id}/state": {"get": {}},
                "/api/v1/production-drills/{run_id}/scenarios/{scenario}": {"get": {}},
                "/api/v1/production-drills/{run_id}/fail": {"get": {}},
            }
        }
    )
    (mock_bin / "bws").write_text(
        "#!/bin/bash\nprintf 'credential lookup should not happen\\n' >&2\nexit 1\n"
    )
    (mock_bin / "uuidgen").write_text(
        '#!/bin/bash\nprintf "00000000-0000-0000-0000-000000000002\\n"\n'
    )
    (mock_bin / "curl").write_text(
        "#!/bin/bash\n"
        'config="${2:?expected curl --config FILE}"\n'
        'url=$(sed -n \'s/^url = "\\(.*\\)"$/\\1/p\' "$config")\n'
        'if grep -q \'^header = "Authorization:\' "$config"; then\n'
        '  printf \'authenticated %s\\n\' "$url" >>"$MOCK_REQUEST_LOG"\n'
        "  exit 1\n"
        "fi\n"
        'printf \'unauthenticated %s\\n\' "$url" >>"$MOCK_REQUEST_LOG"\n'
        f"case \"$url\" in\n  *'/openapi.json') printf '%s\\n' '{openapi}' ;;\n"
        "  *) exit 1 ;;\nesac\n"
    )
    for path in mock_bin.iterdir():
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
            "MOCK_REQUEST_LOG": str(request_log),
        },
        text=True,
    )

    assert result.returncode == 1
    assert "missing required operation: POST" in result.stderr
    assert request_log.read_text().splitlines() == [
        "unauthenticated https://sds.alobar.net/openapi.json"
    ]
    assert json.loads(evidence.read_text())["detail"] == "runner_preflight_failed"


def test_runner_posts_enumerated_redacted_failure_after_a_scenario_error(tmp_path: Path) -> None:
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    evidence = tmp_path / "evidence.json"
    request_log = tmp_path / "requests.log"
    payload_log = tmp_path / "payloads.log"
    run_id = "00000000-0000-0000-0000-000000000001"
    credential = json.dumps(
        {"ORCHESTRATOR_PRODUCTION_DRILL_TOKEN": "test-token", "credential_key_id": "test-key"}
    )
    bws_response = json.dumps({"value": credential})
    openapi = json.dumps(
        {
            "paths": {
                "/health/ready": {"get": {}},
                "/api/v1/production-drills/{run_id}/state": {"get": {}},
                "/api/v1/production-drills/{run_id}/scenarios/{scenario}": {"post": {}},
                "/api/v1/production-drills/{run_id}/fail": {"post": {}},
            }
        }
    )
    (mock_bin / "bws").write_text(f"#!/bin/bash\nprintf '%s\\n' '{bws_response}'\n")
    (mock_bin / "uuidgen").write_text(
        '#!/bin/bash\nprintf "00000000-0000-0000-0000-000000000002\\n"\n'
    )
    (mock_bin / "curl").write_text(
        "#!/bin/bash\n"
        'config="${2:?expected curl --config FILE}"\n'
        'url=$(sed -n \'s/^url = "\\(.*\\)"$/\\1/p\' "$config")\n'
        'printf \'%s\\n\' "$url" >>"$MOCK_REQUEST_LOG"\n'
        'payload=$(sed -n \'s/^data-binary = "@\\(.*\\)"$/\\1/p\' "$config")\n'
        'if [ -n "$payload" ]; then cat "$payload" >>"$MOCK_PAYLOAD_LOG"; printf \'\\n\' >>"$MOCK_PAYLOAD_LOG"; fi\n'  # noqa: E501 - shell mock preserves payload boundaries
        'case "$url" in\n'
        f"  *'/openapi.json') printf '%s\\n' '{openapi}' ;;\n"
        "  *'/health/ready') printf '%s\\n' '{}' ;;\n"
        '  *\'/scenarios/crash_recovery\') printf \'%s\\n\' \'{"run_id":"wrong-run","status":"asserting","units":[]}\' ;;\n'  # noqa: E501 - shell mock response must remain one JSON line
        '  *\'/fail\') printf \'%s\\n\' \'{"run_id":"failed","status":"failed"}\' ;;\n'
        "  *) exit 1 ;;\n"
        "esac\n"
    )
    for path in mock_bin.iterdir():
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
            "MOCK_REQUEST_LOG": str(request_log),
            "MOCK_PAYLOAD_LOG": str(payload_log),
        },
        text=True,
    )

    assert result.returncode == 1, result.stderr
    assert "/scenarios/crash_recovery" in request_log.read_text()
    assert "/fail" in request_log.read_text()
    failure = json.loads(payload_log.read_text().splitlines()[-1])
    assert failure["failure_code"] == "crash_recovery_failed"
    assert failure["diagnostic_ref"].startswith("drill://redacted/")
    assert "test-token" not in evidence.read_text()
