"""Check a computation trace against consumer-pinned source and execution bytes.

The trace is evidence supplied to the verifier, not proof that an operation
actually ran. The consumer chooses the source and execution witnesses. An
independent observer must establish the trace's authority outside this adapter.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path
from typing import Any

from ..common import CaseError, _artifact, _digest, _object, _string

DECIMAL = re.compile(r"^-?(?:0|[1-9][0-9]{0,38})(?:\.[0-9]{1,18})?$")
OPERATIONS = {"add", "subtract", "multiply", "divide"}


def _number(value: Any, label: str) -> Fraction:
    """Parse a finite decimal exactly, without binary floating point or tolerance."""
    if not isinstance(value, str) or not DECIMAL.fullmatch(value):
        raise CaseError(f"{label}: expected finite decimal string")
    try:
        return Fraction(Decimal(value))
    except InvalidOperation as exc:
        raise CaseError(f"{label}: invalid decimal") from exc


def _json_object(data: bytes, label: str) -> dict:
    """Decode a bound artifact and reject duplicate keys and non-JSON numbers."""
    def unique(pairs: list[tuple[str, Any]]) -> dict:
        result: dict = {}
        for key, value in pairs:
            if key in result:
                raise CaseError(f"{label}: duplicate JSON key {key}")
            result[key] = value
        return result

    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (UnicodeDecodeError, ValueError) as exc:
        raise CaseError(f"{label}: invalid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise CaseError(f"{label}: expected JSON object")
    return value


def _values(data: bytes) -> dict[str, Fraction]:
    source = _object(_json_object(data, "source"), "source", {"values"})
    values = source["values"]
    if not isinstance(values, dict) or not values:
        raise CaseError("source.values: expected nonempty object")
    return {_string(key, "source key"): _number(value, f"source.{key}")
            for key, value in values.items()}


def _compute(operation: str, operands: list[Fraction]) -> Fraction | None:
    """Evaluate the four declared binary operations over exact rationals."""
    left, right = operands
    if operation == "add":
        return left + right
    if operation == "subtract":
        return left - right
    if operation == "multiply":
        return left * right
    if right == 0:
        return None
    return left / right


def _finite_decimal(value: Fraction) -> bool:
    """Whether a rational has an exact decimal representation."""
    denominator = value.denominator
    for factor in (2, 5):
        while denominator % factor == 0:
            denominator //= factor
    return denominator == 1


def _validate_trace(trace: dict) -> list[dict]:
    """Validate the complete trace before any step can return a verdict."""
    steps = trace["steps"]
    if not isinstance(steps, list) or not steps:
        raise CaseError("trace.steps: expected nonempty list")
    seen: set[str] = set()
    for index, item in enumerate(steps):
        step = _object(item, f"steps[{index}]", {"id", "operation", "operands", "result"})
        step_id = _string(step["id"], f"steps[{index}].id")
        if step_id in seen:
            raise CaseError("trace.steps: duplicate step id")
        seen.add(step_id)
        operation = _string(step["operation"], f"steps[{index}].operation")
        if operation not in OPERATIONS:
            raise CaseError(f"steps[{index}].operation: unsupported operation")
        operands = step["operands"]
        if not isinstance(operands, list) or len(operands) != 2:
            raise CaseError(f"steps[{index}].operands: expected two operands")
        for position, operand in enumerate(operands):
            label = f"steps[{index}].operands[{position}]"
            ref = _object(operand, label, {"kind", "ref", "value"})
            if _string(ref["kind"], f"{label}.kind") not in {"source", "constant", "step"}:
                raise CaseError(f"{label}.kind: unsupported reference kind")
            _string(ref["ref"], f"{label}.ref")
            _number(ref["value"], f"{label}.value")
        _number(step["result"], f"steps[{index}].result")
    _number(trace["figure"], "trace.figure")
    return steps


def _check_step(
    step: Any, sources: dict[str, Fraction], constants: dict[str, Fraction],
    prior: dict[str, Fraction], index: int,
) -> tuple[dict, Fraction | None]:
    """Resolve every operand before trusting this step's recorded result."""
    step_id = step["id"]
    operation = step["operation"]
    operands = step["operands"]
    observed: list[Fraction] = []
    refs: list[dict] = []
    for position, item in enumerate(operands):
        label = f"steps[{index}].operands[{position}]"
        kind = item["kind"]
        ref = item["ref"]
        value = _number(item["value"], f"{label}.value")
        known = {"source": sources, "constant": constants, "step": prior}[kind]
        referenced = known.get(ref)
        refs.append({"kind": kind, "ref": ref, "present": ref in known,
                     "grounded": referenced == value})
        observed.append(value)
    check = {"id": step_id, "operation": operation, "references": refs}
    if any(not ref["present"] for ref in refs):
        return {**check, "status": "unavailable", "reason": "reference_missing"}, None
    if not all(ref["grounded"] for ref in refs):
        return {**check, "status": "failed", "reason": "operand_unresolved"}, None
    computed = _compute(operation, observed)
    if computed is None:
        return {**check, "status": "failed", "reason": "division_by_zero"}, None
    if not _finite_decimal(computed):
        return {**check, "status": "unavailable", "reason": "rounding_rule_missing"}, None
    result = _number(step["result"], f"steps[{index}].result")
    if result != computed:
        return {**check, "status": "failed", "reason": "result_mismatch"}, None
    return {**check, "status": "met", "reason": "exact_recomputation"}, computed


