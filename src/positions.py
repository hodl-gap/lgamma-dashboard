import logging
import hmac
import hashlib
import time
import aiohttp
from datetime import datetime, timezone

from src.config import settings
from src.db import get_conn, query

logger = logging.getLogger(__name__)


async def sync_deribit_fills():
    """Pull fills from Deribit authenticated API. Graceful skip if auth fails."""
    if not settings.deribit_client_id or not settings.deribit_client_secret:
        return

    try:
        async with aiohttp.ClientSession() as session:
            # Authenticate
            auth_resp = await session.get(f"{settings.deribit_url}/public/auth", params={
                "client_id": settings.deribit_client_id,
                "client_secret": settings.deribit_client_secret,
                "grant_type": "client_credentials",
            })
            auth_data = await auth_resp.json()

            if "error" in auth_data:
                logger.debug(f"Deribit auth skipped: {auth_data['error'].get('message', 'unknown')}")
                return

            token = auth_data["result"]["access_token"]

            # Fetch fills
            resp = await session.get(
                f"{settings.deribit_url}/private/get_user_trades_by_currency",
                params={"currency": "BTC", "count": 100},
                headers={"Authorization": f"Bearer {token}"},
            )
            data = await resp.json()
            fills = data.get("result", {}).get("trades", [])

            conn = get_conn()
            try:
                for fill in fills:
                    trade_id = fill.get("trade_id")
                    # Dedup
                    existing = conn.execute(
                        "SELECT 1 FROM trade_log WHERE exchange = 'deribit' AND exchange_trade_id = ?",
                        [str(trade_id)]
                    ).fetchone()
                    if existing:
                        continue

                    instrument = fill["instrument_name"]
                    parts = instrument.split("-")
                    inst_type = "perp"
                    if len(parts) == 4:
                        inst_type = "call" if parts[3] == "C" else "put"

                    side = "buy" if fill["direction"] == "buy" else "sell"
                    price = fill.get("price", 0)
                    amount = fill.get("amount", 0)
                    fee = fill.get("fee", 0)
                    underlying = fill.get("index_price", 0)
                    ts = datetime.fromtimestamp(fill["timestamp"] / 1000, tz=timezone.utc)

                    conn.execute("""
                        INSERT INTO trade_log (exchange_trade_id, exchange, timestamp, instrument_name,
                            instrument_type, side, size, price, fee, underlying_at_trade, source)
                        VALUES (?, 'deribit', ?, ?, ?, ?, ?, ?, ?, ?, 'api')
                    """, [str(trade_id), ts, instrument, inst_type, side, amount, price, fee, underlying])

                    # Update position
                    _update_position(conn, "deribit", instrument, inst_type, side, amount, price)

                logger.info(f"Synced {len(fills)} Deribit fills")
            finally:
                conn.close()

    except Exception as e:
        logger.debug(f"Deribit fill sync skipped: {e}")


async def sync_binance_fills():
    """Pull fills from Binance authenticated API. Graceful skip if auth fails."""
    if not settings.binance_api_key or not settings.binance_api_secret:
        return

    try:
        timestamp = int(time.time() * 1000)
        query_str = f"symbol=BTCUSDT&limit=100&timestamp={timestamp}"
        signature = hmac.new(
            settings.binance_api_secret.encode(), query_str.encode(), hashlib.sha256
        ).hexdigest()

        async with aiohttp.ClientSession() as session:
            resp = await session.get(
                f"{settings.binance_url}/fapi/v1/userTrades?{query_str}&signature={signature}",
                headers={"X-MBX-APIKEY": settings.binance_api_key},
            )

            if resp.status != 200:
                logger.debug(f"Binance fill sync skipped: HTTP {resp.status}")
                return

            fills = await resp.json()

            conn = get_conn()
            try:
                for fill in fills:
                    trade_id = str(fill.get("id"))
                    existing = conn.execute(
                        "SELECT 1 FROM trade_log WHERE exchange = 'binance' AND exchange_trade_id = ?",
                        [trade_id]
                    ).fetchone()
                    if existing:
                        continue

                    side = "buy" if fill["side"] == "BUY" else "sell"
                    price = float(fill["price"])
                    qty = float(fill["qty"])
                    fee = float(fill.get("commission", 0))
                    ts = datetime.fromtimestamp(fill["time"] / 1000, tz=timezone.utc)

                    conn.execute("""
                        INSERT INTO trade_log (exchange_trade_id, exchange, timestamp, instrument_name,
                            instrument_type, side, size, price, fee, notional_usd, source)
                        VALUES (?, 'binance', ?, 'BTCUSDT', 'perp', ?, ?, ?, ?, ?, 'api')
                    """, [trade_id, ts, side, qty, price, fee, qty * price])

                    _update_position(conn, "binance", "BTCUSDT", "perp", side, qty, price)

                logger.info(f"Synced {len(fills)} Binance fills")
            finally:
                conn.close()

    except Exception as e:
        logger.debug(f"Binance fill sync skipped: {e}")


def _update_position(conn, exchange, instrument, inst_type, side, size, price):
    """Update or create position from a fill."""
    existing = conn.execute("""
        SELECT * FROM positions
        WHERE exchange = ? AND instrument_name = ? AND is_open = TRUE
    """, [exchange, instrument]).fetchone()

    if existing:
        desc = conn.execute("SELECT * FROM positions LIMIT 0").description
        cols = [d[0] for d in desc]
        pos = dict(zip(cols, existing))

        pos_side_mult = 1.0 if pos["side"] == "long" else -1.0
        fill_side_mult = 1.0 if side == "buy" else -1.0
        current_signed = pos["size"] * pos_side_mult
        fill_signed = size * fill_side_mult
        new_signed = current_signed + fill_signed

        if abs(new_signed) < 1e-10:
            # Position closed
            conn.execute("UPDATE positions SET is_open = FALSE, last_updated = now() WHERE id = ?", [pos["id"]])
        elif (new_signed > 0) == (current_signed > 0):
            # Same direction: increase size, update avg price
            new_size = abs(new_signed)
            new_avg = (pos["avg_entry_price"] * pos["size"] + price * size) / (pos["size"] + size)
            conn.execute("""
                UPDATE positions SET size = ?, avg_entry_price = ?, last_updated = now()
                WHERE id = ?
            """, [new_size, new_avg, pos["id"]])
        else:
            # Direction flip
            conn.execute("UPDATE positions SET is_open = FALSE, last_updated = now() WHERE id = ?", [pos["id"]])
            new_side = "long" if new_signed > 0 else "short"
            conn.execute("""
                INSERT INTO positions (exchange, instrument_name, instrument_type, side, size, avg_entry_price, is_open)
                VALUES (?, ?, ?, ?, ?, ?, TRUE)
            """, [exchange, instrument, inst_type, new_side, abs(new_signed), price])
    else:
        # New position
        pos_side = "long" if side == "buy" else "short"

        # Parse expiry/strike from instrument name for options
        expiry_date = None
        strike_price = None
        if inst_type in ("call", "put"):
            parts = instrument.split("-")
            if len(parts) == 4:
                strike_price = float(parts[2])

        conn.execute("""
            INSERT INTO positions (exchange, instrument_name, instrument_type, expiry_date,
                strike_price, side, size, avg_entry_price, is_open)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, TRUE)
        """, [exchange, instrument, inst_type, expiry_date, strike_price, pos_side, size, price])
