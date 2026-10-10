"""Consumer-side adjudication kernel and stable decision envelope.

New claim types register a versioned adapter. The kernel pins the exact
consumer policy in every decision and refuses unknown claim types.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .adapters import (
    authority_anchor,
    brokered_file_write,
    event_absence,
    operand_lineage,
    source_coverage,
    spend_reservation,
)
from .common import CaseError

ADAPTERS: dict[str, Callable[[Any, Any, Path], dict]] = {
    "brokered_file_write/v1": brokered_file_write.adjudicate,
    "source_text_coverage/v1": source_coverage.adjudicate,
    "operand_lineage/v1": operand_lineage.adjudicate,
    "authority_anchor/v1": authority_anchor.adjudicate,
    "event_absence/v1": event_absence.adjudicate,
    "event_absence/v2": event_absence.adjudicate_v2,
    "spend_reservation/v1": spend_reservation.adjudicate,
}
DECISIONS = {"supported", "contradicted", "not_established"}


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def adjudicate(case: Any, policy: Any, artifact_root: Path) -> dict:
    if not isinstance(case, dict) or not isinstance(policy, dict):
        raise CaseError("case and policy must be JSON objects")
    case_id = case.get("case_id")
    assessments = policy.get("assessments")
    if not isinstance(case_id, str) or not isinstance(assessments, dict):
        raise CaseError("case_id and consumer assessments are required")
    assessment = assessments.get(case_id)
    if not isinstance(assessment, dict):
        raise CaseError("case_id has no consumer assessment")
    claim_type = assessment.get("claim_type")
    if not isinstance(claim_type, str) or claim_type not in ADAPTERS:
        raise CaseError("unsupported claim_type")
    result = ADAPTERS[claim_type](case, policy, artifact_root)
    if (result.get("schema_version") != "probity-decision/v1" or
            result.get("decision") not in DECISIONS or
            result.get("case_id") != case_id or
            result.get("claim_type") != claim_type or
            not isinstance(result.get("scope"), dict) or
            not isinstance(result.get("checks"), list)):
        raise CaseError("adapter returned an invalid decision envelope")
    result["policy_sha256"] = hashlib.sha256(canonical_json(policy)).hexdigest()
    return result


def render_packet(result: dict) -> str:
    """Readable packet whose sections work for every registered claim type."""
    lines = [
        "Probity decision packet v1",
        f"Case: {json.dumps(result['case_id'], ensure_ascii=True)}",
        f"Claim: {result['claim_type']}",
        f"Decision: {result['decision']}",
        f"Reason: {result['reason']}",
        f"Policy SHA-256: {result['policy_sha256']}",
        "Scope and evidence:",
        json.dumps(result["scope"], indent=2, sort_keys=True, ensure_ascii=True),
        "Checks:",
        json.dumps(result["checks"], indent=2, sort_keys=True, ensure_ascii=True),
    ]
    return "\n".join(lines) + "\n"
