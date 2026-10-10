"""Adversarial tests for the consumer policy, report contract, and failure path.

These cases are authored from the business rules and public report schema. They
never read the practice answer labels or the sealed final judging set.
"""

from __future__ import annotations

import copy
import importlib
import logging
import sys
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "wrapper"))
gate = importlib.import_module("wrapper")


def report(
    *, status: str = "not_scored", verdicts: tuple[str, ...] = ()
) -> dict[str, Any]:
    """Build a small report conforming to the published mock response schema.

    Parameters
    ----------
    status : str
        Accuracy service status. ``not_scored`` represents no extracted claims.
    verdicts : tuple of str
        Claim-level producer assertions; these are not independent witnesses.

    Returns
    -------
    dict of str to Any
        A complete report with a deliberately favorable top-line score, so
        tests exercise the lower-level signals rather than relying on tier.
    """
    return {
        "verdict": {"tier": "GREEN", "score": 95, "summary": "Producer says reliable."},
        "dimensions": {
            "accuracy": {
                "status": status,
                "score": 100 if status == "scored" else None,
                "claims_total": len(verdicts),
                "claims_verified": verdicts.count("supported"),
                "claims_contradicted": verdicts.count("contradicted"),
            },
            "tone": {"score": 95},
            "bias": {"score": 95},
            "agreement": {"score": 95},
        },
        "claims": [
            {"text": f"Public assertion {index}.", "verdict": verdict, "source": None}
            for index, verdict in enumerate(verdicts)
        ],
        "findings": [],
    }


def policy_for(
    raw: dict[str, Any], findings: list[Any] | None = None
) -> tuple[list[str], str]:
    """Apply the actual configured policy to a valid report and optional findings."""
    return gate.apply_policy(gate.parse_signals(raw), findings or [], gate.POLICY)


