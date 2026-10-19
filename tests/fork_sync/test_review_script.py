"""The fork-side script: the declaration, release selection, findings, the verdict and its exit.

Pure functions run against fakes. The tests that matter most for the gate run `build_report` and
`review` over real throwaway git repositories, because what is accounted for is only meaningful
against what git actually prints.
"""

from __future__ import annotations

import io
import json
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from fork_sync import fork_sync_review as review
from fork_sync.fork_sync_review import (
    Ack,
    Change,
    ConfigError,
    Finding,
    Report,
    ReviewUnavailable,
)

CARGO = """
upstream = "rtk-ai/rtk"
ecosystem = "cargo"
release_tag_pattern = '^v(\\d+\\.\\d+\\.\\d+)$'
[review]
paths_watched = [".claude/*"]
trust_critical = ["src/core/telemetry.rs"]
"""
NPM = """
upstream = "morluto/rea"
ecosystem = "npm"
release_tag_pattern = '^rea-agents-(\\d+\\.\\d+\\.\\d+)$'
test_commands = ["npm run test:run"]
"""


def _no_audit(argv: list[str], cwd: Path, env: dict[str, str]) -> tuple[int, bytes]:
    return 0, b""


# ---- the declaration and releases -----------------------------------------------------------


def test_defaults_come_from_the_ecosystem() -> None:
    config = review.parse_config(NPM)
    assert config.build_commands == ("npm run build",)
    assert config.test_commands == ("npm run test:run",)
    assert config.review.model == "claude-sonnet-5-5"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (CARGO + 'typo = "x"\n', "unknown key"),
        (CARGO + "bogus = 1\n", "unknown key\\(s\\) in \\[review\\]"),
        # Audits are built in; a declared command could run the head's code.
        (CARGO + 'audit_commands = ["true"]\n', "unknown key"),
        (CARGO + "max_diff_chars = 0\n", "positive integer"),
        (CARGO.replace('"cargo"', '"pip"'), "ecosystem"),
        (CARGO.replace("'^v(", "'^v").replace(")$'", "$'"), "capture group"),
        (CARGO.replace('"rtk-ai/rtk"', '"rtk"'), "owner/name"),
        (CARGO.replace('".claude/*"', "3"), "list of non-empty strings"),
        ("upstream = ", "TOML"),
    ],
)
def test_the_declaration_is_strict(text: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        review.parse_config(text)


def test_manifest_versions() -> None:
    assert review.manifest_version("npm", '{"version": "6.4.0"}') == "6.4.0"
    assert review.manifest_version("cargo", '[package]\nversion = "0.48.0"\n') == "0.48.0"
    workspace = '[package]\nversion.workspace = true\n[workspace.package]\nversion = "1.2.3"\n'
    assert review.manifest_version("cargo", workspace) == "1.2.3"
    with pytest.raises(ConfigError):
        review.manifest_version("npm", "{}")


def _release(tag: str, **flags: bool) -> dict[str, Any]:
    return {"tag_name": tag, "draft": False, "prerelease": False, **flags}


def test_next_release_skips_drafts_prereleases_and_other_tags() -> None:
    config = review.parse_config(NPM)
    releases = [
        _release("rea-agents-6.8.0", prerelease=True),
        _release("rea-cli-9.0.0"),
        _release("rea-agents-6.7.0"),
        _release("rea-agents-6.6.0"),
    ]
    assert review.next_release(config, releases, "6.4.0") == "rea-agents-6.7.0"
    assert review.next_release(config, releases, "6.7.0") is None
    assert review.next_release(config, [], "6.4.0") is None


def test_next_release_never_walks_main_back() -> None:
    """A newest release whose version is not ahead of the fork's is never synced to, and the
    comparison is numeric: 6.10.0 is ahead of 6.9.0."""
    config = review.parse_config(NPM)
    assert review.next_release(config, [_release("rea-agents-6.3.0")], "6.4.0") is None
    assert review.next_release(config, [_release("rea-agents-6.10.0")], "6.9.0-fork.1") == (
        "rea-agents-6.10.0"
    )


def test_an_unsafe_upstream_tag_is_refused() -> None:
    config = review.parse_config(CARGO.replace("'^v(", "'^v(.*"))
    with pytest.raises(ConfigError, match="characters"):
        review.next_release(config, [_release("v$(curl x) 1.0.0")], "0.1.0")


# ---- findings without the model -------------------------------------------------------------


def _change(path: str, status: str = "M", new: str = "n1", **modes: str) -> Change:
    return Change(
        status,
        path,
        modes.get("old_mode", "100644"),
        modes.get("new_mode", "100644"),
        "o1" if status != "A" else review.NULL_BLOB,
        new if status != "D" else review.NULL_BLOB,
    )


def test_parse_raw_and_numstat_with_z_keep_unusual_paths_verbatim() -> None:
    path = "dir/ünï côde\tx.js"
    raw = f":100644 100644 aaa bbb M\0{path}\0:000000 160000 000 ccc A\0vendor/lib\0".encode()
    changes = review.parse_raw_diff(raw)
    assert [(c.path, c.status, c.new_mode) for c in changes] == [
        (path, "M", "100644"),
        ("vendor/lib", "A", "160000"),
    ]


def test_binary_is_decided_from_the_bytes() -> None:
    assert review.is_binary(b"\xff\xfe") and review.is_binary(b"// \x00 hidden\n")
    assert not review.is_binary("ünïcode\n".encode())


def test_path_findings_built_in_and_declared() -> None:
    config = review.parse_config(CARGO)
    changes = [
        _change("vendor/lib", "A", old_mode="000000", new_mode="160000"),
        _change("sub/.gitattributes"),
        _change("deep/.lfsconfig"),
        _change("nested/.gitmodules"),
        _change("native/x.node", "A"),
        _change("old.bin", "D"),
        _change(".claude/skills/a.md"),
        _change("install.sh"),
        _change("tools/helper.sh"),
        _change("crates/x/build.rs"),
        _change(".cargo/config.toml"),
        _change("rust-toolchain.toml"),
        _change("src/main.rs"),
    ]
    blobs = {"n1": b"text\n", "x-node": b"\x7fELF\x00\xff"}
    changes = [*changes[:4], _change("native/x.node", "A", new="x-node"), *changes[5:]]
    found = review.path_findings(config, changes, lambda blob: blobs.get(blob))
    assert {(f.kind, f.path) for f in found} == {
        ("submodule", "vendor/lib"),
        ("git-config-file", "sub/.gitattributes"),
        ("git-config-file", "deep/.lfsconfig"),
        ("git-config-file", "nested/.gitmodules"),
        ("binary", "native/x.node"),
        ("watched-path", ".claude/skills/a.md"),
        ("shell-script", "install.sh"),
        ("build-script", "crates/x/build.rs"),
        ("cargo-config", ".cargo/config.toml"),
        ("toolchain", "rust-toolchain.toml"),
    }
    binary = next(f for f in found if f.kind == "binary")
    assert binary.content == review._digest(blobs["x-node"]) and binary.content in binary.summary


def test_npm_path_rules() -> None:
    config = review.parse_config(NPM)
    found = review.path_findings(
        config, [_change("pkg/.npmrc"), _change("build.rs")], lambda _: None
    )
    assert {(f.kind, f.path) for f in found} == {("npmrc", "pkg/.npmrc")}


def _reader(blobs: dict[str, Any]) -> Any:
    def read(blob: str) -> bytes | None:
        value = blobs.get(blob)
        return None if value is None else json.dumps(value).encode()

    return read


def test_npm_manifest_findings() -> None:
    old = {
        "scripts": {"prepare": "node scripts/prepare.mjs", "test": "vitest"},
        "dependencies": {"left-pad": "^1.0.0"},
    }
    new = {
        "scripts": {
            "prepare": "node scripts/prepare.mjs",
            "test": "vitest run",
            "install": "node install.js",
        },
        "bin": {"rea": "bin/rea.js"},
        "dependencies": {"left-pad": "^1.0.0", "evil": "github:someone/evil"},
    }
    changes = [
        Change("M", "package.json", "100644", "100644", "o", "n"),
        Change("M", "scripts/prepare.mjs", "100644", "100644", "o2", "n2"),
        Change("M", "install.js", "100644", "100644", "o3", "n3"),
        Change("M", "scripts/other.mjs", "100644", "100644", "o4", "n4"),
    ]
    found = review.npm_manifest_findings(changes, _reader({"o": old, "n": new}), lambda _: b"{}")
    assert {(f.kind, f.path) for f in found} == {
        ("npm-lifecycle", "package.json"),
        ("npm-bin", "package.json"),
        ("npm-dependency-source", "package.json"),
        ("npm-lifecycle-file", "scripts/prepare.mjs"),
        ("npm-lifecycle-file", "install.js"),
    }


def test_an_ordinary_script_change_is_left_to_the_model() -> None:
    old = {"scripts": {"test": "vitest"}, "dependencies": {"a": "^1"}}
    new = {"scripts": {"test": "vitest run"}, "dependencies": {"a": "^2"}}
    changes = [Change("M", "package.json", "100644", "100644", "o", "n")]
    assert (
        review.npm_manifest_findings(changes, _reader({"o": old, "n": new}), lambda _: None) == []
    )


def test_cargo_proc_macro_change() -> None:
    blobs = {"o": b"[lib]\n", "n": b"[lib]\nproc-macro = true\n"}
    changes = [Change("M", "macros/Cargo.toml", "100644", "100644", "o", "n")]
    [finding] = review.cargo_manifest_findings(changes, blobs.get)
    assert finding.kind == "proc-macro"
    same = [Change("M", "Cargo.toml", "100644", "100644", "o", "o")]
    assert review.cargo_manifest_findings(same, blobs.get) == []


def _npm_lock(**packages: dict[str, Any]) -> bytes:
    return json.dumps({"lockfileVersion": 3, "packages": {"": {}, **packages}}).encode()


def _registry(name: str, version: str, integrity: str = "sha512-a", **extra: Any) -> dict[str, Any]:
    url = f"https://registry.npmjs.org/{name}/-/{name}-{version}.tgz"
    return {"version": version, "resolved": url, "integrity": integrity, **extra}


def test_npm_lock_review_flags_only_what_is_suspicious() -> None:
    change = _change("package-lock.json")
    old = _npm_lock(
        **{
            "node_modules/bumped": _registry("bumped", "1.0.0"),
            "node_modules/retagged": _registry("retagged", "2.0.0", "sha512-old"),
            "node_modules/gone": _registry("gone", "1.0.0"),
        }
    )
    new = _npm_lock(
        **{
            "node_modules/bumped": _registry("bumped", "1.1.0"),
            "node_modules/retagged": _registry("retagged", "2.0.0", "sha512-new"),
            "node_modules/gitdep": {
                "version": "1.0.0",
                "resolved": "git+ssh://git@github.com/x/y.git#abc",
            },
            "node_modules/local": {"link": True},
            "node_modules/hooky": _registry("hooky", "3.0.0", hasInstallScript=True),
        }
    )
    result = review.npm_lock_review(change, old, new)
    assert {(f.kind, f.summary.split(":")[0]) for f in result.findings} == {
        ("npm-lock-integrity", "node_modules/retagged"),
        ("npm-lock-source", "node_modules/gitdep"),
        ("npm-lock-source", "node_modules/local"),
        ("npm-install-script", "node_modules/hooky"),
    }
    assert any(line.startswith("node_modules/bumped: 1.0.0 -> 1.1.0") for line in result.lines)
    assert "node_modules/gone: 1.0.0 -> removed" in result.lines


def test_an_unchanged_install_script_package_is_not_a_finding() -> None:
    entry = _registry("hooky", "3.0.0", hasInstallScript=True)
    lock = _npm_lock(**{"node_modules/hooky": entry})
    assert review.npm_lock_review(_change("package-lock.json"), lock, lock).findings == []


CRATES = "registry+https://github.com/rust-lang/crates.io-index"


def _cargo_lock(*packages: tuple[str, str, str | None, str | None]) -> bytes:
    lines = ["version = 3", ""]
    for name, version, source, checksum in packages:
        lines += ["[[package]]", f'name = "{name}"', f'version = "{version}"']
        lines += [f'source = "{source}"'] if source else []
        lines += [f'checksum = "{checksum}"'] if checksum else []
        lines.append("")
    return "\n".join(lines).encode()


def test_cargo_lock_review_flags_only_what_is_suspicious() -> None:
    old = _cargo_lock(("serde", "1.0.0", CRATES, "a"), ("libc", "0.2.0", CRATES, "b"))
    new = _cargo_lock(
        ("serde", "1.0.1", CRATES, "c"),
        ("libc", "0.2.0", CRATES, "TAMPERED"),
        ("sneaky", "0.1.0", "git+https://github.com/x/sneaky#abc", None),
        ("rtk", "0.48.0", None, None),
    )
    result = review.cargo_lock_review(_change("Cargo.lock"), old, new)
    assert {(f.kind, f.summary.split(":")[0]) for f in result.findings} == {
        ("cargo-lock-checksum", "libc 0.2.0"),
        ("cargo-lock-source", "sneaky 0.1.0"),
    }
    assert f"serde: 1.0.0 ({CRATES}) -> 1.0.1 ({CRATES})" in result.lines


def test_a_git_build_cannot_hide_behind_a_crates_io_entry() -> None:
    """Same name and version from two sources are two packages: the git one is a finding and the
    model sees its URL."""
    git = "git+https://github.com/x/serde#abc"
    old = _cargo_lock(("serde", "1.0.0", CRATES, "a"))
    new = _cargo_lock(("serde", "1.0.0", CRATES, "a"), ("serde", "1.0.0", git, None))
    result = review.cargo_lock_review(_change("Cargo.lock"), old, new)
    assert [(f.kind, f.summary) for f in result.findings] == [
        ("cargo-lock-source", f"serde 1.0.0: source {git}")
    ]
    assert any(git in line for line in result.lines)


@pytest.mark.parametrize(
    ("path", "content"),
    [
        ("Cargo.lock", _cargo_lock(("a", "1.0.0", CRATES, "x"), ("a", "1.0.0", CRATES, "y"))),
        ("package-lock.json", b'{"packages": {"node_modules/a": {}, "node_modules/a": {}}}'),
    ],
)
def test_a_lockfile_with_duplicate_entries_is_unreadable(path: str, content: bytes) -> None:
    result = review.lock_review(_change(path), lambda blob: content if blob == "n1" else None)
    assert [f.kind for f in result.findings] == ["lockfile-unreadable"]


def test_an_unreadable_lockfile_is_a_finding() -> None:
    result = review.lock_review(_change("package-lock.json"), lambda _: b"not json")
    assert [f.kind for f in result.findings] == ["lockfile-unreadable"]


FIXTURES = Path(__file__).parent / "fixtures"
# Real output, captured 2026-10-19: cargo-audit 0.22.2 over rtk-ai/rtk v1.0.1's Cargo.lock (seven
# advisories, four warnings), and npm 11.17.0 over a lockfile pinning lodash 4.17.15,
# minimist 1.2.5, node-fetch 2.6.0 and express 4.17.1 (whose own entry is `high` only through
# string `via` entries naming its vulnerable dependencies).
CARGO_REPORT = (FIXTURES / "cargo-audit-0.22.2-rtk-v1.0.1.json").read_bytes()
NPM_REPORT = (FIXTURES / "npm-11-audit.json").read_bytes()
NPM_LOCK = (FIXTURES / "npm-11-audit-package-lock.json").read_bytes()


def test_audits_are_fixed_and_run_on_copies_away_from_the_checkout() -> None:
    seen: list[tuple[list[str], list[str], dict[str, str]]] = []

    def run(argv: list[str], cwd: Path, env: dict[str, str]) -> tuple[int, bytes]:
        seen.append((argv, sorted(p.name for p in cwd.iterdir()), env))
        return 0, b""

    head = {
        "package.json": b"{}",
        "package-lock.json": b"{}",
        "Cargo.toml": b"",
        "Cargo.lock": b"x",
    }
    assert review.audit_findings("npm", head.get, run) == []
    assert review.audit_findings("cargo", head.get, run) == []
    (npm_argv, npm_files, _), (cargo_argv, cargo_files, cargo_env) = seen
    assert npm_argv == [
        "npm", "audit", "--json", "--audit-level=high", "--userconfig", "/dev/null",
        "--registry", "https://registry.npmjs.org",
    ]  # fmt: skip
    assert npm_files == ["package-lock.json", "package.json"]
    assert cargo_argv[:4] == ["cargo-audit", "audit", "--json", "--file"]
    assert Path(cargo_argv[4]).name == "Cargo.lock" and cargo_files == ["Cargo.lock", "Cargo.toml"]
    assert Path(cargo_env["CARGO_HOME"]).parent == Path(cargo_argv[4]).parent
    assert review.audit_findings("npm", {}.get, run)[0].summary.startswith("no lockfile")


def test_cargo_audit_yields_one_finding_per_advisory() -> None:
    findings = review.advisory_findings("cargo", 1, CARGO_REPORT, b"", "Cargo.lock")
    assert [f.summary.split(":")[0] for f in findings] == [
        "RUSTSEC-2026-0204 crossbeam-epoch 0.9.18",
        "RUSTSEC-2026-0195 quick-xml 0.37.5",
        "RUSTSEC-2026-0194 quick-xml 0.37.5",
        "RUSTSEC-2026-0346 rustls 0.23.37",
        "RUSTSEC-2026-0285 rustls 0.23.37",
        "RUSTSEC-2026-0348 rustls 0.23.37",
        "RUSTSEC-2026-0349 rustls 0.23.37",
    ]  # the four warnings (unmaintained, unsound) are not findings
    assert findings[0].summary.endswith("(no CVSS; fix: >=0.9.20)")
    assert "(CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H; fix: >=0.41.0)" in findings[1].summary
    assert all((f.kind, f.path) == ("audit", "Cargo.lock") for f in findings)


def test_npm_audit_yields_one_finding_per_high_advisory_at_the_installed_version() -> None:
    findings = review.advisory_findings("npm", 1, NPM_REPORT, NPM_LOCK, "package-lock.json")
    assert sorted(f.summary.split(":")[0] for f in findings) == [
        "GHSA-35jh-r3h4-6jhm lodash 4.17.15",
        "GHSA-37ch-88jc-xwx2 path-to-regexp 0.1.7",
        "GHSA-9wv6-86v2-598j path-to-regexp 0.1.7",
        "GHSA-hrpp-h998-j3pp qs 6.7.0",
        "GHSA-p6mc-m468-83gw lodash 4.17.15",
        "GHSA-qwcr-r2fm-qrc7 body-parser 1.19.0",
        "GHSA-r5fr-rjxr-66jc lodash 4.17.15",
        "GHSA-r683-j2x4-v87g node-fetch 2.6.0",
        "GHSA-rhx6-c78j-4q9w path-to-regexp 0.1.7",
        "GHSA-xvch-5gv4-984h minimist 1.2.5",
    ]  # low and moderate advisories are not findings, and express's string `via` entries are
    # its dependencies' advisories, already listed under their own names
    [minimist] = [f for f in findings if "minimist" in f.summary]
    assert minimist.summary.endswith("(critical; fix: minimist 1.2.8)")


def test_an_advisory_ack_names_what_it_accepts_and_outlives_other_changes() -> None:
    """The fingerprint is the advisory, the package and its installed version: unchanged by any
    other change to the lockfile, changed by a new version or a newly published advisory."""
    report = json.loads(CARGO_REPORT)
    [first, *_] = review.advisory_findings("cargo", 1, CARGO_REPORT, b"lock-1", "Cargo.lock")
    [again, *_] = review.advisory_findings("cargo", 1, CARGO_REPORT, b"lock-2", "Cargo.lock")
    assert first.fingerprint() == again.fingerprint()
    report["vulnerabilities"]["list"][0]["package"]["version"] = "0.9.19"
    [bumped, *_] = review.advisory_findings(
        "cargo", 1, json.dumps(report).encode(), b"", "Cargo.lock"
    )
    assert bumped.fingerprint() != first.fingerprint()
    report["vulnerabilities"]["list"][0]["package"]["version"] = "0.9.18"
    report["vulnerabilities"]["list"][0]["advisory"]["id"] = "RUSTSEC-2026-9999"
    [new, *_] = review.advisory_findings("cargo", 1, json.dumps(report).encode(), b"", "Cargo.lock")
    assert new.fingerprint() != first.fingerprint()


def test_audit_findings_read_the_audits_output_against_the_heads_lockfile() -> None:
    head = {"package.json": b"{}", "package-lock.json": NPM_LOCK}
    findings = review.audit_findings("npm", head.get, lambda argv, cwd, env: (1, NPM_REPORT))
    assert len(findings) == 10


def test_a_clean_audit_passes() -> None:
    assert review.advisory_findings("cargo", 0, CARGO_REPORT, b"", "Cargo.lock") == []


@pytest.mark.parametrize(
    ("ecosystem", "output"),
    [
        ("cargo", b"error: couldn't fetch advisory database"),
        ("npm", b'{"error": {"code": "ENOTFOUND", "summary": "request failed"}}'),
        # A failing exit with nothing listed: never read as a pass.
        (
            "cargo",
            json.dumps({"vulnerabilities": {"found": False, "count": 0, "list": []}}).encode(),
        ),
        ("npm", json.dumps({"auditReportVersion": 2, "vulnerabilities": {}}).encode()),
    ],
)
def test_an_audit_that_failed_without_advisories_could_not_run(
    ecosystem: str, output: bytes
) -> None:
    with pytest.raises(ReviewUnavailable):
        review.advisory_findings(ecosystem, 1, output, NPM_LOCK, "lock")


def test_every_changed_path_must_be_accounted_for() -> None:
    changes = [_change("a.js"), _change("b.bin"), _change("c.js")]
    review.account(changes, {"a.js": "diff", "c.js": "diff"}, [Finding("binary", "b.bin", "", "")])
    with pytest.raises(ReviewUnavailable, match="b.bin"):
        review.account(changes, {"a.js": "diff", "c.js": "diff"}, [])


# ---- the model ------------------------------------------------------------------------------


def test_order_material_puts_trust_critical_first() -> None:
    config = review.parse_config(CARGO)
    sections = {"src/a.rs": "A", "src/core/telemetry.rs": "T"}
    material = review.order_material(config, sections)
    assert material.index("### trust-critical: src/core/telemetry.rs") < material.index("A")


def test_parse_verdict() -> None:
    verdict = '```json\n{"findings": [{"severity": "high", "path": "a", "summary": "s"}]}\n```'
    [finding] = review.parse_verdict(verdict)
    assert (finding.kind, finding.path, finding.severity) == ("ai", "a", "high")
    assert review.parse_verdict('{"findings": []}') == []
    for bad in ("I think it is fine", '{"verdict": "ok"}', '{"findings": [{"path": 1}]}'):
        with pytest.raises(ReviewUnavailable):
            review.parse_verdict(bad)


class _Response(io.BytesIO):
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_: object) -> None:
        return None


