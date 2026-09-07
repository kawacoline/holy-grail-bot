# Polymarket V4 Orderflow Bot Strategy (Client 2)


This document provides a highly detailed pseudo-code and architectural breakdown of the **V4 Orderflow Multi-Asset Strategy** used by Client 2 (`clients/client2.env`). It traces the exact workflow from start-up, through portfolio cleanup, market discovery, momentum filtering, and finally order execution.

---

## 1. Startup & Auto-Redeem (Portfolio Cleanup)
Before the bot even looks for new markets, it sweeps the wallet to secure profits and clean up "ghost" tokens from previous trading sessions.

**The Workflow:**
1. Call `get_positions` on the `Data API` to fetch all outcome tokens currently held by the wallet.
2. Filter for tokens belonging to markets that have **resolved** (ended).
3. If the token payout is `$0` (the condition lost), the bot executes a `safeTransferFrom` transaction to send the token to the `0x0000...dEaD` burn address. This removes clutter from the UI.
4. If the token payout is `$1` (the condition won), the bot routes a transaction through the `CtfCollateralAdapter` (or `NegRiskCtfCollateralAdapter`). 
5. The adapter burns the winning tokens and issues pure `pUSD` back into the wallet.

*Note: This process repeats automatically every hour to continuously clean up expired 1-hour markets.*

---

## 2. Market Discovery
The V4 strategy specializes in the highly active **1-Hour Up/Down Crypto Markets** (BTC, ETH, SOL, XRP).

**The Workflow:**
1. The bot computes the exact text "slug" of the upcoming hour based on the Eastern Timezone (ET). 
   * *Example:* If it is 11:15 AM ET, it computes the target for the next hour: `bitcoin-up-or-down-may-8-2026-12pm-et`.
2. It queries the **Gamma API** (`https://gamma-api.polymarket.com/events?slug={slug}`).
3. From the JSON response, it extracts the `market_id`, the `yes_token_id` (UP), and the `no_token_id` (DOWN).
4. If it fails to guess the slug exactly, it fetches all recent events from Gamma and filters them using a prefix search (`btc-up-or-down-`).

---

## 3. Orderbook Polling & Momentum Filters
Once the markets are found, the bot enters a high-speed continuous loop. It does **not** blindly place orders. It waits for the perfect market conditions.

**The Workflow:**
1. The bot takes all unplaced token IDs (Yes and No for all 4 assets) and makes a **single batch POST request** to `https://clob.polymarket.com/books`.
2. This single request returns the full Orderbook (all Bids and Asks) + the `last_trade_price` for every token.
3. **Filter 1 (Supply Wall):** The bot iterates through the `bids` array in the orderbook. It sums up all the shares sitting between the current price and the target `$0.45` price. If there are more than `50,000` shares in the way (a "Whale Wall"), it aborts. It only places orders in thin, fast-moving books.
4. **Filter 2 (Momentum):** The bot checks the `last_trade_price`. If the `last_trade_price` is 0, it means nobody has traded this hour yet. The bot aborts. It requires active market participation before risking capital.

---

## 4. Dynamic Order Sizing & the $1.00 Constraint
The strategy aims to buy shares at exactly `$0.45`. However, Polymarket has a strict rule: **No order can be worth less than $1.00 total value.**

If the bot simply ordered 1 share at `$0.45`, the total value is `$0.45`. The Polymarket CLOB API would reject this with a `400 Bad Request`.

**The Workflow:**
1. The bot calculates `min_value_size = math.ceil(1.00 / 0.45)`. (Which equals 3 shares).
2. It queries the orderbook's explicit `min_order_size` parameter (often 5 shares).
3. It takes your `.env` configured `ORDER_SIZE` (e.g., 5).
4. It dynamically picks the maximum of all these constraints: `max(base_size, asset_min_size, min_value_size)`.
5. This ensures the bot never submits an order that violates the $1.00 exchange rule, while still securing the target $0.45 entry price.

---

## 5. Visualizing the Limit Order (Maker Orders)
When the filters pass, the bot places the orders. These are **Limit Orders** set as `GTC` (Good-Til-Cancelled).

**The Pseudo-Code:**
```python
# Place the UP order
client.create_and_post_order(
    token_id=yes_token_id,
    price=0.45,                 # We only want to pay $0.45 maximum
    size=dynamic_size,          # E.g., 5 shares
    side="BUY",                 # We are buying the outcome
    order_type="GTC"            # Good-Til-Cancelled (passive Maker order)
)

# Place the DOWN order
client.create_and_post_order(
    token_id=no_token_id,
    price=0.45,
    size=dynamic_size,
    side="BUY",
    order_type="GTC"
)
```

**What this looks like on the exchange:**
Because the current price of a 50/50 market is usually around `$0.50`, a limit BUY at `$0.45` will **not** execute immediately.
Instead, the bot becomes a **Market Maker**. The orders are pushed into the Polymarket Orderbook and sit there visibly. 

If the market price crashes down to $0.45, someone else will "cross the spread" and fill the bot's passive order. If both the UP and DOWN orders are filled during volatility, the bot has secured both sides for $0.90 total, guaranteeing a $1.00 payout at the end of the hour — a risk-free 11% arbitrage.
