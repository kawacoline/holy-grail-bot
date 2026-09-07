import os
import sys
import time
from web3 import Web3
from dotenv import load_dotenv

# Load env vars
load_dotenv()

# Configuration
POLYGON_RPC_URLS = [
    "https://polygon.drpc.org",
    "https://polygon-rpc.com",
    "https://rpc.ankr.com/polygon",
    "https://1rpc.io/matic",
    "https://polygon.llamarpc.com",
    "https://rpc-mainnet.maticvigil.com"
]
PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY")

# Tokens on Polygon
USDC_NATIVE = Web3.to_checksum_address("0x3c499c542cef5e3811e1192ce70d8cc03d5c3359")
USDC_E = Web3.to_checksum_address("0x2791bca1f2de4661ed88a30c99a7a9449aa84174")

# Uniswap V3 SwapRouter on Polygon
SWAP_ROUTER = Web3.to_checksum_address("0xE592427A0AEce92De3Edee1F18E0157C05861564")

# Minimal ERC20 ABI for interacting with USDC
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
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
    {
        "constant": True,
        "inputs": [
            {"name": "_owner", "type": "address"},
            {"name": "_spender", "type": "address"},
        ],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    },
]

# Minimal Uniswap V3 SwapRouter ABI
ROUTER_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "tokenIn", "type": "address"},
                    {"internalType": "address", "name": "tokenOut", "type": "address"},
                    {"internalType": "uint24", "name": "fee", "type": "uint24"},
                    {"internalType": "address", "name": "recipient", "type": "address"},
                    {"internalType": "uint256", "name": "deadline", "type": "uint256"},
                    {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
                    {"internalType": "uint256", "name": "amountOutMinimum", "type": "uint256"},
                    {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"}
                ],
                "internalType": "struct ISwapRouter.ExactInputSingleParams",
                "name": "params",
                "type": "tuple"
            }
        ],
        "name": "exactInputSingle",
        "outputs": [{"internalType": "uint256", "name": "amountOut", "type": "uint256"}],
        "stateMutability": "payable",
        "type": "function"
    }
]

def setup_web3():
    w3 = None
    connected = False
    
    for rpc_url in POLYGON_RPC_URLS:
        print(f"🔄 Trying RPC: {rpc_url} ...")
        w3 = Web3(Web3.HTTPProvider(rpc_url))
        if w3.is_connected():
            print(f"✅ Connected to: {rpc_url}")
            connected = True
            break
            
    if not connected:
        print("❌ Could not connect to any Polygon RPC. The VPS might be blocked by these public nodes.")
        sys.exit(1)
    
    if not PRIVATE_KEY:
        print("❌ POLYMARKET_PRIVATE_KEY not found in environment.")
        sys.exit(1)
        
    account = w3.eth.account.from_key(PRIVATE_KEY)
    print(f"✅ Web3 Connected. Wallet: {account.address}")
    return w3, account

def get_balance(w3, contract_addr, wallet):
    try:
        contract = w3.eth.contract(address=contract_addr, abi=ERC20_ABI)
        return contract.functions.balanceOf(wallet).call()
    except Exception as e:
        print(f"⚠️ Error getting balance for {contract_addr}: {e}")
        return 0

def get_allowance(w3, contract_addr, wallet, spender):
    try:
        contract = w3.eth.contract(address=contract_addr, abi=ERC20_ABI)
        return contract.functions.allowance(wallet, spender).call()
    except Exception as e:
        print(f"⚠️ Error getting allowance: {e}")
        return 0

def swap_usdc_to_usdce(w3, account, amount_in_wei):
    usdc_contract = w3.eth.contract(address=USDC_NATIVE, abi=ERC20_ABI)
    router_contract = w3.eth.contract(address=SWAP_ROUTER, abi=ROUTER_ABI)
    
    # 1. Check Allowance and Approve if necessary
    allowance = get_allowance(w3, USDC_NATIVE, account.address, SWAP_ROUTER)
    
    if allowance < amount_in_wei:
        print("Approving Uniswap Router to spend USDC...")
        approve_txn = usdc_contract.functions.approve(SWAP_ROUTER, amount_in_wei).build_transaction({
            'from': account.address,
            'nonce': w3.eth.get_transaction_count(account.address),
            'gas': 100000,
            'gasPrice': w3.eth.gas_price
        })
        signed_approve = w3.eth.account.sign_transaction(approve_txn, private_key=PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed_approve.raw_transaction)
        print(f"Approval TX sent: {w3.to_hex(tx_hash)}")
        print("Waiting for approval confirmation...")
        w3.eth.wait_for_transaction_receipt(tx_hash)
        print("✅ Approved.")

    # 2. Execute Swap
    print(f"Swapping {amount_in_wei / 1e6:.2f} native USDC to bridged USDC.e...")
    
    # 100 fee tier = 0.01% fee (USDC/USDC.e usually use lowest fee tier)
    FEE_TIER = 100 
    
    deadline = int(time.time()) + 600 # 10 mins
    
    # Allow 1% slippage for stablecoin swap
    min_amount_out = int(amount_in_wei * 0.99) 
    
    swap_params = (
        USDC_NATIVE,
        USDC_E,
        FEE_TIER,
        account.address,
        deadline,
        amount_in_wei,
        min_amount_out,
        0
    )

    swap_txn = router_contract.functions.exactInputSingle(swap_params).build_transaction({
        'from': account.address,
        'nonce': w3.eth.get_transaction_count(account.address),
        'gas': 250000, 
        'gasPrice': w3.eth.gas_price
    })
    
    # Simulate first (comment out if it errors incorrectly, but usually good practice)
    # _ = w3.eth.call(swap_txn)

    signed_swap = w3.eth.account.sign_transaction(swap_txn, private_key=PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed_swap.raw_transaction)
    
    print(f"Swap TX sent: {w3.to_hex(tx_hash)}")
    print("Waiting for swap confirmation...")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    
    if receipt.status == 1:
        print("✅ Swap Successful!")
    else:
        print("❌ Swap Failed on-chain.")

if __name__ == "__main__":
    
    print("====================================")
    print("  USDC -> USDC.e Swap Utility")
    print("====================================")
    
    w3, account = setup_web3()
    
    native_balance = get_balance(w3, USDC_NATIVE, account.address)
    bridged_balance = get_balance(w3, USDC_E, account.address)
    
    print(f"\nCurrent Balances:")
    print(f"Native USDC:  {native_balance / 1e6:.6f}")
    print(f"USDC.e:       {bridged_balance / 1e6:.6f}")
    
    if native_balance == 0:
        print("\n❌ You don't have any native USDC to swap.")
        sys.exit(0)
    
    print("\nDo you want to swap ALL your native USDC to USDC.e? (y/n)")
    choice = input("> ").strip().lower()
    
    if choice == 'y':
        swap_usdc_to_usdce(w3, account, native_balance)
        
        # Check balances again
        native_balance = get_balance(w3, USDC_NATIVE, account.address)
        bridged_balance = get_balance(w3, USDC_E, account.address)
        
        print(f"\nFinal Balances:")
        print(f"Native USDC:  {native_balance / 1e6:.6f}")
        print(f"USDC.e:       {bridged_balance / 1e6:.6f}")
    else:
        print("Cancelled.")
