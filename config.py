




# quantbot/config.py
import os
from dotenv import load_dotenv
load_dotenv()

ALPACA_KEY = os.getenv("ALPACA_KEY")
ALPACA_SECRET = os.getenv("ALPACA_SECRET")
ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
MODE = os.getenv("MODE", "SIM").upper()  # SIM or ALPACA

POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "30"))
TICKER = os.getenv("TICKER", "AAPL")
INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", "100000"))
SHORT = int(os.getenv("SHORT", "50"))
LONG = int(os.getenv("LONG", "200"))
ALLOC_PCT = float(os.getenv("ALLOC_PCT", "0.10"))
SLIPPAGE_PCT = float(os.getenv("SLIPPAGE_PCT", "0.0005"))
COMMISSION = float(os.getenv("COMMISSION", "1.0"))
DB_PATH = os.getenv("DB_PATH", "quantbot.db")



