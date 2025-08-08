# Arbitrage Screener (REST mode - ccxt)

This is a deployment-ready demo of an **arbitrage screener** using **ccxt (REST mode)** and **Streamlit**.
It includes basic triangular and cross-exchange scanning logic intended for demo and educational purposes.

## What is included
- `main.py` — Streamlit app
- `requirements.txt` — Python dependencies
- `.streamlit/config.toml` — optional theme

## Mobile deployment steps (no PC required)
1. **Download the zip** to your phone and extract it.
2. **Create a GitHub repo** from your phone:
   - Open GitHub in your mobile browser.
   - Create a new repository.
   - Use "Add file" → "Upload files" to upload all files from the extracted folder.
   - Commit to `main` branch.
3. **Deploy to Streamlit Cloud**
   - Open https://share.streamlit.io/ and log in with GitHub.
   - Click "New app" → select the repo and branch `main` → set main file to `main.py`.
   - Click Deploy.
4. **(Optional) Add Secrets**
   - If you intend to use private exchange API keys, add them in the Streamlit app Settings → Secrets:
     ```
     API_KEY="your_api_key"
     API_SECRET="your_api_secret"
     ```
5. **Run the app** — you'll get a public URL to open in your phone browser.

## Notes & Limitations
- This is a **demo**. For production use:
  - Improve error handling, rate-limit management, and performance.
  - Use `ccxt.pro` or native WebSockets for real-time data.
  - Do not store user API keys unencrypted; use secure vaults.
- Triangular logic here is intentionally simplified and may miss some profitable routes or produce false positives. Use caution.

