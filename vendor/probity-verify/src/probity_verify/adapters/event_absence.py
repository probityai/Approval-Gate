"""Check an absence claim against pinned visibility and observation records."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..common import CaseError, _artifact, _digest, _instant, _object, _string

CLAIM_TYPE = "event_absence/v1"


def _record(data: bytes, label: str, fields: set[str],
            optional: set[str] = frozenset()) -> dict:
    def unique(pairs: list[tuple[str, Any]]) -> dict:
        result: dict = {}
        for key, value in pairs:
            if key in result:
                raise CaseError(f"{label}: duplicate JSON key")
            result[key] = value
        return result

    def invalid_number(value: str) -> None:
        raise CaseError(f"{label}: non-JSON number {value}")

    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=unique,
                           parse_constant=invalid_number)
    except (UnicodeDecodeError, ValueError) as exc:
        raise CaseError(f"{label}: invalid UTF-8 JSON") from exc
    return _object(value, label, fields, optional)


def _scope(value: Any, label: str) -> dict:
    scope = _object(value, label, {"id", "start", "end"})
    _string(scope["id"], f"{label}.id")
    start = _instant(scope["start"], f"{label}.start")
    end = _instant(scope["end"], f"{label}.end")
    if end < start:
        raise CaseError(f"{label}: end precedes start")
    return scope


def _witness(case: dict, policy: dict, witness_id: str, root: Path,
             *, require_vantage: bool = False) -> tuple[bytes | None, dict]:
    witnesses = policy["witnesses"]
    artifacts = case["artifacts"]
    if witness_id not in witnesses:
        raise CaseError(f"unknown witness: {witness_id}")
    fields = {"artifact", "sha256", "authority"}
    if require_vantage:
        fields |= {"producer", "vantage"}
    witness = _object(witnesses[witness_id], f"witness.{witness_id}", fields)
    artifact_id = _string(witness["artifact"], f"witness.{witness_id}.artifact")
    if artifact_id not in artifacts:
        raise CaseError(f"unknown artifact: {artifact_id}")
    pinned = _digest(witness["sha256"], f"witness.{witness_id}.sha256")
    authority = _string(witness["authority"], f"witness.{witness_id}.authority")
    data, binding = _artifact(root, artifacts[artifact_id], f"artifacts.{artifact_id}")
    if binding.get("sha256") != pinned:
        data = None
    pin = {"id": witness_id, "artifact": artifact_id, "authority": authority,
           "consumer_pinned_sha256": pinned, "binding": binding}
    if require_vantage:
        pin["producer"] = _string(witness["producer"], f"witness.{witness_id}.producer")
        if not isinstance(witness["vantage"], str) or witness["vantage"] not in {
            "independent", "self_reported"
        }:
            raise CaseError(f"witness.{witness_id}.vantage: unsupported value")
        pin["vantage"] = witness["vantage"]
    return data, pin


def _observation(data: bytes, *, require_vantage: bool = False) -> dict:
    fields = {"schema_version", "claim_id", "invocation_id",
              "producer", "scope", "coverage"}
    if require_vantage:
        fields.add("vantage")
    record = _record(data, "observation", fields, {"events", "gaps"})
    if record["schema_version"] != "probity-observation/v1":
        raise CaseError("unsupported observation schema_version")
    _string(record["claim_id"], "observation.claim_id")
    _string(record["invocation_id"], "observation.invocation_id")
    _string(record["producer"], "observation.producer")
    if require_vantage and (not isinstance(record["vantage"], str) or
                            record["vantage"] not in {"independent", "self_reported"}):
        raise CaseError("observation.vantage: unsupported value")
    _scope(record["scope"], "observation.scope")
    if not isinstance(record["coverage"], str) or record["coverage"] not in {
        "complete", "incomplete", "unknown"
    }:
        raise CaseError("observation.coverage: unsupported value")
    gaps = record.get("gaps", [])
    if record["coverage"] == "incomplete":
        if not isinstance(gaps, list) or not gaps:
            raise CaseError("observation.gaps: incomplete coverage requires named gaps")
    elif "gaps" in record:
        raise CaseError("observation.gaps: only incomplete coverage can name gaps")
    events = record.get("events", [])
    if not isinstance(events, list):
        raise CaseError("observation.events: expected list")
    seen: set[str] = set()
    start = _instant(record["scope"]["start"], "observation.scope.start")
    end = _instant(record["scope"]["end"], "observation.scope.end")
    for index, item in enumerate(gaps):
        gap = _object(item, f"gaps[{index}]", {"start", "end"})
        gap_start = _instant(gap["start"], f"gaps[{index}].start")
        gap_end = _instant(gap["end"], f"gaps[{index}].end")
        if not start <= gap_start < gap_end <= end:
            raise CaseError("observation.gaps: gap outside scope or empty")
    for index, item in enumerate(events):
        event = _object(item, f"events[{index}]", {"id", "type", "time"})
        event_id = _string(event["id"], f"events[{index}].id")
        _string(event["type"], f"events[{index}].type")
        instant = _instant(event["time"], f"events[{index}].time")
        if event_id in seen or not start <= instant <= end:
            raise CaseError("observation.events: duplicate or out-of-scope event")
        seen.add(event_id)
    return record


def _capabilities(data: bytes) -> dict:
    record = _record(data, "capabilities", {"schema_version", "claim_id", "invocation_id",
                                            "producer", "visible_event_types"})
    if record["schema_version"] != "probity-capabilities/v1":
        raise CaseError("unsupported capabilities schema_version")
    for key in ("claim_id", "invocation_id", "producer"):
        _string(record[key], f"capabilities.{key}")
    types = record["visible_event_types"]
    if not isinstance(types, list) or any(not isinstance(t, str) or not t for t in types):
        raise CaseError("capabilities.visible_event_types: expected strings")
    if len(types) != len(set(types)):
        raise CaseError("capabilities.visible_event_types: duplicate type")
    return record


def adjudicate(case: Any, policy: Any, artifact_root: Path) -> dict:
    return _adjudicate(case, policy, artifact_root, require_vantage=False)


def adjudicate_v2(case: Any, policy: Any, artifact_root: Path) -> dict:
    return _adjudicate(case, policy, artifact_root, require_vantage=True)


def _adjudicate(case: Any, policy: Any, artifact_root: Path,
                *, require_vantage: bool) -> dict:
    case = _object(case, "case", {"schema_version", "case_id", "artifacts"})
    policy = _object(policy, "policy", {"schema_version", "witnesses", "assessments"})
    if case["schema_version"] != "probity-case/v1" or policy["schema_version"] != "probity-policy/v1":
        raise CaseError("unsupported schema_version")
    case_id = _string(case["case_id"], "case_id")
    if not isinstance(case["artifacts"], dict) or not isinstance(policy["witnesses"], dict):
        raise CaseError("artifacts and witnesses: expected objects")
    assessments = policy["assessments"]
    if not isinstance(assessments, dict) or case_id not in assessments:
        raise CaseError("case_id has no consumer assessment")
    fields = {
        "claim_type", "event_type", "invocation_id", "scope",
        "capability_witness", "observation_witness"}
    if require_vantage:
        fields.add("observed_party")
    assessment = _object(assessments[case_id], "assessment", fields)
    claim_type = "event_absence/v2" if require_vantage else CLAIM_TYPE
    if assessment["claim_type"] != claim_type:
        raise CaseError("unsupported claim_type")
    event_type = _string(assessment["event_type"], "event_type")
    invocation = _string(assessment["invocation_id"], "invocation_id")
    scope = _scope(assessment["scope"], "assessment.scope")
    capability_id = _string(assessment["capability_witness"], "capability_witness")
    observation_id = _string(assessment["observation_witness"], "observation_witness")
    observed_party = (
        _string(assessment["observed_party"], "observed_party")
        if require_vantage else None
    )
    if capability_id == observation_id:
        raise CaseError("capability and observation require separate witnesses")
    capability, capability_pin = _witness(case, policy, capability_id, artifact_root)
    observed, observation_pin = _witness(case, policy, observation_id,
                                         artifact_root, require_vantage=require_vantage)
    if capability_pin["artifact"] == observation_pin["artifact"]:
        raise CaseError("capability and observation require separate artifacts")
    limit = ("Pins and compares supplied records. Witness identity, capture completeness, "
             "and provenance must be established independently.")
    if require_vantage:
        limit = ("Producer and vantage are consumer pins, not authenticated identities. "
                 "Establish them outside this packet.")
    result = {
        "schema_version": "probity-decision/v1", "case_id": case_id,
        "claim_type": claim_type,
        "scope": {"event_type": event_type, "invocation_id": invocation,
                  "interval": scope, "witnesses": {"capability": capability_pin,
                                                   "observation": observation_pin},
                  "limit": limit},
        "checks": [],
    }

    def decide(decision: str, reason: str) -> dict:
        return {**result, "decision": decision, "reason": reason}

    if observed is None:
        return decide("not_established", "observation_unavailable_or_unbound")
    record = _observation(observed, require_vantage=require_vantage)
    if (record["claim_id"] != case_id or record["invocation_id"] != invocation or
            record["scope"] != scope):
        return decide("not_established", "observation_context_mismatch")
    manifest = _capabilities(capability) if capability is not None else None
    if require_vantage:
        independent = (record["vantage"] == observation_pin["vantage"] == "independent"
                       and record["producer"] == observation_pin["producer"]
                       and record["producer"] != observed_party)
        result["checks"].append({"id": "observation_vantage",
                                 "status": "met" if independent else "unavailable"})
        if not independent:
            return decide("not_established", "observation_vantage_unestablished")
    matching = [event["id"] for event in record.get("events", [])
                if event["type"] == event_type]
    event_check = {"id": "event", "status": "failed" if matching else "unavailable",
                   "field_present": "events" in record, "observed_ids": matching}
    result["checks"].append(event_check)
    if matching:
        return decide("contradicted", "event_observed")
    if manifest is None:
        return decide("not_established", "capability_unavailable_or_unbound")
    if (manifest["claim_id"] != case_id or manifest["invocation_id"] != invocation or
            manifest["producer"] != record["producer"]):
        return decide("not_established", "capability_context_mismatch")
    visible = event_type in manifest["visible_event_types"]
    complete = record["coverage"] == "complete"
    result["checks"].extend([
        {"id": "field_visibility", "status": "met" if visible else "unavailable"},
        {"id": "observation_coverage", "status": "met" if complete else "unavailable"},
    ])
    if not visible:
        return decide("not_established", "field_visibility_unestablished")
    if not complete:
        return decide("not_established", "observation_coverage_unestablished")
    event_check["status"] = "met"
    return decide("supported", "absence_within_covered_scope")
