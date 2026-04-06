import logging
import numpy as np

from src.db import query, get_config
from src.vol_surface import calc_vol_parametric

logger = logging.getLogger(__name__)

# In-memory alert store (cleared on restart)
_alerts = []


def check_vol_divergence():
    """Check OTM 5% put IV vs fitted IV gap > threshold."""
    threshold = float(get_config("vol_alert_threshold_pct", "0.02"))

    market = query("SELECT deribit_index FROM market_data ORDER BY timestamp DESC LIMIT 1")
    if not market:
        return
    underlying = market[0]["deribit_index"]
    otm_strike = underlying * 0.95  # 5% OTM put

    # Find nearest put
    nearest = query("""
        SELECT instrument_name, strike_price, market_bid_iv, market_ask_iv, custom_iv
        FROM vol_surface
        WHERE option_type = 'put'
          AND timestamp = (SELECT MAX(timestamp) FROM vol_surface)
        ORDER BY ABS(strike_price - ?)
        LIMIT 1
    """, [otm_strike])

    if not nearest:
        return

    n = nearest[0]
    bid_iv = n.get("market_bid_iv") or 0
    ask_iv = n.get("market_ask_iv") or 0
    market_iv = (bid_iv + ask_iv) / 2 if bid_iv and ask_iv else 0
    fitted_iv = n.get("custom_iv", 0)

    if market_iv <= 0 or fitted_iv <= 0:
        return

    spread = abs(market_iv - fitted_iv)
    if spread > threshold:
        msg = (f"Vol divergence: {n['instrument_name']} "
               f"market IV={market_iv:.1%} vs fitted={fitted_iv:.1%} "
               f"(spread={spread:.1%} > threshold={threshold:.1%})")
        logger.warning(msg)
        _alerts.append({"level": "warning", "message": msg, "action": "Consider adjusting vol curve parameters"})


def get_alerts():
    """Return current alerts."""
    return list(_alerts)


def clear_alerts():
    """Clear all alerts."""
    _alerts.clear()
