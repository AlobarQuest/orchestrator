"""claude-octopus: run every inline Python interpreter isolated (`-I`).

THE FINDING. With `-c`, `-m`, `-` or a script on stdin, Python puts the CURRENT DIRECTORY first on
`sys.path`. Claude Code runs a plugin's hooks from the session's project directory, so a
repository's own `json.py` is imported, and runs as Devon, by the first `python3 -c "import json"`
a hook makes, with no prompt. `-I` removes that entry on every Python from 3.4, the macOS system
3.9 included; `PYTHONSAFEPATH` would not, because 3.9 ignores it.

A SCRIPT RUN IS LEFT ALONE. `python3 path/to/x.py` puts the script's own directory first, which is
the plugin's, and several of octo's helpers import their siblings from it (`pid-ledger.py` imports
`process_control`). `-I` would remove that entry and break them. So the check flags only the
inline forms, and the fix inserts `-I` only where the inline form is written out.

HOW A SITE IS READ, AND WHERE IT OVER-COUNTS. An interpreter word (`python`, `python3`, `python3.N`,
alone or ending a path) is followed by its options, and the first token that is not an option
decides. Four readings:

- NOT A RUN: a script operand (`python3 x.py`), `--version` or `--help`, a word after `command -v`,
  `which`, `type` or `hash`, or a line whose first non-blank character is `#`.
- ISOLATED: `-I` among the options.
- FIXABLE, and the fix inserts `-I` after the word: `-c`, `-` or a `<` redirect; `-m` with a
  standard-library module; or nothing at all after a word that a `|` feeds.
- UNCLEAR, flagged and never rewritten: anything else that may be a run. Shell punctuation or
  the end of the line (stdin), a backslash continuation, an option the reading doesn't know,
  `-m` with a module outside the standard library (`-I` drops user site-packages, so the fix
  could break it), a quoted word (`"python3" -c`, where `-I` inside the quotes would join it to
  the word), a list element (`["python3", "-c"]`), and an interpreter found by a lookup
  (`$(command -v python3) -c`).

Any line that names `PYTHONPATH` or `PYTHONHOME` is flagged too: `-I` ignores both, so a run that
relies on them would change behaviour silently. A flagged line that is not a run holds until its
exact line is listed in `READ_LINES`.

STATED LIMITS. An interpreter reached through a variable (`"$PY" -c`) is invisible to any reading
of the text; octo's one such path runs scripts only (`_octo_pid_python_run`). A `-c` body that
imports a package from user site-packages would break under `-I`; at v11.15.1 every inline body
imports only the standard library (measured 2026-10-25).
"""

from __future__ import annotations

import re
import sys
from typing import Final

from fork_harden.read_lines import ReadLines, is_read

_INTERPRETER: Final = re.compile(r"(?<![\w.$-])python(?:3(?:\.\d+)?)?(?![\w.-])")
_LOOKUP: Final = re.compile(r"(?:\bcommand\s+-v|\bwhich|\btype|\bhash)\s+(?:\S*/)?$")
_PIPED: Final = re.compile(r"\|\s*(?:\S*/)?$")
_ENVIRONMENT: Final = re.compile(r"\bPYTHON(?:PATH|HOME)\b")
_OPTION_CLUSTER: Final = re.compile(r"-[A-Za-z]+")
_ATTACHED_VALUE: Final = re.compile(r"-[WX]\S+")
_PUNCTUATION: Final = frozenset("|;&)<>`")  # not-a-vocabulary: shell characters, read here only
_TAKES_ARGUMENT: Final = frozenset("WXQ")  # not-a-vocabulary: Python's own options with a value
_QUOTES: Final = frozenset("\"'")  # not-a-vocabulary: the two shell quote characters
_TERMINAL: Final = frozenset({"--version", "--help", "-V", "-h"})  # not-a-vocabulary: no code runs

NOT_A_RUN: Final = "not a run"
ISOLATED: Final = "isolated"
FIXABLE: Final = "fixable"
UNCLEAR: Final = "unclear"

