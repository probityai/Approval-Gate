"""Compare a typed report with consumer-selected broker-recorded completion."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .. import broker_records as records
from ..common import CaseError, MAX_ARTIFACT_BYTES, _artifact, _digest, _instant, _object


def adjudicate(case: Any, policy: Any, artifact_root: Path) -> dict:
    records.profile(case)
    records.profile(policy)
    case = _object(case, "case", {"schema_version", "case_id", "artifacts"})
    policy = _object(policy, "policy", {"schema_version", "witnesses", "assessments"})
    records.require(case["schema_version"] == "probity-case/v1"
                    and policy["schema_version"] == "probity-policy/v1", "unsupported schema_version")
    case_id = records.identifier(case["case_id"], "case_id")
    records.require(type(case["artifacts"]) is dict and type(policy["witnesses"]) is dict
                    and type(policy["assessments"]) is dict, "invalid artifact or policy maps")
    assessment = _object(policy["assessments"].get(case_id), "assessment",
                         {"claim_type", "report", "broker_witness", "context", "retained_target"})
    records.require(assessment["claim_type"] == "brokered_file_write/v1", "unsupported claim_type")
    context = _object(assessment["context"], "context",
                      {"interval_id", "request_id", "path", "intended_content_sha256", "authority_scope"})
    for field in ("interval_id", "request_id"):
        records.identifier(context[field], "context." + field)
    for field in ("path", "authority_scope"):
        records.absolute_path(context[field], "context." + field)
    records.require(context["path"].startswith(context["authority_scope"] + "/"), "context path outside scope")
    _digest(context["intended_content_sha256"], "context.intended_content_sha256")
    witness_id = records.identifier(assessment["broker_witness"], "broker_witness")
    witness = _object(policy["witnesses"].get(witness_id), "broker witness",
                      {"observer_key", "witness_key", "witness_scope", "record"})
    observer = _digest(witness["observer_key"], "observer_key")
    witness_key = _digest(witness["witness_key"], "witness_key")
    records.require(observer != witness_key and witness["witness_scope"] == "PEER", "invalid witness boundary")
    evidence = witness["record"]
    records.require(type(evidence) is dict and type(evidence.get("state")) is str, "missing typed evidence state")
    if evidence["state"] == "sealed":
        _object(evidence, "sealed evidence", {"state", "packet", "history", "ledger", "head"})
        records.require((evidence["ledger"] is None) == (evidence["head"] is None), "ledger/head must be paired")
    elif evidence["state"] == "missing_terminal":
        _object(evidence, "missing terminal evidence", {"state", "history", "ledger", "head"})
        records.require(evidence["ledger"] is not None and evidence["head"] is not None,
                        "missing terminal needs retained ledger/head selectors")
    else:
        raise CaseError("unsupported evidence state")

    selectors = {"report": assessment["report"]}
    selectors.update({key: value for key, value in evidence.items() if key != "state" and value is not None})
    if assessment["retained_target"] is not None:
        target = _object(assessment["retained_target"], "retained_target", {"artifact", "sha256", "captured_at"})
        _instant(target["captured_at"], "retained_target.captured_at")
        selectors["target"] = {key: target[key] for key in ("artifact", "sha256")}
    identifiers = set()
    raw, bindings = {}, {}
    for role, candidate in selectors.items():
        selector = _object(candidate, role + " selector", {"artifact", "sha256"})
        artifact_id = records.identifier(selector["artifact"], role + ".artifact")
        records.require(artifact_id not in identifiers, "each evidence role needs a separate artifact")
        identifiers.add(artifact_id)
        pin = _digest(selector["sha256"], role + ".sha256")
        metadata = _object(case["artifacts"].get(artifact_id), "artifact metadata", {"path", "sha256", "length"})
        records.require(metadata["sha256"] == pin, "consumer artifact pin differs from declared binding")
        maximum = MAX_ARTIFACT_BYTES if role == "target" else records.MAX_RECORD_BYTES
        records.require(type(metadata["length"]) is int and 0 <= metadata["length"] <= maximum,
                        "artifact exceeds role byte budget")
        raw[role], bindings[role] = _artifact(artifact_root, metadata, "artifacts." + artifact_id)
    records.require(set(case["artifacts"]) == identifiers, "case artifacts must match selected evidence roles")
    report = None
    if raw["report"] is not None:
        report = _object(records.parse_record(raw["report"], canonical_required=False), "report",
                         {"schema_version", "interval_id", "request_id", "path", "content_sha256", "completion_recorded"})
        records.require(report["schema_version"] == "brokered-file-write-report/v1"
                        and type(report["completion_recorded"]) is bool, "invalid report assertion")
        for field in ("interval_id", "request_id"):
            records.identifier(report[field], "report." + field)
        records.absolute_path(report["path"], "report.path")
        _digest(report["content_sha256"], "report.content_sha256")
    parsed = {}
    for role in ("packet",):
        if role in raw and raw[role] is not None:
            parsed[role] = records.parse_record(raw[role])
    entries = records.history(raw["history"]) if raw["history"] is not None else None
    receipts = None
    if "head" in raw and raw["head"] is not None:
        records.ledger_head(raw["head"], witness_key)
    if "ledger" in raw and raw["ledger"] is not None:
        receipts = records.verify_ledger(raw["ledger"], raw["head"], witness_key)
    packet = None
    if "packet" in parsed:
        packet = records.packet(parsed["packet"], entries, observer, witness_key, receipts,
                                ledger_selected=evidence["ledger"] is not None)
    prior = None
    if packet is None and entries:
        authority = {"intervalId": context["interval_id"], "scope": context["authority_scope"], "operation": "write-file"}
        begin = _object(entries[0]["event"], "opening event", {"kind", "commitment", "commitmentDigest"})
        records.require(begin["kind"] == "begin", "partial history has no opening")
        prior = records.commitment(begin["commitment"], observer, authority)
        records.events(entries, prior, context["authority_scope"], sealed=False)

    scope = {"witness_scope": "PEER", "observer_key": observer, "witness_key": witness_key,
             "evidence_state": evidence["state"], "consumer_context": context,
             "report_assertion": report, "artifact_bindings": bindings,
             "does_not_establish": ["arbitrary effects", "workflow success", "issuer permission",
                                    "key custody", "independent operation", "trusted time", "global history"]}
    checks = []
    if "target" in raw:
        target_digest = hashlib.sha256(raw["target"]).hexdigest() if raw["target"] is not None else None
        scope["retained_target"] = {"captured_at": assessment["retained_target"]["captured_at"],
                                    "sha256": target_digest,
                                    "matches_claimed_content": target_digest == report["content_sha256"]
                                    if target_digest is not None and report is not None else None,
                                    "historical_completion_predicate": False}

    def decide(decision, reason):
        return {"schema_version": "probity-decision/v1", "case_id": case_id,
                "claim_type": "brokered_file_write/v1", "decision": decision,
                "reason": reason, "scope": scope, "checks": checks}

    if any(raw[role] is None for role in raw if role != "target"):
        return decide("not_established", "required_artifact_unavailable")
    if evidence["state"] == "missing_terminal":
        selected = [item for item in receipts if item["intervalId"] == context["interval_id"]]
        records.require(not any(item["phase"] == "terminal" for item in selected),
                        "missing-terminal state contains a terminal receipt")
        if entries:
            if selected:
                opening = selected[0]
                records.require(opening["authorityDigest"] == prior["preimage"]["authorityDigest"]
                                and opening["observerKey"] == observer, "opening receipt context differs")
                records.checkpoint(opening["checkpoint"], witness_key, entries[:1])
        else:
            records.require(not selected, "registered opening lacks its original history")
        checks.append({"id": "authenticated_terminal", "status": "unavailable"})
        return decide("not_established", "authenticated_terminal_unavailable")

    claim = packet["claim"]
    if (claim["intervalId"], packet["authority"]["scope"]) != (context["interval_id"], context["authority_scope"]):
        return decide("not_established", "record_outside_consumer_context")
    checks.append({"id": "authenticated_terminal", "status": "passed"})
    if (report["interval_id"], report["request_id"], report["path"]) != (
            context["interval_id"], context["request_id"], context["path"]):
        return decide("not_established", "report_outside_consumer_context")
    selected = [write for write in claim["writes"] if write["requestId"] == context["request_id"]
                and write["path"] == context["path"]]
    scope["recorded_completions"] = selected
    scope["recorded_content_matches_intent"] = (selected[0]["contentDigest"] == context["intended_content_sha256"]
                                                if selected else None)
    present = bool(selected and selected[0]["contentDigest"] == report["content_sha256"])
    if "retained_target" in scope:
        scope["retained_target"]["matches_recorded_content"] = (
            scope["retained_target"]["sha256"] == selected[0]["contentDigest"]
            if selected and scope["retained_target"]["sha256"] is not None else None)
    if not present and claim["coverage"]["knownGaps"]:
        return decide("not_established", "gap_prevents_negative_record_conclusion")
    checks.append({"id": "completion_recorded", "status": "passed" if present == report["completion_recorded"] else "failed",
                   "observations": {"authenticated_selected_completion": present,
                                    "asserted_completion_recorded": report["completion_recorded"]}})
    return decide("supported" if present == report["completion_recorded"] else "contradicted",
                  "assertion_matches_authenticated_history" if present == report["completion_recorded"]
                  else "assertion_differs_from_authenticated_history")