def _model(text: str, seen: list[Any]) -> Any:
    def opener(request: Any) -> _Response:
        seen.append(request)
        return _Response(json.dumps({"content": [{"type": "text", "text": text}]}).encode())

    return opener


def test_ai_findings_are_bound_to_content() -> None:
    """A finding on a changed path binds to that change; one naming any other path binds to the
    whole material, so neither acknowledgement survives a different change."""
    seen: list[Any] = []
    answer = [{"severity": "low", "path": p, "summary": "s"} for p in ("a", "elsewhere")]
    opener = _model(json.dumps({"findings": answer}), seen)
    config = review.parse_config(CARGO)
    on_path, off_path = review.ai_findings(config, "diff", {"a": "blob-a"}, "key", opener)
    assert on_path.content.startswith("blob-a\0low\0")
    assert off_path.content.startswith(review._digest(b"diff") + "\0")
    payload = json.loads(seen[0].data)
    assert payload["model"] == "claude-sonnet-5-5"
    assert "untrusted DATA" in payload["messages"][0]["content"]


def test_a_diff_too_large_is_a_finding_and_is_not_sent() -> None:
    config = review.parse_config(CARGO + "max_diff_chars = 10\n")
    seen: list[Any] = []
    [finding] = review.ai_findings(config, "x" * 11, {}, "key", _model("{}", seen))
    assert (finding.kind, finding.content) == ("too-large", review._digest(b"x" * 11))
    assert seen == []


