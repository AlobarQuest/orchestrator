"""`sds-members stage` derives a stable idempotency key and prints the review page."""

import json

from typer.testing import CliRunner

from sds_members import cli

PROPOSAL = """
summary = "add one"

[[changes]]
repository = "A/one"
reason = "new"
state = "onboarding"
"""


def _run(monkeypatch, tmp_path, token: str | None = "tok", proposal=PROPOSAL):
    file = tmp_path / "p.toml"
    file.write_text(proposal)
    calls = []

    def fake_stage(**kwargs):
        calls.append(kwargs)
        return {"id": "abc-123", "state": "staged"}

    monkeypatch.setattr(cli, "stage", fake_stage)
    if token:
        monkeypatch.setenv("SDS_MEMBERS_SYSTEM_TOKEN", token)
    else:
        monkeypatch.delenv("SDS_MEMBERS_SYSTEM_TOKEN", raising=False)
    result = CliRunner().invoke(cli.app, ["stage", str(file)])
    return result, calls


def test_stage_prints_the_id_and_review_page(monkeypatch, tmp_path):
    result, calls = _run(monkeypatch, tmp_path)
    assert result.exit_code == 0, result.output
    assert "abc-123" in result.output
    assert "https://sds.alobar.net/review/member-proposals/abc-123" in result.output
    [call] = calls
    assert call["credential_key_id"] == "orchestrator-system" and call["token"] == "tok"
    assert call["body"]["summary"] == "add one"


def test_the_key_is_the_digest_of_the_canonical_body(monkeypatch, tmp_path):
    import hashlib

    _, first = _run(monkeypatch, tmp_path)
    _, again = _run(monkeypatch, tmp_path, proposal=PROPOSAL + "\n\n")
    canonical = json.dumps(first[0]["body"], sort_keys=True, separators=(",", ":"))
    assert first[0]["idempotency_key"] == hashlib.sha256(canonical.encode()).hexdigest()
    assert again[0]["idempotency_key"] == first[0]["idempotency_key"]


def test_a_changed_proposal_changes_the_key(monkeypatch, tmp_path):
    _, first = _run(monkeypatch, tmp_path)
    _, second = _run(monkeypatch, tmp_path, proposal=PROPOSAL.replace("new", "newer"))
    assert first[0]["idempotency_key"] != second[0]["idempotency_key"]


def test_an_unset_token_stages_nothing(monkeypatch, tmp_path):
    result, calls = _run(monkeypatch, tmp_path, token=None)
    assert result.exit_code == 1 and calls == []
    assert "SDS_MEMBERS_SYSTEM_TOKEN" in result.output


def test_a_refusal_exits_1_with_its_message(monkeypatch, tmp_path):
    from sds_members.client import ProposalRefused

    def refuse(**_):
        raise ProposalRefused("answered 409: moved")

    file = tmp_path / "p.toml"
    file.write_text(PROPOSAL)
    monkeypatch.setattr(cli, "stage", refuse)
    monkeypatch.setenv("SDS_MEMBERS_SYSTEM_TOKEN", "tok")
    result = CliRunner().invoke(cli.app, ["stage", str(file)])
    assert result.exit_code == 1 and "moved" in result.output
