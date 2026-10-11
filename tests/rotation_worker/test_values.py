"""A credential value never shows itself: not in repr, str, f-strings, pickles or logs."""

from __future__ import annotations

import hashlib
import pickle

import pytest

from rotation_worker.values import REDACTED, Value, scrub

TEXT = "sk-throwaway-0123456789-abcdefghij-klmnop"


def test_repr_str_and_format_show_only_the_fingerprint() -> None:
    value = Value(TEXT)
    for shown in (repr(value), str(value), f"{value}", f"{value!r}", f"{[value]}"):
        assert TEXT not in shown
        assert value.fingerprint.sha256_prefix in shown


def test_the_fingerprint_is_a_16_hex_prefix_and_the_length() -> None:
    value = Value(TEXT)
    assert value.fingerprint.sha256_prefix == hashlib.sha256(TEXT.encode()).hexdigest()[:16]
    assert value.fingerprint.length == len(TEXT)


def test_a_value_is_never_pickled() -> None:
    with pytest.raises(TypeError):
        pickle.dumps(Value(TEXT))


@pytest.mark.parametrize("text", ["", " padded", "padded ", "\tx"])
def test_an_empty_or_padded_value_is_refused(text: str) -> None:
    with pytest.raises(ValueError):
        Value(text)


def test_equality_is_by_content() -> None:
    assert Value(TEXT) == Value(TEXT)
    assert Value(TEXT).same_as(Value(TEXT))
    assert Value(TEXT) != Value(TEXT + "x")
    assert Value(TEXT) != TEXT
    assert len({Value(TEXT), Value(TEXT)}) == 1


def test_scrub_replaces_every_value_handled_longest_first() -> None:
    short, long = Value("abcdefgh-short"), Value("abcdefgh-short-and-longer")
    line = f"a={short.reveal()} b={long.reveal()} c=untouched"
    assert scrub(line) == f"a={REDACTED} b={REDACTED} c=untouched"
