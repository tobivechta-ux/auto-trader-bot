from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

from datetime import datetime, timedelta, timezone
import yfinance as yf
import os
import time
import logging

# =====================
# LOGGING
# =====================
logging.basicConfig(
    filename='logs/auto_trader.log',
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s'
)

# =====================
# API KEYS
# =====================
API_KEY = os.getenv("PKWN4NLKNURIHDKWZNNN34IZ5I")
SECRET_KEY = os.getenv("Dn1ebM9fPgtxD6ooHZHXV1mAfk7Xxj23MC12WFtngbQ2")

# =====================
# WATCHLIST US + EU + ETFs
# =====================
SYMBOLS = [
    # US Tech
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA",
    # US ETFs
    "SPY", "QQQ", "DIA",
    # EU Blue Chips
    "SAP.DE", "SIE.DE", "ALV.DE", "ADS.DE", "ASML.AS", "VOW3.DE",
    # EU ETFs
    "EUNL.DE", "EXSA.DE"
# =====================
# WATCHLIST (GLOBAL)
# =====================

    # BIG TECH
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA",

    # AI / CHIP
    "AMD", "AVGO", "ASML", "TSM",

    # MEGA CAPS
    "BRK.B", "JPM", "V", "MA",

    # DEFENSIVE
    "COST", "WMT", "PG", "KO", "PEP",

    # HEALTHCARE
    "UNH", "LLY", "JNJ",

    # INDUSTRY / ENERGY
    "XOM", "CAT",

    # ETFs (Stabilität)
    "SPY", "QQQ", "DIA"
]

# =====================
# STRATEGIE SETTINGS
# =====================
LOOKBACK_DAYS = 10
AVG_DAYS = 5
STOP_LOSS_PCT = 0.03
RISK_PER_TRADE = 0.01
INTERVAL_SECONDS = 240
MAX_OPEN_POSITIONS = 5
MAX_TRADES_PER_CYCLE = 4


# Trailing Stop-Dict
trailing_positions = {}

# =====================
# CLIENTS
# =====================
data_client = StockHistoricalDataClient(API_KEY, SECRET_KEY)
trading_client = TradingClient(API_KEY, SECRET_KEY, paper=True)

logging.info("Auto Trader gestartet (Paper Mode)")
print("Auto Trader gestartet (Paper Mode)")

# =====================
# POSITION SIZE
# =====================
def calculate_position_size(account_equity, price):
    risk_amount = account_equity * RISK_PER_TRADE
    stop_distance = price * STOP_LOSS_PCT
    qty = risk_amount / stop_distance
    return max(1, int(qty))

# =====================
# MARKTFILTER
# =====================
def market_is_bullish():
    try:
        spy = yf.Ticker("SPY")
        data = spy.history(period="40d")
        sma_short = data["Close"].tail(10).mean()
        sma_long = data["Close"].tail(30).mean()
        return sma_short > sma_long
    except Exception as e:
        logging.warning(f"Marktdaten konnten nicht geladen werden: {e}")
        return True  # fallback

# =====================
# BÖRSENÖFFNUNGS-CHECK
# =====================
def market_open(symbol):
    now = datetime.utcnow().time()
    # US Börse (NYSE/NASDAQ)
    if symbol in ["AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","SPY","QQQ","DIA"]:
        return now >= datetime.strptime("14:30", "%H:%M").time() and now <= datetime.strptime("21:00", "%H:%M").time()
    # EU Börsen (XETRA/AMS/LSE)
    else:
        return now >= datetime.strptime("08:00", "%H:%M").time() and now <= datetime.strptime("16:30", "%H:%M").time()

