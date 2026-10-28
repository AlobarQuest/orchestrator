"""Every out-of-process program under `src/` is confined, and the orchestrator reaches none of them.

Hosting a program in this repository is a packaging choice, and the moment it can import the
orchestrator -- or the orchestrator can import it -- it stops being one. Thirteen near-identical
`*_isolation.py` modules used to say so one program at a time, each with its own copy of the
import scanner; this is the one table they collapse into.

WHAT IS ASSERTED, PER PROGRAM
=============================

1. **The orchestrator imports nothing from it.** A program the orchestrator imports runs inside
   the orchestrator's process, and an out-of-process reading of the orchestrator taken from
   inside it is not independent of anything. A `library` row is the exception (ADR-0050): it
   takes no reading of anything, so the orchestrator's own clients may share its transport.
2. **Every top-level name it imports is a name its row permits.** The row is the program's whole
   dependency surface -- third-party packages, the standard library and any sibling program it
   reuses -- so a new dependency has to be written here, beside the reason it exists, rather than
   acquired. Its own package is implicit.
3. **No row permits `orchestrator`.** The ADR-0039 guard (`test_external_content_observes_only`)
   also refuses that import, over each program's symbol closure; this makes the forward direction
   a property of the table on its own, whatever that closure does or does not walk.
4. **A row marked `no_sibling_lanes` permits no other program at all**, measured against the
   population on disk rather than a hand-written list of siblings -- the three hand lists this
   replaces had each fallen behind the programs that existed. A row marked `library` is not a
   program in that sense: it is shared plumbing with no lane of its own (ADR-0050), so a sibling
   ban does not forbid it. A library row must itself carry `no_sibling_lanes`, so plumbing can
   never become a back door from one lane into another.
5. **The scan saw something.** A program with no files, or whose files import nothing, would pass
   every check above vacuously; that is the shape of a guard that has quietly stopped guarding.

WHAT IS ASSERTED ABOUT THE TABLE
================================

The population is read from the filesystem (`import_scan.out_of_process_packages`, the same
function the ADR-0039 guard counts), so a program that arrives without a row fails -- by name --
and a row whose program has gone fails too. The ADR-0039 guard owns what each program may WRITE to
the orchestrator; this table owns what it may IMPORT. Behaviour -- a forbidden request never
reaching the transport, a path predicate's anchoring -- lives with each program's own tests, in
`tests/<program>/test_client_surface.py`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.architecture.import_scan import out_of_process_packages, package_imports

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
ORCHESTRATOR = "orchestrator"


@dataclass(frozen=True)
class Row:
    allowed: frozenset[str]
    no_sibling_lanes: bool = False
    library: bool = False


def _row(*names: str, no_sibling_lanes: bool = False, library: bool = False) -> Row:
    return Row(frozenset(names), no_sibling_lanes, library)


# Every name below is a top-level import the program actually makes; the comments are the reasons
# the non-obvious ones are here, carried over from the isolation modules that used to hold them.
TABLE: dict[str, Row] = {
    "activation_sweep": _row(
        "__future__",
        "dataclasses",
        "datetime",
        "estate_clients",
        "hashlib",
        "httpx",
        "json",
        "os",
        "pathlib",
        "re",
        # `shutil.which` alone, to find `uv` on PATH before the activation check runs it. The
        # fallback to its standard install location is what keeps a scheduled pass measuring when
        # the plist's PATH does not carry it.
        "shutil",
        "subprocess",
        # `tomllib` reads `[project.scripts]` from a working copy's own manifest, which is what
        # the console-entry-point fact is measured against.
        "tomllib",
        "typer",
        "typing",
        # `urllib.parse` only, for the base-URL shape check. `urllib.request` is an HTTP client
        # and is in the invariant scan's own `HTTP_CLIENTS` set, so it could never arrive here
        # unnoticed.
        "urllib",
    ),
    # `landing_ledger` is reused rather than re-implemented: its GitHub reader, its
    # update-metadata parse and its conclusion vocabulary are exactly what this producer needs,
    # and a second copy of any of them would be a second answer to "what is open and what did CI
    # conclude". ADR-0038 removed the one part it took for JUDGMENT -- the transcribed gate
    # registry -- which now comes from change-manager.
    "bump_proposer": _row(
        "__future__",
        "argparse",
        # ADR-0033. `_run` takes a `Sequence[str]` so it accepts the split of the publish
        # command's literal spelling, which `list`'s invariance refuses.
        "collections",
        "dataclasses",
        # ADR-0034. It takes a bump only once the required checks have settled against it.
        "datetime",
        "estate_clients",
        # G1+G2. The observation's facts are content-addressed, so an unchanged re-run replays.
        "hashlib",
        "httpx",
        "json",
        "landing_ledger",
        "os",
        "pathlib",
        "re",
        # The member record's grant is this lane's scope authority.
        "sds_members",
        "subprocess",
        "sys",
        "typing",
        # `urllib.parse` only, for the orchestrator base-URL shape check.
        "urllib",
    ),
    # Measured when the table was built: `change_proposer` had no isolation module of its own.
    # It reuses the deploy watcher's `WORK_UNIT_ID` shape and its read-only readers.
    "change_proposer": _row(
        "__future__",
        "argparse",
        "collections",
        "dataclasses",
        "deploy_watcher",
        "estate_clients",
        "httpx",
        "os",
        "re",
        # The member record's grant is this lane's scope authority.
        "sds_members",
        "sys",
        "typing",
    ),
    # It reads GitHub with a plain token rather than as the App: the App's JWT assertion needs
    # `pyjwt`, which is not here.
    "deploy_watcher": _row(
        "__future__",
        # `collections.abc`, for the injected-reader signature in `transcription_currency`.
        "collections",
        # `ExitStack`, so the clients are built inside the pass guard (ADR-0050).
        "contextlib",
        "dataclasses",
        "datetime",
        "estate_clients",
        "hashlib",
        "httpx",
        "json",
        "os",
        "re",
        "typer",
        "typing",
    ),
    # The producer's already-confined change-manager client is reused rather than re-implemented,
    # because a second read-only client for one listing would be a second place for the
    # withheld-source trap to be forgotten.
    #
    # `lander` is the shared body of the two landing callers (Tier 3 item 27): each lane imports
    # it, and neither lane imports the other.
    "estate_lander": _row(
        "__future__",
        "argparse",
        "change_proposer",
        "dataclasses",
        "estate_clients",
        "httpx",
        "lander",
        "os",
        "sys",
        "typing",
    ),
    # SDS 1.1 item 5a: the deploy-time image binder, the one program ADR-0039's amendment lets
    # mint a post-deploy unit. It borrows from no other lane -- its git reads and its three
    # confined clients are its own -- so the exception cannot travel through an import.
    # ADR-0055 amendment 14: runs `/verify` on submitted rotation units as VERIFIER, through the
    # shared confined transport. It decides nothing, so it borrows no sibling's knowledge.
    "rotation_verifier": _row(
        "__future__",
        "argparse",
        "collections",
        "dataclasses",
        "estate_clients",
        "httpx",
        "os",
        "re",
        "sys",
        "typing",
        no_sibling_lanes=True,
    ),
    "image_release": _row(
        "__future__",
        "collections",
        "dataclasses",
        "datetime",
        "estate_clients",
        "httpx",
        "json",
        "os",
        "pathlib",
        "re",
        "subprocess",
        # `time.sleep` alone, between production probe attempts; injectable, so tests never wait.
        "time",
        "typer",
        "typing",
        no_sibling_lanes=True,
    ),
    # Two already-confined readers reused rather than re-implemented: the producer's
    # landing-policy client (one path, a READ-scoped credential) and the deploy watcher's
    # read-only GitHub reader (every verb a GET, enforced before the transport). `lander` is the
    # shared body, as above.
    "inert_lander": _row(
        "__future__",
        "argparse",
        "bump_proposer",
        "dataclasses",
        "deploy_watcher",
        "estate_clients",
        "httpx",
        "lander",
        "os",
        "sys",
        "typing",
    ),
    # The ONE body both landing callers share (Tier 3 item 27). It borrows from no program and
    # imports no HTTP client: it reaches the orchestrator only through the confined client a lane
    # passes in, whose surface is that lane's own literal paths. Were it to import a lane, the two
    # programs ADR-0038 keeps separate would be one again by the back door.
    "lander": _row(
        "__future__",
        "dataclasses",
        "typing",
        no_sibling_lanes=True,
    ),
    # ADR-0050: the one confined transport every program reaches a service through. It holds the
    # guards and none of any program's wording, and imports no program.
    "estate_clients": _row(
        "__future__",
        "collections",
        "httpx",
        "typing",
        # `urllib.parse` only, for the base-URL shape check.
        "urllib",
        no_sibling_lanes=True,
        library=True,
    ),
    "landing_ledger": _row(
        "__future__",
        "dataclasses",
        "datetime",
        "estate_clients",
        "hashlib",
        "httpx",
        "json",
        "os",
        "re",
        "typer",
        "typing",
    ),
    # Lanes share DOMAIN knowledge; this one has none to borrow. Its transport is the shared
    # confined client (ADR-0050), which is plumbing with no lane of its own.
    "pin_watcher": _row(
        "__future__",
        "base64",
        "dataclasses",
        "estate_clients",
        "hashlib",
        "httpx",
        "json",
        "os",
        "re",
        "typer",
        "typing",
        # `urllib.parse` only, for the base-URL shape check.
        "urllib",
        no_sibling_lanes=True,
    ),
    # ADR-0002: it reads reality and files what it saw. The lanes import one another for DOMAIN
    # knowledge and never for plumbing. It reads its subjects from the member record through
    # `sds_members`' confined reader, rather than writing a third copy of that client, so it does
    # not carry `no_sibling_lanes`.
    "revision_watcher": _row(
        "__future__",
        "contextlib",
        "dataclasses",
        "datetime",
        "estate_clients",
        "hashlib",
        "httpx",
        "json",
        "os",
        "sds_members",
        "typer",
        "typing",
        "urllib",
    ),
    # Its window predicate is the orchestrator's, deliberately COPIED -- see `window.py`.
    # ADR-0054 amendment 1: "it reuses bump_proposer's audited revise, publish and propose path,
    # its confined client and its observation". So `bump_proposer` is reused rather than copied --
    # the lifecycle call, the hash re-pin, the one publishing act and both confined clients -- and
    # this program imports no HTTP client of its own. It reaches infraops only by running its CLI.
    # ADR-0055: the credential-rotation executor. It reads git, never a working copy, so
    # `subprocess` runs git and nothing else; `tomllib` and `yaml` parse the registry and the
    # package it reads there. `bitwarden_sdk` is its one added dependency, so values reach BWS
    # in-process rather than in an argument list (decision 3). `estate_clients` is the shared
    # confined transport, and `httpx` its type; `hashlib`, `hmac` and `secrets` fingerprint,
    # compare and mint values; `time` is the OpenRouter adapter's sleep between rechecks of a key it
    # deleted (ADR-0055 amendment 8). `ipaddress` and `urllib` (its URL parser only) let the
    # Coolify client send its token over plain HTTP to a tailnet address and nowhere else
    # (amendment 9). The rotation App (increment 7) signs its login with `jwt` and `cryptography`
    # (both already the orchestrator's) and seals an Actions secret with `nacl`, the one added
    # dependency for it; `base64` carries the sealed value and Bitbucket's Basic auth.
    "rotation_worker": _row(
        "__future__",
        "argparse",
        "base64",
        "bitwarden_sdk",
        "collections",
        "cryptography",
        "dataclasses",
        "datetime",
        "enum",
        "estate_clients",
        # The hidden prompt a person stages a new BWS access token through (amendment 12).
        "getpass",
        "hashlib",
        "hmac",
        "httpx",
        "ipaddress",
        "json",
        "jwt",
        "logging",
        "nacl",
        "os",
        "pathlib",
        "re",
        "secrets",
        "stat",
        # The BWS-token probe's known-bad control, a well-formed random token.
        "string",
        "subprocess",
        "sys",
        # The throwaway HOME a fresh Bitwarden login runs in (amendment 1).
        "tempfile",
        "time",
        "tomllib",
        "typing",
        "unicodedata",
        "urllib",
        "uuid",
        "yaml",
        # The scheduled lanes' night hours, which a BWS-token switch waits out.
        "zoneinfo",
        no_sibling_lanes=True,
    ),
    "rotation_proposer": _row(
        "__future__",
        "argparse",
        "bump_proposer",
        # `collections.abc.Callable`, for the injectable runner that asks infraops.
        "collections",
        "dataclasses",
        # The observation's facts are content-addressed, so an unchanged re-run replays.
        "hashlib",
        "json",
        "os",
        "pathlib",
        "re",
        # `shutil.which("node")`: launchd's PATH does not carry a Homebrew node.
        "shutil",
        # Runs infraops' `security-drift-cli cred-findings`; every git and lifecycle act is
        # `bump_proposer`'s.
        "subprocess",
        "sys",
        "typing",
    ),
    # The fork onboarding tool. Operator-run, never scheduled. It reuses two sibling readers rather
    # than writing third copies (`revision_watcher`'s App Brain landing reader, and
    # `tool_installer`'s TOOLS table, which it only reads), so it does not carry
    # `no_sibling_lanes`. `urllib` is `urllib.request` in `fork_sync_review.py`, the script it
    # copies into each fork: that file runs on the fork's Actions runner, never here. `time` is
    # `fork-sync ack`'s pause between polls of its pull request's checks, and `math` is the
    # review script's CVSS roundup. `fork-sync onboard` (ADR-0056 amendment 5) adds five:
    # `jwt` and `cryptography` sign the one JWT it asks GitHub with, as the sync App, whether the
    # App is installed on a fork; `base64` and `binascii` decode that App's PEM from its BWS
    # keeper; and `shutil` finds the `bws` and `uv` executables, which launchd's PATH may miss.
    "fork_sync": _row(
        "__future__",
        "base64",
        "binascii",
        "collections",
        "cryptography",
        "dataclasses",
        "datetime",
        "estate_clients",
        "fnmatch",
        "hashlib",
        "httpx",
        "importlib",
        "json",
        "jwt",
        "math",
        "os",
        "pathlib",
        "re",
        "revision_watcher",
        "shlex",
        "shutil",
        "subprocess",
        "sys",
        "tempfile",
        "time",
        "tomllib",
        "tool_installer",
        "typer",
        "typing",
        "urllib",
    ),
    # ADR-0057. The fork-hardening pass: reads and pushes a fork with the operator's own git
    # credentials, through `subprocess` alone, and imports no sibling lane. `tempfile` holds the
    # private index a hardened tree is built in; `fnmatch` matches a rule's paths; `hashlib` is
    # the sha256 a read line is keyed by; `sys` is `sys.stdlib_module_names`, which decides
    # whether `python3 -m <module>` survives `-I`.
    "fork_harden": _row(
        "__future__",
        "collections",
        "dataclasses",
        "fnmatch",
        "hashlib",
        "json",
        "os",
        "pathlib",
        "re",
        "subprocess",
        "sys",
        "tempfile",
        "typer",
        "typing",
        no_sibling_lanes=True,
    ),
    "sds_members": _row(
        "__future__",
        "collections",
        "dataclasses",
        "estate_clients",
        "hashlib",
        "httpx",
        "json",
        "os",
        "pathlib",
        "tomllib",
        "typer",
        "typing",
        no_sibling_lanes=True,
        library=True,
    ),
    # ADR-0058. Compares the member record with the lists the other lanes carry, so it imports
    # those lanes' own data and readers (declarations, landing policy, fork list, rotation
    # registry) rather than keeping a third copy of each. It reads the record through
    # `sds_members`' confined client. That makes it a program that reuses siblings, so it does
    # not carry `no_sibling_lanes`.
    "membership_check": _row(
        "__future__",
        "bump_proposer",
        "change_proposer",
        "collections",
        "dataclasses",
        "deploy_watcher",
        "fork_harden",
        "fork_sync",
        # `declaration.py`, moved here from the carrier: it reads each repository's
        # `factory-target.toml` from GitHub, which this program compares with the record.
        "httpx",
        "os",
        "pin_watcher",
        "re",
        "rotation_worker",
        "sds_members",
        "tomllib",
        "tool_installer",
        "typer",
        "typing",
        "work_carrier",
    ),
    "tool_installer": _row(
        "__future__",
        "dataclasses",
        "datetime",
        "estate_clients",
        "hashlib",
        "httpx",
        "json",
        "os",
        "pathlib",
        "re",
        "shutil",
        "subprocess",
        # `npm.py` unpacks the tarball `npm pack` produced, with the `data` extraction filter.
        "tarfile",
        # `npm.py` builds in a temporary directory inside the install root.
        "tempfile",
        "typer",
        "typing",
        # `urllib.parse` only, for the base-URL shape check.
        "urllib",
        "zoneinfo",
        no_sibling_lanes=True,
    ),
    "tracker_projection_adapter": _row(
        "__future__",
        "dataclasses",
        "datetime",
        "estate_clients",
        "httpx",
        "json",
        "os",
        "re",
        "typer",
        "typing",
    ),
    # The carry builds its intake payload by running `orchestrator emit-intake-payload`, not by
    # importing the function that backs it -- running the command is what makes its output
    # byte-identical to what a human produces by hand.
    "work_carrier": _row(
        "__future__",
        "argparse",
        "dataclasses",
        "estate_clients",
        "httpx",
        "json",
        "os",
        "pathlib",
        # For the read allowlist's anchored path template: the path carries an id, so what has to
        # be asserted is a TEMPLATE, and a hand-rolled split-and-check is a parser nobody reviews.
        # The member record is the scope authority; the carrier reads it through that library.
        "re",
        "sds_members",
        "subprocess",
        "sys",
        # The capability source's age report.
        "time",
        "typing",
    ),
    # The listing is the carry's, deliberately: one question, one parse, and one place where the
    # pipeline is named in the query. It asks the orchestrator over HTTP for the completion
    # verdict; importing the function that derives it would make it a second implementation.
    "work_watcher": _row(
        "__future__",
        "argparse",
        "estate_clients",
        "httpx",
        "os",
        "re",
        "sys",
        "typing",
        "work_carrier",
    ),
}


def population_violations(src: Path, table: Mapping[str, Row]) -> list[str]:
    """What is wrong with the table as a description of the programs on disk."""
    packages = set(out_of_process_packages(src))
    return sorted(
        [f"{name}: a program under src/ with no isolation row" for name in packages - set(table)]
        + [
            f"{name}: an isolation row for a program not under src/"
            for name in set(table) - packages
        ]
    )


def violations(src: Path, table: Mapping[str, Row], package: str) -> list[str]:
    """Everything one program's row forbids that the tree does, each naming file and import."""
    population = set(out_of_process_packages(src))
    if package not in table:
        return [f"{package}: a program under src/ with no isolation row"]
    row = table[package]
    found: list[str] = []

    if ORCHESTRATOR in row.allowed:
        found.append(f"{package}: its row permits importing the orchestrator")
    if row.library and not row.no_sibling_lanes:
        found.append(f"{package}: a library row must forbid sibling programs")
    libraries = {name for name, other in table.items() if other.library}
    if row.no_sibling_lanes:
        found += [
            f"{package}: its row permits sibling program {sibling!r}"
            for sibling in sorted(row.allowed & (population - {package} - libraries))
        ]

    imports = package_imports(src, package)
    if not imports:
        found.append(f"{package}: the scan found no imports, so every check passed vacuously")
    found += [
        f"{package}: {record.describe(src)} (not in its row)"
        for record in imports
        if record.top != package and record.top not in row.allowed
    ]
    if not row.library:
        found += [
            f"{package}: orchestrator {record.describe(src)}"
            for record in package_imports(src, ORCHESTRATOR)
            if record.top == package
        ]
    return found


