"""A bump's decomposition: `factory decompose --no-execute`, run against this lane's mirror.

intent-packages' generator is the one author of a bump envelope, and the orchestrator holds what
it wrote to the generator's own forms (`bump_binding`), so nothing is reconstructed here.
`--no-execute` reads files and asks the conformance scanner, the Brains and the orchestrator; it
never runs a repository command, because installing a bumped dependency runs its lifecycle scripts
where infrastructure credentials live (Devon, 2026-10-30).

The bump's terms are read from the revision's own `profile_fields`, passed exactly as registered,
since the orchestrator's policy branch binds the commands to those values. Anything that stops the
generator is `DecomposeRefused`, a conformance claim it could not produce included: a placeholder
would pass to the dispatch gate as though someone had measured it.

**`factory` holds no bearer and no GitHub token.** Its conformance claim runs `code-standards`
inside the mirror with its whole environment, so that environment is an allowlist. This lane hands
over what those credentials would fetch: the intake it already read, as a file, and its own proof of
the checkout (`mirror.fresh`), with `--assume-checkout-current`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Final

from sds_members.holds import TOOLING_TO_DEPENDABOT, ecosystem_of_package_id

# The one criterion a bump package's generator writes.
AC_ID: Final = "AC-001"
PROFILE: Final = "dependency-update"
TIMEOUT_SECONDS: Final = 300
_TOOLING: Final = {ecosystem: tooling for tooling, ecosystem in TOOLING_TO_DEPENDABOT.items()}
_DETAIL_CHARS: Final = 300

Run = Callable[..., "subprocess.CompletedProcess[str]"]


class DecomposeRefused(RuntimeError):
    """The generator didn't produce a proposal; the reason says what stopped it."""


def tooling_of(package_id: str, repository: str) -> str | None:
    """The generator's `--tooling` word, or `None` for an id naming none: Dependabot's ecosystem
    for npm is `npm_and_yarn`, which the generator doesn't take."""
    ecosystem = ecosystem_of_package_id(package_id, repository)
    return None if ecosystem is None else _TOOLING[ecosystem]


def profile_fields(intake: Mapping[str, Any]) -> Mapping[str, Any]:
    """The revision's `profile_fields`, served in its enforcement snapshot; empty when absent."""
    snapshot = intake.get("enforcement_snapshot")
    fields = snapshot.get("profile_fields") if isinstance(snapshot, Mapping) else None
    return fields if isinstance(fields, Mapping) else {}


def factory_binary(home: Path) -> str | None:
    """`factory`, from PATH or `~/.local/bin`: launchd's PATH names only what the launcher adds."""
    fallback = home / ".local" / "bin" / "factory"
    return shutil.which("factory") or (str(fallback) if fallback.exists() else None)


class Generator:
    def __init__(
        self,
        binary: str | None,
        environment: Mapping[str, str],
        *,
        run: Run = subprocess.run,
    ) -> None:
        self._binary = binary
        self._environment = environment
        self._run = run

    def __call__(
        self, intake: Mapping[str, Any], repository: str, checkout: Path
    ) -> dict[str, Any]:
        """The proposal body, without its idempotency key, which is this lane's to set."""
        if self._binary is None:
            raise DecomposeRefused("no factory binary on PATH or in ~/.local/bin")
        with tempfile.TemporaryDirectory(prefix="bump-driver-") as scratch:
            intake_file = Path(scratch) / "intake.json"
            intake_file.write_text(json.dumps(intake), encoding="utf-8")
            out = Path(scratch) / "proposal.json"
            argv = self._argv(self._binary, intake, repository, checkout, intake_file, out)
            self._generate(argv, Path(scratch))
            return _proposal(out)

    def _argv(
        self,
        binary: str,
        intake: Mapping[str, Any],
        repository: str,
        checkout: Path,
        intake_file: Path,
        out: Path,
    ) -> list[str]:
        fields = profile_fields(intake)
        terms = [fields.get(name) for name in ("package", "from_version", "to_version")]
        if not all(isinstance(term, str) and term for term in terms):
            raise DecomposeRefused("the revision's profile_fields don't name the bump")
        tooling = tooling_of(str(intake.get("package_id")), repository)
        if tooling is None:
            raise DecomposeRefused(f"{intake.get('package_id')} names no tooling")
        package, old, new = terms
        return [
            binary,
            "decompose",
            "--revision",
            str(intake.get("id")),
            "--ac",
            AC_ID,
            "--target-repo",
            repository,
            "--repo-path",
            str(checkout),
            "--tooling",
            tooling,
            "--package",
            str(package),
            "--from",
            str(old),
            "--to",
            str(new),
            "--out",
            str(out),
            "--intake-file",
            str(intake_file),
            "--no-execute",
            "--assume-checkout-current",
        ]

    def _generate(self, argv: list[str], cwd: Path) -> None:
        try:
            done = self._run(
                argv,
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
                env=dict(self._environment),
                cwd=cwd,
            )
        except subprocess.TimeoutExpired:
            raise DecomposeRefused("factory decompose timed out") from None
        except OSError as error:
            raise DecomposeRefused(f"factory could not start: {type(error).__name__}") from None
        if done.returncode != 0:
            lines = (done.stderr or done.stdout or "").strip().splitlines()
            reason = lines[-1][:_DETAIL_CHARS] if lines else "no output"
            raise DecomposeRefused(f"factory exited {done.returncode}: {reason}")


def _proposal(out: Path) -> dict[str, Any]:
    try:
        body = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise DecomposeRefused(
            f"factory wrote no readable proposal: {type(error).__name__}"
        ) from None
    if not isinstance(body, dict) or not body.get("proposed_units"):
        raise DecomposeRefused("factory's proposal names no unit")
    body.pop("idempotency_key", None)
    return body
