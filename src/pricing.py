import numpy as np
from scipy.stats import norm
from scipy.optimize import brentq


def bs_price_vec(S, K, T, r, q, sigma, is_call):
    """Vectorized Black-Scholes price. All inputs can be arrays."""
    S, K, T, sigma = np.asarray(S, float), np.asarray(K, float), np.asarray(T, float), np.asarray(sigma, float)
    is_call = np.asarray(is_call, bool)

    d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)

    call_price = S * np.exp(-q * T) * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    put_price = K * np.exp(-r * T) * norm.cdf(-d2) - S * np.exp(-q * T) * norm.cdf(-d1)
    return np.where(is_call, call_price, put_price)


def bs_greeks_vec(S, K, T, r, q, sigma, is_call):
    """
    Vectorized Greeks. Returns dict of arrays.
    Vega: per 1% vol move. Theta: per 1 calendar day.
    """
    S, K, T, sigma = np.asarray(S, float), np.asarray(K, float), np.asarray(T, float), np.asarray(sigma, float)
    is_call = np.asarray(is_call, bool)

    sqrt_T = np.sqrt(T)
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T

    nd1_pdf = norm.pdf(d1)
    Nd1 = norm.cdf(d1)
    Nd2 = norm.cdf(d2)

    exp_qT = np.exp(-q * T)
    exp_rT = np.exp(-r * T)

    delta = np.where(is_call, exp_qT * Nd1, exp_qT * (Nd1 - 1))
    gamma = (exp_qT * nd1_pdf) / (S * sigma * sqrt_T)
    vega = (S * exp_qT * nd1_pdf * sqrt_T) / 100
    term1 = -(S * exp_qT * nd1_pdf * sigma) / (2 * sqrt_T)
    theta_call = (term1 + q * S * exp_qT * Nd1 - r * K * exp_rT * Nd2) / 365
    theta_put = (term1 - q * S * exp_qT * norm.cdf(-d1) + r * K * exp_rT * norm.cdf(-d2)) / 365
    theta = np.where(is_call, theta_call, theta_put)

    return {"delta": delta, "gamma": gamma, "vega": vega, "theta": theta}


def inverse_greeks(S, K, T, r, q, sigma, is_call, std_greeks):
    """
    Convert standard BS Greeks to inverse (BTC-settled) Greeks.
    V_btc = V_usd / S, so derivatives w.r.t. S pick up extra terms.
    """
    price_usd = bs_price_vec(S, K, T, r, q, sigma, is_call)
    S = np.asarray(S, float)
    price_btc = price_usd / S
    delta_usd = std_greeks["delta"]

    delta_btc = (delta_usd - price_btc) / S
    gamma_btc = (std_greeks["gamma"] - 2 * delta_btc) / S
    vega_btc = std_greeks["vega"] / S
    theta_btc = std_greeks["theta"] / S

    return {"delta_btc": delta_btc, "gamma_btc": gamma_btc,
            "vega_btc": vega_btc, "theta_btc": theta_btc}


def implied_vol(price, S, K, T, r, q, is_call, bounds=(0.001, 5.0)):
    """Solve for IV using Brent's method. Returns None if no solution."""
    try:
        return brentq(
            lambda sigma: float(bs_price_vec(S, K, T, r, q, sigma, is_call)) - price,
            *bounds, xtol=1e-8, maxiter=200,
        )
    except (ValueError, RuntimeError):
        return None


def implied_vol_vec(prices, S, K, T, r, q, is_call):
    """Compute IV for arrays. Returns array with NaN where solver fails."""
    prices = np.asarray(prices, float)
    S_arr = np.broadcast_to(np.asarray(S, float), prices.shape)
    K_arr = np.asarray(K, float)
    T_arr = np.broadcast_to(np.asarray(T, float), prices.shape)
    is_call_arr = np.asarray(is_call, bool)

    result = np.full(prices.shape, np.nan)
    for i in range(len(prices)):
        if prices[i] > 0 and not np.isnan(prices[i]):
            iv = implied_vol(prices[i], S_arr[i], K_arr[i], T_arr[i], r, q, is_call_arr[i])
            if iv is not None:
                result[i] = iv
    return result
