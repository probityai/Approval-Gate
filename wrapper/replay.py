"""Replay retained decisions offline without calling Ezio or executing actions.

Replay checks consistency with local bytes and the selected consumer policy.
It does not authenticate the audit record, Ezio, or real-world email delivery.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import probity_bridge
from probity_verify.json_input import parse_json

import wrapper as support

DEFAULT_POLICY = Path(__file__).with_name("policy.json")
KERNEL_FIELDS = (
    "schema_version",
    "case_id",
    "claim_type",
    "decision",
    "reason",
    "scope",
    "checks",
    "policy_sha256",
    "consumer_policy",
)
SCOPE = (
    "Consistency with retained inputs, current hash-matched company sources, "
    "the selected consumer policy, and the pinned Probity kernel. "
    "This does not authenticate the record or producer assertions, establish "
    "complete factual coverage, or prove email execution or delivery."
)


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _compare(name: str, retained: Any, replayed: Any) -> dict[str, str]:
    return {
        "check": name,
        "status": "matched"
        if _json_bytes(retained) == _json_bytes(replayed)
        else "mismatch",
    }


def _outcome(checks: list[dict[str, str]]) -> str:
    statuses = {check["status"] for check in checks}
    if "mismatch" in statuses:
        return "mismatch"
    if "refused" in statuses:
        return "refused"
    if statuses & {"not_established", "incomplete"}:
        return "incomplete"
    return "matched"


def _retained_report(
    report: Any, findings: list[support.Finding], policy: dict[str, Any]
) -> tuple[Any, list[str], str]:
    """Reapply action policy to the retained report without fetching a new one."""
    if not isinstance(report, dict):
        raise TypeError("retained_report_missing")
    if set(report) == {"error", "response"} and isinstance(report["error"], str):
        findings = findings + [
            support.Finding(
                policy["on_ezio_failure"], f"Ezio unavailable: {report['error']}"
            )
        ]
        reasons, action = support._resolve(findings)
        return None, reasons, action
    signals = support.parse_signals(report)
    reasons, action = support.apply_policy(signals, findings, policy)
    return signals, reasons, action


def _audit_checks(
    record: dict[str, Any], packet: dict[str, Any], policy: dict[str, Any]
) -> list[dict[str, str]]:
    text, context = record.get("input"), record.get("context")
    if not isinstance(text, str) or not isinstance(context, dict):
        raise TypeError("retained_input_missing")
    if record.get("persona") != "customer_support":
        raise ValueError("unsupported_persona")
    input_hash = support._digest({"text": text, "context": context})
    findings, local_checks, bindings = support._local_checks(
        text, context, "customer_support"
    )
    signals, reasons, action = _retained_report(record.get("ezio"), findings, policy)
    regenerated = local_checks + support._report_checks(record["ezio"], signals)
    evidence = [f"{c['source']}: {c['claim']} [{c['status']}]" for c in regenerated]
    if not evidence:
        evidence = [
            (
                "Selected company sources were loaded; no in-scope factual claim was extracted. "
                "This is not a universal accuracy finding."
            )
        ]
    checks = [
        _compare("input_sha256", packet.get("input_sha256"), input_hash),
        _compare("case_id", packet.get("case_id"), "support-" + input_hash[:16]),
        _compare("source_bindings", packet.get("source_bindings"), bindings),
        _compare("regenerated_checks", packet.get("checks"), regenerated),
        _compare("action_decision", packet.get("action_decision"), action),
        _compare("audit_decision", record.get("decision"), action),
        _compare("audit_reason", record.get("reason"), "; ".join(reasons)),
        _compare("audit_evidence", record.get("evidence"), evidence),
    ]
    if not bindings:
        checks.append({"check": "source_bytes", "status": "not_established"})
    return checks


def _packet_checks(packet: dict[str, Any]) -> list[dict[str, str]]:
    """A bare packet cannot bind raw input or independently regenerate checks."""
    _, bindings = support._load_sources("customer_support")
    return [
        _compare("source_bindings", packet.get("source_bindings"), bindings),
        {"check": "input_sha256", "status": "not_established"},
        {"check": "regenerated_checks", "status": "not_established"},
        {"check": "action_decision", "status": "not_established"},
    ]


def _normalize_ui_export(record: dict[str, Any]) -> dict[str, Any]:
    """Map the page's exact download contract to an ordinary audit object.

    The timestamps, optional review note, and action_executed assertion do
    not establish authority or action execution and do not enter the proof.
    """
    required = {"created_at", "workflow", "input", "result"}
    if required - record.keys() or record.keys() - required - {"review_handoff"}:
        raise ValueError("invalid_ui_export_fields")
    if record["workflow"] != "customer_support" or not isinstance(
        record["created_at"], str
    ):
        raise ValueError("invalid_ui_export_workflow")
    request, response = record["input"], record["result"]
    if not isinstance(request, dict) or not isinstance(response, dict):
        raise TypeError("invalid_ui_export_objects")
    if set(request) != {"persona", "text", "context"}:
        raise ValueError("invalid_ui_export_input_fields")
    if set(response) != {
        "decision",
        "reason",
        "evidence",
        "ezio",
        "decision_packet",
        "action_executed",
    }:
        raise ValueError("invalid_ui_export_result_fields")
    if not isinstance(response["action_executed"], bool):
        raise TypeError("invalid_ui_export_action_assertion")
    if "review_handoff" in record:
        review = record["review_handoff"]
        if not isinstance(review, dict):
            raise TypeError("invalid_ui_export_review")
        if (
            set(review) != {"note", "recorded_at", "action"}
            or review["action"] != "note_only"
        ):
            raise ValueError("invalid_ui_export_review_fields")
        if not isinstance(review["note"], str) or not isinstance(
            review["recorded_at"], str
        ):
            raise TypeError("invalid_ui_export_review_text")
    return {
        "timestamp": record["created_at"],
        "persona": request["persona"],
        "input": request["text"],
        "context": request["context"],
        **{
            key: response[key]
            for key in ("decision", "reason", "evidence", "ezio", "decision_packet")
        },
    }


def replay_record(record: Any, policy: dict[str, Any]) -> dict[str, Any]:
    """Replay an audit record or report the limits of a bare decision packet.

    Args:
        record: An ordinary audit object, the page's exact JSON download,
            or a bare decision packet.
        policy: The consumer-selected action policy, supplied independently.

    Returns:
        Deterministic checks and a replay status. A matched replay can retain
        any evidence verdict, including contradicted and not_established.
        Missing input provenance produces incomplete, never a full match.
    """
    checks: list[dict[str, str]] = []
    result: dict[str, Any] = {
        "schema_version": "probity-replay/v1",
        "scope": SCOPE,
        "checks": checks,
    }
    try:
        _json_bytes(record)
        _json_bytes(policy)
        if not isinstance(record, dict) or not isinstance(policy, dict):
            raise TypeError("expected_json_object")
        if {"created_at", "workflow", "result"} & record.keys():
            record = _normalize_ui_export(record)
        packet = record.get("decision_packet", record)
        if not isinstance(packet, dict):
            raise TypeError("decision_packet_missing")
        case_id = packet.get("case_id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError("case_id_missing")
        if (
            not isinstance(packet.get("action_decision"), str)
            or packet["action_decision"] not in support.RANK
        ):
            raise ValueError("invalid_action_decision")
        probity_bridge.verify_vendor_sources()
        replayed = probity_bridge.adjudicate_checks(
            case_id, packet.get("checks"), policy
        )
        checks.extend(
            [
                _compare("action_policy", packet.get("action_policy"), policy),
                _compare(
                    "action_policy_sha256",
                    packet.get("action_policy_sha256"),
                    support._digest(policy),
                ),
                _compare(
                    "consumer_policy",
                    packet.get("consumer_policy"),
                    replayed["consumer_policy"],
                ),
                _compare(
                    "policy_sha256",
                    packet.get("policy_sha256"),
                    replayed["policy_sha256"],
                ),
                _compare(
                    "kernel_packet",
                    {key: packet.get(key) for key in KERNEL_FIELDS},
                    replayed,
                ),
            ]
        )
        if "decision_packet" in record:
            checks.extend(_audit_checks(record, packet, policy))
        else:
            checks.extend(_packet_checks(packet))
        result.update(
            evidence_decision=replayed["decision"],
            retained_action_decision=packet.get("action_decision"),
            policy_sha256=replayed["policy_sha256"],
        )
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        support.EzioError,
    ):
        checks.append({"check": "replay_input", "status": "refused"})
    result["status"] = _outcome(checks)
    return result


def _parse(raw: bytes) -> Any:
    value = parse_json(raw, max_bytes=16_777_216, max_depth=64, max_nodes=200_000)
    _json_bytes(value)
    return value


def main(argv: list[str] | None = None) -> int:
    """Check a JSON packet, page export, audit object, or an audit JSONL file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "record",
        type=Path,
        help="Audit JSONL, audit JSON, page export JSON, or bare packet JSON",
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=DEFAULT_POLICY,
        help="Consumer-selected policy file",
    )
    args = parser.parse_args(argv)
    try:
        policy = _parse(args.policy.read_bytes())
        raw = args.record.read_bytes()
        if args.record.suffix == ".jsonl":
            records = [_parse(line) for line in raw.splitlines() if line.strip()]
        else:
            value = _parse(raw)
            records = value if isinstance(value, list) else [value]
        if not records:
            raise ValueError("empty_audit")
        results = [replay_record(record, policy) for record in records]
        statuses = [{"status": result["status"]} for result in results]
        status = _outcome(statuses)
        output = {
            "schema_version": "probity-replay-file/v1",
            "status": status,
            "records": results,
        }
    except (OSError, ValueError, TypeError, RecursionError):
        status = "refused"
        output = {
            "schema_version": "probity-replay-file/v1",
            "status": status,
            "error": "invalid_replay_input",
        }
    print(json.dumps(output, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return 0 if status == "matched" else 1 if status == "mismatch" else 2


if __name__ == "__main__":
    raise SystemExit(main())