READ_LINES: Final[ReadLines] = {
    (
        "scripts/lib/pid-ledger.sh",
        "b1924cab02b05e6feb33371f58b8be8ecffec6229eb8f71aaa13436a261ec6e5",
    ): "the interpreter candidates array: each name is looked up with `command -v`, and the one "
    "found runs only scripts",
    (
        "scripts/lib/provider-registry.sh",
        "e46aa06946b72f00b8d91c26479cfb8df58087a3094fa9d38bc0afdf125c7aad",
    ): "a `|`-separated registry row naming python3 as a provider's requirement",
    (
        "scripts/lib/context.sh",
        "745fafda55f0bae57e72a9e87a68022672ec9f6703db6695ae13ce49040beaf5",
    ): "a regular expression of prompt keywords",
    (
        "scripts/lib/intelligence.sh",
        "51d5348652453762db34935eea30307b1527c5ba92a7c4a63c92c3396cc1162d",
    ): "a regular expression of prompt keywords",
    (
        "scripts/lib/routing.sh",
        "8f2f763369d56d1c45333be1d1a5f709752714c3cc93e26d80985b68e10af578",
    ): "a regular expression of prompt keywords",
    (
        "hooks/cwd-changed.sh",
        "905eaf3f380087b409ad0c85d141a497adf6189167876722ff8df63ed04c2858",
    ): "a string naming a project's languages",
}
"""Flagged sites a person read at v11.15.1 (2026-10-25) and found are not a run."""


def _module_reading(module: str) -> str:
    top = module.split(".")[0]
    return FIXABLE if top in sys.stdlib_module_names else UNCLEAR


def _option(token: str, following: list[str]) -> tuple[str | None, bool]:
    """`(reading, skip the next token)` for one token starting with `-`; no reading continues."""
    if token in _TERMINAL:
        return (NOT_A_RUN, False)
    if token == "-":
        return (FIXABLE, False)
    if token.startswith("--"):
        # A long option may take its value as the next token, so where the options end is unknown.
        return (UNCLEAR, False)
    if _ATTACHED_VALUE.fullmatch(token):
        return (None, False)
    if not _OPTION_CLUSTER.fullmatch(token):
        return (UNCLEAR, False)
    letters = token[1:]
    if "I" in letters:
        return (ISOLATED, False)
    if "c" in letters:
        return (FIXABLE, False)
    if "m" in letters:
        named = letters[-1] == "m" and following
        return (_module_reading(following[0]) if named else UNCLEAR, False)
    return (None, letters[-1] in _TAKES_ARGUMENT)


def _reading(rest: str) -> str:
    """How the run reads from the text after one interpreter word."""
    tokens = rest.split()
    skip_next = False
    for index, token in enumerate(tokens):
        if skip_next:
            skip_next = False
            continue
        if token[0] == "<":
            return FIXABLE
        if token[0] in _PUNCTUATION or token[0] in ",]\\":
            return UNCLEAR
        if not token.startswith("-"):
            return NOT_A_RUN
        reading, skip_next = _option(token, tokens[index + 1 : index + 2])
        if reading is not None:
            return reading
    return UNCLEAR


def _sites(line: str) -> list[tuple[int, bool]]:
    """`(end of the interpreter word, fixable)` for each unisolated run that may be inline."""
    if line.lstrip().startswith("#"):
        return []
    found: list[tuple[int, bool]] = []
    for match in _INTERPRETER.finditer(line):
        before, rest = line[: match.start()], line[match.end() :]
        if _LOOKUP.search(before):
            # `$(command -v python3) -c ...` runs what the lookup found.
            if rest.startswith(")") and _reading(rest[1:]) in (FIXABLE, UNCLEAR):
                found.append((match.end(), False))
            continue
        if rest[:1] in _QUOTES:
            if _reading(rest[1:]) != ISOLATED:
                found.append((match.end(), False))
            continue
        reading = _reading(rest)
        if reading == UNCLEAR and not rest.strip() and _PIPED.search(before):
            reading = FIXABLE
        if reading in (FIXABLE, UNCLEAR):
            found.append((match.end(), reading == FIXABLE))
    return found


def detect(path: str, text: str) -> tuple[int, ...]:
    flagged: list[int] = []
    for number, line in enumerate(text.splitlines(), start=1):
        flagged_line = _sites(line) or (
            not line.lstrip().startswith("#") and _ENVIRONMENT.search(line)
        )
        if flagged_line and not is_read(READ_LINES, path, line):
            flagged.append(number)
    return tuple(flagged)


def fix(path: str, text: str) -> str:
    del path
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        body = line.rstrip("\r\n")
        ending = line[len(body) :]
        for end, fixable in reversed(_sites(body)):
            if fixable:
                body = f"{body[:end]} -I{body[end:]}"
        lines[index] = body + ending
    return "".join(lines)
