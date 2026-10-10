"""Exercise genuine kernel reuse, evidence reduction, and input refusal."""

from __future__ import annotations

import copy
import hashlib
import logging
import re
import sys
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "wrapper"))
import probity_bridge as bridge


class TestAdjudicateChecks:
    """Test the challenge adapter without executing any mock email action."""

    class TestPassingCases:
        """Accepted evidence preserves scope and authentic kernel bindings."""

        @pytest.mark.parametrize(
            ("statuses", "expected"),
            [
                ([], "not_established"),
                (["supported"], "supported"),
                (["supported", "supported"], "supported"),
                (["supported", "not_established"], "not_established"),
                (["not_established", "contradicted"], "contradicted"),
            ],
        )
        def test_reduction(self, statuses: list[str], expected: str) -> None:
            """Contradictions dominate missing support; no checks cannot prove a claim."""
            result = bridge.adjudicate_checks(
                "case", [{"status": item} for item in statuses], {}
            )
            assert result["decision"] == expected
            assert result["schema_version"] == "probity-decision/v1"
            assert result["claim_type"] == bridge.CLAIM_TYPE

        def test_preserves_inputs_and_exact_policy_binding(self) -> None:
            """Consumer bytes and provenance remain recoverable without caller mutation."""
            checks = [
                {
                    "status": "supported",
                    "source": "ezio_report",
                    "provenance": "producer_asserted",
                    "scope": {"claim": "literal"},
                }
            ]
            policy = {"version": 1, "threshold": 80}
            originals = copy.deepcopy((checks, policy))
            result = bridge.adjudicate_checks("case", checks, policy)
            effective = {
                **policy,
                "assessments": {"case": {"claim_type": bridge.CLAIM_TYPE}},
            }
            expected = hashlib.sha256(
                bridge._kernel.canonical_json(effective)
            ).hexdigest()
            assert result["policy_sha256"] == expected
            assert result["consumer_policy"] == effective
            assert result["checks"] == checks
            assert (checks, policy) == originals
            result["checks"][0]["scope"]["claim"] = "changed"
            assert checks[0]["scope"]["claim"] == "literal"
            result["consumer_policy"]["threshold"] = 99
            assert policy["threshold"] == 80

        @given(st.lists(st.sampled_from(["supported", "not_established"]), max_size=20))
        def test_adding_contradiction_always_contradicts(
            self, statuses: list[str]
        ) -> None:
            """A good aggregate score cannot override a supplied contradiction."""
            checks = [{"status": value} for value in statuses] + [
                {"status": "contradicted"}
            ]
            assert (
                bridge.adjudicate_checks("case", checks, {})["decision"]
                == "contradicted"
            )

        def test_original_adapter_registry_retained(self) -> None:
            """Event registration does not replace upstream adapter implementations."""
            assert {
                "brokered_file_write/v1",
                "source_text_coverage/v1",
                "operand_lineage/v1",
                "authority_anchor/v1",
                "event_absence/v1",
                "event_absence/v2",
                "spend_reservation/v1",
            } <= bridge._kernel.ADAPTERS.keys()
            assert bridge.verify_vendor_sources()["commit"] == bridge.UPSTREAM_COMMIT

        def test_consumer_policy_changes_binding(self) -> None:
            """A changed consumer requirement cannot retain the same decision-policy hash."""
            checks = [{"status": "supported"}]
            first = bridge.adjudicate_checks("case", checks, {"threshold": 80})
            second = bridge.adjudicate_checks("case", checks, {"threshold": 90})
            assert first["policy_sha256"] != second["policy_sha256"]

    class TestFailingCases:
        """Malformed records produce refusal rather than a misleading verdict."""

        @pytest.mark.parametrize(
            ("case_id", "checks", "policy", "message"),
            [
                ("", [], {}, "case_id must be a non-empty string"),
                ("   ", [], {}, "case_id must be a non-empty string"),
                (1, [], {}, "case_id must be a non-empty string"),
                ("case", {}, {}, "checks must be a JSON array"),
                ("case", [None], {}, "each check must be a JSON object"),
                (
                    "case",
                    [{}],
                    {},
                    "check status must be supported, contradicted, or not_established",
                ),
                (
                    "case",
                    [{"status": []}],
                    {},
                    "check status must be supported, contradicted, or not_established",
                ),
                (
                    "case",
                    [{"status": "GREEN"}],
                    {},
                    "check status must be supported, contradicted, or not_established",
                ),
                ("case", [], [], "policy must be a JSON object"),
                (
                    "case",
                    [],
                    {"assessments": []},
                    "consumer assessments must be a JSON object",
                ),
                (
                    "case",
                    [{"status": "supported", "value": float("nan")}],
                    {},
                    "checks and policy must contain finite JSON-compatible values",
                ),
                (
                    "case",
                    [],
                    {"threshold": float("inf")},
                    "checks and policy must contain finite JSON-compatible values",
                ),
            ],
        )
        def test_refusal_is_exact_and_logged(
            self,
            case_id: Any,
            checks: Any,
            policy: Any,
            message: str,
            caplog: pytest.LogCaptureFixture,
        ) -> None:
            """Return the expected refusal reason without exposing draft content."""
            with (
                caplog.at_level(logging.WARNING, logger=bridge.__name__),
                pytest.raises(bridge.CaseError, match=f"^{re.escape(message)}$"),
            ):
                bridge.adjudicate_checks(case_id, checks, policy)
            assert f"Probity bridge refused input: {message}" in caplog.text
