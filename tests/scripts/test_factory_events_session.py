"""The interactive session's factory-events wrapper (FE-5 cut-over plan, P-OR-2).

`scripts/factory-events-session.sh` runs one `approve` (settings from the switch file) or the one
`import-file` (settings from the staging file, only while the switch is absent), with the append
password and the three settings in that child's environment alone.

Each test runs the real wrapper and the real `sds-bws.sh` from a throwaway tree, with every
`/usr/bin/security` rewritten to a `security` stub, so the real Keychain is never read. `bws`, `env`
and both child interpreters are stubs; HOME is a temporary directory and PATH is the stubs plus the
system directories. Every stub value is fake and names what it stands for.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

APPEND_UUID = "2ce0dbc7-eeac-4bf0-8204-b50200c97516"
SESSION_ITEM = "BWS_ACCESS_TOKEN_FACTORY_EVENTS_SESSION"
SESSION_TOKEN = "stub-token-factory-events-session"
CALLER_TOKEN = "stub-token-the-callers-own"
PASSWORD = f"stub-value-{APPEND_UUID}"
SETTINGS_TEXT = (
    "FACTORY_EVENTS_DB_HOST=100.64.0.1\n"
    "FACTORY_EVENTS_DB_PORT=15432\n"
    "FACTORY_EVENTS_DB_NAME=postgres\n"
)
CHILD_CHAIN = {
    "FACTORY_EVENTS_APPEND_PASSWORD": PASSWORD,
    "FACTORY_EVENTS_DB_HOST": "100.64.0.1",
    "FACTORY_EVENTS_DB_PORT": "15432",
    "FACTORY_EVENTS_DB_NAME": "postgres",
}
# Every name the wrapper assigns: none may reach the child.
OWN_NAMES = (
    "SESSION_COMMAND",
    "SESSION_CHECKOUT",
    "SESSION_PACKAGE",
    "SESSION_EVENTS_FILE",
    "SESSION_SETTINGS",
    "SESSION_PYTHON",
    "SESSION_EVENTS_IDENTITY",
    "CHAIN_PASSWORD",
    "CHAIN_DB_HOST",
    "CHAIN_DB_PORT",
    "CHAIN_DB_NAME",
)

# Prints the token of the item `-a` names, from KEYCHAIN_<account>, and logs only the account.
_SECURITY = """#!/bin/sh
account=""
previous=""
for argument in "$@"; do
    [ "$previous" = "-a" ] && account="$argument"
    previous="$argument"
