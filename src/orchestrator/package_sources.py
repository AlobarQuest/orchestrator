from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml


class PackageSourceError(Exception):
    pass


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


def _git_head(path: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def load_package_intake_payload(path: Path, *, source_repository: str) -> dict[str, object]:
    package = _read_yaml(path / "package.yaml")
    lineage = _read_yaml(path / "lineage.yaml")
    approvals = lineage.get("approvals")
    if not isinstance(approvals, list):
        raise PackageSourceError("lineage approvals must be a list")
    revision = package.get("revision")
    approved_hash = canonical_package_hash(package)
    approval = next(
        (
            item
            for item in approvals
            if isinstance(item, dict)
            and item.get("revision") == revision
            and item.get("approved_hash") == approved_hash
        ),
        None,
    )
    if not isinstance(approval, dict):
        raise PackageSourceError("package revision has no matching approval")
    acceptance = package.get("acceptance")
    if not isinstance(acceptance, list):
        raise PackageSourceError("package acceptance must be a list")
    return {
        "package_id": package["package_id"],
        "source_repository": source_repository,
        "revision": revision,
        "content_hash": approved_hash,
        "source_path": str(path),
        "source_commit": _git_head(path),
        "approved_by": approval["approver"],
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
