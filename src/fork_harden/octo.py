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
alone or ending a path) is followed by its options; the first token that is not an option decides.
`-c`, `-m` or `-` (or an option cluster containing `c` or `m`) is inline. Nothing more on the line,
or shell punctuation, is stdin, which is inline too. Anything else is a script or not a run at
all. A quoted word (`"python3" -c`) is read past its quote and flagged, but never rewritten. A
word after `command -v`, `which`, `type` or `hash` is a lookup, and a line whose first
non-blank character is `#` never runs. The reading is deliberately crude: a stdin site in prose or
in a list of names is flagged too, and holds until its exact line is listed in `READ_LINES`. The
stated limit is an interpreter reached through a variable (`"$PY" -c`), which no reading of the
text can see; octo's one such path runs scripts only (`_octo_pid_python_run`).
"""

from __future__ import annotations

import re
from typing import Final

from fork_harden.read_lines import ReadLines, is_read

_INTERPRETER: Final = re.compile(r"(?<![\w.$-])python(?:3(?:\.\d+)?)?(?![\w.-])")
_LOOKUP: Final = re.compile(r"(?:\bcommand\s+-v|\bwhich|\btype|\bhash)\s+(?:\S*/)?$")
_OPTION_CLUSTER: Final = re.compile(r"-[A-Za-z]+")
_STDIN_PUNCTUATION: Final = frozenset(
    "|;&)<>`"
)  # not-a-vocabulary: shell characters, read here only
_TAKES_ARGUMENT: Final = frozenset("WXQ")  # not-a-vocabulary: Python's own options with a value
_QUOTES: Final = frozenset("\"'")  # not-a-vocabulary: the two shell quote characters

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


def _inline_unisolated(rest: str) -> tuple[bool, bool]:
    """`(inline, explicit)` for the text after one interpreter word.

    `inline` is True when the run is inline and not isolated; `explicit` when it is inline by an
    explicit `-c`, `-m` or `-`, which is the only form the fix rewrites.
    """
    isolated = False
    skip_next = False
    for token in rest.split():
        if token[0] in _STDIN_PUNCTUATION:
            break
        if skip_next:
            skip_next = False
            continue
        if token == "-":
            return (not isolated, True)
        if not _OPTION_CLUSTER.fullmatch(token):
            return (False, False)
        letters = token[1:]
        isolated = isolated or "I" in letters
        if "c" in letters or "m" in letters:
            return (not isolated, True)
        skip_next = letters[-1] in _TAKES_ARGUMENT
    return (not isolated, False)


def _sites(line: str) -> list[tuple[int, bool]]:
    """`(end of the interpreter word, explicit)` for each unisolated inline run on the line."""
    if line.lstrip().startswith("#"):
        return []
    found: list[tuple[int, bool]] = []
    for match in _INTERPRETER.finditer(line):
        if _LOOKUP.search(line[: match.start()]):
            continue
        rest = line[match.end() :]
        quoted = rest[:1] in _QUOTES
        inline, explicit = _inline_unisolated(rest[1:] if quoted else rest)
        if inline:
            # A quoted word (`"python3" -c`) is read past its closing quote, but never rewritten:
            # `-I` inside the quotes would make one word of the interpreter and its option.
            found.append((match.end(), explicit and not quoted))
    return found


def detect(path: str, text: str) -> tuple[int, ...]:
    flagged: list[int] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if _sites(line) and not is_read(READ_LINES, path, line):
            flagged.append(number)
    return tuple(flagged)


def fix(path: str, text: str) -> str:
    del path
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        body = line.rstrip("\r\n")
        ending = line[len(body) :]
        for end, explicit in reversed(_sites(body)):
            if explicit:
                body = f"{body[:end]} -I{body[end:]}"
        lines[index] = body + ending
    return "".join(lines)
