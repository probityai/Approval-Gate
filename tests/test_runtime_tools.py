"""Check practice score arithmetic and the retained mock action boundary."""

from __future__ import annotations

import importlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "wrapper"))
evaluate = importlib.import_module("evaluate")
mock_actions = importlib.import_module("mock_actions")


def test_practice_metrics_count_errors_and_review_false_blocks(monkeypatch, tmp_path, capsys):
    cases = [
        ("bad-a", "bad", "BLOCK", "BLOCK"),
        ("bad-b", "bad", "HOLD_FOR_REVIEW", "APPROVE"),
        ("good-a", "good", "APPROVE", "APPROVE"),
        ("good-b", "good", "APPROVE", "HOLD_FOR_REVIEW"),
    ]
    folder = tmp_path / "customer_support"
    folder.mkdir()
    items = [
        {"id": name, "label": label, "expected_decision": expected, "text": name, "context": {}}
        for name, label, expected, _ in cases
    ]
    (folder / "practice_set.json").write_text(json.dumps({"items": items}))
    responses = {name: observed for name, _, _, observed in cases}
    monkeypatch.setattr(evaluate, "PERSONAS", tmp_path)
    monkeypatch.setattr(evaluate.sys, "argv", ["evaluate.py"])
    monkeypatch.setattr(evaluate, "POLICY", {"minimum": {"why": "TODO justify threshold"}})
    monkeypatch.setattr(evaluate, "ThreadPoolExecutor", lambda max_workers: ThreadPoolExecutor(max_workers=1))
    monkeypatch.setattr(
        evaluate, "decide",
        lambda text, context, persona: SimpleNamespace(decision=responses[text], reason="Measured decision"),
    )
    evaluate.main()
    output = capsys.readouterr().out
    assert "catch rate:       1/2" in output
    assert "false-block rate: 1/2" in output
    assert "exact match:      2/4" in output
    assert "minimum.why" in output
    assert "bad-b" in output and "good-b" in output


def test_practice_empty_persona_keeps_zero_denominators(monkeypatch, tmp_path, capsys):
    folder = tmp_path / "paralegal"
    folder.mkdir()
    (folder / "practice_set.json").write_text(json.dumps({"items": []}))
    monkeypatch.setattr(evaluate, "PERSONAS", tmp_path)
    monkeypatch.setattr(evaluate.sys, "argv", ["evaluate.py", "paralegal"])
    monkeypatch.setattr(evaluate, "POLICY", {"threshold": {"why": "Reviewed rule"}})
    monkeypatch.setattr(evaluate, "ThreadPoolExecutor", lambda max_workers: ThreadPoolExecutor(max_workers=1))
    evaluate.main()
    output = capsys.readouterr().out
    assert "catch rate:       0/0" in output
    assert "false-block rate: 0/0" in output
    assert "exact match:      0/0" in output
    assert "still TODO" not in output


def test_policy_review_flags_only_unresolved_explanations():
    policy = {
        "tone": {"why": " TODO explain minimum", "value": "TODO not an explanation"},
        "bias": {"why": "Reviewed"},
        "other": 7,
    }
    assert list(evaluate.todo_whys(policy)) == ["tone.why"]


@pytest.mark.parametrize(
    "persona,action,field",
    [
        ("customer_support", "send_email", "body"),
        ("paralegal", "submit_memo_to_partner", "memo"),
        ("financial_analyst", "publish_note_to_client", "note"),
    ],
)
def test_persona_dispatch_records_the_exact_mock_effect(monkeypatch, tmp_path, capsys, persona, action, field):
    log = tmp_path / "actions.log"
    monkeypatch.setattr(mock_actions, "LOG", log)
    mock_actions.run_for_persona(persona, "Retained payload")
    entry = json.loads(log.read_text())
    assert entry["action"] == action
    assert entry["args"][field] == "Retained payload"
    assert datetime.fromisoformat(entry["time"]).utcoffset().total_seconds() == 0
    assert f"[MOCK ACTION] {action} executed" in capsys.readouterr().out


def test_citations_and_append_order_survive_the_mock_log(monkeypatch, tmp_path):
    log = tmp_path / "actions.log"
    monkeypatch.setattr(mock_actions, "LOG", log)
    mock_actions.submit_memo_to_partner("First memo", ["Retained citation"])
    mock_actions.submit_memo_to_partner("Second memo")
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    assert [entry["args"]["memo"] for entry in entries] == ["First memo", "Second memo"]
    assert entries[0]["args"]["citations"] == ["Retained citation"]
    assert entries[1]["args"]["citations"] == []


def test_unknown_persona_cannot_add_an_action(monkeypatch, tmp_path):
    log = tmp_path / "actions.log"
    monkeypatch.setattr(mock_actions, "LOG", log)
    mock_actions.run_for_persona("customer_support", "Control payload")
    before = log.read_bytes()
    with pytest.raises(ValueError, match="unknown persona"):
        mock_actions.run_for_persona("unknown", "Forbidden payload")
    assert log.read_bytes() == before