done
printf '%s\\n' "$account" >> "$SECURITY_LOG"
eval "value=\\${KEYCHAIN_$account:-}"
[ -n "$value" ] || exit 44
printf '%s\\n' "$value"
"""
# Logs the identity it was handed beside the secret, and its argv apart from that.
_BWS = """#!/bin/sh
printf '%s\\t%s\\n' "${BWS_ACCESS_TOKEN-<unset>}" "$3" >> "$BWS_LOG"
printf 'bws %s\\n' "$*" >> "$ARGV_LOG"
[ -n "${BWS_FAIL:-}" ] && exit 1
printf '{"value": "stub-value-%s"}' "$3"
"""
_ENV = """#!/bin/sh
printf 'env %s\\n' "$*" >> "$ARGV_LOG"
exec /usr/bin/env "$@"
"""
# A child interpreter: records its argv, its working directory and its whole environment.
_CHILD = """#!/bin/sh
printf 'child %s\\n' "$*" >> "$ARGV_LOG"
pwd -P > "$CHILD_LOG/pwd"
/usr/bin/env > "$CHILD_LOG/env"
echo "child ran: $*"
exit "${CHILD_RC:-0}"
"""


@dataclass
class Run:
    rc: int
    stdout: str
    stderr: str
    keychain: list[str]
    fetches: list[tuple[str, str]]
    argv: str
    child_env: dict[str, str] | None
    child_pwd: Path | None

    @property
    def child_argv(self) -> list[str]:
        return [line for line in self.argv.splitlines() if line.startswith("child ")]

    def chain(self) -> dict[str, str]:
        assert self.child_env is not None
        return {k: v for k, v in self.child_env.items() if k.startswith("FACTORY_EVENTS_")}


@dataclass
class Tree:
    tmp: Path
    home: Path
    checkout: Path
    stubs: Path
    logs: Path
    wrapper: Path

    @property
    def switch(self) -> Path:
        return self.home / ".factory" / "chain-db.env"

    @property
    def staging(self) -> Path:
        return self.home / ".factory" / "chain-db.env.pending"

    @property
    def events_python(self) -> Path:
        return self.home / "Projects" / "security-standards" / ".venv-events" / "bin" / "python"

    def run(
        self,
        *args: str,
        keychain: dict[str, str] | None = None,
        extra_env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> Run:
        for name in ("security", "bws", "argv"):
            (self.logs / name).write_text("")
        for name in ("env", "pwd"):
            (self.logs / name).unlink(missing_ok=True)
        items = {SESSION_ITEM: SESSION_TOKEN} if keychain is None else keychain
        env = {
            "PATH": f"{self.stubs}:/usr/bin:/bin",
            "HOME": str(self.home),
            "SECURITY_LOG": str(self.logs / "security"),
            "BWS_LOG": str(self.logs / "bws"),
            "ARGV_LOG": str(self.logs / "argv"),
            "CHILD_LOG": str(self.logs),
            **{f"KEYCHAIN_{account}": token for account, token in items.items()},
            **(extra_env or {}),
        }
        result = subprocess.run(
            ["/bin/bash", str(self.wrapper), *args],
            env=env,
            cwd=cwd or self.tmp,
            capture_output=True,
            text=True,
            check=False,
        )
        env_file = self.logs / "env"
        pwd_file = self.logs / "pwd"
        return Run(
            rc=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            keychain=(self.logs / "security").read_text().split(),
            fetches=[
                (token, secret)
                for token, secret in (
                    line.split("\t") for line in (self.logs / "bws").read_text().splitlines()
                )
            ],
            argv=(self.logs / "argv").read_text(),
            child_env=(
                dict(
                    line.split("=", 1) for line in env_file.read_text().splitlines() if "=" in line
                )
                if env_file.exists()
                else None
            ),
            child_pwd=Path(pwd_file.read_text().strip()) if pwd_file.exists() else None,
        )


def _executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


@pytest.fixture
def tree(tmp_path: Path) -> Tree:
    root = tmp_path / "orchestrator"
    (root / "scripts").mkdir(parents=True)
    stubs = tmp_path / "stubs"
    for name in ("factory-events-session.sh", "sds-bws.sh"):
        text = (Path("scripts") / name).read_text()
        text = text.replace("/usr/bin/security", str(stubs / "security"))
        assert "/usr/bin/security" not in text
        (root / "scripts" / name).write_text(text)
    venv = root / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").symlink_to((Path.cwd() / ".venv" / "bin" / "python").resolve())
    for name, text in {"security": _SECURITY, "bws": _BWS, "env": _ENV}.items():
        _executable(stubs / name, text)
    home = tmp_path / "home"
    (home / ".factory").mkdir(parents=True)
    checkout = tmp_path / "intent-packages"
    _executable(checkout / ".venv" / "bin" / "python", _CHILD)
    (checkout / "packages" / "pkg").mkdir(parents=True)
    built = Tree(
        tmp_path,
        home,
        checkout,
        stubs,
        tmp_path / "logs",
        root / "scripts" / "factory-events-session.sh",
    )
    built.logs.mkdir()
    _executable(built.events_python, _CHILD)
    return built


def _approve(tree: Tree) -> tuple[str, ...]:
    return ("approve", str(tree.checkout), str(tree.checkout / "packages" / "pkg"))


def _assert_nothing_secret_is_readable(run: Run) -> None:
    """No stub password or stub token in any stub's argv, or on either stream."""
    for secret in (SESSION_TOKEN, CALLER_TOKEN, PASSWORD):
        for readable in (run.argv, run.stdout, run.stderr):
            assert secret not in readable


