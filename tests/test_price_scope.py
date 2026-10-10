"""Keep amounts attached to their own plan and billing proposition."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "wrapper"))
from source_checks import assess_source_of_truth

from wrapper import load_source_of_truth


@pytest.mark.parametrize(
    "text",
    [
        "Starter costs $19 and Pro costs $49 monthly.",
        "Starter costs $19, Pro costs $49 monthly.",
        "The Pro plan costs $49 monthly, or $39.20 per month with annual billing.",
        "The Pro plan costs $39.20 per month and is billed annually.",
        "Pro costs $39.20 per month, billed annually, and Business costs $99 monthly.",
        "Starter costs nineteen dollars and Pro costs forty-nine dollars monthly.",
        "Pro costs $49 monthly or $470.40 per year on annual billing.",
        "Pro costs $49/mo, and annual billing provides 20% off.",
        "Starter costs $19 per month and yearly billing saves 20%.",
        "Pro is $49/mo with a first response within 24 hours, and yearly billing cuts 20% off.",
    ],
)
def test_coordinated_correct_prices_do_not_cross_bind(text):
    """A later plan or billing qualifier cannot change an earlier price."""
    checks = assess_source_of_truth(text, {}, load_source_of_truth("customer_support"))
    assert checks
    assert all(check.status == "supported" for check in checks)


@pytest.mark.parametrize(
    "text",
    [
        "Starter costs $49 and Pro costs $49 monthly.",
        "The Pro plan costs $49 monthly, or $49 per month with annual billing.",
        "Pro costs $39.20 per month on annual billing and Business costs $49 monthly.",
        "Pro costs $48/mo, and annual billing provides 20% off.",
        "Starter costs $19 per month and yearly billing saves 30%.",
    ],
)
def test_contradiction_survives_a_correct_neighbor(text):
    """One supported proposition cannot erase another price contradiction."""
    checks = assess_source_of_truth(text, {}, load_source_of_truth("customer_support"))
    assert any(check.status == "contradicted" for check in checks)


@pytest.mark.parametrize("amount", ["$49e3", "$49k", "$49_000", "$49 million", "$-49", "-$49", "USD +49", "49k USD", "49 million dollars"])
def test_unparsed_amount_is_not_supported_by_a_valid_prefix(amount):
    """A price token must be checked completely before it can be supported."""
    checks = assess_source_of_truth(f"Pro costs {amount} monthly.", {}, load_source_of_truth("customer_support"))
    assert any(check.status == "not_established" for check in checks)