class TestDecisionPolicy:
    """Keep supported behavior and adversarial refusal cases visibly separate."""

    class TestPassingCases:
        def test_claim_free_message_can_approve(self) -> None:
            reasons, decision = policy_for(report())
            assert decision == gate.APPROVE
            assert reasons

        def test_supported_public_claim_needs_no_company_fact(self) -> None:
            _, decision = policy_for(report(status="scored", verdicts=("supported",)))
            assert decision == gate.APPROVE

        @pytest.mark.parametrize("dimension", ["tone", "bias", "agreement"])
        def test_dimension_threshold_is_inclusive(self, dimension: str) -> None:
            raw = report()
            raw["dimensions"][dimension]["score"] = gate.POLICY["dimension_minimums"][
                dimension
            ]["hold_below"]
            assert policy_for(raw)[1] == gate.APPROVE

        @given(
            score=st.floats(
                min_value=70, max_value=100, allow_nan=False, allow_infinity=False
            )
        )
        def test_valid_high_dimension_scores_preserve_claim_free_approval(
            self, score: float
        ) -> None:
            raw = report()
            for dimension in ("tone", "bias", "agreement"):
                raw["dimensions"][dimension]["score"] = score
            assert policy_for(raw)[1] == gate.APPROVE

        def test_local_block_outranks_all_holds(self) -> None:
            raw = report(status="unavailable")
            raw["dimensions"]["tone"]["score"] = 10
            findings = [
                gate.Finding(gate.HOLD, "Manager approval required."),
                gate.Finding(gate.BLOCK, "Company discount contradicts policy."),
            ]
            reasons, decision = policy_for(raw, findings)
            assert decision == gate.BLOCK
            assert "Company discount contradicts policy." in reasons

        @given(order=st.permutations([gate.BLOCK, gate.HOLD, gate.HOLD]))
        def test_finding_order_cannot_weaken_a_block(self, order: list[str]) -> None:
            findings = [
                gate.Finding(level, f"Finding {index}.")
                for index, level in enumerate(order)
            ]
            assert policy_for(report(), findings)[1] == gate.BLOCK

    class TestFailingCases:
        @pytest.mark.parametrize(
            "verdicts", [("contradicted",), ("supported", "contradicted")]
        )
        def test_green_tier_cannot_override_contradicted_claims(
            self, verdicts: tuple[str, ...]
        ) -> None:
            reasons, decision = policy_for(report(status="scored", verdicts=verdicts))
            assert decision == gate.BLOCK
            assert any("contradict" in reason.lower() for reason in reasons)

        def test_unresolved_claim_requires_review(self) -> None:
            assert (
                policy_for(report(status="scored", verdicts=("unverifiable",)))[1]
                == gate.HOLD
            )

        def test_unavailable_accuracy_requires_review(self) -> None:
            assert policy_for(report(status="unavailable"))[1] == gate.HOLD

        @pytest.mark.parametrize("dimension", ["tone", "bias", "agreement"])
        def test_low_dimension_holds_despite_green_tier(self, dimension: str) -> None:
            raw = report()
            raw["dimensions"][dimension]["score"] = 69
            reasons, decision = policy_for(raw)
            assert decision == gate.HOLD
            assert any(dimension in reason for reason in reasons)

        @pytest.mark.parametrize(
            "path,value",
            [
                (("verdict", "score"), True),
                (("verdict", "score"), "95"),
                (("verdict", "score"), -1),
                (("verdict", "score"), 101),
                pytest.param(
                    ("verdict", "score"), 10**400, id="oversized-integer-score"
                ),
                (("verdict", "score"), float("nan")),
                (("verdict", "score"), float("inf")),
                (("verdict", "tier"), "PURPLE"),
                (("verdict", "summary"), {}),
                (("dimensions", "tone", "score"), None),
                (("dimensions", "accuracy", "status"), "unknown"),
                (("dimensions", "accuracy", "claims_total"), -1),
                (("dimensions", "accuracy", "claims_total"), True),
                (("dimensions", "accuracy", "claims_total"), 0.5),
                (("dimensions", "accuracy", "claims_verified"), "0"),
                (("dimensions", "accuracy", "claims_contradicted"), -1),
                (("dimensions", "accuracy", "claims_verified"), 1),
            ],
        )
        def test_malformed_report_is_rejected_with_audit_warning(
            self,
            path: tuple[str, ...],
            value: Any,
            caplog: pytest.LogCaptureFixture,
        ) -> None:
            raw = report()
            parent = raw
            for key in path[:-1]:
                parent = parent[key]
            parent[path[-1]] = value
            with (
                caplog.at_level(logging.WARNING),
                pytest.raises(
                    gate.EzioError, match="^malformed mock response$"
                ) as caught,
            ):
                gate.parse_signals(raw)
            assert caught.value.response is raw
            assert any(
                record.message.startswith("Ezio response rejected: ")
                for record in caplog.records
            )

        @pytest.mark.parametrize(
            "claim",
            [
                None,
                {},
                {"text": "Claim", "verdict": "green"},
                {"text": 42, "verdict": "supported"},
            ],
        )
        def test_malformed_claim_record_is_rejected(self, claim: Any) -> None:
            raw = report(status="scored", verdicts=("supported",))
            raw["claims"] = [claim]
            with pytest.raises(gate.EzioError, match="^malformed mock response$"):
                gate.parse_signals(raw)

        def test_claim_counts_must_agree_with_records(self) -> None:
            raw = report(status="scored", verdicts=("contradicted",))
            raw["dimensions"]["accuracy"].update(
                claims_contradicted=0, claims_verified=1
            )
            with pytest.raises(gate.EzioError, match="^malformed mock response$"):
                gate.parse_signals(raw)

        @pytest.mark.parametrize("status", ["not_scored", "unavailable"])
        def test_unscored_report_cannot_smuggle_claim_records(
            self, status: str
        ) -> None:
            raw = report(status=status, verdicts=("contradicted",))
            with pytest.raises(gate.EzioError, match="^malformed mock response$"):
                gate.parse_signals(raw)

        @given(value=st.one_of(st.integers(max_value=-1), st.integers(min_value=101)))
        def test_out_of_range_scores_never_parse(self, value: int) -> None:
            raw = report()
            raw["dimensions"]["agreement"]["score"] = value
            with pytest.raises(gate.EzioError, match="^malformed mock response$"):
                gate.parse_signals(raw)

        @pytest.mark.parametrize(
            "failure", ["TimeoutError", "HTTP 503", "request budget exhausted"]
        )
        def test_service_failure_holds_and_preserves_error(
            self,
            failure: str,
            monkeypatch: pytest.MonkeyPatch,
        ) -> None:
            def unavailable(*args: Any, **kwargs: Any) -> None:
                raise gate.EzioError(failure, {"error": failure})

            monkeypatch.setattr(gate, "analyze", unavailable)
            result = gate.decide("Thank you for contacting us.", audit_log=False)
            assert result.decision == gate.HOLD
            assert result.reason == f"Ezio unavailable: {failure}"
            assert result.ezio["response"] == {"error": failure}

        @pytest.mark.parametrize(
            "failure",
            ["missing_directory", "missing_required_files", "malformed_policies"],
        )
        def test_unavailable_company_sources_do_not_approve(
            self,
            failure: str,
            tmp_path: Path,
            monkeypatch: pytest.MonkeyPatch,
        ) -> None:
            selected = tmp_path / "customer_support"
            if failure != "missing_directory":
                selected.mkdir()
            if failure == "malformed_policies":
                (selected / "policies.json").write_text("{broken", encoding="utf-8")
            monkeypatch.setattr(gate, "PERSONAS", tmp_path)
            monkeypatch.setattr(gate, "analyze", lambda *args, **kwargs: report())
            result = gate.decide("Your loyalty discount is 30%.", audit_log=False)
            assert result.decision == gate.HOLD
            assert "source" in result.reason.lower()

        def test_company_contradiction_remains_block_during_outage(
            self, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            def unavailable(*args: Any, **kwargs: Any) -> None:
                raise gate.EzioError("HTTP 503", {"error": "unavailable"})

            monkeypatch.setattr(gate, "analyze", unavailable)
            monkeypatch.setattr(
                gate,
                "check_source_of_truth",
                lambda *args, **kwargs: [
                    gate.Finding(gate.BLOCK, "Company discount contradicts policy.")
                ],
            )
            result = gate.decide("A fictional email.", audit_log=False)
            assert result.decision == gate.BLOCK
            assert "Company discount contradicts policy." in result.reason
            assert "Ezio unavailable: HTTP 503" in result.reason

        def test_malformed_report_does_not_become_an_approval(
            self, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            raw = report()
            raw["verdict"]["score"] = float("nan")
            monkeypatch.setattr(gate, "analyze", lambda *args, **kwargs: raw)
            result = gate.decide("Thank you for contacting us.", audit_log=False)
            assert result.decision == gate.HOLD
            assert result.reason == "Ezio unavailable: malformed mock response"

        def test_report_checks_are_labelled_producer_asserted(
            self, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            raw = report(status="scored", verdicts=("supported",))
            monkeypatch.setattr(
                gate, "analyze", lambda *args, **kwargs: copy.deepcopy(raw)
            )
            result = gate.decide("Thank you for contacting us.", audit_log=False)
            checks = result.decision_packet["checks"]
            ezio_checks = [
                check for check in checks if check["source"] == "ezio_report"
            ]
            assert ezio_checks
            assert all(
                check["provenance"] == "producer_asserted" for check in ezio_checks
            )
