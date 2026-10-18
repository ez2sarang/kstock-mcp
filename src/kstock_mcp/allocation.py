"""IPO equal-allocation (균등배정) estimator: a pure function with explicit formula and assumptions."""

from __future__ import annotations

from typing import Any

from .errors import KStockError

FORMULA = [
    "general_shares = floor(total_shares * general_ratio_pct / 100)",
    "equal_shares = floor(general_shares * equal_ratio_pct / 100)",
    "guaranteed_per_subscriber = floor(equal_shares / expected_subscribers)",
    "leftover_shares = equal_shares mod expected_subscribers  (given out by lottery, 1 share each)",
    "chance_of_extra_share_pct = leftover_shares / expected_subscribers * 100",
    "expected_shares_per_subscriber = equal_shares / expected_subscribers",
    "min_deposit_krw = min_subscription_shares * offer_price * deposit_rate_pct / 100",
]
ASSUMPTIONS = [
    "Every subscriber applies for at least the minimum lot, so all of them take part in the equal tranche.",
    "Leftover shares after the equal split are drawn by lottery in 1-share units.",
    "Employee-stock-ownership, institutional and other tranches are outside total_shares * general_ratio_pct.",
    "Real allocation is done per underwriter: use that broker's general-subscriber quota and its own expected "
    "subscriber count for a realistic figure. All inputs are estimates, not guarantees.",
]


def estimate_equal_allocation(
    *,
    total_shares: int,
    offer_price: int,
    expected_subscribers: int,
    general_ratio_pct: float = 25.0,
    equal_ratio_pct: float = 50.0,
    min_subscription_shares: int = 10,
    deposit_rate_pct: float = 50.0,
) -> dict[str, Any]:
    """Estimate shares received through the equal-allocation tranche of a Korean IPO.

    Args:
        total_shares: total public-offering shares of the tranche being modelled.
        offer_price: offer price per share in KRW.
        expected_subscribers: expected number of subscribers (accounts) in the equal tranche.
        general_ratio_pct: share of the offering allocated to general subscribers (%).
        equal_ratio_pct: share of the general-subscriber shares allocated equally (%).
        min_subscription_shares: minimum subscription lot (shares), used for the deposit estimate.
        deposit_rate_pct: subscription deposit rate (%), 50 for Korean IPOs.
    """
    if total_shares <= 0 or offer_price <= 0 or expected_subscribers <= 0 or min_subscription_shares <= 0:
        raise KStockError("total_shares, offer_price, expected_subscribers and min_subscription_shares must be positive.")
    for label, pct in (("general_ratio_pct", general_ratio_pct), ("equal_ratio_pct", equal_ratio_pct),
                       ("deposit_rate_pct", deposit_rate_pct)):
        if not 0 <= pct <= 100:
            raise KStockError(f"{label} must be between 0 and 100.")

    general_shares = int(total_shares * general_ratio_pct / 100)
    equal_shares = int(general_shares * equal_ratio_pct / 100)
    guaranteed, leftover = divmod(equal_shares, expected_subscribers)
    expected_shares = equal_shares / expected_subscribers
    return {
        "inputs": {
            "total_shares": total_shares,
            "offer_price": offer_price,
            "expected_subscribers": expected_subscribers,
            "general_ratio_pct": general_ratio_pct,
            "equal_ratio_pct": equal_ratio_pct,
            "min_subscription_shares": min_subscription_shares,
            "deposit_rate_pct": deposit_rate_pct,
        },
        "result": {
            "general_subscriber_shares": general_shares,
            "equal_tranche_shares": equal_shares,
            "proportional_tranche_shares": general_shares - equal_shares,
            "guaranteed_shares_per_subscriber": guaranteed,
            "leftover_lottery_shares": leftover,
            "chance_of_extra_share_pct": round(leftover / expected_subscribers * 100, 2),
            "expected_shares_per_subscriber": round(expected_shares, 4),
            "expected_value_per_subscriber_krw": round(expected_shares * offer_price),
            "min_deposit_krw": round(min_subscription_shares * offer_price * deposit_rate_pct / 100),
        },
        "formula": FORMULA,
        "assumptions": ASSUMPTIONS,
    }
