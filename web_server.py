"""
Holy Grail Bot — Web Dashboard Server

Serves a live web dashboard with:
  - WebSocket for real-time bot state updates
  - REST endpoints for positions, balances, trades, redemption
  - On-chain balance checking (USDC.e + USDC on Polygon)
"""

import os
import sys
import json
import glob
import asyncio
import time
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cloudflare_bypass  # Ensure the HTTP/2 thread bug patch is applied to the web server

from src.positions import fetch_active_positions, fetch_closed_positions, get_wallet_address, compute_honest_pnl

# Cache for CLOB API credentials to avoid re-deriving on every poll cycle
_api_creds_cache: dict = {}  # key: private_key_hash -> (client, creds)

TRADE_LOG_PATH = "logs/live_trade_log.jsonl"
STATE_FILE = "data/dashboard_state.json"

app = FastAPI(title="Holy Grail Dashboard")

# Allow local dev connections
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ═══════════════════════════════════════════════════════════════════════════
#  Connected WebSocket clients
# ═══════════════════════════════════════════════════════════════════════════

_ws_clients: list[WebSocket] = []


# ═══════════════════════════════════════════════════════════════════════════
#  Env / Client Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _read_env_value(filepath, key):
    """Read a specific key's value from an env file."""
    try:
        with open(filepath, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith(f"{key}="):
                    val = line.split("=", 1)[1].strip()
                    return val if val else None
    except Exception:
        pass
    return None


def get_all_clients():
    """
    Build client info from env files.
    Uses POLYMARKET_PROFILE for the Data API (that's the proxy wallet visible on Polymarket).
    Falls back to deriving from private key if PROFILE is not set.
    """
    clients = []
    for env_file in sorted(glob.glob("clients/*.env")):
        pk = _read_env_value(env_file, "POLYMARKET_PRIVATE_KEY")
        if not pk:
            continue

        # The PROFILE address is what Polymarket uses to index positions
        profile_addr = _read_env_value(env_file, "POLYMARKET_PROFILE")
        eoa_addr = get_wallet_address(pk)

        # Use profile (proxy wallet) for data queries, fallback to EOA
        data_addr = profile_addr or eoa_addr
        client_name = os.path.basename(env_file).replace(".env", "").capitalize()
        strategy = _read_env_value(env_file, "STRATEGY") or "1"
        dry_run = (_read_env_value(env_file, "DRY_RUN") or "false").lower() == "true"
        order_size = _read_env_value(env_file, "ORDER_SIZE") or "5"

        clients.append({
            "name": client_name,
            "eoa": eoa_addr,
            "profile": data_addr,
            "strategy": int(strategy),
            "dry_run": dry_run,
            "order_size": float(order_size),
            "private_key": pk,  # kept in-memory only, never sent to frontend
        })
    return clients


def _get_onchain_balance(wallet_address: str, extra_addresses: list = None) -> dict:
    """
    Fetch on-chain USDC.e and USDC balance for a wallet (and optional extra addresses) on Polygon.
    Returns dict with usdc_e, usdc_native, and total.
    
    Polymarket funds can live at:
      - The EOA (signer address)
      - The profile/proxy wallet (same as signer for sig_type=0)
      - The Gnosis Safe (derived via CREATE2)
    """
    result = {"usdc_e": 0.0, "usdc_native": 0.0, "pusd": 0.0, "total": 0.0, "error": None, "checked_addresses": []}
    
    # Deduplicate addresses
    all_addrs = list(dict.fromkeys(
        [a for a in [wallet_address] + (extra_addresses or []) if a]
    ))
    
    try:
        # USDC.e (bridged) on Polygon
        USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
        # USDC (native) on Polygon
        USDC_NATIVE = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"
        # pUSD (Polymarket V2 collateral) on Polygon
        PUSD = "0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb"

        rpcs = [
            "https://polygon.drpc.org",
            "https://polygon-rpc.com",
            "https://rpc.ankr.com/polygon",
            "https://polygon.llamarpc.com",
        ]

        for addr in all_addrs:
            addr_padded = addr.lower().replace("0x", "").zfill(64)
            call_data = "0x70a08231" + addr_padded
            addr_total = 0.0

            for label, token_addr in [("usdc_e", USDC_E), ("usdc_native", USDC_NATIVE), ("pusd", PUSD)]:
                for rpc_url in rpcs:
                    try:
                        rpc_payload = {
                            "jsonrpc": "2.0",
                            "method": "eth_call",
                            "params": [{"to": token_addr, "data": call_data}, "latest"],
                            "id": 1,
                        }
                        resp = httpx.post(rpc_url, json=rpc_payload, timeout=5)
                        rpc_data = resp.json()
                        result_hex = rpc_data.get("result", "0x0")
                        token_balance = int(result_hex, 16) / 1_000_000
                        result[label] += token_balance
                        addr_total += token_balance
                        break  # success, no need to try other RPCs
                    except Exception:
                        continue  # try next RPC

            result["checked_addresses"].append({"address": addr[:12] + "...", "balance": addr_total})

        result["total"] = result["usdc_e"] + result["usdc_native"] + result["pusd"]
    except Exception as e:
        result["error"] = str(e)
    return result


def _get_or_cache_clob_client(private_key: str, signature_type: int = 0, builder_code: str = None):
    """Get a cached CLOB client with API credentials. Returns (client, success).
    Caches both successes AND failures to prevent repeated 400 errors from the SDK."""
    import hashlib
    cache_key = hashlib.sha256(private_key.strip().encode()).hexdigest()[:16]

    if cache_key in _api_creds_cache:
        cached = _api_creds_cache[cache_key]
        if cached is None:
            return None, False  # Previously failed — skip
        return cached, True

    try:
        from py_clob_client_v2.client import ClobClient
        from py_clob_client_v2.clob_types import BuilderConfig

        builder_cfg = BuilderConfig(builder_code=builder_code) if builder_code else None
        client = ClobClient(
            "https://clob.polymarket.com",
            key=private_key.strip(),
            chain_id=137,
            signature_type=signature_type,
            builder_config=builder_cfg,
        )
        creds = client.create_or_derive_api_key()
        client.set_api_creds(creds)
        _api_creds_cache[cache_key] = client
        return client, True
    except Exception:
        # Wallet not activated on Polymarket (no deposits/trades yet)
        # Cache the failure so the SDK's 400 error only prints ONCE per boot
        _api_creds_cache[cache_key] = None
        return None, False


def _get_clob_balance(private_key: str, signature_type: int = 0) -> float:
    """Get CLOB API balance (exchange deposit balance)."""
    try:
        from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType

        client, ok = _get_or_cache_clob_client(private_key, signature_type)
        if not ok or client is None:
            return 0.0

        params = BalanceAllowanceParams(
            asset_type=AssetType.COLLATERAL,
            signature_type=signature_type,
        )
        result = client.get_balance_allowance(params)
        if isinstance(result, dict):
            return float(result.get("balance", "0")) / 1_000_000
    except Exception:
        pass
    return 0.0


# ═══════════════════════════════════════════════════════════════════════════
#  REST API Endpoints
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/api/wallets")
def api_wallets():
    clients = get_all_clients()
    # Strip private keys before sending
    safe = [{k: v for k, v in c.items() if k != "private_key"} for c in clients]
    return {"wallets": safe}


@app.get("/api/balance")
def api_balance():
    """
    Get real on-chain + CLOB balances for all clients.
    This is the authoritative balance endpoint.
    Checks: EOA, profile/proxy wallet, AND derived Gnosis Safe.
    """
    clients = get_all_clients()
    balances = []
    for c in clients:
        # Collect all possible addresses where funds might live
        extra_addrs = []
        
        # Profile address (may differ from EOA for proxy wallets)
        if c["profile"] and c["profile"] != c["eoa"]:
            extra_addrs.append(c["profile"])
        
        # Derive Gnosis Safe address
        safe_addr = None
        try:
            from src.redeem import _get_safe_address
            safe_addr = _get_safe_address(c["eoa"])
            if safe_addr and safe_addr not in [c["eoa"], c["profile"]]:
                extra_addrs.append(safe_addr)
        except Exception:
            pass

        # On-chain balance (USDC.e + USDC) across all addresses
        onchain = _get_onchain_balance(c["eoa"], extra_addresses=extra_addrs)

        # CLOB exchange balance
        sig_type = 0
        env_files = sorted(glob.glob("clients/*.env"))
        for ef in env_files:
            if c["name"].lower() in os.path.basename(ef).lower():
                st = _read_env_value(ef, "POLYMARKET_SIGNATURE_TYPE")
                if st:
                    sig_type = int(st)
                break

        clob = _get_clob_balance(c["private_key"], sig_type)

        balances.append({
            "client": c["name"],
            "eoa": c["eoa"],
            "profile": c["profile"],
            "safe": safe_addr,
            "onchain": onchain,
            "clob_balance": clob,
            "total": max(onchain["total"], clob),
            "dry_run": c["dry_run"],
        })

    return {"balances": balances}


@app.get("/api/bot-state")
def api_bot_state():
    """Read the latest bot state dumped by dashboard.py."""
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
            # Check staleness
            ts = data.get("timestamp", "")
            if ts:
                try:
                    state_time = datetime.fromisoformat(ts)
                    age = (datetime.now() - state_time).total_seconds()
                    data["age_seconds"] = age
                    data["stale"] = age > 30
                except Exception:
                    data["stale"] = True
            return data
        return {"error": "Bot not running — no state file found", "stale": True}
    except Exception as e:
        return {"error": str(e), "stale": True}


@app.get("/api/positions")
def api_positions():
    """
    Fetch active + closed positions PER CLIENT from Polymarket Data API.
    Uses the Activity API for HONEST PnL (includes both winning AND losing legs).
    """
    clients = get_all_clients()
    per_client = []

    for c in clients:
        addr = c["profile"]

        active = fetch_active_positions(addr, limit=200)
        closed = fetch_closed_positions(addr, limit=200)

        # Tag each position with client info
        for p in active:
            p["_client"] = c["name"]
        for p in closed:
            p["_client"] = c["name"]

        active_value = sum(float(p.get("currentValue") or 0) for p in active)
        closed_volume = sum(float(p.get("totalBought") or 0) for p in closed)

        # HONEST PnL via Activity API — tracks ALL trades + redeems
        # The old /closed-positions approach only showed winning legs for
        # dual-side strategies, hiding losses and inflating win rates.
        try:
            honest = compute_honest_pnl(addr)
            paired_pnl = honest["true_pnl"]
            pair_wins = honest["wins"]
            pair_losses = honest["losses"]
            win_rate = honest["win_rate"]
            total_bought = honest["total_bought"]
            total_sold = honest["total_sold"]
            total_redeemed = honest["total_redeemed"]
            markets_traded = honest["markets_traded"]
            trade_count = honest["trade_count"]
        except Exception as e:
            # Fallback to closed-positions if activity API fails
            paired_pnl = sum(float(p.get("realizedPnl") or 0) for p in closed)
            pair_wins = sum(1 for p in closed if float(p.get("realizedPnl") or 0) > 0)
            pair_losses = 0
            win_rate = 100 if pair_wins > 0 else 0
            total_bought = 0
            total_sold = 0
            total_redeemed = 0
            markets_traded = 0
            trade_count = 0

        # Count redeemable positions (resolved markets where current=100¢ or current=0¢)
        redeemable = sum(1 for p in active if float(p.get("currentValue") or 0) == 0 or
                        (float(p.get("curPrice") or 0) >= 0.99))

        per_client.append({
            "client": {k: v for k, v in c.items() if k != "private_key"},
            "active": active,
            "closed": closed,
            "stats": {
                "active_count": len(active),
                "active_value": active_value,
                "closed_count": len(closed),
                "closed_pnl": paired_pnl,
                "closed_volume": closed_volume,
                "wins": pair_wins,
                "losses": pair_losses,
                "win_rate": win_rate,
                "redeemable": redeemable,
                "total_bought": total_bought,
                "total_sold": total_sold,
                "total_redeemed": total_redeemed,
                "markets_traded": markets_traded,
                "trade_count": trade_count,
            }
        })

    return {"clients": per_client}


@app.post("/api/redeem/{client_index}")
def api_redeem(client_index: int):
    """Trigger auto-redeem for a specific client."""
    import traceback
    clients = get_all_clients()
    if client_index < 0 or client_index >= len(clients):
        return JSONResponse({"error": "Invalid client index"}, status_code=400)

    c = clients[client_index]
    env_file = sorted(glob.glob("clients/*.env"))[client_index]
    pk = _read_env_value(env_file, "POLYMARKET_PRIVATE_KEY")
    relayer_key = _read_env_value(env_file, "RELAYER_API_KEY")
    relayer_addr = _read_env_value(env_file, "RELAYER_API_KEY_ADDRESS")

    try:
        from src.redeem import auto_redeem
        result = auto_redeem(pk, relayer_api_key=relayer_key, relayer_api_key_address=relayer_addr)
        return {"client": c["name"], "result": result}
    except Exception as e:
        tb = traceback.format_exc()
        print(f"[REDEEM ERROR] {c['name']}: {tb}")
        return JSONResponse({"error": str(e), "traceback": tb}, status_code=500)


@app.post("/api/redeem-all")
def api_redeem_all():
    """Trigger auto-redeem for ALL clients."""
    import traceback
    clients = get_all_clients()
    results = []
    for i, c in enumerate(clients):
        if c["dry_run"]:
            results.append({"client": c["name"], "result": {"redeemed": 0, "skipped": "dry_run"}})
            continue
        env_file = sorted(glob.glob("clients/*.env"))[i]
        pk = _read_env_value(env_file, "POLYMARKET_PRIVATE_KEY")
        relayer_key = _read_env_value(env_file, "RELAYER_API_KEY")
        relayer_addr = _read_env_value(env_file, "RELAYER_API_KEY_ADDRESS")
        try:
            from src.redeem import auto_redeem
            result = auto_redeem(pk, relayer_api_key=relayer_key, relayer_api_key_address=relayer_addr)
            results.append({"client": c["name"], "result": result})
        except Exception as e:
            tb = traceback.format_exc()
            print(f"[REDEEM ERROR] {c['name']}: {tb}")
            results.append({"client": c["name"], "error": str(e), "traceback": tb})
    return {"results": results}


@app.get("/api/trade-log")
def api_trade_log():
    """Return the full chronological trade log from live_trade_log.jsonl."""
    trades = []
    if os.path.exists(TRADE_LOG_PATH):
        with open(TRADE_LOG_PATH, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    trades.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    # Sort chronologically (newest first)
    trades.sort(key=lambda t: t.get("timestamp", ""), reverse=True)
    return {"trades": trades, "total": len(trades)}


@app.post("/api/reset")
def api_reset():
    """Clear the trade log to start fresh."""
    try:
        if os.path.exists(TRADE_LOG_PATH):
            # Archive old log before clearing
            archive_path = TRADE_LOG_PATH.replace(".jsonl", f"_archive_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl")
            os.rename(TRADE_LOG_PATH, archive_path)
        # Create fresh empty log
        os.makedirs("logs", exist_ok=True)
        with open(TRADE_LOG_PATH, "w") as f:
            pass
        return {"status": "ok", "message": "Trade log cleared. Old data archived.", "archive": archive_path if 'archive_path' in dir() else None}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/open-orders")
def api_open_orders():
    """
    Fetch live open/pending limit orders from the Polymarket CLOB for each client.
    Uses the V2 SDK's get_open_orders() — these are GTC limit orders sitting on the book.
    Wrapped in a thread timeout to prevent the endpoint from hanging on socket errors.
    """
    import traceback
    import concurrent.futures
    clients = get_all_clients()
    all_orders = []

    def _fetch_for_client(i, c):
        """Fetch open orders for a single client. Runs in a thread with timeout."""
        env_file = sorted(glob.glob("clients/*.env"))[i]
        pk = _read_env_value(env_file, "POLYMARKET_PRIVATE_KEY")
        sig_type = int(_read_env_value(env_file, "POLYMARKET_SIGNATURE_TYPE") or "0")
        funder = _read_env_value(env_file, "POLYMARKET_FUNDER")
        builder_code = _read_env_value(env_file, "POLY_BUILDER_CODE")

        if not pk:
            return []

        orders = []
        try:
            from py_clob_client_v2.clob_types import OpenOrderParams

            client, ok = _get_or_cache_clob_client(pk, sig_type, builder_code)
            if not ok or client is None:
                return []  # Skip wallets that aren't activated on Polymarket yet

            # Fetch all open orders
            raw_orders = client.get_open_orders(only_first_page=True)

            for o in raw_orders:
                # Handle both dict and object responses
                if isinstance(o, dict):
                    order = o
                else:
                    order = o.__dict__ if hasattr(o, '__dict__') else {"raw": str(o)}

                # Resolve asset name from token_id via Gamma API (cached)
                asset_id = order.get("asset_id", "")
                side = order.get("side", "?")
                price = float(order.get("price", 0))
                original_size = float(order.get("original_size", 0) or order.get("size", 0))
                size_matched = float(order.get("size_matched", 0))
                remaining = original_size - size_matched

                orders.append({
                    "client": c["name"],
                    "client_index": i + 1,
                    "order_id": order.get("id", ""),
                    "asset_id": asset_id[:16] + "..." if len(asset_id) > 16 else asset_id,
                    "asset_id_full": asset_id,
                    "side": side,
                    "price": price,
                    "original_size": original_size,
                    "size_matched": size_matched,
                    "remaining": remaining,
                    "status": order.get("status", "LIVE"),
                    "order_type": order.get("type", "GTC"),
                    "created_at": order.get("created_at", ""),
                    "expiration": order.get("expiration", ""),
                    "market": order.get("market", ""),
                })

        except Exception as e:
            tb = traceback.format_exc()
            print(f"[OPEN-ORDERS ERROR] {c['name']}: {tb}")
            orders.append({
                "client": c["name"],
                "client_index": i + 1,
                "error": str(e),
            })
        return orders

    # Run each client's fetch in parallel with a 15-second timeout
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(_fetch_for_client, i, c): c
            for i, c in enumerate(clients)
        }
        for future in concurrent.futures.as_completed(futures, timeout=15):
            try:
                result = future.result(timeout=10)
                all_orders.extend(result)
            except concurrent.futures.TimeoutError:
                c = futures[future]
                all_orders.append({
                    "client": c["name"],
                    "error": "Request timed out (15s)",
                })
            except Exception as e:
                c = futures[future]
                all_orders.append({
                    "client": c["name"],
                    "error": str(e),
                })

    return {"orders": all_orders, "total": len([o for o in all_orders if "error" not in o])}


# ═══════════════════════════════════════════════════════════════════════════
#  WebSocket — Live state push
# ═══════════════════════════════════════════════════════════════════════════

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _ws_clients.append(ws)
    try:
        # Push initial state immediately
        state = _read_state_file()
        if state:
            await ws.send_json({"type": "state", "data": state})

        # Keep alive + push updates
        last_mtime = 0
        while True:
            await asyncio.sleep(1)
            try:
                current_mtime = os.path.getmtime(STATE_FILE) if os.path.exists(STATE_FILE) else 0
                if current_mtime > last_mtime:
                    last_mtime = current_mtime
                    state = _read_state_file()
                    if state:
                        await ws.send_json({"type": "state", "data": state})
            except Exception:
                pass
    except WebSocketDisconnect:
        pass
    finally:
        if ws in _ws_clients:
            _ws_clients.remove(ws)


def _read_state_file():
    """Read the bot state JSON file."""
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return None


# ═══════════════════════════════════════════════════════════════════════════
#  Debug Endpoint
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/api/debug/redeem")
def api_debug_redeem():
    """
    Step-by-step diagnostic: tests every phase of the redeem pipeline.
    Hit this endpoint to see exactly where the redeem process fails.
    """
    import traceback
    steps = []

    # Step 1: Read client env files
    clients = get_all_clients()
    steps.append({"step": "1. Read client envs", "ok": True, "clients": len(clients)})

    for i, c in enumerate(clients):
        client_steps = []
        client_name = c["name"]
        env_file = sorted(glob.glob("clients/*.env"))[i]
        pk = _read_env_value(env_file, "POLYMARKET_PRIVATE_KEY")
        relayer_key = _read_env_value(env_file, "RELAYER_API_KEY")
        relayer_addr = _read_env_value(env_file, "RELAYER_API_KEY_ADDRESS")
        profile = c["profile"]

        # Step 2: Derive signer
        try:
            from eth_account import Account
            signer = Account.from_key(pk).address
            client_steps.append({"step": "Derive signer", "ok": True, "signer": signer})
        except Exception as e:
            client_steps.append({"step": "Derive signer", "ok": False, "error": str(e)})
            steps.append({"client": client_name, "steps": client_steps})
            continue

        # Step 3: Check relayer deps
        use_relayer = bool(relayer_key and relayer_addr)
        client_steps.append({"step": "Relayer mode", "ok": True, "use_relayer": use_relayer,
                             "relayer_key_present": bool(relayer_key), "relayer_addr": relayer_addr})

        # Step 4: Derive Safe address
        if use_relayer:
            try:
                from src.redeem import _get_safe_address
                safe = _get_safe_address(signer)
                client_steps.append({"step": "Derive Safe address", "ok": bool(safe), "safe": safe})
            except Exception as e:
                client_steps.append({"step": "Derive Safe address", "ok": False, "error": str(e), "traceback": traceback.format_exc()})

        # Step 5: Connect Polygon RPC
        try:
            from src.redeem import _connect_polygon
            w3 = _connect_polygon()
            client_steps.append({"step": "Polygon RPC", "ok": bool(w3), "connected": bool(w3)})
        except Exception as e:
            client_steps.append({"step": "Polygon RPC", "ok": False, "error": str(e)})
            steps.append({"client": client_name, "steps": client_steps})
            continue

        # Step 6: Fetch positions from Data API (using profile/proxy address)
        try:
            from src.redeem import _fetch_positions
            positions_proxy = _fetch_positions(profile)
            positions_eoa = _fetch_positions(signer)
            client_steps.append({
                "step": "Fetch positions",
                "ok": True,
                "profile_addr": profile,
                "positions_via_profile": len(positions_proxy),
                "positions_via_eoa": len(positions_eoa),
            })
        except Exception as e:
            client_steps.append({"step": "Fetch positions", "ok": False, "error": str(e)})

        # Step 7: Check USDCe balance
        if w3:
            try:
                from src.redeem import _get_usdce_balance
                balance_addr = safe if use_relayer and safe else signer
                bal = _get_usdce_balance(w3, balance_addr)
                client_steps.append({"step": "USDCe balance", "ok": True, "address": balance_addr, "balance": bal})
            except Exception as e:
                client_steps.append({"step": "USDCe balance", "ok": False, "error": str(e)})

        # Step 8: Check on-chain CTF balances for first few positions
        positions_to_check = (positions_proxy or positions_eoa or [])[:5]
        if positions_to_check and w3:
            ctf_checks = []
            from src.redeem import _check_ctf_balance, _get_condition_id
            balance_addr = safe if use_relayer and safe else signer
            for pos in positions_to_check:
                token_id = pos.get("asset") or pos.get("tokenId") or pos.get("token_id")
                if not token_id:
                    continue
                try:
                    bal = _check_ctf_balance(w3, balance_addr, token_id)
                    info = _get_condition_id(token_id)
                    ctf_checks.append({
                        "token": str(token_id)[:20] + "...",
                        "onchain_balance": bal,
                        "market": (info or {}).get("question", "?")[:50],
                        "closed": (info or {}).get("closed", False),
                        "neg_risk": (info or {}).get("neg_risk", False),
                    })
                except Exception as e:
                    ctf_checks.append({"token": str(token_id)[:20] + "...", "error": str(e)})
            client_steps.append({"step": "CTF balance checks (first 5)", "ok": True, "checks": ctf_checks})

        steps.append({"client": client_name, "dry_run": c["dry_run"], "steps": client_steps})

    return {"diagnostic": steps}


# Serve static files last (the new dashboard)
app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    print("=" * 60)
    print("  HOLY GRAIL — WEB DASHBOARD")
    print("  Open: http://127.0.0.1:8000")
    print("=" * 60)
    uvicorn.run("web_server:app", host="127.0.0.1", port=8000, reload=True)
