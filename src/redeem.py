"""
Auto-redeem winning CTF tokens from resolved Polymarket markets.

Supports two modes:
  1. GASLESS via Polymarket Relayer API (for proxy/safe wallets)
  2. Raw on-chain TX fallback (for EOA wallets with MATIC)
"""

import os
import time
import httpx
from web3 import Web3
from eth_account import Account
from loguru import logger

# ── Polygon RPC endpoints ──────────────────────────────────────────────────
POLYGON_RPC_URLS = [
    "https://polygon.drpc.org",
    "https://polygon-rpc.com",
    "https://rpc.ankr.com/polygon",
    "https://1rpc.io/matic",
    "https://polygon.llamarpc.com",
]

# ── Contract addresses on Polygon ──────────────────────────────────────────
CTF_CONTRACT     = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
USDC_E           = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
PUSD             = "0xC011a7e12a19f7b1f670d46f03b03f3342e82dfb"  # V2 collateral
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
RELAYER_URL      = "https://relayer-v2.polymarket.com"
CHAIN_ID         = 137

# ── Minimal ABIs ───────────────────────────────────────────────────────────
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
]

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

ERC20_ABI = [
    {
        "inputs": [{"internalType": "address", "name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]

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
    {
        "inputs": [
            {"internalType": "address", "name": "account", "type": "address"},
            {"internalType": "address", "name": "operator", "type": "address"}
        ],
        "name": "isApprovedForAll",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "operator", "type": "address"},
            {"internalType": "bool", "name": "approved", "type": "bool"}
        ],
        "name": "setApprovalForAll",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]


# ═══════════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _connect_polygon():
    """Connect to a working Polygon RPC."""
    for rpc_url in POLYGON_RPC_URLS:
        try:
            w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 10}))
            if w3.is_connected():
                return w3
        except Exception:
            continue
    return None


def _get_safe_address(signer_address):
    """Derive the Gnosis Safe address from the signer using CREATE2 (same as Polymarket)."""
    try:
        from py_builder_relayer_client.builder.derive import derive
        from py_builder_relayer_client.config import get_contract_config
        config = get_contract_config(CHAIN_ID)
        return derive(signer_address, config.safe_factory)
    except Exception as e:
        logger.debug(f"Could not derive safe address: {e}")
        return None


def _get_usdce_balance(w3, address):
    """Get USDCe balance for an address."""
    usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=ERC20_ABI)
    return usdc.functions.balanceOf(Web3.to_checksum_address(address)).call() / 1e6


def _get_pusd_balance(w3, address):
    """Get pUSD (V2 collateral) balance for an address."""
    pusd = w3.eth.contract(address=Web3.to_checksum_address(PUSD), abi=ERC20_ABI)
    return pusd.functions.balanceOf(Web3.to_checksum_address(address)).call() / 1e6


def _check_ctf_balance(w3, address, token_id):
    """Check ERC1155 CTF token balance."""
    ctf = w3.eth.contract(address=Web3.to_checksum_address(CTF_CONTRACT), abi=ERC1155_ABI)
    return ctf.functions.balanceOf(Web3.to_checksum_address(address), int(token_id)).call()


def _ensure_ctf_adapter_approved(w3, private_key, address, adapter_address):
    """Ensure the user's wallet has approved the CtfCollateralAdapter to burn their CTF tokens."""
    try:
        ctf = w3.eth.contract(address=Web3.to_checksum_address(CTF_CONTRACT), abi=ERC1155_ABI)
        is_approved = ctf.functions.isApprovedForAll(
            Web3.to_checksum_address(address), Web3.to_checksum_address(adapter_address)
        ).call()
        if not is_approved:
            logger.info(f"  Approving CTF Exchange Adapter ({adapter_address}) for ERC1155...")
            tx_data = ctf.functions.setApprovalForAll(Web3.to_checksum_address(adapter_address), True)
            
            estimated_gas = tx_data.estimate_gas({"from": address})
            nonce = w3.eth.get_transaction_count(address)
            tx = tx_data.build_transaction({
                "from": address,
                "nonce": nonce,
                "gas": int(estimated_gas * 1.2),
                "gasPrice": int(w3.eth.gas_price * 1.2),
            })
            signed = w3.eth.account.sign_transaction(tx, private_key)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
            logger.info("  >> Adapter Approved!")
    except Exception as e:
        logger.warning(f"  Failed to approve CTF Adapter: {e}")



