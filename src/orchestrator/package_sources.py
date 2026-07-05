from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml


class PackageSourceError(Exception):
    pass


_HUMAN_OPERATOR_PROFILE = "human-operator-v1"
_CHAIN_VERIFY_TIMEOUT_SECONDS = 30


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


def _events_file_has_matching_approval(approved_hash: str, revision: object) -> bool:
    if not isinstance(revision, int):
        return False
    events_file = _factory_events_file()
    if not events_file.is_file():
        return False
    try:
        text = events_file.read_text(encoding="utf-8")
    except OSError:
        return False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        event = obj.get("event", obj)
        if event.get("action") != "package.approved":
            continue
        evidence = event.get("evidence")
        if not isinstance(evidence, list):
            continue
        if any(
            isinstance(item, dict)
            and item.get("approved_hash") == approved_hash
            and item.get("revision") == revision
            for item in evidence
        ):
            return True
    return False


def _verify_current_approval(
    path: Path,
    approved_hash: str,
    revision: object,
    approver: object,
) -> bool:
    sibling_verification = _verify_with_intent_packages_cli(path)
    if sibling_verification is not None:
        return sibling_verification
    if not isinstance(approver, str) or not _is_human_operator(approver):
        return False
    if not _verify_factory_chain():
        return False
    return _events_file_has_matching_approval(approved_hash, revision)


def _matching_approvals(
    approvals: object,
    *,
    approved_hash: str,
    revision: object,
) -> list[dict[str, Any]]:
    if not isinstance(approvals, list):
        raise PackageSourceError("lineage approvals must be a list")
    return [
        item
        for item in approvals
        if isinstance(item, dict)
        and item.get("revision") == revision
        and item.get("approved_hash") == approved_hash
    ]


def load_package_intake_payload(path: Path, *, source_repository: str) -> dict[str, object]:
    resolved_path = _resolve_source_path(path)
    package = _read_yaml(resolved_path / "package.yaml")
    lineage = _read_yaml(resolved_path / "lineage.yaml")
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
    acceptance = package.get("acceptance")
    if not isinstance(acceptance, list):
        raise PackageSourceError("package acceptance must be a list")
    approver = approval.get("approver")
    if not _verify_current_approval(resolved_path, approved_hash, revision, approver):
        raise PackageSourceError("approval verification failed")
    return {
        "package_id": package["package_id"],
        "source_repository": source_repository,
        "revision": revision,
        "content_hash": approved_hash,
        "source_path": str(resolved_path),
        "source_commit": _git_head(resolved_path),
        "approved_by": approver,
        "approved_at": approval["approved_at"],
        "approval_event_id": approval["event_id"],
        "approval_ledger_commit": approval.get("commit"),
        "profile": package.get("profile"),
        "status_at_intake": package["status"],
        "verification_mode": "caller_attested_cli_verified",
        "verification_limitations": {
            "api_recomputes_remote_git_object": False,
            "cli_verified_local_package_hash": True,
            "cli_verified_approval_lineage": True,
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
        "acceptance_criteria": [
            {
                "ac_id": item["id"],
                "condition": item["condition"],
                "evidence_type": item["evidence_type"],
                "evidence": item["evidence"],
                "approver": item["approver"],
            }
            for item in acceptance
            if isinstance(item, dict)
        ],
    }
