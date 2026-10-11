"""Credential values in memory: never printed, never pickled, identified only by fingerprint.

ADR-0055 decision 2: the executor is the only place values exist, and none passes through a tool,
a log or an evidence row (T9). Every value it handles is a `Value`, whose `repr` and `str` show a
fingerprint, never the text. The text leaves only through `reveal()`, at the one call that hands
it to BWS or to a provider. Every value ever wrapped is remembered, so `scrub` can replace any of
them in text bound for a log, as the last line of defence behind not logging them at all.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Final, NoReturn

PREFIX_LENGTH: Final = 16
REDACTED: Final = "[redacted]"

_TOUCHED: set[str] = set()


@dataclass(frozen=True)
class Fingerprint:
    """A value's sha256 prefix and length: enough to tell two values apart, never to recover one."""

    sha256_prefix: str
    length: int

    def matches(self, other: Fingerprint) -> bool:
        """Whether `other` names this value: the same length, and a prefix of at least 8 of ours.

        A person records 8 to 16 hex characters at `/review`; the executor always has 16.
        """
        prefix = other.sha256_prefix
        return (
            other.length == self.length
            and 8 <= len(prefix) <= PREFIX_LENGTH
            and self.sha256_prefix.startswith(prefix)
        )


class Value:
    __slots__ = ("_text",)

    def __init__(self, text: str) -> None:
        if not text or text != text.strip():
            raise ValueError("a credential value is non-empty and has no surrounding whitespace")
        self._text = text
        _TOUCHED.add(text)

    def reveal(self) -> str:
        return self._text

    @property
    def fingerprint(self) -> Fingerprint:
        digest = hashlib.sha256(self._text.encode()).hexdigest()
        return Fingerprint(digest[:PREFIX_LENGTH], len(self._text))

    def same_as(self, other: Value) -> bool:
        return hmac.compare_digest(self._text.encode(), other._text.encode())

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Value) and self.same_as(other)

    def __hash__(self) -> int:
        return hash(self.fingerprint)

    def __repr__(self) -> str:
        print_ = self.fingerprint
        return f"Value(sha256:{print_.sha256_prefix}, length {print_.length})"

    __str__ = __repr__

    def __reduce__(self) -> NoReturn:
        raise TypeError("a credential value is never serialised")


def scrub(text: str) -> str:
    """`text` with every value this process has handled replaced by a marker."""
    for value in sorted(_TOUCHED, key=len, reverse=True):
        if value in text:
            text = text.replace(value, REDACTED)
    return text
