"""Customer-support action gate with scoped Probity evidence packets.

Company facts are checked before calling Ezio. Evidence adjudication and
permission to run the mock action remain separate decisions.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import math
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import audit
import mock_actions
from ezio_client import EzioError, analyze
from probity_bridge import adjudicate_checks
from source_checks import Finding, assess_source_of_truth, check_source_of_truth

LOGGER = logging.getLogger(__name__)
HERE = Path(__file__).resolve().parent
PERSONAS = HERE.parent / "personas"
POLICY = json.loads((HERE / "policy.json").read_text())
APPROVE, HOLD, BLOCK = "APPROVE", "HOLD_FOR_REVIEW", "BLOCK"
RANK = {APPROVE: 0, HOLD: 1, BLOCK: 2}


@dataclass
class Signals:
    """Validated producer-reported signals; they are not independent witnesses."""

    tier: str
    score: float
    summary: str
    accuracy_status: str
    claims_total: int
    claims_verified: int
    claims_contradicted: int
    tone: float
    bias: float
    agreement: float
    claims: list[dict[str, Any]] = field(default_factory=list)
    accuracy_score: float | None = None


@dataclass
class Decision:
    """Action permission, reasons, retained report, and separate evidence packet."""

    decision: str
    reason: str
    evidence: list[str]
    ezio: dict[str, Any]
    decision_packet: dict[str, Any] = field(default_factory=dict)


def _score(value: Any) -> float:
    """Require a finite numeric score in the published zero-to-100 range."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("score must be numeric")
    if not 0 <= value <= 100 or not math.isfinite(value):
        raise ValueError("score must be finite and between zero and 100")
    return value


def _count(value: Any) -> int:
    """Refuse coercions, booleans, and negative claim counters."""
    if type(value) is not int or value < 0:
        raise ValueError("claim count must be a nonnegative integer")
    return value


def _claims(records: Any) -> list[dict[str, Any]]:
    """Validate claim records without inferring truth from aggregate scores."""
    if not isinstance(records, list):
        raise TypeError("claims must be an array")
    for claim in records:
        if not isinstance(claim, dict):
            raise TypeError("claim must be an object")
        if not isinstance(claim.get("text"), str) or not claim["text"].strip():
            raise ValueError("claim text must be a nonempty string")
        if claim.get("verdict") not in ("supported", "contradicted", "unverifiable"):
            raise ValueError("claim verdict is invalid")
    return records


def _accuracy(
    acc: dict[str, Any], claims: list[dict[str, Any]]
) -> tuple[int, int, int]:
    """Require counters to match retained records and the accuracy status."""
    counts = tuple(
        _count(acc.get(key, 0))
        for key in ("claims_total", "claims_verified", "claims_contradicted")
    )
    actual = (
        len(claims),
        sum(c["verdict"] == "supported" for c in claims),
        sum(c["verdict"] == "contradicted" for c in claims),
    )
    if counts != actual:
        raise ValueError("claim counters do not match records")
    if acc["status"] != "scored" and claims:
        raise ValueError("unscored report contains claims")
    if acc["status"] == "scored" and not claims:
        raise ValueError("scored report has no claims")
    return counts


def parse_signals(resp: dict[str, Any]) -> Signals:
    """Validate the published report schema or raise a retained EzioError.

    Parameters
    ----------
    resp : dict
        Original response from the verification producer.

    Returns
    -------
    Signals
        Validated counters, dimensions, and claim records.

    Raises
    ------
    EzioError
        Malformed producer response; original response is retained.
    """
    try:
        verdict, dims = resp["verdict"], resp["dimensions"]
        acc = dims["accuracy"]
        if verdict["tier"] not in ("GREEN", "YELLOW", "RED"):
            raise ValueError("invalid tier")
        if acc["status"] not in ("scored", "not_scored", "unavailable"):
            raise ValueError("invalid accuracy status")
        if not isinstance(verdict["summary"], str):
            raise TypeError("summary must be a string")
        claims = _claims(resp.get("claims", []))
        total, verified, contradicted = _accuracy(acc, claims)
        accuracy_score = _score(acc["score"]) if acc["status"] == "scored" else None
        return Signals(
            verdict["tier"],
            _score(verdict["score"]),
            verdict["summary"],
            acc["status"],
            total,
            verified,
            contradicted,
            *(_score(dims[d]["score"]) for d in ("tone", "bias", "agreement")),
            claims,
            accuracy_score,
        )
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        LOGGER.warning("Ezio response rejected: %s", error)
        raise EzioError("malformed mock response", resp) from error


