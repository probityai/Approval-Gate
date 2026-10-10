"""Replay a consumer-pinned spend trace without importing its producer."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..common import CaseError, _artifact, _digest, _object, _string

CLAIM_TYPE = "spend_reservation/v1"
MAX_INTEGER = (1 << 53) - 1
CALL_FIELDS = {"call_id", "attempt", "provider", "model", "target", "args_sha256",
               "input_limit", "output_limit", "price_sha256"}
PRICE_FIELDS = {"provider", "model", "target", "unit", "valid_from", "expires_at",
                "input_rate", "output_rate", "fixed_cost"}
BUDGET_FIELDS = {"schema_version", "budget_id", "unit", "limit", "valid_from",
                 "expires_at", "prices_sha256"}
EVENT_FIELDS = {
    "reserve": {"reservation", "call", "maximum", "remaining_before", "remaining_after"},
    "dispatch": {"reservation", "call", "maximum", "remaining_after"},
    "settle": {"reservation", "actual", "bound_exceeded", "remaining_after"},
    "refund": {"reservation", "amount", "reason", "remaining_after"},
    "deny": {"call", "reason", "remaining_after"},
}


def _hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _json(data: bytes, label: str) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise CaseError(f"{label}: duplicate JSON key")
            result[key] = value
        return result

    def nonfinite(value: str) -> None:
        raise CaseError(f"{label}: non-JSON number {value}")

    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=unique,
                          parse_constant=nonfinite)
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise CaseError(f"{label}: invalid UTF-8 JSON ({exc})") from exc


def _integer(value: Any, label: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_INTEGER:
        raise CaseError(f"{label}: expected nonnegative safe integer")
    return value


def _text(value: Any, label: str) -> str:
    value = _string(value, label)
    if not value.isascii() or len(value) > 256:
        raise CaseError(f"{label}: expected at most 256 ASCII characters")
    return value


def _call_fault(value: Any) -> str | None:
    """A deny record can retain the malformed call that the host refused."""
    if not isinstance(value, dict) or set(value) != CALL_FIELDS:
        return "invalid-call"
    for field in ("call_id", "provider", "model", "target"):
        text = value[field]
        if not isinstance(text, str) or not text or not text.isascii() or len(text) > 256:
            return "invalid-" + field
    for field in ("attempt", "input_limit", "output_limit"):
        number = value[field]
        if type(number) is not int or not 0 <= number <= MAX_INTEGER:
            return "invalid-" + field
    for field in ("args_sha256", "price_sha256"):
        try:
            _digest(value[field], field)
        except CaseError:
            return "invalid-" + field.replace("_", "-")
    return None


def _validate(budget: Any, prices: Any, trace: Any) -> None:
    _object(budget, "budget policy", BUDGET_FIELDS)
    if budget["schema_version"] != "probity-spend-policy/v1":
        raise CaseError("unsupported spend policy schema")
    for field in ("budget_id", "unit"):
        _text(budget[field], "budget." + field)
    for field in ("limit", "valid_from", "expires_at"):
        _integer(budget[field], "budget." + field)
    _digest(budget["prices_sha256"], "budget.prices_sha256")
    if budget["expires_at"] <= budget["valid_from"]:
        raise CaseError("budget: empty or reversed validity window")
    if not isinstance(prices, list) or not 1 <= len(prices) <= 256:
        raise CaseError("prices: expected 1 to 256 price records")
    routes = set()
    for price in prices:
        _object(price, "price", PRICE_FIELDS)
        for field in ("provider", "model", "target", "unit"):
            _text(price[field], "price." + field)
        for field in ("valid_from", "expires_at", "input_rate", "output_rate", "fixed_cost"):
            _integer(price[field], "price." + field)
        route = tuple(price[field] for field in ("provider", "model", "target"))
        if route in routes or price["expires_at"] <= price["valid_from"]:
            raise CaseError("prices: duplicate route or invalid validity window")
        routes.add(route)
    _object(trace, "trace", {"schema_version", "budget_id", "policy_sha256", "events"})
    if trace["schema_version"] != "probity-spend-trace/v1":
        raise CaseError("unsupported spend trace schema")
    _text(trace["budget_id"], "trace.budget_id")
    _digest(trace["policy_sha256"], "trace.policy_sha256")
    if not isinstance(trace["events"], list):
        raise CaseError("trace.events: expected list")
    for event in trace["events"]:
        if (not isinstance(event, dict) or not isinstance(event.get("kind"), str)
                or event["kind"] not in EVENT_FIELDS):
            raise CaseError("trace event: unsupported kind")
        _object(event, "trace event", {"sequence", "kind", "at"} | EVENT_FIELDS[event["kind"]])
        for field in ("sequence", "at", "maximum", "remaining_before", "actual", "amount"):
            if field in event:
                _integer(event[field], "event." + field)
        # An over-bound settlement can produce a negative balance. It is a
        # contradictory trace, rather than malformed syntax to hide that debt.
        if type(event["remaining_after"]) is not int:
            raise CaseError("event.remaining_after: expected integer")
        if "reservation" in event:
            _digest(event["reservation"], "event.reservation")
        if event["kind"] in {"reserve", "dispatch"}:
            fault = _call_fault(event["call"])
            if fault:
                raise CaseError("event.call: " + fault)
        if "bound_exceeded" in event and type(event["bound_exceeded"]) is not bool:
            raise CaseError("event.bound_exceeded: expected boolean")
        if "reason" in event:
            _text(event["reason"], "event.reason")


class _Mismatch(Exception):
    pass


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise _Mismatch(reason)


class _Replay:
    def __init__(self, budget: dict, prices: list[dict]) -> None:
        self.budget = budget
        self.prices = {tuple(price[k] for k in ("provider", "model", "target")): price
                       for price in prices}
        self.reservations: dict[str, dict] = {}
        self.refund_due: str | None = None
        self.at = budget["valid_from"]
        self.sequence = 0

    def remaining(self) -> int:
        return self.budget["limit"] - sum(
            row["actual"] if row["state"] == "settled" else
            0 if row["state"] == "cancelled" else row["maximum"]
            for row in self.reservations.values())

    def priced(self, call: dict, at: int) -> int:
        price = self.prices.get(tuple(call[k] for k in ("provider", "model", "target")))
        _require(price is not None, "unknown-price")
        _require(_hash(price) == call["price_sha256"], "price-version-mismatch")
        _require(price["unit"] == self.budget["unit"], "price-unit-mismatch")
        _require(self.budget["valid_from"] <= at < self.budget["expires_at"],
                 "budget-window-closed")
        _require(price["valid_from"] <= at < price["expires_at"], "stale-price")
        maximum = (call["input_limit"] * price["input_rate"] +
                   call["output_limit"] * price["output_rate"] + price["fixed_cost"])
        _require(maximum <= MAX_INTEGER, "invalid-maximum")
        return maximum

    def reservation_id(self, call: dict) -> str:
        return _hash({"budget_id": self.budget["budget_id"],
                      "call_id": call["call_id"], "attempt": call["attempt"]})

    def denied(self, event: dict) -> None:
        call = event["call"]
        reason = _call_fault(call)
        if reason is None:
            try:
                maximum = self.priced(call, event["at"])
                existing = self.reservations.get(self.reservation_id(call))
                if existing and existing["call"] != call:
                    reason = "call-attempt-rebound"
                elif existing is None and maximum > self.remaining():
                    reason = "insufficient-budget"
            except _Mismatch as exc:
                reason = str(exc)
        _require(reason is not None and event["reason"] == reason, "deny-reason-mismatch")

    def apply(self, event: dict) -> None:
        _require(event["sequence"] == self.sequence + 1, "trace-sequence-mismatch")
        _require(event["at"] >= self.at, "trace-clock-rollback")
        if self.refund_due is not None:
            _require(event["kind"] == "refund" and event["reservation"] == self.refund_due,
                     "settlement-refund-missing")
        self.sequence = event["sequence"]
        self.at = event["at"]
        kind = event["kind"]
        identity = event.get("reservation")
        row = self.reservations.get(identity)
        if kind == "reserve":
            call = event["call"]
            maximum = self.priced(call, self.at)
            _require(identity == self.reservation_id(call), "reservation-id-mismatch")
            _require(row is None, "reservation-reused")
            _require(maximum == event["maximum"], "reservation-maximum-mismatch")
            _require(event["remaining_before"] == self.remaining(), "reserve-balance-mismatch")
            _require(maximum <= self.remaining(), "over-budget-reservation")
            self.reservations[identity] = {"call": call, "maximum": maximum,
                                           "state": "reserved", "actual": None,
                                           "refund_seen": False}
        elif kind == "dispatch":
            maximum = self.priced(event["call"], self.at)
            _require(row is not None and row["state"] == "reserved",
                     "dispatch-without-unused-reservation")
            _require(row["call"] == event["call"], "dispatch-call-mismatch")
            _require(row["maximum"] == maximum == event["maximum"], "dispatch-maximum-mismatch")
            row["state"] = "dispatched"
        elif kind == "settle":
            _require(row is not None and row["state"] == "dispatched", "settlement-without-dispatch")
            _require(event["actual"] <= row["maximum"], "settlement-bound-exceeded")
            _require(event["bound_exceeded"] is False, "settlement-bound-flag-mismatch")
            row.update(state="settled", actual=event["actual"])
            self.refund_due = identity
        elif kind == "refund":
            _require(row is not None, "refund-without-reservation")
            if event["reason"] == "cancel-before-dispatch":
                _require(row["state"] == "reserved", "refund-after-dispatch-or-used")
                _require(event["amount"] == row["maximum"], "refund-amount-mismatch")
                row["state"] = "cancelled"
            else:
                _require(event["reason"] == "unused-reservation" and row["state"] == "settled"
                         and self.refund_due == identity and not row["refund_seen"],
                         "refund-without-unrefunded-settlement")
                _require(event["amount"] == row["maximum"] - row["actual"], "refund-amount-mismatch")
                row["refund_seen"] = True
                self.refund_due = None
        else:
            self.denied(event)
        _require(event["remaining_after"] == self.remaining(), "event-balance-mismatch")

    def accounting(self) -> dict:
        rows = list(self.reservations.values())
        held = sum(row["maximum"] for row in rows if row["state"] in {"reserved", "dispatched"}
                   or (row["state"] == "settled" and not row["refund_seen"]))
        settled = sum(row["actual"] for row in rows
                      if row["state"] == "settled" and row["refund_seen"])
        return {"limit": self.budget["limit"], "unit": self.budget["unit"],
                "held_maximum": held, "recorded_settled_cost": settled,
                "conservative_remaining": self.budget["limit"] - held - settled,
                "pending_dispatch_receipts": sum(row["state"] == "dispatched" for row in rows),
                "execution_outcome": "not_established"}


def adjudicate(case: Any, policy: Any, artifact_root: Path) -> dict:
    case = _object(case, "case", {"schema_version", "case_id", "artifacts"})
    policy = _object(policy, "policy", {"schema_version", "witnesses", "assessments"})
    if case["schema_version"] != "probity-case/v1" or policy["schema_version"] != "probity-policy/v1":
        raise CaseError("unsupported schema_version")
    case_id = _string(case["case_id"], "case_id")
    if not isinstance(case["artifacts"], dict) or not isinstance(policy["witnesses"], dict):
        raise CaseError("artifacts and witnesses: expected objects")
    if not isinstance(policy["assessments"], dict) or case_id not in policy["assessments"]:
        raise CaseError("case_id has no consumer assessment")
    assessment = _object(policy["assessments"][case_id], "assessment", {
        "claim_type", "budget_id", "budget_policy_witness", "price_table_witness", "trace_witness"})
    if assessment["claim_type"] != CLAIM_TYPE:
        raise CaseError("unsupported claim_type")
    budget_id = _text(assessment["budget_id"], "assessment.budget_id")
    pins, records = {}, {}
    seen_witnesses, seen_artifacts = set(), set()
    for role, field in (("budget_policy", "budget_policy_witness"),
                        ("price_table", "price_table_witness"), ("trace", "trace_witness")):
        identity = _string(assessment[field], field)
        if identity not in policy["witnesses"] or identity in seen_witnesses:
            raise CaseError("spend evidence needs three distinct consumer witnesses")
        seen_witnesses.add(identity)
        witness = _object(policy["witnesses"][identity], "witness." + identity,
                          {"artifact", "sha256", "authority"})
        artifact = _string(witness["artifact"], "witness.artifact")
        if artifact not in case["artifacts"] or artifact in seen_artifacts:
            raise CaseError("spend evidence needs three distinct artifact bindings")
        seen_artifacts.add(artifact)
        pinned = _digest(witness["sha256"], "witness.sha256")
        authority = _string(witness["authority"], "witness.authority")
        data, binding = _artifact(artifact_root, case["artifacts"][artifact], "artifacts." + artifact)
        records[role] = data if binding.get("sha256") == pinned else None
        pins[role] = {"id": identity, "artifact": artifact, "authority": authority,
                      "consumer_pinned_sha256": pinned, "binding": binding}
    result = {"schema_version": "probity-decision/v1", "case_id": case_id,
              "claim_type": CLAIM_TYPE, "scope": {"budget_id": budget_id, "witnesses": pins,
              "limit": "Checks pinned records. Host clock, prices, tool bounds and trace capture provenance need separate evidence."},
              "checks": []}

    def decide(decision: str, reason: str) -> dict:
        return {**result, "decision": decision, "reason": reason}

    for role, data in records.items():
        if data is None:
            return decide("not_established", role + "_unavailable_or_unbound")
    budget = _json(records["budget_policy"], "budget policy")
    prices = _json(records["price_table"], "price table")
    trace = _json(records["trace"], "trace")
    _validate(budget, prices, trace)
    replay = _Replay(budget, prices)
    try:
        _require(budget["budget_id"] == trace["budget_id"] == budget_id, "budget-scope-mismatch")
        _require(budget["prices_sha256"] == _hash(prices), "price-table-selection-mismatch")
        _require(trace["policy_sha256"] == _hash(budget), "trace-policy-selection-mismatch")
        _require(all(price["unit"] == budget["unit"] for price in prices), "price-unit-mismatch")
        for event in trace["events"]:
            replay.apply(event)
            result["checks"].append({"id": "event", "sequence": event["sequence"],
                                     "kind": event["kind"], "status": "met"})
    except _Mismatch as exc:
        result["checks"].append({"id": "spend_trace", "status": "failed",
                                 "sequence": replay.sequence})
        result["scope"]["last_verified_sequence"] = len(result["checks"]) - 1
        return decide("contradicted", str(exc).replace("-", "_"))
    result["scope"]["accounting"] = replay.accounting()
    if not trace["events"]:
        return decide("not_established", "spend_trace_empty")
    if replay.refund_due is not None:
        return decide("not_established", "settlement_refund_missing")
    return decide("supported", "spend_reservations_match_consumer_policy")
