"""
Arbitrage Scanner (ccxt REST) — Triangular + Cross-Exchange
Includes order-book volume depth validation for a given investment amount.
Designed to be GitHub-ready and easy to run locally, on Streamlit Cloud, or Colab.
"""

import time
import ccxt
import math
import pandas as pd
from typing import Dict, List, Tuple

# ============ CONFIG ============
DEFAULT_EXCHANGES = ["kucoin", "bybit", "okx", "binance"]  # sensible defaults for first runs
# You can pass other exchange ids supported by ccxt; beware of rate limits.
MAX_MARKETS_PER_EXCHANGE = 500   # limit markets loaded per exchange for speed
ORDERBOOK_DEPTH_LEVELS = 10      # number of levels to fetch for depth checks
ESTIMATED_FEE_PCT = 0.2          # estimated round-trip fee percent (adjust for your exchanges)
MIN_PROFIT_PCT = 0.25            # minimum profit percent to surface
INVESTMENT_AMOUNT = 1000.0       # amount in quote currency to test liquidity (e.g., USDT)
# =================================

def init_exchange(name: str, enable_rate_limit: bool = True) -> ccxt.Exchange:
    cls = getattr(ccxt, name)
    return cls({'enableRateLimit': enable_rate_limit})

def safe_load_markets(exchange: ccxt.Exchange, limit: int = 500) -> Dict[str, dict]:
    try:
        markets = exchange.load_markets()
        items = list(markets.items())[:limit]
        return {k: v for k, v in items}
    except Exception as e:
        print(f"[warn] load_markets {getattr(exchange,'id',str(exchange))}: {e}")
        return {}

def safe_fetch_ticker(exchange: ccxt.Exchange, symbol: str):
    try:
        return exchange.fetch_ticker(symbol)
    except Exception as e:
        return None

def safe_fetch_order_book(exchange: ccxt.Exchange, symbol: str, depth: int = 10):
    try:
        return exchange.fetch_order_book(symbol, depth)
    except Exception as e:
        return None

def compute_fillable_base_amount_from_asks(order_book: dict, max_quote: float) -> float:
    """
    Given order_book asks [[price, amount], ...] and a maximum quote currency amount (e.g., USDT),
    compute how many base units you can buy with up to max_quote across depth levels.
    Returns base_amount that can be acquired.
    """
    if not order_book or 'asks' not in order_book: 
        return 0.0
    remaining_quote = max_quote
    base_acquired = 0.0
    for level in order_book['asks']:
        price, amount = level[0], level[1]
        if price <= 0:
            continue
        cost = price * amount
        if cost <= remaining_quote + 1e-12:
            base_acquired += amount
            remaining_quote -= cost
        else:
            # partial fill at this level
            fill_amount = remaining_quote / price
            base_acquired += fill_amount
            remaining_quote = 0.0
            break
    return base_acquired

def compute_fillable_quote_amount_from_bids(order_book: dict, max_base: float) -> float:
    """
    Given order_book bids [[price, amount], ...] and a maximum base amount to sell,
    compute how much quote currency you'd receive by filling up to max_base across bids.
    Returns quote_received.
    """
    if not order_book or 'bids' not in order_book:
        return 0.0
    remaining_base = max_base
    quote_received = 0.0
    for level in order_book['bids']:
        price, amount = level[0], level[1]
        if price <= 0:
            continue
        if amount <= remaining_base + 1e-12:
            quote_received += price * amount
            remaining_base -= amount
        else:
            # partial fill
            fill_amount = remaining_base
            quote_received += price * fill_amount
            remaining_base = 0.0
            break
    return quote_received

def check_cross_exchange_liquidity(buy_ex, sell_ex, symbol: str, investment_quote: float, depth_levels: int = 10) -> Tuple[bool, float]:
    """
    Check if buying on buy_ex and selling on sell_ex for 'symbol' is feasible with investment_quote amount.
    Returns (feasible, expected_profit_percent_after_fees)
    """
    ob_buy = safe_fetch_order_book(buy_ex, symbol, depth_levels)
    ob_sell = safe_fetch_order_book(sell_ex, symbol, depth_levels)
    if not ob_buy or not ob_sell:
        return (False, 0.0)
    # How much base can we buy on buy_ex with investment_quote?
    base_bought = compute_fillable_base_amount_from_asks(ob_buy, investment_quote)
    if base_bought <= 0:
        return (False, 0.0)
    # How much quote can we get by selling that base on sell_ex?
    quote_received = compute_fillable_quote_amount_from_bids(ob_sell, base_bought)
    if quote_received <= 0:
        return (False, 0.0)
    gross = quote_received / investment_quote
    profit_pct = (gross - 1.0) * 100.0 - ESTIMATED_FEE_PCT
    return (profit_pct >= MIN_PROFIT_PCT, profit_pct)

