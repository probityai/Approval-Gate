"""Bounded customer-support fact checks against retained company data.

This module extracts recognizable pricing and policy claims from an email. It
does not authenticate company data, understand arbitrary prose, or adjudicate
public-knowledge claims. Unrecognized prose remains the responsibility of Ezio
and the consumer's broader decision policy. Structured checks retain both
positive evidence and the reasons for a refusal.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

LOGGER = logging.getLogger(__name__)
BLOCK = "BLOCK"
HOLD = "HOLD_FOR_REVIEW"
ONES = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
TENS = ["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
NUMBER_WORDS = {word: Decimal(index) for index, word in enumerate(ONES)}
NUMBER_WORDS.update(
    {word: Decimal((index + 2) * 10) for index, word in enumerate(TENS)}
)
WORD_NUMBER = (
    "(?:"
    + "|".join(NUMBER_WORDS)
    + ")(?:[ -](?:one|two|three|four|five|six|seven|eight|nine))?"
)
DIGITS = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
NUMBER = rf"(?:{DIGITS}|{WORD_NUMBER})"
PERCENT = re.compile(
    rf"\b(?P<number>{NUMBER})\s*(?:%|percent\b|per cent\b)", re.IGNORECASE
)
DAYS = re.compile(rf"\b(?P<number>{NUMBER})[ -]*(?:days?\b)", re.IGNORECASE)
HOURS = re.compile(rf"\b(?P<number>{NUMBER})[ -]*(?:hours?\b|hrs?\b)", re.IGNORECASE)
MONEY = re.compile(
    rf"(?:\$\s*(?P<dollar>{DIGITS})(?!\d)|\b(?P<usd>{NUMBER})\s*(?:USD|dollars?)\b|\bUSD\s*(?P<prefix>{DIGITS}))",
    re.IGNORECASE,
)
WINDOW = re.compile(
    rf"\b(?P<number>{NUMBER}|a|an|one|first)[ -]*(?P<unit>days?|weeks?|months?|years?)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SourceCheck:
    """A scoped company-data comparison, including successful comparisons.

    Attributes
    ----------
    status : str
        ``supported``, ``contradicted``, or ``not_established``. These statuses
        describe the comparison, not an overall trust score or task outcome.
    claim, source : str
        Extracted claim and the company-data field used for comparison.
    observed, expected : object
        JSON-compatible representations of the extracted and policy values.
    message : str
        Human-readable reason sufficient to review the comparison.
    """

    status: str
    claim: str
    source: str
    observed: Any = None
    expected: Any = None
    message: str = ""


@dataclass(frozen=True)
class Finding:
    """A blocking or review finding derived from a :class:`SourceCheck`."""

    level: str
    message: str
    claim: str = ""
    source: str = ""
    observed: Any = None
    expected: Any = None
    status: str = ""


def _number(value: str) -> Decimal:
    """Parse numeric digits or an English integer through ninety-nine exactly."""
    try:
        return Decimal(value.replace(",", ""))
    except InvalidOperation:
        return sum(
            (NUMBER_WORDS[word] for word in re.split(r"[ -]+", value.lower())),
            Decimal(0),
        )


def _display(value: Any) -> Any:
    """Preserve JSON-compatible evidence without floating-point conversion."""
    return str(value) if isinstance(value, Decimal) else value


def _comparison(
    claim: str, source: str, observed: Any, expected: Any, label: str
) -> SourceCheck:
    """Create an exact supported or contradicted comparison."""
    status = "supported" if observed == expected else "contradicted"
    message = f"{label}: email states {_display(observed)}; company data requires {_display(expected)}."
    return SourceCheck(
        status, claim, source, _display(observed), _display(expected), message
    )


def _unknown(claim: str, source: str, message: str) -> SourceCheck:
    """Create a review obligation when necessary context cannot be established."""
    return SourceCheck("not_established", claim, source, None, None, message)


def _sentences(text: str) -> list[str]:
    """Split independent sentences while retaining decimal monetary amounts."""
    return [
        part.strip()
        for part in re.split(r"(?<!\d)[.!?](?!\d)|[;\n]", text)
        if part.strip()
    ]


def _negated(sentence: str, start: int) -> bool:
    """Recognize immediate negation and prohibited/hypothetical claim prefixes.

    Negation is intentionally bounded to the local clause; a prior ``not`` must
    not erase a subsequent positive claim after ``but`` or a comma.
    """
    prefix = re.split(r",|\bbut\b|\binstead\b", sentence[:start], flags=re.IGNORECASE)[
        -1
    ]
    return bool(
        re.search(
            r"\b(?:not|never|cannot|can't|isn't|aren't|won't|don't|doesn't)\b[^,:]{0,45}$",
            prefix,
            re.IGNORECASE,
        )
    )


def _assertion_comparison(
    claim: str, position: int, source: str, observed: Any, expected: Any, label: str
) -> SourceCheck:
    """Invert the comparison when the email explicitly denies a stated value."""
    if not _negated(claim, position):
        return _comparison(claim, source, observed, expected, label)
    status = "contradicted" if observed == expected else "supported"
    return SourceCheck(
        status,
        claim,
        source,
        {"denied": _display(observed)},
        _display(expected),
        f"{label}: email denies {_display(observed)}; company data states {_display(expected)}.",
    )


def _plan(
    sentence: str, position: int, context: dict[str, Any], rows: list[dict[str, Any]]
) -> str | None:
    """Select the preceding plan in a proposition, or its first following plan."""
    names = [str(row["plan"]) for row in rows] + ["Enterprise"]
    mentions = [
        (match.start(), name)
        for name in names
        for match in re.finditer(rf"\b{re.escape(name)}\b", sentence, re.IGNORECASE)
    ]
    if mentions:
        preceding = [mention for mention in mentions if mention[0] <= position]
        return (max(preceding) if preceding else min(mentions))[1]
    return next(
        (
            name
            for name in names
            if name.lower() == str(context.get("plan", "")).lower()
        ),
        None,
    )


def _row(plan: str | None, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Find a selected plan without inventing a default plan."""
    return next((row for row in rows if row["plan"] == plan), None)


