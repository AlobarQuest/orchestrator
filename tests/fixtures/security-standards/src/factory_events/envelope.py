import hashlib
import uuid

import jsonschema

SCHEMA_VERSION = "factory-event/v1"

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema",
        "event_id",
        "timestamp",
        "actor",
        "action",
        "result",
        "evidence",
        "source",
    ],
    "properties": {
        "schema": {"const": SCHEMA_VERSION},
        "event_id": {"type": "string", "pattern": "^evt-[0-9a-f]{32,64}$"},
        "timestamp": {
            "type": "string",
            "pattern": "^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(\\.\\d{1,6})?Z$",
        },
        "actor": {"type": "string", "minLength": 1},
        "action": {"type": "string", "pattern": "^[a-z0-9_]+\\.[a-z0-9_.\\-]+$"},
        "target": {"type": ["string", "null"]},
        "work_package": {"type": ["string", "null"]},
        "input_revision": {"type": ["string", "null"]},
        "result": {"enum": ["success", "failure", "unknown"]},
        "evidence": {"type": "array", "items": {"type": "object"}},
        "authority_grant": {"type": ["object", "string", "null"]},
        "correlation_id": {"type": ["string", "null"]},
        "source": {
            "type": "object",
            "additionalProperties": False,
            "required": ["system", "ref"],
            "properties": {
                "system": {
                    "enum": ["high-power-audit", "change-manager", "orchestrator", "direct"]
                },
                "ref": {"type": "string"},
            },
        },
    },
}


class EnvelopeError(ValueError):
    pass


def deterministic_event_id(system: str, payload: str) -> str:
    return "evt-" + hashlib.sha256(f"{system}:{payload}".encode()).hexdigest()


def new_event_id() -> str:
    return "evt-" + uuid.uuid4().hex


def validate_event(event: dict) -> None:
    errors = sorted(jsonschema.Draft202012Validator(SCHEMA).iter_errors(event), key=str)
    if errors:
        raise EnvelopeError(errors[0].message)


def make_event(
    *,
    actor: str,
    action: str,
    result: str,
    source: dict,
    timestamp: str,
    target: str | None = None,
    work_package: str | None = None,
    input_revision: str | None = None,
    evidence: list[dict] | None = None,
    authority_grant: dict | str | None = None,
    correlation_id: str | None = None,
    event_id: str | None = None,
) -> dict:
    from agent_registry.registry import registered_ids

    if actor not in registered_ids():
        raise EnvelopeError(f"actor {actor!r} is not a registered agent_id")
    event = {
        "schema": SCHEMA_VERSION,
        "event_id": event_id or new_event_id(),
        "timestamp": timestamp,
        "actor": actor,
        "action": action,
        "target": target,
        "work_package": work_package,
        "input_revision": input_revision,
        "result": result,
        "evidence": evidence or [],
        "authority_grant": authority_grant,
        "correlation_id": correlation_id,
        "source": source,
    }
    validate_event(event)
    return event
