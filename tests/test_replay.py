"""Replay supplied evidence and detect changed bytes without executing an action."""

from __future__ import annotations

import copy
import json
import shutil
import sys
from dataclasses import asdict
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "wrapper"))
import replay

import wrapper as support


def _report() -> dict:
    return {
        "verdict": {"tier": "GREEN", "score": 95, "summary": "Producer assertion."},
        "dimensions": {
            "accuracy": {
                "status": "not_scored",
                "score": None,
                "claims_total": 0,
                "claims_verified": 0,
                "claims_contradicted": 0,
            },
            "tone": {"score": 95},
            "bias": {"score": 95},
            "agreement": {"score": 95},
        },
        "claims": [],
        "findings": [],
    }


@pytest.fixture
def audit_record(monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setattr(support, "analyze", lambda *args, **kwargs: _report())
    text, context = "The Pro plan costs $49 per month.", {"plan": "Pro"}
    decision = support.decide(text, context, audit_log=False)
    return {
        "timestamp": "2026-10-10T00:00:00+00:00",
        "persona": "customer_support",
        "input": text,
        "context": context,
        **asdict(decision),
    }


def _check(result: dict, name: str) -> str:
    return next(check["status"] for check in result["checks"] if check["check"] == name)


def test_untouched_audit_replays_without_network_or_actions(
    audit_record: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    network, action = (
        Mock(side_effect=AssertionError("network forbidden")),
        Mock(side_effect=AssertionError("action forbidden")),
    )
    monkeypatch.setattr(support, "analyze", network)
    monkeypatch.setattr(support.mock_actions, "run_for_persona", action)
    original = copy.deepcopy(audit_record)
    result = replay.replay_record(audit_record, support.POLICY)
    assert result["status"] == "matched"
    assert result["evidence_decision"] == "supported"
    assert result["retained_action_decision"] == "APPROVE"
    assert result == replay.replay_record(audit_record, support.POLICY)
    assert audit_record == original
    network.assert_not_called()
    action.assert_not_called()


def _ui_export(record: dict) -> dict:
    return {
        "created_at": "2026-10-10T00:00:00.000Z",
        "workflow": "customer_support",
        "input": {
            "persona": record["persona"],
            "text": record["input"],
            "context": copy.deepcopy(record["context"]),
        },
        "result": {
            **{
                key: copy.deepcopy(record[key])
                for key in ("decision", "reason", "evidence", "ezio", "decision_packet")
            },
            "action_executed": True,
        },
    }


def test_ui_export_replays_without_treating_action_assertion_as_proof(
    audit_record: dict,
) -> None:
    export = _ui_export(audit_record)
    export["review_handoff"] = {
        "note": "Needs human review",
        "recorded_at": "2026-10-10T00:00:00.000Z",
        "action": "note_only",
    }
    original = copy.deepcopy(export)
    matched = replay.replay_record(export, support.POLICY)
    assert matched["status"] == "matched"
    assert export == original
    export["result"]["action_executed"] = False
    assert replay.replay_record(export, support.POLICY) == matched


def test_ui_export_tampered_input_is_detected(audit_record: dict) -> None:
    export = _ui_export(audit_record)
    export["input"]["text"] = "The Pro plan costs $99 per month."
    result = replay.replay_record(export, support.POLICY)
    assert result["status"] == "mismatch"
    assert _check(result, "input_sha256") == "mismatch"


def test_ui_export_same_verdict_changed_claim_is_detected(audit_record: dict) -> None:
    export = _ui_export(audit_record)
    export["result"]["decision_packet"]["checks"][0]["claim"] = "Changed claim"
    result = replay.replay_record(export, support.POLICY)
    assert result["evidence_decision"] == "supported"
    assert result["status"] == "mismatch"
    assert _check(result, "regenerated_checks") == "mismatch"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("input", "draft"),
        ("result", []),
        ("workflow", "financial_analyst"),
        ("review_handoff", "approved"),
    ],
)
def test_ui_export_malformed_structure_is_refused(
    audit_record: dict, key: str, value: object
) -> None:
    export = _ui_export(audit_record)
    export[key] = value
    assert replay.replay_record(export, support.POLICY)["status"] == "refused"


def test_ui_export_missing_required_field_is_refused(audit_record: dict) -> None:
    export = _ui_export(audit_record)
    del export["result"]["ezio"]
    assert replay.replay_record(export, support.POLICY)["status"] == "refused"


