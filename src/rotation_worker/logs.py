"""Structured JSON logs on stderr, every line scrubbed of every value the process has handled.

Code Brain's `error-logging` road: JSON lines, never a secret. They go to stderr, not the road's
stdout, because stdout is `emit-secret`'s value channel: anything else written there would reach
the host's `set-root-token --stdin` as the value. The systemd unit sets neither stream, so the
journal takes both and a pass's lines read the same. The executor never logs a value on purpose;
`values.scrub` is the net behind that, applied to the whole rendered line.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Final

from rotation_worker.values import scrub

# Attributes every LogRecord has; anything else on a record came from `extra=`.
_STANDARD: Final = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
    "message",
    "asctime",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname.lower(),
            "event": record.getMessage(),
        }
        line.update({k: v for k, v in vars(record).items() if k not in _STANDARD})
        if record.exc_info:
            line["error"] = self.formatException(record.exc_info)
        # Each field is scrubbed before JSON escapes it, when a value still reads as itself; the
        # whole line is scrubbed again after, for anything a field's str() assembled.
        scrubbed = {key: scrub(str(value)) for key, value in line.items()}
        return scrub(json.dumps(scrubbed))


def configure() -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    # httpx logs every request's URL at info, and an OpenRouter key's URL carries its hash.
    logging.getLogger("httpx").setLevel(logging.WARNING)