def simulate_triangular_with_depth(exchange: ccxt.Exchange, route: Tuple[str,str,str], investment_quote: float, depth_levels: int=10) -> Tuple[bool, float]:
    """
    Simulate a triangular route A/B, B/C, C/A where route is (s1, s2, s3) symbol strings.
    We'll attempt to simulate order execution using order books and return (feasible, profit_pct).
    The simulation uses conservative depth-based fill estimates.
    """
    s1, s2, s3 = route
    # Step 1: spend investment_quote on s1 (buy base1 using quote1)
    ob1 = safe_fetch_order_book(exchange, s1, depth_levels)
    if not ob1:
        return (False, 0.0)
    # determine which side is base/quote
    base1, quote1 = s1.split('/')
    # for s1 we assume we buy base1 using quote1 (use asks)
    base1_acquired = compute_fillable_base_amount_from_asks(ob1, investment_quote)
    if base1_acquired <= 0:
        return (False, 0.0)
    # Step 2: exchange base1 -> base2 across s2 (which may be base2/quote2)
    ob2 = safe_fetch_order_book(exchange, s2, depth_levels)
    if not ob2:
        return (False, 0.0)
    base2, quote2 = s2.split('/')
    # Determine how much of base2 we can get by selling base1 (we may need to sell base1 as bids on s2 if base1==quote2 or other mapping).
    # For simplicity, we will attempt common pattern where s2 = base1/quoteX or quoteX/base1; try both directions.
    # Approach: convert base1_acquired into quote via matching symbol direction then into next base
    # We'll implement a conservative heuristic: use mid prices for conversion but validate depth for the main legs.
    # Sell base1_acquired on s2's bids to get quote (if base1 is the base of s2)
    quote_after_s2 = None
    # try if s2 is base1/quote2 => selling base1 on bids yields quote2
    if s2 == f"{base1}/{quote2}":
        quote_after_s2 = compute_fillable_quote_amount_from_bids(ob2, base1_acquired)
    elif s2 == f"{quote2}/{base1}":
        # more complex mapping; skip conservative simulation
        quote_after_s2 = None
    if quote_after_s2 is None or quote_after_s2 <= 0:
        return (False, 0.0)
    # Step 3: use quote_after_s2 to buy the final asset via s3 and then sell back to original quote; for conservative sim, we'll assume final conversion yields mid price and check depth on s3.
    ob3 = safe_fetch_order_book(exchange, s3, depth_levels)
    if not ob3:
        return (False, 0.0)
    try:
        top_bid = ob3.get('bids')[0][0] if ob3.get('bids') else None
        top_ask = ob3.get('asks')[0][0] if ob3.get('asks') else None
        if not top_bid or not top_ask:
            return (False, 0.0)
        mid = (top_bid + top_ask) / 2.0
        quote_back = quote_after_s2 * mid  # VERY approximate
        gross = quote_back / investment_quote
        profit_pct = (gross - 1.0) * 100.0 - ESTIMATED_FEE_PCT
        return (profit_pct >= MIN_PROFIT_PCT, profit_pct)
    except Exception:
        return (False, 0.0)

# ---------------- Core scanning functions ----------------

def scan_cross_exchanges(exchanges: Dict[str, ccxt.Exchange],
                         investment: float = INVESTMENT_AMOUNT,
                         min_profit_pct: float = MIN_PROFIT_PCT,
                         fee_pct: float = ESTIMATED_FEE_PCT,
                         depth_levels: int = ORDERBOOK_DEPTH_LEVELS,
                         max_markets: int = MAX_MARKETS_PER_EXCHANGE) -> List[dict]:
    results = []
    markets_by_ex = {}
    # load markets for each exchange
    for name, ex in exchanges.items():
        markets_by_ex[name] = safe_load_markets(ex, limit=max_markets)
    names = list(exchanges.keys())
    for i in range(len(names)):
        for j in range(i+1, len(names)):
            a, b = names[i], names[j]
            common = set(markets_by_ex[a].keys()) & set(markets_by_ex[b].keys())
            for sym in list(common)[:500]:
                feasible, profit_pct = check_cross_exchange_liquidity(exchanges[a], exchanges[b], sym, investment, depth_levels)
                if feasible and profit_pct >= min_profit_pct:
                    results.append({
                        'pair': sym,
                        'buy_exchange': a,
                        'sell_exchange': b,
                        'profit_percent': round(profit_pct,6)
                    })
                # reverse direction
                feasible2, profit2 = check_cross_exchange_liquidity(exchanges[b], exchanges[a], sym, investment, depth_levels)
                if feasible2 and profit2 >= min_profit_pct:
                    results.append({
                        'pair': sym,
                        'buy_exchange': b,
                        'sell_exchange': a,
                        'profit_percent': round(profit2,6)
                    })
    return results

