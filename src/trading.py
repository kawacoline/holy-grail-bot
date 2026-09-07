import functools
import logging
from typing import Optional
import time

from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import (
    BalanceAllowanceParams,
    AssetType,
    OrderArgs,
    OrderType,
    PostOrdersArgs,
    PartialCreateOrderOptions,
    BuilderConfig,
)
from py_clob_client_v2.order_builder.constants import BUY, SELL

from .config import Settings

logger = logging.getLogger(__name__)


_cached_clients = {}

def get_client(settings: Settings) -> ClobClient:
    global _cached_clients
    
    if not settings.private_key:
        raise RuntimeError("POLYMARKET_PRIVATE_KEY is required for trading")
        
    cache_key = settings.private_key.strip()
    if cache_key in _cached_clients:
        return _cached_clients[cache_key]
    
    if not settings.private_key:
        raise RuntimeError("POLYMARKET_PRIVATE_KEY is required for trading")
    
    # V2 CLOB endpoint (April 2026 upgrade)
    host = "https://clob.polymarket.com"
    
    # Build V2 builder config for order attribution
    builder_cfg = None
    if settings.builder_code:
        builder_cfg = BuilderConfig(builder_code=settings.builder_code)
        logger.info(f"Builder code attached: {settings.builder_code[:16]}...")
    
    # Create client (Python V2 SDK still uses chain_id)
    client = ClobClient(
        host, 
        key=settings.private_key.strip(), 
        chain_id=137, 
        signature_type=settings.signature_type, 
        funder=settings.funder.strip() if settings.funder else None,
        builder_config=builder_cfg
    )
    
    # Derive API credentials - simple method that works
    logger.info("Deriving User API credentials from private key...")
    logger.info("  (Note: If you already have an API key, the SDK will print a harmless '400 Could not create api key' warning before deriving the existing one.)")
    derived_creds = client.create_or_derive_api_key()
    client.set_api_creds(derived_creds)
    
    logger.info("✅ Existing API credentials successfully derived and configured! (Ignored previous creation warnings)")
    logger.info(f"   API Key: {derived_creds.api_key}")
    logger.info(f"   Wallet: {client.get_address()}")
    logger.info(f"   Funder: {settings.funder}")
    
    _cached_clients[cache_key] = client
    return client
# Global HTTP client for connection pooling to prevent WinError 10013 (port exhaustion)
_rpc_client = None

def get_balance(settings: Settings) -> float:
    """Get USDC balance. Checks CLOB API + on-chain and returns whichever is higher."""
    global _rpc_client
    clob_balance = 0.0
    chain_balance = 0.0

    # 1. Try CLOB API (this is the Polymarket exchange balance)
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        
        client = get_client(settings)
        params = BalanceAllowanceParams(
            asset_type=AssetType.COLLATERAL,
            signature_type=settings.signature_type
        )
        result = client.get_balance_allowance(params)
        if isinstance(result, dict):
            balance_raw = result.get("balance", "0")
            clob_balance = float(balance_raw) / 1_000_000
            # logger.info(f"💰 CLOB balance: ${clob_balance:.4f} (raw={balance_raw})")
        else:
            logger.warning(f"💰 CLOB balance unexpected response: {result}")
    except Exception as e:
        logger.warning(f"💰 CLOB balance FAILED: {str(e)[:100]}")

    # 2. On-chain fallback: read wallet pUSD + USDC directly from Polygon
    try:
        import httpx as _httpx
        from eth_account import Account

        if _rpc_client is None:
            # Persistent connection pool with extended timeout to prevent WinError 10013/10054
            transport = _httpx.HTTPTransport(retries=2)
            _rpc_client = _httpx.Client(
                transport=transport,
                timeout=15.0,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                limits=_httpx.Limits(max_connections=20, max_keepalive_connections=10)
            )

        wallet = Account.from_key(settings.private_key.strip()).address
        # logger.info(f"💰 Checking on-chain balance for {wallet[:10]}...")

        # balanceOf(address) = 0x70a08231 + address padded to 32 bytes
        addr_padded = wallet.lower().replace("0x", "").zfill(64)
        call_data = "0x70a08231" + addr_padded

        # pUSD (Polymarket V2 Collateral)
        PUSD = "0xC011a7e12a19f7b1f670d46f03b03f3342e82dfb"
        # USDC (native) on Polygon
        USDC_NATIVE = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"

        for label, token_addr in [("pUSD", PUSD), ("USDC", USDC_NATIVE)]:
            try:
                rpc_payload = {
                    "jsonrpc": "2.0",
                    "method": "eth_call",
                    "params": [{"to": token_addr, "data": call_data}, "latest"],
                    "id": 1,
                }
                resp = _rpc_client.post("https://polygon.drpc.org", json=rpc_payload)
                resp.raise_for_status()
                rpc_data = resp.json()
                result_hex = rpc_data.get("result", "0x0")
                if result_hex and result_hex != "0x":
                    token_balance = int(result_hex, 16) / 1_000_000
                    chain_balance += token_balance
            except Exception as e:
                # Silence these as they spam the UI and we still have clob_balance usually
                pass
    except Exception as e2:
        pass

    final = max(clob_balance, chain_balance)
    return final