def _assert_refused_before_any_credential(run: Run, reason: str) -> None:
    assert run.rc == 2, run.stderr
    assert run.stderr.startswith("factory-events-session: "), run.stderr
    assert reason in run.stderr
    assert run.keychain == [] and run.fetches == []
    assert run.child_env is None and run.child_argv == []
    _assert_nothing_secret_is_readable(run)


def _assert_refused_without_a_child(run: Run, reason: str) -> None:
    # The JSON parse in `sds_bws_value` may print its own traceback first; the named line is last.
    assert run.rc == 2, run.stderr
    last = run.stderr.splitlines()[-1]
    assert last.startswith("factory-events-session: ") and reason in last, run.stderr
    assert run.child_env is None and run.child_argv == []
    _assert_nothing_secret_is_readable(run)


# ---- the two commands ----------------------------------------------------------------------------


def test_approve_runs_the_checkouts_approve_in_the_checkout_with_the_chain(tree: Tree) -> None:
    tree.switch.write_text(SETTINGS_TEXT)
    run = tree.run(*_approve(tree))
    assert run.rc == 0, run.stderr
    assert run.child_argv == [f"child -m intent_packages approve {tree.checkout}/packages/pkg"]
    assert run.child_pwd == tree.checkout.resolve()
    assert run.chain() == CHILD_CHAIN
    assert run.keychain == [SESSION_ITEM]
    assert run.fetches == [(SESSION_TOKEN, APPEND_UUID)]
    assert "child ran: -m intent_packages approve" in run.stdout
    _assert_nothing_secret_is_readable(run)


def test_import_file_runs_the_events_interpreter_with_the_staging_settings(tree: Tree) -> None:
    tree.staging.write_text(SETTINGS_TEXT.replace("15432", "15433"))
    events = tree.home / ".factory" / "events.jsonl"
    run = tree.run("import-file", str(events))
    assert run.rc == 0, run.stderr
    assert run.child_argv == [f"child -m factory_events import-file --path {events}"]
    assert run.chain() == {**CHILD_CHAIN, "FACTORY_EVENTS_DB_PORT": "15433"}
    assert run.fetches == [(SESSION_TOKEN, APPEND_UUID)]
    _assert_nothing_secret_is_readable(run)


def test_a_relative_package_dir_is_taken_from_the_callers_directory(tree: Tree) -> None:
    tree.switch.write_text(SETTINGS_TEXT)
    run = tree.run("approve", str(tree.checkout), "pkg", cwd=tree.checkout / "packages")
    assert run.rc == 0, run.stderr
    assert run.child_argv == [f"child -m intent_packages approve {tree.checkout / 'packages'}/pkg"]


@pytest.mark.parametrize("rc", [0, 1, 7])
def test_the_exit_code_is_the_childs(tree: Tree, rc: int) -> None:
    tree.switch.write_text(SETTINGS_TEXT)
    assert tree.run(*_approve(tree), extra_env={"CHILD_RC": str(rc)}).rc == rc


# ---- the environment -----------------------------------------------------------------------------


def test_a_callers_token_chain_variables_and_own_names_never_reach_the_child(tree: Tree) -> None:
    tree.switch.write_text(SETTINGS_TEXT)
    caller = {
        "BWS_ACCESS_TOKEN": CALLER_TOKEN,
        "FACTORY_EVENTS_APPEND_PASSWORD": "stub-callers-append-password",
        "FACTORY_EVENTS_READ_PASSWORD": "stub-callers-read-password",
        "FACTORY_EVENTS_DB_HOST": "callers-host",
        "FACTORY_EVENTS_DB_PORT": "1",
        "FACTORY_EVENTS_DB_NAME": "callers-db",
        **{name: f"stub-callers-{name}" for name in OWN_NAMES},
    }
    run = tree.run(*_approve(tree), extra_env=caller)
    assert run.rc == 0, run.stderr
    # The fetch used the session's item, never the caller's token.
    assert run.fetches == [(SESSION_TOKEN, APPEND_UUID)]
    assert run.chain() == CHILD_CHAIN
    assert run.child_env is not None
    for name in OWN_NAMES:
        assert name not in run.child_env
    # The harness's fake Keychain (`KEYCHAIN_*`) is the caller's own environment; nothing else
    # may hold the session token.
    assert SESSION_TOKEN not in "\n".join(
        value for name, value in run.child_env.items() if not name.startswith("KEYCHAIN_")
    )
    _assert_nothing_secret_is_readable(run)


