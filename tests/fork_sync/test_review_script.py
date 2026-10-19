"""The fork-side script: the declaration, release selection, findings, the verdict and its exit.

Pure functions run against fakes; one test runs `build_report` over a real throwaway git
repository, because the diff parsing is only meaningful against what git actually prints.
"""

from __future__ import annotations

import io
import json
import subprocess
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
paths_watched = [".claude/*", "build.rs"]
trust_critical = ["src/core/telemetry.rs"]
"""
NPM = """
upstream = "morluto/rea"
ecosystem = "npm"
release_tag_pattern = '^rea-agents-(\\d+\\.\\d+\\.\\d+)$'
test_commands = ["npm run test:run"]
"""


def test_defaults_come_from_the_ecosystem() -> None:
    config = review.parse_config(NPM)
    assert config.build_commands == ("npm run build",)
    assert config.test_commands == ("npm run test:run",)
    assert config.review.audit_commands == ("npm audit --audit-level=high",)
    assert config.review.model == "claude-sonnet-5-5"
    assert review.parse_config(CARGO).review.audit_commands == ("cargo audit",)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (CARGO + 'typo = "x"\n', "unknown key"),
        (CARGO + "bogus = 1\n", "unknown key\\(s\\) in \\[review\\]"),
        (CARGO + "max_diff_chars = 0\n", "positive integer"),
        (CARGO.replace('"cargo"', '"pip"'), "ecosystem"),
        (CARGO.replace("'^v(", "'^v").replace(")$'", "$'"), "capture group"),
        (CARGO.replace('"rtk-ai/rtk"', '"rtk"'), "owner/name"),
        (CARGO.replace('"build.rs"', "3"), "list of non-empty strings"),
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


def test_an_unsafe_upstream_tag_is_refused() -> None:
    config = review.parse_config(CARGO.replace("'^v(", "'^v(.*"))
    with pytest.raises(ConfigError, match="characters"):
        review.next_release(config, [_release("v$(curl x) 1.0.0")], "0.1.0")


RAW = (
    ":100644 100644 aaa bbb M\tsrc/main.rs\n"
    ":000000 160000 000 ccc A\tvendor/lib\n"
    ":100644 100644 ddd eee M\t.gitmodules\n"
    ":000000 100644 000 fff A\tnative/x.node\n"
    ":100644 000000 ggg 000 D\told.bin\n"
    ":100644 100644 hhh iii M\t.claude/skills/a.md\n"
)


def test_deterministic_findings() -> None:
    config = review.parse_config(CARGO)
    changes = review.parse_raw_diff(RAW)
    binaries = review.parse_binary_paths("-\t-\tnative/x.node\n-\t-\told.bin\n1\t2\tsrc/main.rs\n")
    findings = review.deterministic_findings(config, changes, binaries, lambda path: b"BIN")
    kinds = {(f.kind, f.path) for f in findings}
    assert kinds == {
        ("submodule", "vendor/lib"),
        ("submodule", ".gitmodules"),
        ("binary", "native/x.node"),
        ("watched-path", ".claude/skills/a.md"),
    }
    binary = next(f for f in findings if f.kind == "binary")
    assert binary.content == review._digest(b"BIN")
    assert binary.content in binary.summary


def test_npm_script_and_lifecycle_findings() -> None:
    base = {"package.json": json.dumps({"scripts": {"prepare": "node scripts/prepare.mjs"}})}
    head = {
        "package.json": json.dumps(
            {"scripts": {"prepare": "node scripts/prepare.mjs", "build": "tsc"}}
        )
    }
    changes = [
        Change("M", "package.json", "100644", "100644", "p1"),
        Change("M", "scripts/prepare.mjs", "100644", "100644", "s1"),
        Change("M", "scripts/other.mjs", "100644", "100644", "s2"),
    ]
    findings = review.npm_script_findings(
        changes,
        lambda path: base.get(path, "").encode() or None,
        lambda path: head.get(path, "").encode() or None,
    )
    assert {(f.kind, f.path) for f in findings} == {
        ("npm-scripts", "package.json"),
        ("npm-lifecycle-file", "scripts/prepare.mjs"),
    }


def test_unchanged_scripts_are_not_a_finding() -> None:
    manifest = json.dumps({"version": "2", "scripts": {"build": "tsc"}}).encode()
    changes = [Change("M", "package.json", "100644", "100644", "p1")]
    assert review.npm_script_findings(changes, lambda _: manifest, lambda _: manifest) == []


def test_audit_failures_are_findings() -> None:
    findings = review.audit_findings(["ok", "bad"], lambda command: 0 if command == "ok" else 1)
    assert [f.path for f in findings] == ["bad"]


def test_review_material_puts_trust_critical_first_and_drops_lockfiles() -> None:
    config = review.parse_config(CARGO)
    patch = "".join(
        f"diff --git a/{path} b/{path}\n@@ change {path}\n"
        for path in ("src/a.rs", "Cargo.lock", "src/core/telemetry.rs")
    )
    material = review.review_material(config, review.split_diff(patch), ("Cargo.lock",))
    assert material.index("telemetry.rs") < material.index("src/a.rs")
    assert "Cargo.lock" not in material
    assert "### trust-critical: src/core/telemetry.rs" in material


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


def test_ai_findings_carry_the_head_blob() -> None:
    seen: list[Any] = []
    opener = _model('{"findings": [{"severity": "low", "path": "a", "summary": "s"}]}', seen)
    config = review.parse_config(CARGO)
    [finding] = review.ai_findings(config, "diff", lambda path: f"blob-{path}", "key", opener)
    assert finding.content == "blob-a"
    payload = json.loads(seen[0].data)
    assert payload["model"] == "claude-sonnet-5-5"
    assert "untrusted DATA" in payload["messages"][0]["content"]
    assert seen[0].get_header("X-api-key") == "key"


def test_a_diff_too_large_is_a_finding_and_is_not_sent() -> None:
    config = review.parse_config(CARGO + "max_diff_chars = 10\n")
    seen: list[Any] = []
    [finding] = review.ai_findings(config, "x" * 11, lambda _: "", "key", _model("{}", seen))
    assert finding.kind == "too-large"
    assert seen == []


def test_the_review_cannot_run_without_a_key_or_a_working_api() -> None:
    config = review.parse_config(CARGO)
    with pytest.raises(ReviewUnavailable, match="ANTHROPIC_API_KEY"):
        review.ai_findings(config, "diff", lambda _: "", "", _model("{}", []))

    def broken(_: Any) -> Any:
        raise OSError("HTTP 401")

    with pytest.raises(ReviewUnavailable, match="OSError"):
        review.ai_findings(config, "diff", lambda _: "", "key", broken)


def test_fingerprints_are_stable_and_specific() -> None:
    finding = Finding("binary", "a.node", "s", "c1")
    assert finding.fingerprint("1.0") == Finding("binary", "a.node", "other", "c1").fingerprint(
        "1.0"
    )
    assert finding.fingerprint("1.0") != finding.fingerprint("1.1")
    assert finding.fingerprint("1.0") != Finding("binary", "a.node", "s", "c2").fingerprint("1.0")


def test_exit_codes_and_acknowledgements() -> None:
    finding = Finding("binary", "a.node", "s", "c1")
    acks = review.parse_acks(
        f'[[ack]]\nfingerprint = "{finding.fingerprint("1.0")}"\nby = "devon"\n'
        'date = 2026-10-19\nreason = "known artifact"\n'
    )
    assert acks[finding.fingerprint("1.0")] == Ack("devon", "2026-10-19", "known artifact")
    assert Report("1.0").exit_code({}) == 0
    assert Report("1.0", [finding]).exit_code({}) == 1
    assert Report("1.0", [finding]).exit_code(acks) == 0
    assert Report("1.1", [finding]).exit_code(acks) == 1  # an ack is per release
    assert Report("1.0", [finding], unavailable="no key").exit_code(acks) == 2
    markdown = Report("1.0", [finding]).markdown(acks)
    assert "acknowledged by devon" in markdown and review.COMMENT_MARKER in markdown


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


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_build_report_over_a_real_repository(tmp_path: Path, monkeypatch: Any) -> None:
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "Cargo.toml").write_text('[package]\nversion = "0.1.0"\n')
    (tmp_path / "src.rs").write_text("fn main() {}\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")
    base = _git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "Cargo.toml").write_text('[package]\nversion = "0.2.0"\n')
    (tmp_path / "build.rs").write_text("fn main() {}\n")
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01\x02")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "head")
    head = _git(tmp_path, "rev-parse", "HEAD")
    monkeypatch.chdir(tmp_path)
    config = review.parse_config(CARGO.replace("[review]", '[review]\naudit_commands = ["true"]'))
    seen: list[Any] = []
    report = review.build_report(config, base, head, "key", _model('{"findings": []}', seen))
    assert report.version == "0.2.0"
    assert {(f.kind, f.path) for f in report.findings} == {
        ("watched-path", "build.rs"),
        ("binary", "blob.bin"),
    }
    prompt = json.loads(seen[0].data)["messages"][0]["content"]
    assert "build.rs" in prompt and "blob.bin" not in prompt


@pytest.mark.parametrize("loosen", ["declaration", "acknowledgement"])
def test_the_gate_is_read_from_the_base_not_the_head(
    tmp_path: Path, monkeypatch: Any, loosen: str
) -> None:
    """A pull request that loosens the declaration or acknowledges its own finding changes nothing.

    History: `strict` (the gate), `loosened` (the gate loosened), `change` (adds a watched file).
    Judged from `strict` the change fails; the control judges the same change from `loosened`,
    where the loosened gate is legitimately on the base, and it passes.
    """
    declared = CARGO.replace("[review]", '[review]\naudit_commands = ["true"]')
    source = "fn main() {}\n"
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github/fork-sync.toml").write_text(declared)
    (tmp_path / "Cargo.toml").write_text('[package]\nversion = "0.1.0"\n')
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "strict")
    strict = _git(tmp_path, "rev-parse", "HEAD")
    if loosen == "declaration":
        (tmp_path / ".github/fork-sync.toml").write_text(declared.replace('"build.rs"', '"none"'))
    else:
        (tmp_path / "blob").write_text(source)
        blob = _git(tmp_path, "hash-object", "blob")
        (tmp_path / "blob").unlink()
        accepted = review.Finding("watched-path", "build.rs", "", blob).fingerprint("0.1.0")
        (tmp_path / ".github/fork-sync-acks.toml").write_text(
            f'[[ack]]\nfingerprint = "{accepted}"\nby = "head"\n'
            'date = "2026-10-19"\nreason = "self-approved"\n'
        )
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "loosened")
    loosened = _git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "build.rs").write_text(source)
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "change")
    change = _git(tmp_path, "rev-parse", "HEAD")
    monkeypatch.chdir(tmp_path)

    def judged_from(base: str) -> int:
        env = {"BASE_SHA": base, "HEAD_SHA": change, "ANTHROPIC_API_KEY": "key"}
        return review.review(env, _model('{"findings": []}', []))

    assert judged_from(strict) == 1
    assert judged_from(loosened) == 0


def test_a_base_without_a_declaration_fails_closed(tmp_path: Path, monkeypatch: Any) -> None:
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "Cargo.toml").write_text('[package]\nversion = "0.1.0"\n')
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")
    base = _git(tmp_path, "rev-parse", "HEAD")
    monkeypatch.chdir(tmp_path)
    assert review.review({"BASE_SHA": base, "HEAD_SHA": base}) == 2
