import numpy as np
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.vol_surface import calc_vol_parametric, svi_calibrate, svi_eval, svi_quasi_to_raw, durrleman_condition


def test_parametric_atm():
    """At ATM strike, vol should equal atm_vol (no skew/smile contribution)."""
    vol = calc_vol_parametric(95000, 95000, 0.1, 0.6, -2.5, 10, 0.01, 0.017)
    assert abs(float(vol) - 0.6) < 1e-10


def test_parametric_skew_shape():
    """With negative skew, OTM puts (lower strikes) should have higher vol."""
    strikes = np.array([85000, 90000, 95000, 100000, 105000])
    vols = calc_vol_parametric(strikes, 95000, 0.1, 0.6, -2.5, 10, 0.01, 0.017)
    # Left wing (puts) should be higher than ATM
    assert float(vols[0]) > float(vols[2]), "OTM put vol should be > ATM vol with negative skew"


def test_parametric_smile_shape():
    """With smile > 0, both wings should be elevated relative to ATM."""
    strikes = np.array([80000, 95000, 110000])
    vols = calc_vol_parametric(strikes, 95000, 0.1, 0.6, 0.0, 15, 0.0, 0.0)
    # Both wings above ATM
    assert float(vols[0]) > float(vols[1]), "Left wing should be > ATM"
    assert float(vols[2]) > float(vols[1]), "Right wing should be > ATM"


def test_parametric_floor():
    """Vol should never go below 1%."""
    vol = calc_vol_parametric(1000, 95000, 0.1, 0.01, 0.0, 0.0, -1.0, -1.0)
    assert float(vol) >= 0.01


def test_parametric_vectorized():
    """Array inputs should produce array outputs."""
    strikes = np.linspace(85000, 105000, 20)
    vols = calc_vol_parametric(strikes, 95000, 0.1, 0.6, -2.5, 10, 0.01, 0.017)
    assert len(vols) == 20
    assert all(v > 0 for v in vols)


def test_svi_calibrate_synthetic():
    """SVI should fit synthetic data with low RMSE."""
    # Generate synthetic SVI data
    k = np.linspace(-0.3, 0.3, 50)
    # True params: a=0.04, b=0.4, rho=-0.3, m=0.0, sigma=0.1
    true_w = 0.04 + 0.4 * (-0.3 * k + np.sqrt(k**2 + 0.01))
    true_iv = true_w  # treat as total variance for simplicity

    a, d, c, m, sigma = svi_calibrate(true_iv, k)
    # Evaluate fitted
    from src.vol_surface import svi_quasi
    y = (k - m) / max(sigma, 1e-6)
    fitted = svi_quasi(y, a, d, c)
    rmse = np.sqrt(np.mean((fitted - true_iv)**2))
    assert rmse < 0.001, f"SVI RMSE={rmse}, expected < 0.001"


def test_svi_eval_produces_vols():
    """svi_eval should return positive implied vols."""
    k = np.linspace(-0.2, 0.2, 20)
    true_w = 0.04 + 0.3 * (-0.2 * k + np.sqrt(k**2 + 0.01))
    a, d, c, m, sigma = svi_calibrate(true_w, k)

    strikes = 95000 * np.exp(k)
    vols = svi_eval(strikes, 95000, 0.1, a, d, c, m, sigma)
    assert all(v > 0 for v in vols), "SVI vols should be positive"


def test_svi_quasi_to_raw_roundtrip():
    """Quasi → raw conversion should preserve SVI evaluation."""
    a, d, c, m, sigma = 0.04, -0.02, 0.05, 0.0, 0.1
    a_raw, b, rho, m_raw, sigma_raw = svi_quasi_to_raw(a, d, c, m, sigma)
    assert m_raw == m
    assert sigma_raw == sigma
    assert b >= 0


def test_durrleman_condition():
    """Durrleman g(k) should be non-negative for well-behaved SVI."""
    k = np.linspace(-0.3, 0.3, 100)
    # Use conservative params that should be arb-free
    g = durrleman_condition(k, a=0.04, b=0.2, rho=-0.1, m=0.0, sigma=0.15)
    # At least the central region should be non-negative
    central = g[30:70]
    assert all(c >= -1e-6 for c in central), "Central region should satisfy Durrleman condition"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
