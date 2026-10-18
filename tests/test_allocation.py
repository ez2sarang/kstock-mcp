import pytest

from kstock_mcp.allocation import ASSUMPTIONS, FORMULA, estimate_equal_allocation
from kstock_mcp.errors import KStockError


def test_known_case():
    r = estimate_equal_allocation(
        total_shares=1_000_000, offer_price=20_000, expected_subscribers=1_000_000,
        general_ratio_pct=25, equal_ratio_pct=50, min_subscription_shares=10, deposit_rate_pct=50,
    )["result"]
    assert r["general_subscriber_shares"] == 250_000
    assert r["equal_tranche_shares"] == 125_000
    assert r["proportional_tranche_shares"] == 125_000
    assert r["guaranteed_shares_per_subscriber"] == 0
    assert r["leftover_lottery_shares"] == 125_000
    assert r["chance_of_extra_share_pct"] == 12.5
    assert r["expected_shares_per_subscriber"] == 0.125
    assert r["expected_value_per_subscriber_krw"] == 2_500
    assert r["min_deposit_krw"] == 100_000


def test_guaranteed_shares_when_few_subscribers():
    r = estimate_equal_allocation(total_shares=400_000, offer_price=10_000, expected_subscribers=10_000)["result"]
    # general 100,000 -> equal 50,000 -> 5 each, no leftover
    assert r["guaranteed_shares_per_subscriber"] == 5
    assert r["leftover_lottery_shares"] == 0
    assert r["chance_of_extra_share_pct"] == 0


def test_leftover_split():
    r = estimate_equal_allocation(total_shares=40, offer_price=1_000, expected_subscribers=3,
                                  general_ratio_pct=100, equal_ratio_pct=100)["result"]
    assert (r["guaranteed_shares_per_subscriber"], r["leftover_lottery_shares"]) == (13, 1)
    assert r["chance_of_extra_share_pct"] == pytest.approx(33.33)


def test_response_states_formula_and_assumptions():
    out = estimate_equal_allocation(total_shares=1000, offer_price=1000, expected_subscribers=10)
    assert out["formula"] == FORMULA and out["assumptions"] == ASSUMPTIONS
    assert out["inputs"]["expected_subscribers"] == 10


@pytest.mark.parametrize(
    "kwargs",
    [
        {"total_shares": 0}, {"offer_price": 0}, {"expected_subscribers": 0},
        {"general_ratio_pct": 101}, {"equal_ratio_pct": -1}, {"deposit_rate_pct": 150},
        {"min_subscription_shares": 0},
    ],
)
def test_invalid_inputs(kwargs):
    base = {"total_shares": 1000, "offer_price": 1000, "expected_subscribers": 10}
    with pytest.raises(KStockError):
        estimate_equal_allocation(**{**base, **kwargs})
