"""The one scanner for the processes a module can start.

Every scope is read: the module, each function and method however it is named, and each lambda.
A call starts a process when it resolves through the module's imports to a known starter, or when
the module imports `subprocess` and the call is `run`-shaped (`run`, `_run`, `self._run`,
`runner`), the form an injected runner takes. A helper that forwards a parameter to either is
followed to its callers, to any depth.

What a call starts is reported as the code spells it: the first element of its argument list, a
named constant by its name. What can't be resolved fails closed, reported by its source text, so a
table row has to name it. The same goes for a starter or the `subprocess` module held as a value,
any import of a module whose purpose is starting something, and `exec`, `eval` and `__import__`.

Not seen: a callable under a name that isn't `run`-shaped, handed in from another module, when
the module holding it imports none of the above.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Final

STARTERS: Final = frozenset(
    {
        "asyncio.create_subprocess_exec",
        "asyncio.create_subprocess_shell",
        "asyncio.subprocess.create_subprocess_exec",
        "asyncio.subprocess.create_subprocess_shell",
        "subprocess.Popen",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "subprocess.getoutput",
        "subprocess.getstatusoutput",
        "subprocess.run",
    }
)
OS_STARTERS: Final = re.compile(
    r"os\.(system|popen|spawn\w*|exec\w*|posix_spawn\w*|fork\w*|forkpty)"
)
PROCESS_MODULES: Final = frozenset({"importlib", "multiprocessing", "pexpect", "pty", "sh"})
DYNAMIC: Final = frozenset({"__import__", "eval", "exec"})
MODULES_NOT_VALUES: Final = frozenset({"os", "subprocess"})
RUNNER: Final = re.compile(r"_?run(ner)?")
LANDMARK: Final = "bitwarden_sdk"
_FUNCTIONS: Final = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


@dataclass
class _Scope:
    node: ast.AST
    parent: _Scope | None
    parameters: tuple[str, ...] = ()
    bound: dict[str, list[ast.expr]] = field(default_factory=dict)
    calls: list[ast.Call] = field(default_factory=list)
    returns: list[ast.expr] = field(default_factory=list)


def _parameters(node: ast.AST) -> tuple[str, ...]:
    if not isinstance(node, _FUNCTIONS):
        return ()
    args = node.args
    return tuple(arg.arg for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs))


def _own_nodes(node: ast.AST) -> Iterator[ast.AST]:
    """Everything under `node` outside any function or lambda nested in it, which yield too."""
    body = [node.body] if isinstance(node, ast.Lambda) else getattr(node, "body", [])
    pending: list[ast.AST] = list(body)
    if isinstance(node, _FUNCTIONS):
        pending += [*node.args.defaults, *node.args.kw_defaults]
    while pending:
        current = pending.pop()
        if current is None:
            continue
        yield current
        if not isinstance(current, _FUNCTIONS):
            pending += list(ast.iter_child_nodes(current))


def _scopes(tree: ast.Module) -> list[_Scope]:
    found: list[_Scope] = []

    def enter(node: ast.AST, parent: _Scope | None) -> None:
        scope = _Scope(node, parent, _parameters(node))
        found.append(scope)
        for inner in _own_nodes(node):
            if isinstance(inner, _FUNCTIONS):
                enter(inner, scope)
            elif isinstance(inner, ast.Call):
                scope.calls.append(inner)
            elif isinstance(inner, ast.Return) and inner.value is not None:
                scope.returns.append(inner.value)
            elif isinstance(inner, ast.Assign):
                for target in inner.targets:
                    if isinstance(target, ast.Name):
                        scope.bound.setdefault(target.id, []).append(inner.value)
            elif isinstance(inner, ast.AnnAssign) and inner.value is not None:
                if isinstance(inner.target, ast.Name):
                    scope.bound.setdefault(inner.target.id, []).append(inner.value)

    enter(tree, None)
    return found


def _aliases(tree: ast.Module) -> dict[str, str]:
    """Each local name an import binds, and the dotted name it stands for."""
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found[alias.asname or alias.name.split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                found[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return found


def _tops(tree: ast.Module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[0])
    return found


class _Scan:
    def __init__(self, tree: ast.Module) -> None:
        self.aliases = _aliases(tree)
        self.scopes = _scopes(tree)
        self.heads: set[str] = set()
        # Function name -> the parameters it hands to something that starts a process.
        self.forwarders: dict[str, set[str]] = {}
        self.named: dict[str, list[_Scope]] = {}
        for scope in self.scopes:
            if isinstance(scope.node, ast.FunctionDef | ast.AsyncFunctionDef):
                self.named.setdefault(scope.node.name, []).append(scope)

    def dotted(self, node: ast.expr) -> str | None:
        match node:
            case ast.Name(id=name):
                return self.aliases.get(name, name)
            case ast.Attribute(value=value, attr=attr):
                base = self.dotted(value)
                return f"{base}.{attr}" if base else None
            case _:
                return None

    def resolve(self, value: ast.expr, scope: _Scope, seen: set[tuple[int, str]]) -> set[str]:
        """What `value` starts. A parameter marks its function as a forwarder instead."""
        match value:
            case ast.List(elts=[ast.Constant(value=str(program)), *_]):
                return {program}
            case ast.Tuple(elts=[ast.Constant(value=str(program)), *_]):
                return {program}
            case ast.List(elts=[first, *_]) | ast.Tuple(elts=[first, *_]):
                return {ast.unparse(first)}
            case ast.Constant(value=str(command)):
                return {command}
            case ast.Name(id=name):
                return self._name(name, scope, seen)
            case ast.Call(func=ast.Name(id="list" | "tuple"), args=[inner]):
                return self.resolve(inner, scope, seen)
            case ast.Call(func=ast.Attribute(attr="split", value=ast.Constant(value=str(text)))):
                return {text}
            case ast.Call(func=ast.Attribute(attr="split", value=spelled)):
                return {ast.unparse(spelled)}
            case ast.Call(func=ast.Name(id=name)) if name in self.named:
                found: set[str] = set()
                for callee in self.named[name]:
                    for returned in callee.returns:
                        found |= self.resolve(returned, callee, seen)
                return found or {ast.unparse(value)}
            case ast.BinOp(left=left):
                return self.resolve(left, scope, seen)
            case ast.IfExp(body=body, orelse=orelse):
                return self.resolve(body, scope, seen) | self.resolve(orelse, scope, seen)
            case _:
                return {ast.unparse(value)}

    def _name(self, name: str, scope: _Scope, seen: set[tuple[int, str]]) -> set[str]:
        found: set[str] = set()
        current: _Scope | None = scope
        while current is not None:
            if name in current.bound or name in current.parameters:
                key = (id(current), name)
                if key in seen:
                    return found
                seen.add(key)
                for value in current.bound.get(name, []):
                    found |= self.resolve(value, current, seen)
                if name in current.parameters:
                    self._forward(current, name, found)
                return found
            current = current.parent
        return {name}

    def _forward(self, scope: _Scope, name: str, found: set[str]) -> None:
        if isinstance(scope.node, ast.FunctionDef | ast.AsyncFunctionDef):
            self.forwarders.setdefault(scope.node.name, set()).add(name)
        else:
            found.add(name)  # a lambda has no name to follow to its callers

    def _targets(self, call: ast.Call, scope: _Scope, positions: list[ast.expr]) -> None:
        keyword = [item.value for item in call.keywords if item.arg == "args"]
        for argument in (*positions, *keyword):
            self.heads |= self.resolve(argument, scope, set())

    def _forwarded(self, call: ast.Call, scope: _Scope, name: str) -> None:
        for callee in self.named[name]:
            parameters = list(callee.parameters)
            if isinstance(call.func, ast.Attribute) and parameters[:1] in (["self"], ["cls"]):
                parameters = parameters[1:]
            for position, argument in enumerate(call.args):
                if position < len(parameters) and parameters[position] in self.forwarders[name]:
                    self.heads |= self.resolve(argument, scope, set())
            for item in call.keywords:
                if item.arg in self.forwarders[name]:
                    self.heads |= self.resolve(item.value, scope, set())

    def _call(self, call: ast.Call, scope: _Scope, runners: bool) -> None:
        spelled = self.dotted(call.func)
        name = (spelled or ast.unparse(call.func)).rsplit(".", 1)[-1]
        if spelled in DYNAMIC:
            self.heads.add(spelled)
        elif spelled in STARTERS or (spelled and OS_STARTERS.fullmatch(spelled)):
            if spelled != "subprocess.run":
                self.heads.add(spelled)
            self._targets(call, scope, call.args[:1])
        elif name in self.forwarders:
            self._forwarded(call, scope, name)
        elif runners and RUNNER.fullmatch(name) and name not in self.named:
            self._targets(call, scope, call.args)

    def run(self, runners: bool) -> None:
        while True:
            before = (len(self.heads), sum(len(names) for names in self.forwarders.values()))
            for scope in self.scopes:
                for call in scope.calls:
                    self._call(call, scope, runners)
            if before == (len(self.heads), sum(len(names) for names in self.forwarders.values())):
                return

    def uncalled_forwarders(self) -> set[str]:
        """A forwarder nothing here calls is called from elsewhere, with arguments not seen. A
        `run`-shaped one is the exception: its callers are the `run`-shaped calls."""
        called = {
            (self.dotted(call.func) or ast.unparse(call.func)).rsplit(".", 1)[-1]
            for scope in self.scopes
            for call in scope.calls
        }
        return {
            f"{name}(forwarded)"
            for name in self.forwarders
            if name not in called and not RUNNER.fullmatch(name)
        }

    def held_as_values(self, tree: ast.Module) -> set[str]:
        called = {id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
        bases = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        found: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Name | ast.Attribute) or id(node) in called:
                continue
            spelled = self.dotted(node)
            if spelled in STARTERS:
                found.add(f"{spelled} as a value")
            elif (
                isinstance(node, ast.Name)
                and id(node) not in bases
                and node.id in self.aliases
                and self.aliases[node.id] in MODULES_NOT_VALUES
            ):
                found.add(f"{self.aliases[node.id]} as a value")
        return found


def process_heads(text: str) -> frozenset[str]:
    """Every program the module can start, as its code spells it. A module importing `subprocess`
    and starting nothing is `subprocess`; one importing `bitwarden_sdk` says so."""
    tree = ast.parse(text)
    tops = _tops(tree)
    scan = _Scan(tree)
    scan.run(runners="subprocess" in tops)
    heads = scan.heads | scan.uncalled_forwarders() | scan.held_as_values(tree)
    heads |= {f"import {module}" for module in tops & PROCESS_MODULES}
    if "subprocess" in tops and not heads:
        heads.add("subprocess")
    if LANDMARK in tops:
        heads.add(LANDMARK)
    return frozenset(heads)
