"""Lines a person read and found are not the finding, bound to their exact content.

A rule's check over-counts, so it flags some lines that are not the finding (a regular expression
naming `python`, a docs example). Each such line is listed by its path and the sha256 of its
exact text, with the reason a person gave. Any change to the line, a whitespace change included,
brings it back as a finding. This is the fork-sync gate's acknowledgement shape (ADR-0056
decision 6), kept in the rule's own module so a rule and the lines it excuses are reviewed
together.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

ReadLines = Mapping[tuple[str, str], str]
"""`(path, sha256 of the line) -> why it is not the finding`."""


def digest(line: str) -> str:
    return hashlib.sha256(line.encode()).hexdigest()


def is_read(table: ReadLines, path: str, line: str) -> bool:
    return (path, digest(line)) in table