def _billing(sentence: str, position: int, context: dict[str, Any]) -> str | None:
    """Select nearest explicit billing term or retained billing context."""
    mentions = [
        (abs(match.start() - position), match.group().lower())
        for match in re.finditer(
            r"\b(?:annual|annually|yearly|monthly)\b", sentence, re.IGNORECASE
        )
    ]
    if mentions:
        return "annual" if min(mentions)[1] != "monthly" else "monthly"
    value = str(context.get("billing", context.get("billing_cycle", ""))).lower()
    return (
        "annual"
        if value in {"annual", "yearly"}
        else "monthly"
        if value == "monthly"
        else None
    )


def _price_scopes(sentence: str, rows: list[dict[str, Any]]) -> list[str]:
    """Keep coordinated price propositions separate from their qualifiers."""
    plans = "|".join(re.escape(str(row["plan"])) for row in rows)
    discount_subject = r"(?:annual|yearly)\s+billing\s+(?:takes|saves|provides|gives|cuts|reduces)\b"
    subject = rf"(?:{plans}|Enterprise)\b|(?:\$|USD\b|{NUMBER}\s+dollars?\b)|{discount_subject}"
    boundary = rf"(?:,\s*(?:(?:and|or|but)\s+)?|\s+(?:and|or|but)\s+)(?={subject})"
    return [part.strip() for part in re.split(boundary, sentence, flags=re.IGNORECASE) if part.strip()]


def _unsupported_money(sentence: str) -> bool:
    """Refuse signed, scaled, or partially parsed currency tokens."""
    token = r"(?P<token>[+-]?[0-9][0-9A-Za-z_.,+-]*)"
    scale = r"(?:\s+(?P<scale>thousand|million|billion|k|m|bn)\b)?"
    patterns = (
        rf"(?P<sign>[+-]?)(?:\$\s*|\bUSD\s*){token}{scale}",
        rf"(?P<sign>){token}{scale}\s+(?:USD|dollars?)\b",
    )
    return any(
        match.group("sign") or match.group("scale")
        or not re.fullmatch(DIGITS, match.group("token").rstrip(".,"))
        for pattern in patterns
        for match in re.finditer(pattern, sentence, re.IGNORECASE)
    )