def test_the_table_is_the_population_on_disk() -> None:
    assert population_violations(SRC, TABLE) == []


def test_the_orchestrator_scan_is_not_trivially_empty() -> None:
    """The reverse direction scans the orchestrator once per program; an empty scan passes all."""
    assert len(package_imports(SRC, ORCHESTRATOR)) > 100


@pytest.mark.parametrize("package", out_of_process_packages(SRC))
def test_each_program_imports_only_its_row_and_the_orchestrator_imports_none_of_it(
    package: str,
) -> None:
    assert violations(SRC, TABLE, package) == []


# ------------------------------------------------------------------------------------------------
# Controls: each shape the table exists to refuse, shown refused on a synthetic tree.
# ------------------------------------------------------------------------------------------------


def _tree(tmp_path: Path, files: Mapping[str, str]) -> Path:
    for relative, text in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return tmp_path


CLEAN = {
    "orchestrator/__init__.py": "import json\n",
    "orchestrator/app.py": "from orchestrator import db\n",
    "lane/__init__.py": "import httpx\n",
    "lane/client.py": "from lane import helpers\nimport os\n",
    "other/__init__.py": "import json\n",
}
CLEAN_TABLE = {"lane": _row("httpx", "os"), "other": _row("json")}


def test_control_a_clean_tree_passes(tmp_path: Path) -> None:
    src = _tree(tmp_path, CLEAN)
    assert population_violations(src, CLEAN_TABLE) == []
    assert violations(src, CLEAN_TABLE, "lane") == []
    assert violations(src, CLEAN_TABLE, "other") == []


