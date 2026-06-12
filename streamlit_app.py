
# streamlit_app.py
"""
Streamlit dashboard for SMA/EMA trading bot with paper trading controls.

Features:
 - Select tickers (multi-select)
 - Fetch OHLC using quantbot.utils (Alpaca or yfinance fallback)
 - Compute SMA and EMA
 - Visualize charts
 - Place trades (market/limit)
 - Auto-calculate position size by allocation %
 - Show account equity & positions
 - Keep session trade history (in-memory)
"""

import streamlit as st
import pandas as pd
import time
from datetime import datetime
from typing import Optional

st.set_page_config(page_title="SMA/EMA Trading Bot Dashboard", layout="wide")

# --- Imports from your project ---
try:
    from quantbot.broker import Broker
    from quantbot import utils
    try:
        from quantbot import db
    except Exception:
        db = None
except Exception as e:
    st.error(f"Failed to import quantbot package modules: {e}")
    st.stop()

# -----------------------
# UI / Defaults
# -----------------------
DEFAULT_TICKERS = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "PG"]

if "session_trades" not in st.session_state:
    st.session_state.session_trades = []

# Title & control bar
col1, col2, col3 = st.columns([4, 1, 1])
with col1:
    st.title("📈 SMA/EMA Trading Bot Dashboard")
with col2:
    run_strategy_btn = st.button("▶ Run Strategy")
with col3:
    auto_refresh = st.checkbox("Auto refresh (60s)", False)

# Left: settings
left, right = st.columns([3, 7])
with left:
    st.subheader("Configuration")

    tickers = st.multiselect("Tickers", DEFAULT_TICKERS, default=["AAPL"])
    lookback = st.selectbox("Period", ["7d", "30d", "90d", "180d", "365d"], index=1)
    interval = st.selectbox("Interval", ["1d", "1wk"], index=0)
    sma_short = st.number_input("Short SMA", 5, 200, 50, 5)
    ema_long = st.number_input("Long EMA", 10, 500, 200, 10)
    alloc_pct = st.slider("Allocation % per position", 0.01, 1.0, 0.10, 0.01)
    order_type_ui = st.selectbox("Order Type", ["market", "limit"])

    try:
        broker = Broker()
        acct_val = broker.account_value({})
        st.success(f"Broker Ready ✅ (Mode Active)\nEquity: ${acct_val:.2f}")
    except Exception as e:
        st.error(f"Broker initialization failed: {e}")
        st.stop()

# Right: charts and trade section
with right:
    if not tickers:
        st.warning("Select at least one ticker to fetch data.")
        st.stop()

    @st.cache_data(ttl=60)
    def fetch_ohlc_for(ticker: str, period: str, interval: str):
        df = utils.fetch_latest_ohlc(ticker, period=period, interval=interval)
        if not isinstance(df, pd.DataFrame) or df.empty:
            return pd.DataFrame(columns=["Open", "Close"])
        df = df.loc[:, ~df.columns.duplicated()]
        df = df[["Open", "Close"]].dropna()
        df.index = pd.to_datetime(df.index)
        return df

    for t in tickers:
        st.markdown(f"## {t}")
        df = fetch_ohlc_for(t, lookback, interval)

        if df.empty:
            st.warning(f"No data for {t}")
            continue

        df = df.assign(Close=df["Close"].astype(float))
        df[f"SMA{sma_short}"] = df["Close"].rolling(window=sma_short).mean()
        df[f"EMA{ema_long}"] = df["Close"].ewm(span=ema_long, adjust=False).mean()

        df_reset = df.reset_index().rename(columns={df.reset_index().columns[0]: "Date"})
        st.line_chart(df.set_index(df_reset.columns[0])[["Close", f"SMA{sma_short}", f"EMA{ema_long}"]])

        last_close = float(df["Close"].iloc[-1])
        last_sma = float(df[f"SMA{sma_short}"].iloc[-1])
        last_ema = float(df[f"EMA{ema_long}"].iloc[-1])

        signal = "HOLD"
        if last_sma > last_ema:
            signal = "BUY"
        elif last_sma < last_ema:
            signal = "SELL"

        colA, colB, colC = st.columns(3)
        colA.metric("Last Price", f"${last_close:.2f}")
        colB.metric(f"SMA{sma_short}", f"{last_sma:.2f}")
        colC.metric(f"EMA{ema_long}", f"{last_ema:.2f}")

        st.write(f"**Strategy Signal:** `{signal}` (SMA{sma_short} vs EMA{ema_long})")

        # --- Manual Trade ---
        st.markdown("### Manual Trade")
        trade_cols = st.columns([3, 3, 2, 2])

        # Auto quantity based on allocation
        try:
            alloc_qty = int((acct_val * alloc_pct) // last_close)
        except Exception:
            alloc_qty = 0

        buy_qty = trade_cols[0].number_input(f"Buy qty for {t}", min_value=0, value=alloc_qty, step=1, key=f"buy_{t}")
        sell_qty = trade_cols[1].number_input(f"Sell qty for {t}", min_value=0, value=0, step=1, key=f"sell_{t}")
        order_type_opt = trade_cols[2].selectbox("Order type", ["market", "limit"], key=f"otype_{t}")
        limit_price = trade_cols[3].number_input("Limit price (if limit)", min_value=0.0, value=float(last_close), key=f"lprice_{t}")

        buy_col, sell_col = st.columns(2)

        def place_trade(symbol: str, side: str, qty: int, order_type: str, limit_price_val: Optional[float]):
            if qty <= 0:
                st.warning("Quantity must be greater than 0")
                return
            price_for_order = None if order_type == "market" else float(limit_price_val)
            try:
                res = broker.place_order(symbol, side, price_for_order, qty)
                rec = {
                    "ts": datetime.utcnow().isoformat(),
                    "symbol": symbol,
                    "side": side,
                    "qty": qty,
                    "price": price_for_order or "market",
                    "result": res
                }
                st.session_state.session_trades.insert(0, rec)
                st.success(f"{side.upper()} {qty} {symbol} executed at {price_for_order or 'market'}")
            except Exception as e:
                st.error(f"Trade failed: {e}")

        if buy_col.button(f"BUY {t}"):
            place_trade(t, "BUY", int(buy_qty), order_type_opt, limit_price)

        if sell_col.button(f"SELL {t}"):
            place_trade(t, "SELL", int(sell_qty), order_type_opt, limit_price)

        try:
            pos_qty, pos_avg = broker.get_position(t)
            st.write(f"**Current Position:** {pos_qty} shares @ ${pos_avg:.2f}")
        except Exception:
            st.write("**Current Position:** N/A")

        st.divider()

    # --- Trade history ---
    st.subheader("Session Trade History")
    trades_df = pd.DataFrame(st.session_state.session_trades)
    if not trades_df.empty:
        def _summary(r):
            if isinstance(r, dict):
                return f"{r.get('status', '?')} ({r.get('filled_qty', r.get('qty', '?'))})"
            return str(r)
        trades_df["summary"] = trades_df["result"].apply(_summary)
        st.dataframe(trades_df[["ts", "symbol", "side", "qty", "price", "summary"]])
    else:
        st.info("No trades executed this session.")

# --- Background ---
if run_strategy_btn:
    st.experimental_rerun()

if auto_refresh:
    time.sleep(60)
    st.experimental_rerun()

