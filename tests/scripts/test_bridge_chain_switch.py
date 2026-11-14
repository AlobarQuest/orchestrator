"""The bump proposer and the work carrier behind the factory-events switch (FE-5 cut-over plan,
P-OR-1).

With `~/.factory/chain-db.env` absent, neither launcher fetches a chain password or sets a chain
variable. With it present, the bump proposer fetches the append password on a `--submit` run only,
through its own Keychain item, and the carrier fetches the read password through `sds-operator`;
each passes the password and the three settings to its one program, as an assignment prefix.

Each real launcher runs from a throwaway tree with the real `sds-deadman.sh` and `sds-bws.sh`, fake
programs, and `security`, `bws`, `env`, `curl` and `gh` stubs. Every `/usr/bin/security` in the
copied scripts is rewritten to the `security` stub, so the real Keychain is never read, and HOME is
a temporary directory. Every stub value is fake and names what it stands for.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.scripts.test_bridge_launchers import _CURL, _GH, PING_URL

APPEND_UUID = "2ce0dbc7-eeac-4bf0-8204-b50200c97516"
READ_UUID = "2f708d08-ea5d-4d11-bde0-b50200c9a60b"
CHAIN_NAMES = {
    "FACTORY_EVENTS_APPEND_PASSWORD",
    "FACTORY_EVENTS_READ_PASSWORD",
    "FACTORY_EVENTS_DB_HOST",
    "FACTORY_EVENTS_DB_PORT",
    "FACTORY_EVENTS_DB_NAME",
}
SWITCH = (
    "FACTORY_EVENTS_DB_HOST=100.64.0.1\n"
    "FACTORY_EVENTS_DB_PORT=15432\n"
    "FACTORY_EVENTS_DB_NAME=postgres\n"
)
SETTINGS = {
    "FACTORY_EVENTS_DB_HOST": "100.64.0.1",
    "FACTORY_EVENTS_DB_PORT": "15432",
    "FACTORY_EVENTS_DB_NAME": "postgres",
}

# The fake Keychain: each item's stub token. A test drops an item to make it missing.
KEYCHAIN = {
    "BWS_ACCESS_TOKEN_VPS_BACKUP": "stub-token-broad",
    "BWS_ACCESS_TOKEN_SDS": "stub-token-sds-operator",
    "BWS_ACCESS_TOKEN_BUMP_PROPOSER": "stub-token-bump-proposer",
}


def stub_value(uuid: str) -> str:
    """What the `bws` stub answers for one secret: fake, and different for every secret."""
    return f"stub-value-{uuid}"


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
# Logs the identity it was handed beside the secret it was asked for, and its argv apart from
# that, and answers each secret with its own fake value unless the secret is BWS_FAIL_UUID.
_BWS = """#!/bin/sh
printf '%s\\t%s\\n' "${BWS_ACCESS_TOKEN-<unset>}" "$3" >> "$BWS_LOG"
printf '%s\\n' "$*" >> "$ARGV_LOG"
[ "$3" = "${BWS_FAIL_UUID:-none}" ] && exit 1
printf '{"value": "stub-value-%s"}' "$3"
"""
_ENV = """#!/bin/sh
printf '%s\\n' "$*" >> "$ARGV_LOG"
exec /usr/bin/env "$@"
"""
# Records its argv, the NAMES it was given, and the values of the chain variables only.
_PROGRAM = """#!/bin/sh
printf '%s\\n' "$*" >> "$ARGV_LOG"
/usr/bin/env | sed 's/=.*//' > "$PROGRAM_LOG/{name}.names"
/usr/bin/env | grep '^FACTORY_EVENTS_' > "$PROGRAM_LOG/{name}.chain"
echo "{name} found: nothing"
exit 0
"""


@dataclass(frozen=True)
class Lane:
    launcher: str
    check: str
    programs: tuple[str, ...]
    # The shell variables the launcher's chain block assigns, which it must unset before use.
    own_names: frozenset[str]


CHAIN_VARIABLES = frozenset({"CHAIN_PASSWORD", "CHAIN_DB_HOST", "CHAIN_DB_PORT", "CHAIN_DB_NAME"})
BUMP = Lane(
    "run-bump-proposer.sh",
    "sds-bump-proposer",
    ("bump-proposer",),
    CHAIN_VARIABLES | {"BUMP_PROPOSER_EVENTS_IDENTITY"},
)
CARRIER = Lane(
    "run-work-carrier.sh", "sds-work-carrier", ("work-watcher", "work-carrier"), CHAIN_VARIABLES
)


@dataclass
class Run:
    rc: int
    output: str
    pings: list[tuple[str, ...]]
    fetches: list[tuple[str, str]]
    keychain: list[str]
    argv: str
    names: dict[str, set[str]]
    chain: dict[str, dict[str, str]]
    home: Path

    def fetched(self, uuid: str) -> list[str]:
        """The identities a secret was fetched with."""
        return [token for token, secret in self.fetches if secret == uuid]

    def everything_a_person_or_ps_could_read(self) -> str:
        return "\n".join([self.output, self.argv, *("\t".join(ping) for ping in self.pings)])


def _executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


def _run(
    tmp_path: Path,
    lane: Lane,
    *args: str,
    switch: str | None = None,
    keychain: dict[str, str] | None = None,
    extra_env: dict[str, str] | None = None,
) -> Run:
    root = tmp_path / "tree"
    (root / "scripts").mkdir(parents=True)
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name in (lane.launcher, "sds-deadman.sh", "sds-bws.sh"):
        text = (Path("scripts") / name).read_text()
        text = text.replace("/usr/bin/security", str(stubs / "security"))
        assert "/usr/bin/security" not in text
        (root / "scripts" / name).write_text(text)
    venv = root / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").symlink_to((Path.cwd() / ".venv" / "bin" / "python").resolve())
    for program in lane.programs:
        _executable(venv / program, _PROGRAM.format(name=program))
    for name, text in {
        "security": _SECURITY,
        "bws": _BWS,
        "env": _ENV,
        "curl": _CURL,
        "gh": _GH,
    }.items():
        _executable(stubs / name, text)
    home = tmp_path / "home"
    (home / ".factory").mkdir(parents=True)
    if switch is not None:
        (home / ".factory" / "chain-db.env").write_text(switch)
    logs = tmp_path / "logs"
    logs.mkdir()
    for name in ("pings", "bws", "security", "argv"):
        (logs / name).write_text("")
    items = KEYCHAIN if keychain is None else keychain
    env = {
        "PATH": f"{stubs}:/usr/bin:/bin",
        "HOME": str(home),
        "LISTING": json.dumps({"checks": [{"name": lane.check, "ping_url": PING_URL}]}),
        "PING_LOG": str(logs / "pings"),
        "BWS_LOG": str(logs / "bws"),
        "SECURITY_LOG": str(logs / "security"),
        "ARGV_LOG": str(logs / "argv"),
        "PROGRAM_LOG": str(logs),
        **{f"KEYCHAIN_{account}": token for account, token in items.items()},
        **(extra_env or {}),
    }
    result = subprocess.run(
        ["/bin/bash", str(root / "scripts" / lane.launcher), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    names = {
        program: set((logs / f"{program}.names").read_text().split())
        for program in lane.programs
        if (logs / f"{program}.names").exists()
    }
    chain = {
        program: dict(
            line.split("=", 1) for line in (logs / f"{program}.chain").read_text().splitlines()
        )
        for program in lane.programs
        if (logs / f"{program}.chain").exists()
    }
    return Run(
        rc=result.returncode,
        output=result.stdout + result.stderr,
        pings=[tuple(line.split("\t")) for line in (logs / "pings").read_text().splitlines()],
        fetches=[
            (token, secret)
            for token, secret in (
                line.split("\t") for line in (logs / "bws").read_text().splitlines()
            )
        ],
        keychain=(logs / "security").read_text().split(),
        argv=(logs / "argv").read_text(),
        names=names,
        chain=chain,
        home=home,
    )


def _assert_nothing_secret_is_readable(run: Run) -> None:
    """No stub password or stub token in argv, the report, the lane log or a ping body."""
    readable = run.everything_a_person_or_ps_could_read()
    for secret in (*KEYCHAIN.values(), stub_value(APPEND_UUID), stub_value(READ_UUID)):
        assert secret not in readable


# ---- no switch file: nothing changes ------------------------------------------------------------

BOTH_RUNS = [
    pytest.param(BUMP, (), id="bump-proposer-dry-run"),
    pytest.param(BUMP, ("--submit",), id="bump-proposer-submit"),
    pytest.param(CARRIER, (), id="carrier-bare"),
    pytest.param(CARRIER, ("--register",), id="carrier-register"),
]


@pytest.mark.parametrize(("lane", "args"), BOTH_RUNS)
def test_without_the_switch_no_chain_password_is_fetched_and_no_chain_variable_set(
    tmp_path: Path, lane: Lane, args: tuple[str, ...]
) -> None:
    run = _run(tmp_path, lane, *args)
    assert run.rc == 0, run.output
    assert not run.fetched(APPEND_UUID) and not run.fetched(READ_UUID)
    assert "BWS_ACCESS_TOKEN_BUMP_PROPOSER" not in run.keychain
    for program in lane.programs:
        assert not run.names[program] & CHAIN_NAMES
    _assert_nothing_secret_is_readable(run)


@pytest.mark.parametrize(("lane", "args"), BOTH_RUNS)
def test_without_the_switch_an_ambient_chain_variable_still_passes_through(
    tmp_path: Path, lane: Lane, args: tuple[str, ...]
) -> None:
    """The launchers neither set nor clear a chain variable without the switch, as before it."""
    ambient = {"FACTORY_EVENTS_APPEND_PASSWORD": "ambient-append", **SETTINGS}
    run = _run(tmp_path, lane, *args, extra_env=ambient)
    assert run.rc == 0, run.output
    for program in lane.programs:
        assert run.chain[program] == ambient


# ---- the bump proposer with the switch ----------------------------------------------------------


def test_a_submitting_bump_proposer_fetches_the_append_password_through_its_own_item(
    tmp_path: Path,
) -> None:
    run = _run(tmp_path, BUMP, "--submit", switch=SWITCH)
    assert run.rc == 0, run.output
    assert run.fetched(APPEND_UUID) == [KEYCHAIN["BWS_ACCESS_TOKEN_BUMP_PROPOSER"]]
    assert not run.fetched(READ_UUID)
    assert run.keychain.count("BWS_ACCESS_TOKEN_BUMP_PROPOSER") == 1
    # The new item's token reaches only that one fetch.
    others = [
        secret
        for token, secret in run.fetches
        if token == KEYCHAIN["BWS_ACCESS_TOKEN_BUMP_PROPOSER"]
    ]
    assert others == [APPEND_UUID]
    assert run.chain["bump-proposer"] == {
        "FACTORY_EVENTS_APPEND_PASSWORD": stub_value(APPEND_UUID),
        **SETTINGS,
    }
    _assert_nothing_secret_is_readable(run)


def test_a_dry_run_bump_proposer_with_the_switch_fetches_and_passes_nothing(
    tmp_path: Path,
) -> None:
    run = _run(tmp_path, BUMP, switch=SWITCH)
    assert run.rc == 0, run.output
    assert not run.fetched(APPEND_UUID)
    assert "BWS_ACCESS_TOKEN_BUMP_PROPOSER" not in run.keychain
    assert not run.names["bump-proposer"] & CHAIN_NAMES
    assert run.pings == []


def test_a_bump_proposer_whose_item_is_missing_pings_fail_and_never_runs(tmp_path: Path) -> None:
    keychain = {k: v for k, v in KEYCHAIN.items() if k != "BWS_ACCESS_TOKEN_BUMP_PROPOSER"}
    run = _run(tmp_path, BUMP, "--submit", switch=SWITCH, keychain=keychain)
    assert run.rc == 1
    assert "bump-proposer" not in run.names
    assert not run.fetched(APPEND_UUID)
    assert run.pings[-1][0] == f"{PING_URL}/fail"
    assert "BWS_ACCESS_TOKEN_BUMP_PROPOSER" in run.pings[-1][1]
    _assert_nothing_secret_is_readable(run)


def test_a_bump_proposer_whose_append_password_is_unreadable_pings_fail_and_never_runs(
    tmp_path: Path,
) -> None:
    run = _run(tmp_path, BUMP, "--submit", switch=SWITCH, extra_env={"BWS_FAIL_UUID": APPEND_UUID})
    assert run.rc == 1
    assert "bump-proposer" not in run.names
    assert run.pings[-1][0] == f"{PING_URL}/fail"
    assert "factory-events append password" in run.pings[-1][1]


# ---- the carrier with the switch ----------------------------------------------------------------


@pytest.mark.parametrize("args", [(), ("--register",)], ids=["bare", "register"])
def test_the_carrier_fetches_the_read_password_through_sds_operator_for_the_carry_alone(
    tmp_path: Path, args: tuple[str, ...]
) -> None:
    run = _run(tmp_path, CARRIER, *args, switch=SWITCH)
    assert run.rc == 0, run.output
    assert run.fetched(READ_UUID) == [KEYCHAIN["BWS_ACCESS_TOKEN_SDS"]]
    assert not run.fetched(APPEND_UUID)
    assert "BWS_ACCESS_TOKEN_BUMP_PROPOSER" not in run.keychain
    assert run.chain["work-carrier"] == {
        "FACTORY_EVENTS_READ_PASSWORD": stub_value(READ_UUID),
        **SETTINGS,
    }
    assert not run.names["work-watcher"] & CHAIN_NAMES
    _assert_nothing_secret_is_readable(run)


def test_a_registering_carrier_whose_read_password_is_unreadable_pings_fail(
    tmp_path: Path,
) -> None:
    run = _run(
        tmp_path, CARRIER, "--register", switch=SWITCH, extra_env={"BWS_FAIL_UUID": READ_UUID}
    )
    assert run.rc == 1
    assert run.names == {}
    assert run.pings[-1][0] == f"{PING_URL}/fail"
    assert "factory-events read password" in run.pings[-1][1]


def test_a_bare_carrier_whose_read_password_is_unreadable_fails(tmp_path: Path) -> None:
    """A carry that verified against the file instead would report on the wrong chain."""
    run = _run(tmp_path, CARRIER, switch=SWITCH, extra_env={"BWS_FAIL_UUID": READ_UUID})
    assert run.rc == 1
    assert run.names == {}
    assert run.pings == []


def test_a_bare_carrier_with_no_sds_operator_identity_skips_the_fetch(tmp_path: Path) -> None:
    keychain = {k: v for k, v in KEYCHAIN.items() if k != "BWS_ACCESS_TOKEN_SDS"}
    run = _run(tmp_path, CARRIER, switch=SWITCH, keychain=keychain)
    assert not run.fetched(READ_UUID)
    assert not run.names["work-carrier"] & CHAIN_NAMES
    assert "the factory-events read password is not fetched" in run.output


def test_a_registering_carrier_with_no_sds_operator_identity_pings_fail(tmp_path: Path) -> None:
    keychain = {k: v for k, v in KEYCHAIN.items() if k != "BWS_ACCESS_TOKEN_SDS"}
    run = _run(tmp_path, CARRIER, "--register", switch=SWITCH, keychain=keychain)
    assert run.rc == 1
    assert not run.fetched(READ_UUID)
    assert run.pings[-1][0] == f"{PING_URL}/fail"


# ---- both launchers ---------------------------------------------------------------------------

SWITCHING_RUNS = [
    pytest.param(BUMP, ("--submit",), id="bump-proposer-submit"),
    pytest.param(BUMP, (), id="bump-proposer-dry-run"),
    pytest.param(CARRIER, ("--register",), id="carrier-register"),
    pytest.param(CARRIER, (), id="carrier-bare"),
]
# Each malformed file, and the reason its refusal must carry to the lane log and the `/fail` body.
MALFORMED = {
    "a missing key": (
        "FACTORY_EVENTS_DB_HOST=h\nFACTORY_EVENTS_DB_PORT=1\n",
        "has no FACTORY_EVENTS_DB_NAME",
    ),
    "an unknown key": (SWITCH + "FACTORY_EVENTS_EXTRA=hunter2\n", "line 4 has an unknown key"),
}


@pytest.mark.parametrize(("lane", "args"), SWITCHING_RUNS)
@pytest.mark.parametrize("case", sorted(MALFORMED))
def test_a_malformed_switch_fails_the_pass_before_any_program_runs(
    tmp_path: Path, lane: Lane, args: tuple[str, ...], case: str
) -> None:
    text, reason = MALFORMED[case]
    run = _run(tmp_path, lane, *args, switch=text)
    assert run.rc == 1
    assert run.names == {}
    assert f"the factory-events switch: {run.home}/.factory/chain-db.env" in run.output
    assert reason in run.output
    assert "hunter2" not in run.output
    assert not run.fetched(APPEND_UUID) and not run.fetched(READ_UUID)
    if args:
        assert run.pings[-1][0] == f"{PING_URL}/fail"
        assert reason in run.pings[-1][1]
    else:
        assert run.pings == []


@pytest.mark.parametrize(("lane", "args"), SWITCHING_RUNS)
def test_a_name_the_caller_exported_does_not_reach_a_program(
    tmp_path: Path, lane: Lane, args: tuple[str, ...]
) -> None:
    """A launcher's own names are unset before use, so `~/.factory/env` or a shell that exported
    one can't leave the password in every child's environment."""
    preset = {name: f"preset-{name}" for name in lane.own_names}
    run = _run(tmp_path, lane, *args, switch=SWITCH, extra_env=preset)
    assert run.rc == 0, run.output
    for program in lane.programs:
        assert not run.names[program] & set(preset)


# What each program receives with the switch on and every chain variable already ambient: only
# what the launcher fetched and read, and nothing at all for the others.
EXPECTED_WITH_AMBIENT = {
    (BUMP, ("--submit",)): {
        "bump-proposer": {"FACTORY_EVENTS_APPEND_PASSWORD": stub_value(APPEND_UUID), **SETTINGS}
    },
    (BUMP, ()): {"bump-proposer": {}},
    (CARRIER, ("--register",)): {
        "work-watcher": {},
        "work-carrier": {"FACTORY_EVENTS_READ_PASSWORD": stub_value(READ_UUID), **SETTINGS},
    },
    (CARRIER, ()): {
        "work-watcher": {},
        "work-carrier": {"FACTORY_EVENTS_READ_PASSWORD": stub_value(READ_UUID), **SETTINGS},
    },
}


@pytest.mark.parametrize(("lane", "args"), SWITCHING_RUNS)
def test_with_the_switch_an_ambient_chain_variable_reaches_no_program(
    tmp_path: Path, lane: Lane, args: tuple[str, ...]
) -> None:
    """With the switch on, the launcher is the chain variables' only source."""
    ambient = {name: f"ambient-{name}" for name in CHAIN_NAMES}
    run = _run(tmp_path, lane, *args, switch=SWITCH, extra_env=ambient)
    assert run.rc == 0, run.output
    assert run.chain == EXPECTED_WITH_AMBIENT[(lane, args)]


def test_control_a_name_the_caller_exported_survives_a_plain_assignment() -> None:
    """Without this, the case above would pass on a shell where assignment unexports."""
    result = subprocess.run(
        ["/bin/bash", "-c", "CHAIN_PASSWORD=x; /usr/bin/env | grep -c '^CHAIN_PASSWORD='"],
        env={"PATH": "/usr/bin:/bin", "CHAIN_PASSWORD": "preset"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.stdout.strip() == "1"


def test_the_harness_reads_no_keychain_item_by_absolute_path() -> None:
    """The harness's rewrite is what keeps the real Keychain unread: each copied script must
    still name `/usr/bin/security` where the launcher reads an item, or the rewrite is a no-op."""
    for name in (BUMP.launcher, CARRIER.launcher):
        assert "/usr/bin/security" in (Path("scripts") / name).read_text()
