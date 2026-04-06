import numpy as np
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.pricing import bs_greeks_vec, inverse_greeks
from src.cash_greeks import cash_greeks_usd, cash_greeks_btc, perp_cash_greeks, aggregate_cash_greeks


def test_cash_delta_usd_atm_call():
    """ATM call: cash delta ≈ 0.6 * S * size."""
    S = 95000
    g = bs_greeks_vec(S, 95000, 0.1, 0.0, 0.0, 0.6, True)
    cash = cash_greeks_usd(float(g["delta"]), float(g["gamma"]),
                           float(g["vega"]), float(g["theta"]), S, 1.0)
    # Delta ~0.55, so cash_delta ~52k
    assert 30000 < cash["cash_delta_usd"] < 70000


def test_btc_cash_delta_differs_from_naive():
    """BTC cash delta should use inverse Greeks, not just USD / S."""
    S = 95000
    std_g = bs_greeks_vec(S, 95000, 0.1, 0.0, 0.0, 0.6, True)
    inv_g = inverse_greeks(S, 95000, 0.1, 0.0, 0.0, 0.6, True, std_g)

    usd_cash = cash_greeks_usd(float(std_g["delta"]), float(std_g["gamma"]),
                                float(std_g["vega"]), float(std_g["theta"]), S, 1.0)
    btc_cash = cash_greeks_btc(float(inv_g["delta_btc"]), float(inv_g["gamma_btc"]),
                                float(inv_g["vega_btc"]), float(inv_g["theta_btc"]), S, 1.0)

    # BTC cash delta * S should NOT equal USD cash delta (inverse adjustment)
    naive_btc_to_usd = btc_cash["cash_delta_btc"] * S
    assert abs(naive_btc_to_usd - usd_cash["cash_delta_usd"]) > 1.0, \
        "BTC and USD cash Greeks should differ due to inverse adjustment"


def test_perp_cash_greeks_long():
    """Long perp: delta = size * price, all others zero."""
    cash = perp_cash_greeks("long", 2.0, 95000)
    assert cash["cash_delta_usd"] == 2.0 * 95000
    assert cash["cash_gamma_usd"] == 0.0
    assert cash["cash_delta_btc"] == 2.0


def test_perp_cash_greeks_short():
    """Short perp: delta negative."""
    cash = perp_cash_greeks("short", 1.5, 95000)
    assert cash["cash_delta_usd"] == -1.5 * 95000


def test_aggregate():
    """Aggregation sums across positions."""
    p1 = {"cash_delta_usd": 100, "cash_gamma_usd": 10, "cash_vega_usd": 5, "cash_theta_usd": -2,
           "cash_delta_btc": 1, "cash_gamma_btc": 0.1, "cash_vega_btc": 0.05, "cash_theta_btc": -0.02}
    p2 = {"cash_delta_usd": -50, "cash_gamma_usd": 20, "cash_vega_usd": 3, "cash_theta_usd": -1,
           "cash_delta_btc": -0.5, "cash_gamma_btc": 0.2, "cash_vega_btc": 0.03, "cash_theta_btc": -0.01}
    totals = aggregate_cash_greeks([p1, p2])
    assert totals["cash_delta_usd"] == 50
    assert totals["cash_gamma_usd"] == 30


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