# =====================
# MAIN LOOP
# =====================
while True:
    try:
        # Konto
        account = trading_client.get_account()
        account_equity = float(account.equity)
        print(f"\nKontowert: ${account_equity:.2f}")
        logging.info(f"Kontowert: ${account_equity:.2f}")

        # Offene Positionen
        positions = trading_client.get_all_positions()
        open_positions = {p.symbol: {"entry": float(p.avg_entry_price), "qty": int(float(p.qty))} for p in positions}
        trades_this_cycle = 0

        # Symbol Loop
        for symbol in SYMBOLS:
            if trades_this_cycle >= MAX_TRADES_PER_CYCLE:
                break

            if not market_open(symbol):
                print(f"{symbol}: Börse geschlossen")
                logging.info(f"{symbol}: Börse geschlossen")
                continue

            print(f"\n--- Prüfe {symbol} ---")
            logging.info(f"Prüfe {symbol}")

            end = datetime.utcnow().replace(tzinfo=timezone.utc)
            start = end - timedelta(days=LOOKBACK_DAYS)

            try:
                request = StockBarsRequest(
                    symbol_or_symbols=symbol,
                    timeframe=TimeFrame.Day,
                    start=start,
                    end=end,
                    feed="iex"
                )
                bars = data_client.get_stock_bars(request).df
            except Exception as e:
                logging.warning(f"Daten für {symbol} konnten nicht geladen werden: {e}")
                continue

            if bars.empty:
                logging.info(f"Keine Daten für {symbol}")
                continue

            last_close = bars["close"].iloc[-1]
            avg_close = bars["close"].tail(AVG_DAYS).mean()
            print(f"Preis {last_close:.2f} | AVG {avg_close:.2f}")
            logging.info(f"{symbol} | Preis {last_close:.2f} | AVG {avg_close:.2f}")

            # =====================
            # SELL / Trailing Stop
            # =====================
            if symbol in open_positions:
                entry_price = open_positions[symbol]["entry"]
                qty = open_positions[symbol]["qty"]

                if symbol not in trailing_positions:
                    trailing_positions[symbol] = {"highest_price": last_close, "stop_price": last_close * (1-STOP_LOSS_PCT)}

                if last_close > trailing_positions[symbol]["highest_price"]:
                    trailing_positions[symbol]["highest_price"] = last_close
                    trailing_positions[symbol]["stop_price"] = last_close * (1-STOP_LOSS_PCT)

                if last_close <= trailing_positions[symbol]["stop_price"]:
                    print(f"{symbol}: TRAILING STOP aktiviert")
                    logging.info(f"{symbol}: TRAILING STOP aktiviert")
                    trading_client.submit_order(
                        MarketOrderRequest(
                            symbol=symbol,
                            qty=qty,
                            side=OrderSide.SELL,
                            time_in_force=TimeInForce.GTC
                        )
                    )
                    del trailing_positions[symbol]
                    continue

                elif last_close >= entry_price * 1.03:
                    print(f"{symbol}: TAKE PROFIT")
                    logging.info(f"{symbol}: TAKE PROFIT")
                    trading_client.submit_order(
                        MarketOrderRequest(
                            symbol=symbol,
                            qty=qty,
                            side=OrderSide.SELL,
                            time_in_force=TimeInForce.GTC
                        )
                    )
                    del trailing_positions[symbol]
                    continue

            # =====================
            # BUY
            # =====================
            else:
                if len(open_positions) >= MAX_OPEN_POSITIONS:
                    continue
                if last_close > avg_close and market_is_bullish():
                    qty = calculate_position_size(account_equity, last_close)
                    print(f"{symbol}: BUY {qty} Aktien")
                    logging.info(f"{symbol}: BUY {qty} Aktien")
                    trading_client.submit_order(
                        MarketOrderRequest(
                            symbol=symbol,
                            qty=qty,
                            side=OrderSide.BUY,
                            time_in_force=TimeInForce.GTC
                        )
                    )
                    trades_this_cycle += 1

        print("\nWarte auf nächsten Durchlauf...\n")
        time.sleep(INTERVAL_SECONDS)

    except Exception as e:
        logging.error(f"Fehler: {e}")
        print(f"Fehler: {e}")
        time.sleep(30)