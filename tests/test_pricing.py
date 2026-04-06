import numpy as np
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.pricing import bs_price_vec, bs_greeks_vec, inverse_greeks, implied_vol, implied_vol_vec


def test_bs_call_price_known():
    """BS call: S=100, K=100, T=1, r=0.05, sigma=0.2 → ~10.4506"""
    price = float(bs_price_vec(100, 100, 1.0, 0.05, 0.0, 0.2, True))
    assert abs(price - 10.4506) < 0.01, f"Expected ~10.4506, got {price}"


def test_bs_put_price_known():
    """BS put via put-call parity: P = C - S + K*e^(-rT)"""
    call = float(bs_price_vec(100, 100, 1.0, 0.05, 0.0, 0.2, True))
    put = float(bs_price_vec(100, 100, 1.0, 0.05, 0.0, 0.2, False))
    parity_put = call - 100 + 100 * np.exp(-0.05)
    assert abs(put - parity_put) < 1e-8, f"Put-call parity violated: put={put}, parity={parity_put}"


def test_bs_vectorized():
    """Vectorized computation matches scalar."""
    S = np.array([100, 100])
    K = np.array([100, 110])
    prices = bs_price_vec(S, K, 1.0, 0.05, 0.0, 0.2, np.array([True, True]))
    assert len(prices) == 2
    assert prices[0] > prices[1]  # ATM call > OTM call


def test_greeks_delta_call_atm():
    """ATM call delta should be ~0.5 (slightly above due to drift)."""
    greeks = bs_greeks_vec(100, 100, 1.0, 0.05, 0.0, 0.2, True)
    delta = float(greeks["delta"])
    assert 0.5 < delta < 0.7, f"ATM call delta={delta}, expected ~0.6"


def test_greeks_delta_put():
    """Put delta = call delta - 1 (for q=0)."""
    call_g = bs_greeks_vec(100, 100, 1.0, 0.05, 0.0, 0.2, True)
    put_g = bs_greeks_vec(100, 100, 1.0, 0.05, 0.0, 0.2, False)
    assert abs(float(put_g["delta"]) - (float(call_g["delta"]) - 1.0)) < 1e-8


def test_greeks_gamma_same():
    """Gamma is same for call and put."""
    call_g = bs_greeks_vec(100, 100, 1.0, 0.05, 0.0, 0.2, True)
    put_g = bs_greeks_vec(100, 100, 1.0, 0.05, 0.0, 0.2, False)
    assert abs(float(call_g["gamma"]) - float(put_g["gamma"])) < 1e-10


def test_greeks_vega_positive():
    """Vega should be positive for all options."""
    g = bs_greeks_vec(100, 100, 1.0, 0.05, 0.0, 0.2, True)
    assert float(g["vega"]) > 0


def test_greeks_theta_negative_call():
    """Call theta should be negative (time decay)."""
    g = bs_greeks_vec(100, 100, 1.0, 0.05, 0.0, 0.2, True)
    assert float(g["theta"]) < 0


def test_inverse_delta_differs():
    """Inverse delta should differ from standard delta / S."""
    S, K, T, r, q, sigma = 95000, 95000, 0.1, 0.0, 0.0, 0.6
    std_g = bs_greeks_vec(S, K, T, r, q, sigma, True)
    inv_g = inverse_greeks(S, K, T, r, q, sigma, True, std_g)
    # Inverse delta != standard delta / S due to convexity term
    naive_btc_delta = float(std_g["delta"]) / S
    actual_btc_delta = float(inv_g["delta_btc"])
    assert abs(naive_btc_delta - actual_btc_delta) > 1e-8, \
        "Inverse delta should differ from naive delta/S"


def test_implied_vol_roundtrip():
    """Compute price from known vol, then recover vol via IV solver."""
    S, K, T, r, q, sigma = 100, 100, 1.0, 0.05, 0.0, 0.3
    price = float(bs_price_vec(S, K, T, r, q, sigma, True))
    recovered = implied_vol(price, S, K, T, r, q, True)
    assert recovered is not None
    assert abs(recovered - sigma) < 1e-6, f"Expected {sigma}, got {recovered}"


def test_implied_vol_vec():
    """Vectorized IV solver."""
    S, T, r, q, sigma = 100, 1.0, 0.05, 0.0, 0.25
    K = np.array([90, 100, 110])
    is_call = np.array([True, True, True])
    prices = bs_price_vec(S, K, T, r, q, sigma, is_call)
    ivs = implied_vol_vec(prices, S, K, T, r, q, is_call)
    np.testing.assert_allclose(ivs, sigma, atol=1e-5)


def test_implied_vol_no_solution():
    """Price below intrinsic returns None."""
    result = implied_vol(0.001, 100, 50, 1.0, 0.05, 0.0, True)
    assert result is None


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
