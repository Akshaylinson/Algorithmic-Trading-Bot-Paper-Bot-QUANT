






# quantbot/broker.py
"""
Minimal Broker class for this paper trading bot.
- Provides a simple simulator when MODE != 'ALPACA'
- Uses Alpaca paper API when MODE == 'ALPACA' and keys are available
Exports: Broker  (class)
"""

import os
from decimal import Decimal, ROUND_HALF_UP
from dotenv import load_dotenv

load_dotenv()

MODE = os.getenv("MODE", "SIM").upper()
ALPACA_KEY = os.getenv("ALPACA_KEY")
ALPACA_SECRET = os.getenv("ALPACA_SECRET")
ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

# lazy import for DB (relative import to avoid circular import issues)
# we'll import inside functions where used


# -----------------------
# Lightweight local simulation broker
# -----------------------
class _SimBroker:
    def __init__(self, initial_cash=100000.0):
        self.cash = float(initial_cash)
        # positions: symbol -> (qty:int, avg_price:float)
        self.positions = {}

    def account_value(self, price_map=None):
        price_map = price_map or {}
        total = float(self.cash)
        for sym, (qty, avg) in list(self.positions.items()):
            p = price_map.get(sym)
            if p is None:
                # if no price provided, treat as 0 for safety
                p = 0.0
            total += qty * float(p)
        return total

    def get_position(self, symbol):
        return self.positions.get(symbol, (0, 0.0))

    def place_order(self, symbol, side, price, qty, fees=0.0):
        """Immediate fill simulation at given price."""
        symbol = symbol.upper()
        side = side.upper()
        price = float(price) if price is not None else None
        qty = int(qty)
        fees = float(fees)

        if qty <= 0:
            raise RuntimeError("Quantity must be > 0")

        if side == "BUY":
            # for simulation, if no price provided use last-known market price? require price
            if price is None:
                raise RuntimeError("SimBroker requires a price for BUY in this simple implementation")
            cost = price * qty + fees
            if cost > self.cash:
                raise RuntimeError("Insufficient cash for BUY")
            prev_qty, prev_avg = self.positions.get(symbol, (0, 0.0))
            new_qty = prev_qty + qty
            new_avg = ((prev_qty * prev_avg) + (price * qty)) / new_qty if new_qty else 0.0
            self.positions[symbol] = (new_qty, new_avg)
            self.cash -= cost
            res = {
                "symbol": symbol,
                "side": "BUY",
                "qty": qty,
                "filled_qty": qty,
                "filled_avg_price": price,
                "status": "filled",
                "id": None
            }
        elif side == "SELL":
            prev_qty, prev_avg = self.positions.get(symbol, (0, 0.0))
            sell_qty = min(qty, prev_qty)
            if sell_qty <= 0:
                raise RuntimeError("No shares to sell")
            if price is None:
                raise RuntimeError("SimBroker requires a price for SELL in this simple implementation")
            proceeds = price * sell_qty - fees
            new_qty = prev_qty - sell_qty
            if new_qty == 0:
                self.positions.pop(symbol, None)
            else:
                self.positions[symbol] = (new_qty, prev_avg)
            self.cash += proceeds
            res = {
                "symbol": symbol,
                "side": "SELL",
                "qty": qty,
                "filled_qty": sell_qty,
                "filled_avg_price": price,
                "status": "filled",
                "id": None
            }
        else:
            raise ValueError("Unknown side")

        # persist trade to DB (best-effort)
        try:
            # import local db module to avoid circular import at top-level
            from . import db
            filled_price = res.get("filled_avg_price", price)
            db.persist_trade(
                symbol=res["symbol"],
                side=res["side"],
                qty=int(res.get("filled_qty", res.get("qty", qty))),
                price=float(filled_price or price or 0.0),
                fees=float(fees or 0.0),
                order_type=("market" if price is None else "limit"),
                status=res.get("status", "unknown"),
                alpaca_order_id=res.get("id")
            )
        except Exception:
            # don't break order flow if DB write fails
            pass

        return res

    def close(self):
        # nothing to close for simulation
        return