def test_the_review_cannot_run_without_a_key_or_a_working_api() -> None:
    config = review.parse_config(CARGO)
    with pytest.raises(ReviewUnavailable, match="ANTHROPIC_API_KEY"):
        review.ai_findings(config, "diff", {}, "", _model("{}", []))

    def broken(_: Any) -> Any:
        raise OSError("HTTP 401")

    with pytest.raises(ReviewUnavailable, match="OSError"):
        review.ai_findings(config, "diff", {}, "key", broken)


# ---- verdict, acknowledgements, comment -----------------------------------------------------


def test_fingerprints_name_exact_content_and_nothing_head_declared() -> None:
    finding = Finding("binary", "a.node", "s", "c1")
    assert finding.fingerprint() == Finding("binary", "a.node", "other", "c1").fingerprint()
    assert finding.fingerprint() != Finding("binary", "a.node", "s", "c2").fingerprint()
    assert finding.fingerprint() != Finding("binary", "b.node", "s", "c1").fingerprint()


def test_exit_codes_and_acknowledgements() -> None:
    finding = Finding("binary", "a.node", "s", "c1")
    acks = review.parse_acks(
        f'[[ack]]\nfingerprint = "{finding.fingerprint()}"\nby = "devon"\n'
        'date = 2026-10-19\nreason = "known artifact"\n'
    )
    assert acks[finding.fingerprint()] == Ack("devon", "2026-10-19", "known artifact")
    assert Report("h").exit_code({}) == 0
    assert Report("h", [finding]).exit_code({}) == 1
    assert Report("h", [finding]).exit_code(acks) == 0
    later = Finding("binary", "a.node", "s", "c2")  # the same path, different bytes
    assert Report("h", [later]).exit_code(acks) == 1
    assert Report("h", [finding], unavailable="no key").exit_code(acks) == 2
    markdown = Report("h", [finding]).markdown(acks)
    assert "acknowledged by devon" in markdown and review.COMMENT_MARKER in markdown


