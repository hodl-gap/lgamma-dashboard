import duckdb
import threading
from src.config import settings

_lock = threading.Lock()


def get_conn():
    """Get a DuckDB connection. Thread-safe via lock."""
    return duckdb.connect(settings.db_path)


def execute(sql, params=None):
    """Execute SQL with thread-safe locking."""
    with _lock:
        conn = get_conn()
        try:
            if params:
                conn.execute(sql, params)
            else:
                conn.execute(sql)
        finally:
            conn.close()


def query(sql, params=None):
    """Query and return as list of dicts."""
    with _lock:
        conn = get_conn()
        try:
            if params:
                result = conn.execute(sql, params)
            else:
                result = conn.execute(sql)
            columns = [desc[0] for desc in result.description]
            rows = result.fetchall()
            return [dict(zip(columns, row)) for row in rows]
        finally:
            conn.close()


def query_df(sql, params=None):
    """Query and return as polars DataFrame (or pandas if polars unavailable)."""
    with _lock:
        conn = get_conn()
        try:
            if params:
                return conn.execute(sql, params).fetchdf()
            return conn.execute(sql).fetchdf()
        finally:
            conn.close()


def init_schema():
    """Create all tables if they don't exist."""
    conn = get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS market_data (
                id              INTEGER PRIMARY KEY DEFAULT nextval('market_data_seq'),
                timestamp       TIMESTAMPTZ DEFAULT now(),
                perp_price      DOUBLE NOT NULL,
                funding_rate    DOUBLE,
                deribit_index   DOUBLE NOT NULL,
                basis           DOUBLE,
                basis_pct       DOUBLE,
                is_eod          BOOLEAN DEFAULT FALSE
            )
        """)
    except duckdb.CatalogException:
        conn.execute("CREATE SEQUENCE IF NOT EXISTS market_data_seq START 1")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS market_data (
                id              INTEGER PRIMARY KEY DEFAULT nextval('market_data_seq'),
                timestamp       TIMESTAMPTZ DEFAULT now(),
                perp_price      DOUBLE NOT NULL,
                funding_rate    DOUBLE,
                deribit_index   DOUBLE NOT NULL,
                basis           DOUBLE,
                basis_pct       DOUBLE,
                is_eod          BOOLEAN DEFAULT FALSE
            )
        """)

    conn.execute("CREATE SEQUENCE IF NOT EXISTS option_chain_seq START 1")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS option_chain_raw (
            id                  INTEGER PRIMARY KEY DEFAULT nextval('option_chain_seq'),
            timestamp           TIMESTAMPTZ DEFAULT now(),
            instrument_name     VARCHAR NOT NULL,
            expiry_date         TIMESTAMPTZ NOT NULL,
            days_to_expiry      INTEGER NOT NULL,
            time_to_expiry      DOUBLE NOT NULL,
            strike_price        DOUBLE NOT NULL,
            option_type         VARCHAR NOT NULL,
            best_bid            DOUBLE,
            best_ask            DOUBLE,
            mid_price           DOUBLE,
            mark_price          DOUBLE,
            exchange_mark_iv    DOUBLE,
            bid_iv              DOUBLE,
            ask_iv              DOUBLE,
            underlying_price    DOUBLE NOT NULL
        )
    """)

    conn.execute("CREATE SEQUENCE IF NOT EXISTS vol_surface_seq START 1")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vol_surface (
            id                  INTEGER PRIMARY KEY DEFAULT nextval('vol_surface_seq'),
            timestamp           TIMESTAMPTZ DEFAULT now(),
            instrument_name     VARCHAR NOT NULL,
            expiry_date         TIMESTAMPTZ NOT NULL,
            strike_price        DOUBLE NOT NULL,
            option_type         VARCHAR NOT NULL,
            market_bid_iv       DOUBLE,
            market_ask_iv       DOUBLE,
            custom_iv           DOUBLE NOT NULL,
            prev_custom_iv      DOUBLE,
            theo_price_btc      DOUBLE,
            theo_price_usd      DOUBLE,
            market_mid_usd      DOUBLE,
            price_spread        DOUBLE,
            delta               DOUBLE,
            gamma               DOUBLE,
            vega                DOUBLE,
            theta               DOUBLE,
            cash_delta_usd      DOUBLE,
            cash_gamma_usd      DOUBLE,
            cash_vega_usd       DOUBLE,
            cash_theta_usd      DOUBLE,
            cash_delta_btc      DOUBLE,
            cash_gamma_btc      DOUBLE,
            cash_vega_btc       DOUBLE,
            cash_theta_btc      DOUBLE
        )
    """)

    conn.execute("CREATE SEQUENCE IF NOT EXISTS vol_params_seq START 1")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vol_params (
            id                  INTEGER PRIMARY KEY DEFAULT nextval('vol_params_seq'),
            timestamp           TIMESTAMPTZ DEFAULT now(),
            expiry_date         TIMESTAMPTZ NOT NULL,
            atm_vol             DOUBLE NOT NULL,
            base_skew           DOUBLE NOT NULL,
            base_smile          DOUBLE NOT NULL,
            put_shift           DOUBLE NOT NULL,
            call_shift          DOUBLE NOT NULL,
            atm_strike          DOUBLE NOT NULL,
            effective_skew      DOUBLE,
            effective_smile     DOUBLE,
            model_type          VARCHAR DEFAULT 'parametric',
            svi_a               DOUBLE,
            svi_b               DOUBLE,
            svi_rho             DOUBLE,
            svi_m               DOUBLE,
            svi_sigma           DOUBLE,
            is_active           BOOLEAN DEFAULT TRUE
        )
    """)

    conn.execute("CREATE SEQUENCE IF NOT EXISTS positions_seq START 1")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id                  INTEGER PRIMARY KEY DEFAULT nextval('positions_seq'),
            exchange            VARCHAR NOT NULL,
            instrument_name     VARCHAR NOT NULL,
            instrument_type     VARCHAR NOT NULL,
            expiry_date         TIMESTAMPTZ,
            strike_price        DOUBLE,
            side                VARCHAR NOT NULL,
            size                DOUBLE NOT NULL,
            avg_entry_price     DOUBLE NOT NULL,
            opened_at           TIMESTAMPTZ DEFAULT now(),
            last_updated        TIMESTAMPTZ DEFAULT now(),
            is_open             BOOLEAN DEFAULT TRUE
        )
    """)

    conn.execute("CREATE SEQUENCE IF NOT EXISTS trade_log_seq START 1")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trade_log (
            id                  INTEGER PRIMARY KEY DEFAULT nextval('trade_log_seq'),
            exchange_trade_id   VARCHAR,
            exchange            VARCHAR NOT NULL,
            timestamp           TIMESTAMPTZ NOT NULL,
            instrument_name     VARCHAR NOT NULL,
            instrument_type     VARCHAR NOT NULL,
            expiry_date         TIMESTAMPTZ,
            strike_price        DOUBLE,
            side                VARCHAR NOT NULL,
            size                DOUBLE NOT NULL,
            price               DOUBLE NOT NULL,
            fee                 DOUBLE,
            underlying_at_trade DOUBLE,
            notional_usd        DOUBLE,
            position_id         INTEGER,
            source              VARCHAR DEFAULT 'api'
        )
    """)

    conn.execute("CREATE SEQUENCE IF NOT EXISTS eod_snapshots_seq START 1")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS eod_snapshots (
            id                  INTEGER PRIMARY KEY DEFAULT nextval('eod_snapshots_seq'),
            snapshot_date       DATE NOT NULL,
            snapshot_timestamp  TIMESTAMPTZ NOT NULL,
            position_id         INTEGER,
            instrument_name     VARCHAR NOT NULL,
            instrument_type     VARCHAR NOT NULL,
            underlying_price    DOUBLE NOT NULL,
            close_price         DOUBLE,
            strike_price        DOUBLE,
            days_to_expiry      INTEGER,
            size                DOUBLE NOT NULL,
            theo_price          DOUBLE,
            iv                  DOUBLE,
            delta               DOUBLE,
            gamma               DOUBLE,
            vega                DOUBLE,
            theta               DOUBLE,
            cash_delta_usd      DOUBLE,
            cash_gamma_usd      DOUBLE,
            cash_vega_usd       DOUBLE,
            cash_theta_usd      DOUBLE,
            trading_pnl         DOUBLE,
            delta_pnl           DOUBLE,
            gamma_pnl           DOUBLE,
            vega_pnl            DOUBLE,
            theta_pnl           DOUBLE,
            basis_pnl           DOUBLE,
            theo_pnl            DOUBLE,
            market_pnl          DOUBLE,
            risk_free_rate      DOUBLE,
            time_to_expiry      DOUBLE
        )
    """)

    conn.execute("CREATE SEQUENCE IF NOT EXISTS vol_history_seq START 1")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vol_history (
            id                  INTEGER PRIMARY KEY DEFAULT nextval('vol_history_seq'),
            snapshot_date       DATE NOT NULL,
            snapshot_timestamp  TIMESTAMPTZ NOT NULL,
            expiry_date         TIMESTAMPTZ NOT NULL,
            strike_price        DOUBLE NOT NULL,
            fitted_iv           DOUBLE NOT NULL,
            atm_vol             DOUBLE,
            base_skew           DOUBLE,
            base_smile          DOUBLE,
            put_shift           DOUBLE,
            call_shift          DOUBLE,
            model_type          VARCHAR
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS system_config (
            key                 VARCHAR PRIMARY KEY,
            value               VARCHAR NOT NULL,
            updated_at          TIMESTAMPTZ DEFAULT now()
        )
    """)

    # Insert default config if empty
    existing = conn.execute("SELECT count(*) FROM system_config").fetchone()[0]
    if existing == 0:
        conn.execute("""
            INSERT INTO system_config VALUES
                ('risk_free_rate', '0.0', now()),
                ('dividend_yield', '0.0', now()),
                ('strike_range_pct', '0.10', now()),
                ('poll_interval_price_sec', '5', now()),
                ('poll_interval_chain_sec', '15', now()),
                ('poll_interval_fills_sec', '30', now()),
                ('eod_snapshot_utc_hour', '8', now()),
                ('vol_alert_threshold_pct', '0.02', now())
        """)

    conn.close()


def get_config(key, default=None):
    rows = query("SELECT value FROM system_config WHERE key = ?", [key])
    if rows:
        return rows[0]["value"]
    return default


def set_config(key, value):
    execute("DELETE FROM system_config WHERE key = ?", [key])
    execute("INSERT INTO system_config VALUES (?, ?, now())", [key, str(value)])
