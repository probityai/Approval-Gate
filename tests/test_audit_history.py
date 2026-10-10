"""Retained history reads must remain bounded and expose read failures."""

import importlib
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "wrapper"))
audit = importlib.import_module("audit")
serve = importlib.import_module("serve")


def test_history_keeps_latest_records_and_original_input(tmp_path, monkeypatch):
    """Read the last records in write order without discarding retained inputs."""
    monkeypatch.setattr(audit, "LOG", tmp_path / "audit.jsonl")
    assert audit.latest() == []
    decision = SimpleNamespace(
        decision="HOLD_FOR_REVIEW", reason="Missing authority", evidence=[], ezio=None,
        decision_packet={"policy": "test"},
    )
    for index in range(30):
        audit.record("customer_support", f"Draft {index}", {"case": index}, decision)
    entries = audit.latest()
    assert len(entries) == 25
    assert entries[0]["input"] == "Draft 5"
    assert entries[-1]["context"] == {"case": 29}
    assert entries[-1]["decision_packet"] == {"policy": "test"}


@pytest.mark.parametrize("corrupt", [False, True])
def test_http_history_distinguishes_records_from_corruption(tmp_path, monkeypatch, corrupt):
    """A corrupt history cannot silently appear as a successful empty history."""
    path = tmp_path / "audit.jsonl"
    path.write_text("not JSON\n" if corrupt else '{"input":"retained draft"}\n')
    monkeypatch.setattr(audit, "LOG", path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/api/audit"
        if corrupt:
            with pytest.raises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(url, timeout=3)
            assert caught.value.code == 503
            caught.value.close()
        else:
            with urllib.request.urlopen(url, timeout=3) as response:
                assert json.load(response) == {"entries": [{"input": "retained draft"}]}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
