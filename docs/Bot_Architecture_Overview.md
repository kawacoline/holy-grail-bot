# Holy Grail Bot Architecture Map / PseudoCode
Here is the exact map of **which `.py` files execute which part of the V4 strategy and the overall bot workflow**.

---

## 1. The Entry Point & Process Manager
**Files:** `START.bat` ➔ `auto_updater.py`

When you double-click `START.bat`, it executes `auto_updater.py`. This Python file acts as the primary process manager for the entire system:
1. It spawns a background thread to run the FastAPI web server (`web_server.py` at `http://127.0.0.1:8000`).
2. It spawns the main trading engine (`dashboard.py`) as a separate subprocess.
3. It monitors GitHub for updates and listens for exit code `69` (Circuit Breaker) to halt the system if safety limits are breached.

---

## 2. The Main Trading Engine
**File:** `dashboard.py`

This is the heartbeat of the bot. It uses the `rich` library to render the live console interface.
Inside the `run_dashboard()` async loop, it evaluates which clients are loaded (from `clients/*.env`). If a client has `STRATEGY=V4`, it initializes the `V4OrderFlowBot` class.

**The Loop Workflow:**
At 10 iterations per second, `dashboard.py` executes the following on the `V4OrderFlowBot` object:
1. Calls `bot.check_limit_strategy()`
2. If the check returns an action payload, it calls `bot.execute_limit_strategy(action)`.
3. Every X minutes (or when a market expires), it calls `_auto_redeem_quiet(bot)`.
4. Every loop, it calls `_dump_state()` to save the live stats to `data/dashboard_state.json` for the web UI.

---

## 3. The Strategy Logic (V4 Orderflow)
**File:** `src/v4_orderflow_bot.py`

This file contains the pseudo-code logic outlined in the V4 Strategy Breakdown. It is entirely self-contained.

**Key Functions:**
- **`find_upcoming_1h_markets()` & `find_1h_markets_via_gamma()`:** Computes the ET timezone slug (e.g. `btc-up-or-down-...`) and hits `https://gamma-api.polymarket.com/events` to extract the `yes_token_id` and `no_token_id`.
- **`check_limit_strategy()`:** Uses a persistent `httpx.Client` to batch-fetch orderbooks from `https://clob.polymarket.com/books`. It extracts the `bids` array to calculate "supply walls" (Filter 1) and checks `last_trade_price` to confirm momentum (Filter 2). It also dynamically extracts `min_order_size` from the orderbook response.
- **`execute_limit_strategy()`:** Calculates the `$1.00` minimum order value constraint (`max(base_size, asset_min_size, math.ceil(1.00 / 0.45))`). It then calls the trading helper to place the limit orders.

---

## 4. The CLOB API Execution
**File:** `src/trading.py`

When `v4_orderflow_bot.py` decides it's time to place an order, it calls the `place_order()` function located in `src/trading.py`.

**What happens here:**
1. It leverages the official `py_clob_client_v2` SDK.
2. It initializes the `ClobClient` using the `.env` settings (L1 Private Key, Signature Type, Funder).
3. **API Keys:** It calls `client.create_or_derive_api_key()`. *(This is where the harmless `400 Could not create api key` warning prints if the key already exists on the Polymarket server, before it derives the existing key)*.
4. **Order Execution:** `place_order()` constructs the payload and submits the `GTC` Maker limit orders to the Polymarket orderbook.

---

## 5. Portfolio Cleanup & Auto-Redemption
**File:** `src/redeem.py`

Triggered automatically by `dashboard.py` (via `_auto_redeem_quiet`), this file handles the complex V2 settlement logic.

**Key Functions:**
- **`auto_redeem_portfolio()`:** Queries the Data API for active positions in the wallet. Filters out tokens for markets that have "Resolved".
- **Ghost Token Burn:** Identifies tokens that paid out `$0`. It constructs a `safeTransferFrom` transaction to manually burn them to `0x0000...dEaD`.
- **V2 Collateral Routing:** For winning tokens, it checks if it's a NegRisk market. It then routes the redemption payload to either the `CtfCollateralAdapter` or `NegRiskCtfCollateralAdapter`.
- **Approval Auto-Gate:** `_ensure_ctf_adapter_approved()` verifies that the wallet has granted `setApprovalForAll` to the adapter on the CTF contract, and signs the transaction if it hasn't, before executing the pure-`pUSD` redemption.

---

### Summary of Data Flow
`START.bat` ➔ `auto_updater.py` ➔ `dashboard.py` (The Loop) ➔ `src/v4_orderflow_bot.py` (The Brains) ➔ `src/trading.py` (The Hands) ➔ `src/redeem.py` (The Janitor)
