"""What the edge tables share: a row's shape, the bearers, and how a row is held to the code.

`test_bridge_edges.py`, `test_landing_edges.py`, `test_rotation_edges.py` and
`test_execution_edges.py` each state one layer's outbound surface. Each row is compared with the
predicates the lane's `ConfinedClient` really asks, so the comparison and the capture of an inline
predicate live here, once.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple
from unittest import mock

import alert_hub_client
from estate_clients.confined import has_control_character
from tests.architecture.import_scan import file_imports
from tests.architecture.process_scan import process_heads

Route = tuple[str, str]
Permits = Callable[[str, str], bool]

PROPOSE_BEARER = "acccb346-4baa-43ec-a1d4-b4a400c048ee"
READ_BEARER = "314f276d-55ca-4ddc-a24d-b4a3013508cd"
OBSERVER_BEARER = "f793576f-e9aa-4f9d-8089-b4a000b9e2d5"
SYSTEM_BEARER = "221a48d5-3f29-4898-b300-b4820140c880"
# The work carrier's own observer bearer (ADR-0055 amendment 22).
WORK_CARRIER_OBSERVATION_BEARER = "a9843d17-e599-4e80-ab1b-b4ff0164d8e2"
# The `sds-bridge` alert sender token (bridge Part 3).
SDS_BRIDGE_ALERT_BEARER = "b415d03b-f92d-46b1-8a9d-b4f10103df49"
# The landers' and the rotation verifier's GitHub token is not a BWS secret: each launcher takes
# it from the gh CLI.
GH_AUTH_TOKEN = "gh auth token"
# Every launcher arms a Healthchecks check through `sds-deadman.sh`: the Management API key that
# finds the check, and the ping to it.
DEADMAN_API_KEY = "51509d5e-64f5-4781-ae82-b4ed00b644e1"
DEADMAN_PING = "healthchecks ping"
DEADMAN = frozenset({DEADMAN_API_KEY, DEADMAN_PING})

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)


@dataclass(frozen=True)
class Edge:
    change_manager: frozenset[Route]
    orchestrator: frozenset[Route]
    github: frozenset[Route]
    alert_hub: frozenset[Route]
    bearers: frozenset[str]

    def columns(self) -> dict[str, frozenset[Route]]:
        return {
            "change_manager": self.change_manager,
            "orchestrator": self.orchestrator,
            "github": self.github,
            "alert_hub": self.alert_hub,
        }


@dataclass(frozen=True)
class ServiceEdge:
    """One client of the orchestrator itself, which isn't a lane: no launcher, no BWS bearer."""

    base_url: str
    routes: frozenset[Route]
    credential: str


class Pin(NamedTuple):
    """One fixture held byte-identical with another repository, and what pins it on each side."""

    fixture: str
    test: str
    constant: str
    digest: Callable[[bytes], str]
    counterpart_test: str
    counterpart_constant: str


class BuiltClient(NamedTuple):
    base_url: str
    permits: Permits


class _Built(BaseException):
    """Ends the capture at a client's first request. Not an `Exception`: no program catches it."""


def built_clients(module: object, call: Callable[[], object]) -> list[BuiltClient]:
    """Every `ConfinedClient` the module builds during `call`, in order, with what it was given.

    Some readers build their predicate as a lambda at the call site rather than naming it, and some
    close over the instance's own repository or application. The recorder stands in for
    `ConfinedClient` on that module only, keeps the real predicate, and stops the call at the first
    request, so nothing is sent. A lambda reads its module's globals when it runs, so a later
    monkeypatch of the module still reaches it.
    """
    built: list[BuiltClient] = []

    def recorder(*, base_url: str, permits: Permits, **_: object) -> object:
        built.append(BuiltClient(base_url, permits))
        client = mock.MagicMock()
        client.request.side_effect = _Built
        return client

    with mock.patch.object(module, "ConfinedClient", recorder), contextlib.suppress(_Built):
        call()
    assert built, f"{module!r} built no ConfinedClient"
    return built


def captured_permits(module: object, call: Callable[[], object]) -> Permits:
    """The predicate of the one `ConfinedClient` the module builds during `call`."""
    [client] = built_clients(module, call)
    return client.permits


