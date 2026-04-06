import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime, timezone
import logging

logging.basicConfig(level=logging.INFO)

from src.db import init_schema, query, query_df, execute, get_conn
from src.ingestion import start_polling, stop_polling, is_polling, fetch_and_process
from src.vol_surface import calc_vol_parametric, svi_calibrate, svi_eval, svi_quasi_to_raw, durrleman_condition
from src.config import settings
from src.alerts import get_alerts, clear_alerts

st.set_page_config(page_title="Lgamma BTC Options", layout="wide", page_icon="📊")

# Initialize
init_schema()

# Auto-start polling
if "polling_started" not in st.session_state:
    start_polling()
    st.session_state.polling_started = True


# ============================================================
# SIDEBAR
# ============================================================
with st.sidebar:
    st.title("Lgamma Dashboard")

    # Status
    status = "Active" if is_polling() else "Inactive"
    st.metric("Polling Status", status)

    market = query("SELECT * FROM market_data ORDER BY timestamp DESC LIMIT 1")
    if market:
        m = market[0]
        st.metric("Last Update", datetime.fromisoformat(str(m["timestamp"])).strftime("%H:%M:%S UTC"))
        st.metric("DB Rows (chain)", query("SELECT count(*) as c FROM option_chain_raw")[0]["c"])

    st.divider()

    # EOD Snapshot
    if st.button("Take EOD Snapshot"):
        from src.snapshots import take_eod_snapshot
        take_eod_snapshot()
        st.success("Snapshot taken!")

    # Alerts
    alerts = get_alerts()
    if alerts:
        st.divider()
        st.subheader("Alerts")
        for alert in alerts[-5:]:  # Show last 5
            st.warning(alert["message"])
        if st.button("Clear Alerts"):
            clear_alerts()
            st.rerun()

    st.divider()

    # Config
    st.subheader("Settings")
    r = st.number_input("Risk-free rate", value=settings.risk_free_rate, format="%.4f", step=0.001)
    strike_range = st.number_input("Strike range %", value=settings.strike_range_pct * 100, step=1.0) / 100

    if r != settings.risk_free_rate:
        settings.risk_free_rate = r
    st.session_state["strike_range_pct"] = strike_range


# ============================================================
# TABS
# ============================================================
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "Risk Profile", "Vol Curve Editor", "Option Chain", "Trade Log", "History & Analytics"
])

# ============================================================
# TAB 1: RISK PROFILE
# ============================================================
with tab1:
    st.header("Risk Profile")

    # Market summary
    market = query("SELECT * FROM market_data ORDER BY timestamp DESC LIMIT 1")
    if market:
        m = market[0]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("BTC (Deribit Index)", f"${m['deribit_index']:,.0f}")
        c2.metric("Perp (Binance)", f"${m['perp_price']:,.0f}")
        c3.metric("Basis", f"${m.get('basis', 0):,.0f}" if m.get('basis') else "N/A")
        funding = m.get("funding_rate")
        c4.metric("Funding Rate", f"{funding:.6f}" if funding else "N/A")
    else:
        st.info("Waiting for market data...")

    st.divider()

    # Portfolio Greeks (from positions)
    positions = query("SELECT * FROM positions WHERE is_open = TRUE")
    if positions:
        st.subheader("Portfolio Greeks")
        # Aggregate from vol_surface for each position's instrument
        total_greeks = {"cash_delta_usd": 0, "cash_gamma_usd": 0, "cash_vega_usd": 0, "cash_theta_usd": 0}
        pos_data = []
        for pos in positions:
            vs = query("""
                SELECT * FROM vol_surface WHERE instrument_name = ?
                ORDER BY timestamp DESC LIMIT 1
            """, [pos["instrument_name"]])
            if vs:
                v = vs[0]
                side_mult = 1.0 if pos["side"] == "long" else -1.0
                size = pos["size"]
                for k in total_greeks:
                    total_greeks[k] += v.get(k, 0) * size * side_mult

                pos_data.append({
                    "Instrument": pos["instrument_name"],
                    "Type": pos["instrument_type"],
                    "Side": pos["side"],
                    "Size": pos["size"],
                    "Entry": pos["avg_entry_price"],
                    "Delta": f"{v.get('delta', 0):.3f}",
                    "Gamma": f"{v.get('gamma', 0):.6f}",
                    "Vega": f"{v.get('vega', 0):.3f}",
                    "Theta": f"{v.get('theta', 0):.4f}",
                })

        gc1, gc2, gc3, gc4 = st.columns(4)
        gc1.metric("Cash Delta (USD)", f"${total_greeks['cash_delta_usd']:,.0f}")
        gc2.metric("Cash Gamma (USD)", f"${total_greeks['cash_gamma_usd']:,.0f}")
        gc3.metric("Cash Vega (USD)", f"${total_greeks['cash_vega_usd']:,.0f}")
        gc4.metric("Cash Theta (USD)", f"${total_greeks['cash_theta_usd']:,.0f}")

        st.subheader("Positions")
        st.dataframe(pd.DataFrame(pos_data), use_container_width=True)
    else:
        st.info("No open positions. Fill sync will activate when API credentials are IP-whitelisted.")

    # PnL section
    pnl_data = query("SELECT * FROM eod_snapshots ORDER BY snapshot_date DESC LIMIT 1")
    if pnl_data:
        st.subheader("Today's PnL Attribution")
        snap = pnl_data[0]
        pc1, pc2, pc3, pc4, pc5 = st.columns(5)
        pc1.metric("Delta PnL", f"${snap.get('delta_pnl', 0):,.0f}")
        pc2.metric("Gamma PnL", f"${snap.get('gamma_pnl', 0):,.0f}")
        pc3.metric("Vega PnL", f"${snap.get('vega_pnl', 0):,.0f}")
        pc4.metric("Theta PnL", f"${snap.get('theta_pnl', 0):,.0f}")
        pc5.metric("Total PnL", f"${snap.get('market_pnl', 0):,.0f}")


