import logging
from datetime import datetime, timezone

from src.db import get_conn, query
from src.config import settings

logger = logging.getLogger(__name__)


def take_eod_snapshot(trigger="manual"):
    """Capture full system state: positions + Greeks + PnL, vol surface, market data."""
    conn = get_conn()
    try:
        now = datetime.now(timezone.utc)
        snapshot_date = now.date()

        # 1. Mark latest market_data as EOD
        conn.execute("""
            UPDATE market_data SET is_eod = TRUE
            WHERE timestamp = (SELECT MAX(timestamp) FROM market_data)
        """)

        # 2. Snapshot all open positions + Greeks
        positions = conn.execute("SELECT * FROM positions WHERE is_open = TRUE").fetchall()
        if positions:
            pos_desc = conn.execute("SELECT * FROM positions LIMIT 0").description
            pos_cols = [d[0] for d in pos_desc]

            for pos_row in positions:
                pos = dict(zip(pos_cols, pos_row))

                # Get latest Greeks for this instrument
                vs_rows = conn.execute("""
                    SELECT * FROM vol_surface WHERE instrument_name = ?
                    ORDER BY timestamp DESC LIMIT 1
                """, [pos["instrument_name"]]).fetchall()

                if vs_rows:
                    vs_desc = conn.execute("SELECT * FROM vol_surface LIMIT 0").description
                    vs_cols = [d[0] for d in vs_desc]
                    vs = dict(zip(vs_cols, vs_rows[0]))

                    conn.execute("""
                        INSERT INTO eod_snapshots (snapshot_date, snapshot_timestamp, position_id,
                            instrument_name, instrument_type, underlying_price, close_price,
                            strike_price, days_to_expiry, size, theo_price, iv,
                            delta, gamma, vega, theta,
                            cash_delta_usd, cash_gamma_usd, cash_vega_usd, cash_theta_usd,
                            risk_free_rate, time_to_expiry)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, [
                        snapshot_date, now, pos["id"],
                        pos["instrument_name"], pos["instrument_type"],
                        vs.get("theo_price_usd", 0) / vs.get("custom_iv", 1) if vs.get("custom_iv") else 0,  # approx underlying
                        vs.get("theo_price_btc"), vs.get("strike_price"),
                        None, pos["size"], vs.get("theo_price_btc"), vs.get("custom_iv"),
                        vs.get("delta"), vs.get("gamma"), vs.get("vega"), vs.get("theta"),
                        vs.get("cash_delta_usd"), vs.get("cash_gamma_usd"),
                        vs.get("cash_vega_usd"), vs.get("cash_theta_usd"),
                        settings.risk_free_rate, None,
                    ])

        # 3. Snapshot vol surface → vol_history
        vol_params = conn.execute("""
            SELECT * FROM vol_params WHERE is_active = TRUE
        """).fetchall()

        if vol_params:
            vp_desc = conn.execute("SELECT * FROM vol_params LIMIT 0").description
            vp_cols = [d[0] for d in vp_desc]

            for vp_row in vol_params:
                vp = dict(zip(vp_cols, vp_row))

                # Get all strikes for this expiry from latest vol_surface
                strikes = conn.execute("""
                    SELECT DISTINCT strike_price, custom_iv FROM vol_surface
                    WHERE expiry_date = ? AND timestamp = (
                        SELECT MAX(timestamp) FROM vol_surface WHERE expiry_date = ?
                    )
                    ORDER BY strike_price
                """, [vp["expiry_date"], vp["expiry_date"]]).fetchall()

                for strike_row in strikes:
                    strike_price, fitted_iv = strike_row
                    conn.execute("""
                        INSERT INTO vol_history (snapshot_date, snapshot_timestamp, expiry_date,
                            strike_price, fitted_iv, atm_vol, base_skew, base_smile,
                            put_shift, call_shift, model_type)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, [
                        snapshot_date, now, vp["expiry_date"],
                        strike_price, fitted_iv,
                        vp["atm_vol"], vp["base_skew"], vp["base_smile"],
                        vp["put_shift"], vp["call_shift"], vp.get("model_type", "parametric"),
                    ])

        logger.info(f"EOD snapshot complete: {snapshot_date} (trigger: {trigger})")
    finally:
        conn.close()
