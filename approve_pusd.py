"""
Approve pUSD spending for all 3 Polymarket V2 exchange contracts.
This is a ONE-TIME on-chain setup step required before the bot can place orders.

Run: venv/Scripts/python.exe approve_pusd.py
"""
import os
import time
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

# Config
RPC_URL = "https://polygon.drpc.org"
PUSD_ADDRESS = "0xC011a7e12a19f7b1f670d46f03b03f3342e82dfb"

# All 3 V2 exchange spenders that need pUSD approval
SPENDERS = {
    "CTF Exchange V2":   "0xE111180000d2663C0091e4f400237545B87B996B",
    "NegRisk Exchange":  "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296",
    "NegRisk Adapter V2":"0xe2222d279d744050d28e00520010520000310F59",
}

# Max uint256 approval (unlimited)
MAX_APPROVAL = 2**256 - 1

# Load PK from client2.env
try:
    with open("clients/client2.env", "r") as f:
        env_content = f.read()
    pk = None
    for line in env_content.splitlines():
        if line.startswith("POLYMARKET_PRIVATE_KEY="):
            pk = line.split("=", 1)[1].strip()
            break
except Exception as e:
    print(f"Error loading client2.env: {e}")
    exit(1)

if not pk:
    print("POLYMARKET_PRIVATE_KEY not found in client2.env")
    exit(1)

# Connect Web3
w3 = Web3(Web3.HTTPProvider(RPC_URL))
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

if not w3.is_connected():
    print("Failed to connect to Polygon RPC.")
    exit(1)

account = w3.eth.account.from_key(pk)
address = account.address
print(f"Connected as {address}")

# ABI
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}, {"name": "_spender", "type": "address"}],
        "name": "allowance",
        "outputs": [{"name": "remaining", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [{"name": "_spender", "type": "address"}, {"name": "_value", "type": "uint256"}],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    },
]

pusd = w3.eth.contract(address=w3.to_checksum_address(PUSD_ADDRESS), abi=ERC20_ABI)
pusd_balance = pusd.functions.balanceOf(address).call()
print(f"pUSD Balance: {pusd_balance / 1e6:.6f}")

if pusd_balance == 0:
    print("\n⚠️  You have 0 pUSD. Run fund_pusd.py first to wrap USDC.e -> pUSD.")
    # Still continue to set approvals for when you do have pUSD

print("\n" + "=" * 60)
print("  APPROVING pUSD FOR POLYMARKET V2 EXCHANGE CONTRACTS")
print("=" * 60)

for name, spender_addr in SPENDERS.items():
    spender = w3.to_checksum_address(spender_addr)
    current = pusd.functions.allowance(address, spender).call()
    
    if current > 0:
        print(f"\n✅ {name}: Already approved (allowance: {current / 1e6:.2f})")
        continue

    print(f"\n>> {name} ({spender_addr[:12]}...): Allowance = 0 -> Approving...")
    
    try:
        gas_price = int(w3.eth.gas_price * 1.2)  # 20% boost
        nonce = w3.eth.get_transaction_count(address)
        
        txn = pusd.functions.approve(spender, MAX_APPROVAL).build_transaction({
            'from': address,
            'nonce': nonce,
            'gas': 80000,
            'gasPrice': gas_price,
        })
        signed = w3.eth.account.sign_transaction(txn, private_key=pk)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        print(f"   TX sent: {tx_hash.hex()}")
        
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        if receipt.status == 1:
            print(f"   ✅ Approved! Gas used: {receipt.gasUsed}")
        else:
            print(f"   ❌ TX reverted!")
        
        time.sleep(2)  # Wait between approvals
    except Exception as e:
        print(f"   ❌ Failed: {e}")

# Verify
print("\n" + "=" * 60)
print("  VERIFICATION")
print("=" * 60)
all_good = True
for name, spender_addr in SPENDERS.items():
    spender = w3.to_checksum_address(spender_addr)
    allowance = pusd.functions.allowance(address, spender).call()
    status = "✅" if allowance > 0 else "❌"
    print(f"  {status} {name}: {allowance / 1e6:.2f}")
    if allowance == 0:
        all_good = False

if all_good:
    print("\n🎉 All approvals set! Your bot is ready to trade.")
else:
    print("\n⚠️  Some approvals failed. Re-run this script or check MATIC balance for gas.")
