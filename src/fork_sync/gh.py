"""Every external command this program runs goes through one injectable runner.

Tests replace the runner, so no test reaches GitHub, git or the network. The real one is
`subprocess` with the operator's own `gh` login and git credentials.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

Runner = Callable[[Sequence[str], str | None, Path | None], str]
"""`(argv, stdin, cwd) -> stdout`; raises `CommandFailed` on a non-zero exit."""


class CommandFailed(RuntimeError):
    def __init__(self, argv: Sequence[str], returncode: int, stderr: str) -> None:
        super().__init__(f"{' '.join(argv[:3])} ... exited {returncode}: {stderr.strip()[:300]}")
        self.returncode = returncode
        self.stderr = stderr


def subprocess_runner(argv: Sequence[str], stdin: str | None, cwd: Path | None) -> str:
    result = subprocess.run(list(argv), input=stdin, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise CommandFailed(argv, result.returncode, result.stderr)
    return result.stdout


def gh_api(run: Runner, path: str, *, raw: bool = False) -> Any | None:
    """GET a GitHub API path. `None` when GitHub answers 404; raw text when `raw`."""
    argv = ["gh", "api", path]
    if raw:
        argv[2:2] = ["-H", "Accept: application/vnd.github.raw"]
    try:
        out = run(argv, None, None)
    except CommandFailed as failure:
        if "HTTP 404" in failure.stderr:
            return None
        raise
    return out if raw else json.loads(out)


def gh_write(run: Runner, method: str, path: str, body: dict[str, Any]) -> None:
    run(["gh", "api", "-X", method, path, "--input", "-"], json.dumps(body), None)