def test_no_bws_access_token_is_needed_in_the_callers_environment(tree: Tree) -> None:
    tree.switch.write_text(SETTINGS_TEXT)
    run = tree.run(*_approve(tree))
    assert run.rc == 0, run.stderr
    assert run.child_env is not None and "BWS_ACCESS_TOKEN" not in run.child_env


def test_the_chain_reaches_no_process_but_the_child(tree: Tree) -> None:
    """`bws` and `env` are the only other processes the wrapper starts; neither gets the chain."""
    tree.switch.write_text(SETTINGS_TEXT)
    probe = tree.stubs / "bws"
    probe.write_text(
        _BWS.replace(
            "#!/bin/sh\n",
            '#!/bin/sh\n/usr/bin/env | grep "^FACTORY_EVENTS_" >> "$CHILD_LOG/bws-chain"\n',
        )
    )
    (tree.logs / "bws-chain").write_text("")
    run = tree.run(*_approve(tree))
    assert run.rc == 0, run.stderr
    assert (tree.logs / "bws-chain").read_text() == ""


def test_a_retired_dsn_is_left_set_so_the_child_refuses_it(tree: Tree) -> None:
    tree.switch.write_text(SETTINGS_TEXT)
    run = tree.run(*_approve(tree), extra_env={"FACTORY_EVENTS_APPEND_DSN": "stub-retired-dsn"})
    assert run.rc == 0, run.stderr
    assert run.chain() == {**CHILD_CHAIN, "FACTORY_EVENTS_APPEND_DSN": "stub-retired-dsn"}


# ---- refusals before any credential is read ----------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [(), ("verify",), ("emit", "x"), ("Approve", "a", "b"), ("--help",)],
    ids=["none", "verify", "emit", "case", "option"],
)
def test_any_other_first_argument_is_refused(tree: Tree, args: tuple[str, ...]) -> None:
    tree.switch.write_text(SETTINGS_TEXT)
    tree.staging.write_text(SETTINGS_TEXT)
    _assert_refused_before_any_credential(tree.run(*args), "unknown subcommand")


@pytest.mark.parametrize(
    "args",
    [
        ("approve",),
        ("approve", "x"),
        ("approve", "x", "y", "--approver"),
        ("import-file",),
        ("import-file", "a", "b"),
    ],
)
def test_a_wrong_argument_count_is_refused(tree: Tree, args: tuple[str, ...]) -> None:
    tree.switch.write_text(SETTINGS_TEXT)
    _assert_refused_before_any_credential(tree.run(*args), "usage:")


@pytest.mark.parametrize("package", ["--by-policy", ""])
def test_approve_refuses_an_option_or_empty_package_dir(tree: Tree, package: str) -> None:
    tree.switch.write_text(SETTINGS_TEXT)
    run = tree.run("approve", str(tree.checkout), package)
    _assert_refused_before_any_credential(run, "must be a path")


def test_approve_refuses_without_the_switch(tree: Tree) -> None:
    tree.staging.write_text(SETTINGS_TEXT)
    _assert_refused_before_any_credential(tree.run(*_approve(tree)), "chain-db.env is absent")


def test_import_file_refuses_while_the_switch_exists(tree: Tree) -> None:
    tree.staging.write_text(SETTINGS_TEXT)
    tree.switch.write_text(SETTINGS_TEXT)
    run = tree.run("import-file", "/tmp/events.jsonl")
    _assert_refused_before_any_credential(run, "chain-db.env exists")