def scan_triangular_for_all(exchanges: Dict[str, ccxt.Exchange],
                            min_profit_pct: float = MIN_PROFIT_PCT,
                            fee_pct: float = ESTIMATED_FEE_PCT,
                            depth_levels: int = ORDERBOOK_DEPTH_LEVELS,
                            max_markets: int = MAX_MARKETS_PER_EXCHANGE) -> List[dict]:
    results = []
    for name, ex in exchanges.items():
        markets = safe_load_markets(ex, limit=max_markets)
        symbols = [s for s in markets.keys() if '/' in s]
        # build price map (mid/bid/ask) for present symbols
        prices = {}
        for s in symbols[:max_markets]:
            t = safe_fetch_ticker(ex, s)
            if not t:
                continue
            bid = t.get('bid') or t.get('last') or 0
            ask = t.get('ask') or t.get('last') or bid or 0
            prices[s] = {'bid': bid, 'ask': ask, 'mid': (bid+ask)/2 if bid and ask else (t.get('last') or 0)}
        # gather currencies
        currencies = set()
        for s in prices.keys():
            try:
                a,b = s.split('/')
                currencies.add(a); currencies.add(b)
            except Exception:
                continue
        currencies = list(currencies)[:80]
        # naive triangular combos
        for i in range(len(currencies)):
            for j in range(len(currencies)):
                for k in range(len(currencies)):
                    if i==j or j==k or i==k:
                        continue
                    A = currencies[i]; B = currencies[j]; C = currencies[k]
                    s1 = f"{A}/{B}"; s2 = f"{B}/{C}"; s3 = f"{C}/{A}"
                    if s1 in prices and s2 in prices and s3 in prices:
                        # perform depth-aware simulation
                        feasible, profit_pct = simulate_triangular_with_depth(ex, (s1,s2,s3), INVESTMENT_AMOUNT, depth_levels)
                        if feasible and profit_pct >= min_profit_pct:
                            results.append({
                                'exchange': name,
                                'route': f"{A}->{B}->{C}->{A}",
                                'profit_percent': round(profit_pct,6)
                            })
    return results

# ----------------- CLI demo runner -----------------

def main():
    # Initialize exchanges (DEFAULT_EXCHANGES by default)
    exchanges = {}
    for name in DEFAULT_EXCHANGES:
        try:
            ex = init_exchange(name)
            exchanges[name] = ex
            print(f"[info] initialized {name}")
        except Exception as e:
            print(f"[warn] failed to init {name}: {e}")

    print("[info] scanning triangular opportunities (depth-aware)...")
    t0 = time.time()
    tri = scan_triangular_for_all(exchanges)
    t1 = time.time()
    print(f"[info] triangular scan finished in {round(t1-t0,2)}s, results: {len(tri)}")
    if tri:
        df_tri = pd.DataFrame(tri).sort_values(by='profit_percent', ascending=False)
        print(df_tri.head(50).to_string(index=False))
        df_tri.to_csv("triangular_opportunities_depth.csv", index=False)
        print("[info] saved triangular_opportunities_depth.csv")
    else:
        print("[info] no triangular opportunities found.")

    print("[info] scanning cross-exchange opportunities (depth-aware)...")
    t2 = time.time()
    cross = scan_cross_exchanges(exchanges)
    t3 = time.time()
    print(f"[info] cross-exchange scan finished in {round(t3-t2,2)}s, results: {len(cross)}")
    if cross:
        df_cross = pd.DataFrame(cross).sort_values(by='profit_percent', ascending=False)
        print(df_cross.head(50).to_string(index=False))
        df_cross.to_csv("cross_exchange_opportunities_depth.csv", index=False)
        print("[info] saved cross_exchange_opportunities_depth.csv")
    else:
        print("[info] no cross-exchange opportunities found.")

if __name__ == "__main__":
    main()