def _load_sources(persona: str) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Parse and hash the same selected bytes, avoiding stale cache bindings."""
    data, bindings = {}, []
    for path in sorted((PERSONAS / persona).iterdir()):
        if path.stem in ("practice_set", "business_rules") or path.suffix not in (
            ".json",
            ".csv",
        ):
            continue
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        data[path.stem] = (
            json.loads(text)
            if path.suffix == ".json"
            else list(csv.DictReader(io.StringIO(text)))
        )
        bindings.append(
            {
                "path": str(path.relative_to(HERE.parent)),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    return data, bindings


def load_source_of_truth(persona: str) -> dict[str, Any]:
    """Read selected persona sources without practice labels or cached bytes."""
    return _load_sources(persona)[0]


def _local_checks(
    text: str, context: dict[str, Any], persona: str
) -> tuple[list[Finding], list[dict[str, Any]], list[dict[str, str]]]:
    """Require the declared source files; unavailable source evidence holds."""
    try:
        sot, bindings = _load_sources(persona)
        if persona != "customer_support":
            raise ValueError("This implementation covers customer_support only.")
        if not {"policies", "price_sheet"}.issubset(sot):
            raise ValueError("Required company sources are missing.")
        findings = check_source_of_truth(text, context, sot)
        local = assess_source_of_truth(text, context, sot)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        message = f"Company-source checks unavailable: {error}"
        LOGGER.warning("%s", message)
        return (
            [Finding(HOLD, message)],
            [
                {
                    "status": "not_established",
                    "claim": message,
                    "source": "company_policy",
                    "provenance": "consumer_selected_local_file",
                }
            ],
            [],
        )
    checks = [
        {
            **asdict(check),
            "source_path": check.source,
            "source": "company_policy",
            "provenance": "consumer_selected_local_file",
        }
        for check in local
    ]
    return findings, checks, bindings


def _report_findings(signals: Signals, policy: dict[str, Any]) -> list[Finding]:
    """Convert claim-level producer assertions into explicit action findings."""
    findings = []
    if signals.claims_contradicted:
        findings.append(
            Finding(
                BLOCK, f"{signals.claims_contradicted} reported claim(s) contradicted."
            )
        )
    unresolved = (
        signals.claims_total - signals.claims_verified - signals.claims_contradicted
    )
    if unresolved:
        findings.append(Finding(HOLD, f"{unresolved} claim(s) remain unverifiable."))
    if signals.accuracy_status == "unavailable":
        findings.append(
            Finding(policy["when_accuracy_unavailable"], "fact-check was unavailable")
        )
    if (
        signals.accuracy_status == "not_scored"
        and policy["when_accuracy_not_scored"] != "ignore"
    ):
        findings.append(
            Finding(policy["when_accuracy_not_scored"], "accuracy was not scored")
        )
    return findings


def apply_policy(
    signals: Signals, findings: list[Finding], policy: dict[str, Any]
) -> tuple[list[str], str]:
    """Apply contradiction dominance and explicit review thresholds."""
    all_findings = list(findings) + _report_findings(signals, policy)
    for dimension, rule in policy["dimension_minimums"].items():
        score = getattr(signals, dimension)
        if score < rule["hold_below"]:
            all_findings.append(
                Finding(
                    HOLD, f"{dimension} score {score} is below {rule['hold_below']}"
                )
            )
    tier_decision = policy["tier_decisions"][signals.tier]
    if tier_decision != APPROVE:
        all_findings.append(
            Finding(tier_decision, f"Ezio tier is {signals.tier} ({signals.score})")
        )
    return _resolve(all_findings)


def _resolve(findings: list[Finding]) -> tuple[list[str], str]:
    """Reduce action findings by severity; finding order cannot weaken a block."""
    decision = max((f.level for f in findings), key=RANK.get, default=APPROVE)
    reasons = list(dict.fromkeys(f.message for f in findings))
    return reasons or [
        "Selected policy checks passed; no blocking or review findings."
    ], decision


def _report_checks(
    response: dict[str, Any], signals: Signals | None
) -> list[dict[str, Any]]:
    """Label producer assertions explicitly, including failed verification."""
    if signals is None or signals.accuracy_status == "unavailable":
        return [
            {
                "status": "not_established",
                "claim": "Verification service supplied usable accuracy evidence.",
                "source": "ezio_report",
                "provenance": "producer_asserted",
            }
        ]
    return [
        {
            "status": "not_established"
            if c["verdict"] == "unverifiable"
            else c["verdict"],
            "claim": c["text"],
            "source": "ezio_report",
            "reported_source": c.get("source"),
            "provenance": "producer_asserted",
        }
        for c in signals.claims
    ]


def _digest(value: Any) -> str:
    """Bind retained local input bytes; this hash is not an external witness."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _packet(
    text: str,
    context: dict[str, Any],
    checks: list[dict[str, Any]],
    bindings: list[dict[str, str]],
    decision: str,
) -> dict[str, Any]:
    """Use the authentic Probity kernel and retain reproducible consumer policy."""
    case_id = "support-" + _digest({"text": text, "context": context})[:16]
    packet = adjudicate_checks(case_id, checks, POLICY)
    packet.update(
        action_decision=decision,
        input_sha256=_digest({"text": text, "context": context}),
        source_bindings=bindings,
        action_policy=POLICY,
        action_policy_sha256=_digest(POLICY),
    )
    return packet