def _prices(
    sentence: str, context: dict[str, Any], sot: dict[str, Any]
) -> list[SourceCheck]:
    """Check stated USD plan prices, including annual totals and monthly rates."""
    checks = []
    rows = sot["price_sheet"]
    scopes = _price_scopes(sentence, rows)
    if len(scopes) > 1:
        offset = 0
        for scope in scopes:
            offset = sentence.index(scope, offset)
            scoped_context = {**context, "plan": _plan(sentence, offset, context, rows)}
            checks.extend(_prices(scope, scoped_context, sot))
            offset += len(scope)
        return checks
    if not re.search(
        r"\b(?:plan|price|costs?|pricing|monthly|annually)\b|(?:per|/)\s*(?:mo\b|month|year)",
        sentence,
        re.IGNORECASE,
    ):
        return checks
    if _unsupported_money(sentence):
        return [_unknown(sentence, "price_sheet.csv", "The stated price uses unsupported amount notation; review its complete value.")]
    for match in MONEY.finditer(sentence):
        plan = _plan(sentence, match.start(), context, rows)
        observed = _number(
            match.group("dollar") or match.group("usd") or match.group("prefix")
        )
        if plan == "Enterprise":
            checks.append(
                _assertion_comparison(
                    sentence,
                    match.start(),
                    "policies.json:enterprise.pricing",
                    observed,
                    "custom",
                    "Enterprise published pricing requires a custom sales quote",
                )
            )
            continue
        row = _row(plan, rows)
        if row is None:
            checks.append(
                _unknown(
                    sentence,
                    "price_sheet.csv",
                    "A plan price was stated without a supported plan; review the price scope.",
                )
            )
            continue
        billing = _billing(sentence, match.start(), context) or "monthly"
        field = (
            "annual_price_per_month_usd" if billing == "annual" else "monthly_price_usd"
        )
        expected = Decimal(str(row[field]))
        if re.search(
            r"(?:per|a|each|/)\s*(?:year|annum)\b|\bannual total\b",
            sentence,
            re.IGNORECASE,
        ):
            expected *= 12
        checks.append(
            _assertion_comparison(
                sentence,
                match.start(),
                f"price_sheet.csv:{plan}.{field}",
                observed,
                expected,
                f"{plan} price ({billing} billing)",
            )
        )
    return checks


def _discount_name(sentence: str, position: int) -> str | None:
    """Associate a percentage with the nearest policy discount label."""
    labels = {"loyalty": r"\bloyalty\b", "annual_billing": r"\b(?:annual|yearly)\b"}
    mentions = [
        (abs(match.start() - position), name)
        for name, pattern in labels.items()
        for match in re.finditer(pattern, sentence, re.IGNORECASE)
    ]
    return min(mentions)[1] if mentions else None


def _eligibility(
    sentence: str, name: str, context: dict[str, Any], sot: dict[str, Any]
) -> list[SourceCheck]:
    """Check a claimed customer offer against plan and minimum-tenure rules."""
    rule = sot["policies"]["discounts"][name]
    plan = context.get("plan")
    if plan is None:
        return [
            _unknown(
                sentence,
                f"policies.json:discounts.{name}.applies_to",
                "Discount eligibility requires the customer's plan in context.",
            )
        ]
    checks = [
        _comparison(
            sentence,
            f"policies.json:discounts.{name}.applies_to",
            str(plan).lower() in {p.lower() for p in rule["applies_to"]},
            True,
            f"{name} plan eligibility",
        )
    ]
    minimum = rule.get("min_tenure_months")
    if minimum is None:
        return checks
    tenure = context.get("customer_tenure_months", context.get("tenure_months"))
    if (
        isinstance(tenure, bool)
        or not isinstance(tenure, (int, float))
        or isinstance(tenure, float)
        and not math.isfinite(tenure)
    ):
        return checks + [
            _unknown(
                sentence,
                f"policies.json:discounts.{name}.min_tenure_months",
                "Loyalty eligibility requires numeric customer tenure in context.",
            )
        ]
    return checks + [
        _comparison(
            sentence,
            f"policies.json:discounts.{name}.min_tenure_months",
            tenure >= minimum,
            True,
            f"Loyalty requires at least {minimum} months; customer has {tenure}",
        )
    ]