def _fetch_positions(address):
    """Fetch all positions from Polymarket Data API."""
    positions = []
    for endpoint in [
        f"https://data-api.polymarket.com/positions?user={address}",
        f"https://data-api.polymarket.com/closed-positions?user={address}",
    ]:
        try:
            resp = httpx.get(endpoint, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    positions.extend(data)
        except Exception:
            pass
    return positions


def _get_condition_id(token_id):
    """Get conditionId and market info from Gamma API."""
    try:
        url = f"https://gamma-api.polymarket.com/markets?clob_token_ids={token_id}"
        resp = httpx.get(url, timeout=10)
        if resp.status_code == 200:
            markets = resp.json()
            if markets:
                m = markets[0]
                return {
                    "condition_id": m.get("conditionId") or m.get("condition_id"),
                    "question": m.get("question", "Unknown"),
                    "neg_risk": m.get("neg_risk", False) or m.get("negRisk", False),
                    "closed": m.get("closed", False),
                }
    except Exception:
        pass
    return None


def _encode_redeem_calldata(condition_id, neg_risk, collateral_token=None):
    """Encode the redeemPositions function calldata.
    
    For V2 markets, collateral_token should be pUSD.
    For legacy markets, collateral_token should be USDC.e.
    Defaults to pUSD (V2) if not specified.
    """
    w3 = Web3()
    condition_bytes = bytes.fromhex(condition_id.replace("0x", ""))
    index_sets = [1, 2]
    collateral = collateral_token or PUSD  # Default to V2 collateral

    if neg_risk:
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(NEG_RISK_ADAPTER), abi=NEG_RISK_ABI
        )
        calldata = contract.encode_abi("redeemPositions", args=[condition_bytes, index_sets])
        target = NEG_RISK_ADAPTER
    else:
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(CTF_CONTRACT), abi=CTF_ABI
        )
        calldata = contract.encode_abi(
            "redeemPositions",
            args=[Web3.to_checksum_address(collateral), b'\x00' * 32, condition_bytes, index_sets],
        )
        target = CTF_CONTRACT

    return calldata, target


# ═══════════════════════════════════════════════════════════════════════════
#  Gasless Redemption via Polymarket Relayer
# ═══════════════════════════════════════════════════════════════════════════

