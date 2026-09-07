"""
Redeem winning tokens from resolved Polymarket markets.

Queries the user's positions, finds resolved markets with unredeemed
CTF tokens, and calls redeemPositions on the CTF contract to convert
winning tokens back to USDCe.

Usage:
    python redeem_winnings.py          # Dry-run (shows what would be redeemed)
    python redeem_winnings.py --execute # Actually submit redemption transactions
"""

import os
import sys
import json
import argparse
import time
from web3 import Web3
from dotenv import load_dotenv

load_dotenv(override=False)

# ── Polygon RPC endpoints (same list as swap_usdc.py) ──────────────────────
POLYGON_RPC_URLS = [
    "https://polygon.drpc.org",
    "https://polygon-rpc.com",
    "https://rpc.ankr.com/polygon",
    "https://1rpc.io/matic",
    "https://polygon.llamarpc.com",
    "https://rpc-mainnet.maticvigil.com",
]

# ── Contract addresses on Polygon ──────────────────────────────────────────
CTF_CONTRACT   = Web3.to_checksum_address("0x4D97DCd97eC945f40cF65F87097ACe5EA0476045")
USDC_E         = Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
NEG_RISK_ADAPTER = Web3.to_checksum_address("0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296")

# ── Minimal ABI for redeemPositions ────────────────────────────────────────
CTF_ABI = [
    {
        "inputs": [
            {"internalType": "contract IERC20", "name": "collateralToken", "type": "address"},
            {"internalType": "bytes32", "name": "parentCollectionId", "type": "bytes32"},
            {"internalType": "bytes32", "name": "conditionId", "type": "bytes32"},
            {"internalType": "uint256[]", "name": "indexSets", "type": "uint256[]"},
        ],
        "name": "redeemPositions",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "bytes32", "name": "conditionId", "type": "bytes32"},
        ],
        "name": "payoutNumerators",
        "outputs": [
            {"internalType": "uint256[]", "name": "", "type": "uint256[]"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]

# ── NegRisk Adapter ABI (for NegRisk markets) ─────────────────────────────
NEG_RISK_ABI = [
    {
        "inputs": [
            {"internalType": "bytes32", "name": "conditionId", "type": "bytes32"},
            {"internalType": "uint256[]", "name": "indexSets", "type": "uint256[]"},
        ],
        "name": "redeemPositions",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]

# ── ERC20 balanceOf ABI ────────────────────────────────────────────────────
ERC20_ABI = [
    {
        "inputs": [{"internalType": "address", "name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]

# ── ERC1155 balanceOf ABI (for CTF tokens) ─────────────────────────────────
ERC1155_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "account", "type": "address"},
            {"internalType": "uint256", "name": "id", "type": "uint256"},
        ],
        "name": "balanceOf",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]


def setup_web3():
    for rpc_url in POLYGON_RPC_URLS:
        print(f"🔄 Trying RPC: {rpc_url} ...")
        w3 = Web3(Web3.HTTPProvider(rpc_url))
        if w3.is_connected():
            print(f"✅ Connected to: {rpc_url}\n")
            return w3
    print("❌ Could not connect to any Polygon RPC.")
    sys.exit(1)


def get_user_address(private_key: str, w3: Web3) -> str:
    account = w3.eth.account.from_key(private_key)
    return account.address


def fetch_positions(address: str) -> list:
    """Fetch all user positions from Polymarket Data API."""
    import httpx

    all_positions = []
    # Fetch open positions
    url = f"https://data-api.polymarket.com/positions?user={address}"
    print(f"📡 Fetching positions from Data API...")
    try:
        resp = httpx.get(url, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                all_positions.extend(data)
                print(f"   Found {len(data)} open position(s)")
        else:
            print(f"   ⚠️ Data API returned {resp.status_code}")
    except Exception as e:
        print(f"   ❌ Error fetching positions: {e}")

    # Also fetch closed positions
    url2 = f"https://data-api.polymarket.com/closed-positions?user={address}"
    try:
        resp2 = httpx.get(url2, timeout=15)
        if resp2.status_code == 200:
            data2 = resp2.json()
            if isinstance(data2, list):
                all_positions.extend(data2)
                print(f"   Found {len(data2)} closed position(s)")
        else:
            print(f"   ⚠️ Closed positions API returned {resp2.status_code}")
    except Exception as e:
        print(f"   ⚠️ Error fetching closed positions: {e}")

    return all_positions


def get_condition_id_from_gamma(token_id: str) -> dict | None:
    """Look up market info including conditionId from Gamma API by token ID."""
    import httpx

    try:
        url = f"https://gamma-api.polymarket.com/markets?clob_token_ids={token_id}"
        resp = httpx.get(url, timeout=10)
        if resp.status_code == 200:
            markets = resp.json()
            if markets and len(markets) > 0:
                m = markets[0]
                return {
                    "condition_id": m.get("conditionId") or m.get("condition_id"),
                    "question": m.get("question", "Unknown"),
                    "resolved": m.get("closed", False) or m.get("resolved", False),
                    "neg_risk": m.get("neg_risk", False) or m.get("negRisk", False),
                    "tokens": m.get("tokens", []),
                }
    except Exception as e:
        print(f"   ⚠️ Gamma API lookup failed for {token_id[:20]}...: {e}")
    return None


def check_ctf_balance(w3: Web3, address: str, token_id: str) -> int:
    """Check on-chain CTF ERC1155 token balance."""
    ctf = w3.eth.contract(address=CTF_CONTRACT, abi=ERC1155_ABI)
    token_id_int = int(token_id)
    return ctf.functions.balanceOf(address, token_id_int).call()


def check_usdce_balance(w3: Web3, address: str) -> float:
    """Check USDCe balance (6 decimals)."""
    usdc = w3.eth.contract(address=USDC_E, abi=ERC20_ABI)
    raw = usdc.functions.balanceOf(address).call()
    return raw / 1e6


def redeem(w3: Web3, private_key: str, condition_id: str, neg_risk: bool, execute: bool) -> bool:
    """Call redeemPositions on the CTF contract."""
    account = w3.eth.account.from_key(private_key)
    address = account.address
    condition_bytes = bytes.fromhex(condition_id.replace("0x", ""))
    parent_collection = b'\x00' * 32
    index_sets = [1, 2]  # Redeem both YES and NO (only winners pay out)

    if neg_risk:
        # NegRisk markets use the NegRisk Adapter contract
        contract = w3.eth.contract(address=NEG_RISK_ADAPTER, abi=NEG_RISK_ABI)
        tx_data = contract.functions.redeemPositions(
            condition_bytes,
            index_sets,
        )
    else:
        # Standard markets use the CTF contract directly
        contract = w3.eth.contract(address=CTF_CONTRACT, abi=CTF_ABI)
        tx_data = contract.functions.redeemPositions(
            USDC_E,
            parent_collection,
            condition_bytes,
            index_sets,
        )

    if not execute:
        print(f"      🔍 [DRY RUN] Would call redeemPositions with conditionId={condition_id[:16]}...")
        return True

    try:
        nonce = w3.eth.get_transaction_count(address)
        gas_price = w3.eth.gas_price

        tx = tx_data.build_transaction({
            "from": address,
            "nonce": nonce,
            "gas": 300_000,
            "gasPrice": int(gas_price * 1.2),
        })

        signed = w3.eth.account.sign_transaction(tx, private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        print(f"      📤 TX sent: {tx_hash.hex()}")

        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        if receipt.status == 1:
            print(f"      ✅ Redeemed! Gas used: {receipt.gasUsed}")
            return True
        else:
            print(f"      ❌ TX reverted. Receipt: {receipt}")
            return False
    except Exception as e:
        print(f"      ❌ Redemption TX failed: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Redeem winning Polymarket tokens")
    parser.add_argument("--execute", action="store_true", help="Actually submit redemption transactions (default: dry-run)")
    args = parser.parse_args()

    private_key = os.getenv("POLYMARKET_PRIVATE_KEY", "").strip()
    if not private_key:
        print("❌ POLYMARKET_PRIVATE_KEY not set in .env")
        sys.exit(1)

    w3 = setup_web3()
    address = get_user_address(private_key, w3)
    print(f"👛 Wallet: {address}")

    # Check starting USDCe balance
    usdce_before = check_usdce_balance(w3, address)
    print(f"💰 USDCe balance (before): ${usdce_before:.6f}\n")

    # Fetch positions
    positions = fetch_positions(address)
    if not positions:
        print("\n⚠️ No positions found. Nothing to redeem.")
        return

    # Deduplicate by token ID
    seen_conditions = set()
    redeemable = []

    print(f"\n🔎 Checking {len(positions)} position(s) for redeemable tokens...\n")

    for pos in positions:
        token_id = pos.get("asset") or pos.get("tokenId") or pos.get("token_id")
        if not token_id:
            continue

        # Check on-chain balance for this token
        on_chain_balance = check_ctf_balance(w3, address, token_id)
        if on_chain_balance == 0:
            continue  # No tokens to redeem

        # Look up market info from Gamma
        market_info = get_condition_id_from_gamma(token_id)
        if not market_info or not market_info.get("condition_id"):
            print(f"   ⚠ Could not find conditionId for token {token_id[:20]}... (skipping)")
            continue

        cond_id = market_info["condition_id"]
        if cond_id in seen_conditions:
            continue
        seen_conditions.add(cond_id)

        shares = on_chain_balance / 1e6  # CTF tokens have 6 decimals on Polymarket
        print(f"   📌 {market_info['question'][:60]}...")
        print(f"      conditionId: {cond_id[:16]}...")
        print(f"      On-chain shares: {shares:.2f}")
        print(f"      Resolved: {market_info['resolved']}, NegRisk: {market_info['neg_risk']}")

        redeemable.append({
            "condition_id": cond_id,
            "question": market_info["question"],
            "neg_risk": market_info["neg_risk"],
            "shares": shares,
        })

    if not redeemable:
        print("\n✅ No redeemable positions found (all tokens already redeemed or zero balance).")
        return

    print(f"\n{'='*60}")
    print(f"Found {len(redeemable)} market(s) to redeem")
    if not args.execute:
        print("🔍 DRY RUN MODE — pass --execute to submit transactions")
    print(f"{'='*60}\n")

    success_count = 0
    for item in redeemable:
        print(f"   🔄 Redeeming: {item['question'][:50]}...")
        ok = redeem(w3, private_key, item["condition_id"], item["neg_risk"], args.execute)
        if ok:
            success_count += 1
        if args.execute:
            time.sleep(2)  # Wait between TXs to avoid nonce issues

    # Check final USDCe balance
    if args.execute:
        time.sleep(3)
        usdce_after = check_usdce_balance(w3, address)
        gained = usdce_after - usdce_before
        print(f"\n{'='*60}")
        print(f"💰 USDCe balance (before): ${usdce_before:.6f}")
        print(f"💰 USDCe balance (after):  ${usdce_after:.6f}")
        print(f"📈 Gained:                 ${gained:.6f}")
        print(f"✅ Redeemed {success_count}/{len(redeemable)} market(s)")
        print(f"{'='*60}")
    else:
        print(f"\n🔍 Dry run complete. {success_count} market(s) would be redeemed.")
        print(f"   Run with --execute to submit transactions.")


if __name__ == "__main__":
    main()