# ============================================================
# TAB 2: VOL CURVE EDITOR
# ============================================================
with tab2:
    st.header("Vol Curve Editor")

    # Get available expiries
    expiries_raw = query("""
        SELECT DISTINCT expiry_date FROM vol_params
        WHERE is_active = TRUE ORDER BY expiry_date
    """)
    if not expiries_raw:
        st.info("Waiting for first data poll to populate expiries...")
    else:
        expiry_options = [str(e["expiry_date"]) for e in expiries_raw]
        selected_expiry = st.selectbox("Expiry", expiry_options)

        # Load current params
        params = query("""
            SELECT * FROM vol_params
            WHERE expiry_date = ? AND is_active = TRUE
            ORDER BY timestamp DESC LIMIT 1
        """, [selected_expiry])

        if params:
            p = params[0]

            # Parameter controls
            col_params, col_chart = st.columns([1, 3])

            with col_params:
                st.subheader("Parameters")
                atm_vol = st.number_input("ATM Vol", value=float(p["atm_vol"]), format="%.4f", step=0.01, key="atm_vol")
                base_skew = st.number_input("Skew", value=float(p["base_skew"]), format="%.2f", step=0.1, key="skew")
                base_smile = st.number_input("Smile", value=float(p["base_smile"]), format="%.1f", step=0.5, key="smile")
                put_shift = st.number_input("Put Shift", value=float(p["put_shift"]), format="%.4f", step=0.005, key="pshift")
                call_shift = st.number_input("Call Shift", value=float(p["call_shift"]), format="%.4f", step=0.005, key="cshift")
                atm_strike = float(p["atm_strike"])

                # ATM IV = avg of call bid, call ask, put bid, put ask at ATM strike
                atm_ivs = query("""
                    SELECT bid_iv, ask_iv, option_type FROM option_chain_raw
                    WHERE expiry_date = ? AND strike_price = ?
                    AND timestamp = (SELECT MAX(timestamp) FROM option_chain_raw WHERE expiry_date = ?)
                """, [selected_expiry, atm_strike, selected_expiry])
                atm_iv_vals = [v for row in (atm_ivs or []) for v in [row.get("bid_iv"), row.get("ask_iv")] if v and not np.isnan(v)]
                market_atm_iv = np.mean(atm_iv_vals) if atm_iv_vals else None
                atm_iv_display = f"{market_atm_iv*100:.1f}%" if market_atm_iv else "N/A"
                st.text(f"ATM Strike: {atm_strike:,.0f}  |  Mkt ATM IV: {atm_iv_display}")

                # Get T for this expiry
                chain_sample = query("""
                    SELECT time_to_expiry, days_to_expiry FROM option_chain_raw
                    WHERE expiry_date = ? ORDER BY timestamp DESC LIMIT 1
                """, [selected_expiry])
                T = chain_sample[0]["time_to_expiry"] if chain_sample else 0.1
                dte = chain_sample[0]["days_to_expiry"] if chain_sample else 0
                st.text(f"DTE: {dte}  T: {T:.4f}")

                if st.button("Apply & Save"):
                    # Deactivate old, insert new
                    execute("UPDATE vol_params SET is_active = FALSE WHERE expiry_date = ?", [selected_expiry])
                    now = datetime.now(timezone.utc)
                    execute("""
                        INSERT INTO vol_params (timestamp, expiry_date, atm_vol, base_skew, base_smile,
                            put_shift, call_shift, atm_strike, is_active)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, TRUE)
                    """, [now, selected_expiry, atm_vol, base_skew, base_smile, put_shift, call_shift, atm_strike])
                    st.success("Params saved!")
                    st.rerun()

            with col_chart:
                # Get market data for this expiry
                chain = query("""
                    SELECT strike_price, bid_iv, ask_iv, option_type FROM option_chain_raw
                    WHERE expiry_date = ? AND timestamp = (
                        SELECT MAX(timestamp) FROM option_chain_raw WHERE expiry_date = ?
                    )
                    ORDER BY strike_price
                """, [selected_expiry, selected_expiry])

                if chain:
                    # Filter to OTM only: puts below ATM, calls at/above ATM
                    otm_market = [c for c in chain if
                                  (c["option_type"] == "put" and c["strike_price"] < atm_strike) or
                                  (c["option_type"] == "call" and c["strike_price"] >= atm_strike)]
                    strikes_market = np.array([c["strike_price"] for c in otm_market])
                    bid_ivs = np.array([c.get("bid_iv") or np.nan for c in otm_market]) * 100
                    ask_ivs = np.array([c.get("ask_iv") or np.nan for c in otm_market]) * 100

                    # Fitted curve (fine grid)
                    strike_range = np.linspace(strikes_market.min(), strikes_market.max(), 200)
                    fitted = calc_vol_parametric(strike_range, atm_strike, T, atm_vol, base_skew, base_smile, put_shift, call_shift) * 100

                    fig = go.Figure()
                    # Bid/Ask dots
                    fig.add_trace(go.Scatter(x=strikes_market, y=bid_ivs, mode="markers",
                                             name="Bid IV", marker=dict(color="blue", size=5)))
                    fig.add_trace(go.Scatter(x=strikes_market, y=ask_ivs, mode="markers",
                                             name="Ask IV", marker=dict(color="red", size=5)))
                    # Fitted curve
                    fig.add_trace(go.Scatter(x=strike_range, y=fitted, mode="lines",
                                             name="Fitted (Parametric)", line=dict(color="green", width=2)))

                    # SVI overlay (if enough data points)
                    otm_chain = [c for c in chain if
                                 (c["option_type"] == "put" and c["strike_price"] < atm_strike) or
                                 (c["option_type"] == "call" and c["strike_price"] >= atm_strike)]
                    if len(otm_chain) >= 5:
                        otm_strikes = np.array([c["strike_price"] for c in otm_chain])
                        otm_ivs = []
                        for c in otm_chain:
                            iv = c.get("bid_iv") or c.get("ask_iv")
                            otm_ivs.append(iv if iv and not np.isnan(iv) else np.nan)
                        otm_ivs = np.array(otm_ivs)
                        valid = ~np.isnan(otm_ivs)

                        if valid.sum() >= 5:
                            log_k = np.log(otm_strikes[valid] / atm_strike)
                            iv_valid = otm_ivs[valid]
                            total_var = (iv_valid ** 2) * T

                            try:
                                a, d, c_svi, m, sigma = svi_calibrate(total_var, log_k)
                                svi_vols = svi_eval(strike_range, atm_strike, T, a, d, c_svi, m, sigma) * 100

                                fig.add_trace(go.Scatter(x=strike_range, y=svi_vols, mode="lines",
                                                         name="SVI (Raw)", line=dict(color="orange", width=2, dash="dash")))

                                # Durrleman check
                                a_raw, b, rho, m_raw, sigma_raw = svi_quasi_to_raw(a, d, c_svi, m, sigma)
                                log_k_grid = np.log(strike_range / atm_strike)
                                g_vals = durrleman_condition(log_k_grid, a_raw, b, rho, m_raw, sigma_raw)
                                arb_strikes = strike_range[g_vals < 0]
                                if len(arb_strikes) > 0:
                                    st.caption(f"Arbitrage warning: Durrleman condition violated at {len(arb_strikes)} strikes")
                            except Exception:
                                pass

                    fig.update_layout(
                        title=f"Vol Curve — {selected_expiry[:10]} (DTE: {dte})",
                        xaxis_title="Strike",
                        yaxis_title="Implied Vol (%)",
                        height=500,
                    )
                    st.plotly_chart(fig, use_container_width=True)

            # Strike table
            st.subheader("Strike Table")
            vs_data = query("""
                SELECT strike_price, option_type, market_bid_iv, market_ask_iv, custom_iv, prev_custom_iv
                FROM vol_surface
                WHERE expiry_date = ? AND timestamp = (
                    SELECT MAX(timestamp) FROM vol_surface WHERE expiry_date = ?
                )
                ORDER BY strike_price, option_type
            """, [selected_expiry, selected_expiry])

            if vs_data:
                # Filter to OTM only: puts below ATM, calls at/above ATM
                vs_otm = [v for v in vs_data if
                          (v["option_type"] == "put" and v["strike_price"] < atm_strike) or
                          (v["option_type"] == "call" and v["strike_price"] >= atm_strike)]
                df = pd.DataFrame(vs_otm)
                df["market_bid_iv"] = df["market_bid_iv"].apply(lambda x: f"{x*100:.1f}%" if x and not np.isnan(x) else "")
                df["market_ask_iv"] = df["market_ask_iv"].apply(lambda x: f"{x*100:.1f}%" if x and not np.isnan(x) else "")
                df["custom_iv"] = df["custom_iv"].apply(lambda x: f"{x*100:.1f}%")
                df.columns = ["Strike", "Type", "Bid IV", "Ask IV", "Fitted IV", "Prev IV"]
                st.dataframe(df, use_container_width=True, height=400)


