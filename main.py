import streamlit as st
import ccxt
import pandas as pd
import time
from typing import List, Dict, Tuple

st.set_page_config(page_title="Arbitrage Screener (REST)", layout="wide")

st.title("Arbitrage Screener — REST mode (ccxt)")

with st.sidebar:
    st.header("Settings")
    exchanges_input = st.text_input("Exchanges (comma-separated, e.g. binance,bybit,kucoin)", "binance")
    investment = st.number_input("Investment amount (base currency)", min_value=1.0, value=1000.0, step=1.0)
    min_profit = st.number_input("Minimum profit % to show", min_value=0.0, value=0.2, step=0.1)
    max_markets = st.number_input("Max markets to scan per exchange (for speed)", min_value=20, max_value=1000, value=120, step=10)
    fee_percent = st.number_input("Estimated round-trip fee % (all legs combined)", min_value=0.0, value=0.2, step=0.01)
    refresh = st.button("Scan now")

st.markdown("""
This app runs in **REST mode** using `ccxt` (no ccxt.pro required).  
It performs a **basic** triangular and cross-exchange scan for educational/demo purposes.  
""")


@st.cache_data(ttl=30)
def load_exchanges(names: List[str]) -> Dict[str, ccxt.Exchange]:
    exs = {}
    for name in names:
        try:
            cls = getattr(ccxt, name)
            ex = cls({'enableRateLimit': True})
            exs[name] = ex
        except Exception as e:
            st.warning(f"Could not load exchange {name}: {e}")
    return exs

def safe_fetch_markets(exchange: ccxt.Exchange, limit: int):
    try:
        markets = exchange.load_markets()
        items = list(markets.items())[:limit]
        return {k:v for k,v in items}
    except Exception as e:
        st.warning(f"Error loading markets for {exchange.id}: {e}")
        return {}

def fetch_order_book_safe(exchange: ccxt.Exchange, symbol: str):
    try:
        ob = exchange.fetch_order_book(symbol)
        return ob
    except Exception:
        return None

def get_common_symbols(ex_a: Dict, ex_b: Dict) -> List[str]:
    s_a = set(ex_a.keys())
    s_b = set(ex_b.keys())
    return list(s_a & s_b)

def estimate_triangular_profit_from_ticker_prices(t1: float, t2: float, t3: float, fees: float):
    # Simplified model: multiply rates and subtract fees
    # t1: price A->B (i.e., how many B per A), etc.
    return (t1 * t2 * t3 - 1.0) * 100.0 - fees

def scan_triangular_for_exchange(name: str, ex: ccxt.Exchange, max_markets:int, investment:float, fee_percent:float):
    results = []
    markets = safe_fetch_markets(ex, max_markets)
    symbols = list(markets.keys())
    # Build quick lookup for pairs like BASE/QUOTE -> price
    ticker_prices = {}
    for s in symbols:
        try:
            t = ex.fetch_ticker(s)
            # We'll use mid price approximation
            mid = (t.get('bid') or t.get('last') or 0) and (t.get('ask') or t.get('last') or 0)
            # fallback if bid/ask exist
            b = t.get('bid') or t.get('last') or 0
            a = t.get('ask') or t.get('last') or b or 0
            if a and b:
                midp = (a + b) / 2.0
            else:
                midp = t.get('last') or 0
            ticker_prices[s] = {'bid': t.get('bid') or midp, 'ask': t.get('ask') or midp, 'mid': midp}
        except Exception:
            continue
    # Attempt naive triangular combos: A/B, B/C, C/A
    # Build list of currencies
    currencies = set()
    for s in symbols:
        if '/' in s:
            b,q = s.split('/')
            currencies.add(b)
            currencies.add(q)
    currencies = list(currencies)
    # Limit currencies for speed
    if len(currencies) > 50:
        currencies = currencies[:50]
    for i in range(len(currencies)):
        for j in range(len(currencies)):
            for k in range(len(currencies)):
                if i==j or j==k or i==k:
                    continue
                A = currencies[i]
                B = currencies[j]
                C = currencies[k]
                s1 = f"{A}/{B}"
                s2 = f"{B}/{C}"
                s3 = f"{C}/{A}"
                if s1 in ticker_prices and s2 in ticker_prices and s3 in ticker_prices:
                    # assume path A -> B -> C -> A
                    # when buying A->B we use ask of s1 (we spend A to get B) etc.
                    p1 = 1.0 / ticker_prices[s1]['ask'] if ticker_prices[s1]['ask'] else None
                    # Actually for currency conversions it's complex; we'll approximate using mid prices.
                    try:
                        rate1 = 1.0 / ticker_prices[s1]['ask'] if ticker_prices[s1]['ask']>0 else None
                        rate2 = 1.0 / ticker_prices[s2]['ask'] if ticker_prices[s2]['ask']>0 else None
                        rate3 = 1.0 / ticker_prices[s3]['ask'] if ticker_prices[s3]['ask']>0 else None
                    except Exception:
                        continue
                    if not rate1 or not rate2 or not rate3:
                        continue
                    gross = rate1 * rate2 * rate3
                    profit_pct = (gross - 1.0) * 100.0 - fee_percent
                    if profit_pct >= min_profit:
                        results.append({
                            "exchange": name,
                            "route": f"{A} -> {B} -> {C} -> {A}",
                            "profit_percent": round(profit_pct, 4),
                            "gross_multiplier": round(gross,6)
                        })
    return results

