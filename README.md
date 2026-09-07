# 🏆 Holy Grail Bot — Multi-Strategy Prediction Market Algorithmic Trading Engine

[![Python Version](https://img.shields.io/badge/Python-3.10%2B-blue.svg?logo=python)](https://python.org)
[![Market Platform](https://img.shields.io/badge/Platform-Polymarket%20CLOB-purple.svg)](https://polymarket.com)
[![Network](https://img.shields.io/badge/Network-Polygon%20PoS-8247E5.svg?logo=polygon)](https://polygon.technology)
[![WebSockets](https://img.shields.io/badge/Data-Real--Time%20WSS-green.svg)](https://websockets.readthedocs.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Holy Grail Bot** is a high-performance, multi-strategy, multi-client algorithmic trading system engineered for **Polymarket** decentralized binary and crypto Up/Down markets (BTC, ETH, SOL, XRP). 

The platform pairs real-time WebSocket order book ingestion with sub-millisecond execution logic, autonomous L2 credential derivation, automated smart contract token redemptions, and an isolated multi-tenant architecture managed via a live terminal dashboard.

---

## 🏛️ Strategy Taxonomy

| Strategy | Target Markets | Resolution Horizon | Quantitative Mechanics |
|---|---|---|---|
| **V1 Arbitrage** | BTC Up/Down | 15-minute | Instantaneous structural parity capture: buys both sides when `Price(Yes) + Price(No) < $1.00`, securing guaranteed payout at resolution. |
| **V3 Limit Squeeze** | BTC, ETH, SOL, XRP | 1-hour | Dual passive limit placements at $0.45 per side to capture cross-side volatility ($0.90 combined outlay → $1.00 redemption). |
| **V4 Order Flow** | BTC, ETH, SOL, XRP | 1-hour | Microstructure evaluation incorporating Order Book Supply Wall depth, cumulative bid/ask absorption, and directional momentum filtering. |

---

## 🏗️ System Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│                      REAL-TIME DATA STREAM (WSS)                       │
│  Polymarket WebSocket stream pushing L2 order book updates & tick events│
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Level-2 Order Book State
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                   QUANTITATIVE STRATEGY RUNTIME                        │
│  • V1 Arbitrage: Statistical parity scanner (<1ms evaluate loop)       │
│  • V3 Limit Maker: Multi-asset liquidity provision engine             │
│  • V4 Order Flow: Depth-of-book supply wall & momentum protector       │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Validated Execution Signal
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                     ORDER EXECUTION & WEB3 CORE                        │
│  • Automatic L2 API Key Derivation from private keys at boot           │
│  • EIP-712 Order Construction & FAK/FOK gasless execution via CLOB     │
│  • Automated Smart Contract Token Redemption (CtfCollateralAdapter)    │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Multi-Tenant Telemetry
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                      REAL-TIME TUI DASHBOARD                           │
│  • Client-isolated terminal metrics: fills, balances, scans, ROI       │
│  • Zero state cross-contamination between parallel client workers      │
└────────────────────────────────────────────────────────────────────────┘
```

---

## ⚡ Key Capabilities

- **Zero-Friction Dynamic Credential Derivation**: Automatically creates and derives Polymarket L2 API credentials directly from the user's private key during initialization—no manual API key onboarding needed.
- **Autonomous On-Chain Token Redemption**: When prediction markets resolve, the bot interacts directly with Polygon smart contracts (`CtfCollateralAdapter` and `NegRiskCtfCollateralAdapter`) to unwrap conditional tokens and return native pUSD directly to your wallet.
- **Circuit Breakers & Capital Protection**: Embedded drawdown limits, max consecutive failure detection, and automatic emergency cooldown windows.
- **Isolated Multi-Tenant Architecture**: Capable of managing multiple client wallets concurrently, each assigned distinct risk profiles, balance constraints, and strategy engines.
- **Live Terminal Telemetry**: High-density console dashboard reporting book spreads, time remaining per contract, active quotes, and net P&L.

---

## 🛠️ Quickstart & Setup

### 1. Prerequisites
- **Python 3.10+**
- **Polygon (PoS) Wallet**: Funded with USDC.e and a nominal amount of MATIC/POL for gas during contract redemptions (~0.5 MATIC).

### 2. Clone & Setup
```bash
git clone https://github.com/kawacoline/holy-grail-bot.git
cd holy-grail-bot
setup.bat
```

*(On Linux / macOS)*:
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Environment Configuration
Copy the template configuration:
```bash
copy .env.example .env
```

```ini
TARGET_PAIR_COST=0.99
ORDER_SIZE=5.0
ORDER_TYPE=FAK
DRY_RUN=true
USE_WSS=true

# Polymarket Web3 Credentials
POLYMARKET_PRIVATE_KEY=your_polygon_private_key_here
POLYMARKET_SIGNATURE_TYPE=0
```

### 4. Running the Dashboard
Launch via the automated startup script:
```bash
START.bat
```

Or execute directly:
```bash
python dashboard.py
```

---

## 📁 Repository Structure

```
├── dashboard.py            # Master multi-client TUI dashboard & orchestrator
├── src/
│   ├── btc_15m_arb_bot.py  # V1 15-minute statistical arbitrage engine
│   ├── v3_limit_bot.py     # V3 passive limit placement engine
│   ├── v4_orderflow_bot.py # V4 microstructure and order book wall strategy
│   ├── trading.py          # CLOB order routing and EIP-712 signing
│   ├── redeem.py           # On-chain smart contract redemption handler
│   ├── circuit_breaker.py  # Automated drawdown and volatility protection
│   ├── wss_market.py       # WebSocket real-time market data ingestion
│   └── market_lookup.py    # Gamma API slug and contract resolver
├── docs/                   # Detailed quantitative strategy documentation
├── requirements.txt        # Production dependencies
└── .env.example            # Sanitized environment template
```

---

## 👨‍💻 Author

**Hazael**  
*Full Stack Software Engineer & Quantitative Web3 Specialist*  
- **GitHub**: [@kawacoline](https://github.com/kawacoline)  
- **Email**: kawacoline@gmail.com  
- **Portfolio**: [hazael.dev](https://github.com/kawacoline)

---

## ⚖️ Disclaimer

*Holy Grail Bot is published strictly for algorithmic research and portfolio demonstration. Trading on prediction markets involves substantial financial risk. Test extensively in simulation (`DRY_RUN=true`) before engaging in live operations.*
