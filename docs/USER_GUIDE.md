# User Guide: Holy Grail Bot

This guide is for **people who want to run the bot** without editing code. Follow the steps below to get from zero to running.

---

## Prerequisites

- **Python 3.10+** installed
- A **Polymarket account** at [polymarket.com](https://polymarket.com)
- **USDC** in your Polymarket wallet
- Your **wallet private key** (MetaMask export, or the key linked to your Polymarket account)
- A computer or VPS that stays online 24/7

---

## Step 1: Install

```bash
# Clone the repository
git clone https://github.com/kawacoline/holy-grail-bot.git
cd holy-grail-bot

# Create virtual environment
python -m venv venv

# Activate it
venv\Scripts\activate          # Windows
# source venv/bin/activate     # Linux/Mac

# Install dependencies
pip install -r requirements.txt
```

---

## Step 2: Configure Your Client(s)

Each client is a separate `.env` file inside the `clients/` folder. The bot loads **all** client files on startup and runs them simultaneously.

### Create your client file

Copy the example below and save it as `clients/client1.env`:

```ini
# ── Polymarket Credentials ────────────────────────────
POLYMARKET_PRIVATE_KEY=0xYOUR_PRIVATE_KEY_HERE
POLYMARKET_API_KEY=your_api_key
POLYMARKET_API_SECRET=your_api_secret
POLYMARKET_API_PASSPHRASE=your_passphrase
POLYMARKET_SIGNATURE_TYPE=0

# ── Trading Parameters ────────────────────────────────
DRY_RUN=true
ORDER_SIZE=5
TARGET_PAIR_COST=0.99
SIM_BALANCE=10
COOLDOWN_SECONDS=10

# ── Strategy ──────────────────────────────────────────
# 1 = V1 BTC 15m Arbitrage
# 3 = V3 1-hour Multi-Asset Limit Orders
STRATEGY=1
```

### Configuration Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `POLYMARKET_PRIVATE_KEY` | ✅ | Your wallet private key (starts with `0x`). Keep this secret. |
| `POLYMARKET_API_KEY` | ✅ | CLOB API key (generate with `python -m src.create_api_keys`) |
| `POLYMARKET_API_SECRET` | ✅ | CLOB API secret |
| `POLYMARKET_API_PASSPHRASE` | ✅ | CLOB API passphrase |
| `POLYMARKET_SIGNATURE_TYPE` | ✅ | `0` = MetaMask/EOA, `1` = Magic.link/email login |
| `POLYMARKET_FUNDER` | ⚠️ | Required for Magic.link logins — your Polymarket proxy wallet address |
| `DRY_RUN` | | `true` = simulation mode (no real trades). Default: `false` |
| `ORDER_SIZE` | | Shares per side per trade. Start small (1-5). Default: `5` |
| `TARGET_PAIR_COST` | | V1 only: Max combined UP+DOWN cost to trigger a trade. Default: `0.99` |
| `STRATEGY` | | `1` = V1 Arb, `3` = V3 Limit. Default: `1` |
| `RELAYER_API_KEY` | | For gasless redemption (proxy/Safe wallets only) |
| `RELAYER_API_KEY_ADDRESS` | | Your Safe/proxy wallet address for relayer auth |

### Multiple Clients

To run multiple wallets simultaneously, create multiple files:
```
clients/
├── client1.env    ← Wallet A, V1 Arb strategy, simulation
└── client2.env    ← Wallet B, V3 Limit strategy, live trading
```

Each client runs independently with its own strategy, balance, and trade history.

---

## Step 3: Generate API Keys

If you don't have CLOB API keys yet:

```bash
python -m src.create_api_keys
```

This prints your **API Key**, **Secret**, and **Passphrase**. Copy them into your client `.env` file.

---

## Step 4: Verify Setup

Before going live, verify everything works:

```bash
# Check wallet, API connection, and balance
python -m src.check_config

# Check USDC balance
python -m src.check_balance
```

Fix any errors reported before proceeding.

---

## Step 5: Run the Bot

### Option A: Dashboard (recommended)

```bash
python dashboard.py
```

This launches the **live dashboard** showing all clients simultaneously. Each client gets its own panel:

- **V1 clients**: Show market slug, order book, arbitrage check, threshold
- **V3 clients**: Show per-asset market table (BTC/ETH/SOL/XRP), order status
- Each panel shows that client's balance, trade count, and recent trade history

### Option B: Auto-restart wrapper (VPS)

```bash
RUN_24_7.bat
```

This runs the dashboard inside a loop that auto-restarts on crash.

### Stopping

Press **Ctrl+C** in the terminal.

---

## Strategies

### V1 — BTC 15-Minute Arbitrage (`STRATEGY=1`)

- Targets the BTC Up/Down 15-minute markets
- Scans order book for UP and DOWN prices every 100ms
- When `UP price + DOWN price ≤ TARGET_PAIR_COST` (e.g. $0.99): buys both sides
- On market resolution, one side pays $1.00 → guaranteed profit
- Auto-finds next 15m market when current one closes

**Key settings**: `TARGET_PAIR_COST`, `ORDER_SIZE`

### V3 — 1-Hour Multi-Asset Limit Orders (`STRATEGY=3`)

- Targets 1-hour Up/Down markets for BTC, ETH, SOL, and XRP
- Pre-places passive GTC limit orders at $0.45 on both UP and DOWN sides
- If both fill: combined cost = $0.90, payout = $1.00 → ~11% profit
- Auto-discovers markets from Polymarket's slug format
- Re-discovers next hourly markets when current ones close

**Key settings**: `ORDER_SIZE` (keep at 1 if balance is low)

For detailed strategy documentation, see:
- [Strategy V1](STRATEGY.md)
- [Strategy V3](STRATEGY_V3.md)

---

## Auto-Redemption

After markets resolve, the bot automatically redeems winning tokens back to USDC.

**Two modes** (selected automatically based on your config):

| Wallet Type | Method | Config Needed |
|-------------|--------|---------------|
| EOA (MetaMask) | Direct on-chain Polygon TX | Just `POLYMARKET_PRIVATE_KEY` |
| Proxy/Safe (Gnosis) | Gasless via Polymarket Relayer API | `RELAYER_API_KEY` + `RELAYER_API_KEY_ADDRESS` |

Redemption runs:
1. On bot startup (catches tokens from aborted sessions)
2. After each V1 market closes
3. The relayer signs EIP-712 Safe transactions — no MATIC gas needed

---

## Logs

Each client gets isolated log files:

```
logs/
├── Client_0xa86E.log     ← Client 1 activity
├── Client_0x338c.log     ← Client 2 activity
└── session_reports/
    ├── client_1_session_*.json    ← Session-by-session reports
    └── client_2_session_*.json
```

---

## Auto-Update (VPS)

The bot checks for git updates every 60 seconds:

1. **On your local PC**: Make changes, commit, and push
   ```bash
   git add . && git commit -m "tweak order size" && git push
   ```
2. **On the VPS**: The bot detects new commits, pulls, and restarts automatically

No SSH required for routine changes.

---

## Troubleshooting

### "PolyApiException[status_code=4]"
- **Insufficient balance or allowance**. Check your USDC balance and reduce `ORDER_SIZE`.
- If balance is $0.38, you cannot place orders of size 5 at $0.45/side ($2.25 needed).

### "Invalid signature"
- Run `python -m src.check_config` to diagnose
- For email login: set `POLYMARKET_SIGNATURE_TYPE=1` and `POLYMARKET_FUNDER`
- Regenerate API keys: `python -m src.create_api_keys`

### Balance shows $0
- For Magic.link accounts: your funds are in the proxy wallet. Set `POLYMARKET_FUNDER`.
- Run `python -m src.check_balance` to verify which address is being queried.

### "No active BTC 15min market found"
- Markets roll every 15 minutes. The bot waits automatically.
- Check your internet connection.

### Auto-redeem not working (proxy wallet)
- Ensure `RELAYER_API_KEY` and `RELAYER_API_KEY_ADDRESS` are set in your client `.env`
- The relayer handles gasless transactions for Gnosis Safe proxy wallets

### Dashboard shows wrong address
- The dashboard derives the wallet address from your private key
- If it shows `0xfdcc...` instead of your expected address , your private key may be for a different wallet

---

## Quick Reference

| Action | Command |
|--------|---------|
| Generate API keys | `python -m src.create_api_keys` |
| Check config/wallet | `python -m src.check_config` |
| Check balance | `python -m src.check_balance` |
| Run dashboard | `python dashboard.py` |
| Run with auto-restart | `RUN_24_7.bat` |
| Check positions | `python check_positions.py` |