def test_the_report_cannot_be_broken_by_a_path() -> None:
    finding = Finding("ai", "a|b`c\nd", "x | y", "")
    row = Report("h", [finding]).markdown({}).splitlines()[-3]
    assert row.count(" | ") == 4 and "\n" not in row


def test_an_ack_without_a_reason_is_refused() -> None:
    with pytest.raises(ConfigError, match="reason"):
        review.parse_acks('[[ack]]\nfingerprint = "f"\nby = "d"\ndate = "x"\n')


@pytest.mark.parametrize("existing", [True, False])
def test_the_comment_is_rolling(existing: bool) -> None:
    calls: list[tuple[str, str]] = []
    listing = [{"id": 7, "body": f"{review.COMMENT_MARKER}\nold"}] if existing else []

    def opener(request: Any) -> _Response:
        calls.append((request.get_method(), request.full_url))
        return _Response(json.dumps(listing if request.get_method() == "GET" else {}).encode())

    review.post_comment("o/r", "5", "body", "t", opener)
    expected = (
        ("PATCH", "https://api.github.com/repos/o/r/issues/comments/7")
        if existing
        else ("POST", "https://api.github.com/repos/o/r/issues/5/comments")
    )
    assert calls[-1] == expected


# ---- over real repositories -----------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit(repo: Path, files: Mapping[str, str | bytes], message: str) -> str:
    for path, content in files.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _repo(tmp_path: Path, monkeypatch: Any, files: Mapping[str, str | bytes]) -> str:
    _git(tmp_path, "init", "-q", "-b", "main")
    monkeypatch.chdir(tmp_path)
    return _commit(
        tmp_path,
        {"Cargo.toml": '[package]\nversion = "0.1.0"\n', "Cargo.lock": "version = 3\n", **files},
        "base",
    )


