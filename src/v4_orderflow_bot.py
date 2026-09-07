"""
V4 Order Flow Pre-Placement Strategy Bot for Polymarket.

Target markets: 1-hour Up/Down crypto markets (BTC, ETH, SOL, XRP).
Mechanism:
  1. Discover the next upcoming 1-hour Up/Down market for each asset.
  2. Order Book Depth Filter: Check book for "supply walls". If the path to $0.45 
     is blocked by huge bids, wait. Only proceed if "thin".
  3. Momentum (Order Flow) Trigger: Check recent trades to see if there is active
     volume or momentum before placing the limits.
  4. Pre-place passive GTC limit orders at $0.45 on BOTH sides (Up + Down).
"""

import re
import time
import json
import concurrent.futures
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
# TradeParams no longer needed — using get_last_trade_price for fast momentum checks

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

V4_ASSETS = ["bitcoin", "ethereum", "solana", "xrp"]
V4_LIMIT_PRICE = 0.45           # $0.45 per side
V4_COMBINED_TARGET = 0.90       # $0.90 combined
V4_WINDOW_SECONDS = 3600        # 1 hour

# V4 Specific thresholds
MAX_WALL_SIZE = 50000.0         # Only block truly massive whale walls (50k+ shares)
MIN_MOMENTUM_VOLUME = 1.0       # Enter if there's any trading activity at all (1 share minimum)

# Month names for slug generation
MONTH_NAMES = [
    "", "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december"
]


# ─── Market Discovery ────────────────────────────────────────────────────────

def _format_hour_et(dt: datetime) -> str:
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
    month = MONTH_NAMES[dt_et.month]
    day = dt_et.day
    year = dt_et.year
    hour_str = _format_hour_et(dt_et)
    return f"{asset}-up-or-down-{month}-{day}-{year}-{hour_str}-et"


