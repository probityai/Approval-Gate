"""Audit log: one JSON line per decision. Done for you; extend the fields if your policy needs more."""
import json
import os
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

# gate.py judge/try set GATE_LOG_DIR so evaluation runs never pollute your demo audit sample.
LOG = Path(os.getenv("GATE_LOG_DIR") or Path(__file__).parent) / "audit_log.jsonl"
_lock = threading.Lock()


def latest(limit=25):
    """Read the latest retained records with bounded memory and the writer lock."""
    with _lock:
        if not LOG.exists():
            return []
        with LOG.open() as handle:
            lines = deque(handle, maxlen=limit)
    return [json.loads(line) for line in lines if line.strip()]


def record(persona, text, context, decision):
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "persona": persona,
        "input": text,
        "context": context,
        "decision": decision.decision,
        "reason": decision.reason,
        "evidence": decision.evidence,
        "ezio": decision.ezio,
        "decision_packet": getattr(decision, "decision_packet", {}),
    }
    with _lock, LOG.open("a") as f:
        f.write(json.dumps(entry) + "\n")
