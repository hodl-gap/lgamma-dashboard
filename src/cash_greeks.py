import numpy as np


def cash_greeks_usd(delta, gamma, vega, theta, underlying_price, position_size=1.0):
    """
    USD cash Greeks for Deribit BTC options (1 BTC notional).
    Uses standard BS Greeks — measures USD PnL sensitivity.
    """
    S = underlying_price
    return {
        "cash_delta_usd": delta * S * position_size,
        "cash_gamma_usd": gamma * S**2 * position_size / 100,
        "cash_vega_usd": vega * S * position_size,
        "cash_theta_usd": theta * S * position_size,
    }


def cash_greeks_btc(delta_btc, gamma_btc, vega_btc, theta_btc, underlying_price, position_size=1.0):
    """
    BTC cash Greeks for Deribit inverse options.
    Uses inverse-adjusted Greeks from pricing.inverse_greeks().
    """
    S = underlying_price
    return {
        "cash_delta_btc": delta_btc * S * position_size,
        "cash_gamma_btc": gamma_btc * S**2 * position_size / 100,
        "cash_vega_btc": vega_btc * S * position_size,
        "cash_theta_btc": theta_btc * S * position_size,
    }


def perp_cash_greeks(side, size, perp_price):
    """Cash Greeks for perpetual futures hedge leg. Delta only."""
    side_mult = 1.0 if side == "long" else -1.0
    return {
        "cash_delta_usd": side_mult * size * perp_price,
        "cash_gamma_usd": 0.0,
        "cash_vega_usd": 0.0,
        "cash_theta_usd": 0.0,
        "cash_delta_btc": side_mult * size,
        "cash_gamma_btc": 0.0,
        "cash_vega_btc": 0.0,
        "cash_theta_btc": 0.0,
    }


def aggregate_cash_greeks(positions_greeks):
    """Sum cash Greeks across all positions. Input: list of dicts."""
    totals = {}
    keys = ["cash_delta_usd", "cash_gamma_usd", "cash_vega_usd", "cash_theta_usd",
            "cash_delta_btc", "cash_gamma_btc", "cash_vega_btc", "cash_theta_btc"]
    for k in keys:
        totals[k] = sum(p.get(k, 0.0) for p in positions_greeks)
    return totals
