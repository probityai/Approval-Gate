"""Adjudicate challenge checks using the authentic Probity decision kernel.

This module supplies a new, event-specific adapter. The vendored upstream
kernel and its existing adapters remain unmodified. A decision establishes
only how supplied check records reduce under the consumer policy; it does
not authenticate Ezio's assertions or establish real-world execution.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import sys
from pathlib import Path
from typing import Any, NoReturn

LOGGER = logging.getLogger(__name__)
CLAIM_TYPE = "customer_support_policy/v1"
UPSTREAM_COMMIT = "a2a7e23413be0ab0ad1001d60085a2ff21eaa907"
VENDOR_ROOT = Path(__file__).resolve().parents[1] / "vendor" / "probity-verify"


def verify_vendor_sources() -> dict[str, Any]:
    """Check every retained upstream source against its recorded digest.

    Returns
    -------
    dict[str, Any]
        Source manifest containing the upstream commit and per-file hashes.

    Raises
    ------
    RuntimeError
        If a retained source differs from its manifest, or the commit differs
        from :data:`UPSTREAM_COMMIT`. This detects accidental local changes;
        the manifest itself is not an independently authenticated trust root.
    """
    manifest = json.loads((VENDOR_ROOT / "SOURCE.json").read_text(encoding="utf-8"))
    if manifest["commit"] != UPSTREAM_COMMIT:
        raise RuntimeError(
            "vendored Probity source commit does not match the bridge pin"
        )
    for entry in manifest["files"]:
        data = (VENDOR_ROOT / entry["path"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise RuntimeError(
                f"vendored Probity source hash mismatch: {entry['path']}"
            )
    return manifest


SOURCE_MANIFEST = verify_vendor_sources()
sys.path.insert(0, str(VENDOR_ROOT / "src"))
from probity_verify import core as _kernel
from probity_verify.common import CaseError

if (
    Path(_kernel.__file__).resolve()
    != VENDOR_ROOT / "src" / "probity_verify" / "core.py"
):
    raise RuntimeError("Probity kernel was imported from an unexpected source")


def _refuse(message: str, cause: Exception | None = None) -> NoReturn:
    """Log a bounded validation reason and refuse malformed inputs."""
    LOGGER.warning("Probity bridge refused input: %s", message)
    raise CaseError(message) from cause


def _validate_checks(checks: Any) -> list[dict[str, Any]]:
    """Validate statuses while preserving the caller's scoped evidence fields."""
    if not isinstance(checks, list):
        _refuse("checks must be a JSON array")
    for check in checks:
        if not isinstance(check, dict):
            _refuse("each check must be a JSON object")
        status = check.get("status")
        if not isinstance(status, str) or status not in _kernel.DECISIONS:
            _refuse("check status must be supported, contradicted, or not_established")
    return copy.deepcopy(checks)


def _reduce_checks(checks: list[dict[str, Any]]) -> tuple[str, str]:
    """Reduce check statuses; contradictions dominate unresolved records."""
    statuses = {check["status"] for check in checks}
    if "contradicted" in statuses:
        return (
            "contradicted",
            "At least one supplied check contradicts the selected policy.",
        )
    if not checks or "not_established" in statuses:
        return (
            "not_established",
            "Necessary support is missing or unresolved in the supplied checks.",
        )
    return (
        "supported",
        "Every supplied check supports the selected policy within its stated scope.",
    )


def _adjudicate_customer_support(
    case: Any, policy: Any, artifact_root: Path
) -> dict[str, Any]:
    """Implement the versioned adapter interface consumed by the kernel.

    ``artifact_root`` is part of Probity's adapter interface. This adapter
    intentionally reads no artifacts and makes no network requests; retained
    source comparisons are performed by the wrapper before this reduction.
    """
    checks = _validate_checks(case.get("checks"))
    decision, reason = _reduce_checks(checks)
    return {
        "schema_version": "probity-decision/v1",
        "case_id": case["case_id"],
        "claim_type": CLAIM_TYPE,
        "decision": decision,
        "reason": reason,
        "scope": {
            "claim": "Supplied customer-support checks satisfy the selected consumer policy.",
            "evidence_basis": "Wrapper-supplied local checks and producer-reported Ezio assertions.",
            "upstream_repository": "https://github.com/probityai/probity-verify",
            "upstream_commit": UPSTREAM_COMMIT,
            "adapter": "Event-specific adapter; not an upstream Probity claim type.",
            "does_not_establish": [
                "Independent witnessing or authentication of Ezio assertions",
                "Complete coverage of all factual claims in arbitrary prose",
                "Execution or delivery of a real-world customer email",
                "Authority to act beyond the separately configured action policy",
            ],
        },
        "checks": checks,
    }


def _register_adapter() -> None:
    """Add the event adapter without replacing upstream registrations."""
    existing = _kernel.ADAPTERS.get(CLAIM_TYPE)
    if existing is not None and existing.__module__ != __name__:
        raise RuntimeError(
            "customer-support claim type is already registered by another adapter"
        )
    _kernel.ADAPTERS[CLAIM_TYPE] = _adjudicate_customer_support


_register_adapter()


def adjudicate_checks(
    case_id: str, checks: list[dict[str, Any]], policy: dict[str, Any]
) -> dict[str, Any]:
    """Return a policy-bound Probity decision for supplied challenge checks.

    Parameters
    ----------
    case_id : str
        Non-empty identifier for this assessment, selected by the wrapper.
    checks : list[dict[str, Any]]
        Scoped evidence records, each containing a ``status`` of
        ``supported``, ``contradicted``, or ``not_established``. Additional
        fields, including source and provenance labels, are retained exactly.
    policy : dict[str, Any]
        JSON-compatible consumer policy. A deep copy receives the selected
        assessment; neither this mapping nor ``checks`` is mutated.

    Returns
    -------
    dict[str, Any]
        Authentic ``probity-decision/v1`` envelope validated by the upstream
        kernel, including its SHA-256 binding to the exact effective policy.
        ``consumer_policy`` retains the effective policy for exact replay.
        This evidence decision is separate from APPROVE/HOLD/BLOCK and does
        not execute any action. Empty checks yield ``not_established``.

    Raises
    ------
    CaseError
        If input is malformed or not JSON-compatible. Malformed records are
        refused rather than converted into an evidence verdict.

    See Also
    --------
    verify_vendor_sources : Check retained upstream source digests.
    probity_verify.core.adjudicate : Validate envelopes and bind policy bytes.
    """
    if not isinstance(case_id, str) or not case_id.strip():
        _refuse("case_id must be a non-empty string")
    if not isinstance(policy, dict):
        _refuse("policy must be a JSON object")
    checked = _validate_checks(checks)
    effective_policy = copy.deepcopy(policy)
    assessments = effective_policy.setdefault("assessments", {})
    if not isinstance(assessments, dict):
        _refuse("consumer assessments must be a JSON object")
    assessments[case_id] = {"claim_type": CLAIM_TYPE}
    try:
        json.dumps({"policy": effective_policy, "checks": checked}, allow_nan=False)
    except (TypeError, ValueError) as error:
        _refuse("checks and policy must contain finite JSON-compatible values", error)
    result = _kernel.adjudicate(
        {"case_id": case_id, "checks": checked}, effective_policy, VENDOR_ROOT
    )
    result["consumer_policy"] = effective_policy
    LOGGER.debug(
        "Probity check adjudication completed with decision %s", result["decision"]
    )
    return result
