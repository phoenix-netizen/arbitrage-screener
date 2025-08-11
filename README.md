
# Arbitrage Scanner (REST mode) — Order Book Depth Validation Included

This GitHub-ready project scans exchanges using **ccxt (REST)** and checks **order-book depth** to ensure an `INVESTMENT_AMOUNT` can actually be filled (conservatively) before reporting an arbitrage opportunity.

## Files
- `main.py` — scanner and demo runner
- `requirements.txt` — pip dependencies
- `.gitignore` — ignores caches and secrets (not included here)

## How to run locally / Colab / Streamlit
1. Install Python 3.10+ locally or use Google Colab.
2. Install deps: `pip install -r requirements.txt`
3. Run: `python main.py`
4. Output CSVs: `triangular_opportunities_depth.csv`, `cross_exchange_opportunities_depth.csv`

## Notes & Limitations
- This is a **demo/POC**. Trade execution requires much more careful handling.
- Triangular route simulation is conservative and heuristic-based — it may miss complex symbol permutations.
- Scanning many exchanges may hit rate limits; prefer running with 3–8 exchanges on free tiers.
- You can change config params at the top of `main.py`:
  - `DEFAULT_EXCHANGES`, `INVESTMENT_AMOUNT`, `ORDERBOOK_DEPTH_LEVELS`, `MIN_PROFIT_PCT`, etc.
