import asyncio
import aiohttp
import logging
import time
import threading
import hmac
import hashlib
from datetime import datetime, timezone
import numpy as np

from src.config import settings
from src.pricing import bs_price_vec, bs_greeks_vec, inverse_greeks, implied_vol_vec
from src.vol_surface import calc_vol_parametric
from src.db import get_conn, query, init_schema

logger = logging.getLogger(__name__)

# ---------- Deribit public endpoints ----------

async def fetch_instruments(session):
    """Fetch all active BTC option instruments from Deribit."""
    url = f"{settings.deribit_url}/public/get_instruments"
    params = {"currency": "BTC", "kind": "option", "expired": "false"}
    async with session.get(url, params=params) as resp:
        data = await resp.json()
        return data.get("result", [])


async def _fetch_orderbook(session, semaphore, instrument_name):
    """Fetch single orderbook with rate-limit semaphore."""
    async with semaphore:
        url = f"{settings.deribit_url}/public/get_order_book"
        params = {"instrument_name": instrument_name, "depth": 1}
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
                return data.get("result")
        except Exception as e:
            logger.warning(f"Failed to fetch {instrument_name}: {e}")
            return None


async def fetch_orderbooks(session, instrument_names):
    """Fetch all orderbooks concurrently with semaphore."""
    semaphore = asyncio.Semaphore(settings.max_concurrent_requests)
    tasks = [_fetch_orderbook(session, semaphore, name) for name in instrument_names]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return {name: r for name, r in zip(instrument_names, results) if r is not None and not isinstance(r, Exception)}