def _prompt(seen: list[Any]) -> str:
    return json.loads(seen[0].data)["messages"][0]["content"]


def test_build_report_over_a_real_repository(tmp_path: Path, monkeypatch: Any) -> None:
    base = _repo(tmp_path, monkeypatch, {"src.rs": "fn main() {}\n"})
    head = _commit(
        tmp_path,
        {"build.rs": "fn main() {}\n", "blob.bin": b"\xff\xfe\x00"},
        "head",
    )
    seen: list[Any] = []
    config = review.parse_config(CARGO)
    report = review.build_report(
        config, base, head, "key", _model('{"findings": []}', seen), _no_audit
    )
    assert report.unavailable is None
    assert {(f.kind, f.path) for f in report.findings} == {
        ("build-script", "build.rs"),
        ("binary", "blob.bin"),
    }
    assert "build.rs" in _prompt(seen) and "blob.bin" not in _prompt(seen)


def test_unusual_paths_reach_the_model(tmp_path: Path, monkeypatch: Any) -> None:
    """Names git would quote, and a name that collides with a diff header's `a/… b/…` shape, are
    each reviewed: their content reaches the model."""
    base = _repo(tmp_path, monkeypatch, {})
    names = {"ünïcode.js": "UNICODE_PAYLOAD", "x b/y.js": "HEADER_PAYLOAD", 'q"uote.js': "QUOTE"}
    head = _commit(tmp_path, names, "head")
    seen: list[Any] = []
    report = review.build_report(
        review.parse_config(CARGO), base, head, "key", _model('{"findings": []}', seen), _no_audit
    )
    assert report.unavailable is None
    for payload in names.values():
        assert payload in _prompt(seen)