def _evaluate(trace: dict, sources: dict[str, Fraction], constants: dict[str, Fraction],
              final_step: str) -> tuple[list[dict], str]:
    trace = _object(trace, "trace", {"steps", "figure"})
    steps = _validate_trace(trace)
    prior: dict[str, Fraction] = {}
    checks: list[dict] = []
    for index, step in enumerate(steps):
        check, result = _check_step(step, sources, constants, prior, index)
        checks.append(check)
        if check["status"] != "met":
            return checks, check["reason"]
        prior[check["id"]] = result
    if final_step not in prior:
        return checks, "final_step_unobserved"
    reachable = {final_step}
    used_constants: set[str] = set()
    for check in reversed(checks):
        if check["id"] not in reachable:
            continue
        for ref in check["references"]:
            if ref["kind"] == "step":
                reachable.add(ref["ref"])
            if ref["kind"] == "constant":
                used_constants.add(ref["ref"])
    if len(reachable) != len(checks):
        return checks, "step_not_in_final_lineage"
    if used_constants != constants.keys():
        return checks, "declared_constant_unused"
    figure = _number(trace["figure"], "trace.figure")
    if figure != prior[final_step]:
        return checks, "figure_mismatch"
    return checks, "lineage_recomputed"


def adjudicate(case: Any, policy: Any, artifact_root: Path) -> dict:
    """Adjudicate exact operand lineage within two pinned artifacts.

    Returns ``not_established`` when a witness cannot be bound or a rounding
    rule is needed. A bound trace with unresolved operands or arithmetic that
    does not reproduce its figure is ``contradicted``. Malformed input raises
    :class:`~probity_verify.common.CaseError`, outside the verdict axis.

    This cannot authenticate who observed the execution, prove the selected
    sources were complete, or decide whether the declared operation was the
    right operation for the user's question.
    """
    case = _object(case, "case", {"schema_version", "case_id", "artifacts"})
    policy = _object(policy, "policy", {"schema_version", "witnesses", "assessments"})
    if case["schema_version"] != "probity-case/v1" or policy["schema_version"] != "probity-policy/v1":
        raise CaseError("unsupported schema_version")
    case_id = _string(case["case_id"], "case_id")
    assessments = policy["assessments"]
    if not isinstance(assessments, dict) or case_id not in assessments:
        raise CaseError("case_id has no consumer assessment")
    assessment = _object(assessments[case_id], "assessment", {
        "claim_type", "source_witness", "execution_witness", "final_step", "constants"
    })
    if assessment["claim_type"] != "operand_lineage/v1":
        raise CaseError("unsupported claim_type")
    constants = assessment["constants"]
    if not isinstance(constants, dict):
        raise CaseError("constants: expected object")
    constants = {_string(key, "constant key"): _number(value, f"constants.{key}")
                 for key, value in constants.items()}
    witnesses = policy["witnesses"]
    artifacts = case["artifacts"]
    if not isinstance(witnesses, dict) or not isinstance(artifacts, dict):
        raise CaseError("witnesses and artifacts: expected objects")
    bound: dict[str, bytes | None] = {}
    scope: dict[str, Any] = {"claim_type": "operand_lineage/v1", "witnesses": {},
                             "final_step": assessment["final_step"],
                             "limit": "Checks the pinned values and trace only. Observer identity, source completeness, and operation suitability require separate evidence."}
    for role in ("source", "execution"):
        witness_id = _string(assessment[f"{role}_witness"], f"{role}_witness")
        if witness_id not in witnesses:
            raise CaseError(f"{role}_witness: unknown witness")
        witness = _object(witnesses[witness_id], f"{role} witness",
                          {"artifact", "sha256", "authority"})
        artifact_id = _string(witness["artifact"], f"{role}.artifact")
        if artifact_id not in artifacts:
            raise CaseError(f"{role}.artifact: unknown artifact")
        if role == "execution" and artifact_id == scope["witnesses"]["source"]["artifact_id"]:
            raise CaseError("source and execution require separate artifacts")
        pinned = _digest(witness["sha256"], f"{role}.sha256")
        _string(witness["authority"], f"{role}.authority")
        data, check = _artifact(artifact_root, artifacts[artifact_id], f"artifacts.{artifact_id}")
        bound[role] = data if check.get("sha256") == pinned else None
        scope["witnesses"][role] = {"id": witness_id, "artifact_id": artifact_id,
                                     "authority": witness["authority"],
                                     "consumer_pinned_sha256": pinned, "artifact": check}
    result = {"schema_version": "probity-decision/v1", "case_id": case_id,
              "claim_type": "operand_lineage/v1", "scope": scope, "checks": []}
    if any(data is None for data in bound.values()):
        return {**result, "decision": "not_established", "reason": "witness_unavailable_or_unbound"}
    sources = _values(bound["source"])
    trace = _json_object(bound["execution"], "execution")
    checks, reason = _evaluate(trace, sources, constants,
                               _string(assessment["final_step"], "final_step"))
    result["checks"] = checks
    decision = ("supported" if reason == "lineage_recomputed" else
                "not_established" if reason in {"rounding_rule_missing", "reference_missing"}
                else "contradicted")
    return {**result, "decision": decision, "reason": reason}
