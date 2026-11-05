"""The API's error status map and body, shared by every handler that answers an error."""

# Nothing about the request is wrong: this process cannot answer it right now, either because of
# its own configuration or because something it must read is unreachable. A 409 would tell the
# caller to change the request, which is the wrong instruction.
_UNAVAILABLE = frozenset(  # not-a-vocabulary: this API's own status classes, not a wire enum
    {
        "csrf_unavailable",
        "factory_policy_invalid",
        "factory_policy_version_unsupported",
        "named_check_observation_unavailable",
    }
)

# ADR-0027 split intake's role refusal out of `human_actor_required`, and a refusal that left
# this set would have regressed 403 -> 409 -- telling a worker credential to change its request
# rather than that it may not make one. `intake_change_record_required` is deliberately NOT here:
# that actor MAY register, and what is wrong is the request.
_FORBIDDEN = frozenset(  # not-a-vocabulary: as above
    {
        "role_forbidden",
        "human_actor_required",
        "csrf_rejected",
        "intake_registrar_invalid",
    }
)


def status_for(code: str) -> int:
    """The HTTP status a domain error code is answered with."""
    if code in _FORBIDDEN:
        return 403
    if code in _UNAVAILABLE:
        return 503
    if code.endswith("_not_found"):
        return 404
    return 409


def error_body(code: str, message: str, **detail: object) -> dict[str, object]:
    """The wire error body; a detail whose value is `None` is left out."""
    return {
        "error": {
            "code": code,
            "message": message,
            **{name: value for name, value in detail.items() if value is not None},
        }
    }


def authentication_message(code: str) -> str:
    """The text a 401 carries for an authentication refusal code."""
    if code == "authentication_required":
        return "valid authentication credentials are required"
    return "authentication credentials were rejected"