# ============================================================
# TAB 3: OPTION CHAIN
# ============================================================
with tab3:
    st.header("Option Chain")

    expiries_chain = query("""
        SELECT DISTINCT expiry_date FROM option_chain_raw
        WHERE timestamp = (SELECT MAX(timestamp) FROM option_chain_raw)
        ORDER BY expiry_date
    """)

    if not expiries_chain:
        st.info("Waiting for data...")
    else:
        exp_opts = [str(e["expiry_date"]) for e in expiries_chain]
        sel_exp = st.selectbox("Expiry", exp_opts, key="chain_expiry")

        # Get latest vol surface for this expiry
        vs = query("""
            SELECT * FROM vol_surface
            WHERE expiry_date = ? AND timestamp = (
                SELECT MAX(timestamp) FROM vol_surface WHERE expiry_date = ?
            )
            ORDER BY strike_price, option_type
        """, [sel_exp, sel_exp])

        # Apply strike range filter from sidebar
        _sr = st.session_state.get("strike_range_pct", settings.strike_range_pct)
        _mkt = query("SELECT deribit_index FROM market_data ORDER BY timestamp DESC LIMIT 1")
        if vs and _mkt:
            _idx = _mkt[0]["deribit_index"]
            _lo, _hi = _idx * (1 - _sr), _idx * (1 + _sr)
            vs = [v for v in vs if _lo <= v["strike_price"] <= _hi]

        if vs:
            calls = [v for v in vs if v["option_type"] == "call"]
            puts = [v for v in vs if v["option_type"] == "put"]

            # Build symmetric table
            strikes = sorted(set(v["strike_price"] for v in vs))
            call_map = {v["strike_price"]: v for v in calls}
            put_map = {v["strike_price"]: v for v in puts}

            rows = []
            for k in strikes:
                c = call_map.get(k, {})
                p = put_map.get(k, {})
                rows.append({
                    "C Bid": f"{c.get('market_bid_iv', 0)*100:.1f}%" if c.get("market_bid_iv") else "",
                    "C Ask": f"{c.get('market_ask_iv', 0)*100:.1f}%" if c.get("market_ask_iv") else "",
                    "C Theo": f"${c.get('theo_price_usd', 0):,.0f}" if c.get("theo_price_usd") else "",
                    "C Delta": f"{c.get('delta', 0):.3f}" if c.get("delta") else "",
                    "C Spread": f"${c.get('price_spread', 0):,.0f}" if c.get("price_spread") else "",
                    "Strike": f"{k:,.0f}",
                    "P Spread": f"${p.get('price_spread', 0):,.0f}" if p.get("price_spread") else "",
                    "P Delta": f"{p.get('delta', 0):.3f}" if p.get("delta") else "",
                    "P Theo": f"${p.get('theo_price_usd', 0):,.0f}" if p.get("theo_price_usd") else "",
                    "P Ask": f"{p.get('market_ask_iv', 0)*100:.1f}%" if p.get("market_ask_iv") else "",
                    "P Bid": f"{p.get('market_bid_iv', 0)*100:.1f}%" if p.get("market_bid_iv") else "",
                })

            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True, height=600)