# -----------------------
# Alpaca wrapper
# -----------------------
class _AlpacaBroker:
    def __init__(self):
        try:
            import alpaca_trade_api as tradeapi
        except Exception as e:
            raise RuntimeError(f"alpaca_trade_api not available: {e}")
        # REST client (v2)
        # note: api_version 'v2' is typical for modern alpaca-trade-api
        self.api = tradeapi.REST(ALPACA_KEY, ALPACA_SECRET, ALPACA_BASE_URL, api_version="v2")

    def account_value(self, price_map=None):
        try:
            acct = self.api.get_account()
            # prefer equity (already includes positions)
            equity = getattr(acct, "equity", None)
            cash = getattr(acct, "cash", None)
            return float(equity if equity is not None else (cash if cash is not None else 0.0))
        except Exception:
            try:
                acct = self.api.get_account()
                return float(getattr(acct, "equity", getattr(acct, "cash", 0.0)))
            except Exception:
                return 0.0

    def get_position(self, symbol):
        symbol = symbol.upper()
        try:
            p = self.api.get_position(symbol)
            qty = int(float(getattr(p, "qty", 0)))
            avg = float(getattr(p, "avg_entry_price", getattr(p, "average_entry_price", 0.0)))
            return (qty, avg)
        except Exception:
            return (0, 0.0)

    def _quantize_price(self, price, decimals=2):
        """
        Ensure price fits allowed tick-size (default 2 decimals).
        Returns a string formatted price like '258.06'.
        """
        # Use Decimal to avoid float precision artifacts
        d = Decimal(str(price))
        q = Decimal(1).scaleb(-decimals)  # Decimal('0.01') for decimals=2
        quantized = d.quantize(q, rounding=ROUND_HALF_UP)
        # Format as string without scientific notation
        return format(quantized, 'f')

    def place_order(self, symbol: str, side: str, price, qty: int, fees=0.0, stop_price=None, take_price=None):
        """
        Submit an order to Alpaca.
        - If price is None -> market order
        - If price numeric -> limit order with price quantized to 2 decimals
        - Optional stop_price / take_price for bracket orders
        Returns a dict summarizing the order.
        """
        symbol = symbol.upper()
        side = side.lower()
        qty = int(qty)
        fees = float(fees or 0.0)

        if qty <= 0:
            raise RuntimeError("Quantity must be > 0")

        order = None

        # MARKET ORDER path
        if price is None:
            try:
                order = self.api.submit_order(
                    symbol=symbol,
                    qty=qty,
                    side=side,
                    type="market",
                    time_in_force="day",
                )
            except Exception as e:
                raise RuntimeError(f"Alpaca submit_order (market) failed: {e}") from e

        # LIMIT / BRACKET ORDER path
        else:
            # quantize to 2 decimals (common tick size). Change decimals if needed for other assets.
            try:
                limit_price_str = self._quantize_price(price, decimals=2)
            except Exception:
                # As fallback, cast to float and format
                limit_price_str = f"{float(price):.2f}"

            try:
                if stop_price is not None or take_price is not None:
                    # prepare bracket parameters if provided
                    tp = {"limit_price": self._quantize_price(take_price, decimals=2)} if take_price is not None else None
                    sl = {"stop_price": self._quantize_price(stop_price, decimals=2)} if stop_price is not None else None

                    # build kwargs for submit_order
                    submit_kwargs = dict(
                        symbol=symbol,
                        qty=qty,
                        side=side,
                        type="limit",
                        time_in_force="day",
                        limit_price=str(limit_price_str),
                        order_class="bracket",
                    )
                    if tp:
                        submit_kwargs["take_profit"] = tp
                    if sl:
                        submit_kwargs["stop_loss"] = sl

                    order = self.api.submit_order(**submit_kwargs)
                else:
                    order = self.api.submit_order(
                        symbol=symbol,
                        qty=qty,
                        side=side,
                        type="limit",
                        time_in_force="day",
                        limit_price=str(limit_price_str),
                    )
            except Exception as e:
                # If error message indicates tick-size or invalid limit_price, optionally fallback to market order
                err_msg = str(e)
                if "sub-penny" in err_msg.lower() or "invalid limit_price" in err_msg.lower() or "minimum pricing" in err_msg.lower():
                    try:
                        # fallback: place a market order instead
                        order = self.api.submit_order(
                            symbol=symbol,
                            qty=qty,
                            side=side,
                            type="market",
                            time_in_force="day",
                        )
                    except Exception as e2:
                        raise RuntimeError(f"Alpaca submit_order (limit failed: {err_msg}; market fallback failed: {e2})") from e2
                else:
                    raise RuntimeError(f"Alpaca submit_order (limit) failed: {e}") from e

        # Build simplified result dict
        try:
            res = {
                "symbol": getattr(order, "symbol", symbol),
                "side": getattr(order, "side", side).upper(),
                # note: Alpaca order object fields may be strings
                "qty": int(float(getattr(order, "qty", qty))),
                "filled_qty": int(float(getattr(order, "filled_qty", 0) or 0)),
                "filled_avg_price": float(getattr(order, "filled_avg_price", getattr(order, "filled_avg_price", 0.0) or 0.0)),
                "status": getattr(order, "status", None),
                "id": getattr(order, "id", None),
                "submitted_at": getattr(order, "submitted_at", None) or getattr(order, "created_at", None),
            }
        except Exception:
            res = {"symbol": symbol, "side": side.upper(), "qty": qty, "status": "unknown", "id": None}

        # persist trade to DB (best-effort)
        try:
            from . import db
            filled_price = res.get("filled_avg_price", price)
            db.persist_trade(
                symbol=res.get("symbol", symbol),
                side=res.get("side", side).upper(),
                qty=int(res.get("filled_qty", res.get("qty", qty))),
                price=float(filled_price or price or 0.0),
                fees=float(fees or 0.0),
                order_type=("market" if price is None else "limit"),
                status=res.get("status", "unknown"),
                alpaca_order_id=res.get("id")
            )
        except Exception:
            # don't break if DB persistence fails
            pass

        return res

    def close(self):
        # nothing special to close for REST client
        return


# -----------------------
# Exported Broker class - chooses implementation depending on MODE/keys
# -----------------------
class Broker:
    def __init__(self):
        # choose implementation based on MODE and availability of keys
        if MODE == "ALPACA" and ALPACA_KEY and ALPACA_SECRET:
            try:
                self._impl = _AlpacaBroker()
                print("[Broker] Using Alpaca REST (paper mode).")
            except Exception as e:
                print(f"[Broker] Alpaca init failed ({e}) - falling back to SIM broker.")
                self._impl = _SimBroker()
        else:
            self._impl = _SimBroker()
            print("[Broker] Using SIM broker (no Alpaca keys or MODE!=ALPACA).")

    def account_value(self, price_map=None):
        return self._impl.account_value(price_map)

    def get_position(self, symbol):
        return self._impl.get_position(symbol)

    def place_order(self, symbol, side, price, qty, fees=0.0, stop_price=None, take_price=None):
        # unified signature that both impls support (SimBroker ignores stop/take)
        return self._impl.place_order(symbol, side, price, qty, fees=fees, stop_price=stop_price, take_price=take_price)

    def close(self):
        try:
            return getattr(self._impl, "close")()
        except Exception:
            pass