def test_control_a_program_without_a_row_fails(tmp_path: Path) -> None:
    src = _tree(tmp_path, {**CLEAN, "newcomer/__init__.py": "import os\n"})
    assert population_violations(src, CLEAN_TABLE) == [
        "newcomer: a program under src/ with no isolation row"
    ]
    assert violations(src, CLEAN_TABLE, "newcomer") == [
        "newcomer: a program under src/ with no isolation row"
    ]


def test_control_a_row_for_a_vanished_program_fails(tmp_path: Path) -> None:
    src = _tree(tmp_path, CLEAN)
    assert population_violations(src, {**CLEAN_TABLE, "gone": _row("os")}) == [
        "gone: an isolation row for a program not under src/"
    ]


def test_control_a_dependency_outside_the_row_fails_naming_program_and_import(
    tmp_path: Path,
) -> None:
    src = _tree(tmp_path, {**CLEAN, "lane/client.py": "import os\nimport requests.adapters\n"})
    assert violations(src, CLEAN_TABLE, "lane") == [
        "lane: lane/client.py:2: imports requests.adapters (not in its row)"
    ]


def test_control_importing_the_orchestrator_fails_even_from_a_function_body(
    tmp_path: Path,
) -> None:
    body = "import os\n\ndef later():\n    from orchestrator.services import lifecycle\n"
    src = _tree(tmp_path, {**CLEAN, "lane/client.py": body})
    assert violations(src, CLEAN_TABLE, "lane") == [
        "lane: lane/client.py:4: imports orchestrator.services (not in its row)"
    ]