# ============================================================
# TAB 4: TRADE LOG
# ============================================================
with tab4:
    st.header("Trade Log")

    trades = query("SELECT * FROM trade_log ORDER BY timestamp DESC LIMIT 100")
    if trades:
        df = pd.DataFrame(trades)
        display_cols = ["timestamp", "exchange", "instrument_name", "side", "size", "price", "fee", "notional_usd"]
        available = [c for c in display_cols if c in df.columns]
        st.dataframe(df[available], use_container_width=True)
    else:
        st.info("No trades yet. Fill sync will activate when API credentials are IP-whitelisted.")


# ============================================================
# TAB 5: HISTORY & ANALYTICS
# ============================================================
with tab5:
    st.header("History & Analytics")

    # PnL time series
    st.subheader("Daily PnL Attribution")
    pnl_history = query("""
        SELECT snapshot_date,
            SUM(delta_pnl) as delta_pnl,
            SUM(gamma_pnl) as gamma_pnl,
            SUM(vega_pnl) as vega_pnl,
            SUM(theta_pnl) as theta_pnl,
            SUM(market_pnl) as total_pnl
        FROM eod_snapshots
        GROUP BY snapshot_date
        ORDER BY snapshot_date
    """)

    if pnl_history:
        df_pnl = pd.DataFrame(pnl_history)
        fig = go.Figure()
        for col in ["delta_pnl", "gamma_pnl", "vega_pnl", "theta_pnl"]:
            if col in df_pnl.columns:
                fig.add_trace(go.Bar(x=df_pnl["snapshot_date"], y=df_pnl[col], name=col.replace("_pnl", "").title()))
        if "total_pnl" in df_pnl.columns:
            fig.add_trace(go.Scatter(x=df_pnl["snapshot_date"], y=df_pnl["total_pnl"],
                                     name="Total", line=dict(color="white", width=2)))
        fig.update_layout(barmode="relative", height=400)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No EOD snapshots yet. Click 'Take EOD Snapshot' in the sidebar to start accumulating history.")

    # Vol surface history
    st.subheader("Vol Surface History")
    vol_dates = query("SELECT DISTINCT snapshot_date FROM vol_history ORDER BY snapshot_date DESC LIMIT 30")
    if vol_dates:
        date_opts = [str(d["snapshot_date"]) for d in vol_dates]
        sel_date = st.selectbox("Compare Date", date_opts, key="vol_hist_date")

        hist_vol = query("""
            SELECT strike_price, fitted_iv FROM vol_history
            WHERE snapshot_date = ? ORDER BY strike_price
        """, [sel_date])

        if hist_vol:
            fig_vh = go.Figure()
            fig_vh.add_trace(go.Scatter(
                x=[h["strike_price"] for h in hist_vol],
                y=[h["fitted_iv"] * 100 for h in hist_vol],
                name=f"Historical ({sel_date})", line=dict(dash="dash"),
            ))
            # Current
            current_vol = query("""
                SELECT strike_price, custom_iv FROM vol_surface
                WHERE timestamp = (SELECT MAX(timestamp) FROM vol_surface)
                ORDER BY strike_price
            """)
            if current_vol:
                fig_vh.add_trace(go.Scatter(
                    x=[c["strike_price"] for c in current_vol],
                    y=[c["custom_iv"] * 100 for c in current_vol],
                    name="Current", line=dict(color="green"),
                ))
            fig_vh.update_layout(xaxis_title="Strike", yaxis_title="IV (%)", height=400)
            st.plotly_chart(fig_vh, use_container_width=True)
    else:
        st.info("No vol history snapshots yet.")
