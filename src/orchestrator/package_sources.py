from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class PackageSourceError(Exception):
    pass


_HUMAN_OPERATOR_PROFILE = "human-operator-v1"
_CHAIN_VERIFY_TIMEOUT_SECONDS = 30
_EXECUTABLE_INTAKE_STATE = "approved"
_PROTOCOL_FIXTURE_STATE = "closed"


@dataclass(frozen=True)
class VerifiedApproval:
    approved_by: str
    approved_at: str
    approval_event_id: str
    approval_ledger_commit: str


class _NoDatesLoader(yaml.SafeLoader):
    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key_node, _value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    None,
                    None,
                    f"duplicate key {key!r} in mapping",
                    node.start_mark,
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


_NoDatesLoader.yaml_implicit_resolvers = {
    ch: [(tag, regexp) for (tag, regexp) in resolvers if tag != "tag:yaml.org,2002:timestamp"]
    for ch, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def canonical_package_hash(package: Mapping[str, object]) -> str:
    core = dict(package)
    core.pop("status", None)
    return hashlib.sha256(_canon(core).encode("utf-8")).hexdigest()


def _canon(value: object) -> str:
    if value is None or value is True or value is False:
        return {None: "null", True: "true", False: "false"}[value]
    if isinstance(value, float):
        raise PackageSourceError("floats are not allowed in intent packages")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return _canon_str(value)
    if isinstance(value, list):
        return "[" + ",".join(_canon(item) for item in value) + "]"
    if isinstance(value, Mapping):
        items = sorted(value.items(), key=lambda item: item[0].encode("utf-16-be"))
        return "{" + ",".join(f"{_canon_str(key)}:{_canon(item)}" for key, item in items) + "}"
    raise PackageSourceError(f"unhashable type in package: {type(value).__name__}")


def _canon_str(value: str) -> str:
    out = ['"']
    for char in value:
        if char == '"':
            out.append('\\"')
        elif char == "\\":
            out.append("\\\\")
        elif char == "\b":
            out.append("\\b")
        elif char == "\f":
            out.append("\\f")
        elif char == "\n":
            out.append("\\n")
        elif char == "\r":
            out.append("\\r")
        elif char == "\t":
            out.append("\\t")
        elif ord(char) < 0x20:
            out.append(f"\\u{ord(char):04x}")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PackageSourceError(f"could not read {path.name}: {exc}") from exc
    try:
        docs = list(yaml.load_all(text, Loader=_NoDatesLoader))
    except yaml.YAMLError as exc:
        raise PackageSourceError(f"invalid YAML in {path.name}: {exc}") from exc
    if len(docs) != 1:
        raise PackageSourceError(f"{path.name} must contain exactly one YAML document")
    value = docs[0]
    if not isinstance(value, dict):
        raise PackageSourceError(f"{path.name} must contain a mapping")
    return value


def _resolve_source_path(path: Path) -> Path:
    try:
        return path.resolve(strict=True)
    except OSError as exc:
        raise PackageSourceError(f"could not resolve source path {path}: {exc}") from exc


def _git_head(path: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "git rev-parse HEAD failed"
        raise PackageSourceError(f"could not read git provenance for {path}: {detail}")
    commit = result.stdout.strip()
    if not commit:
        raise PackageSourceError(f"could not read git provenance for {path}: empty git HEAD")
    return commit


def _intent_packages_src() -> Path | None:
    sibling = Path(__file__).resolve().parents[2].parent / "intent-packages" / "src"
    return sibling if sibling.is_dir() else None


def _verify_with_intent_packages_cli(path: Path) -> bool | None:
    sibling_src = _intent_packages_src()
    if sibling_src is None:
        return None
    env = dict(os.environ)
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{sibling_src}{os.pathsep}{existing_pythonpath}"
        if existing_pythonpath
        else str(sibling_src)
    )
    try:
        result = subprocess.run(
            [sys.executable, "-m", "intent_packages", "verify-approval", str(path)],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
    except OSError:
        return None
    return result.returncode == 0


def _security_registry_dir() -> Path | None:
    env_dir = os.environ.get("SECURITY_STANDARDS_DIR")
    if env_dir:
        candidate = Path(env_dir) / "registry"
        return candidate if candidate.is_dir() else None
    candidate = Path.home() / "Projects" / "security-standards" / "registry"
    return candidate if candidate.is_dir() else None


def _is_human_operator(agent_id: str) -> bool:
    registry_dir = _security_registry_dir()
    if registry_dir is None:
        return False
    agent_path = registry_dir / "agents" / f"{agent_id}.yaml"
    if not agent_path.is_file():
        return False
    data = _read_yaml(agent_path)
    return data.get("authority_profile") == _HUMAN_OPERATOR_PROFILE


def _security_standards_dir() -> Path | None:
    env_dir = os.environ.get("SECURITY_STANDARDS_DIR")
    if env_dir:
        candidate = Path(env_dir)
        return candidate if candidate.is_dir() else None
    candidate = Path.home() / "Projects" / "security-standards"
    return candidate if candidate.is_dir() else None


def _factory_events_file() -> Path:
    home = Path(os.environ.get("FACTORY_EVENTS_HOME", str(Path.home() / ".factory")))
    return home / "events.jsonl"


def _verify_factory_chain() -> bool:
    sec_std_dir = _security_standards_dir()
    if sec_std_dir is None:
        return False
    src_dir = sec_std_dir / "src"
    env = dict(os.environ)
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{src_dir}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else str(src_dir)
    )
    try:
        result = subprocess.run(
            [sys.executable, "-m", "factory_events", "verify"],
            capture_output=True,
            text=True,
            env=env,
            timeout=_CHAIN_VERIFY_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _matching_event_evidence(
    evidence: object,
    *,
    approved_hash: str,
    revision: int,
    approver: str,
    commit: str,
) -> bool:
    if not isinstance(evidence, list):
        return False
    return any(
        isinstance(item, dict)
        and item.get("approved_hash") == approved_hash
        and item.get("revision") == revision
        and item.get("approver") == approver
        and item.get("commit") == commit
        for item in evidence
    )


def _iter_package_approval_events() -> list[dict[str, Any]] | None:
    events_file = _factory_events_file()
    if not events_file.is_file():
        return None
    try:
        text = events_file.read_text(encoding="utf-8")
    except OSError:
        return None
    events: list[dict[str, Any]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        event = obj.get("event", obj)
        if isinstance(event, dict) and event.get("action") == "package.approved":
            events.append(event)
    return events


def _approval_event_to_verified(
    event: Mapping[str, Any],
    *,
    package_id: str,
    approved_hash: str,
    revision: int,
    approver: str,
    commit: str,
    event_id: str,
) -> VerifiedApproval | None:
    if event.get("event_id") != event_id:
        return None
    source = event.get("source")
    if not isinstance(source, dict) or source.get("ref") != package_id:
        return None
    if not _matching_event_evidence(
        event.get("evidence"),
        approved_hash=approved_hash,
        revision=revision,
        approver=approver,
        commit=commit,
    ):
        return None
    approved_at = event.get("timestamp")
    if not isinstance(approved_at, str) or not approved_at:
        return None
    return VerifiedApproval(
        approved_by=approver,
        approved_at=approved_at,
        approval_event_id=event_id,
        approval_ledger_commit=commit,
    )


def _verified_approval_from_events(
    *,
    package_id: str,
    approved_hash: str,
    revision: int,
    approver: str,
    commit: str,
    event_id: str,
) -> VerifiedApproval | None:
    if not isinstance(revision, int):
        return None
    events = _iter_package_approval_events()
    if events is None:
        return None
    for event in events:
        verified = _approval_event_to_verified(
            event,
            package_id=package_id,
            approved_hash=approved_hash,
            revision=revision,
            approver=approver,
            commit=commit,
            event_id=event_id,
        )
        if verified is not None:
            return verified
    return None


def _verify_current_approval(
    path: Path,
    package_id: str,
    approved_hash: str,
    revision: object,
    approval: Mapping[str, Any],
) -> VerifiedApproval | None:
    approver = approval.get("approver")
    commit = approval.get("commit")
    event_id = approval.get("event_id")
    if not isinstance(revision, int):
        return None
    if not isinstance(approver, str) or not _is_human_operator(approver):
        return None
    if not isinstance(commit, str) or not commit:
        return None
    if not isinstance(event_id, str) or not event_id:
        return None
    sibling_verification = _verify_with_intent_packages_cli(path)
    if sibling_verification is not True:
        return None
    if not _verify_factory_chain():
        return None
    return _verified_approval_from_events(
        package_id=package_id,
        approved_hash=approved_hash,
        revision=revision,
        approver=approver,
        commit=commit,
        event_id=event_id,
    )


def _matching_approvals(
    approvals: object,
    *,
    approved_hash: str,
    revision: object,
) -> list[dict[str, Any]]:
    if not isinstance(approvals, list):
        raise PackageSourceError("lineage approvals must be a list")
    matching: list[dict[str, Any]] = []
    required = ("revision", "approved_hash", "approver", "approved_at", "commit", "event_id")
    for item in approvals:
        if not isinstance(item, dict):
            raise PackageSourceError("lineage approval entries must be mappings")
        missing = [field for field in required if field not in item]
        if missing:
            raise PackageSourceError(f"lineage approval entry missing {missing[0]}")
        if item.get("revision") == revision and item.get("approved_hash") == approved_hash:
            matching.append(item)
    return matching


def _acceptance_criteria(acceptance: object) -> list[dict[str, object]]:
    if not isinstance(acceptance, list):
        raise PackageSourceError("package acceptance must be a list")
    criteria: list[dict[str, object]] = []
    required = ("id", "condition", "evidence_type", "evidence", "approver")
    for item in acceptance:
        if not isinstance(item, dict):
            raise PackageSourceError("package acceptance entries must be mappings")
        missing = [field for field in required if field not in item]
        if missing:
            raise PackageSourceError(f"package acceptance entry missing {missing[0]}")
        criteria.append(
            {
                "ac_id": item["id"],
                "condition": item["condition"],
                "evidence_type": item["evidence_type"],
                "evidence": item["evidence"],
                "approver": item["approver"],
            }
        )
    return criteria


def _validate_approved_current_state(
    package: Mapping[str, Any],
    lineage: Mapping[str, Any],
) -> None:
    _validate_current_state(
        package,
        lineage,
        expected_state=_EXECUTABLE_INTAKE_STATE,
        error="package current revision is not approved for intake",
    )


def _validate_closed_current_state(
    package: Mapping[str, Any],
    lineage: Mapping[str, Any],
) -> None:
    _validate_current_state(
        package,
        lineage,
        expected_state=_PROTOCOL_FIXTURE_STATE,
        error="package current revision is not closed for protocol fixture intake",
    )


def _validate_current_state(
    package: Mapping[str, Any],
    lineage: Mapping[str, Any],
    *,
    expected_state: str,
    error: str,
) -> None:
    status = package.get("status")
    current_state = lineage.get("current_state")
    if status != current_state:
        raise PackageSourceError("package status does not match lineage current_state")
    if status != expected_state:
        raise PackageSourceError(error)


def load_package_intake_payload(path: Path, *, source_repository: str) -> dict[str, object]:
    resolved_path = _resolve_source_path(path)
    package = _read_yaml(resolved_path / "package.yaml")
    lineage = _read_yaml(resolved_path / "lineage.yaml")
    _validate_approved_current_state(package, lineage)
    return _load_intake_payload(
        resolved_path,
        package,
        lineage,
        source_repository=source_repository,
        intake_purpose="executable",
        protocol_fixture_only=False,
    )


def load_protocol_fixture_intake_payload(
    path: Path,
    *,
    source_repository: str,
) -> dict[str, object]:
    resolved_path = _resolve_source_path(path)
    package = _read_yaml(resolved_path / "package.yaml")
    lineage = _read_yaml(resolved_path / "lineage.yaml")
    _validate_closed_current_state(package, lineage)
    return _load_intake_payload(
        resolved_path,
        package,
        lineage,
        source_repository=source_repository,
        intake_purpose="protocol_fixture",
        protocol_fixture_only=True,
    )


def _load_intake_payload(
    resolved_path: Path,
    package: dict[str, Any],
    lineage: dict[str, Any],
    *,
    source_repository: str,
    intake_purpose: str,
    protocol_fixture_only: bool,
) -> dict[str, object]:
    acceptance_criteria = _acceptance_criteria(package.get("acceptance"))
    revision = package.get("revision")
    approved_hash = canonical_package_hash(package)
    matching_approvals = _matching_approvals(
        lineage.get("approvals"),
        approved_hash=approved_hash,
        revision=revision,
    )
    if not matching_approvals:
        raise PackageSourceError("package revision has no matching approval")
    if len(matching_approvals) != 1:
        raise PackageSourceError("package revision has ambiguous matching approvals")
    approval = matching_approvals[0]
    verified_approval = _verify_current_approval(
        resolved_path,
        str(package["package_id"]),
        approved_hash,
        revision,
        approval,
    )
    if verified_approval is None:
        raise PackageSourceError("approval verification failed")
    return {
        "package_id": package["package_id"],
        "source_repository": source_repository,
        "revision": revision,
        "content_hash": approved_hash,
        "source_path": str(resolved_path),
        "source_commit": _git_head(resolved_path),
        "approved_by": verified_approval.approved_by,
        "approved_at": verified_approval.approved_at,
        "approval_event_id": verified_approval.approval_event_id,
        "approval_ledger_commit": verified_approval.approval_ledger_commit,
        "profile": package.get("profile"),
        "status_at_intake": package["status"],
        "intake_purpose": intake_purpose,
        "verification_mode": "caller_attested_cli_verified",
        "verification_limitations": {
            "api_recomputes_remote_git_object": False,
            "cli_verified_local_package_hash": True,
            "cli_verified_approval_lineage": True,
            "protocol_fixture_only": protocol_fixture_only,
        },
        "enforcement_snapshot": {
            "title": package["title"],
            "outcome": package["outcome"],
            "scope": package["scope"],
            "dependencies": package["dependencies"],
            "profile_fields": package.get("profile_fields"),
            "applicable_standards": package["applicable_standards"],
        },
        "authority": package["authority"],
        "registry_version": 1,
        "acceptance_criteria": acceptance_criteria,
    }