def _is_customer_offer(sentence: str) -> bool:
    """Distinguish applying an offer from describing its general policy."""
    return bool(
        re.search(
            r"\b(?:you|your|qualif|eligible|apply|applies|receive|get|offer)\w*\b",
            sentence,
            re.IGNORECASE,
        )
    )


def _unclassified_discount(
    sentence: str, match: re.Match[str], sot: dict[str, Any]
) -> list[SourceCheck]:
    """Require a published rate and establish which offer the customer receives."""
    if not re.search(r"\b(?:discount|off|save|saving)\b", sentence, re.IGNORECASE):
        return []
    allowed = {
        Decimal(str(rule["percent"]))
        for rule in sot["policies"]["discounts"].values()
        if isinstance(rule, dict)
    }
    value = _number(match.group("number"))
    if _negated(sentence, match.start()) and value not in allowed:
        return [
            SourceCheck(
                "supported",
                sentence,
                "policies.json:discounts",
                {"denied": str(value)},
                sorted(str(n) for n in allowed),
                "The email denies an unpublished discount rate.",
            )
        ]
    if value not in allowed:
        return [
            SourceCheck(
                "contradicted",
                sentence,
                "policies.json:discounts",
                str(value),
                sorted(str(n) for n in allowed),
                "The offered discount is outside the published discount policy.",
            )
        ]
    return [
        _unknown(
            sentence,
            "policies.json:discounts",
            "A discount is promised without a policy category; a manager must review it.",
        )
    ]


def _named_discount(
    sentence: str, match: re.Match[str], context: dict[str, Any], sot: dict[str, Any]
) -> list[SourceCheck]:
    """Compare one asserted percentage and its customer-offer eligibility."""
    name = _discount_name(sentence, match.start())
    if name is None:
        return _unclassified_discount(sentence, match, sot)
    expected = Decimal(str(sot["policies"]["discounts"][name]["percent"]))
    checks = [
        _assertion_comparison(
            sentence,
            match.start(),
            f"policies.json:discounts.{name}.percent",
            _number(match.group("number")),
            expected,
            f"{name} discount percent",
        )
    ]
    offer = _is_customer_offer(sentence) and not _negated(sentence, match.start())
    return checks + (_eligibility(sentence, name, context, sot) if offer else [])


def _discounts(
    sentence: str, context: dict[str, Any], sot: dict[str, Any]
) -> list[SourceCheck]:
    """Compare discount percentages, customer eligibility, and stacking claims."""
    checks = []
    if re.search(
        r"\b(?:combin\w*|stack\w*|together|both discounts|additional discount)\b",
        sentence,
        re.IGNORECASE,
    ) and not _negated(sentence, len(sentence)):
        checks.append(
            _comparison(
                sentence,
                "policies.json:discounts.stacking_allowed",
                True,
                sot["policies"]["discounts"]["stacking_allowed"],
                "Combining discounts",
            )
        )
    for match in PERCENT.finditer(sentence):
        checks.extend(_named_discount(sentence, match, context, sot))
    if not list(PERCENT.finditer(sentence)) and _is_customer_offer(sentence):
        name = _discount_name(sentence, 0)
        if (
            name
            and "discount" in sentence.lower()
            and not _negated(sentence, len(sentence))
        ):
            checks.extend(_eligibility(sentence, name, context, sot))
    return checks


