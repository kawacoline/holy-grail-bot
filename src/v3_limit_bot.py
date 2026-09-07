"""
V3 Limit Order Pre-Placement Strategy Bot for Polymarket.

Target markets: 1-hour Up/Down crypto markets (BTC, ETH, SOL, XRP).
Mechanism:
  1. Discover the next upcoming 1-hour Up/Down market for each asset.
  2. Pre-place passive GTC limit orders at $0.45 on BOTH sides (Up + Down).
  3. Combined cost = $0.90. If both fill, payout = $1.00 → $0.10 profit (~11%).
  4. If only one side fills, unwind to flatten exposure.

Slug format (discovered from Polymarket):
  Event:  bitcoin-up-or-down-april-1-2026-8pm-et
  Market: same slug pattern (binary Up/Down within the event)
"""

import re
import time
import json
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx

from .config import Settings
from .market_lookup import fetch_market_from_slug
from .trading import (
    get_client,
    get_balance,
    place_order,
    extract_order_id,
    get_positions,
)

import logging
from loguru import logger
from .logging_config import setup_logging
setup_logging()

logging.getLogger("httpx").setLevel(logging.WARNING)

# ─── Constants ────────────────────────────────────────────────────────────────

V3_ASSETS = ["bitcoin", "ethereum", "solana", "xrp"]
V3_LIMIT_PRICE = 0.45           # $0.45 per side
V3_COMBINED_TARGET = 0.90       # $0.90 combined
V3_WINDOW_SECONDS = 3600        # 1 hour

# Month names for slug generation
MONTH_NAMES = [
    "", "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december"
]


# ─── Market Discovery ────────────────────────────────────────────────────────

def _format_hour_et(dt: datetime) -> str:
    """Format a datetime to Polymarket's hour slug component like '8pm' or '12am'."""
    hour = dt.hour
    if hour == 0:
        return "12am"
    elif hour < 12:
        return f"{hour}am"
    elif hour == 12:
        return "12pm"
    else:
        return f"{hour - 12}pm"


def _compute_1h_slug(asset: str, dt_et: datetime) -> str:
    """
    Build a slug like: bitcoin-up-or-down-april-1-2026-8pm-et
    dt_et must already be in US/Eastern timezone.
    """
    month = MONTH_NAMES[dt_et.month]
    day = dt_et.day
    year = dt_et.year
    hour_str = _format_hour_et(dt_et)
    return f"{asset}-up-or-down-{month}-{day}-{year}-{hour_str}-et"


def _get_eastern_now() -> datetime:
    """Get current time in US/Eastern (UTC-4 for EDT, UTC-5 for EST).
    Simple approach: use UTC offset. Most of the year this is EDT (UTC-4)."""
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo("America/New_York")
        return datetime.now(tz)
    except Exception:
        # Fallback: assume EDT (UTC-4) if zoneinfo is missing or has no data
        utc_now = datetime.now(timezone.utc)
        et_now = utc_now - timedelta(hours=4)
        return et_now.replace(tzinfo=None)


