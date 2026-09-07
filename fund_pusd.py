import os
import time
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

# Config
RPC_URL = "https://polygon.drpc.org"
USDC_E_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
ONRAMP_ADDRESS = "0x93070a847efEf7F70739046A929D47a521F5B8ee"
PUSD_ADDRESS = "0xC011a7e12a19f7b1f670d46f03b03f3342e82dfb"

# Load PK
try:
    with open("clients/client2.env", "r") as f:
        env_content = f.read()
    
    pk = None
    for line in env_content.splitlines():
        if line.startswith("POLYMARKET_PRIVATE_KEY="):
            pk = line.split("=")[1].strip()
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

# ABIs
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [
            {"name": "_owner", "type": "address"},
            {"name": "_spender", "type": "address"}
        ],
        "name": "allowance",
        "outputs": [{"name": "remaining", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [
            {"name": "_spender", "type": "address"},
            {"name": "_value", "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
]

ONRAMP_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "_asset", "type": "address"},
            {"internalType": "address", "name": "_to", "type": "address"},
            {"internalType": "uint256", "name": "_amount", "type": "uint256"}
        ],
        "name": "wrap",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    }
]

usdc = w3.eth.contract(address=w3.to_checksum_address(USDC_E_ADDRESS), abi=ERC20_ABI)
pusd = w3.eth.contract(address=w3.to_checksum_address(PUSD_ADDRESS), abi=ERC20_ABI)
onramp = w3.eth.contract(address=w3.to_checksum_address(ONRAMP_ADDRESS), abi=ONRAMP_ABI)

usdc_balance = usdc.functions.balanceOf(address).call()
pusd_balance = pusd.functions.balanceOf(address).call()

print(f"Current USDC.e Balance: {usdc_balance / 1e6:.6f}")
print(f"Current pUSD Balance: {pusd_balance / 1e6:.6f}")

if usdc_balance == 0:
    print("No USDC.e available to wrap. You are ready if you already have pUSD.")
    exit(0)

amount_to_wrap = usdc_balance

print(f"\nProceeding to wrap {amount_to_wrap / 1e6:.6f} USDC.e -> pUSD...")

# Check Allowance
current_allowance = usdc.functions.allowance(address, w3.to_checksum_address(ONRAMP_ADDRESS)).call()
if current_allowance < amount_to_wrap:
    print("1. Approving Onramp to spend USDC.e...")
    try:
        gas_price = w3.eth.gas_price
        # boost gas price by 10%
        gas_price = int(gas_price * 1.1)
        approve_txn = usdc.functions.approve(w3.to_checksum_address(ONRAMP_ADDRESS), amount_to_wrap).build_transaction({
            'from': address,
            'nonce': w3.eth.get_transaction_count(address),
            'gas': 100000,
            'gasPrice': gas_price
        })
        signed_approve = w3.eth.account.sign_transaction(approve_txn, private_key=pk)
        tx_hash = w3.eth.send_raw_transaction(signed_approve.raw_transaction)
        print(f"   Approval TX sent: {tx_hash.hex()}")
        
        print("   Waiting for approval confirmation...")
        w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        print("   Approval confirmed!")
        time.sleep(2)
    except Exception as e:
        print(f"Error during approval: {e}")
        exit(1)
else:
    print("1. Allowance already sufficient, skipping approval.")

# Wrap
print("2. Calling wrap() on Collateral Onramp...")
try:
    gas_price = w3.eth.gas_price
    gas_price = int(gas_price * 1.1)
    wrap_txn = onramp.functions.wrap(
        w3.to_checksum_address(USDC_E_ADDRESS),
        address,
        amount_to_wrap
    ).build_transaction({
        'from': address,
        'nonce': w3.eth.get_transaction_count(address),
        'gas': 500000,
        'gasPrice': gas_price
    })
    signed_wrap = w3.eth.account.sign_transaction(wrap_txn, private_key=pk)
    tx_hash = w3.eth.send_raw_transaction(signed_wrap.raw_transaction)
    print(f"   Wrap TX sent: {tx_hash.hex()}")
    
    print("   Waiting for wrap confirmation...")
    w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    print("   Wrap confirmed!")
except Exception as e:
    print(f"Error during wrap: {e}")
    print("Note: The wrap function ABI might be slightly different. We can inspect the transaction failure if needed.")
    exit(1)

new_pusd_balance = pusd.functions.balanceOf(address).call()
print(f"\nSuccessfully wrapped! New pUSD Balance: {new_pusd_balance / 1e6:.6f}")
print("Your bot is now ready to trade on Polymarket V2.")
