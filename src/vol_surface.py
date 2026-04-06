import numpy as np
from scipy.optimize import lsq_linear, minimize


def calc_vol_parametric(strike, atm_strike, T, atm_vol, base_skew, base_smile, put_shift, call_shift):
    """Vectorized parametric vol curve. All inputs can be arrays."""
    strike = np.asarray(strike, float)
    atm_strike = np.asarray(atm_strike, float)

    sqrt_T = np.sqrt(T)
    log_moneyness = np.log(strike / atm_strike)
    denom = atm_vol * sqrt_T
    normalized_strike = np.where(denom == 0, 0.0, log_moneyness / denom)

    skew = 0.2 * base_skew * sqrt_T
    smile = 0.04 * base_smile * T

    vol = atm_vol + skew * normalized_strike + smile * normalized_strike**2
    vol = vol + np.where(
        strike < atm_strike,
        put_shift * np.abs(normalized_strike),
        np.where(strike > atm_strike, call_shift * normalized_strike, 0.0),
    )
    return np.maximum(vol, 0.01)


def svi_quasi(y, a, d, c):
    """SVI in quasi parameterization."""
    return a + d * y + c * np.sqrt(y**2 + 1)


def _solve_adc(iv, x, m, sigma):
    """Inner step: solve (a, d, c) via bounded linear least-squares."""
    sigma = max(sigma, 1e-6)
    y = (x - m) / sigma
    z = np.sqrt(y**2 + 1)
    A = np.column_stack([np.ones(len(iv)), y, z])
    ub_a = max(float(iv.max()), 1e-6)
    bounds = ([0, -np.inf, 0], [ub_a, np.inf, np.inf])
    result = lsq_linear(A, iv, bounds, tol=1e-12, verbose=0)
    return result.x


def svi_calibrate(iv_array, log_moneyness_array, init_m=0.0, init_sigma=0.1):
    """
    Quasi-explicit SVI calibration (2-step method).
    Returns (a, d, c, m, sigma) in quasi form.
    """
    iv = np.asarray(iv_array, float)
    x = np.asarray(log_moneyness_array, float)

    def objective(params):
        m, sigma = params
        sigma = max(sigma, 1e-6)
        a, d, c = _solve_adc(iv, x, m, sigma)
        y = (x - m) / sigma
        fitted = svi_quasi(y, a, d, c)
        return float(np.sum((fitted - iv) ** 2))

    result = minimize(
        objective, [init_m, init_sigma], method="Nelder-Mead",
        options={"xatol": 1e-10, "fatol": 1e-12, "maxiter": 500},
    )
    m, sigma = result.x
    sigma = max(sigma, 1e-6)
    a, d, c = _solve_adc(iv, x, m, sigma)
    return a, d, c, m, sigma


def svi_quasi_to_raw(a, d, c, m, sigma):
    """Convert quasi SVI params to raw SVI params (a_raw, b, rho, m, sigma)."""
    b = np.sqrt(d**2 + c**2) / sigma if sigma > 1e-8 else 0.0
    rho = d / (b * sigma) if b * sigma > 1e-8 else 0.0
    a_raw = a - b * sigma * np.sqrt(1 - rho**2)
    return a_raw, b, rho, m, sigma


def svi_raw(k, a, b, rho, m, sigma):
    """Raw SVI total variance: w(k) = a + b*(rho*(k-m) + sqrt((k-m)^2 + sigma^2))"""
    k = np.asarray(k, float)
    return a + b * (rho * (k - m) + np.sqrt((k - m) ** 2 + sigma**2))


def svi_eval(strikes, forward, T, a, d, c, m, sigma):
    """Evaluate SVI to get implied vols from quasi params."""
    k = np.log(np.asarray(strikes, float) / forward)
    sigma_safe = max(sigma, 1e-6)
    y = (k - m) / sigma_safe
    total_var = svi_quasi(y, a, d, c)
    total_var = np.maximum(total_var, 1e-8)
    return np.sqrt(total_var / T)


def durrleman_condition(k, a, b, rho, m, sigma):
    """
    Check Durrleman's no-arbitrage condition: g(k) >= 0.
    Returns g(k) array. Negative values indicate arbitrage.
    """
    k = np.asarray(k, float)
    w = svi_raw(k, a, b, rho, m, sigma)
    dk = k - m
    sqrt_term = np.sqrt(dk**2 + sigma**2)
    w_prime = b * (rho + dk / sqrt_term)
    w_double_prime = b * sigma**2 / (sqrt_term**3)

    g = (1 - k * w_prime / (2 * w)) ** 2 - w_prime**2 / 4 * (1 / w + 0.25) + w_double_prime / 2
    return g
