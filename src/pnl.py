def pnl_attribution(prev_snapshot, current_state):
    """
    Decompose daily PnL into Greek components.
    Uses T-1 Greeks * today's market moves.

    prev_snapshot / current_state: dicts with keys:
        underlying, iv, cash_delta, cash_gamma, cash_vega, cash_theta, market_value
    """
    dS = current_state["underlying"] - prev_snapshot["underlying"]
    dIV = current_state["iv"] - prev_snapshot["iv"]

    delta_pnl = prev_snapshot["cash_delta"] * (dS / prev_snapshot["underlying"])
    gamma_pnl = 0.5 * prev_snapshot["cash_gamma"] * (dS**2)
    vega_pnl = prev_snapshot["cash_vega"] * (dIV * 100)  # vega is per 1%
    theta_pnl = prev_snapshot["cash_theta"]  # already per day

    explained_pnl = delta_pnl + gamma_pnl + vega_pnl + theta_pnl
    actual_pnl = current_state["market_value"] - prev_snapshot["market_value"]
    unexplained = actual_pnl - explained_pnl

    return {
        "delta_pnl": delta_pnl,
        "gamma_pnl": gamma_pnl,
        "vega_pnl": vega_pnl,
        "theta_pnl": theta_pnl,
        "explained_pnl": explained_pnl,
        "actual_pnl": actual_pnl,
        "unexplained": unexplained,
    }
