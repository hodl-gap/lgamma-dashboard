import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _get_secret(key: str, default: str = "") -> str:
    """Read from Streamlit Cloud secrets first, then fall back to env vars."""
    try:
        import streamlit as st
        return st.secrets.get(key, os.getenv(key, default))
    except Exception:
        return os.getenv(key, default)


class Settings:
    # Deribit
    deribit_url = "https://www.deribit.com/api/v2"
    deribit_client_id = _get_secret("DERIBIT_CLIENT_ID")
    deribit_client_secret = _get_secret("DERIBIT_CLIENT_SECRET")

    # Binance
    binance_url = "https://fapi.binance.com"
    binance_api_key = _get_secret("BINANCE_API_KEY")
    binance_api_secret = _get_secret("BINANCE_API_SECRET")

    # Database
    db_path = str(BASE_DIR / "lgamma.duckdb")

    # Polling intervals (seconds)
    poll_price_sec = 5
    poll_chain_sec = 15
    poll_fills_sec = 30

    # Pricing defaults
    risk_free_rate = 0.0
    dividend_yield = 0.0
    strike_range_pct = 0.10

    # EOD snapshot
    eod_utc_hour = 8  # 08:00 UTC = Deribit settlement

    # Alerts
    vol_alert_threshold_pct = 0.02

    # Concurrency
    max_concurrent_requests = 20


settings = Settings()
