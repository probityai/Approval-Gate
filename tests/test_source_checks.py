"""Adversarial and generative tests of bounded company-data comparisons."""

from __future__ import annotations

import csv
import json
import logging
import sys
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "wrapper"))
from source_checks import BLOCK, HOLD, assess_source_of_truth, check_source_of_truth


@pytest.fixture(scope="module")
def sot() -> dict:
    """Load the authoritative files without consuming labelled practice cases."""
    folder = Path(__file__).resolve().parents[1] / "personas" / "customer_support"
    with (folder / "price_sheet.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    return {
        "price_sheet": rows,
        "policies": json.loads((folder / "policies.json").read_text()),
    }


CONTEXT = {"plan": "Pro", "customer_tenure_months": 18}


class TestSourceChecks:
    """Independent company facts must not be confused with global truth."""

    class TestPassingCases:
        @pytest.mark.parametrize(
            "text,context",
            [
                ("Thank you for your patience; I am looking into this.", {}),
                ("The GDPR provides rights concerning personal data.", {}),
                ("Pro costs $49 monthly.", CONTEXT),
                ("Pro costs USD 49 monthly.", CONTEXT),
                ("Starter is nineteen dollars per month.", {}),
                ("Business costs $79.20 per month on annual billing.", {}),
                ("The annual Pro price is $470.40 per year.", {}),
                ("You qualify for fifteen percent loyalty savings.", CONTEXT),
                ("Annual billing provides twenty percent off.", {}),
                ("Your loyalty discount is not 30%, but 15%.", CONTEXT),
                ("Pro is not $79 monthly; it is $49 monthly.", CONTEXT),
                ("We cannot combine your loyalty and annual discounts.", CONTEXT),
                (
                    "Monthly refunds cover the first payment within thirty days.",
                    CONTEXT,
                ),
                ("Annual refunds cover the first payment within two weeks.", CONTEXT),
                (
                    "Pro has a fourteen-day free trial with no credit card required.",
                    CONTEXT,
                ),
                ("Business offers a 14 day trial; a credit card isn't required.", {}),
                ("Starter has no free trial.", {}),
                ("The first response for Business is within four hours.", {}),
                ("Starter gets a first response within two days.", {}),
                ("We cannot promise a new feature next week.", CONTEXT),
            ],
        )
        def test_supported_or_uncheckable_text_does_not_refuse(
            self, sot, text, context
        ):
            assert check_source_of_truth(text, context, sot) == []

        @pytest.mark.parametrize(
            "plan,price", [("Starter", "19"), ("Pro", "49"), ("Business", "99")]
        )
        def test_exact_money_retains_supported_evidence(self, sot, plan, price):
            checks = assess_source_of_truth(f"{plan} costs ${price} monthly.", {}, sot)
            assert len(checks) == 1
            assert checks[0].status == "supported"
            assert checks[0].observed == checks[0].expected == price
            assert checks[0].source.startswith("price_sheet.csv:")

        @given(tenure=st.integers(min_value=12, max_value=1000))
        def test_loyalty_eligible_tenure_boundary(self, sot, tenure):
            assert (
                check_source_of_truth(
                    "Your 15% loyalty discount applies.",
                    {"plan": "Pro", "customer_tenure_months": tenure},
                    sot,
                )
                == []
            )

        def test_source_does_not_apply_customer_policy_to_other_persona(self):
            assert (
                check_source_of_truth("A stock will rise 10%.", {}, {"securities": []})
                == []
            )

        def test_debug_log_exposes_counts_not_customer_text(self, sot, caplog):
            with caplog.at_level(logging.DEBUG, logger="source_checks"):
                assess_source_of_truth(
                    "Sensitive greeting; Pro is $49 monthly.", {}, sot
                )
            assert "Company evidence checks:" in caplog.text
            assert "Sensitive greeting" not in caplog.text

    class TestFailingCases:
        @pytest.mark.parametrize(
            "text,context,level,source",
            [
                ("Pro costs $49.01 monthly.", CONTEXT, BLOCK, "price_sheet.csv"),
                ("Pro costs $49,999 monthly.", CONTEXT, BLOCK, "price_sheet.csv"),
                ("Enterprise costs $49 monthly.", CONTEXT, BLOCK, "enterprise.pricing"),
                (
                    "Business annual billing is $79 per month.",
                    {},
                    BLOCK,
                    "price_sheet.csv",
                ),
                (
                    "You receive thirty percent loyalty savings.",
                    CONTEXT,
                    BLOCK,
                    "discounts.loyalty.percent",
                ),
                (
                    "Your loyalty discount is not 15%.",
                    CONTEXT,
                    BLOCK,
                    "discounts.loyalty.percent",
                ),
                ("Pro does not cost $49 monthly.", CONTEXT, BLOCK, "price_sheet.csv"),
                (
                    "You qualify for 15% loyalty savings.",
                    {"plan": "Starter", "customer_tenure_months": 18},
                    BLOCK,
                    "applies_to",
                ),
                (
                    "You qualify for 15% loyalty savings.",
                    {"plan": "Business", "customer_tenure_months": 11},
                    BLOCK,
                    "min_tenure_months",
                ),
                (
                    "You qualify for the loyalty discount.",
                    {"plan": "Pro", "customer_tenure_months": 2},
                    BLOCK,
                    "min_tenure_months",
                ),
                (
                    "You qualify for 15% loyalty savings.",
                    {"plan": "Pro"},
                    HOLD,
                    "min_tenure_months",
                ),
                ("You qualify for 15% loyalty savings.", {}, HOLD, "applies_to"),
                (
                    "Combine annual and loyalty discounts.",
                    CONTEXT,
                    BLOCK,
                    "stacking_allowed",
                ),
                ("I'll offer you a 25% discount.", CONTEXT, BLOCK, "discounts"),
                (
                    "Your monthly refund window is 14 days.",
                    CONTEXT,
                    BLOCK,
                    "monthly_plans_days",
                ),
                (
                    "We refund the annual first payment within a year.",
                    CONTEXT,
                    BLOCK,
                    "annual_plans_days",
                ),
                (
                    "Monthly refunds cover every payment within 30 days.",
                    CONTEXT,
                    BLOCK,
                    "refunds.scope",
                ),
                ("You have a 30-day refund window.", CONTEXT, HOLD, "refunds"),
                ("Starter offers a 14-day trial.", {}, BLOCK, "free_trial.plans"),
                ("Pro has a 30-day free trial.", {}, BLOCK, "free_trial.days"),
                ("The Pro trial requires a credit card.", {}, BLOCK, "card_required"),
                ("Business has no trial.", {}, BLOCK, "free_trial.plans"),
                (
                    "Business costs $99 monthly and gets a response within 24 hours.",
                    {},
                    BLOCK,
                    "first_response_hours",
                ),
                (
                    "We will ship the requested feature next month.",
                    CONTEXT,
                    HOLD,
                    "agent_authority",
                ),
                (
                    "I'll make an exception for this request.",
                    CONTEXT,
                    HOLD,
                    "agent_authority",
                ),
            ],
        )
        def test_refusal_has_scoped_evidence(self, sot, text, context, level, source):
            findings = check_source_of_truth(text, context, sot)
            matching = [finding for finding in findings if source in finding.source]
            assert matching, findings
            assert matching[0].level == level
            assert matching[0].status == (
                "contradicted" if level == BLOCK else "not_established"
            )
            assert matching[0].claim
            assert matching[0].message

        @given(
            price=st.integers(min_value=1, max_value=100000).filter(
                lambda value: value != 4900
            )
        )
        def test_wrong_price_cents_always_contradict(self, sot, price):
            text = f"Pro costs ${price // 100}.{price % 100:02d} monthly."
            findings = check_source_of_truth(text, CONTEXT, sot)
            assert any(finding.level == BLOCK for finding in findings)

        @given(tenure=st.integers(min_value=0, max_value=11))
        def test_loyalty_tenure_below_minimum(self, sot, tenure):
            findings = check_source_of_truth(
                "You get a fifteen percent loyalty discount.",
                {"plan": "Business", "customer_tenure_months": tenure},
                sot,
            )
            assert any(
                finding.level == BLOCK and "min_tenure_months" in finding.source
                for finding in findings
            )

        @pytest.mark.parametrize(
            "tenure", [True, False, "18", None, float("inf"), float("nan")]
        )
        def test_invalid_tenure_never_establishes_eligibility(self, sot, tenure):
            findings = check_source_of_truth(
                "Your 15% loyalty discount applies.",
                {"plan": "Pro", "customer_tenure_months": tenure},
                sot,
            )
            assert any(
                finding.level == HOLD and "min_tenure_months" in finding.source
                for finding in findings
            )
