"""The checkouts the generator reads: shallow clones at `origin/main`, written by git alone.

**No code of the repository runs here, and nothing here installs anything.** Every command is one
of git's clone, fetch, reset and clean. Hooks are off through the environment, so the generator's
own `git fetch` inside a mirror is covered by the same setting, and the user's and the system's git
config are not read at all.

**The token is never an argument and never in `.git/config`.** The remote is the bare HTTPS URL;
the token rides `GIT_CONFIG_*` environment variables as an `http.extraheader` scoped to GitHub, so
`fresh`'s commands and the generator's fetch both authenticate, and a `ps` of either shows nothing.

A repository outside the member record is refused before any command runs. `granted` maps the
case-folded name to the record's spelling, which is the spelling a mirror's path is built from.
"""

from __future__ import annotations

import base64
import os
import re
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Final

_REPOSITORY: Final = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9][A-Za-z0-9._-]*$")
GITHUB: Final = "https://github.com/"
BRANCH: Final = "main"
TIMEOUT_SECONDS: Final = 300
STATE_DIRECTORY: Final = ("Library", "Application Support", "sds", "bump-driver")

Run = Callable[..., "subprocess.CompletedProcess[str]"]


class MirrorRefused(RuntimeError):
    """This repository is no mirror's to hold: a person decides why it was asked for."""


class MirrorFailed(RuntimeError):
    """git could not bring the mirror up: usually the network, and the next pass tries again."""


def state_directory(home: Path) -> Path:
    return home.joinpath(*STATE_DIRECTORY)


class Mirrors:
    def __init__(
        self,
        root: Path,
        granted: Mapping[str, str],
        *,
        token: str,
        base_url: str = GITHUB,
        environ: Mapping[str, str] = os.environ,
        run: Run = subprocess.run,
    ) -> None:
        self._root = root
        self._granted = granted
        self._token = token
        self._base_url = base_url
        self._environ = environ
        self._run = run

    def environment(self) -> dict[str, str]:
        """The git environment of every command here and of the generator's own fetch."""
        credential = base64.b64encode(f"x-access-token:{self._token}".encode()).decode()
        pairs = (
            ("core.hooksPath", "/dev/null"),
            (f"http.{GITHUB}.extraheader", f"AUTHORIZATION: basic {credential}"),
        )
        return {
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_COUNT": str(len(pairs)),
            **{f"GIT_CONFIG_KEY_{n}": key for n, (key, _) in enumerate(pairs)},
            **{f"GIT_CONFIG_VALUE_{n}": value for n, (_, value) in enumerate(pairs)},
        }

    def fresh(self, repository: str) -> Path:
        """The mirror of `repository`, exactly `origin/main`, clean."""
        spelled = self._granted.get(repository.casefold())
        if spelled is None or _REPOSITORY.fullmatch(spelled) is None:
            raise MirrorRefused(f"{repository} is not a granted member")
        path = self._root / spelled
        if not (path / ".git").is_dir():
            path.parent.mkdir(parents=True, exist_ok=True)
            self._git(
                "clone",
                "--depth=1",
                "--single-branch",
                f"--branch={BRANCH}",
                f"{self._base_url}{spelled}.git",
                str(path),
            )
            return path
        self._git("-C", str(path), "fetch", "--depth=1", "origin", BRANCH)
        self._git("-C", str(path), "reset", "--hard", f"origin/{BRANCH}")
        self._git("-C", str(path), "clean", "-ffdx")
        return path

    def _git(self, *args: str) -> None:
        verb = next(arg for arg in args if arg in {"clone", "fetch", "reset", "clean"})
        keywords: dict[str, Any] = {
            "capture_output": True,
            "text": True,
            "timeout": TIMEOUT_SECONDS,
            "env": {
                "PATH": self._environ.get("PATH", ""),
                "HOME": self._environ.get("HOME", ""),
                **self.environment(),
            },
        }
        try:
            done = self._run(["git", *args], **keywords)
        except subprocess.TimeoutExpired:
            raise MirrorFailed(f"git {verb} timed out") from None
        except OSError as error:
            raise MirrorFailed(f"git {verb} could not start: {type(error).__name__}") from None
        if done.returncode != 0:
            # git's own last line. The token is in no URL and no argument, so it can't echo it.
            lines = (done.stderr or "").strip().splitlines()
            raise MirrorFailed(
                f"git {verb} exited {done.returncode}: {lines[-1] if lines else 'no output'}"
            )
