"""Refuse nonfinite customer metadata before deciding or executing actions."""

from __future__ import annotations

import importlib
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "wrapper"))
serve = importlib.import_module("serve")


class TestRequestBoundary:
    """Keep invalid context from reaching the action gate."""

    class TestFailingCases:
        @pytest.mark.parametrize("number", ["NaN", "Infinity", "1e999"])
        def test_nonfinite_metadata_is_refused(self, number: str) -> None:
            """Return a client error without calling either decision or action."""
            server = ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            body = (
                '{"text":"Thank you.","persona":"customer_support",'
                '"context":{"customer_tenure_months":' + number + "}}"
            ).encode()
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/decide",
                data=body,
                headers={"Content-Type": "application/json"},
            )
            try:
                with (
                    patch.object(serve, "decide") as decide,
                    patch.object(serve.mock_actions, "run_for_persona") as action,
                    pytest.raises(urllib.error.HTTPError) as caught,
                ):
                    urllib.request.urlopen(request, timeout=3)
                assert caught.value.code == 400
                caught.value.close()
                decide.assert_not_called()
                action.assert_not_called()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)