def test_control_a_row_that_permits_the_orchestrator_fails(tmp_path: Path) -> None:
    src = _tree(tmp_path, CLEAN)
    table = {**CLEAN_TABLE, "lane": _row("httpx", "os", "orchestrator")}
    assert violations(src, table, "lane") == ["lane: its row permits importing the orchestrator"]


def test_control_the_orchestrator_importing_a_program_fails_naming_file_and_import(
    tmp_path: Path,
) -> None:
    src = _tree(tmp_path, {**CLEAN, "orchestrator/app.py": "import lane.client as c\n"})
    assert violations(src, CLEAN_TABLE, "lane") == [
        "lane: orchestrator orchestrator/app.py:1: imports lane.client"
    ]
    assert violations(src, CLEAN_TABLE, "other") == []
    # A library is shared plumbing, not a program taking a reading, so the orchestrator may use it.
    library = {**CLEAN_TABLE, "lane": _row("httpx", "os", no_sibling_lanes=True, library=True)}
    assert violations(src, library, "lane") == []


def test_control_a_sibling_in_a_no_sibling_row_fails_against_the_derived_population(
    tmp_path: Path,
) -> None:
    src = _tree(tmp_path, {**CLEAN, "lane/client.py": "import os\nfrom other import x\n"})
    table = {**CLEAN_TABLE, "lane": _row("httpx", "os", "other", no_sibling_lanes=True)}
    assert violations(src, table, "lane") == ["lane: its row permits sibling program 'other'"]
    # The same row without the flag is an ordinary reuse, which is how `inert_lander` borrows.
    assert violations(src, {**table, "lane": _row("httpx", "os", "other")}, "lane") == []


