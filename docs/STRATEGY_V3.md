# Limit Order Pre-Place Strategy (v3)

## Overview

The **main strategy** in v3 is **pre-placing limit orders** for **upcoming** markets, rather than reacting to live prices after a market opens.

---

## How it works

- **Markets:** Primarily **1-hour** markets (not 15-minute).
- **Assets:** BTC, ETH, SOL, XRP.
- **Mechanism:** Place **limit orders** on **both sides** (Up and Down) at a fixed price: **$0.45**.
- **Timing:** Orders are placed **before** or at the start of the upcoming market, so they are resting in the book when the market goes live.

---

## Why pre-place at $0.45?

- **Combined cost target:** Up at $0.45 + Down at $0.45 = **$0.90** total. If both legs fill, profit per share = $1.00 − $0.90 = **$0.10** (about 11% on cost).
- **Passive execution:** Instead of chasing the market, the bot posts limit orders and waits for the market to come to them. In volatile or illiquid opens, one or both sides may fill at or better than $0.45.
- **Multi-asset, 1-hour focus:** Spreads the approach across BTC, ETH, SOL, and XRP on 1-hour resolution, which can offer different liquidity and volatility than 15-minute BTC-only.

---

## Summary

| Aspect | v3 (Limit Order Pre-Place) |
|--------|----------------------------|
| **Market type** | 1-hour Up/Down |
| **Assets** | BTC, ETH, SOL, XRP |
| **Order type** | Limit orders, both sides |
| **Price** | $0.45 per side (target combined $0.90) |
| **Edge** | Harvest spread when both legs fill at or below target |