def scan_cross_exchange(exchanges: Dict[str, ccxt.Exchange], investment: float, fee_percent: float):
    results = []
    # load markets for all
    markets_by_ex = {}
    for name, ex in exchanges.items():
        markets_by_ex[name] = safe_fetch_markets(ex, max_markets)
    # Compare pairwise
    names = list(exchanges.keys())
    for i in range(len(names)):
        for j in range(i+1, len(names)):
            a = names[i]
            b = names[j]
            common = get_common_symbols(markets_by_ex[a], markets_by_ex[b])
            for sym in common:
                ob_a = fetch_order_book_safe(exchanges[a], sym)
                ob_b = fetch_order_book_safe(exchanges[b], sym)
                if not ob_a or not ob_b:
                    continue
                # price to buy on A = best ask on A, sell on B = best bid on B
                ask_a = ob_a.get('asks')[0][0] if ob_a.get('asks') else None
                bid_b = ob_b.get('bids')[0][0] if ob_b.get('bids') else None
                if not ask_a or not bid_b:
                    continue
                # potential profit %
                if ask_a == 0:
                    continue
                gross = bid_b / ask_a
                profit_pct = (gross - 1.0) * 100.0 - fee_percent
                if profit_pct >= min_profit:
                    results.append({
                        "pair": sym,
                        "buy_exchange": a,
                        "sell_exchange": b,
                        "buy_price": ask_a,
                        "sell_price": bid_b,
                        "profit_percent": round(profit_pct,4)
                    })
                # reverse direction
                ask_b = ob_b.get('asks')[0][0] if ob_b.get('asks') else None
                bid_a = ob_a.get('bids')[0][0] if ob_a.get('bids') else None
                if ask_b and bid_a and ask_b>0:
                    gross2 = bid_a / ask_b
                    profit2 = (gross2 - 1.0) * 100.0 - fee_percent
                    if profit2 >= min_profit:
                        results.append({
                            "pair": sym,
                            "buy_exchange": b,
                            "sell_exchange": a,
                            "buy_price": ask_b,
                            "sell_price": bid_a,
                            "profit_percent": round(profit2,4)
                        })
    return results

def main():
    names = [n.strip() for n in exchanges_input.split(",") if n.strip()]
    if not names:
        st.warning("Enter at least one exchange id (e.g., binance).")
        return
    with st.spinner("Loading exchanges..."):
        exchanges = load_exchanges(names)
    st.write("Loaded exchanges:", list(exchanges.keys()))
    global min_profit, max_markets
    # use values from widget
    min_profit = min_profit = float(min_profit_widget()) if callable(min_profit_widget:=lambda: min_profit) else min_profit
    # scan on button press
    if refresh:
        start = time.time()
        all_tri = []
        for name, ex in exchanges.items():
            tri = scan_triangular_for_exchange(name, ex, max_markets=int(max_markets), investment=investment, fee_percent=float(fee_percent))
            all_tri.extend(tri)
        df_tri = pd.DataFrame(all_tri)
        if not df_tri.empty:
            df_tri = df_tri.sort_values(by='profit_percent', ascending=False)
            st.subheader("Triangular Opportunities (sorted)")
            st.dataframe(df_tri)
            csv = df_tri.to_csv(index=False)
            st.download_button("Download triangular CSV", csv, file_name="triangular_opportunities.csv")
        else:
            st.info("No triangular opportunities found above the specified profit threshold.")
        cross = scan_cross_exchange(exchanges, investment, float(fee_percent))
        df_cross = pd.DataFrame(cross)
        if not df_cross.empty:
            df_cross = df_cross.sort_values(by='profit_percent', ascending=False)
            st.subheader("Cross-Exchange Opportunities (sorted)")
            st.dataframe(df_cross)
            csv2 = df_cross.to_csv(index=False)
            st.download_button("Download cross-exchange CSV", csv2, file_name="cross_exchange_opportunities.csv")
        else:
            st.info("No cross-exchange opportunities found above the specified profit threshold.")
        st.success(f"Scan completed in {round(time.time()-start,2)}s")

if __name__ == "__main__":
    main()