def test_control_a_library_is_not_a_sibling_but_a_library_row_must_ban_siblings(
    tmp_path: Path,
) -> None:
    src = _tree(tmp_path, {**CLEAN, "lane/client.py": "import os\nfrom other import x\n"})
    lane = _row("httpx", "os", "other", no_sibling_lanes=True)
    library = _row("json", no_sibling_lanes=True, library=True)
    assert violations(src, {**CLEAN_TABLE, "lane": lane, "other": library}, "lane") == []
    loose = _row("json", library=True)
    assert violations(src, {**CLEAN_TABLE, "lane": lane, "other": loose}, "other") == [
        "other: a library row must forbid sibling programs"
    ]


def test_control_a_program_whose_scan_is_empty_fails(tmp_path: Path) -> None:
    src = _tree(tmp_path, {**CLEAN, "other/__init__.py": '"""Nothing imported."""\n'})
    assert violations(src, CLEAN_TABLE, "other") == [
        "other: the scan found no imports, so every check passed vacuously"
    ]


def test_control_a_relative_import_is_charged_to_the_package_that_owns_it(tmp_path: Path) -> None:
    """None exist under `src/` today. The day one does, it is its own package, not a name `x`."""
    src = _tree(tmp_path, {**CLEAN, "lane/client.py": "import os\nfrom . import helpers\n"})
    assert violations(src, CLEAN_TABLE, "lane") == []
    src = _tree(tmp_path, {**CLEAN, "lane/sub/deep.py": "from ..client import thing\n"})
    records = [r.module for r in package_imports(src, "lane") if r.path.name == "deep.py"]
    assert records == ["lane.client"]


@pytest.mark.parametrize("level", [4, 5])
def test_control_a_relative_import_above_the_top_package_is_refused_not_wrapped(
    tmp_path: Path, level: int
) -> None:
    """Slicing with a negative bound counts from the end, so an over-climb could read as a real
    name -- at level 4 below as `lane.x`, which the program's own row permits. It must fail."""
    dots = "." * level
    body = f"import os\ndef later():\n    from {dots}x import y\n"
    src = _tree(tmp_path, {**CLEAN, "lane/a/b.py": body})
    records = [r.module for r in package_imports(src, "lane") if r.path.name == "b.py"]
    assert records == ["os", f"{dots}x"]
    assert violations(src, CLEAN_TABLE, "lane") != []