def _get_eastern_now() -> datetime:
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo("America/New_York")
        return datetime.now(tz)
    except Exception:
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
    if assets is None:
        assets = V4_ASSETS

    now_et = _get_eastern_now()
    found_markets = []

    for asset in assets:
        market_info = None
        for hour_offset in range(3):
            target_dt = now_et.replace(minute=0, second=0, microsecond=0) + timedelta(hours=hour_offset)
            slug = _compute_1h_slug(asset, target_dt)

            try:
                info = _fetch_market_via_gamma(slug)
                market_info = {
                    "asset": asset,
                    "slug": slug,
                    "market_id": info["market_id"],
                    "yes_token_id": info["yes_token_id"],
                    "no_token_id": info["no_token_id"],
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

    return found_markets


def find_1h_markets_via_gamma(assets: list[str] = None) -> list[dict]:
    if assets is None:
        assets = V4_ASSETS

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
            pattern = f"{asset}-up-or-down-"
            matches = [e for e in events if (e.get("slug") or "").startswith(pattern)]
            if matches:
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
                    pass
    except Exception as e:
        logger.warning(f"Gamma API search failed: {e}")

    return found


# ─── V4 Bot Class ─────────────────────────────────────────────────────────────

class V4OrderFlowBot:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = get_client(settings)

        # Persistent HTTP client for fast scanning (connection pooling + keep-alive)
        self._http = httpx.Client(
            base_url="https://clob.polymarket.com",
            timeout=10.0,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=5),
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
        )

        self.assets = V4_ASSETS
        self.active_markets: dict[str, dict] = {}
        self.order_state: dict[str, dict] = {}

        self.trades_executed = 0
        self.unwinds_executed = 0
        self.opportunities_found = 0
        self.total_invested = 0.0
        self.total_shares_bought = 0.0
        self.order_history: list[dict] = []

        self.cached_balance = None
        self.sim_balance = self.settings.sim_balance if self.settings.sim_balance > 0 else 100.0
        self.sim_start_balance = self.sim_balance

        self.market_slug = "v4-orderflow-multi-asset"
        self.market_end_timestamp = None

        if self.settings.private_key:
            from eth_account import Account
            w = Account.from_key(self.settings.private_key).address
            self.client_name = f"Client_{w[:8]}"
        else:
            self.client_name = "Client_Sim"
        from .logging_config import bind_client_logger
        self.logger = bind_client_logger(self.client_name)

        self.logger.info("=" * 70)
        self.logger.info("🛡️ V4 ORDER FLOW BOT INITIALIZED")
        self.logger.info(f"   Assets: {', '.join(self.assets)}")
        self.logger.info(f"   Limit Price: ${V4_LIMIT_PRICE} per side")
        self.logger.info(f"   Max Wall Size: {MAX_WALL_SIZE} | Min Momentum: {MIN_MOMENTUM_VOLUME}")
        self.logger.info(f"   Mode: {'SIMULATION' if self.settings.dry_run else 'LIVE'}")
        self.logger.info("=" * 70)

    def get_time_remaining(self) -> str:
        if not self.active_markets:
            return "No market"
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
        self.logger.info("🔍 Discovering 1-hour Up/Down markets...")
        markets = find_upcoming_1h_markets(self.assets)
        found_assets = {m["asset"] for m in markets}
        missing = [a for a in self.assets if a not in found_assets]
        if missing:
            markets.extend(find_1h_markets_via_gamma(missing))

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

    def _batch_get_books(self, token_ids: list[str]) -> dict:
        """Fetch ALL orderbooks in ONE batch API call via persistent httpx client.
        The /books endpoint expects POST with [{"token_id": "..."}, ...] body.
        Response includes bids, asks, AND last_trade_price for each book.
        Returns {token_id: book_dict}."""
        try:
            payload = [{"token_id": tid} for tid in token_ids]
            resp = self._http.post("/books", json=payload)
            if resp.status_code == 200:
                results = resp.json()
                if isinstance(results, list):
                    books = {}
                    for book in results:
                        asset_id = book.get("asset_id", "")
                        books[asset_id] = book
                    return books
        except Exception as e:
            self.logger.debug(f"Batch books failed, using parallel fallback: {e}")
        
        # Fallback: individual calls in parallel (still fast with 8 workers)
        books = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            futs = {ex.submit(self._fetch_single_book_raw, tid): tid for tid in token_ids}
            for f in concurrent.futures.as_completed(futs):
                tid = futs[f]
                try:
                    books[tid] = f.result()
                except Exception:
                    books[tid] = None
        return books

    def _fetch_single_book_raw(self, token_id: str) -> dict:
        """Fetch a single orderbook via persistent httpx client (connection reuse)."""
        try:
            resp = self._http.get("/book", params={"token_id": token_id})
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        return None

    def _extract_wall_from_book(self, book, target_price: float) -> float:
        """Extract wall size from a pre-fetched orderbook. No API call needed."""
        if book is None:
            return 99999.0
        try:
            bids = []
            if hasattr(book, 'bids') and book.bids:
                bids = book.bids
            elif isinstance(book, dict) and 'bids' in book:
                bids = book['bids']
            
            wall_size = 0.0
            for level in bids:
                price = float(level.get('price', 0) if isinstance(level, dict) else getattr(level, 'price', 0))
                size = float(level.get('size', 0) if isinstance(level, dict) else getattr(level, 'size', 0))
                if price >= target_price:
                    wall_size += size
            return wall_size
        except Exception:
            return 99999.0

    def _extract_momentum_from_book(self, book) -> bool:
        """Extract momentum signal from pre-fetched book (last_trade_price field).
        No extra API call needed — comes free with the orderbook data."""
        if book is None:
            return False
        try:
            ltp = book.get("last_trade_price", "0") if isinstance(book, dict) else getattr(book, 'last_trade_price', "0")
            return float(ltp or "0") > 0
        except Exception:
            return False

    def check_limit_strategy(self) -> Optional[dict]:
        if not self.active_markets:
            self.discover_markets()

        if not self.active_markets:
            return None

        # Collect all assets that need checking (not yet placed)
        pending = {}
        for asset, mkt in self.active_markets.items():
            state = self.order_state.get(asset, {})
            if not state.get("placed", False):
                pending[asset] = mkt

        if not pending:
            return None

        # ── SINGLE API CALL: Batch fetch ALL orderbooks ──
        # The /books response includes bids, asks, AND last_trade_price
        # So we get wall depth + momentum from ONE request
        all_token_ids = []
        for asset, mkt in pending.items():
            all_token_ids.extend([mkt["yes_token_id"], mkt["no_token_id"]])

        books = self._batch_get_books(all_token_ids)

        # ── Evaluate all assets (pure CPU, instant) ──
        unplaced = []
        for asset, mkt in pending.items():
            yes_tid = mkt["yes_token_id"]
            no_tid = mkt["no_token_id"]

            up_book = books.get(yes_tid)
            down_book = books.get(no_tid)

            up_wall = self._extract_wall_from_book(up_book, V4_LIMIT_PRICE)
            down_wall = self._extract_wall_from_book(down_book, V4_LIMIT_PRICE)
            up_has_momentum = self._extract_momentum_from_book(up_book)
            down_has_momentum = self._extract_momentum_from_book(down_book)

            self.logger.debug(
                f"{asset} -> Walls: UP={up_wall:.0f}, DOWN={down_wall:.0f} | "
                f"Momentum: UP={'YES' if up_has_momentum else 'NO'}, DOWN={'YES' if down_has_momentum else 'NO'}"
            )

            # Filter 1: No massive whale walls
            if up_wall > MAX_WALL_SIZE or down_wall > MAX_WALL_SIZE:
                self.logger.info(f"⏳ {asset}: Whale wall blocks ${V4_LIMIT_PRICE} (UP={up_wall:.0f}, DOWN={down_wall:.0f})")
                continue

            # Filter 2: Any momentum at all
            if not up_has_momentum and not down_has_momentum:
                self.logger.info(f"⏳ {asset}: No trading activity detected yet")
                continue

            # Extract dynamic minimum order size from book (Polymarket V2 requirement)
            up_min_size = float(up_book.get("min_order_size", 5)) if isinstance(up_book, dict) else 5.0
            down_min_size = float(down_book.get("min_order_size", 5)) if isinstance(down_book, dict) else 5.0
            asset_min_size = max(up_min_size, down_min_size)

            unplaced.append({
                "asset": asset, 
                "min_size": asset_min_size,
                "up_wall": up_wall,
                "down_wall": down_wall,
                "up_has_momentum": up_has_momentum,
                "down_has_momentum": down_has_momentum
            })

        if unplaced:
            return {
                "action": "place_limits",
                "assets": [item["asset"] for item in unplaced],
                "min_order_sizes": {item["asset"]: item["min_size"] for item in unplaced},
                "asset_data": {item["asset"]: item for item in unplaced},
                "price_up": V4_LIMIT_PRICE,
                "price_down": V4_LIMIT_PRICE,
                "base_order_size": self.settings.order_size,
                "total_cost": V4_COMBINED_TARGET * self.settings.order_size * len(unplaced),
                "total_investment": V4_COMBINED_TARGET * self.settings.order_size * len(unplaced),
                "expected_profit": (1.0 - V4_COMBINED_TARGET) * self.settings.order_size * len(unplaced),
            }

        return None

    def _ensure_allowance(self):
        """Check and set USDC allowance for the exchange contract if needed."""
        try:
            from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType
            params = BalanceAllowanceParams(
                asset_type=AssetType.COLLATERAL,
                signature_type=self.settings.signature_type,
            )
            result = self.client.get_balance_allowance(params)
            if isinstance(result, dict):
                allowance = float(result.get("allowance", "0"))
                if allowance == 0:
                    self.logger.warning("⚠️ USDC allowance is 0 — setting approval...")
                    self.client.update_balance_allowance(params)
                    self.logger.success("✅ USDC allowance approved for exchange")
                    return True
            return True
        except Exception as e:
            self.logger.error(f"❌ Failed to check/set allowance: {e}")
            return False

    def execute_limit_strategy(self, action: dict):
        if action["action"] != "place_limits":
            return

        assets = action["assets"]
        base_size = action.get("base_order_size", self.settings.order_size)
        min_sizes = action.get("min_order_sizes", {})

        self.logger.info("=" * 70)
        self.logger.info("🛡️ V4 ORDER FLOW STRATEGY: TRIGGERED!")
        self.logger.info(f"   Assets: {', '.join(assets)}")
        self.logger.info(f"   Reason: Path clear & Order Flow Momentum Detected!")
        self.logger.info("=" * 70)

        current_balance = self.get_balance()
        if current_balance <= 0 and not self.settings.dry_run:
            self.logger.warning("⚠️ Cannot place orders — balance is 0.")
            return

        # Ensure allowance is set before attempting any orders
        if not self.settings.dry_run:
            if not self._ensure_allowance():
                self.logger.error("❌ Cannot place orders — allowance check failed")
                # Mark ALL assets as placed to prevent spam (retry after cooldown)
                for asset in assets:
                    self.order_state[asset] = {
                        "placed": True,
                        "up_order_id": None,
                        "down_order_id": None,
                        "up_filled": False,
                        "down_filled": False,
                        "_failed_at": time.time(),
                    }
                return

        for asset in assets:
            mkt = self.active_markets.get(asset)
            if not mkt:
                continue

            import math
            asset_min_size = min_sizes.get(asset, 5.0)
            # Rule 1: size >= min_order_size
            # Rule 2: size * price >= 1.00 -> size >= 1.00 / price
            min_value_size = math.ceil(1.00 / V4_LIMIT_PRICE)
            required_size = max(base_size, asset_min_size, min_value_size)

            total_cost = required_size * V4_COMBINED_TARGET
            
            asset_data = action.get("asset_data", {}).get(asset, {})
            up_wall = asset_data.get("up_wall", 0.0)
            down_wall = asset_data.get("down_wall", 0.0)
            up_mom = asset_data.get("up_has_momentum", False)
            down_mom = asset_data.get("down_has_momentum", False)
            trigger_reason = f"Walls: UP={up_wall:.0f} DN={down_wall:.0f} | Mom: UP={'Y' if up_mom else 'N'} DN={'Y' if down_mom else 'N'}"

            self.logger.info(f"\n--- {asset.upper()} ({mkt['slug']}) ---")

            if not self.settings.dry_run and current_balance < total_cost:
                self.logger.warning(f"⚠️ Skipping {asset} — need ${total_cost:.2f}, only have ${current_balance:.2f} left.")
                # Mark as placed so we don't spam
                self.order_state[asset] = {
                    "placed": True,
                    "up_order_id": None,
                    "down_order_id": None,
                    "up_filled": False,
                    "down_filled": False,
                    "_failed_at": time.time(),
                }
                continue

            # Deduct so we don't overspend on the next asset in the loop
            if not self.settings.dry_run:
                current_balance -= total_cost

            if self.settings.dry_run:
                self.logger.info(f"🔸 SIM: Would place UP limit ${V4_LIMIT_PRICE} x {required_size}")
                self.logger.info(f"🔸 SIM: Would place DOWN limit ${V4_LIMIT_PRICE} x {required_size}")
                self.order_state[asset] = {
                    "up_order_id": f"sim-{asset}-up",
                    "down_order_id": f"sim-{asset}-down",
                    "placed": True,
                    "up_filled": False,
                    "down_filled": False,
                }
                self._add_to_history({"order_size": required_size, "total_cost": total_cost}, "success", f"{asset} limits | {trigger_reason}")
                continue

            try:
                self.logger.info(f"Placing {asset} UP limit order...")
                up_res = place_order(
                    self.settings,
                    side="BUY",
                    token_id=mkt["yes_token_id"],
                    price=V4_LIMIT_PRICE,
                    size=required_size,
                    tif="GTC",
                )
                up_id = extract_order_id(up_res)
                self.logger.success(f"✅ {asset} UP Order placed: {up_id}")

                self.logger.info(f"Placing {asset} DOWN limit order...")
                down_res = place_order(
                    self.settings,
                    side="BUY",
                    token_id=mkt["no_token_id"],
                    price=V4_LIMIT_PRICE,
                    size=required_size,
                    tif="GTC",
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
                self.total_invested += total_cost
                self.total_shares_bought += required_size * 2
                self._add_to_history({"order_size": required_size, "total_cost": total_cost}, "success", f"{asset} limits | {trigger_reason}")
                
                self._log_trade(asset, mkt['slug'], up_id, down_id, required_size, trigger_reason)

            except Exception as e:
                self.logger.error(f"❌ Failed to place {asset} limits: {e}")
                # Re-add balance on failure so another asset might use it
                if not self.settings.dry_run:
                    current_balance += total_cost
                
                # CRITICAL: Mark as placed to prevent retry spam at 47 scans/sec
                # The asset will be retried when the market rotates (hourly)
                self.order_state[asset] = {
                    "placed": True,
                    "up_order_id": None,
                    "down_order_id": None,
                    "up_filled": False,
                    "down_filled": False,
                    "_failed_at": time.time(),
                }
                self._add_to_history({"order_size": required_size, "total_cost": 0}, "failed", f"{asset}: {str(e)[:50]}")

    def _add_to_history(self, opportunity: dict, status: str, details: str):
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

    def _log_trade(self, asset: str, slug: str, up_id: str, down_id: str, size: float, trigger_reason: str = ""):
        import os
        os.makedirs("logs", exist_ok=True)
        record = {
            "timestamp": datetime.now().isoformat(),
            "strategy": "V4",
            "asset": asset,
            "slug": slug,
            "up_order_id": up_id,
            "down_order_id": down_id,
            "price": V4_LIMIT_PRICE,
            "size": size,
            "combined_cost": V4_COMBINED_TARGET * size,
            "dry_run": self.settings.dry_run,
            "trigger_data": trigger_reason,
        }
        try:
            with open("logs/live_trade_log.jsonl", "a") as f:
                f.write(json.dumps(record) + "\n")
        except Exception:
            pass

    def check_arbitrage(self, **kwargs) -> Optional[dict]:
        return None

    def execute_arbitrage(self, opportunity: dict):
        pass

    def show_current_positions(self):
        try:
            all_tokens = []
            for mkt in self.active_markets.values():
                all_tokens.extend([mkt["yes_token_id"], mkt["no_token_id"]])
            if not all_tokens:
                return
            positions = get_positions(self.settings, all_tokens)
            self.logger.info("-" * 70)
            self.logger.info("📊 V4 POSITIONS:")
            for asset, mkt in self.active_markets.items():
                up_shares = positions.get(mkt["yes_token_id"], {}).get("size", 0)
                down_shares = positions.get(mkt["no_token_id"], {}).get("size", 0)
                self.logger.info(f"   {asset.upper()}: Up={up_shares:.2f} | Down={down_shares:.2f}")
            self.logger.info("-" * 70)
        except Exception as e:
            self.logger.error(f"Error fetching positions: {e}")
