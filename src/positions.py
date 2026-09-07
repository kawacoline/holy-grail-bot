"""
Polymarket Position Tracker — fetches active & closed positions + honest PnL
from the Data API.

Endpoints (official docs):
  GET https://data-api.polymarket.com/positions?user=<address>
  GET https://data-api.polymarket.com/closed-positions?user=<address>
  GET https://data-api.polymarket.com/activity?user=<address>

The `user` parameter should be the **proxy wallet** address visible on the
profile page (e.g. https://polymarket.com/@kawacoline).

NOTE: The /closed-positions endpoint sorts by REALIZEDPNL DESC by default
and only shows positions that existed at resolution. For strategies that buy
BOTH sides of a market, this hides the losing legs. Use the /activity
endpoint for honest PnL calculation.
"""

import httpx
from loguru import logger
from typing import Optional
from collections import defaultdict


def get_wallet_address(private_key: str) -> str:
    """Derive the EOA wallet address from a private key (used as proxy wallet for sig_type=0)."""
    try:
        from eth_account import Account
        account = Account.from_key(private_key.strip())
        return account.address
    except Exception as e:
        logger.warning(f"Could not derive wallet from private key: {e}")
        return ""


def _safe_float(val, default=0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def fetch_active_positions(wallet_address: str, limit: int = 100) -> list:
    """Fetch current (open) positions for a wallet."""
    try:
        resp = httpx.get(
            "https://data-api.polymarket.com/positions",
            params={"user": wallet_address, "limit": limit},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning(f"Failed to fetch active positions for {wallet_address[:10]}...: {e}")
    return []


def fetch_closed_positions(wallet_address: str, limit: int = 100) -> list:
    """Fetch closed (resolved) positions for a wallet."""
    try:
        resp = httpx.get(
            "https://data-api.polymarket.com/closed-positions",
            params={"user": wallet_address, "limit": limit},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning(f"Failed to fetch closed positions for {wallet_address[:10]}...: {e}")
    return []


def _fetch_all_activity(wallet_address: str, activity_type: str, max_pages: int = 10) -> list:
    """
    Fetch ALL activity rows for a wallet, paginating through the API.
    activity_type: 'TRADE', 'REDEEM', 'SPLIT', 'MERGE', etc.
    """
    all_rows = []
    offset = 0
    page_size = 500
    for _ in range(max_pages):
        try:
            resp = httpx.get(
                "https://data-api.polymarket.com/activity",
                params={
                    "user": wallet_address,
                    "limit": page_size,
                    "offset": offset,
                    "type": activity_type,
                },
                timeout=15,
            )
            if resp.status_code != 200:
                break
            batch = resp.json()
            if not batch or not isinstance(batch, list):
                break
            all_rows.extend(batch)
            if len(batch) < page_size:
                break
            offset += page_size
        except Exception as e:
            logger.warning(f"Failed to fetch {activity_type} activity for {wallet_address[:10]}...: {e}")
            break
    return all_rows


def compute_honest_pnl(wallet_address: str) -> dict:
    """
    Compute HONEST PnL using the Activity API.

    The /closed-positions endpoint can miss losing legs of dual-side strategies.
    This function uses raw trade + redeem activity to compute true PnL:
        True PnL = (total_sold + total_redeemed) - total_bought

    Returns dict with:
        total_bought, total_sold, total_redeemed, true_pnl,
        markets_traded, wins, losses, breakeven, win_rate
    """
    # Fetch all trades
    trades = _fetch_all_activity(wallet_address, "TRADE")
    redeems = _fetch_all_activity(wallet_address, "REDEEM")

    total_bought = 0.0
    total_sold = 0.0
    total_redeemed = sum(_safe_float(r.get("usdcSize")) for r in redeems)

    # Group trades by market (eventSlug) for win/loss counting
    market_flows = defaultdict(lambda: {"bought": 0.0, "sold": 0.0, "redeemed": 0.0})

    for t in trades:
        slug = t.get("eventSlug") or "unknown"
        usdc = _safe_float(t.get("usdcSize"))
        side = t.get("side", "")
        if side == "BUY":
            total_bought += usdc
            market_flows[slug]["bought"] += usdc
        elif side == "SELL":
            total_sold += usdc
            market_flows[slug]["sold"] += usdc

    # Assign redeems to markets
    for r in redeems:
        slug = r.get("eventSlug") or "unknown"
        usdc = _safe_float(r.get("usdcSize"))
        market_flows[slug]["redeemed"] += usdc

    # Count wins/losses per market
    wins = 0
    losses = 0
    breakeven = 0
    for slug, flow in market_flows.items():
        net = (flow["sold"] + flow["redeemed"]) - flow["bought"]
        if net > 0.001:
            wins += 1
        elif net < -0.001:
            losses += 1
        else:
            breakeven += 1

    true_pnl = (total_sold + total_redeemed) - total_bought
    total_markets = wins + losses + breakeven
    win_rate = round(wins / (wins + losses) * 100) if (wins + losses) > 0 else 0

    return {
        "total_bought": round(total_bought, 4),
        "total_sold": round(total_sold, 4),
        "total_redeemed": round(total_redeemed, 4),
        "true_pnl": round(true_pnl, 4),
        "markets_traded": total_markets,
        "trade_count": len(trades),
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "win_rate": win_rate,
    }


def summarize_positions(wallet_address: str) -> dict:
    """
    Build a P&L summary across active + closed positions.

    Returns dict with:
        active_count, active_value, active_pnl,
        closed_count, closed_realized_pnl, closed_total_bought,
        overall_pnl
    """
    summary = {
        "wallet": wallet_address,
        "active_count": 0,
        "active_value": 0.0,
        "active_pnl": 0.0,
        "closed_count": 0,
        "closed_realized_pnl": 0.0,
        "closed_total_bought": 0.0,
        "overall_pnl": 0.0,
    }

    # Active positions
    active = fetch_active_positions(wallet_address)
    summary["active_count"] = len(active)
    for pos in active:
        summary["active_value"] += _safe_float(pos.get("currentValue"))
        summary["active_pnl"] += _safe_float(pos.get("cashPnl"))

    # Closed positions
    closed = fetch_closed_positions(wallet_address)
    summary["closed_count"] = len(closed)
    for pos in closed:
        summary["closed_realized_pnl"] += _safe_float(pos.get("realizedPnl"))
        summary["closed_total_bought"] += _safe_float(pos.get("totalBought"))

    # Overall = cash PnL from active + realized PnL from closed
    summary["overall_pnl"] = summary["active_pnl"] + summary["closed_realized_pnl"]

    return summary
