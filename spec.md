# Lgamma BTC Options Trading Dashboard — System Specification

> **Migration**: KOSPI 200 Excel/VBA system (`Lgamma_v1_6.xlsm`) → Web-hosted BTC options trading dashboard  
> **Version**: 1.1 (Reviewed)  
> **Last Updated**: 2026-04-02

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Architecture](#2-architecture)
3. [Data Sources & Ingestion](#3-data-sources--ingestion)
4. [Database Schema](#4-database-schema)
5. [Core Engine: Vol Surface & Pricing](#5-core-engine-vol-surface--pricing)
6. [Position Management & Booking](#6-position-management--booking)
7. [Risk Profile & PnL](#7-risk-profile--pnl)
8. [EOD Snapshot & History](#8-eod-snapshot--history)
9. [Frontend Dashboard](#9-frontend-dashboard)
10. [Alerts & Triggers](#10-alerts--triggers)
11. [API Design](#11-api-design)
12. [Phasing & Milestones](#12-phasing--milestones)
13. [Appendix A: Excel → System Mapping](#appendix-excel--system-mapping)
14. [Appendix B: Reference Repository Analysis](#appendix-b-reference-repository-analysis)

---

## 1. Project Overview

### 1.1 What This System Does

A self-hosted options trading dashboard for **BTC options on Deribit**, hedged with **Binance BTC/USDT perpetual futures**. The system replaces an Excel/VBA workbook that currently handles:

- Fetching option chain data and computing implied volatilities (Brent's method solver)
- Fitting a custom volatility curve with trader-adjustable parameters (ATM vol, skew, smile, put/call shift)
- Computing Black-Scholes theoretical prices and Greeks (delta, gamma, vega, theta)
- Converting Greeks to cash-denominated values (USD primary, BTC secondary)
- Tracking positions, booking trades, and computing realized/unrealized PnL
- Snapshotting EOD state (positions, greeks, vol curve, PnL attribution) to a history database

### 1.2 Final Deliverable

A single Docker Compose stack (`docker-compose up`) that runs on a VPS and provides:

1. **Web dashboard** (React) accessible at `https://<your-domain>/` — 5 tabs: Risk Profile, Vol Curve Editor, Option Chain, Trade Log, History & Analytics
2. **REST API** (FastAPI) at `https://<your-domain>/api/v1/` — all market data, vol surface, positions, risk, and snapshot endpoints
3. **Background services** — polling Deribit + Binance for live data, syncing fills, running EOD snapshots on schedule
4. **PostgreSQL database** — persistent storage for positions, trade history, vol surface snapshots, PnL attribution

The system replaces the Excel workbook entirely. The trader opens the dashboard in a browser, adjusts vol curve parameters, monitors portfolio Greeks and PnL in real-time (15s refresh), and reviews historical performance — all without touching Excel.

### 1.3 Key Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Options exchange | Deribit only | Primary BTC options liquidity venue |
| Hedging exchange | Binance perpetual futures | Deepest BTC perp liquidity |
| Vol curve model | Parametric (MVP) + SVI (upgrade) | Parametric mirrors existing Excel logic; SVI is industry standard |
| Risk-free rate `r` | Configurable, default 0% | No consensus "risk-free" in crypto |
| Dividend yield `q` | Fixed 0% | BTC has no dividends |
| Expiry coverage | All listed Deribit expiries (~8-12) | Unlike KOSPI's 2-expiry limit |
| Cash greeks | USD primary, BTC secondary | USD for P&L intuition, BTC for on-chain accounting |
| Trade booking | API-connected (auto-pull fills) | Deribit + Binance fill APIs |
| Data refresh | Polling (configurable interval) | WebSocket deferred to later phase |
| Stack | Python backend + React frontend | Self-hosted VPS |
| Database | PostgreSQL | Relational, time-series friendly, production-grade |

### 1.4 BTC vs KOSPI: Key Differences

| Aspect | KOSPI 200 (Excel) | BTC (New System) |
|---|---|---|
| Contract type | KRX-listed, KRW-settled | Deribit inverse options (BTC-settled, 1 BTC notional) |
| Multiplier | 250,000 KRW | 1 BTC (≈ USD equivalent at spot) |
| Strike spacing | 2.5pt increments | Variable ($500–$5000 depending on distance from ATM) |
| Expiry count | 2 (near + far month) | 8–12 active at any time |
| Hedging instrument | KOSPI 200 futures | Binance BTC/USDT perpetual |
| Hedging cost | Basis (futures vs spot) | Funding rate (8h periodic) |
| Trading hours | KRX hours (09:00–15:45 KST) | 24/7 |
| Settlement | Cash (KRW) | Physical (BTC) |

---

## 2. Architecture

### 2.1 High-Level Components

```
┌─────────────────────────────────────────────────────────┐
│                    React Frontend                        │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────┐ │
│  │ Vol Curve │ │  Option  │ │   Risk   │ │  History   │ │
│  │  Editor   │ │  Chain   │ │ Profile  │ │  & PnL     │ │
│  └──────────┘ └──────────┘ └──────────┘ └────────────┘ │
└────────────────────────┬────────────────────────────────┘
                         │ REST API / WebSocket
┌────────────────────────┴────────────────────────────────┐
│                   Python Backend (FastAPI)                │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────┐ │
│  │  Data     │ │  Pricing │ │ Position │ │  Snapshot   │ │
│  │ Ingestion │ │  Engine  │ │ Manager  │ │  Service    │ │
│  └──────────┘ └──────────┘ └──────────┘ └────────────┘ │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────┴────────────────────────────────┐
│                    PostgreSQL                             │
│  market_data · option_chain · vol_surface · positions    │
│  trade_log · eod_snapshots · vol_history                 │
└─────────────────────────────────────────────────────────┘
         ▲                              ▲
    Deribit API                   Binance API
   (options chain,                (BTC/USDT perp
    fills, mark IV)                price, funding
                                   rate, fills)
```

### 2.2 Tech Stack

| Layer | Technology |
|---|---|
| Backend framework | FastAPI (Python 3.11+) |
| Pricing/math | NumPy (vectorized), SciPy (`norm.cdf`, `norm.pdf`, `brentq` for IV solver) |
| Database | PostgreSQL 16 + SQLAlchemy ORM |
| Task scheduler | APScheduler (polling loops, EOD snapshots) |
| Frontend | React 18 + TypeScript |
| Charting | Recharts or Lightweight Charts (vol curve), AG Grid (option chain table) |
| Deployment | Docker Compose on VPS (Nginx reverse proxy) |

### 2.3 Polling Architecture

The system uses a configurable polling loop rather than WebSocket streaming for MVP.

| Data Type | Source | Default Interval | Notes |
|---|---|---|---|
| BTC spot / perp price | Binance REST | 5s | `GET /fapi/v1/ticker/price` |
| Funding rate | Binance REST | 60s | `GET /fapi/v1/fundingRate` (updates every 8h) |
| Option chain (all expiries) | Deribit REST | 15s | `GET /public/get_book_summary_by_currency` |
| Option orderbook (per instrument) | Deribit REST | 15s | `GET /public/get_order_book` |
| Trade fills (positions) | Deribit + Binance REST | 30s | Authenticated endpoints |

All intervals are configurable via environment variables or admin UI.

### 2.4 Authentication & API Key Management

**Exchange API keys** (Deribit + Binance):
- Stored as environment variables (`DERIBIT_API_KEY`, `DERIBIT_API_SECRET`, `BINANCE_API_KEY`, `BINANCE_API_SECRET`)
- Loaded via `.env` file (gitignored) or Docker secrets in production
- Minimum required scopes: Deribit `trade:read`, `account:read`; Binance futures `read-only`
- Never stored in database or logged

**Dashboard access**:
- Single-user system (self-hosted) — no multi-user auth for MVP
- Optional: HTTP Basic Auth via Nginx reverse proxy for VPS exposure
- Phase 4: Add proper auth if multi-user access is needed

---

## 3. Data Sources & Ingestion

### 3.1 Deribit API

**Base URL**: `https://www.deribit.com/api/v2`

#### 3.1.1 Option Chain Retrieval

```
GET /public/get_instruments?currency=BTC&kind=option&expired=false
```
Returns all active BTC option instruments. From each instrument, extract:
- `instrument_name` (e.g., `BTC-28MAR26-95000-C`)
- `expiration_timestamp` → `expiry_date`
- `strike` → `strike_price`
- `option_type` → `call` / `put`

#### 3.1.2 Orderbook / Quotes

```
GET /public/get_order_book?instrument_name={name}&depth=1
```
Extract per instrument:
- `best_bid_price`, `best_ask_price` (in BTC, as fraction of underlying)
- `mark_iv` (Deribit's mark implied volatility, as percentage e.g. 55.2)
- `underlying_price` (Deribit's BTC index)
- `mark_price`

**Important**: Deribit quotes option prices as a fraction of BTC (e.g., 0.0345 = 3.45% of 1 BTC). To get USD price: `price_usd = price_btc_fraction * underlying_price`.

#### 3.1.3 Strike Filtering

Mirror the Excel logic: only fetch strikes within **±10% of current BTC price** (configurable). For a $95,000 BTC, this means strikes from ~$85,500 to ~$104,500.

```python
lower_bound = underlying_price * (1 - strike_range_pct)  # default 0.10
upper_bound = underlying_price * (1 + strike_range_pct)
filtered = [i for i in instruments if lower_bound <= i.strike <= upper_bound]
```

#### 3.1.4 Trade Fills (Authenticated)

```
GET /private/get_user_trades_by_currency?currency=BTC&count=100
```
Requires API key with `trade:read` scope. Used for auto-booking positions.

### 3.2 Binance API

**Base URL**: `https://fapi.binance.com`

#### 3.2.1 Perpetual Futures Price

```
GET /fapi/v1/ticker/price?symbol=BTCUSDT
```
Returns `price` — the current BTC/USDT perpetual futures price. This is the **hedging instrument price** (equivalent to KOSPI futures price in the Excel).

#### 3.2.2 Funding Rate

```
GET /fapi/v1/fundingRate?symbol=BTCUSDT&limit=1
```
Returns the current funding rate. This replaces the KOSPI "REPO" concept — it represents the ongoing cost of holding a perp hedge.

#### 3.2.3 Trade Fills (Authenticated)

```
GET /fapi/v1/userTrades?symbol=BTCUSDT&limit=100
```
Requires API key with futures read permission.

### 3.3 Data Ingestion Service

A background service (`DataIngestionService`) manages all polling loops.

**Concurrent orderbook fetching** (adopted from `schepal/deribit_data_collector`): Per-instrument orderbook requests are dispatched concurrently using `asyncio.gather()` with a semaphore to respect Deribit's rate limits (~20 requests/sec for public endpoints). This is critical because a full chain fetch (8-12 expiries × 20+ strikes × 2 types) requires 300+ individual orderbook calls.

```python
class DataIngestionService:
    def __init__(self, max_concurrent_requests: int = 20):
        self.semaphore = asyncio.Semaphore(max_concurrent_requests)
    
    async def _fetch_orderbook(self, session, instrument_name: str):
        """Fetch single orderbook with rate-limit semaphore."""
        async with self.semaphore:
            resp = await session.get(f"{DERIBIT_URL}/public/get_order_book",
                                     params={"instrument_name": instrument_name, "depth": 1})
            return await resp.json()
    
    async def poll_deribit_chain(self):
        """Every 15s: fetch instruments list, then all orderbooks concurrently."""
        instruments = await self._fetch_instruments()
        async with aiohttp.ClientSession() as session:
            tasks = [self._fetch_orderbook(session, i["instrument_name"]) 
                     for i in instruments]
            results = await asyncio.gather(*tasks, return_exceptions=True)
        # Process results, skip any failed fetches
    
    async def poll_binance_price(self):
        """Every 5s: fetch BTC/USDT perp price + funding rate"""
    
    async def poll_trade_fills(self):
        """Every 30s: fetch new fills from Deribit + Binance"""
    
    async def run(self):
        """Start all polling loops via APScheduler"""
```

---

## 4. Database Schema

### 4.1 `market_data` — Underlying Price Snapshots

Replaces: Excel `Risk Profile` sheet cells B4–B6 (index price, futures price, funding rate)

One row per snapshot with all sources captured together (no half-NULL columns):

```sql
CREATE TABLE market_data (
    id              BIGSERIAL PRIMARY KEY,
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- Binance perp data (polled every 5s)
    perp_price      NUMERIC(16,2) NOT NULL,          -- BTC/USDT perpetual price
    funding_rate    NUMERIC(12,8),                    -- Current 8h funding rate (polled separately, may lag)
    
    -- Deribit index (extracted from option chain poll, every 15s)
    deribit_index   NUMERIC(16,2) NOT NULL,           -- Deribit BTC index price
    
    -- Derived
    basis           NUMERIC(16,2),                    -- perp_price - deribit_index
    basis_pct       NUMERIC(8,6),                     -- basis / deribit_index
    
    -- Snapshot flag
    is_eod          BOOLEAN DEFAULT FALSE             -- True for daily close snapshot
);

CREATE INDEX idx_market_data_ts ON market_data (timestamp DESC);
```

> **Note**: Each row captures a consistent snapshot from both sources. The ingestion service
> waits for both Binance price and Deribit index before writing a row. Funding rate updates
> less frequently (every 8h) — the latest known value is carried forward.

### 4.2 `option_chain_raw` — Raw Option Quotes

Replaces: Excel `KOSPI` sheet columns C–I (bid/ask/close per strike), `HVOL` sheet columns B–D (bid vol, ask vol per strike)

```sql
CREATE TABLE option_chain_raw (
    id                  BIGSERIAL PRIMARY KEY,
    timestamp           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    instrument_name     VARCHAR(50) NOT NULL,        -- 'BTC-28MAR26-95000-C'
    
    -- Instrument metadata (denormalized for query speed)
    expiry_date         TIMESTAMPTZ NOT NULL,
    days_to_expiry      INTEGER NOT NULL,             -- expiry - today
    time_to_expiry      NUMERIC(10,8) NOT NULL,       -- in years (days/365)
    strike_price        NUMERIC(16,2) NOT NULL,
    option_type         VARCHAR(4) NOT NULL,           -- 'call' | 'put'
    
    -- Quote data (prices in BTC fraction)
    best_bid            NUMERIC(16,8),
    best_ask            NUMERIC(16,8),
    mid_price           NUMERIC(16,8),                -- (bid + ask) / 2
    mark_price          NUMERIC(16,8),                -- Deribit's mark price
    
    -- Exchange-provided IV (reference only)
    exchange_mark_iv    NUMERIC(8,4),                 -- Deribit mark IV (e.g., 0.552 = 55.2%)
    
    -- Our computed IV (Brent's method from bid/ask prices)
    bid_iv              NUMERIC(8,6),                 -- IV implied from bid price
    ask_iv              NUMERIC(8,6),                 -- IV implied from ask price
    
    -- Underlying at time of capture
    underlying_price    NUMERIC(16,2) NOT NULL,
    
    UNIQUE (timestamp, instrument_name)
);

CREATE INDEX idx_chain_ts ON option_chain_raw (timestamp DESC);
CREATE INDEX idx_chain_expiry ON option_chain_raw (expiry_date, strike_price);
```

**Data retention**: At 15s intervals × ~300 instruments, this table grows ~1.7M rows/day (~50M rows/month). Retention policy:
- **Hot** (last 7 days): Full resolution, kept in main table for real-time queries
- **Warm** (8-90 days): Downsample to 5-minute snapshots via scheduled job, delete intermediate rows
- **Cold** (90+ days): Only EOD snapshots retained (in `eod_snapshots` + `vol_history`)

Implemented as a nightly cleanup job in APScheduler:
```sql
-- Delete rows older than 7 days that aren't on 5-min boundaries
DELETE FROM option_chain_raw
WHERE timestamp < NOW() - INTERVAL '7 days'
  AND EXTRACT(EPOCH FROM timestamp)::int % 300 != 0;

-- Delete rows older than 90 days entirely (EOD data preserved in snapshot tables)
DELETE FROM option_chain_raw
WHERE timestamp < NOW() - INTERVAL '90 days';
```

### 4.3 `vol_surface` — Custom Fitted Vol Surface (Core Table)

Replaces: Excel `VolParameters` sheet columns G–K (strike, bid vol, ask vol, fitted vol, prev IV) + `KOSPI` sheet columns O–AA (greeks, cash greeks)

```sql
CREATE TABLE vol_surface (
    id                  BIGSERIAL PRIMARY KEY,
    timestamp           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    instrument_name     VARCHAR(50) NOT NULL,
    
    -- Expiry group
    expiry_date         TIMESTAMPTZ NOT NULL,
    strike_price        NUMERIC(16,2) NOT NULL,
    option_type         VARCHAR(4) NOT NULL,
    
    -- Vol surface values
    market_bid_iv       NUMERIC(8,6),                 -- From option_chain_raw
    market_ask_iv       NUMERIC(8,6),                 -- From option_chain_raw
    custom_iv           NUMERIC(8,6) NOT NULL,         -- Our fitted vol curve output
    prev_custom_iv      NUMERIC(8,6),                  -- Previous snapshot's custom_iv
    
    -- Pricing
    theo_price_btc      NUMERIC(16,8),                -- BS theo price (BTC fraction)
    theo_price_usd      NUMERIC(16,2),                -- BS theo price (USD)
    market_mid_usd      NUMERIC(16,2),                -- Market mid price (USD)
    price_spread        NUMERIC(16,2),                -- market_mid - theo (USD) → trade signal
    
    -- Pure Greeks (per 1 contract = 1 BTC notional)
    delta               NUMERIC(12,8),
    gamma               NUMERIC(12,8),
    vega                NUMERIC(12,8),                -- Per 1% vol move
    theta               NUMERIC(12,8),                -- Per 1 day
    
    -- Cash Greeks (USD)
    cash_delta_usd      NUMERIC(16,2),                -- delta * underlying * contract_size
    cash_gamma_usd      NUMERIC(16,2),                -- gamma * underlying^2 * contract_size / 100
    cash_vega_usd       NUMERIC(16,2),                -- vega * contract_size (already per 1% move)
    cash_theta_usd      NUMERIC(16,2),                -- theta * contract_size
    
    -- Cash Greeks (BTC)
    cash_delta_btc      NUMERIC(16,8),
    cash_gamma_btc      NUMERIC(16,8),
    cash_vega_btc       NUMERIC(16,8),
    cash_theta_btc      NUMERIC(16,8),
    
    UNIQUE (timestamp, instrument_name)
);

CREATE INDEX idx_vol_ts ON vol_surface (timestamp DESC);
CREATE INDEX idx_vol_expiry ON vol_surface (expiry_date, strike_price);
```

### 4.4 `vol_params` — Trader-Set Vol Curve Parameters

Replaces: Excel `VolParameters` sheet cells B2–B8 (ATM vol, base skew, base smile, put shift, call shift, ATM strike, maturity)

```sql
CREATE TABLE vol_params (
    id                  BIGSERIAL PRIMARY KEY,
    timestamp           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expiry_date         TIMESTAMPTZ NOT NULL,          -- One param set per expiry
    
    -- Trader-adjustable parameters
    atm_vol             NUMERIC(8,6) NOT NULL,         -- ATM implied volatility
    base_skew           NUMERIC(10,4) NOT NULL,        -- Skew coefficient (e.g., -2.5)
    base_smile          NUMERIC(10,4) NOT NULL,        -- Smile coefficient (e.g., 10)
    put_shift           NUMERIC(10,6) NOT NULL,        -- OTM put vol adjustment
    call_shift          NUMERIC(10,6) NOT NULL,        -- OTM call vol adjustment
    atm_strike          NUMERIC(16,2) NOT NULL,        -- ATM strike reference
    
    -- Computed (derived from base params + maturity)
    effective_skew      NUMERIC(10,6),                 -- 0.2 * base_skew * sqrt(T)
    effective_smile     NUMERIC(10,6),                 -- 0.04 * base_smile * T
    
    -- Model type
    model_type          VARCHAR(20) DEFAULT 'parametric', -- 'parametric' | 'svi'
    
    -- SVI parameters (Phase 2)
    svi_a               NUMERIC(12,8),
    svi_b               NUMERIC(12,8),
    svi_rho             NUMERIC(12,8),
    svi_m               NUMERIC(12,8),
    svi_sigma           NUMERIC(12,8),
    
    is_active           BOOLEAN DEFAULT TRUE,          -- Current active param set
    
    UNIQUE (expiry_date, is_active) -- Only one active param set per expiry
);
```

### 4.5 `positions` — Current Live Positions

Replaces: Excel `Risk Profile` sheet rows 22+ (position table)

```sql
CREATE TABLE positions (
    id                  BIGSERIAL PRIMARY KEY,
    
    -- Instrument identification
    exchange            VARCHAR(20) NOT NULL,           -- 'deribit' | 'binance'
    instrument_name     VARCHAR(50) NOT NULL,           -- 'BTC-28MAR26-95000-C' or 'BTCUSDT'
    instrument_type     VARCHAR(10) NOT NULL,           -- 'call' | 'put' | 'perp'
    expiry_date         TIMESTAMPTZ,                    -- NULL for perpetuals
    strike_price        NUMERIC(16,2),                  -- NULL for perpetuals
    
    -- Position state
    side                VARCHAR(5) NOT NULL,            -- 'long' | 'short'
    size                NUMERIC(16,8) NOT NULL,         -- In contracts (BTC for Deribit, contracts for Binance)
    avg_entry_price     NUMERIC(16,8) NOT NULL,         -- Weighted average entry
    
    -- Timestamps
    opened_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_updated        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- Status
    is_open             BOOLEAN DEFAULT TRUE
);

-- Partial unique index: only one OPEN position per instrument per exchange.
-- Allows unlimited closed rows (re-opening same instrument after close).
CREATE UNIQUE INDEX idx_positions_one_open
ON positions (exchange, instrument_name) WHERE is_open = TRUE;
```

### 4.6 `trade_log` — Individual Fill Records

Replaces: Excel `Booking` sheet (매매일자, Book, instrument, 매수/매도, Lot, 매매가, etc.)

```sql
CREATE TABLE trade_log (
    id                  BIGSERIAL PRIMARY KEY,
    
    -- Trade identification
    exchange_trade_id   VARCHAR(100),                   -- Exchange-assigned trade ID
    exchange            VARCHAR(20) NOT NULL,
    timestamp           TIMESTAMPTZ NOT NULL,
    
    -- Instrument
    instrument_name     VARCHAR(50) NOT NULL,
    instrument_type     VARCHAR(10) NOT NULL,
    expiry_date         TIMESTAMPTZ,
    strike_price        NUMERIC(16,2),
    
    -- Trade details
    side                VARCHAR(5) NOT NULL,            -- 'buy' | 'sell'
    size                NUMERIC(16,8) NOT NULL,
    price               NUMERIC(16,8) NOT NULL,         -- Fill price
    fee                 NUMERIC(16,8),                   -- Exchange fee
    
    -- USD equivalent at time of trade
    underlying_at_trade NUMERIC(16,2),
    notional_usd        NUMERIC(16,2),
    
    -- Linked position
    position_id         INTEGER REFERENCES positions(id),
    
    -- Source
    source              VARCHAR(20) DEFAULT 'api',      -- 'api' | 'manual'
    
    UNIQUE (exchange, exchange_trade_id)
);
```

### 4.7 `eod_snapshots` — End-of-Day Position Snapshots

Replaces: Excel `History_DB` sheet (Date, UID, 종류, 수량, 종가, Greeks, Cash Greeks, PnL attribution)

```sql
CREATE TABLE eod_snapshots (
    id                  BIGSERIAL PRIMARY KEY,
    snapshot_date       DATE NOT NULL,
    snapshot_timestamp  TIMESTAMPTZ NOT NULL,
    
    -- Position reference
    position_id         INTEGER REFERENCES positions(id),
    instrument_name     VARCHAR(50) NOT NULL,
    instrument_type     VARCHAR(10) NOT NULL,
    
    -- Market state
    underlying_price    NUMERIC(16,2) NOT NULL,
    close_price         NUMERIC(16,8),                  -- Option/perp close price
    strike_price        NUMERIC(16,2),
    days_to_expiry      INTEGER,
    
    -- Quantity
    size                NUMERIC(16,8) NOT NULL,
    
    -- Pricing
    theo_price          NUMERIC(16,8),
    iv                  NUMERIC(8,6),
    
    -- Pure Greeks
    delta               NUMERIC(12,8),
    gamma               NUMERIC(12,8),
    vega                NUMERIC(12,8),
    theta               NUMERIC(12,8),
    
    -- Cash Greeks (USD)
    cash_delta_usd      NUMERIC(16,2),
    cash_gamma_usd      NUMERIC(16,2),
    cash_vega_usd       NUMERIC(16,2),
    cash_theta_usd      NUMERIC(16,2),
    
    -- PnL Attribution
    trading_pnl         NUMERIC(16,2),                  -- Realized from today's trades
    delta_pnl           NUMERIC(16,2),                  -- PnL from delta exposure
    gamma_pnl           NUMERIC(16,2),                  -- PnL from gamma (convexity)
    vega_pnl            NUMERIC(16,2),                  -- PnL from vol change
    theta_pnl           NUMERIC(16,2),                  -- PnL from time decay
    basis_pnl           NUMERIC(16,2),                  -- PnL from futures basis
    theo_pnl            NUMERIC(16,2),                  -- Theo price based total PnL
    market_pnl          NUMERIC(16,2),                  -- Market price based total PnL
    
    -- Parameters at snapshot
    risk_free_rate      NUMERIC(8,6),
    time_to_expiry      NUMERIC(10,8),
    
    UNIQUE (snapshot_date, instrument_name)
);
```

### 4.8 `vol_history` — Historical Vol Curve Snapshots

Replaces: Excel `History_Vol` sheet (Date, Strike, IV)

```sql
CREATE TABLE vol_history (
    id                  BIGSERIAL PRIMARY KEY,
    snapshot_date       DATE NOT NULL,
    snapshot_timestamp  TIMESTAMPTZ NOT NULL,
    
    expiry_date         TIMESTAMPTZ NOT NULL,
    strike_price        NUMERIC(16,2) NOT NULL,
    fitted_iv           NUMERIC(8,6) NOT NULL,
    
    -- Parameter snapshot (denormalized for easy replay)
    atm_vol             NUMERIC(8,6),
    base_skew           NUMERIC(10,4),
    base_smile          NUMERIC(10,4),
    put_shift           NUMERIC(10,6),
    call_shift          NUMERIC(10,6),
    model_type          VARCHAR(20),
    
    UNIQUE (snapshot_date, expiry_date, strike_price)
);
```

### 4.9 `system_config` — Runtime Configuration

```sql
CREATE TABLE system_config (
    key                 VARCHAR(100) PRIMARY KEY,
    value               TEXT NOT NULL,
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

-- Default entries
INSERT INTO system_config VALUES
    ('risk_free_rate', '0.0', NOW()),
    ('dividend_yield', '0.0', NOW()),
    ('strike_range_pct', '0.10', NOW()),
    ('poll_interval_price_sec', '5', NOW()),
    ('poll_interval_chain_sec', '15', NOW()),
    ('poll_interval_fills_sec', '30', NOW()),
    ('eod_snapshot_utc_hour', '8', NOW()),          -- 08:00 UTC = Deribit settlement time = 17:00 KST
    ('vol_alert_threshold_pct', '0.02', NOW());    -- 2% IV divergence alert
```

---

## 5. Core Engine: Vol Surface & Pricing

### 5.1 Implied Volatility Solver

Replaces: Excel `Module2.bas` → `CalculateImpliedVolForStrikes()` + `CalculateIV_For_Cell()`

Given an observed option price, solve for σ in the Black-Scholes formula using Brent's method (more robust than Newton-Raphson for edge cases).

```python
import numpy as np
from scipy.stats import norm
from scipy.optimize import brentq

def bs_price_vec(S, K, T, r, q, sigma, is_call):
    """
    Vectorized Black-Scholes price for European options.
    All inputs can be NumPy arrays for batch computation.
    
    Adopted from pyBlackScholesAnalytics vectorized pattern — computes
    entire option chain in a single NumPy broadcast pass.
    """
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    call_price = S * np.exp(-q * T) * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    put_price = K * np.exp(-r * T) * norm.cdf(-d2) - S * np.exp(-q * T) * norm.cdf(-d1)
    return np.where(is_call, call_price, put_price)

def implied_vol(price, S, K, T, r, q, option_type, bounds=(0.001, 5.0)):
    """Solve for implied volatility using Brent's method (per-option, scalar)."""
    try:
        is_call = option_type == 'call'
        return brentq(
            lambda sigma: float(bs_price_vec(S, K, T, r, q, sigma, is_call)) - price,
            *bounds, xtol=1e-8, maxiter=200
        )
    except ValueError:
        return None  # No solution in bounds (e.g., price below intrinsic)
```

> **Reference note**: `bs_price_vec` follows the vectorized pattern from
> [`pyBlackScholesAnalytics/options.py:1258-1271`](references/pyBlackScholesAnalytics/pyblackscholesanalytics/options/options.py).
> The scalar `implied_vol` still uses Brent's method per-option since root-finding is inherently sequential.
> For bulk IV computation across a full chain, use `np.vectorize(implied_vol)` or a thread pool.

**Per-strike IV computation** (mirrors Excel `CalculateImpliedVolForStrikes`):

For each instrument in `option_chain_raw`:
1. Convert Deribit BTC-fraction price to USD: `price_usd = mid_price * underlying_price`
2. Compute `bid_iv = implied_vol(bid_usd, S, K, T, r, q, option_type)`
3. Compute `ask_iv = implied_vol(ask_usd, S, K, T, r, q, option_type)`

**OTM filtering** (mirrors Excel `HVOL` sheet logic):
- For strikes **below** ATM → use **Put** bid/ask IV (put is OTM)
- For strikes **above** ATM → use **Call** bid/ask IV (call is OTM)
- For ATM strike → average of call bid, call ask, put bid, put ask IV (4-way average)

### 5.2 Vol Curve Fitting — Parametric Model (MVP)

Replaces: Excel `Module1.bas` → `CalcVolForParams()`

The parametric model computes fitted IV for each strike given 5 trader-adjustable parameters plus the ATM reference:

```python
def calc_vol_parametric(strike, atm_strike, T, atm_vol, base_skew, base_smile, put_shift, call_shift):
    """
    Vectorized parametric vol curve fitting.
    Ported from VBA CalcVolForParams(). All inputs can be NumPy arrays.
    
    Parameters are set per-expiry by the trader.
    """
    sqrt_T = np.sqrt(T)
    log_moneyness = np.log(strike / atm_strike)
    
    denom = atm_vol * sqrt_T
    normalized_strike = np.where(denom == 0, 0.0, log_moneyness / denom)
    
    # Scale base params by maturity
    skew = 0.2 * base_skew * sqrt_T
    smile = 0.04 * base_smile * T
    
    # Core quadratic model
    vol = atm_vol + skew * normalized_strike + smile * normalized_strike**2
    
    # Asymmetric shifts for put/call wings
    vol = vol + np.where(strike < atm_strike, put_shift * np.abs(normalized_strike),
                 np.where(strike > atm_strike, call_shift * normalized_strike, 0.0))
    
    return np.maximum(vol, 0.01)  # Floor at 1% vol
```

**Parameter semantics** (all set per-expiry):

| Parameter | Typical Range | Effect |
|---|---|---|
| `atm_vol` | 0.20–1.50 | Level of the entire curve (parallel shift) |
| `base_skew` | -5.0 to +2.0 | Tilt: negative = OTM puts more expensive (normal) |
| `base_smile` | 0 to 30 | Curvature: higher = more U-shape at wings |
| `put_shift` | -0.05 to +0.10 | Extra vol added to OTM put wing |
| `call_shift` | -0.05 to +0.10 | Extra vol added to OTM call wing |
| `atm_strike` | auto-detected | Reference strike for moneyness (nearest listed strike to spot) |

### 5.3 Vol Curve Fitting — SVI Model (Phase 2)

Gatheral's Stochastic Volatility Inspired (SVI) parameterization of total implied variance:

```
w(k) = a + b * (ρ * (k - m) + sqrt((k - m)² + σ²))
```

where `k = log(K/F)` is log-moneyness, `w = σ²_impl * T` is total variance, and `{a, b, ρ, m, σ}` are 5 parameters.

SVI advantages over the parametric model:
- Guaranteed absence of calendar spread arbitrage (with proper constraints)
- Better fit for deep OTM wings common in BTC
- Industry standard, easier to compare with other desks

#### Calibration: Quasi-Explicit 2-Step Method

Adopted from [`wangys96/SVI-Volatility-Surface-Calibration/svi.py`](references/SVI-Volatility-Surface-Calibration/svi.py).

Instead of brute-force 5-parameter optimization, use the **quasi-explicit** approach which is both faster and more robust:

1. **Quasi SVI form**: Reparameterize as `w(y) = a + d*y + c*sqrt(y² + 1)` where `y = (k - m) / σ`
2. **Inner step** (closed-form): For fixed `(m, σ)`, solve for `(a, d, c)` via bounded linear least-squares (`scipy.optimize.lsq_linear`). Uses a 45° coordinate rotation trick for efficiency.
3. **Outer step** (2D optimizer): Optimize `(m, σ)` via Nelder-Mead (only 2 free parameters instead of 5).
4. **Iterate**: Alternate inner/outer steps until RMSE converges (typically 3-5 iterations).

```python
from scipy.optimize import lsq_linear, minimize

def svi_calibrate(iv_array, log_moneyness_array, init_m=0.0, init_sigma=0.1, max_iter=10):
    """
    Quasi-explicit SVI calibration.
    Reference: wangys96/SVI-Volatility-Surface-Calibration/svi.py:9-58
    
    Returns: (a, d, c, m, sigma, rmse) in quasi form.
    Convert to raw SVI via: b = sqrt(d² + c²)/σ, ρ = d/(b*σ)
    """
    def solve_adc(iv, x, m, sigma):
        y = (x - m) / max(sigma, 1e-6)
        z = np.sqrt(y**2 + 1)
        A = np.column_stack([np.ones(len(iv)), y, z])
        bounds = ([0, -np.inf, 0], [iv.max(), np.inf, np.inf])
        return lsq_linear(A, iv, bounds, tol=1e-12).x
    
    def objective(params):
        m, sigma = params
        a, d, c = solve_adc(iv_array, log_moneyness_array, m, sigma)
        y = (log_moneyness_array - m) / sigma
        fitted = a + d * y + c * np.sqrt(y**2 + 1)
        return np.sum((fitted - iv_array)**2)
    
    result = minimize(objective, [init_m, init_sigma], method='Nelder-Mead')
    m, sigma = result.x
    a, d, c = solve_adc(iv_array, log_moneyness_array, m, sigma)
    return a, d, c, m, sigma
```

> **Decision (2026-04-02)**: Compute **both** unconstrained and arbitrage-free fits. Display as two
> overlaid curves in the Vol Curve Editor — the trader sees exactly where the wings diverge and
> can toggle between them or choose which to use for pricing.
>
> - **Raw SVI** (unconstrained): Fast quasi-explicit 2-step method above. Always shown.
> - **Arb-free SVI** (Durrleman's condition): `g(k) = (1 - kw'/2w)² - w'/4*(1/w + 1/4) + w''/2 ≥ 0`.
>   Uses SLSQP optimizer with inequality constraints, seeded from the raw SVI params.
>   Shown as dashed overlay. Divergence from raw curve highlights arbitrage-prone regions.
>
> Reference: `JackJacquier/SSVI` for Durrleman condition implementation.

The `vol_params` table already has `svi_a/b/rho/m/sigma` columns. SVI implementation is part of **Phase 2** (alongside position management), since vol surface accuracy directly feeds PnL attribution quality.

### 5.4 Greeks Computation

Replaces: Excel `Module3.bas` → `BS_Greeks()` function

#### 5.4.1 Standard BS Greeks (USD-denominated risk)

```python
def bs_greeks_vec(S, K, T, r, q, sigma, is_call):
    """
    Vectorized Greeks for entire option chain in one pass.
    All inputs are NumPy arrays (or scalars that broadcast).
    Returns dict of arrays.
    
    These are STANDARD BS Greeks — they measure USD-denominated risk
    (i.e., how the USD value of the option changes).
    
    Adopted from pyBlackScholesAnalytics (options.py:1273-1369) vectorized pattern.
    Key change: replaced scalar if/else with np.where for call/put branching.
    
    Conventions:
    - Vega: per 1% vol move (÷100), matching Excel convention
    - Theta: per 1 calendar day (÷365), matching Excel convention
    """
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    
    nd1_pdf = norm.pdf(d1)      # N'(d1) — same for call and put
    Nd1 = norm.cdf(d1)          # N(d1)
    Nd2 = norm.cdf(d2)          # N(d2)
    
    exp_qT = np.exp(-q * T)
    exp_rT = np.exp(-r * T)
    
    # DELTA
    delta = np.where(is_call, exp_qT * Nd1, exp_qT * (Nd1 - 1))
    
    # GAMMA (same for call and put)
    gamma = (exp_qT * nd1_pdf) / (S * sigma * np.sqrt(T))
    
    # VEGA (per 1% vol change)
    vega = (S * exp_qT * nd1_pdf * np.sqrt(T)) / 100
    
    # THETA (per 1 calendar day)
    term1 = -(S * exp_qT * nd1_pdf * sigma) / (2 * np.sqrt(T))
    theta_call = (term1 + q * S * exp_qT * Nd1 - r * K * exp_rT * Nd2) / 365
    theta_put = (term1 - q * S * exp_qT * norm.cdf(-d1) + r * K * exp_rT * norm.cdf(-d2)) / 365
    theta = np.where(is_call, theta_call, theta_put)
    
    return {'delta': delta, 'gamma': gamma, 'vega': vega, 'theta': theta}
```

> **Reference note**: Vectorized Greeks pattern from
> [`pyBlackScholesAnalytics/options.py:1273-1369`](references/pyBlackScholesAnalytics/pyblackscholesanalytics/options/options.py).
> That library also provides `NumericGreeks` (finite-difference) in `numeric_routines.py:65-156`
> which can be used for verification during testing.

#### 5.4.2 Inverse Option Adjustment (Deribit-specific)

**Critical**: Deribit BTC options are **inverse** (BTC-settled, 1 BTC notional). The option premium and payoff are denominated in BTC, not USD. This means:

- **Call payoff in BTC**: `max(0, S - K) / S` (not `max(0, S - K)`)
- **Put payoff in BTC**: `max(0, K - S) / S`

The standard BS Greeks above measure **USD risk** (how the USD value changes with spot). For **BTC-denominated risk** (how the BTC value of the position changes), we need the inverse adjustment:

```python
def inverse_greeks(S, K, T, r, q, sigma, is_call, std_greeks):
    """
    Convert standard BS Greeks to inverse (BTC-settled) Greeks.
    
    For inverse options, the BTC value of the option is V_btc = V_usd / S.
    Differentiating V_btc w.r.t. S gives:
        dV_btc/dS = (delta_usd - V_btc) / S = delta_usd/S - V_usd/S²
    
    This means:
        delta_btc = (delta_usd * S - price_usd) / S²  = delta_usd/S - price_btc/S
        gamma_btc = (gamma_usd * S - 2 * delta_btc) / S  (second derivative)
    
    Vega and theta in BTC terms are simply divided by S (since V_btc = V_usd / S):
        vega_btc  = vega_usd / S
        theta_btc = theta_usd / S
    """
    price_usd = bs_price_vec(S, K, T, r, q, sigma, is_call)
    price_btc = price_usd / S
    
    delta_usd = std_greeks['delta']
    
    # BTC delta: derivative of (V_usd / S) w.r.t. S
    delta_btc = (delta_usd - price_btc) / S
    
    # BTC gamma: second derivative
    gamma_btc = (std_greeks['gamma'] - 2 * delta_btc) / S
    
    # Vega and theta: straightforward division
    vega_btc = std_greeks['vega'] / S
    theta_btc = std_greeks['theta'] / S
    
    return {'delta_btc': delta_btc, 'gamma_btc': gamma_btc,
            'vega_btc': vega_btc, 'theta_btc': theta_btc}
```

**When to use which**:
- **USD cash Greeks** (for USD PnL, delta hedging with USD-margined perps): Use standard `bs_greeks_vec()` directly
- **BTC cash Greeks** (for BTC-margined PnL, on-chain accounting): Use `inverse_greeks()`
- **Delta hedging on Binance** (USD-margined perp): Standard delta is correct — hedge `delta_usd * position_size` notional

### 5.5 Cash Greeks Conversion

Replaces: Excel `KOSPI` sheet columns W–AA (Cash delta, Cash Gamma, Cash Vega, Cash Theta) and `Risk Profile` sheet columns K–N

For Deribit BTC options (1 BTC notional per contract):

```python
contract_size_btc = 1.0  # Always 1 BTC per contract on Deribit

# USD Cash Greeks — use standard BS Greeks (Section 5.4.1)
# These measure USD PnL sensitivity, used for hedging with USD-margined perps
cash_delta_usd = delta * underlying_price * contract_size_btc * position_size
cash_gamma_usd = gamma * (underlying_price ** 2) * contract_size_btc * position_size / 100
cash_vega_usd  = vega * underlying_price * contract_size_btc * position_size
cash_theta_usd = theta * underlying_price * contract_size_btc * position_size

# BTC Cash Greeks — use inverse-adjusted Greeks (Section 5.4.2)
# These measure BTC PnL sensitivity, used for BTC-margined accounting
cash_delta_btc = delta_btc * underlying_price * contract_size_btc * position_size
cash_gamma_btc = gamma_btc * (underlying_price ** 2) * contract_size_btc * position_size / 100
cash_vega_btc  = vega_btc * underlying_price * contract_size_btc * position_size
cash_theta_btc = theta_btc * underlying_price * contract_size_btc * position_size
```

> **Important**: The BTC cash Greeks use the **inverse-adjusted** Greeks from Section 5.4.2,
> not the standard Greeks divided by S. This is because the Deribit option payoff in BTC
> has a convexity term (`-V/S`) that affects delta and gamma.

**For perpetual futures (hedge leg)**:
- Delta = 1.0 (long) or -1.0 (short)
- Gamma, Vega, Theta = 0
- Cash delta = `side * size * perp_price` (USD) or `side * size` (BTC)

### 5.6 Theoretical Price & Spread

```python
theo_price = bs_price_vec(S, K, T, r, q, custom_iv, is_call)
market_mid = (best_bid + best_ask) / 2
price_spread = market_mid - theo_price  # positive = market overpriced vs our model
```

`price_spread` is the core **trade signal**: if significantly positive, the option is expensive relative to our vol curve → potential sell signal. If negative → potential buy signal.

---

## 6. Position Management & Booking

### 6.1 Auto-Pull from Exchanges

Replaces: Excel `Booking` sheet (manual entry)

The system polls Deribit and Binance fill endpoints every 30 seconds. New fills are:

1. Deduplicated by `(exchange, exchange_trade_id)` against `trade_log`
2. Inserted into `trade_log`
3. Aggregated into `positions` table (update `size`, `avg_entry_price`)

```python
class PositionManager:
    async def sync_fills(self):
        """Pull new fills from exchanges, update positions."""
        
        # Deribit fills
        deribit_fills = await self.deribit_client.get_user_trades(currency='BTC')
        for fill in deribit_fills:
            if not await self.trade_log_exists(fill.trade_id):
                await self.record_fill(fill)
                await self.update_position(fill)
        
        # Binance perp fills
        binance_fills = await self.binance_client.get_user_trades(symbol='BTCUSDT')
        for fill in binance_fills:
            if not await self.trade_log_exists(fill.trade_id):
                await self.record_fill(fill)
                await self.update_position(fill)
    
    async def update_position(self, fill):
        """Update or create position from a fill."""
        # Logic: find existing open position for this instrument
        # If same side: increase size, recalc weighted avg price
        # If opposite side: reduce size (partial close) or flip
        # If size reaches 0: mark position as closed
```

### 6.2 Position Aggregation for Risk View

Aggregate all open positions into a single risk view per instrument:

```python
def build_risk_profile(positions, vol_surface, market_data):
    """
    Build the Risk Profile view.
    Mirrors Excel Risk Profile sheet rows 22+.
    """
    profile = []
    for pos in positions:
        if pos.instrument_type in ('call', 'put'):
            # Look up Greeks from vol_surface
            vs = vol_surface.get(pos.instrument_name)
            greeks = {
                'delta': vs.delta * pos.size * pos.side_multiplier,
                'gamma': vs.gamma * pos.size,
                'vega': vs.vega * pos.size,
                'theta': vs.theta * pos.size,
            }
        elif pos.instrument_type == 'perp':
            greeks = {
                'delta': 1.0 * pos.size * pos.side_multiplier,
                'gamma': 0, 'vega': 0, 'theta': 0,
            }
        
        # Cash greeks = pure greeks * multiplier
        cash_greeks_usd = convert_to_cash(greeks, market_data.underlying_price)
        
        profile.append({**pos.dict(), **greeks, **cash_greeks_usd})
    
    # Portfolio totals
    totals = aggregate_greeks(profile)
    return profile, totals
```

---

## 7. Risk Profile & PnL

### 7.1 Real-Time PnL Components

Replaces: Excel `Risk Profile` sheet cells F4–G8 (Trading PnL, 이론가 손익, Basis 합, 총 손익)

```python
# Unrealized PnL per position
unrealized_pnl_usd = (current_price - avg_entry_price) * size * multiplier * side_mult

# For options: multiplier = underlying_price (inverse settlement)
# For perps: multiplier = contract_value (Binance = notional per contract)
```

### 7.2 PnL Attribution (Greek Decomposition)

Mirrors the Excel `Risk Profile` PnL columns (O–V: Trading, Delta, Gamma, Vega, Theta, Basis):

```python
def pnl_attribution(prev_snapshot, current_state):
    """
    Decompose daily PnL into Greek components.
    Uses T-1 Greeks * today's market moves.
    """
    dS = current_state.underlying - prev_snapshot.underlying   # spot move
    dIV = current_state.iv - prev_snapshot.iv                   # vol change
    dt = 1 / 365                                                # 1 day elapsed
    
    delta_pnl = prev_snapshot.cash_delta * (dS / prev_snapshot.underlying)
    gamma_pnl = 0.5 * prev_snapshot.cash_gamma * (dS ** 2)
    vega_pnl  = prev_snapshot.cash_vega * (dIV * 100)           # vega is per 1%
    theta_pnl = prev_snapshot.cash_theta                        # theta already per day
    
    explained_pnl = delta_pnl + gamma_pnl + vega_pnl + theta_pnl
    actual_pnl = current_state.market_value - prev_snapshot.market_value
    unexplained = actual_pnl - explained_pnl                    # higher-order terms, basis, etc.
    
    return {
        'trading_pnl': today_realized_pnl,
        'delta_pnl': delta_pnl,
        'gamma_pnl': gamma_pnl,
        'vega_pnl': vega_pnl,
        'theta_pnl': theta_pnl,
        'theo_pnl': explained_pnl,
        'market_pnl': actual_pnl,
        'unexplained': unexplained,
    }
```

### 7.3 Portfolio-Level Risk Summary

Displayed at the top of the Risk Profile dashboard:

| Metric | Source |
|---|---|
| BTC Price (spot) | Deribit index |
| Perp Price | Binance BTCUSDT |
| Perp Funding Rate | Binance (annualized display) |
| Total Cash Delta (USD/BTC) | Sum across all positions |
| Total Cash Gamma (USD/BTC) | Sum across all positions |
| Total Cash Vega (USD/BTC) | Sum across all positions |
| Total Cash Theta (USD/BTC) | Sum across all positions |
| Today's Trading PnL | Sum of realized fills today |
| Today's Theo PnL | Greek-decomposed PnL |
| Today's Total PnL | Mark-to-market total |

---

## 8. EOD Snapshot & History

### 8.1 Snapshot Trigger

Replaces: Excel `Module4.bas` → `Save_EOD_Snapshot_V4()`

An automated job runs daily at a configurable hour (default: 08:00 UTC = Deribit settlement time = 17:00 KST). Aligned with Deribit's daily settlement to capture Greeks at the moment expiring options settle. The snapshot captures:

1. **Position snapshot** → `eod_snapshots` table (all open positions with Greeks, PnL attribution)
2. **Vol curve snapshot** → `vol_history` table (fitted IV for every strike of every active expiry)
3. **Market data snapshot** → `market_data` table with `is_eod = TRUE`

Manual trigger also available via API / UI button (for intraday vol curve changes).

### 8.2 Snapshot Service

```python
class SnapshotService:
    async def take_eod_snapshot(self, trigger='scheduled'):
        """
        Capture full system state.
        Mirrors Save_EOD_Snapshot_V4() from VBA.
        """
        timestamp = datetime.utcnow()
        snapshot_date = timestamp.date()
        
        # 1. Snapshot market data
        await self.snapshot_market_data(timestamp)
        
        # 2. Snapshot all positions + Greeks + PnL
        positions = await self.position_manager.get_open_positions()
        for pos in positions:
            greeks = await self.pricing_engine.get_greeks(pos.instrument_name)
            pnl = await self.compute_pnl_attribution(pos, greeks)
            await self.db.insert_eod_snapshot(snapshot_date, pos, greeks, pnl)
        
        # 3. Snapshot vol surface (all expiries, all strikes)
        for expiry in await self.get_active_expiries():
            params = await self.get_vol_params(expiry)
            strikes = await self.get_strikes_for_expiry(expiry)
            for strike in strikes:
                fitted_iv = self.pricing_engine.calc_vol(strike, params)
                await self.db.insert_vol_history(snapshot_date, expiry, strike, fitted_iv, params)
        
        logger.info(f"EOD snapshot complete: {snapshot_date} (trigger: {trigger})")
```

---

## 9. Frontend Dashboard

### 9.1 Page Layout

The React frontend consists of 5 primary views, accessible via tab navigation:

#### Tab 1: **Risk Profile** (default landing)

Mirrors: Excel `Risk Profile` sheet

```
┌─────────────────────────────────────────────────────────┐
│  BTC: $94,850  │  Perp: $94,920  │  Funding: 0.0045%  │
│  ΔS: +$320 (+0.34%)                                    │
├─────────────────────────────────────────────────────────┤
│  PORTFOLIO GREEKS                                        │
│  Cash Δ: -$12,450  │  Cash Γ: $89,200  │                │
│  Cash ν: $34,100   │  Cash Θ: -$8,900  │                │
├─────────────────────────────────────────────────────────┤
│  TODAY'S PnL                                             │
│  Trading: -$1,100  │  Theo: +$15,537  │  Total: +$14k  │
│  [Δ: +$8.2k] [Γ: +$4.1k] [ν: +$2.8k] [Θ: -$8.9k]   │
├─────────────────────────────────────────────────────────┤
│  POSITIONS TABLE                                         │
│  Instrument  │ Type │ Size │ Entry │ Mark │ Δ │ Γ │ ν │ │
│  BTC-28MAR.. │ Put  │ 50   │ 0.034 │ 0.041│...│...│...│ │
│  BTCUSDT     │ Perp │ 35   │94,200 │94,920│ 1 │ 0 │ 0 │ │
│  ...                                                     │
└─────────────────────────────────────────────────────────┘
```

#### Tab 2: **Vol Curve Editor**

Mirrors: Excel `VolParameters` sheet + chart

```
┌──────────────────────┬──────────────────────────────────┐
│  EXPIRY SELECTOR     │                                   │
│  [28 Mar] [25 Apr]   │     VOL CURVE CHART               │
│  [30 May] [27 Jun]   │     (bid dots, ask dots,          │
│  ...                 │      fitted curve, prev curve)    │
│                      │                                   │
│  PARAMETERS          │     X: Strike                     │
│  ATM Vol:  [0.59  ]  │     Y: Implied Vol                │
│  Skew:     [-2.5  ]  │                                   │
│  Smile:    [10    ]  │                                   │
│  Put Shift:[0.01  ]  │                                   │
│  Call Shift:[0.017]  │                                   │
│  ATM Strike: 95000   │                                   │
│                      │                                   │
│  [Apply] [Reset]     │                                   │
│  [Save Snapshot]     │                                   │
├──────────────────────┴──────────────────────────────────┤
│  STRIKE TABLE                                            │
│  Strike │ Bid IV │ Ask IV │ Fitted IV │ Prev IV │ Diff  │
│  85000  │ 72.3%  │ 79.3%  │ 72.6%     │ 70.1%   │ +2.5% │
│  87500  │ 70.2%  │ 73.5%  │ 70.5%     │ 67.5%   │ +3.0% │
│  ...                                                     │
└─────────────────────────────────────────────────────────┘
```

**Key interaction**: Slider or input fields for each parameter. On change, the fitted curve updates immediately (client-side computation using the same `calc_vol_parametric` formula ported to JS/TS). "Apply" pushes the params to the backend and triggers a full vol surface recomputation.

#### Tab 3: **Option Chain**

Mirrors: Excel `KOSPI` sheet (full option chain with Greeks)

```
┌─────────────────────────────────────────────────────────┐
│  EXPIRY: [28 Mar ▾]    DTE: 13    T: 0.0356            │
├──────────────────┬───────┬──────────────────────────────┤
│     CALLS        │Strike │         PUTS                  │
│  Bid│Ask│Theo│Δ  │       │  Δ│Theo│Ask│Bid              │
│ 0.12│0.14│0.13│.83│85000 │-.17│0.02│0.03│0.01           │
│ 0.09│0.11│0.10│.74│87500 │-.26│0.04│0.05│0.03           │
│ ...              │       │ ...                           │
│                  │       │                               │
│  Highlighted: cells where |spread| > threshold          │
└─────────────────────────────────────────────────────────┘
```

Spread column (market mid - theo) is color-coded: green = underpriced, red = overpriced.

#### Tab 4: **Trade Log / Booking**

Mirrors: Excel `Booking` sheet

Table of all fills pulled from exchanges, with filters by date range, instrument type, and exchange. Shows:
- Date, Exchange, Instrument, Side, Size, Price, Fee, Notional USD

#### Tab 5: **History & Analytics**

Mirrors: Excel `History_DB` + `History_Vol` sheets

- **PnL time series**: Daily total PnL, with Greek attribution stacked area chart
- **Vol surface evolution**: Select a date, see that day's fitted vol curve overlaid with today's
- **Position history**: Greeks evolution over time for a selected instrument

### 9.2 UI Component Library

| Component | Library |
|---|---|
| Data tables (option chain, positions) | AG Grid React |
| Vol curve chart | Recharts (scatter + line combo) |
| PnL time series | Recharts (area chart) |
| Parameter sliders | Custom React components (shadcn/ui) |
| Layout | Tailwind CSS |

---

## 10. Alerts & Triggers

### 10.1 Vol Curve Divergence Alert

Replaces: Excel `Risk Profile` cells C13–D16 (IV spread + 곡선 상태 판별)

The system monitors the divergence between market IV and the fitted vol curve at key strike points.

```python
async def check_vol_divergence(self):
    """
    Alert when OTM option IV diverges from fitted curve by > threshold.
    Mirrors Excel: 'OTM5% put IV vs fitted IV gap > 2% → alarm'
    """
    threshold = float(await self.config.get('vol_alert_threshold_pct'))  # default 0.02
    
    otm_5pct_strike = underlying_price * 0.95  # 5% OTM put
    nearest_put = find_nearest_strike(otm_5pct_strike, active_puts)
    
    market_iv = nearest_put.mid_iv  # average of bid/ask IV
    fitted_iv = calc_vol_parametric(nearest_put.strike, current_params)
    
    spread = abs(market_iv - fitted_iv)
    if spread > threshold:
        await self.send_alert(
            level='warning',
            message=f'Vol curve divergence: {nearest_put.instrument_name} '
                    f'market IV={market_iv:.1%} vs fitted={fitted_iv:.1%} '
                    f'(spread={spread:.1%} > threshold={threshold:.1%})',
            action='Consider adjusting vol curve parameters'
        )
```

### 10.2 Configurable Alert Channels

- **In-app**: Toast notification on dashboard
- **Telegram** (Phase 4): Bot message to configured chat
- **Webhook** (Phase 4): POST to arbitrary URL

### 10.3 Additional Alert Types (Phase 4)

- **Position limit**: Total notional exceeds configured threshold
- **Gamma exposure**: Cash gamma exceeds threshold (large convexity risk)
- **Funding rate spike**: Binance funding rate exceeds threshold (hedging cost)
- **Expiry proximity**: Position in option expiring within N hours

---

## 11. API Design

### 11.1 REST Endpoints

All endpoints prefixed with `/api/v1`.

#### Market Data

```
GET  /market/price              → Current BTC prices (Deribit index + Binance perp)
GET  /market/funding-rate       → Current Binance funding rate
GET  /market/option-chain       → Full option chain (all expiries, filtered by strike range)
     ?expiry=2026-03-28         → Filter to single expiry
```

#### Vol Surface

```
GET  /vol/surface               → Current fitted vol surface (all expiries)
     ?expiry=2026-03-28         → Filter to single expiry
GET  /vol/params                → Current vol parameters (all expiries)
     ?expiry=2026-03-28         → Filter to single expiry
PUT  /vol/params/{expiry}       → Update vol parameters for an expiry
     Body: { atm_vol, base_skew, base_smile, put_shift, call_shift }
POST /vol/snapshot              → Manually trigger vol curve snapshot
GET  /vol/history               → Historical vol curves
     ?date=2026-03-20&expiry=2026-03-28
```

#### Positions & Trades

```
GET  /positions                 → All open positions with current Greeks
GET  /positions/{id}            → Single position detail
GET  /trades                    → Trade log with pagination
     ?from=2026-03-01&to=2026-03-29&exchange=deribit
POST /trades/sync               → Force-sync fills from exchanges
```

#### Risk Profile

```
GET  /risk/profile              → Full risk profile (positions + aggregate Greeks + PnL)
GET  /risk/pnl                  → Today's PnL attribution breakdown
GET  /risk/pnl/history          → Historical daily PnL
     ?from=2026-03-01&to=2026-03-29
```

#### Snapshots

```
POST /snapshots/eod             → Manually trigger EOD snapshot
GET  /snapshots                 → List EOD snapshots by date
GET  /snapshots/{date}          → Full snapshot detail for a date
```

#### System Config

```
GET  /config                    → All config key-values
PUT  /config/{key}              → Update a config value
     Body: { value: "0.03" }
```

### 11.2 WebSocket (Phase 4)

```
WS   /ws/market                 → Real-time price + chain updates
WS   /ws/risk                   → Real-time risk profile updates
WS   /ws/alerts                 → Alert stream
```

---

## 12. Phasing & Milestones

### Phase 1: Core Engine (MVP)

**Goal**: Replace the Excel computation engine. No frontend yet — validated via API + tests.

- [ ] Database schema + migrations (PostgreSQL + Alembic)
- [ ] Deribit data ingestion (option chain, instruments, mark IV)
- [ ] Binance data ingestion (perp price, funding rate)
- [ ] IV solver (Brent's method, vectorized for full chain)
- [ ] OTM IV extraction (bid/ask curves, ATM averaging)
- [ ] Parametric vol curve fitting (`calc_vol_parametric`)
- [ ] Vol parameter CRUD per expiry
- [ ] BS pricing engine (theo price from custom IV)
- [ ] Greeks engine (delta, gamma, vega, theta) — vectorized
- [ ] Inverse option Greeks adjustment (BTC-settled delta/gamma correction)
- [ ] Cash Greeks (USD via standard Greeks, BTC via inverse-adjusted Greeks)
- [ ] Price spread computation (market mid - theo)
- [ ] Data retention cleanup job (option_chain_raw: 7d full, 90d downsampled)
- [ ] REST API for all above
- [ ] Unit tests (IV solver accuracy, Greeks vs known values, vol curve shape, inverse Greeks vs Deribit mark)

### Phase 2: Position Management + PnL + SVI

**Goal**: Track positions, compute PnL, and add SVI vol model.

- [ ] Deribit fill sync (authenticated API)
- [ ] Binance fill sync (authenticated API)
- [ ] Position aggregation logic
- [ ] Unrealized PnL computation
- [ ] PnL Greek attribution
- [ ] EOD snapshot service (scheduled + manual)
- [ ] Vol history snapshots
- [ ] Trade log API
- [ ] SVI quasi-explicit calibration (raw + arb-free overlay)
- [ ] SVI ↔ parametric toggle in vol_params per expiry

### Phase 3: Frontend Dashboard

**Goal**: Interactive UI replacing the Excel UX.

- [ ] Risk Profile page (positions table + aggregate Greeks + PnL)
- [ ] Vol Curve Editor (parameter sliders + live chart + strike table)
- [ ] Option Chain viewer (call/put symmetrical display, spread highlighting)
- [ ] Trade Log page
- [ ] History page (PnL chart, vol surface replay)

### Phase 4: Alerts + Polish

- [ ] Vol divergence alert engine
- [ ] In-app notification system
- [ ] Telegram bot integration
- [ ] WebSocket upgrade for real-time streaming (needed for auto-hedging)
- [ ] Dark mode UI
- [ ] Mobile-responsive layout

---

## Appendix: Excel → System Mapping

### Sheet-Level Mapping

| Excel Sheet | System Equivalent |
|---|---|
| `Risk Profile` | `/risk/profile` API + Risk Profile UI tab |
| `KOSPI` | `/market/option-chain` API + Option Chain UI tab |
| `HVOL` | Internal: OTM IV extraction pipeline (bid/ask curves) |
| `VolParameters` | `/vol/params` API + `/vol/surface` API + Vol Curve Editor UI tab |
| `History_DB` | `eod_snapshots` table + History UI tab |
| `History_Vol` | `vol_history` table + History UI tab |
| `Booking` | `trade_log` table + Trade Log UI tab |
| `Instrument` | `system_config` + hardcoded contract specs per exchange |
| `asset_hold` | Not migrated (KOSPI fund-specific, not applicable to BTC) |

### VBA Module Mapping

| VBA Module | Function | System Equivalent |
|---|---|---|
| `Module1` | `CalcVolForParams()` | `pricing_engine.calc_vol_parametric()` |
| `Module1` | `UpdateNearMonthVolCurve()` | `vol_service.recompute_surface(expiry)` |
| `Module1` | `SetupInteractiveVolTool()` | Vol Curve Editor React component |
| `Module2` | `CalculateImpliedVolForStrikes()` | `pricing_engine.compute_chain_iv()` |
| `Module2` | `CalculateIV_For_Cell()` | `pricing_engine.implied_vol()` (Brent) |
| `Module3` | `BS_Greeks()` | `pricing_engine.bs_greeks_vec()` + `inverse_greeks()` |
| `Module3` | `UpdateGreeksFromFittedVol()` | `vol_service.update_greeks()` |
| `Module4` | `Save_EOD_Snapshot_V4()` | `snapshot_service.take_eod_snapshot()` |
| `Module5` | `GetTheoPrice()` | `pricing_engine.bs_price_vec()` |
| `Module6` | `GetStraddleIV()` | `pricing_engine.straddle_iv()` (ATM vol extraction) |
| `Sheet3` | `Worksheet_Change()` (auto-update on param edit) | React `onChange` → API `PUT /vol/params` |

### Key Formula Mapping

| Excel Formula / Logic | Python Equivalent |
|---|---|
| `normalizedStrike = log(K/ATM) / (σ*√T)` | Same, in `calc_vol_parametric()` |
| `skew = 0.2 * baseSkew * √T` | Same |
| `smile = 0.04 * baseSmile * T` | Same |
| `vol = ATM + skew*x + smile*x² + shift*|x|` | Same |
| `d1 = (ln(S/K) + (r-q+σ²/2)*T) / (σ√T)` | Same, in `bs_price_vec()` and `bs_greeks_vec()` |
| Cash Delta = `Δ * spot * multiplier * qty` | `delta * underlying_price * 1.0 * size` |
| Cash Gamma = `Γ * spot² * multiplier * qty / 100` | Same pattern, adapted for BTC notional |
| Vega = `S * e^(-qT) * N'(d1) * √T / 100` | Same (per 1% vol move convention) |
| Theta = `[...] / 365` | Same (per 1 calendar day convention) |

---

## Appendix B: Reference Repository Analysis

Reviewed 2026-04-02. Repos cloned to `references/` directory.

### Adopted Changes (simpler + better)

| Change | Source Repo | What Changed in Spec |
|---|---|---|
| **Vectorized BS pricing** | `pyBlackScholesAnalytics` (options.py:1258-1271) | `bs_price` and `bs_greeks` now use NumPy array broadcasting instead of scalar if/else. Computes entire chain in one pass. |
| **Concurrent orderbook fetching** | `schepal/deribit_data_collector` (ThreadPoolExecutor pattern) | `DataIngestionService.poll_deribit_chain()` now uses `asyncio.gather()` + semaphore(20) for parallel fetches. ~15x faster for full chain. |
| **SVI quasi-explicit calibration** | `wangys96/SVI-Volatility-Surface-Calibration` (svi.py:9-58) | Phase 2 SVI now uses 2-step method: linear solve for (a,d,c) + 2D Nelder-Mead for (m,σ). Faster and more stable than 5-param brute force. |

### Tradeoffs — Resolved (2026-04-02)

#### ✅ 1. SVI Arbitrage-Free Constraints → Show Both

Compute both raw SVI (unconstrained, fast) and arb-free SVI (Durrleman's condition, SLSQP). Display as two overlaid curves in Vol Curve Editor — trader sees where wings diverge and chooses which to use for pricing. See Section 5.3.

#### ✅ 2. WebSocket vs REST for Deribit Data → REST for MVP

Both `Derbit-Volatility-Visulization` and `vol-surface-visualizer` implement Deribit WebSocket but leave it half-connected/unused. REST polling (15s) is sufficient for vol curve editing — not HFT. WebSocket deferred to Phase 4 (needed only if auto-hedging is added, where real-time price feeds prevent slippage).

#### ✅ 3. PnL Calculation → Keep Spec's Clean Separation

`nostoz/deribit_pnl`'s approach (cumulative avg aggregation) conflates realized/unrealized PnL with double-counting risk. Spec's separate `positions` + `trade_log` tables with Greek decomposition via T-1 snapshots is correct for multi-leg options + perp hedge books.

### Repos Not Adopted (and why)

| Repo | Why Not |
|---|---|
| `vol-surface-visualizer` | Architecture useful as concept reference, but code quality too low for adoption (SQL injection, no async, bisection IV solver with $0.05 tolerance, no vol curve model). |
| `deribit_pnl` | PnL formula has double-counting risk. No Greek decomposition. SQLite-only. Useful only as API endpoint reference for Deribit authenticated calls. |
| `Derbit-Volatility-Visulization` | Uses deprecated Deribit API v1 (`/api/v1`). WebSocket code has SSL verification disabled. Visualization is matplotlib-based (not web). |
| `pyBlackScholesAnalytics` | Greeks engine adopted (vectorized pattern). But: no dividend yield q support, no IV solver, no vol surface model, no PnL decomposition. Over-abstracted class hierarchy (10+ levels) not worth copying. |
| `deribit_data_collector` | Concurrent fetch pattern adopted. But: no strike filtering, no IV computation, CSV-only storage, no error handling. |
