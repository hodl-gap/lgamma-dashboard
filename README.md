# Lgamma BTC Options Trading Dashboard

Self-hosted options trading dashboard for BTC options on Deribit, hedged with Binance BTC/USDT perpetual futures.

## Quick Start

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Setup

1. Copy `.env.example` to `.env` and fill in your API credentials:

```
DERIBIT_CLIENT_ID=your_client_id
DERIBIT_CLIENT_SECRET=your_client_secret
BINANCE_API_KEY=your_api_key
BINANCE_API_SECRET=your_api_secret
```

2. **IP Whitelist**: Add your machine's public IP to both Deribit and Binance API settings. Without this, the dashboard still works with public data (option chain, vol surface, Greeks) but position/trade sync will be inactive.

3. Run:
```bash
streamlit run app.py
```

## Features

- **Risk Profile**: Portfolio Greeks, PnL attribution, positions table
- **Vol Curve Editor**: Interactive parameter sliders, parametric + SVI curve fitting, arb-free overlay
- **Option Chain**: Call/put symmetric display with theo prices and spread highlighting
- **Trade Log**: Auto-synced fills from Deribit + Binance
- **History**: Daily PnL attribution chart, vol surface evolution

## Architecture

- **Backend**: Python (NumPy/SciPy for pricing, aiohttp for API calls)
- **Frontend**: Streamlit
- **Database**: DuckDB (file-based, zero-config)
- **Data**: Deribit REST API (15s polling) + Binance REST API (5s polling)
