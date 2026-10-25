"""octo's rule: every inline interpreter run gets `-I`, and nothing else is rewritten."""

from __future__ import annotations

import pytest

from fork_harden import octo
from fork_harden.read_lines import digest

PATH = "hooks/example.sh"

INLINE = [
    'NAME=$(printf "%s" "$INPUT" | python3 -c "import json,sys; print(1)")',
    "python3 - \"$raw\" <<'PY'",
    "python -m json.tool",
    "/usr/bin/python3 -c 'import os'",
    "python3.12 -c 'import os'",
    "python3 -u -c 'import os'",
    "python3 -uc 'import os'",
    "python3 -W ignore -c 'import os'",
    "python3 -E -m http.server",
    "exec python3 -c 'import os'",
    'X="$Y" python3 -c "import os"',
]

FLAGGED_NOT_REWRITTEN = [
    '"python3" -c "import os"',
    "'python3' - <<'PY'",
    'printf "%s" "$x" | python3',
    "python3 <<'EOF'",
    "out=$(python3)",
    "python3; echo done",
]

NOT_A_RUN = [
    "python3 -I -c 'import os'",
    "python3 -Ic 'import os'",
    "python3 -E -I - <<'PY'",
    'python3 "$_HOOK_DIR/safety-contract.py" field command',
    "python3 scripts/helpers/pid-ledger.py capability",
    "command -v python3 >/dev/null 2>&1",
    "if ! command -v python3 &>/dev/null; then",
    "which python3 >/dev/null",
    "hash python3 2>/dev/null",
    "    # python3 -c is how the hook reads JSON",
    "#!/usr/bin/env python3",
    '_octo_pid_python_path "$candidate"',
    'echo "pid-ledger-python" "state"',
    "python3 --version",
    "[[ $x =~ (python3?|pip) ]]",
    "my-python3 -c 'import os'",
    "tool.python3 -c 'import os'",
    "$python3 -c 'import os'",
]


@pytest.mark.parametrize("line", INLINE + FLAGGED_NOT_REWRITTEN)
def test_an_inline_run_without_isolation_is_the_finding(line: str) -> None:
    assert octo.detect(PATH, line + "\n") == (1,)


@pytest.mark.parametrize("line", NOT_A_RUN)
def test_an_isolated_run_a_script_or_a_lookup_is_not(line: str) -> None:
    assert octo.detect(PATH, line + "\n") == ()


def test_an_interpreter_named_by_a_variable_is_a_stated_limit() -> None:
    # No reading of the text can see what "$PY" names. The module docstring says so; this pins
    # that the limit is the stated one and not a wider one.
    assert octo.detect(PATH, '"$PY" -c "import os"\n') == ()


@pytest.mark.parametrize("line", INLINE)
def test_the_fix_isolates_every_explicit_inline_run(line: str) -> None:
    fixed = octo.fix(PATH, line + "\n")
    assert " -I " in fixed
    assert octo.detect(PATH, fixed) == ()


@pytest.mark.parametrize("line", FLAGGED_NOT_REWRITTEN)
def test_a_stdin_run_or_a_quoted_word_is_not_rewritten_and_stays_the_finding(line: str) -> None:
    text = line + "\n"
    assert octo.fix(PATH, text) == text
    assert octo.detect(PATH, octo.fix(PATH, text)) == (1,)


@pytest.mark.parametrize("line", NOT_A_RUN)
def test_the_fix_leaves_everything_else_byte_identical(line: str) -> None:
    assert octo.fix(PATH, line + "\n") == line + "\n"


def test_every_site_on_a_line_is_fixed_and_the_rest_of_the_line_kept() -> None:
    line = 'a=$(python3 -c "x"); b=$(python3 -m y | python3 - z)'
    assert octo.fix(PATH, line) == 'a=$(python3 -I -c "x"); b=$(python3 -I -m y | python3 -I - z)'


def test_the_fix_keeps_line_endings_and_a_missing_final_newline() -> None:
    text = "python3 -c 'a'\r\nok\npython3 -c 'b'"
    assert octo.fix(PATH, text) == "python3 -I -c 'a'\r\nok\npython3 -I -c 'b'"


def test_the_fix_is_idempotent() -> None:
    text = "\n".join(INLINE) + "\n"
    once = octo.fix(PATH, text)
    assert octo.fix(PATH, once) == once


def test_a_line_a_person_read_is_excused_only_while_it_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    line = "CANDIDATES=(python3 python3.12)"
    monkeypatch.setattr(octo, "READ_LINES", {(PATH, digest(line)): "a list of names"})
    assert octo.detect(PATH, line + "\n") == ()
    assert octo.detect("hooks/other.sh", line + "\n") == (1,)
    assert octo.detect(PATH, line + " \n") == (1,)


def test_the_flagged_line_numbers_are_the_lines_with_a_site() -> None:
    text = "ok\npython3 -c 'a'\nok\nx | python3\n"
    assert octo.detect(PATH, text) == (2, 4)