# The predicate every lane's alert client asks: captured, because the client holds it inline.
ALERT_HUB_PERMITS = captured_permits(
    alert_hub_client,
    lambda: alert_hub_client.KeyedAlerts(
        lambda: "token", Path("unused.json"), user_agent="edges/1"
    ).open_client(),
)


def either(*predicates: Permits) -> Permits:
    return lambda method, path: any(predicate(method, path) for predicate in predicates)


def sendable(permits: Permits, method: str, path: str) -> bool:
    """Whether a `ConfinedClient` holding this predicate would send the route."""
    return not has_control_character(path) and permits(method, path)


def near_misses(paths: Iterable[str]) -> frozenset[str]:
    """Each path, and its trailing-slash, suffixed and trailing-newline near misses."""
    listed = frozenset(paths)
    return frozenset(path + end for path in listed for end in ("", "/", "x", "\n"))


def route_disagreements(
    edge: Edge,
    permits: Mapping[str, Permits | None],
    corpus: Collection[str],
    methods: Iterable[str],
) -> frozenset[tuple[str, str, str, bool]]:
    """(service, method, path, accepted) for each corpus route where the code and the row differ.

    `None` is a service the lane has no client for: it permits nothing.
    """
    found: set[tuple[str, str, str, bool]] = set()
    for service, listed in edge.columns().items():
        predicate = permits[service]
        for method in methods:
            for path in corpus:
                accepted = predicate is not None and sendable(predicate, method, path)
                if accepted != ((method, path) in listed):
                    found.add((service, method, path, accepted))
    return frozenset(found)


_SOURCED = re.compile(r"^\s*(?:source|\.)\s+(.+)$", re.MULTILINE)
_SCRIPT = re.compile(r"([\w.-]+\.sh)\"?\s*$")
# Sourced from outside this repository, by a variable: `activate_checkout`, which fetches no
# secret. It does run `git fetch` and a fast-forward merge, so the launcher's commands are wider
# than its text shows.
_EXTERNAL_SOURCES = frozenset({'"$_SDS_ACTIVATE"'})


def launcher_text(path: Path) -> str:
    """A launcher and everything it sources, transitively. A `source` this can't place fails."""
    texts: dict[Path, str] = {}
    waiting = [path]
    while waiting:
        current = waiting.pop()
        if current in texts:
            continue
        texts[current] = current.read_text(encoding="utf-8")
        for sourced in _SOURCED.findall(texts[current]):
            script = _SCRIPT.search(sourced)
            if script:
                waiting.append(current.parent / script.group(1))
            else:
                assert sourced.strip() in _EXTERNAL_SOURCES, f"{current}: source {sourced}"
    return "\n".join(texts.values())


def launcher_secrets(path: Path) -> frozenset[str]:
    """The BWS secrets a launcher reaches, and its dead-man ping if it arms one."""
    text = launcher_text(path)
    named = {uuid.lower() for uuid in _UUID.findall(text)}
    return frozenset(named | ({DEADMAN_PING} if "SDS_DEADMAN_PING_URL" in text else set()))


def launcher_bearers(path: Path) -> frozenset[str]:
    """`launcher_secrets`, and its `gh` login if it reads one."""
    gh = GH_AUTH_TOKEN in launcher_text(path)
    return launcher_secrets(path) | ({GH_AUTH_TOKEN} if gh else set())


NETWORK_IMPORTS = frozenset(
    {"aiohttp", "alert_hub_client", "estate_clients", "http", "httpx", "requests", "socket"}
)


def script_network(path: Path) -> frozenset[str]:
    """The network modules one file imports directly. Not followed: what its imports reach."""
    modules = {found.module for found in file_imports(path)}
    tops = {name.split(".")[0] for name in modules} & NETWORK_IMPORTS
    return frozenset(tops | ({"urllib.request"} if "urllib.request" in modules else set()))


def script_processes(path: Path) -> frozenset[str]:
    """The programs one file can start, as `process_scan` spells them."""
    return process_heads(path.read_text(encoding="utf-8"))


class ShellUnplaced(ValueError):
    """Text the shell reader can't place. Failing is the honest answer: a guess hides a command."""


class Shell(NamedTuple):
    commands: frozenset[str]
    hosts: frozenset[str]
    sources: frozenset[str]


