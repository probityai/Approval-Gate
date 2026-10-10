"""Match an identity claim against a consumer-pinned authority capture."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..common import CaseError, _artifact, _digest, _instant, _object, _string

CLAIM_TYPE = "authority_anchor/v1"


def _https_url(value: Any, label: str) -> str:
    url = _string(value, label)
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or
                parsed.password or parsed.fragment):
            raise ValueError()
        parsed.port
    except ValueError as exc:
        raise CaseError(f"{label}: expected HTTPS URL without credentials or fragment") from exc
    return url


def _capture(data: bytes) -> dict:
    """Read a bound JSON capture without accepting ambiguous object keys."""
    def unique(pairs: list[tuple[str, Any]]) -> dict:
        value: dict = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate JSON key")
            value[key] = item
        return value

    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (UnicodeDecodeError, ValueError) as exc:
        raise CaseError("capture is not unambiguous UTF-8 JSON") from exc
    return _object(value, "capture", {"schema_version", "request_url", "captured_at",
                                      "status", "body"})


def _pointer(body: Any, pointer: str) -> str | None:
    """Resolve an RFC 6901 pointer. A missing or non-string field is unavailable."""
    current = body
    for part in pointer.split("/")[1:]:
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if key not in current:
                return None
            current = current[key]
        elif (isinstance(current, list) and key.isascii() and key.isdecimal() and
              len(key) <= 19 and (key == "0" or not key.startswith("0")) and
              int(key) < len(current)):
            current = current[int(key)]
        else:
            return None
    return current if isinstance(current, str) else None


def adjudicate(case: Any, policy: Any, artifact_root: Path) -> dict:
    """Decide an exact JSON field in a bound capture, within a policy time window.

    The consumer must establish the capture's provenance outside this program.
    The program neither contacts the authority nor authenticates its transport.
    """
    case = _object(case, "case", {"schema_version", "case_id", "artifacts"})
    policy = _object(policy, "policy", {"schema_version", "witnesses", "assessments"})
    if case["schema_version"] != "probity-case/v1" or policy["schema_version"] != "probity-policy/v1":
        raise CaseError("unsupported schema_version")
    case_id = _string(case["case_id"], "case_id")
    artifacts = case["artifacts"]
    witnesses = policy["witnesses"]
    assessments = policy["assessments"]
    if not isinstance(artifacts, dict) or not isinstance(witnesses, dict) or not isinstance(assessments, dict):
        raise CaseError("artifacts, witnesses, and assessments: expected objects")
    if case_id not in assessments:
        raise CaseError("case_id has no consumer assessment")
    assessment = _object(assessments[case_id], "assessment", {
        "claim_type", "capture_witness", "request_url", "json_pointer",
        "expected_value", "capture_window"
    })
    if assessment["claim_type"] != CLAIM_TYPE:
        raise CaseError("unsupported claim_type")
    witness_id = _string(assessment["capture_witness"], "capture_witness")
    request_url = _https_url(assessment["request_url"], "request_url")
    expected = _string(assessment["expected_value"], "expected_value")
    pointer = _string(assessment["json_pointer"], "json_pointer")
    if not pointer.startswith("/") or any(
        pointer[i + 1:i + 2] not in {"0", "1"}
        for i, char in enumerate(pointer) if char == "~"
    ):
        raise CaseError("json_pointer: expected nonempty RFC 6901 pointer")
    window = _object(assessment["capture_window"], "capture_window", {"start", "end"})
    start = _instant(window["start"], "capture_window.start")
    end = _instant(window["end"], "capture_window.end")
    if end < start:
        raise CaseError("capture_window.end precedes start")
    if witness_id not in witnesses:
        raise CaseError("capture_witness has no consumer pin")
    witness = _object(witnesses[witness_id], "witness", {"artifact", "sha256", "authority"})
    artifact_id = _string(witness["artifact"], "witness.artifact")
    pinned = _digest(witness["sha256"], "witness.sha256")
    authority = _string(witness["authority"], "witness.authority")
    if artifact_id not in artifacts:
        raise CaseError("capture_witness names no case artifact")
    data, binding = _artifact(artifact_root, artifacts[artifact_id], f"artifacts.{artifact_id}")
    scope = {
        "claim_type": CLAIM_TYPE, "request_url": request_url,
        "capture_window": window, "json_pointer": pointer, "expected_value": expected,
        "witness": {"id": witness_id, "authority": authority,
                    "consumer_pinned_sha256": pinned, "artifact": binding},
        "limit": "The consumer pinned these capture bytes. This program does not authenticate the authority, transport, or capture time, or infer that a class-level detection stamp proves this particular observation.",
    }
    result = {"schema_version": "probity-decision/v1", "case_id": case_id,
              "claim_type": CLAIM_TYPE, "scope": scope, "checks": []}
    if data is None or binding["sha256"] != pinned:
        return {**result, "decision": "not_established", "reason": "witness_unavailable_or_unbound"}
    try:
        capture = _capture(data)
        if capture["schema_version"] != "probity-http-capture/v1":
            raise CaseError("unsupported capture schema_version")
        observed_at = _instant(capture["captured_at"], "capture.captured_at")
        if type(capture["status"]) is not int or not 100 <= capture["status"] <= 599:
            raise CaseError("capture.status: expected HTTP status")
        _https_url(capture["request_url"], "capture.request_url")
    except CaseError:
        return {**result, "decision": "not_established", "reason": "capture_unreadable"}
    scope["witness"]["captured_at"] = capture["captured_at"]
    scope["witness"]["status"] = capture["status"]
    if capture["request_url"] != request_url:
        return {**result, "decision": "not_established", "reason": "capture_target_mismatch"}
    if not start <= observed_at <= end:
        return {**result, "decision": "not_established", "reason": "capture_outside_window"}
    if capture["status"] != 200:
        return {**result, "decision": "not_established", "reason": "authority_response_unavailable"}
    observed = _pointer(capture["body"], pointer)
    result["checks"] = [{"id": "anchor", "status": "unavailable" if observed is None else
                         "met" if observed == expected else "failed", "observed": observed}]
    if observed is None:
        return {**result, "decision": "not_established", "reason": "field_unavailable"}
    if observed != expected:
        return {**result, "decision": "contradicted", "reason": "anchor_mismatch"}
    return {**result, "decision": "supported", "reason": "anchor_matches"}