def test_a_lockfile_only_change_is_reviewed_not_passed(tmp_path: Path, monkeypatch: Any) -> None:
    lock = {"lockfileVersion": 3, "packages": {"": {}}}
    config = NPM
    base = _repo(
        tmp_path,
        monkeypatch,
        {
            "package.json": "{}",
            "package-lock.json": json.dumps(lock),
            ".github/fork-sync.toml": config,
        },
    )
    lock["packages"]["node_modules/evil"] = {
        "version": "1.0.0",
        "resolved": "https://evil.example/e.tgz",
    }
    head = _commit(tmp_path, {"package-lock.json": json.dumps(lock)}, "head")
    seen: list[Any] = []
    report = review.build_report(
        review.parse_config(NPM), base, head, "key", _model('{"findings": []}', seen), _no_audit
    )
    assert [(f.kind, f.path) for f in report.findings] == [("npm-lock-source", "package-lock.json")]
    assert "node_modules/evil: new -> 1.0.0" in _prompt(seen)
    assert report.exit_code({}) == 1


def test_replace_objects_and_attributes_cannot_hide_content(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Git reads the real objects: a `git replace` swap and an attributes file declaring the path
    binary (both of which the head's code could arrange on the runner) change nothing."""
    base = _repo(tmp_path, monkeypatch, {})
    head = _commit(tmp_path, {"evil.js": "EVIL_PAYLOAD\n"}, "head")
    benign = _git(tmp_path, "hash-object", "-w", "--stdin")  # empty blob
    _git(tmp_path, "replace", _git(tmp_path, "rev-parse", f"{head}:evil.js"), benign)
    (tmp_path / "attrs").write_text("*.js -diff\n")
    _git(tmp_path, "config", "core.attributesFile", str(tmp_path / "attrs"))
    seen: list[Any] = []
    report = review.build_report(
        review.parse_config(CARGO), base, head, "key", _model('{"findings": []}', seen), _no_audit
    )
    assert report.findings == [] and "EVIL_PAYLOAD" in _prompt(seen)


@pytest.mark.parametrize("loosen", ["declaration", "acknowledgement"])
def test_the_gate_is_read_from_the_base_not_the_head(
    tmp_path: Path, monkeypatch: Any, loosen: str
) -> None:
    """A pull request that loosens the declaration or acknowledges its own finding changes nothing.

    History: `strict` (the gate), `loosened` (the gate loosened), `change` (adds a watched file).
    Judged from `strict` the change fails; the control judges the same change from `loosened`,
    where the loosened gate is legitimately on the base, and it passes.
    """
    source = "secret stuff\n"
    strict = _repo(tmp_path, monkeypatch, {".github/fork-sync.toml": CARGO})
    if loosen == "declaration":
        loosened_files = {".github/fork-sync.toml": CARGO.replace('".claude/*"', '"none"')}
    else:
        blob = subprocess.run(
            ["git", "hash-object", "--stdin"], input=source, capture_output=True, text=True
        ).stdout.strip()
        accepted = Finding("watched-path", ".claude/x.md", "", blob).fingerprint()
        loosened_files = {
            ".github/fork-sync-acks.toml": f'[[ack]]\nfingerprint = "{accepted}"\nby = "head"\n'
            'date = "2026-10-19"\nreason = "self-approved"\n'
        }
    loosened = _commit(tmp_path, loosened_files, "loosened")
    change = _commit(tmp_path, {".claude/x.md": source}, "change")

    def judged_from(base: str) -> int:
        env = {"BASE_SHA": base, "HEAD_SHA": change, "ANTHROPIC_API_KEY": "key"}
        return review.review(env, _model('{"findings": []}', []), _no_audit)

    assert judged_from(strict) == 1
    assert judged_from(loosened) == 0


def test_a_base_without_a_declaration_fails_closed(tmp_path: Path, monkeypatch: Any) -> None:
    base = _repo(tmp_path, monkeypatch, {})
    assert review.review({"BASE_SHA": base, "HEAD_SHA": base}) == 2


def test_an_acknowledged_remark_does_not_cover_a_graver_one() -> None:
    config = review.parse_config(CARGO)

    def answer(severity: str, summary: str) -> Finding:
        verdict = {"findings": [{"severity": severity, "path": "a", "summary": summary}]}
        [finding] = review.ai_findings(
            config, "diff", {"a": "blob"}, "key", _model(json.dumps(verdict), [])
        )
        return finding

    low = answer("low", "uses a new helper")
    assert low.fingerprint() == answer("low", "uses a new helper").fingerprint()
    assert low.fingerprint() != answer("high", "uses a new helper").fingerprint()
    assert low.fingerprint() != answer("low", "sends tokens to evil.example").fingerprint()


def test_npm_aliases_are_findings() -> None:
    old = {"dependencies": {"lodash": "^4.17.0"}}
    new = {"dependencies": {"lodash": "npm:lodash-evil@4.17.0"}}
    changes = [Change("M", "package.json", "100644", "100644", "o", "n")]
    found = review.npm_manifest_findings(changes, _reader({"o": old, "n": new}), lambda _: None)
    assert [(f.kind, f.summary) for f in found] == [
        ("npm-dependency-source", "dependencies.lodash: npm:lodash-evil@4.17.0")
    ]
    lock = _npm_lock(
        **{"node_modules/lodash": {**_registry("lodash-evil", "4.17.0"), "name": "lodash-evil"}}
    )
    result = review.npm_lock_review(_change("package-lock.json"), None, lock)
    assert [f.kind for f in result.findings] == ["npm-lock-alias"]
    scoped = _npm_lock(**{"node_modules/@s/x": {**_registry("x", "1.0.0"), "name": "@s/x"}})
    assert review.npm_lock_review(_change("package-lock.json"), None, scoped).findings == []


def test_an_oddly_named_upstream_tag_is_skipped_not_fatal() -> None:
    config = review.parse_config(CARGO.replace("'^v(\\d+\\.\\d+\\.\\d+)$'", "'^v(.+)$'"))
    releases = [_release("vnightly"), _release("v0.49.0")]
    assert review.next_release(config, releases, "0.48.0") == "v0.49.0"


def test_attributes_on_the_base_cannot_hide_code(tmp_path: Path, monkeypatch: Any) -> None:
    """The base's own `.gitattributes` (perhaps from an upstream release acknowledged earlier)
    marks scripts as binary; a NUL in a comment would too. The model still reads both."""
    base = _repo(tmp_path, monkeypatch, {".gitattributes": "*.js -diff binary\n"})
    head = _commit(
        tmp_path,
        {"plain.js": "PLAIN_PAYLOAD\n", "nul.js": "// \x00\nNUL_PAYLOAD\n"},
        "head",
    )
    seen: list[Any] = []
    report = review.build_report(
        review.parse_config(CARGO), base, head, "key", _model('{"findings": []}', seen), _no_audit
    )
    assert {(f.kind, f.path) for f in report.findings} == {("binary", "nul.js")}
    assert "PLAIN_PAYLOAD" in _prompt(seen) and "NUL_PAYLOAD" in _prompt(seen)


def test_the_fork_s_own_github_is_not_part_of_the_reviewed_change(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The sync restores main's `.github/`, so it introduces nothing there: it is not reviewed, and
    adding an acknowledgement to main leaves the reviewed diff -- and every digest bound to it --
    unchanged. Before this, an acknowledgement of a whole-diff finding could never match."""
    upstream = _repo(tmp_path, monkeypatch, {"src.rs": "1\n", ".github/up.yml": "upstream\n"})
    fork: dict[str, str | None] = {".github/up.yml": None, ".github/fork-sync.toml": CARGO}

    def tree(github: Mapping[str, str | None]) -> None:
        for path, content in github.items():
            target = tmp_path / path
            if content is None:
                target.unlink(missing_ok=True)
            else:
                target.write_text(content)

    tree(fork)
    main = _commit(tmp_path, {}, "fork .github")
    _git(tmp_path, "checkout", "-q", "-b", "sync", upstream)
    tree(fork)
    sync = _commit(tmp_path, {"src.rs": "2\n"}, "upstream release, fork .github")
    _git(tmp_path, "checkout", "-q", "main")
    acked = _commit(tmp_path, {".github/fork-sync-acks.toml": "# none\n"}, "ack")
    _git(tmp_path, "checkout", "-q", "sync")
    resynced = _commit(tmp_path, {".github/fork-sync-acks.toml": "# none\n"}, "resync .github")

    prompts = []
    for base, head in ((main, sync), (acked, resynced)):
        seen: list[Any] = []
        report = review.build_report(
            review.parse_config(CARGO),
            base,
            head,
            "key",
            _model('{"findings": []}', seen),
            _no_audit,
        )
        assert report.unavailable is None and report.findings == []
        prompts.append(_prompt(seen))
    assert prompts[0] == prompts[1]
    assert "src.rs" in prompts[0] and ".github" not in prompts[0]


def test_a_path_the_head_changed_away_from_the_base_tip_is_still_reviewed() -> None:
    a = review.Change("M", "a", "100644", "100644", "1" * 40, "2" * 40)
    b = review.Change("M", "b", "100644", "100644", "1" * 40, "3" * 40)
    assert review.merged_changes([a, b], [b]) == [b]