async def fetch_binance_price():
    """Fetch BTC/USDT perp price + funding rate from Binance, with Deribit fallback."""
    async with aiohttp.ClientSession() as session:
        perp_price = None
        funding_rate = None

        # Try Binance first (with fallback to Deribit)
        try:
            async with session.get(f"{settings.binance_url}/fapi/v1/ticker/price",
                                   params={"symbol": "BTCUSDT"}, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                price_data = await resp.json()
                perp_price = float(price_data["price"])

            async with session.get(f"{settings.binance_url}/fapi/v1/fundingRate",
                                   params={"symbol": "BTCUSDT", "limit": "1"}, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                funding_data = await resp.json()
                funding_rate = float(funding_data[0]["fundingRate"]) if funding_data else None
        except Exception:
            logger.info("Binance unreachable, falling back to Deribit index price")

        # Fallback: Deribit index price
        if perp_price is None:
            try:
                async with session.get(f"{settings.deribit_url}/public/get_index_price",
                                       params={"index_name": "btc_usd"}) as resp:
                    data = await resp.json()
                    perp_price = float(data["result"]["index_price"])
            except Exception as e:
                logger.error(f"Deribit index price fallback also failed: {e}")
                raise

        return perp_price, funding_rate


# ---------- Build option chain ----------

def _parse_instrument_name(name):
    """Parse 'BTC-28MAR26-95000-C' → (expiry_str, strike, option_type)."""
    parts = name.split("-")
    if len(parts) != 4:
        return None, None, None
    return parts[1], float(parts[2]), "call" if parts[3] == "C" else "put"


async def fetch_and_process():
    """Full chain fetch → IV computation → vol surface → write to DuckDB."""
    now = datetime.now(timezone.utc)

    # Fetch Binance price
    perp_price, funding_rate = await fetch_binance_price()

    async with aiohttp.ClientSession() as session:
        # Fetch instruments
        instruments = await fetch_instruments(session)
        if not instruments:
            logger.warning("No instruments returned from Deribit")
            return

        # Get underlying price from first instrument's orderbook
        # Filter to ±strike_range_pct
        underlying_price = None
        instrument_map = {}
        for inst in instruments:
            name = inst["instrument_name"]
            expiry_ts = inst["expiration_timestamp"] / 1000  # ms → s
            strike = inst["strike"]
            opt_type = "call" if inst["option_type"] == "call" else "put"
            instrument_map[name] = {
                "expiry_ts": expiry_ts,
                "expiry_date": datetime.fromtimestamp(expiry_ts, tz=timezone.utc),
                "strike": strike,
                "option_type": opt_type,
            }

        # Fetch a sample orderbook to get underlying price
        sample_name = instruments[0]["instrument_name"]
        sample_books = await fetch_orderbooks(session, [sample_name])
        if sample_name in sample_books:
            underlying_price = sample_books[sample_name].get("underlying_price")

        if underlying_price is None:
            underlying_price = perp_price  # fallback

        # Filter strikes within range
        lower = underlying_price * (1 - settings.strike_range_pct)
        upper = underlying_price * (1 + settings.strike_range_pct)
        filtered_names = [
            name for name, info in instrument_map.items()
            if lower <= info["strike"] <= upper
        ]

        logger.info(f"Fetching {len(filtered_names)} orderbooks (filtered from {len(instruments)})")

        # Fetch all orderbooks concurrently
        orderbooks = await fetch_orderbooks(session, filtered_names)

    # Write market data
    conn = get_conn()
    try:
        basis = perp_price - underlying_price
        basis_pct = basis / underlying_price if underlying_price > 0 else 0
        conn.execute(
            "INSERT INTO market_data (timestamp, perp_price, funding_rate, deribit_index, basis, basis_pct) VALUES (?, ?, ?, ?, ?, ?)",
            [now, perp_price, funding_rate, underlying_price, basis, basis_pct],
        )

        # Process each orderbook → option_chain_raw
        chain_rows = []
        for name, book in orderbooks.items():
            info = instrument_map.get(name)
            if not info or not book:
                continue

            best_bid = book.get("best_bid_price") or 0
            best_ask = book.get("best_ask_price") or 0
            mid = (best_bid + best_ask) / 2 if best_bid and best_ask else 0
            mark = book.get("mark_price") or 0
            mark_iv = book.get("mark_iv")
            if mark_iv is not None:
                mark_iv = mark_iv / 100  # Deribit gives %, we want decimal

            dte = info["expiry_date"] - now
            days_to_expiry = max(dte.total_seconds() / 86400, 0.001)
            time_to_expiry = days_to_expiry / 365

            chain_rows.append({
                "timestamp": now,
                "instrument_name": name,
                "expiry_date": info["expiry_date"],
                "days_to_expiry": int(days_to_expiry),
                "time_to_expiry": time_to_expiry,
                "strike_price": info["strike"],
                "option_type": info["option_type"],
                "best_bid": best_bid,
                "best_ask": best_ask,
                "mid_price": mid,
                "mark_price": mark,
                "exchange_mark_iv": mark_iv,
                "underlying_price": underlying_price,
            })

        if not chain_rows:
            logger.warning("No valid chain rows to process")
            conn.close()
            return

        # Compute bid/ask IV
        r = settings.risk_free_rate
        q = settings.dividend_yield
        for row in chain_rows:
            S = row["underlying_price"]
            K = row["strike_price"]
            T = row["time_to_expiry"]
            is_call = row["option_type"] == "call"

            bid_usd = row["best_bid"] * S if row["best_bid"] else 0
            ask_usd = row["best_ask"] * S if row["best_ask"] else 0

            if bid_usd > 0:
                row["bid_iv"] = float(implied_vol_vec(np.array([bid_usd]), S, np.array([K]), T, r, q, np.array([is_call]))[0])
            if ask_usd > 0:
                row["ask_iv"] = float(implied_vol_vec(np.array([ask_usd]), S, np.array([K]), T, r, q, np.array([is_call]))[0])

        # Insert chain rows
        for row in chain_rows:
            conn.execute("""
                INSERT INTO option_chain_raw (timestamp, instrument_name, expiry_date, days_to_expiry,
                    time_to_expiry, strike_price, option_type, best_bid, best_ask, mid_price,
                    mark_price, exchange_mark_iv, bid_iv, ask_iv, underlying_price)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [row["timestamp"], row["instrument_name"], row["expiry_date"],
                  row["days_to_expiry"], row["time_to_expiry"], row["strike_price"],
                  row["option_type"], row.get("best_bid"), row.get("best_ask"),
                  row.get("mid_price"), row.get("mark_price"), row.get("exchange_mark_iv"),
                  row.get("bid_iv"), row.get("ask_iv"), row["underlying_price"]])

        # Build vol surface using current params (or defaults)
        _build_vol_surface(conn, chain_rows, underlying_price, now, r, q)

        logger.info(f"Processed {len(chain_rows)} instruments, underlying={underlying_price:.0f}")
    finally:
        conn.close()


def _build_vol_surface(conn, chain_rows, underlying_price, now, r, q):
    """Compute fitted vol + Greeks for all instruments and write to vol_surface."""
    # Group by expiry
    expiries = {}
    for row in chain_rows:
        exp = str(row["expiry_date"])
        if exp not in expiries:
            expiries[exp] = []
        expiries[exp].append(row)

    for exp_str, rows in expiries.items():
        T = rows[0]["time_to_expiry"]
        expiry_date = rows[0]["expiry_date"]

        # Get vol params for this expiry (or use defaults)
        params_rows = conn.execute(
            "SELECT * FROM vol_params WHERE expiry_date = ? AND is_active = TRUE ORDER BY timestamp DESC LIMIT 1",
            [expiry_date]
        ).fetchall()

        if params_rows:
            p = params_rows[0]
            # DuckDB returns tuples; map by index based on CREATE TABLE order
            desc = conn.execute("SELECT * FROM vol_params LIMIT 0").description
            cols = [d[0] for d in desc]
            p_dict = dict(zip(cols, p))
            atm_vol = p_dict["atm_vol"]
            base_skew = p_dict["base_skew"]
            base_smile = p_dict["base_smile"]
            put_shift = p_dict["put_shift"]
            call_shift = p_dict["call_shift"]
            atm_strike = p_dict["atm_strike"]
        else:
            # Default params: use exchange mark IV at ATM as atm_vol
            atm_strike = round(underlying_price / 500) * 500  # nearest $500
            atm_rows = [r for r in rows if abs(r["strike_price"] - atm_strike) < 1000]
            if atm_rows:
                ivs = [r.get("exchange_mark_iv") for r in atm_rows if r.get("exchange_mark_iv")]
                atm_vol = np.mean(ivs) if ivs else 0.6
            else:
                atm_vol = 0.6
            base_skew = -2.0
            base_smile = 8.0
            put_shift = 0.01
            call_shift = 0.01

            # Save default params
            conn.execute("""
                INSERT INTO vol_params (timestamp, expiry_date, atm_vol, base_skew, base_smile,
                    put_shift, call_shift, atm_strike, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, TRUE)
            """, [now, expiry_date, atm_vol, base_skew, base_smile, put_shift, call_shift, atm_strike])

        # Compute fitted vol for each strike
        strikes = np.array([r["strike_price"] for r in rows])
        option_types = np.array([r["option_type"] for r in rows])
        is_call = option_types == "call"

        fitted_iv = calc_vol_parametric(strikes, atm_strike, T, atm_vol, base_skew, base_smile, put_shift, call_shift)

        # Compute theo price + Greeks
        S = underlying_price
        theo_usd = bs_price_vec(S, strikes, T, r, q, fitted_iv, is_call)
        theo_btc = theo_usd / S

        greeks = bs_greeks_vec(S, strikes, T, r, q, fitted_iv, is_call)
        inv_g = inverse_greeks(S, strikes, T, r, q, fitted_iv, is_call, greeks)

        for i, row in enumerate(rows):
            mid_usd = row["mid_price"] * S if row["mid_price"] else 0
            spread = mid_usd - float(theo_usd[i]) if mid_usd > 0 else None

            conn.execute("""
                INSERT INTO vol_surface (timestamp, instrument_name, expiry_date, strike_price,
                    option_type, market_bid_iv, market_ask_iv, custom_iv, theo_price_btc,
                    theo_price_usd, market_mid_usd, price_spread,
                    delta, gamma, vega, theta,
                    cash_delta_usd, cash_gamma_usd, cash_vega_usd, cash_theta_usd,
                    cash_delta_btc, cash_gamma_btc, cash_vega_btc, cash_theta_btc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                now, row["instrument_name"], expiry_date, row["strike_price"],
                row["option_type"], row.get("bid_iv"), row.get("ask_iv"),
                float(fitted_iv[i]), float(theo_btc[i]), float(theo_usd[i]),
                mid_usd, spread,
                float(greeks["delta"][i]), float(greeks["gamma"][i]),
                float(greeks["vega"][i]), float(greeks["theta"][i]),
                float(greeks["delta"][i]) * S, float(greeks["gamma"][i]) * S**2 / 100,
                float(greeks["vega"][i]) * S, float(greeks["theta"][i]) * S,
                float(inv_g["delta_btc"][i]) * S, float(inv_g["gamma_btc"][i]) * S**2 / 100,
                float(inv_g["vega_btc"][i]) * S, float(inv_g["theta_btc"][i]) * S,
            ])


# ---------- Background polling ----------

_polling_active = False
_poll_thread = None


def _poll_loop():
    """Background polling loop."""
    global _polling_active
    last_chain = 0
    last_fills = 0

    while _polling_active:
        now = time.time()

        # Chain + full surface rebuild
        if now - last_chain >= settings.poll_chain_sec:
            try:
                asyncio.run(fetch_and_process())
                last_chain = time.time()
                # Check alerts after each chain update
                try:
                    from src.alerts import check_vol_divergence
                    check_vol_divergence()
                except Exception as e:
                    logger.debug(f"Alert check error: {e}")
            except Exception as e:
                logger.error(f"Chain poll error: {e}", exc_info=True)

        # Fill sync (authenticated, graceful skip)
        if now - last_fills >= settings.poll_fills_sec:
            try:
                from src.positions import sync_deribit_fills, sync_binance_fills
                asyncio.run(sync_deribit_fills())
                asyncio.run(sync_binance_fills())
                last_fills = time.time()
            except Exception as e:
                logger.debug(f"Fill sync error: {e}")

        time.sleep(1)


def start_polling():
    """Start background polling thread."""
    global _polling_active, _poll_thread
    if _polling_active:
        return
    init_schema()
    _polling_active = True
    _poll_thread = threading.Thread(target=_poll_loop, daemon=True)
    _poll_thread.start()
    logger.info("Polling started")


def stop_polling():
    """Stop background polling."""
    global _polling_active
    _polling_active = False
    logger.info("Polling stopped")


def is_polling():
    return _polling_active
