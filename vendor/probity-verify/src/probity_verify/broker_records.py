"""Authenticate the bounded Observer v0 wire profile without importing its code.

These signatures authenticate selected records under consumer-held public keys.
They do not authenticate key custody, a host, a clock, or an arbitrary effect.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from pathlib import PurePosixPath
from typing import Any

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .common import CaseError, _digest, _instant, _object, _string
from .json_input import parse_json

MAX_RECORD_BYTES = 1024 * 1024
MAX_ENTRIES = 512
MAX_DEPTH = 32
MAX_NODES = 32768
GENESIS = "0" * 64
NONCLAIMS = ["reads", "transient-writes", "file-modes", "network-effects",
             "host-operator-independence"]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CaseError(message)


def profile(value: Any) -> None:
    """Bound parsed work and enforce exact restricted JSON types iteratively."""
    pending = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        require(nodes <= MAX_NODES and depth <= MAX_DEPTH, "record structure exceeds budget")
        if item is None or type(item) is bool:
            continue
        if type(item) is int:
            require(abs(item) < 2**53, "record integer exceeds safe range")
        elif type(item) is str:
            require(item.isascii(), "record string must be ASCII")
        elif type(item) is list:
            require(len(item) + len(pending) <= MAX_NODES, "record structure exceeds budget")
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is dict:
            require(len(item) * 2 + len(pending) <= MAX_NODES, "record structure exceeds budget")
            for key, child in item.items():
                require(type(key) is str, "record member name must be string")
                pending.extend(((key, depth + 1), (child, depth + 1)))
        else:
            raise CaseError("value is outside restricted record profile")


def canonical(value: Any) -> bytes:
    profile(value)
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("ascii")
    require(len(raw) <= MAX_RECORD_BYTES, "record exceeds byte budget")
    return raw


def record_digest(domain: str, value: Any) -> str:
    return hashlib.sha256(domain.encode("ascii") + b"\x00" + canonical(value)).hexdigest()


def parse_record(raw: bytes, *, canonical_required: bool = True) -> Any:
    require(len(raw) <= MAX_RECORD_BYTES, "record exceeds byte budget")
    def invalid_number(value):
        raise CaseError("record number must be a safe integer")

    value = parse_json(raw, max_bytes=MAX_RECORD_BYTES, max_depth=MAX_DEPTH,
                       max_nodes=MAX_NODES, parse_float=invalid_number, ascii_only=True)
    profile(value)
    if canonical_required:
        require(canonical(value) == raw, "record JSON is not canonical")
    return value


def identifier(value: Any, label: str) -> str:
    result = _string(value, label)
    require(result.isascii() and len(result) <= 128
            and all(33 <= ord(char) <= 126 for char in result), f"{label}: invalid identifier")
    return result


def absolute_path(value: Any, label: str) -> str:
    result = _string(value, label)
    require(result.isascii() and len(result) <= 1024 and result.startswith("/")
            and result == str(PurePosixPath(result)) and "\\" not in result
            and all(33 <= ord(char) <= 126 for char in result)
            and all(part not in {"", ".", ".."} for part in result[1:].split("/")),
            f"{label}: expected normalized absolute path")
    return result


def count(value: Any, label: str, minimum: int = 0) -> int:
    require(type(value) is int and minimum <= value <= MAX_ENTRIES,
            f"{label}: expected bounded integer")
    return value


def signature(key: str, domain: str, payload: Any, encoded: Any) -> None:
    _digest(key, "public key")
    require(type(encoded) is str and len(encoded) == 88, "invalid signature encoding")
    try:
        raw = base64.b64decode(encoded, validate=True)
        require(len(raw) == 64 and base64.b64encode(raw).decode("ascii") == encoded,
                "invalid signature encoding")
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(key)).verify(
            raw, domain.encode("ascii") + b"\x00" + canonical(payload))
    except (ValueError, binascii.Error, InvalidSignature, UnsupportedAlgorithm) as exc:
        raise CaseError("signature does not verify under consumer key") from exc


def history(raw: bytes) -> list[dict]:
    require(len(raw) <= MAX_RECORD_BYTES, "history exceeds byte budget")
    require(not raw or raw.endswith(b"\n"), "history ends with incomplete line")
    require(raw.count(b"\n") <= MAX_ENTRIES, "history exceeds entry budget")
    entries = []
    previous = GENESIS
    for index, line in enumerate(raw.split(b"\n")[:-1], 1):
        entry = _object(parse_record(line), "history entry", {"sequence", "previous", "event", "hash"})
        require(type(entry["sequence"]) is int and entry["sequence"] == index
                and entry["previous"] == previous, "history sequence or predecessor differs")
        _digest(entry["previous"], "history previous")
        _digest(entry["hash"], "history hash")
        require(entry["hash"] == record_digest("probity-history-entry-v0",
                {key: entry[key] for key in ("sequence", "previous", "event")}),
                "history digest differs")
        previous = entry["hash"]
        entries.append(entry)
    return entries


def commitment(record: Any, observer: str, authority: dict) -> dict:
    value = _object(record, "commitment", {"preimage", "committedAt", "keyid", "signature"})
    preimage = _object(value["preimage"], "commitment.preimage",
                       {"authorityDigest", "beforeRoot", "intervalId", "witnessNonce"})
    for field in ("authorityDigest", "beforeRoot", "witnessNonce"):
        _digest(preimage[field], "commitment." + field)
    identifier(preimage["intervalId"], "commitment.intervalId")
    _instant(value["committedAt"], "commitment.committedAt")
    require(value["keyid"] == observer and preimage["intervalId"] == authority["intervalId"]
            and preimage["authorityDigest"] == record_digest("probity-authority-v0", authority),
            "commitment authority or key differs")
    signature(observer, "probity-prior-commitment-v0",
              {key: value[key] for key in ("preimage", "committedAt")}, value["signature"])
    return value


def checkpoint(value: Any, witness: str, entries: list[dict] | None = None) -> dict:
    record = _object(value, "checkpoint", {"count", "head", "keyid", "signature"}, {"ledgerReceipt"})
    count(record["count"], "checkpoint.count", 1)
    _digest(record["head"], "checkpoint.head")
    require(record["keyid"] == witness, "checkpoint key differs")
    payload = {key: record[key] for key in ("count", "head")}
    signature(witness, "probity-checkpoint-v0", payload, record["signature"])
    if entries is not None:
        require(record["count"] == len(entries) and entries
                and record["head"] == entries[-1]["hash"], "checkpoint history binding differs")
    return record


def receipt_record(value: Any, witness: str) -> dict:
    receipt = _object(value, "receipt", {"sequence", "previous", "intervalId", "authorityDigest",
                       "observerKey", "phase", "checkpoint", "hash", "keyid", "signature"})
    count(receipt["sequence"], "receipt.sequence", 1)
    for field in ("previous", "authorityDigest", "observerKey", "hash"):
        _digest(receipt[field], "receipt." + field)
    identifier(receipt["intervalId"], "receipt.intervalId")
    require(type(receipt["phase"]) is str and receipt["phase"] in {"begin", "terminal"}, "unknown receipt phase")
    body = {key: receipt[key] for key in ("sequence", "previous", "intervalId",
            "authorityDigest", "observerKey", "phase", "checkpoint")}
    require(receipt["hash"] == record_digest("probity-witness-receipt-v0", body)
            and receipt["keyid"] == witness, "receipt hash or key differs")
    signature(witness, "probity-witness-receipt-v0", body, receipt["signature"])
    point = _object(receipt["checkpoint"], "receipt.checkpoint", {"count", "head", "keyid", "signature"})
    checkpoint(point, witness)
    require(point["count"] == 1 if receipt["phase"] == "begin" else point["count"] > 1,
            "receipt checkpoint has wrong phase count")
    return receipt


def ledger_head(raw: bytes, witness: str) -> dict:
    head = _object(parse_record(raw), "ledger head", {"format", "count", "head", "keyid", "signature"})
    count(head["count"], "ledger head.count")
    _digest(head["head"], "ledger head.head")
    require(head["count"] != 0 or head["head"] == GENESIS, "empty ledger head differs from genesis")
    require(head["format"] == "probity-witness-ledger-head-v0" and head["keyid"] == witness,
            "ledger head format or key differs")
    signature(witness, "probity-witness-ledger-head-v0",
              {key: head[key] for key in ("format", "count", "head")}, head["signature"])
    return head


def verify_ledger(raw: bytes, head_raw: bytes | None, witness: str) -> list[dict]:
    require(len(raw) <= MAX_RECORD_BYTES and (not raw or raw.endswith(b"\n"))
            and raw.count(b"\n") <= MAX_ENTRIES, "invalid or oversized witness ledger")
    receipts = []
    beginnings = {}
    terminals = set()
    for index, line in enumerate(raw.split(b"\n")[:-1], 1):
        receipt = receipt_record(parse_record(line), witness)
        require(receipt["sequence"] == index
                and receipt["previous"] == (receipts[-1]["hash"] if receipts else GENESIS),
                "receipt sequence or predecessor differs")
        interval = receipt["intervalId"]
        if receipt["phase"] == "begin":
            require(interval not in beginnings, "receipt repeats opening")
            beginnings[interval] = receipt
        else:
            require(interval in beginnings and interval not in terminals, "receipt has no unique opening")
            opening = beginnings[interval]
            require((receipt["authorityDigest"], receipt["observerKey"])
                    == (opening["authorityDigest"], opening["observerKey"]), "receipt terminal differs from opening")
            terminals.add(interval)
        receipts.append(receipt)
    if head_raw is not None:
        head = ledger_head(head_raw, witness)
        require(head["count"] <= len(receipts) and head["head"]
                == (receipts[head["count"] - 1]["hash"] if head["count"] else GENESIS),
                "ledger does not extend retained head")
    return receipts


def events(entries: list[dict], prior: dict, scope: str, *, sealed: bool) -> tuple[list[dict], list[str]]:
    require(entries and type(entries[0]["event"]) is dict and entries[0]["event"].get("kind") == "begin", "history has no opening")
    begin = _object(entries[0]["event"], "begin", {"kind", "commitment", "commitmentDigest"})
    require(begin["commitment"] == prior and begin["commitmentDigest"] == record_digest(
            "probity-prior-commitment-v0", {key: prior[key] for key in ("preimage", "committedAt")}),
            "opening commitment differs")
    current = prior["preimage"]["beforeRoot"]
    writes, gaps = [], []
    pending = None
    requests = set()
    completed = {}
    for position, entry in enumerate(entries[1:], 1):
        event = entry["event"]
        require(type(event) is dict and type(event.get("kind")) is str, "invalid history event")
        kind = event["kind"]
        if pending is not None:
            require(kind == "write", "event inside pending intent")
        if kind in {"write-intent", "write"}:
            fields = {"kind", "requestId", "path", "contentDigest", "beforeRoot"}
            _object(event, kind, fields | ({"afterRoot"} if kind == "write" else set()))
            request = identifier(event["requestId"], kind + ".requestId")
            path = absolute_path(event["path"], kind + ".path")
            require(path.startswith(scope + "/"), "write lies outside authority scope")
            _digest(event["contentDigest"], kind + ".contentDigest")
            _digest(event["beforeRoot"], kind + ".beforeRoot")
            require(event["beforeRoot"] == current, "write root chain differs")
            if kind == "write-intent":
                require(request not in requests, "repeated write intent")
                requests.add(request)
                pending = event
            else:
                require(pending is not None and request not in completed
                        and all(event[key] == pending[key] for key in
                                ("requestId", "path", "contentDigest", "beforeRoot")),
                        "write differs from unique durable intent")
                current = _digest(event["afterRoot"], "write.afterRoot")
                pending = None
                completed[request] = path
                writes.append(event)
        elif kind == "retry":
            _object(event, "retry", {"kind", "requestId", "path"})
            identifier(event["requestId"], "retry.requestId")
            absolute_path(event["path"], "retry.path")
            require(completed.get(event["requestId"]) == event["path"], "retry has no matching completion")
        elif kind == "denied":
            _object(event, "denied", {"kind", "requestId", "path", "reason"})
            for key in ("requestId", "path", "reason"):
                _string(event[key], "denied." + key)
        elif kind == "gap":
            _object(event, "gap", {"kind", "reason"})
            reason = _string(event["reason"], "gap.reason")
            require(reason not in gaps, "duplicate gap reason")
            gaps.append(reason)
        elif kind == "seal":
            _object(event, "seal", {"kind", "claimDigest"})
            _digest(event["claimDigest"], "seal.claimDigest")
            require(position == len(entries) - 1, "seal is not final event")
        else:
            raise CaseError("unknown or repeated history boundary")
    if sealed:
        require(pending is None and entries[-1]["event"]["kind"] == "seal", "sealed history lacks terminal")
    return writes, gaps


def packet(record: Any, entries: list[dict] | None, observer: str, witness: str,
           receipts: list[dict] | None, *, ledger_selected: bool) -> dict:
    value = _object(record, "packet", {"authority", "commitment", "startCheckpoint", "claim",
                    "claimKeyid", "claimSignature", "checkpoint"}, {"authorizationBinding"})
    authority = _object(value["authority"], "authority", {"intervalId", "scope", "operation"})
    identifier(authority["intervalId"], "authority.intervalId")
    scope = absolute_path(authority["scope"], "authority.scope")
    require(authority["operation"] == "write-file", "unsupported authority operation")
    prior = commitment(value["commitment"], observer, authority)
    claim = _object(value["claim"], "claim", {"format", "intervalId", "authorityDigest", "beforeRoot",
                    "afterRoot", "writes", "coverage", "witnessScope", "doesNotAssert", "sealedAt"})
    require(claim["format"] == "probity-observer-prototype-v0" and claim["witnessScope"] == "PEER"
            and claim["doesNotAssert"] == NONCLAIMS and value["claimKeyid"] == observer,
            "unsupported claim profile or key")
    require(claim["intervalId"] == authority["intervalId"]
            and claim["authorityDigest"] == prior["preimage"]["authorityDigest"]
            and claim["beforeRoot"] == prior["preimage"]["beforeRoot"], "claim authority binding differs")
    _digest(claim["afterRoot"], "claim.afterRoot")
    require(_instant(claim["sealedAt"], "claim.sealedAt") >= _instant(prior["committedAt"], "committedAt"),
            "claim sealed before commitment")
    signature(observer, "probity-claim-v0", claim, value["claimSignature"])
    require(type(claim["writes"]) is list and len(claim["writes"]) <= MAX_ENTRIES,
            "invalid claim write population")
    writes = claim["writes"]
    current = claim["beforeRoot"]
    requests = set()
    for write in writes:
        _object(write, "claim write", {"kind", "requestId", "path", "contentDigest", "beforeRoot", "afterRoot"})
        request = identifier(write["requestId"], "claim write.requestId")
        path = absolute_path(write["path"], "claim write.path")
        require(write["kind"] == "write" and request not in requests and path.startswith(scope + "/")
                and write["beforeRoot"] == current, "invalid signed write chain")
        _digest(write["contentDigest"], "claim write.contentDigest")
        current = _digest(write["afterRoot"], "claim write.afterRoot")
        requests.add(request)
    require(claim["afterRoot"] == current, "claim after root differs")
    coverage = _object(claim["coverage"], "coverage", {"scope", "observedPopulation", "noDetectedGap",
                                                       "knownGaps", "unmediatedEffects"})
    require(type(coverage["knownGaps"]) is list and len(coverage["knownGaps"]) <= MAX_ENTRIES,
            "invalid gap population")
    gaps = coverage["knownGaps"]
    for gap in gaps:
        _string(gap, "gap reason")
    require(len(set(gaps)) == len(gaps) and coverage["scope"] == scope and coverage["observedPopulation"]
            == "broker-write-calls-with-valid-request-id" and coverage["unmediatedEffects"] == "not-established"
            and type(coverage["noDetectedGap"]) is bool and coverage["noDetectedGap"] == (not gaps),
            "unsupported coverage declaration")
    for phase, field in (("begin", "startCheckpoint"), ("terminal", "checkpoint")):
        prefix = (entries[:1] if phase == "begin" else entries) if entries is not None else None
        point = checkpoint(value[field], witness, prefix)
        require(("ledgerReceipt" in point) == ledger_selected, "checkpoint ledger selection differs")
        if ledger_selected:
            if receipts is not None:
                candidate = canonical(point["ledgerReceipt"])
                receipt = next((item for item in receipts if canonical(item) == candidate), None)
                require(receipt is not None, "checkpoint receipt lacks exact selected ledger inclusion")
            else:
                receipt = receipt_record(point["ledgerReceipt"], witness)
            require(receipt["phase"] == phase and receipt["intervalId"] == authority["intervalId"]
                    and receipt["authorityDigest"] == claim["authorityDigest"]
                    and receipt["observerKey"] == observer
                    and receipt["checkpoint"] == {key: point[key] for key in
                                                   ("count", "head", "keyid", "signature")},
                    "checkpoint receipt role or content differs")
    if entries is not None:
        actual_writes, actual_gaps = events(entries, prior, scope, sealed=True)
        require(writes == actual_writes and gaps == actual_gaps
                and entries[-1]["event"]["claimDigest"] == record_digest("probity-claim-v0", claim),
                "claim does not match actual history")
    if "authorizationBinding" in value:
        binding = _object(value["authorizationBinding"], "authorizationBinding", {"payload", "keyid", "signature"})
        payload = _object(binding["payload"], "authorizationBinding.payload", {"profile", "request",
                          "authorizedAt", "claimDigest", "grantDigest", "intervalId"})
        request = _object(payload["request"], "authorizationBinding.request", {"run_id", "attempt_id",
                         "request_id", "tenant_id", "principal_id", "tool_id", "target_path", "content_sha256"})
        for key in ("run_id", "attempt_id", "request_id", "tenant_id", "principal_id", "tool_id"):
            identifier(request[key], "authorizationBinding.request." + key)
        absolute_path(request["target_path"], "authorizationBinding.target_path")
        _digest(request["content_sha256"], "authorizationBinding.content_sha256")
        _digest(payload["grantDigest"], "authorizationBinding.grantDigest")
        require(binding["keyid"] == observer and payload["profile"] == "probity-protected-action-reference-v0"
                and payload["claimDigest"] == record_digest("probity-claim-v0", claim)
                and payload["intervalId"] == request["run_id"] == authority["intervalId"]
                and len(writes) == 1 and (writes[0]["requestId"], writes[0]["path"], writes[0]["contentDigest"])
                == (request["request_id"], request["target_path"], request["content_sha256"]),
                "authorization reference differs from recorded completion")
        require(_instant(prior["committedAt"], "committedAt")
                <= _instant(payload["authorizedAt"], "authorizationBinding.authorizedAt")
                <= _instant(claim["sealedAt"], "sealedAt"), "authorization reference time outside interval")
        signature(observer, "probity-protected-action-binding-v0", payload, binding["signature"])
    return value