def test_import_file_refuses_while_a_dangling_switch_symlink_exists(tree: Tree) -> None:
    tree.staging.write_text(SETTINGS_TEXT)
    tree.switch.symlink_to(tree.tmp / "nowhere")
    run = tree.run("import-file", "/tmp/events.jsonl")
    _assert_refused_before_any_credential(run, "chain-db.env exists")


def test_import_file_refuses_without_the_staging_file(tree: Tree) -> None:
    run = tree.run("import-file", "/tmp/events.jsonl")
    _assert_refused_before_any_credential(run, "chain-db.env.pending is absent")


def test_import_file_refuses_a_symlinked_staging_file(tree: Tree) -> None:
    real = tree.tmp / "real.env"
    real.write_text(SETTINGS_TEXT)
    tree.staging.symlink_to(real)
    run = tree.run("import-file", "/tmp/events.jsonl")
    _assert_refused_before_any_credential(run, "is not a regular file")


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        (SETTINGS_TEXT + "FACTORY_EVENTS_APPEND_PASSWORD=stub-pasted\n", "unknown key"),
        (
            SETTINGS_TEXT.replace("FACTORY_EVENTS_DB_NAME=postgres\n", ""),
            "no FACTORY_EVENTS_DB_NAME",
        ),
        (SETTINGS_TEXT.replace("postgres", "'postgres'"), "outside"),
    ],
    ids=["unknown-key", "missing-key", "quoted"],
)
def test_a_malformed_settings_file_is_refused(tree: Tree, text: str, reason: str) -> None:
    tree.switch.write_text(text)
    run = tree.run(*_approve(tree))
    _assert_refused_before_any_credential(run, reason)
    assert "stub-pasted" not in run.stderr


def test_approve_refuses_a_checkout_without_its_interpreter(tree: Tree) -> None:
    tree.switch.write_text(SETTINGS_TEXT)
    run = tree.run("approve", str(tree.tmp / "elsewhere"), "/x/pkg")
    _assert_refused_before_any_credential(run, "is missing or not executable")


def test_import_file_refuses_without_the_events_interpreter(tree: Tree) -> None:
    tree.staging.write_text(SETTINGS_TEXT)
    tree.events_python.unlink()
    run = tree.run("import-file", "/tmp/events.jsonl")
    _assert_refused_before_any_credential(run, "is missing or not executable")


# ---- refusals at the fetch -----------------------------------------------------------------------


@pytest.mark.parametrize("command", ["approve", "import-file"])
def test_a_missing_keychain_item_is_refused_and_the_callers_token_is_not_used(
    tree: Tree, command: str
) -> None:
    if command == "approve":
        tree.switch.write_text(SETTINGS_TEXT)
        args = _approve(tree)
    else:
        tree.staging.write_text(SETTINGS_TEXT)
        args = ("import-file", "/tmp/events.jsonl")
    run = tree.run(*args, keychain={}, extra_env={"BWS_ACCESS_TOKEN": CALLER_TOKEN})
    _assert_refused_without_a_child(run, "no BWS identity")
    assert run.keychain == [SESSION_ITEM]
    assert run.fetches == []


def test_an_empty_password_is_refused(tree: Tree) -> None:
    tree.switch.write_text(SETTINGS_TEXT)
    run = tree.run(*_approve(tree), extra_env={"BWS_FAIL": "1"})
    _assert_refused_without_a_child(run, "could not read the factory-events append password")
    assert run.fetches == [(SESSION_TOKEN, APPEND_UUID)]


def test_the_fetch_passes_color_no_and_keeps_the_token_out_of_argv(tree: Tree) -> None:
    tree.switch.write_text(SETTINGS_TEXT)
    run = tree.run(*_approve(tree))
    assert f"bws secret get {APPEND_UUID} --output json --color no" in run.argv.splitlines()
    _assert_nothing_secret_is_readable(run)