_ASSIGNMENT = re.compile(r"[A-Za-z_]\w*(\[[^\]]*\])?\+?=")
_HOST = re.compile(r"https?://[A-Za-z0-9.-]+")
_LEADING = frozenset({"if", "then", "else", "elif", "do", "while", "until", "!", "{", "time"})
_NOT_COMMANDS = frozenset({"for", "case", "in", "fi", "done", "esac", "}", "function"})
_BUILTINS = frozenset(
    {"[", "[[", "echo", "printf", "return", "exit", "local", "source", ".", "cd", "pwd", "export",
     "shift", "trap", "true", "false", ":", "read", "set", "unset", "break", "continue"}
)  # fmt: skip


def literal_hosts(text: str) -> frozenset[str]:
    """Every `http(s)://host` the text spells."""
    return frozenset(_HOST.findall(text))


class _Reader:
    """A quote-aware reader of shell text: each simple command's words, `$(...)` included.

    It follows quotes, `${}`, `$()`, `case` arms, function definitions, array literals and comments,
    and takes the head of each simple command after leading assignments and keywords (and past
    `env` and its options). It does not follow a command assembled in a variable, a wrapper other
    than `env`, or `eval`; a here-document's body is skipped unread; backticks and `$((` fail
    loudly. A `trap` handler written as one quoted string is read as commands.
    """

    def __init__(self, text: str) -> None:
        self.t = text
        self.i = 0
        self.heads: set[str] = set()
        self.hosts: set[str] = set()
        self.sources: set[str] = set()
        self.functions: set[str] = set()
        self.pending: list[str] = []

    def _fail(self, why: str) -> ShellUnplaced:
        return ShellUnplaced(f"{why} at {self.t[max(0, self.i - 20) : self.i + 20]!r}")

    def _skip_quote(self) -> None:
        quote = self.t[self.i]
        close = self.t.find(quote, self.i + 1) if quote == "'" else -1
        if quote == "'":
            if close < 0:
                raise self._fail("unterminated quote")
            self.i = close + 1
            return
        self.i += 1
        while self.i < len(self.t) and self.t[self.i] != '"':
            self._step(inside_double=True)
        if self.i >= len(self.t):
            raise self._fail("unterminated quote")
        self.i += 1

    def _step(self, inside_double: bool = False) -> None:
        """Advance over one character or one nested construct."""
        t, c = self.t, self.t[self.i]
        if c == "\\":
            self.i += 2
        elif c == "`":
            raise self._fail("backtick")
        elif t.startswith("$((", self.i):
            raise self._fail("arithmetic")
        elif t.startswith("$(", self.i):
            self.i += 2
            self._list(nested=True)
        elif t.startswith("${", self.i):
            self.i += 2
            depth = 1
            while depth:
                if self.i >= len(t):
                    raise self._fail("unterminated ${")
                if t[self.i] == "}":
                    depth -= 1
                    self.i += 1
                elif t.startswith("${", self.i):
                    depth += 1
                    self.i += 2
                else:
                    self._step(inside_double=False)
        elif c in "'\"" and not (inside_double and c == "'"):
            self._skip_quote()
        else:
            self.i += 1

    def _word(self) -> str:
        t, start = self.t, self.i
        while self.i < len(t):
            c = t[self.i]
            if c in " \t\n;|()" or (c == "&" and t[self.i - 1] not in "<>"):
                if c == "(" and t[start : self.i].endswith("="):
                    self.i += 1
                    while t[self.i] != ")":
                        self._step()
                    self.i += 1
                    continue
                break
            if t.startswith("<<", self.i) and not t.startswith("<<<", self.i):
                self.i += 2 + t.startswith("<<-", self.i)
                while t[self.i] in " \t":
                    self.i += 1
                self.pending.append(self._word().strip("'\""))
                break
            self._step()
        return t[start : self.i]

    def _skip_heredocs(self) -> None:
        for delimiter in self.pending:
            while self.i < len(self.t):
                end = self.t.find("\n", self.i)
                end = len(self.t) if end < 0 else end
                line = self.t[self.i : end].strip()
                self.i = min(end + 1, len(self.t))
                if line == delimiter:
                    break
        self.pending.clear()

    def _pattern(self, cases: list[str]) -> None:
        t = self.t
        while self.i < len(t):
            if t[self.i] in " \t\n":
                self.i += 1
            elif t[self.i] == "#":
                self.i = t.find("\n", self.i) % (len(t) + 1)
            else:
                break
        if t.startswith("esac", self.i):
            cases.pop()
            self.i += 4
            return
        while self.i < len(t) and t[self.i] != ")":
            self._step()
        if self.i >= len(t):
            raise self._fail("case arm without a close")
        self.i += 1
        cases[-1] = "body"

    def _list(self, nested: bool) -> None:
        t = self.t
        words: list[str] = []
        cases: list[str] = []
        parens = 0
        while self.i < len(t):
            if cases and cases[-1] == "pattern":
                self._pattern(cases)
                continue
            c = t[self.i]
            if c in " \t":
                self.i += 1
            elif c == "\n":
                self._finish(words)
                self.i += 1
                self._skip_heredocs()
            elif c == "\\" and t.startswith("\\\n", self.i):
                self.i += 2
            elif c == "#":
                end = t.find("\n", self.i)
                self.i = len(t) if end < 0 else end
            elif t.startswith(";;", self.i):
                self._finish(words)
                self.i += 2
                if cases:
                    cases[-1] = "pattern"
            elif c in ";|&":
                self._finish(words)
                self.i += 1
            elif c == "(":
                if len(self._strip(words)) == 1 and t.startswith("()", self.i):
                    self.functions.add(self._strip(words)[0])
                    words.clear()
                    self.i += 2
                else:
                    self._finish(words)
                    parens += 1
                    self.i += 1
            elif c == ")":
                self._finish(words)
                self.i += 1
                if parens:
                    parens -= 1
                elif nested:
                    return
                else:
                    raise self._fail("unbalanced )")
            else:
                word = self._word()
                if not self._strip(words) and word == "case":
                    cases.append("await_in")
                    continue
                if cases and cases[-1] == "await_in" and word == "in":
                    cases[-1] = "pattern"
                    words.clear()
                    continue
                if not self._strip(words) and word == "esac" and cases:
                    cases.pop()
                    continue
                words.append(word)
        if nested:
            raise self._fail("unterminated $(")
        self._finish(words)

    @staticmethod
    def _strip(words: list[str]) -> list[str]:
        rest = list(words)
        while rest and (rest[0] in _LEADING or _ASSIGNMENT.match(rest[0])):
            rest.pop(0)
        return rest

    def _finish(self, words: list[str]) -> None:
        self.hosts.update(host for word in words for host in literal_hosts(word))
        rest = self._strip(words)
        words.clear()
        if not rest or rest[0] in _NOT_COMMANDS:
            return
        head = rest[0]
        if head == "env":
            self.heads.add(head)
            index = 1
            while index < len(rest) and (
                rest[index].startswith("-") or _ASSIGNMENT.match(rest[index])
            ):
                index += 2 if rest[index] == "-u" else 1
            rest = rest[index:]
            head = rest[0] if rest else ""
        if head in {"source", "."} and len(rest) > 1:
            self.sources.add(rest[1])
        if head == "trap" and len(rest) > 1 and rest[1].startswith("'"):
            inner = _Reader(rest[1][1:-1])
            inner._list(nested=False)
            self.heads |= inner.heads
            self.hosts |= inner.hosts
        if head:
            self.heads.add(head)


def read_shell(text: str) -> Shell:
    """The external commands, literal hosts and sourced scripts a shell text names.

    A command is the head of a simple command as the text spells it, so `"$_PYTHON"` is reported
    under that spelling; builtins and the functions the text itself defines are not commands.
    Sources are the `.sh` names sourced. Hosts are every literal `http(s)://host` in a command's
    words, comments excluded. A function defined in a sourced script is a command here unless that
    text is included: use `launcher_text` to read a script with what it sources.
    """
    reader = _Reader(text)
    reader._list(nested=False)
    scripts = (_SCRIPT.search(source) for source in reader.sources)
    return Shell(
        frozenset(reader.heads - _BUILTINS - reader.functions),
        frozenset(reader.hosts),
        frozenset(found.group(1) for found in scripts if found),
    )