def place_order(settings: Settings, *, side: str, token_id: str, price: float, size: float, tif: str = "GTC", post_only: bool = False) -> dict:
    if price <= 0:
        raise ValueError("price must be > 0")
    if size <= 0:
        raise ValueError("size must be > 0")
    if not token_id:
        raise ValueError("token_id is required")

    side_up = side.upper()
    if side_up not in {"BUY", "SELL"}:
        raise ValueError("side must be BUY or SELL")

    client = get_client(settings)
    
    try:
        # Create order args (V2: add builder_code for attribution)
        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=BUY if side_up == "BUY" else SELL,
            builder_code=settings.builder_code if settings.builder_code else "0x0000000000000000000000000000000000000000000000000000000000000000"
        )
        
        # We MUST NOT hardcode neg_risk. Py-clob-client will auto-resolve it 
        # based on the specific token ID. Some daily/15m markets fluctuate
        # between being NegRisk and standard markets.
        options = PartialCreateOrderOptions()
        signed_order = client.create_order(order_args, options)
        
        tif_up = (tif or "GTC").upper()
        order_type = getattr(OrderType, tif_up, OrderType.GTC)
        return client.post_order(signed_order, order_type, post_only=post_only)
    except Exception as exc:  # pragma: no cover - passthrough from client
        raise RuntimeError(f"place_order failed: {exc}") from exc



def place_orders_fast(settings: Settings, orders: list[dict], *, order_type: str = "GTC") -> list[dict]:
    """Place multiple orders as fast as possible.

    Strategy: pre-sign all orders first, then submit them together.
    This minimizes the time gap between legs.

    Args:
        settings: Bot settings
        orders: List of order dicts with keys: side, token_id, price, size
        order_type: One of OrderType: FOK, FAK, GTC, GTD

    Returns:
        List of order results.
    """
    client = get_client(settings)

    tif_up = (order_type or "GTC").upper()
    ot = getattr(OrderType, tif_up, OrderType.GTC)

    # Step 1: Pre-sign all orders (this is the slow part)
    # MUST NOT hardcode neg_risk. Let the client auto-resolve it.
    builder_code = settings.builder_code if settings.builder_code else "0x0000000000000000000000000000000000000000000000000000000000000000"
    options = PartialCreateOrderOptions()
    signed_orders = []
    for order_params in orders:
        side_up = order_params["side"].upper()
        order_args = OrderArgs(
            token_id=order_params["token_id"],
            price=order_params["price"],
            size=order_params["size"],
            side=BUY if side_up == "BUY" else SELL,
            builder_code=builder_code,
        )
        signed_order = client.create_order(order_args, options)
        signed_orders.append(signed_order)

    # Step 2: Post all orders in a single request when possible.
    try:
        args = [PostOrdersArgs(order=o, orderType=ot) for o in signed_orders]
        result = client.post_orders(args)
        if isinstance(result, list):
            return result
        return [result]
    except Exception:
        # Fallback to sequential posting if batch fails for any reason.
        results: list[dict] = []
        for signed_order in signed_orders:
            try:
                results.append(client.post_order(signed_order, ot))
            except Exception as exc:
                results.append({"error": str(exc)})
        return results


def extract_order_id(result: dict) -> Optional[str]:
    """Best-effort extraction of an order id from API responses."""
    if not isinstance(result, dict):
        return None
    # Common variants observed across APIs/versions
    for key in ("orderID", "orderId", "order_id", "id"):
        val = result.get(key)
        if val:
            return str(val)
    # Sometimes nested
    for key in ("order", "data", "result"):
        nested = result.get(key)
        if isinstance(nested, dict):
            oid = extract_order_id(nested)
            if oid:
                return oid
    return None


def get_order(settings: Settings, order_id: str) -> dict:
    client = get_client(settings)
    return client.get_order(order_id)


def cancel_orders(settings: Settings, order_ids: list[str]) -> Optional[dict]:
    if not order_ids:
        return None
    client = get_client(settings)
    return client.cancel_orders(order_ids)


def _coerce_float(val) -> Optional[float]:
    try:
        if val is None:
            return None
        return float(val)
    except Exception:
        return None


