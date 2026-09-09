"""The offline half of the gate that refuses known-good/profile budget drift.

`scripts/check_profile_budget_agreement.py` compares three sites of one number: the known-good
pattern's ceilings in `factory-policy.toml`, `PROFILE_BUDGETS` in the authority test module, and
`BUDGETS` in intent-packages. The third reading is over the network, so it runs as a CI step --
but everything the script DECIDES with is pure, and pure is what gets tested here.

WHY THIS MODULE EXISTS AT ALL: mutation review, 2026-09-06. Four mutants inside the script's own
comparison survived -- `if True`, and dropping each of the three sites from it -- because the only
grader was the script's own exit code and nothing exercised the script. A check nothing checks is
the defect it guards against, wearing a different hat; its sibling
`check_routing_policy_compatibility.py` has had a test module in intent-packages from the start.
"""

from __future__ import annotations

import http.client

import pytest

from scripts import check_profile_budget_agreement as check

PROFILE_SOURCE = 'BUDGETS: dict[str, int] = {"max_attempts": 3, "max_llm_calls": 360}\n'


def _fetch(profile_source: str):
    def fake(repo, path, ref):
        if path == check.PROFILE_PATH:
            return profile_source
        raise AssertionError(f"unexpected fetch: {path}")

    return fake


def test_the_three_sites_agree_today() -> None:
    """Not vacuous: the live artifact and the live constant, independent of the network."""
    assert check.pattern_budgets() == check.test_constant_budgets()
    assert check.pattern_budgets()["max_llm_calls"] == 360


def test_the_profile_parser_reads_an_annotated_assignment() -> None:
    """intent-packages annotates its constant, so AnnAssign is the shape that actually occurs."""
    assert check.profile_budgets(PROFILE_SOURCE) == {"max_attempts": 3, "max_llm_calls": 360}


def test_the_profile_parser_reads_a_bare_assignment() -> None:
    """Annotation is a style choice on the far side of a boundary this check does not control."""
    assert check.profile_budgets('BUDGETS = {"max_attempts": 3, "max_llm_calls": 360}\n') == {
        "max_attempts": 3,
        "max_llm_calls": 360,
    }


@pytest.mark.parametrize(
    "source",
    [
        "OTHER = {}\n",
        "BUDGETS = compute()\n",
        'BUDGETS = {"max_attempts": 3}\n',
        'BUDGETS = {"max_attempts": 3, "max_llm_calls": "360"}\n',
        "def f():\n    BUDGETS = {}\n",
    ],
    ids=["absent", "not-a-literal", "missing-key", "not-an-int", "not-module-level"],
)
def test_the_profile_parser_is_loud_rather_than_guessing(source: str) -> None:
    """Never a default: a comparison against a number nobody could read is not a comparison."""
    with pytest.raises(check.Unresolvable):
        check.profile_budgets(source)


def test_the_comparison_fires_when_the_pattern_is_below_the_profile(monkeypatch, capsys) -> None:
    """The eighteen-day defect, reproduced: the pattern recognises nothing and nothing says so."""
    monkeypatch.setattr(check, "fetch", _fetch(PROFILE_SOURCE))
    monkeypatch.setattr(check, "pattern_budgets", lambda: {"max_attempts": 3, "max_llm_calls": 4})

    assert check.main() == 1
    assert "must be EQUAL" in capsys.readouterr().err


def test_the_comparison_fires_when_the_pattern_is_above_the_profile(monkeypatch) -> None:
    """The other direction, and it is not symmetric with the first: above the profile the pattern
    recognises an envelope no profile emits, which widens the recognised shape."""
    monkeypatch.setattr(check, "fetch", _fetch(PROFILE_SOURCE))
    monkeypatch.setattr(
        check, "pattern_budgets", lambda: {"max_attempts": 3, "max_llm_calls": 3600}
    )

    assert check.main() == 1


def test_the_comparison_fires_when_the_test_constant_drifts(monkeypatch) -> None:
    """PROFILE_BUDGETS is the site the local suite trusts; unpinned it goes stale exactly as the
    pattern did. Dropping it from the comparison must not pass."""
    monkeypatch.setattr(check, "fetch", _fetch(PROFILE_SOURCE))
    monkeypatch.setattr(
        check, "test_constant_budgets", lambda: {"max_attempts": 3, "max_llm_calls": 4}
    )

    assert check.main() == 1


def test_the_comparison_fires_when_only_the_profile_moves(monkeypatch) -> None:
    """The live case: intent-packages raises BUDGETS and this repository has not followed."""
    monkeypatch.setattr(
        check,
        "fetch",
        _fetch('BUDGETS: dict[str, int] = {"max_attempts": 3, "max_llm_calls": 720}\n'),
    )

    assert check.main() == 1


def test_the_comparison_passes_when_all_three_agree(monkeypatch, capsys) -> None:
    monkeypatch.setattr(check, "fetch", _fetch(PROFILE_SOURCE))

    assert check.main() == 0
    assert "PASS" in capsys.readouterr().out


def test_both_budget_keys_are_compared() -> None:
    """max_attempts is a ceiling too. Comparing one key would let the other drift silently."""
    assert set(check.BUDGET_KEYS) == {"max_attempts", "max_llm_calls"}


def test_an_unreadable_profile_is_unresolvable_rather_than_agreement(monkeypatch) -> None:
    """A network that cannot answer is not three sites agreeing."""

    def boom(repo, path, ref):
        raise check.Unresolvable("simulated")

    monkeypatch.setattr(check, "fetch", boom)

    assert check.main() == 1


def test_the_gate_runs_the_script_this_module_tests() -> None:
    """A check nothing invokes is the defect it guards against, wearing a different hat."""
    from pathlib import Path

    workflows = [
        path
        for path in Path(".github/workflows").glob("*.yml")
        if "check_profile_budget_agreement.py" in path.read_text(encoding="utf-8")
    ]

    assert [path.name for path in workflows] == ["quality.yml"]


class _Response:
    """A context manager whose `read` fails part-way, which is what urllib does NOT wrap."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        raise self._error


@pytest.mark.parametrize(
    "error",
    [
        http.client.IncompleteRead(b"half"),
        TimeoutError("the read timed out"),
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
    ],
    ids=["incomplete-read", "read-timeout", "not-utf-8"],
)
def test_a_failure_during_the_body_read_is_unresolvable(error: Exception, monkeypatch) -> None:
    """None of these is wrapped by urllib, and `IncompleteRead` is not even an `OSError` -- so a
    clause naming only `HTTPError`/`URLError` let all three escape as a bare traceback.

    Verified to reproduce against the pre-fix code: with the third `except` removed, every case
    here raises the injected error rather than `Unresolvable`.
    """
    monkeypatch.setattr(check.urllib.request, "urlopen", lambda *a, **k: _Response(error))

    with pytest.raises(check.Unresolvable):
        check.fetch(check.INTENT_PACKAGES_REPO, check.PROFILE_PATH, check.PROFILE_REF)


def test_the_ref_this_check_sends_is_a_literal_and_needs_no_encoding() -> None:
    """Named rather than fixed. `#` and `&` are legal in a branch name and truncate a query string,
    so an unquoted ref CAN compare the wrong branch's bytes -- but this check's ref is a module
    constant and its path is one too, so nothing caller-supplied reaches the URL. Percent-encoding
    here would guard an input that does not exist; the sibling that DOES take a variable ref
    validates it against `^[0-9a-f]{40}$` before use.
    """
    assert check.PROFILE_REF == "main"
    assert "{" not in check.PROFILE_PATH