def test_cli_accepts_exact_page_export_json(
    audit_record: dict, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "probity-decision.json"
    path.write_text(json.dumps(_ui_export(audit_record)))
    assert replay.main([str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "matched"


@pytest.mark.parametrize(
    ("field", "replacement", "check"),
    [
        ("input", "The Pro plan costs $99 per month.", "input_sha256"),
        ("context", {"plan": "Business"}, "input_sha256"),
        ("decision", "BLOCK", "audit_decision"),
    ],
)
def test_changed_audit_fields_are_detected(
    audit_record: dict, field: str, replacement: object, check: str
) -> None:
    audit_record[field] = replacement
    result = replay.replay_record(audit_record, support.POLICY)
    assert result["status"] == "mismatch"
    assert _check(result, check) == "mismatch"


@pytest.mark.parametrize(
    ("field", "replacement", "check"),
    [
        ("input_sha256", "0" * 64, "input_sha256"),
        ("decision", "contradicted", "kernel_packet"),
        ("policy_sha256", "0" * 64, "policy_sha256"),
        ("action_policy_sha256", "0" * 64, "action_policy_sha256"),
        ("action_decision", "BLOCK", "action_decision"),
    ],
)
def test_changed_packet_fields_are_detected(
    audit_record: dict, field: str, replacement: object, check: str
) -> None:
    audit_record["decision_packet"][field] = replacement
    result = replay.replay_record(audit_record, support.POLICY)
    assert result["status"] == "mismatch"
    assert _check(result, check) == "mismatch"


def test_same_verdict_check_change_is_detected(audit_record: dict) -> None:
    audit_record["decision_packet"]["checks"][0]["observed"] = "tampered"
    result = replay.replay_record(audit_record, support.POLICY)
    assert result["evidence_decision"] == "supported"
    assert result["status"] == "mismatch"
    assert _check(result, "regenerated_checks") == "mismatch"


def test_changed_source_binding_is_detected(audit_record: dict) -> None:
    audit_record["decision_packet"]["source_bindings"][0]["sha256"] = "0" * 64
    result = replay.replay_record(audit_record, support.POLICY)
    assert result["status"] == "mismatch"
    assert _check(result, "source_bindings") == "mismatch"


def test_changed_company_source_bytes_are_detected(
    audit_record: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = Path(__file__).resolve().parents[1]
    shutil.copytree(project / "personas", tmp_path / "personas")
    (tmp_path / "wrapper").mkdir()
    monkeypatch.setattr(support, "HERE", tmp_path / "wrapper")
    monkeypatch.setattr(support, "PERSONAS", tmp_path / "personas")
    source = tmp_path / "personas" / "customer_support" / "price_sheet.csv"
    source.write_text(source.read_text().replace("Pro,49,", "Pro,50,"))
    result = replay.replay_record(audit_record, support.POLICY)
    assert result["status"] == "mismatch"
    assert _check(result, "source_bindings") == "mismatch"
    assert _check(result, "regenerated_checks") == "mismatch"


def test_changed_selected_policy_is_detected(audit_record: dict) -> None:
    policy = copy.deepcopy(support.POLICY)
    policy["dimension_minimums"]["tone"]["hold_below"] = 99
    result = replay.replay_record(audit_record, policy)
    assert result["status"] == "mismatch"
    assert _check(result, "action_policy") == "mismatch"
    assert _check(result, "policy_sha256") == "mismatch"
    assert _check(result, "action_decision") == "mismatch"


def test_changed_effective_policy_is_detected(audit_record: dict) -> None:
    audit_record["decision_packet"]["consumer_policy"]["on_ezio_failure"] = "APPROVE"
    result = replay.replay_record(audit_record, support.POLICY)
    assert result["status"] == "mismatch"
    assert _check(result, "consumer_policy") == "mismatch"


@pytest.mark.parametrize(
    "text", ["The Pro plan costs $99 per month.", "Thank you for your message."]
)
def test_replay_match_does_not_change_the_evidence_verdict(
    text: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(support, "analyze", lambda *args, **kwargs: _report())
    decision = support.decide(text, {}, audit_log=False)
    record = {
        "input": text,
        "context": {},
        "persona": "customer_support",
        **asdict(decision),
    }
    result = replay.replay_record(record, support.POLICY)
    assert result["status"] == "matched"
    assert result["evidence_decision"] == decision.decision_packet["decision"]
    assert result["evidence_decision"] in {"contradicted", "not_established"}


def test_service_failure_replays_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(*args: object, **kwargs: object) -> None:
        raise support.EzioError("HTTP 503", {"error": "temporarily unavailable"})

    monkeypatch.setattr(support, "analyze", unavailable)
    text = "The Pro plan costs $49 per month."
    decision = support.decide(text, {}, audit_log=False)
    record = {
        "input": text,
        "context": {},
        "persona": "customer_support",
        **asdict(decision),
    }
    result = replay.replay_record(record, support.POLICY)
    assert result["status"] == "matched"
    assert result["retained_action_decision"] == "HOLD_FOR_REVIEW"
    assert result["evidence_decision"] == "not_established"


def test_bare_packet_cannot_claim_complete_input_replay(audit_record: dict) -> None:
    result = replay.replay_record(audit_record["decision_packet"], support.POLICY)
    assert result["status"] == "incomplete"
    assert _check(result, "input_sha256") == "not_established"
    assert _check(result, "regenerated_checks") == "not_established"


def test_cli_bare_packet_retains_incomplete_status(
    audit_record: dict, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "packet.json"
    path.write_text(json.dumps(audit_record["decision_packet"]))
    assert replay.main([str(path)]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "incomplete"


def test_invalid_retained_verdict_cannot_echo_arbitrary_input(
    audit_record: dict,
) -> None:
    audit_record["decision_packet"]["action_decision"] = {"private": "secret draft"}
    result = replay.replay_record(audit_record, support.POLICY)
    assert result["status"] == "refused"
    assert "secret draft" not in json.dumps(result)


def test_cli_checks_every_record_and_reports_safe_json(
    audit_record: dict, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "audit.jsonl"
    log.write_text(json.dumps(audit_record) + "\n" + json.dumps(audit_record) + "\n")
    assert replay.main([str(log)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "matched"
    assert len(output["records"]) == 2
    assert "The Pro plan costs" not in json.dumps(output)
    audit_record["input"] = "changed input"
    log.write_text(json.dumps(audit_record) + "\n")
    assert replay.main([str(log)]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "mismatch"


@pytest.mark.parametrize(
    "raw", [b"", b"{", b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}']
)
def test_cli_refuses_malformed_records(
    raw: bytes, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "record.json"
    path.write_bytes(raw)
    assert replay.main([str(path)]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "refused"
