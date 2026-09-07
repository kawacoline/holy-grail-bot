# V4 Strategy: Order Flow & Book Depth Filter

## Overview
The V4 Strategy builds upon the V3 Limit Strategy by solving its biggest risk: **The 1-Sided Fill**.

In V3, the bot blindly placed a $0.45 UP limit and a $0.45 DOWN limit, waiting for both to get hit. If the market only moved in one direction, the bot suffered a loss.

V4 upgrades this by acting as an "intelligent sniper" rather than setting blind traps, incorporating two Wall Street concepts: **Order Book Depth** and **Order Flow Momentum**, powered by a **Blazing Fast Batch Scan Engine**.

---

## 1. The Scan Engine (Dancing with the Rate Limits)

The V4 bot is built for extreme speed to catch market volatility.
- **Batch /books API**: Instead of checking 8 order books individually (4 assets x 2 sides), V4 fetches the entire state of the market in **one single POST request** to the `/books` endpoint.
- **Persistent HTTP Client**: Uses HTTP/2 connection pooling. No TLS handshakes per scan.
- **Free Momentum Data**: The `/books` endpoint returns `last_trade_price` natively, completely eliminating the need to poll `/trades` API for momentum.
- **Performance**: Capable of scanning **~47 times every 10 seconds** (~0.2s per scan) while utilizing less than 10% of Polymarket's rate limit budget.

---

## 2. The Order Book Depth Filter (Avoiding Resistance)

Before the bot places a limit order, it checks the **Order Book** for "Supply Walls".
- **The Concept:** Think of the order book like a see-saw. If there are a massive amount of orders sitting between the current price and our $0.45 target price, the market will struggle to drop down to our level.
- **The Mechanism:** The bot scans all *Bids* (buyers) sitting above our $0.45 target. If the combined size of those bids exceeds `MAX_WALL_SIZE` (e.g., 3,000 shares), the bot considers the path blocked.
- **The Outcome:** We do not place an order. We only deploy capital when the path to $0.45 is "thin" / easy for the price to move through without major resistance.

---

## 3. The Order Flow Trigger (Riding Momentum)

Even if the path is clear, an asset might just sit still. V4 solves this by waiting for **Momentum**.
- **The Concept:** The bot waits until actual trades are happening on the book.
- **The Mechanism:** By inspecting the `last_trade_price` from the fast batch query, the bot knows if the token is actively trading or stagnant.
- **The Outcome:** The bot says: *Wait until the market is actively moving before we set our GTC limit traps.*

---

## 4. Configuration & `.env` Setup

Each instance of the bot runs via client `.env` files (e.g., `clients/client2.env`). 
Crucial settings for V4:

- `ORDER_SIZE`: The base number of shares to buy per side. Polymarket enforces minimums, so the bot **dynamically scales this**.
- `DRY_RUN=false`: Set to true for simulation, false for live trading.
- `MAX_WALL_SIZE`: Controls how strict the Order Book Depth filter is. Lower = safer (requires thinner books), Higher = more aggressive.
- `MIN_MOMENTUM_VOLUME`: The minimum amount of trading action required to trigger the bot.

---

## 5. Dynamic Sizing & Minimum Capital

Polymarket V2 has strict order minimums: **$1 minimum value** and a dynamic **min_order_size** per asset (often 5 shares).

**How the Bot Handles This:**
1. It reads the exact `min_order_size` directly from the Polymarket API for that specific asset.
2. It calculates `required_size = max(env_size, min_order_size, ceil($1.00 / price))`.
3. If the user sets `ORDER_SIZE=1` but the market requires 5 shares, the bot automatically bumps it to 5.

**Minimum Capital Required:**
At $0.45 target price, placing orders on both sides (Up + Down) for a single asset requires:
- `5 shares * $0.45 * 2 sides = $4.50` per asset.
- To safely trade **all 4 assets** (BTC, ETH, SOL, XRP) simultaneously, the recommended minimum capital is **$18.00 pUSD** per wallet.
- **Balance Guard:** The bot calculates your exact available `pUSD` before trading. If you only have $11.00, it will place limits on 2 assets ($9.00 total) and intelligently skip the remaining assets to prevent API rejection spam.

---

## 6. Execution Mechanics

- **No `post_only`**: Orders are sent as standard `GTC` (Good-Til-Cancelled) limits. If the market is already trading at or below $0.45 when our trigger fires, the order fills instantly. Otherwise, it rests passively on the book.
- **pUSD Approval**: V4 automatically checks if the wallet has approved the Polymarket Exchange contracts to spend `pUSD`. If allowance is 0, it calls `approve_pusd.py` concepts internally.
- **Cooldowns**: If an order fails (e.g., balance too low), the bot marks the asset as `placed` and enters a cooldown for that hour's market, ensuring it doesn't spam Polymarket 47 times a second with failed requests.
