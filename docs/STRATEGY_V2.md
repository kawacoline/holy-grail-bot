# Dump–Hedge Strategy — Client Overview (v2)

## What problem does it solve?

On Polymarket’s 15-minute BTC Up/Down markets, **Up + Down prices should sum to ~$1**. When one side briefly “dumps” (sells off), the other side is often overpriced. That creates a window to **buy the cheap side first, then buy the other side when it’s also cheap**, so **total cost < $1** and one side is guaranteed to pay $1 at settlement. The bot automates finding that window and executing both legs.

---

## Value proposition (one sentence)

**We buy the side that dumps, then hedge by buying the opposite side only when the combined cost is below a target (e.g. 95¢), locking in a known edge per share with defined risk.**

---

## How it works (simple version)

1. **Watch** — For the first N minutes of each 15-minute round, we monitor Up and Down ask prices.
2. **Leg 1 — “Buy the dump”** — If one side drops by a set percentage (e.g. 15%) within a short window (e.g. 3 seconds), we buy that side. We’re buying temporary mispricing, not guessing direction.
3. **Leg 2 — “Hedge when cheap”** — We only buy the opposite side when **Leg1 price + opposite price ≤ target** (e.g. 0.95). That locks in a spread: we hold both outcomes, one pays $1, so profit ≈ (1.00 − total cost) × size.
4. **Stop loss** — If the opposite side never gets cheap enough within a time limit, we either hedge anyway at worse terms or sell Leg 1 (configurable). So risk is bounded, not open-ended.
5. **Settlement** — When the market resolves, we redeem the winning side. No directional bet on BTC—we’re harvesting the spread between combined cost and $1.

---

## Why this can be compelling for a client

| Point | Explanation |
|-------|-------------|
| **Defined edge** | Profit per share = $1 − (price_leg1 + price_leg2). We only open Leg 2 when that is positive (e.g. total cost ≤ 0.95). |
| **Not directional** | We don’t bet “Up or Down.” We bet that **combined cost stays below $1** and that one side will pay $1. |
| **Automated discipline** | Entries and hedge/stop-loss rules are coded. No emotional overtrading or skipping the hedge. |
| **Transparent logic** | Dump threshold, hedge target, wait time, and stop-loss method are all configurable and auditable. |
| **Controlled downside** | Max wait for hedge + stop-loss (hedge anyway or sell Leg 1) caps how long we’re one-sided. |

---

## Risks and caveats (be upfront with the client)

- **Execution** — We buy at current ask. Slippage or illiquidity can make actual fill worse than the “target” price.
- **Hedge may not come** — If the opposite side never gets cheap enough, we either hedge at a worse price (smaller or negative edge) or sell Leg 1 (possible loss). That’s why stop-loss rules exist.
- **Market structure** — Strategy assumes 15-minute BTC Up/Down markets and that one side eventually pays $1. If Polymarket changes product or rules, the logic may need updates.
- **No guarantee of dumps** — We only trade when a dump is detected. In quiet periods there may be few or no trades.

---

## Talking points for the pitch

1. **“We’re not guessing if BTC goes up or down.”**  
   We’re capturing moments when both sides can be bought for less than $1 combined.

2. **“The edge is in the numbers.”**  
   When we add the second leg, we do it only when total cost is below our target (e.g. 95¢), so the expected profit per share is explicit.

3. **“Risk is bounded.”**  
   We have a maximum wait time and a clear rule: either hedge (and lock in whatever spread we get) or sell the first leg. We don’t hold one side indefinitely.

4. **“It’s systematic.”**  
   Rules are in code: when to enter, when to hedge, when to stop loss. That makes the strategy reviewable and consistent.

5. **“We can tune it to your risk tolerance.”**  
   Size, hedge target, dump sensitivity, and stop-loss method (hedge vs sell) can all be adjusted.

---

## Optional: one-line version for emails/slides

**“We buy the side that dumps, hedge only when the combined cost is below our target, and use a time-based stop loss—so we’re harvesting spread with bounded risk, not betting on BTC direction.”**