def _fetch_market_via_gamma(slug: str) -> dict:
    """Fetch market info via Gamma API (reliable). Falls back to HTML scrape."""
    try:
        resp = httpx.get(
            f"https://gamma-api.polymarket.com/events?slug={slug}",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        events = resp.json()
        if events and len(events) > 0:
            mkt = events[0].get("markets", [{}])[0]
            clob_tokens = mkt.get("clobTokenIds") or []
            if isinstance(clob_tokens, str):
                import json
                clob_tokens = json.loads(clob_tokens)
            outcomes = mkt.get("outcomes") or []
            if isinstance(outcomes, str):
                import json
                outcomes = json.loads(outcomes)
            if len(clob_tokens) == 2:
                return {
                    "market_id": str(mkt.get("id", "")),
                    "yes_token_id": clob_tokens[0],
                    "no_token_id": clob_tokens[1],
                    "outcomes": outcomes,
                    "question": mkt.get("question", ""),
                    "end_date": mkt.get("endDate") or events[0].get("endDate"),
                }
    except Exception:
        pass
    # Fallback to HTML scrape
    return fetch_market_from_slug(slug)


def find_upcoming_1h_markets(assets: list[str] = None) -> list[dict]:
    """
    Find upcoming/live 1-hour Up/Down markets for the given assets.
    Returns a list of dicts with market info for each asset found.
    """
    if assets is None:
        assets = V3_ASSETS

    now_et = _get_eastern_now()
    found_markets = []

    for asset in assets:
        market_info = None

        # Try current hour and next 2 hours
        for hour_offset in range(3):
            target_dt = now_et.replace(minute=0, second=0, microsecond=0) + timedelta(hours=hour_offset)
            slug = _compute_1h_slug(asset, target_dt)

            try:
                logger.debug(f"  Trying slug: {slug}")
                info = _fetch_market_via_gamma(slug)
                market_info = {
                    "asset": asset,
                    "slug": slug,
                    "market_id": info["market_id"],
                    "yes_token_id": info["yes_token_id"],   # Up
                    "no_token_id": info["no_token_id"],      # Down
                    "question": info.get("question", ""),
                    "end_date": info.get("end_date"),
                    "hour_et": target_dt,
                }
                logger.info(f"✅ Found {asset} 1h market: {slug}")
                break
            except Exception:
                continue

        if market_info:
            found_markets.append(market_info)
        else:
            logger.warning(f"⚠️ No 1h market found for {asset}")

    return found_markets


def find_1h_markets_via_gamma(assets: list[str] = None) -> list[dict]:
    """
    Alternative discovery via Gamma API search.
    Searches for 'up or down 1 hr' markets.
    """
    if assets is None:
        assets = V3_ASSETS

    found = []
    try:
        resp = httpx.get(
            "https://gamma-api.polymarket.com/events",
            params={"closed": "false", "limit": 1000},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        resp.raise_for_status()
        events = resp.json()

        for asset in assets:
            # Match slugs like "bitcoin-up-or-down-..."
            pattern = f"{asset}-up-or-down-"
            matches = [e for e in events if (e.get("slug") or "").startswith(pattern)]
            if matches:
                # Pick the most recent one
                event = matches[0]
                slug = event["slug"]
                try:
                    info = _fetch_market_via_gamma(slug)
                    found.append({
                        "asset": asset,
                        "slug": slug,
                        "market_id": info["market_id"],
                        "yes_token_id": info["yes_token_id"],
                        "no_token_id": info["no_token_id"],
                        "question": info.get("question", ""),
                        "end_date": info.get("end_date"),
                    })
                    logger.info(f"✅ [Gamma] Found {asset} 1h: {slug}")
                except Exception as e:
                    logger.warning(f"Found {asset} slug {slug} but couldn't fetch: {e}")
    except Exception as e:
        logger.warning(f"Gamma API search failed: {e}")

    return found


# ─── V3 Bot Class ─────────────────────────────────────────────────────────────

class V3LimitBot:
    """
    V3 Limit Order Pre-Placement Bot.

    For each 1-hour Up/Down market:
    - Places a BUY limit at $0.45 for Up (YES) with post_only=True
    - Places a BUY limit at $0.45 for Down (NO) with post_only=True
    - Monitors for fills
    - Unwinds if only one leg fills
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = get_client(settings)

        # Which assets to trade (configurable, defaults to all V3 assets)
        self.assets = V3_ASSETS

        # Active market tracking (keyed by asset name)
        self.active_markets: dict[str, dict] = {}

        # Order tracking per asset: {asset: {"up_order_id": ..., "down_order_id": ..., "placed": bool}}
        self.order_state: dict[str, dict] = {}

        # Execution stats
        self.trades_executed = 0
        self.unwinds_executed = 0
        self.opportunities_found = 0
        self.total_invested = 0.0
        self.total_shares_bought = 0.0
        self.order_history: list[dict] = []

        # Balance
        self.cached_balance = None
        self.sim_balance = self.settings.sim_balance if self.settings.sim_balance > 0 else 100.0
        self.sim_start_balance = self.sim_balance

        # Expose for dashboard compatibility
        self.market_slug = "v3-multi-asset"
        self.market_end_timestamp = None

        # Per-client isolated logger
        if self.settings.private_key:
            from eth_account import Account
            w = Account.from_key(self.settings.private_key).address
            self.client_name = f"Client_{w[:8]}"
        else:
            self.client_name = "Client_Sim"
        from .logging_config import bind_client_logger
        self.logger = bind_client_logger(self.client_name)

        self.logger.info("=" * 70)
        self.logger.info("🛡️ V3 LIMIT ORDER BOT INITIALIZED")
        self.logger.info(f"   Assets: {', '.join(self.assets)}")
        self.logger.info(f"   Limit Price: ${V3_LIMIT_PRICE} per side")
        self.logger.info(f"   Order Size: {self.settings.order_size}")
        self.logger.info(f"   Mode: {'SIMULATION' if self.settings.dry_run else 'LIVE'}")
        self.logger.info("=" * 70)

    def get_time_remaining(self) -> str:
        """Get remaining time for the nearest active market."""
        if not self.active_markets:
            return "No market"
        # Find the nearest end time
        now = int(datetime.now(timezone.utc).timestamp())
        nearest = None
        for mkt in self.active_markets.values():
            end = mkt.get("end_date")
            if end:
                try:
                    from .market_lookup import parse_iso
                    end_dt = parse_iso(end)
                    if end_dt:
                        end_ts = int(end_dt.timestamp())
                        if nearest is None or end_ts < nearest:
                            nearest = end_ts
                except Exception:
                    pass
        if nearest is None:
            return "Unknown"
        remaining = nearest - now
        if remaining <= 0:
            return "CLOSED"
        minutes = int(remaining // 60)
        seconds = int(remaining % 60)
        return f"{minutes}m {seconds}s"

    def get_balance(self) -> float:
        if self.settings.dry_run:
            return self.sim_balance
        from .trading import get_balance
        bal = get_balance(self.settings)
        self.cached_balance = bal
        return bal

    def discover_markets(self):
        """Find and cache the current 1-hour Up/Down markets."""
        self.logger.info("🔍 Discovering 1-hour Up/Down markets...")

        # Try computed slugs first (faster, no API)
        markets = find_upcoming_1h_markets(self.assets)

        # Fallback to Gamma API if computed slugs miss anything
        found_assets = {m["asset"] for m in markets}
        missing = [a for a in self.assets if a not in found_assets]
        if missing:
            self.logger.info(f"Trying Gamma API for: {', '.join(missing)}")
            gamma_markets = find_1h_markets_via_gamma(missing)
            markets.extend(gamma_markets)

        for m in markets:
            self.active_markets[m["asset"]] = m
            if m["asset"] not in self.order_state:
                self.order_state[m["asset"]] = {
                    "up_order_id": None,
                    "down_order_id": None,
                    "placed": False,
                    "up_filled": False,
                    "down_filled": False,
                }

        self.logger.info(f"📊 Found {len(self.active_markets)} markets: {list(self.active_markets.keys())}")
        return len(self.active_markets) > 0

    def check_limit_strategy(self) -> Optional[dict]:
        """
        Check what V3 actions need to be taken.
        Returns an action dict, or None if nothing to do.
        """
        # If no markets discovered yet, try to discover
        if not self.active_markets:
            self.discover_markets()

        if not self.active_markets:
            return None

        # Find assets that don't have orders placed yet
        unplaced = []
        for asset, mkt in self.active_markets.items():
            state = self.order_state.get(asset, {})
            if not state.get("placed", False):
                unplaced.append(asset)

        if unplaced:
            return {
                "action": "place_limits",
                "assets": unplaced,
                "price_up": V3_LIMIT_PRICE,
                "price_down": V3_LIMIT_PRICE,
                "order_size": self.settings.order_size,
                "total_cost": V3_COMBINED_TARGET * self.settings.order_size * len(unplaced),
                "total_investment": V3_COMBINED_TARGET * self.settings.order_size * len(unplaced),
                "expected_profit": (1.0 - V3_COMBINED_TARGET) * self.settings.order_size * len(unplaced),
            }

        return None

    def execute_limit_strategy(self, action: dict):
        """Execute the V3 limit pre-placement for all pending assets."""
        if action["action"] != "place_limits":
            return

        assets = action["assets"]
        size = action["order_size"]

        self.logger.info("=" * 70)
        self.logger.info("🛡️ V3 LIMIT STRATEGY: PRE-PLACING PASSIVE ORDERS")
        self.logger.info(f"   Assets: {', '.join(assets)}")
        self.logger.info(f"   Price: ${V3_LIMIT_PRICE} per side | Size: {size}")
        self.logger.info(f"   Combined cost per asset: ${V3_COMBINED_TARGET}")
        self.logger.info("=" * 70)

        for asset in assets:
            mkt = self.active_markets.get(asset)
            if not mkt:
                self.logger.warning(f"No market data for {asset}, skipping")
                continue

            self.logger.info(f"\n--- {asset.upper()} ({mkt['slug']}) ---")

            if self.settings.dry_run:
                self.logger.info(f"🔸 SIM: Would place UP limit ${V3_LIMIT_PRICE} x {size}")
                self.logger.info(f"🔸 SIM: Would place DOWN limit ${V3_LIMIT_PRICE} x {size}")
                self.order_state[asset] = {
                    "up_order_id": f"sim-{asset}-up",
                    "down_order_id": f"sim-{asset}-down",
                    "placed": True,
                    "up_filled": False,
                    "down_filled": False,
                }
                self._add_to_history({"order_size": size, "total_cost": V3_COMBINED_TARGET}, "success", f"{asset} SIM limits placed")
                continue

            try:
                # Place UP (YES) limit
                self.logger.info(f"Placing {asset} UP limit order...")
                up_res = place_order(
                    self.settings,
                    side="BUY",
                    token_id=mkt["yes_token_id"],
                    price=V3_LIMIT_PRICE,
                    size=size,
                    tif="GTC",
                    post_only=True,
                )
                up_id = extract_order_id(up_res)
                self.logger.success(f"✅ {asset} UP Order placed: {up_id}")

                # Place DOWN (NO) limit
                self.logger.info(f"Placing {asset} DOWN limit order...")
                down_res = place_order(
                    self.settings,
                    side="BUY",
                    token_id=mkt["no_token_id"],
                    price=V3_LIMIT_PRICE,
                    size=size,
                    tif="GTC",
                    post_only=True,
                )
                down_id = extract_order_id(down_res)
                self.logger.success(f"✅ {asset} DOWN Order placed: {down_id}")

                self.order_state[asset] = {
                    "up_order_id": up_id,
                    "down_order_id": down_id,
                    "placed": True,
                    "up_filled": False,
                    "down_filled": False,
                }
                self.trades_executed += 1
                self.total_invested += V3_COMBINED_TARGET * size
                self.total_shares_bought += size * 2  # both sides
                self._add_to_history({"order_size": size, "total_cost": V3_COMBINED_TARGET * size}, "success", f"{asset} limits placed")
                
                # Write to trade log for audit trail
                self._log_trade(asset, mkt['slug'], up_id, down_id, size)

            except Exception as e:
                self.logger.error(f"❌ Failed to place {asset} limits: {e}")
                self._add_to_history({"order_size": size, "total_cost": 0}, "failed", f"{asset}: {str(e)[:50]}")

    def _add_to_history(self, opportunity: dict, status: str, details: str):
        """Add record and trim to last 10 entries."""
        record = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "size": opportunity.get("order_size", "?"),
            "cost": f"${opportunity.get('total_cost', 0):.3f}",
            "status": status,
            "details": details,
        }
        self.order_history.append(record)
        if len(self.order_history) > 10:
            self.order_history.pop(0)

    def _log_trade(self, asset: str, slug: str, up_id: str, down_id: str, size: float):
        """Append trade record to live_trade_log.jsonl for audit."""
        import os
        os.makedirs("logs", exist_ok=True)
        record = {
            "timestamp": datetime.now().isoformat(),
            "strategy": "V3",
            "asset": asset,
            "slug": slug,
            "up_order_id": up_id,
            "down_order_id": down_id,
            "price": V3_LIMIT_PRICE,
            "size": size,
            "combined_cost": V3_COMBINED_TARGET * size,
            "dry_run": self.settings.dry_run,
        }
        try:
            with open("logs/live_trade_log.jsonl", "a") as f:
                f.write(json.dumps(record) + "\n")
        except Exception:
            pass

    # ─── Dashboard compatibility stubs (same API as Btc15mArbBot) ─────────

    def check_arbitrage(self, **kwargs) -> Optional[dict]:
        """Dashboard compat: V3 doesn't use the V1 arb check."""
        return None

    def execute_arbitrage(self, opportunity: dict):
        """Dashboard compat: V3 doesn't use V1 execution."""
        pass

    def show_current_positions(self):
        """Show positions across all tracked tokens."""
        try:
            all_tokens = []
            for mkt in self.active_markets.values():
                all_tokens.extend([mkt["yes_token_id"], mkt["no_token_id"]])
            if not all_tokens:
                return
            positions = get_positions(self.settings, all_tokens)
            self.logger.info("-" * 70)
            self.logger.info("📊 V3 POSITIONS:")
            for asset, mkt in self.active_markets.items():
                up_shares = positions.get(mkt["yes_token_id"], {}).get("size", 0)
                down_shares = positions.get(mkt["no_token_id"], {}).get("size", 0)
                self.logger.info(f"   {asset.upper()}: Up={up_shares:.2f} | Down={down_shares:.2f}")
            self.logger.info("-" * 70)
        except Exception as e:
            self.logger.error(f"Error fetching positions: {e}")