def decide(
    text: str,
    context: dict[str, Any] | None = None,
    persona: str = "customer_support",
    scenario: str | None = None,
    audit_log: bool = True,
) -> Decision:
    """Check company sources before Ezio and retain a scoped evidence decision."""
    context = context or {}
    findings, checks, bindings = _local_checks(text, context, persona)
    signals = None
    try:
        response = analyze(text, scenario=scenario)
        signals = parse_signals(response)
    except EzioError as error:
        response = {"error": str(error), "response": error.response}
        findings.append(
            Finding(POLICY["on_ezio_failure"], f"Ezio unavailable: {error}")
        )
        reasons, decision = _resolve(findings)
    else:
        reasons, decision = apply_policy(signals, findings, POLICY)
    checks.extend(_report_checks(response, signals))
    evidence = [f"{c['source']}: {c['claim']} [{c['status']}]" for c in checks]
    if not evidence:
        evidence.append(
            "Selected company sources were loaded; no in-scope factual claim was extracted. This is not a universal accuracy finding."
        )
    result = Decision(
        decision,
        "; ".join(reasons),
        evidence,
        response,
        _packet(text, context, checks, bindings, decision),
    )
    if audit_log:
        audit.record(persona, text, context, result)
    LOGGER.info("Support gate decision: %s", decision)
    return result


def gate(
    text: str, context: dict[str, Any] | None = None, persona: str = "customer_support"
) -> Decision:
    """Execute the provided mock action only when the action policy approves."""
    result = decide(text, context, persona)
    print(f"{result.decision}: {result.reason}")
    if result.decision == APPROVE:
        mock_actions.run_for_persona(persona, text)
    else:
        print("[GATE] action not executed")
    return result


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit('usage: python3 wrapper.py <persona> "<text>" [context-json]')
    gate(sys.argv[2], json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}, sys.argv[1])