def summarize_order_state(order_data: dict, *, requested_size: Optional[float] = None) -> dict:
    """Normalize an order payload into a small, stable summary.

    The API field names vary by version; this function is defensive.
    """
    if not isinstance(order_data, dict):
        return {"status": None, "filled_size": None, "requested_size": requested_size, "raw": order_data}

    status = order_data.get("status") or order_data.get("state") or order_data.get("order_status")
    status_str = str(status).lower() if status is not None else None

    filled_size = None
    for key in ("filled_size", "filledSize", "size_filled", "sizeFilled", "matched_size", "matchedSize"):
        if key in order_data:
            filled_size = _coerce_float(order_data.get(key))
            break

    # Some payloads provide remaining size rather than filled size
    remaining_size = None
    for key in ("remaining_size", "remainingSize", "size_remaining", "sizeRemaining"):
        if key in order_data:
            remaining_size = _coerce_float(order_data.get(key))
            break

    original_size = None
    for key in ("original_size", "originalSize", "size", "order_size", "orderSize"):
        if key in order_data:
            original_size = _coerce_float(order_data.get(key))
            break

    if filled_size is None and remaining_size is not None and original_size is not None:
        filled_size = max(0.0, original_size - remaining_size)

    return {
        "status": status_str,
        "filled_size": filled_size,
        "remaining_size": remaining_size,
        "original_size": original_size,
        "requested_size": requested_size,
        "raw": order_data,
    }


def wait_for_terminal_order(
    settings: Settings,
    order_id: str,
    *,
    requested_size: Optional[float] = None,
    timeout_seconds: float = 3.0,
    poll_interval_seconds: float = 0.25,
) -> dict:
    """Poll order state until it is terminal, filled, or timeout."""
    terminal_statuses = {"filled", "matched", "canceled", "cancelled", "rejected", "expired"}
    start = time.monotonic()
    last_summary: Optional[dict] = None

    while (time.monotonic() - start) < timeout_seconds:
        try:
            od = get_order(settings, order_id)
            last_summary = summarize_order_state(od, requested_size=requested_size)
        except Exception as exc:
            last_summary = {"status": "error", "error": str(exc), "filled_size": None, "requested_size": requested_size}

        status = (last_summary.get("status") or "").lower() if isinstance(last_summary, dict) else ""
        filled = last_summary.get("filled_size") if isinstance(last_summary, dict) else None

        if requested_size is not None and filled is not None and filled + 1e-9 >= float(requested_size):
            last_summary["terminal"] = True
            last_summary["filled"] = True
            return last_summary

        if status in terminal_statuses:
            last_summary["terminal"] = True
            
            # Treat "matched" as a successful fill because Polymarket FOK/FAK orders often 
            # sit in "matched" status momentarily before fully clearing to "filled"
            if status == "filled" or status == "matched":
                last_summary["filled"] = True
            else:
                last_summary["filled"] = False
                
            return last_summary

        time.sleep(poll_interval_seconds)

    if last_summary is None:
        last_summary = {"status": None, "filled_size": None, "requested_size": requested_size}
    last_summary["terminal"] = False
    last_summary.setdefault("filled", False)
    return last_summary


def get_positions(settings: Settings, token_ids: list[str] = None) -> dict:
    """
    Get current positions (shares owned) for the user.
    
    Args:
        settings: Bot settings
        token_ids: Optional list of token IDs to filter by
        
    Returns:
        Dictionary with token_id -> position data
    """
    try:
        client = get_client(settings)
        
        # Custom implementation because py_clob_client_v2 might lack get_positions
        import httpx
        
        # We need the user address (wallet)
        # client.get_address() or derive it
        try:
             address = client.get_address()
        except:
             # Fallback if get_address is missing too (unlikely for py-clob-client)
             creds = client.create_or_derive_api_key()
             # This usually returns an object with .address or similar, 
             # but let's assume get_address works as seen in logs.
             raise
             
             # This usually returns an object with .address or similar, 
             # but let's assume get_address works as seen in logs.
             raise
             
        # Per docs, positions are in Data API, not CLOB API
        url = f"https://data-api.polymarket.com/positions?user={address}"
        
        # This is a public endpoint on Data API (no sig required usually for read)
        resp = httpx.get(url, timeout=10)
             
        if resp.status_code == 200:
             positions = resp.json()
             if isinstance(positions, list):
                 pass # Good
             else:
                 positions = [] 
        else:
             logger.error(f"Failed to fetch positions: {resp.status_code} {resp.text}")
             return {}
        
        # Filter by token_ids if provided
        result = {}
        for pos in positions:
            # Data API format usually: { "asset": "TOKEN_ID", "size": "10.0", "title": "..." }
            # Or sometimes: { "tokenId": "...", "size": ... }
            # We handle variations defensively
            
            token_id = pos.get("asset") or pos.get("tokenId") or pos.get("token_id")
            
            if token_id:
                if token_ids is None or token_id in token_ids:
                    size = float(pos.get("size", 0))
                    avg_price = float(pos.get("avgPrice") or pos.get("avg_price") or 0)
                    result[token_id] = {
                        "size": size,
                        "avg_price": avg_price,
                        "raw": pos
                    }
        
        return result
    except Exception as e:
        logger.error(f"Error getting positions: {e}")
        return {}