def _refunds(
    sentence: str, context: dict[str, Any], sot: dict[str, Any]
) -> list[SourceCheck]:
    """Check refund windows and explicitly broadened payment scopes."""
    if not re.search(r"\brefund\w*\b|money[ -]back", sentence, re.IGNORECASE):
        return []
    checks = []
    policy = sot["policies"]["refunds"]
    for match in WINDOW.finditer(sentence):
        billing = _billing(sentence, match.start(), context)
        value = match.group("number")
        observed = (
            Decimal(1) if value.lower() in {"a", "an", "first"} else _number(value)
        )
        observed *= next(
            multiplier
            for unit, multiplier in (
                ("day", 1),
                ("week", 7),
                ("month", 30),
                ("year", 365),
            )
            if match.group("unit").lower().startswith(unit)
        )
        if billing is None:
            if _negated(sentence, match.start()):
                continue
            legal = {
                Decimal(str(policy["monthly_plans_days"])),
                Decimal(str(policy["annual_plans_days"])),
            }
            status = "not_established" if observed in legal else "contradicted"
            checks.append(
                SourceCheck(
                    status,
                    sentence,
                    "policies.json:refunds",
                    str(observed),
                    sorted(str(n) for n in legal),
                    "Refund windows depend on billing: monthly 30 days, annual 14 days.",
                )
            )
            continue
        field = f"{billing}_plans_days"
        checks.append(
            _assertion_comparison(
                sentence,
                match.start(),
                f"policies.json:refunds.{field}",
                observed,
                Decimal(str(policy[field])),
                f"{billing} refund window in days",
            )
        )
    scope = re.search(
        r"\b(?:every|any|all|each|subsequent)\s+(?:payment|purchase|charge)|\brenewals?\b",
        sentence,
        re.IGNORECASE,
    )
    if scope and not _negated(sentence, scope.start()):
        checks.append(
            _comparison(
                sentence,
                "policies.json:refunds.scope",
                "all payments",
                policy["scope"],
                "Refund payment scope",
            )
        )
    return checks


def _trials(
    sentence: str, context: dict[str, Any], sot: dict[str, Any]
) -> list[SourceCheck]:
    """Check trial length, eligible plans, and credit-card requirements."""
    if not re.search(r"\btrials?\b", sentence, re.IGNORECASE):
        return []
    policy = sot["policies"]["free_trial"]
    checks = []
    for match in DAYS.finditer(sentence):
        checks.append(
            _assertion_comparison(
                sentence,
                match.start(),
                "policies.json:free_trial.days",
                _number(match.group("number")),
                Decimal(str(policy["days"])),
                "Trial length in days",
            )
        )
    plan = _plan(sentence, 0, context, sot["price_sheet"])
    denied_trial = bool(
        re.search(
            r"\b(?:no|not|without)\s+(?:a\s+)?(?:free\s+)?trial|\btrial\s+(?:is\s+)?(?:unavailable|not available)",
            sentence,
            re.IGNORECASE,
        )
    )
    if plan:
        checks.append(
            _comparison(
                sentence,
                "policies.json:free_trial.plans",
                not denied_trial,
                plan in policy["plans"],
                f"{plan} free-trial availability",
            )
        )
    if re.search(r"\b(?:credit\s+)?card\b", sentence, re.IGNORECASE):
        no_card = bool(
            re.search(
                r"\b(?:no|without)\s+(?:a\s+)?(?:credit\s+)?card|\bcard\s+(?:(?:is\s+)?not required|isn't required|optional)|\b(?:do not|don't)\s+(?:need|require)\s+(?:a\s+)?(?:credit\s+)?card",
                sentence,
                re.IGNORECASE,
            )
        )
        if no_card or re.search(
            r"\b(?:require\w*|need\w*|must)\b", sentence, re.IGNORECASE
        ):
            checks.append(
                _comparison(
                    sentence,
                    "policies.json:free_trial.card_required",
                    not no_card,
                    policy["card_required"],
                    "Trial card requirement",
                )
            )
    return checks


def _responses(
    sentence: str, context: dict[str, Any], sot: dict[str, Any]
) -> list[SourceCheck]:
    """Check first-response commitments without equating them to resolutions."""
    if not re.search(r"\b(?:respond|response|reply)\w*\b", sentence, re.IGNORECASE):
        return []
    checks = []
    matches = list(HOURS.finditer(sentence)) + list(DAYS.finditer(sentence))
    for match in matches:
        plan = _plan(sentence, match.start(), context, sot["price_sheet"])
        row = _row(plan, sot["price_sheet"])
        if row is None:
            checks.append(
                _unknown(
                    sentence,
                    "price_sheet.csv:first_response_hours",
                    "A response time requires a selected plan.",
                )
            )
        else:
            observed = _number(match.group("number")) * (
                24 if re.search(r"days?", match.group(), re.IGNORECASE) else 1
            )
            checks.append(
                _assertion_comparison(
                    sentence,
                    match.start(),
                    f"price_sheet.csv:{plan}.first_response_hours",
                    observed,
                    Decimal(str(row["first_response_hours"])),
                    f"{plan} first-response hours",
                )
            )
    return checks