def _redeem_via_relayer(private_key, condition_id, neg_risk, relayer_api_key, relayer_api_key_address):
    """
    Submit gasless redemption through the Polymarket Relayer API.
    Uses EIP-712 Safe transaction signing + Relayer API Key auth.
    """
    from py_builder_relayer_client.signer import Signer
    from py_builder_relayer_client.builder.derive import derive
    from py_builder_relayer_client.builder.safe import (
        create_struct_hash,
        split_and_pack_sig,
        create_safe_signature,
    )
    from py_builder_relayer_client.models import OperationType
    from py_builder_relayer_client.constants.constants import ZERO_ADDRESS
    from py_builder_relayer_client.config import get_contract_config

    signer = Signer(private_key, CHAIN_ID)
    signer_address = signer.address()
    config = get_contract_config(CHAIN_ID)
    safe_address = derive(signer_address, config.safe_factory)

    # Encode the redeemPositions calldata
    calldata, target_contract = _encode_redeem_calldata(condition_id, neg_risk)
    target = Web3.to_checksum_address(target_contract)

    # Get nonce from the relayer
    try:
        nonce_resp = httpx.get(
            f"{RELAYER_URL}/nonce?address={signer_address}&type=SAFE",
            timeout=10,
        )
        nonce_data = nonce_resp.json()
        nonce = str(nonce_data.get("nonce", "0"))
    except Exception as e:
        logger.warning(f"  Could not get relayer nonce: {e}")
        return False

    # Build the EIP-712 Safe struct hash
    struct_hash = create_struct_hash(
        chain_id=CHAIN_ID,
        safe=safe_address,
        to=target,
        value="0",
        data=calldata,
        operation=OperationType.Call,
        safe_tx_gas="0",
        base_gas="0",
        gas_price="0",
        gas_token=ZERO_ADDRESS,
        refund_receiver=ZERO_ADDRESS,
        nonce=nonce,
    )

    # Sign the struct hash and pack the signature
    sig = create_safe_signature(signer, struct_hash)
    packed_sig = split_and_pack_sig(sig)

    # Build the relayer submission body
    body = {
        "from": signer_address,
        "to": target,
        "proxyWallet": safe_address,
        "data": calldata,
        "value": "0",
        "nonce": nonce,
        "signature": packed_sig,
        "signatureParams": {
            "gasPrice": "0",
            "operation": "0",
            "safeTxnGas": "0",
            "baseGas": "0",
            "gasToken": ZERO_ADDRESS,
            "refundReceiver": ZERO_ADDRESS,
        },
        "type": "SAFE",
    }

    headers = {
        "Content-Type": "application/json",
        "RELAYER_API_KEY": relayer_api_key,
        "RELAYER_API_KEY_ADDRESS": relayer_api_key_address,
    }

    try:
        resp = httpx.post(f"{RELAYER_URL}/submit", json=body, headers=headers, timeout=30)
        result = resp.json()

        if resp.status_code == 200:
            tx_id = result.get("transactionID", "unknown")
            logger.info(f"  📡 Relayer accepted: txID={tx_id[:20]}...")

            # Poll for on-chain confirmation (up to ~45 seconds)
            for _ in range(15):
                time.sleep(3)
                try:
                    poll = httpx.get(
                        f"{RELAYER_URL}/transaction?id={tx_id}", timeout=10
                    )
                    if poll.status_code == 200:
                        txns = poll.json()
                        if txns and isinstance(txns, list):
                            state = txns[0].get("state", "")
                            if state in ("STATE_CONFIRMED", "STATE_MINED"):
                                logger.success(f"  ✅ Confirmed on-chain!")
                                return True
                            if state in ("STATE_FAILED", "STATE_INVALID"):
                                logger.warning(f"  ❌ Relayer tx failed: {state}")
                                return False
                except Exception:
                    pass

            # If we didn't get explicit failure, assume pending/success
            logger.info(f"  ⏳ Relayer tx pending (may confirm later)")
            return True
        else:
            error_msg = result.get("error", result.get("message", str(result)))
            logger.warning(f"  ❌ Relayer rejected ({resp.status_code}): {error_msg}")
            return False

    except Exception as e:
        logger.warning(f"  ❌ Relayer request failed: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════════
#  Raw on-chain TX fallback (EOA wallets only)
# ═══════════════════════════════════════════════════════════════════════════

def _redeem_raw_tx(w3, private_key, condition_id, neg_risk):
    """Direct on-chain redemption (requires MATIC for gas, EOA only).
    
    Tries pUSD collateral first (V2 markets), falls back to USDC.e (legacy).
    NegRisk markets don't take a collateral param so they work for both.
    """
    account = Account.from_key(private_key)
    address = account.address
    condition_bytes = bytes.fromhex(condition_id.replace("0x", ""))
    index_sets = [1, 2]

    # For neg_risk markets, collateral token is not a parameter — just call once
    if neg_risk:
        _ensure_ctf_adapter_approved(w3, private_key, address, NEG_RISK_ADAPTER)
        try:
            contract = w3.eth.contract(
                address=Web3.to_checksum_address(NEG_RISK_ADAPTER), abi=NEG_RISK_ABI
            )
            tx_data = contract.functions.redeemPositions(condition_bytes, index_sets)
            return _send_redeem_tx(w3, tx_data, address, private_key, condition_id)
        except Exception as e:
            logger.debug(f"  NegRisk raw TX reverted for {condition_id[:16]}...: {e}")
            return False

    # For non-negRisk: V2 uses the CtfCollateralAdapter for pUSD. Legacy uses the native CTF for USDC.e.
    CTF_ADAPTER = "0xAdA100Db00Ca00073811820692005400218FcE1f"
    
    _ensure_ctf_adapter_approved(w3, private_key, address, CTF_ADAPTER)
    
    for collateral_label, collateral_addr, target_contract in [
        ("pUSD", PUSD, CTF_ADAPTER)
    ]:
        try:
            contract = w3.eth.contract(
                address=Web3.to_checksum_address(target_contract), abi=CTF_ABI
            )
            tx_data = contract.functions.redeemPositions(
                Web3.to_checksum_address(collateral_addr), b'\x00' * 32, condition_bytes, index_sets
            )
            result = _send_redeem_tx(w3, tx_data, address, private_key, condition_id)
            if result:
                logger.debug(f"  Redeemed with {collateral_label} collateral")
                return True
        except Exception as e:
            logger.debug(f"  Raw TX with {collateral_label} reverted for {condition_id[:16]}...: {e}")
            continue

    return False


def _send_redeem_tx(w3, tx_data, address, private_key, condition_id):
    """Build, sign, and send a redemption transaction."""
    try:
        estimated_gas = tx_data.estimate_gas({"from": address})

        nonce = w3.eth.get_transaction_count(address)
        tx = tx_data.build_transaction({
            "from": address,
            "nonce": nonce,
            "gas": int(estimated_gas * 1.2),
            "gasPrice": int(w3.eth.gas_price * 1.2),
        })

        signed = w3.eth.account.sign_transaction(tx, private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        return receipt.status == 1
    except Exception as e:
        logger.debug(f"  TX send failed for {condition_id[:16]}...: {e}")
        return False


def _burn_worthless_token(w3, private_key, address, token_id, balance):
    """Sends worthless tokens to the burn address so they disappear from the UI."""
    try:
        CTF_ERC1155_ABI = [
            {
                "constant": False,
                "inputs": [
                    {"name": "from", "type": "address"},
                    {"name": "to", "type": "address"},
                    {"name": "id", "type": "uint256"},
                    {"name": "amount", "type": "uint256"},
                    {"name": "data", "type": "bytes"}
                ],
                "name": "safeTransferFrom",
                "outputs": [],
                "type": "function"
            }
        ]
        contract = w3.eth.contract(address=Web3.to_checksum_address(CTF_CONTRACT), abi=CTF_ERC1155_ABI)
        burn_address = Web3.to_checksum_address("0x000000000000000000000000000000000000dEaD")
        
        tx_data = contract.functions.safeTransferFrom(
            address, burn_address, int(token_id), balance, b""
        )
        
        estimated_gas = tx_data.estimate_gas({"from": address})
        nonce = w3.eth.get_transaction_count(address)
        tx = tx_data.build_transaction({
            "from": address,
            "nonce": nonce,
            "gas": int(estimated_gas * 1.2),
            "gasPrice": int(w3.eth.gas_price * 1.2),
        })
        signed = w3.eth.account.sign_transaction(tx, private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        return receipt.status == 1
    except Exception as e:
        logger.debug(f"  Burn failed for {str(token_id)[:16]}...: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════════
#  Main auto-redeem entry point
# ═══════════════════════════════════════════════════════════════════════════

def auto_redeem(
    private_key,
    relayer_api_key=None,
    relayer_api_key_address=None,
):
    """
    Auto-redeem all tokens from resolved markets.

    Key discovery: on Polymarket with sig_type=0, CTF tokens live at the
    SIGNER address (same as profile), NOT at the derived Safe.  The Safe
    is only used for submitting gasless transactions via the relayer.

    If relayer credentials are provided, uses gasless Relayer API.
    Otherwise falls back to raw on-chain TX (EOA, needs MATIC for gas).

    Returns:
        {"redeemed": int, "gained_usdce": float, "errors": list}
    """
    result = {"redeemed": 0, "gained_usdce": 0.0, "gained_pusd": 0.0, "gained_total": 0.0, "errors": []}

    try:
        signer_address = Account.from_key(private_key).address

        # CRITICAL: CTF tokens live at the SIGNER address (profile wallet).
        # The derived Safe (0x25a053...) holds ZERO tokens.
        # The relayer submits TXs FROM the Safe, which has nothing to redeem.
        # Therefore we MUST use raw TX mode (direct from signer, costs MATIC gas).
        logger.info(f"Auto-redeem [raw TX] for {signer_address[:10]}...")
        token_address = signer_address

        # Connect to Polygon for on-chain checks
        w3 = _connect_polygon()
        if not w3:
            result["errors"].append("Could not connect to Polygon RPC")
            logger.warning("Auto-redeem: could not connect to Polygon RPC")
            return result

        # Collateral balances before — check both pUSD (V2) and USDC.e (legacy)
        usdce_check_addr = signer_address
        usdce_before = _get_usdce_balance(w3, usdce_check_addr)
        pusd_before = _get_pusd_balance(w3, usdce_check_addr)

        # Fetch positions from Data API using signer/profile address
        positions = _fetch_positions(signer_address)
        if not positions:
            logger.debug("  No positions found from Data API")
            return result

        logger.info(f"  Found {len(positions)} positions from Data API")

        seen_conditions = set()

        for pos in positions:
            token_id = pos.get("asset") or pos.get("tokenId") or pos.get("token_id")
            if not token_id:
                continue

            # Check on-chain balance at the SIGNER address (where tokens live)
            try:
                balance = _check_ctf_balance(w3, token_address, token_id)
            except Exception as e:
                logger.debug(f"  CTF balance check failed: {e}")
                continue
            if balance == 0:
                continue

            # Use conditionId from position data (Gamma API is unreliable for these tokens)
            cond_id = pos.get("conditionId") or pos.get("condition_id")
            if not cond_id:
                # Fallback: try Gamma API
                info = _get_condition_id(token_id)
                if info:
                    cond_id = info.get("condition_id")

            if not cond_id:
                logger.debug(f"  No conditionId for token {str(token_id)[:20]}...")
                continue

            if cond_id in seen_conditions:
                continue
            seen_conditions.add(cond_id)

            # Determine if market is closed from position data
            cur_value = float(pos.get("currentValue") or 0)
            cur_price = float(pos.get("curPrice") or 0)
            title = pos.get("title") or pos.get("eventSlug") or "Unknown"

            # A position at exactly 100 cents is resolved AND winning
            is_resolved = (cur_price >= 0.99)
            
            # If the token is worthless (losing side), we can never redeem it for anything.
            if cur_price <= 0.01 or cur_value == 0:
                logger.info(f"  Burning worthless token: {title[:50]}...")
                _burn_worthless_token(w3, private_key, token_address, token_id, balance)
                continue

            if not is_resolved:
                # Double-check via Gamma
                info = _get_condition_id(token_id)
                if info and info.get("closed"):
                    is_resolved = True
                else:
                    logger.debug(f"  Skipping: {title[:50]}... (market still open, price={cur_price})")
                    continue

            # Determine neg_risk from position data or Gamma
            neg_risk = pos.get("neg_risk", False) or pos.get("negRisk", False)
            if not neg_risk:
                info = _get_condition_id(token_id)
                if info:
                    neg_risk = info.get("neg_risk", False)

            question_short = title[:50]
            logger.info(f"  Redeeming: {question_short}... (negRisk={neg_risk}, bal={balance})")

            # Try redeeming — always raw TX (tokens are at signer, not Safe)
            try:
                ok = _redeem_raw_tx(w3, private_key, cond_id, neg_risk)

                if ok:
                    result["redeemed"] += 1
                    logger.info(f"  >> Redeemed: {question_short}")
                else:
                    logger.debug(f"  Skipped conditionId={cond_id[:16]}... (revert)")
            except Exception as e:
                logger.warning(f"  Redeem error for {question_short}: {e}")
                result["errors"].append(f"{question_short}: {str(e)[:80]}")

            time.sleep(2)  # Avoid nonce collisions

        # Calculate gains — V2 pays out in pUSD, legacy in USDC.e
        if result["redeemed"] > 0:
            time.sleep(3)
            usdce_after = _get_usdce_balance(w3, usdce_check_addr)
            pusd_after = _get_pusd_balance(w3, usdce_check_addr)
            result["gained_usdce"] = usdce_after - usdce_before
            result["gained_pusd"] = pusd_after - pusd_before
            result["gained_total"] = result["gained_usdce"] + result["gained_pusd"]
            logger.info(
                f"Redemption complete: +${result['gained_total']:.4f} "
                f"(pUSD: +${result['gained_pusd']:.4f}, USDCe: +${result['gained_usdce']:.4f}) "
                f"from {result['redeemed']} market(s)"
            )

    except Exception as e:
        logger.error(f"Auto-redeem error: {e}")
        result["errors"].append(str(e))

    return result