def _authority(
    sentence: str, context: dict[str, Any], sot: dict[str, Any]
) -> list[SourceCheck]:
    """Hold feature, completion, and exception promises clause by clause."""
    clauses = [part.strip() for part in re.split(r",|\bbut\b|\band\s+(?=(?:we|I)\b|(?:promise|guarantee)\b)", sentence, flags=re.IGNORECASE) if part.strip()]
    if len(clauses) > 1:
        return [check for clause in clauses for check in _authority(clause, context, sot)]
    del context, sot
    referral = re.search(
        r"\b(?:ask|check|consult|escalate|contact|confirm)\b.*\b(?:whether|if)\b",
        sentence, re.IGNORECASE,
    )
    if referral and not re.search(r"\b(?:promise|guarantee|guaranteed|definitely|certainly)\b", sentence, re.IGNORECASE):
        return []
    promise = re.search(
        r"\b(?:we(?:'ll| will)|I(?:'ll| will)|will|promise|guarantee|guaranteed|definitely|ensure|certainly)\b",
        sentence,
        re.IGNORECASE,
    )
    restricted = re.search(
        r"\b(?:feature|roadmap|ship|launch|deliver|delivery|release|exception|waiv\w*|custom integration|integrat\w*|implement|add|build|ready|available|complete\w*|finish\w*|live|working|work)\b",
        sentence,
        re.IGNORECASE,
    )
    explicit = re.search(
        r"\b(?:make|grant|offer)\s+(?:an?\s+)?exception|\b(?:feature|release|delivery)\b.*\b(?:will|tomorrow|next (?:week|month)|by (?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday))\b",
        sentence,
        re.IGNORECASE,
    )
    if not ((promise and restricted) or explicit):
        return []
    if (promise and _negated(sentence, promise.start())) or _negated(sentence, restricted.start() if restricted else len(sentence)):
        return []
    return [
        _unknown(
            sentence,
            "policies.json:agent_authority",
            "A feature, delivery, or exception promise requires manager authorization.",
        )
    ]


def assess_source_of_truth(
    text: str, context: dict[str, Any], sot: dict[str, Any]
) -> list[SourceCheck]:
    """Extract bounded company claims and retain their comparisons.

    Parameters
    ----------
    text : str
        Proposed customer email. Numbers and contextual labels are extracted;
        arbitrary semantic equivalence is outside this implementation's scope.
    context : dict
        Trusted customer metadata, including ``plan``,
        ``customer_tenure_months``, and optional ``billing``/``billing_cycle``.
        An assertion inside the email does not replace customer eligibility.
    sot : dict
        Loaded files keyed by stem: ``policies`` and ``price_sheet``. These
        files are assumed to be selected by the caller's trust policy.

    Returns
    -------
    list of SourceCheck
        Independent comparisons, including supported evidence. Missing the
        customer-support source files returns no checks, allowing another
        persona to use its own policy instead of misapplying this checker.

    See Also
    --------
    check_source_of_truth : Convert negative comparisons to gate findings.
    """
    if "policies" not in sot or "price_sheet" not in sot:
        return []
    handlers = (_prices, _discounts, _refunds, _trials, _responses, _authority)
    checks = [
        check
        for sentence in _sentences(text)
        for handler in handlers
        for check in handler(sentence, context, sot)
    ]
    LOGGER.debug(
        "Company evidence checks: %s",
        {
            status: sum(check.status == status for check in checks)
            for status in ("supported", "contradicted", "not_established")
        },
    )
    return checks


def check_source_of_truth(
    text: str, context: dict[str, Any], sot: dict[str, Any]
) -> list[Finding]:
    """Return blocking and review findings from company-data comparisons.

    Parameters
    ----------
    text, context, sot
        See :func:`assess_source_of_truth` for input semantics and limitations.

    Returns
    -------
    list of Finding
        Contradictions become ``BLOCK``; necessary unresolved evidence becomes
        ``HOLD_FOR_REVIEW``. Supported checks do not cause refusal. An email
        with no recognized company claim returns an empty list.
    """
    return [
        Finding(
            BLOCK if check.status == "contradicted" else HOLD,
            check.message,
            check.claim,
            check.source,
            check.observed,
            check.expected,
            check.status,
        )
        for check in assess_source_of_truth(text, context, sot)
        if check.status != "supported"
    ]
