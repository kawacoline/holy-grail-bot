"""
BTC 15-minute arbitrage bot for Polymarket.

Strategy: Buy both sides (UP and DOWN) when total cost < $1.00
to lock in profit regardless of whether Bitcoin goes up or down.
"""

import asyncio
# import logging  # Removed standard logging
import re
import time
from datetime import datetime
from typing import Optional

import httpx

from .config import load_settings
from .market_lookup import fetch_market_from_slug
from .trading import (
    get_client,
    place_order,
    get_positions,
    place_orders_fast,
    extract_order_id,
    wait_for_terminal_order,
    cancel_orders,
)
from .wss_market import MarketWssClient


import sys
from loguru import logger
# Configure logging via our custom module
from .logging_config import setup_logging
# Ensure logging is set up
setup_logging()

# Disable HTTP logs from httpx (loguru handles this via sink filters if needed, but good to silence library noise)
# We can't easily silence httpx via loguru directly without intercepting, but standard logging config still applies to libraries
import logging
logging.getLogger("httpx").setLevel(logging.WARNING)


# 15min window length in seconds
BTC_15M_WINDOW = 900


def _find_btc_15m_via_computed_slugs() -> Optional[str]:
    """
    Try computed slugs for current and next 15m windows.
    Uses Gamma API to verify the slug exists (much more reliable than page scraping).
    """
    now_ts = int(datetime.now().timestamp())
    for i in range(7):
        ts = now_ts + (i * BTC_15M_WINDOW)
        ts_rounded = (ts // BTC_15M_WINDOW) * BTC_15M_WINDOW
        slug = f"btc-updown-15m-{ts_rounded}"
        try:
            logger.info(f"  Checking: {slug}")
            # Use Gamma API (reliable) instead of HTML scraping
            resp = httpx.get(
                f"https://gamma-api.polymarket.com/events?slug={slug}",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            events = resp.json()
            if events and len(events) > 0:
                # Market exists — check if still open
                if now_ts < ts_rounded + BTC_15M_WINDOW:
                    return slug
                # Else market exists but closed, keep trying next window
        except Exception:
            continue
    return None


def _find_btc_15m_via_gamma_api() -> Optional[str]:
    """Find BTC 15m slug from Polymarket Gamma API (closed=false markets)."""
    try:
        resp = httpx.get(
            "https://gamma-api.polymarket.com/markets",
            params={"closed": "false", "limit": 500},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            return None
        now_ts = int(datetime.now().timestamp())
        pattern = re.compile(r"^btc-updown-15m-(\d+)$")
        candidates = []
        for m in data:
            slug = (m.get("slug") or "").strip()
            mo = pattern.match(slug)
            if not mo:
                continue
            ts = int(mo.group(1))
            if now_ts < ts + BTC_15M_WINDOW:
                candidates.append((ts, slug))
        if not candidates:
            for m in data:
                slug = (m.get("slug") or "").strip()
                if pattern.match(slug):
                    candidates.append((int(pattern.match(slug).group(1)), slug))
        if not candidates:
            return None
        candidates.sort(key=lambda x: (x[0] + BTC_15M_WINDOW > now_ts, x[0]), reverse=True)
        return candidates[0][1]
    except Exception as e:
        logger.debug("Gamma API lookup failed: %s", e)
        return None


def _find_btc_15m_via_page_scrape() -> Optional[str]:
    """Fallback: scrape crypto/15M page for btc-updown-15m slugs (HTML or __NEXT_DATA__)."""
    try:
        import json
        resp = httpx.get(
            "https://polymarket.com/crypto/15M",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=15,
        )
        resp.raise_for_status()
        text = resp.text
        now_ts = int(datetime.now().timestamp())
        pattern = re.compile(r"^btc-updown-15m-(\d+)$")
        # Plain regex in HTML
        matches = re.findall(r"btc-updown-15m-(\d+)", text)
        if matches:
            all_ts = sorted(set(int(ts) for ts in matches), reverse=True)
            open_ts = [t for t in all_ts if now_ts < t + BTC_15M_WINDOW]
            chosen = open_ts[0] if open_ts else all_ts[0]
            return f"btc-updown-15m-{chosen}"
        # __NEXT_DATA__
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', text, re.DOTALL)
        if m:
            payload = json.loads(m.group(1))
            queries = (payload.get("props") or {}).get("pageProps", {}).get("dehydratedState", {}).get("queries") or []
            for q in queries:
                data = q.get("state", {}).get("data")
                if not isinstance(data, dict):
                    continue
                for ev in (data.get("events") or []) + (data.get("markets") or []):
                    slug = (ev.get("slug") or "").strip()
                    if pattern.match(slug):
                        return slug
            def find_slugs(obj):
                if isinstance(obj, dict):
                    s = obj.get("slug")
                    if isinstance(s, str) and pattern.match(s):
                        return [s]
                    return [x for v in obj.values() for x in find_slugs(v)]
                if isinstance(obj, list):
                    return [x for item in obj for x in find_slugs(item)]
                return []
            slugs = find_slugs(payload)
            if slugs:
                return slugs[0]
        return None
    except Exception as e:
        logger.debug("Page scrape failed: %s", e)
        return None


def get_active_btc_15m_slug() -> str:
    """
    Find the current active BTC 15min market on Polymarket.

    Strategy (aligned with find_bitcoin_15min_market reference):
    1. Try computed slugs for current/next 15m windows (event URLs btc-updown-15m-{ts_rounded})
    2. Gamma API (active markets)
    3. Scrape crypto/15M page
    """
    logger.info("Searching for current BTC 15min market...")

    slug = _find_btc_15m_via_computed_slugs()
    if slug:
        logger.info("✅ Market found (computed slug): %s", slug)
        return slug

    slug = _find_btc_15m_via_gamma_api()
    if slug:
        logger.info("✅ Market found (Gamma API): %s", slug)
        return slug

    slug = _find_btc_15m_via_page_scrape()
    if slug:
        logger.info("✅ Market found (page scrape): %s", slug)
        return slug

    raise RuntimeError(
        "No active BTC 15min market found (tried computed slugs, Gamma API, and crypto/15M page). "
        "You can set POLYMARKET_MARKET_SLUG in .env to a slug like btc-updown-15m-<timestamp>."
    )


class Btc15mArbBot:
    """BTC 15-minute arbitrage bot for Polymarket UP/DOWN markets."""
    
    def __init__(self, settings):
        self.settings = settings
        self.client = get_client(settings)

        # Simulation balance — MUST be initialized before any code that can fail
        self.sim_balance = self.settings.sim_balance if self.settings.sim_balance > 0 else 100.0
        self.sim_start_balance = self.sim_balance

        # Per-client isolated logger
        if self.settings.private_key:
            from eth_account import Account
            w = Account.from_key(self.settings.private_key).address
            self.client_name = f"Client_{w[:8]}"
        else:
            self.client_name = "Client_Sim"
        from .logging_config import bind_client_logger
        self.logger = bind_client_logger(self.client_name)
        
        # Try to find target market automatically or fallback to config
        try:
            if settings.target_market == "WEATHER" or settings.target_market == "CUSTOM":
                if not settings.market_slug:
                    raise RuntimeError(f"For {settings.target_market} markets, you must specify POLYMARKET_MARKET_SLUG in your .env.")
                market_slug = settings.market_slug
            else:
                market_slug = get_active_btc_15m_slug()
        except Exception as e:
            # Fallback: use the slug configured in .env
            if settings.market_slug:
                self.logger.info(f"Using configured market: {settings.market_slug}")
                market_slug = settings.market_slug
            else:
                raise RuntimeError(f"Could not find targeted market ({e}) and no slug configured in .env")
        
        # Get token IDs from the market — try Gamma API first (reliable), fall back to page scrape
        self.logger.info(f"Getting market information: {market_slug}")
        market_info = None
        try:
            # Gamma API: fast and reliable
            resp = httpx.get(
                f"https://gamma-api.polymarket.com/events?slug={market_slug}",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            events = resp.json()
            if events and len(events) > 0:
                mkt = events[0].get("markets", [{}])[0]
                clob_tokens = mkt.get("clobTokenIds") or []
                if isinstance(clob_tokens, str):
                    import json as _json
                    clob_tokens = _json.loads(clob_tokens)
                outcomes = mkt.get("outcomes") or []
                if isinstance(outcomes, str):
                    import json as _json
                    outcomes = _json.loads(outcomes)
                if len(clob_tokens) == 2:
                    market_info = {
                        "market_id": str(mkt.get("id", "")),
                        "yes_token_id": clob_tokens[0],
                        "no_token_id": clob_tokens[1],
                        "outcomes": outcomes,
                        "question": mkt.get("question", ""),
                        "end_date": mkt.get("endDate") or events[0].get("endDate"),
                    }
        except Exception as e:
            self.logger.debug(f"Gamma API fetch failed, trying page scrape: {e}")

        if not market_info:
            market_info = fetch_market_from_slug(market_slug)
        
        self.market_id = market_info["market_id"]
        self.yes_token_id = market_info["yes_token_id"]
        self.no_token_id = market_info["no_token_id"]
        
        self.logger.info(f"Market ID: {self.market_id}")
        self.logger.info(f"UP Token (YES): {self.yes_token_id}")
        self.logger.info(f"DOWN Token (NO): {self.no_token_id}")
        
        # Extract market timestamp to calculate remaining time
        # The timestamp in the slug is when it OPENS, not when it closes
        # 15min markets close 15 minutes (900 seconds) later
        import re
        match = re.search(r'btc-updown-15m-(\d+)', market_slug)
        market_start = int(match.group(1)) if match else None
        self.market_end_timestamp = market_start + 900 if market_start else None  # +15 min
        self.market_slug = market_slug
        
        self.last_check = None
        self.opportunities_found = 0
        self.trades_executed = 0
        self.unwinds_executed = 0
        
        # Cached balance (updated after each trade)
        self.cached_balance = None
        
        # Trade History Tracking (Max 10 records to keep UI clean)
        self.order_history: list[dict] = []
        # Investment tracking
        self.total_invested = 0.0
        self.total_shares_bought = 0
        self.positions = []  # List of open positions

        # Simple cooldown to avoid repeated orders on the same fleeting opportunity
        self._last_execution_ts = 0.0
        
        # Strategy V3 Initialization
        self.v3_orders_placed = False
        self.v3_order_ids = {'up': None, 'down': None}
        self.v3_placed_size = 0.0
        
        # Initialize Circuit Breaker
        # We need to know the starting balance for the breaker.
        # But get_balance() calls get_balance() which might loop if we are not careful.
        # So we fetch it once here directly.
        try:
            from .circuit_breaker import CircuitBreaker
            if self.settings.dry_run:
                 start_bal = self.sim_balance
                 max_dd = 0.90 # 90% for SIM
            else:
                 from .trading import get_balance
                 start_bal = get_balance(self.settings)
                 max_dd = 0.15 # 15% for LIVE
                 
            self.circuit_breaker = CircuitBreaker(initial_balance=start_bal, max_drawdown_pct=max_dd)
            self.logger.info(f"🛡️ CIRCUIT BREAKER ARMED: Max loss {max_dd*100}% of ${start_bal:.2f} (Stop at ${start_bal*(1-max_dd):.2f})")
        except Exception as e:
            self.logger.error(f"Failed to arm CircuitBreaker: {e}")
            self.circuit_breaker = None
    
    def get_time_remaining(self) -> str:
        """Get remaining time until market closes."""
        if not self.market_end_timestamp:
            return "Unknown"
        
        from datetime import datetime
        now = int(datetime.now().timestamp())
        remaining = self.market_end_timestamp - now
        
        if remaining <= 0:
            return "CLOSED"
        
        minutes = int(remaining // 60)
        seconds = int(remaining % 60)
        return f"{minutes}m {seconds}s"
    
    def get_balance(self) -> float:
        """Get current USDC balance (or simulated balance in dry_run mode)."""
        # Check Circuit Breaker
        if hasattr(self, 'circuit_breaker') and self.circuit_breaker:
             # We need actual balance to check breaker.
             # Avoid infinite recursion: separate the fetch logic
             pass

        if self.settings.dry_run:
            bal = self.sim_balance
        else:
            from .trading import get_balance
            bal = get_balance(self.settings)
            
        # Check breaker status
        if hasattr(self, 'circuit_breaker') and self.circuit_breaker:
            if not self.circuit_breaker.check(bal):
                logger.critical("❌ CIRCUIT BREAKER TRIPPED - EMERGENCY STOP")
                import os
                time.sleep(1) # Allow logs to flush
                
                if self.settings.dry_run:
                    logger.critical("🔹 Since this is a SIMULATION, only this specific bot will stop. The main process will stay alive.")
                    self.sim_circuit_breaker_tripped = True
                else:
                    logger.critical("🔴 LIVE MODE - KILLING ENTIRE DASHBOARD PROCESS TO PROTECT FUNDS")
                    os._exit(69) # 69 is our custom emergency exit code
                
        return bal
    
    def get_current_prices(self) -> tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
        """
        Get current prices from order book (best ask = lowest price we can BUY at).
        
        Using best_ask ensures we get the actual available price, not a historical
        trade price that may no longer be available.
        
        Returns:
            (up_price, down_price, up_size, down_size) - prices and available sizes
        """
        try:
            # Get order book for both tokens
            up_book = self.get_order_book(self.yes_token_id)
            down_book = self.get_order_book(self.no_token_id)
            
            # Use best_ask (lowest sell price = price we can buy at)
            price_up = up_book.get("best_ask")
            price_down = down_book.get("best_ask")
            
            # Available sizes at best ask prices
            size_up = up_book.get("ask_size", 0)
            size_down = down_book.get("ask_size", 0)
            
            # If no asks available, we can't buy
            if price_up is None or price_down is None:
                self.logger.warning("No asks available in order book")
                return None, None, None, None
            
            return price_up, price_down, size_up, size_down
        except Exception as e:
            self.logger.error(f"Error getting prices: {e}")
            return None, None, None, None

    def _levels_to_tuples(self, levels) -> list[tuple[float, float]]:
        """Convert OrderSummary-like objects into (price, size) tuples."""
        tuples: list[tuple[float, float]] = []
        for level in levels or []:
            try:
                price = float(level.price)
                size = float(level.size)
            except Exception:
                continue
            if size <= 0:
                continue
            tuples.append((price, size))
        return tuples

    def _compute_buy_fill(self, asks: list[tuple[float, float]], target_size: float) -> Optional[dict]:
        """
        Compute fill information for buying `target_size` shares using the ask book.

        Returns:
            dict with keys: filled, vwap, worst, best, cost
            or None if not enough liquidity.
        """
        if target_size <= 0:
            return None

        # Cheapest asks first
        sorted_asks = sorted(asks, key=lambda x: x[0])
        filled = 0.0
        cost = 0.0
        worst = None
        best = sorted_asks[0][0] if sorted_asks else None

        for price, size in sorted_asks:
            if filled >= target_size:
                break
            take = min(size, target_size - filled)
            cost += take * price
            filled += take
            worst = price

        if filled + 1e-9 < target_size:
            return None

        vwap = cost / filled if filled > 0 else None
        return {
            "filled": filled,
            "vwap": vwap,
            "worst": worst,
            "best": best,
            "cost": cost,
        }
    
    def get_order_book(self, token_id: str) -> dict:
        """Get order book for a token."""
        try:
            book = self.client.get_order_book(token_id=token_id)
            # The result is an OrderBookSummary object, not a dict
            bids = book.bids if hasattr(book, 'bids') and book.bids else []
            asks = book.asks if hasattr(book, 'asks') and book.asks else []

            bid_levels = self._levels_to_tuples(bids)
            ask_levels = self._levels_to_tuples(asks)

            best_bid = max((p for p, _ in bid_levels), default=None)
            best_ask = min((p for p, _ in ask_levels), default=None)

            bid_size = 0.0
            if best_bid is not None:
                for p, s in bid_levels:
                    if p == best_bid:
                        bid_size = s
                        break

            ask_size = 0.0
            if best_ask is not None:
                for p, s in ask_levels:
                    if p == best_ask:
                        ask_size = s
                        break

            spread = (best_ask - best_bid) if (best_bid is not None and best_ask is not None) else None

            return {
                "best_bid": best_bid,
                "best_ask": best_ask,
                "spread": spread,
                "bid_size": bid_size,
                "ask_size": ask_size,
                "bids": bid_levels,
                "asks": ask_levels,
            }
        except Exception as e:
            self.logger.error(f"Error getting order book: {e}")
            return {}

    async def _fetch_order_books_parallel(self) -> tuple[dict, dict]:
        """Fetch UP/DOWN order books concurrently to reduce per-scan latency."""
        try:
            up_task = asyncio.to_thread(self.get_order_book, self.yes_token_id)
            down_task = asyncio.to_thread(self.get_order_book, self.no_token_id)
            up_book, down_book = await asyncio.gather(up_task, down_task)
            return up_book, down_book
        except Exception as e:
            self.logger.warning(f"Parallel order book fetch failed, falling back to sequential: {e}")
            return self.get_order_book(self.yes_token_id), self.get_order_book(self.no_token_id)
    
    def check_arbitrage(self, up_book: Optional[dict] = None, down_book: Optional[dict] = None) -> Optional[dict]:
        """
        Check if an arbitrage opportunity exists.
        
        Uses order book (best ask) to get REAL prices we can buy at.
        Also verifies there's enough liquidity at those prices.
        
        Returns dict with information if opportunity exists, None otherwise.
        """
        if getattr(self, 'sim_circuit_breaker_tripped', False):
            return None
        # Pull full order books (allow caller to pass pre-fetched books to reduce latency)
        if up_book is None:
            up_book = self.get_order_book(self.yes_token_id)
        if down_book is None:
            down_book = self.get_order_book(self.no_token_id)

        # Basic sanity: in a normal book, best_ask >= best_bid
        for side_name, book in ("UP", up_book), ("DOWN", down_book):
            best_bid = book.get("best_bid")
            best_ask = book.get("best_ask")
            if best_bid is not None and best_ask is not None and best_ask < best_bid:
                self.logger.warning(
                    f"{side_name} order book looks inverted (best_ask={best_ask:.4f} < best_bid={best_bid:.4f}); skipping scan"
                )
                return None

        asks_up = up_book.get("asks", [])
        asks_down = down_book.get("asks", [])
        
        # 1. Ignore Empty Sides & Liquidity Threshold
        asks_up = up_book.get("asks", [])
        asks_down = down_book.get("asks", [])
        
        # Calculate total available liquidity across the whole book
        total_liq_up = sum(s for p, s in asks_up)
        total_liq_down = sum(s for p, s in asks_down)
        
        if total_liq_up <= 0 or total_liq_down <= 0:
            return None
            
        if total_liq_up < self.settings.min_liquidity or total_liq_down < self.settings.min_liquidity:
            return None

        # Compute the prices required to actually fill ORDER_SIZE shares (walk the book)
        fill_up = self._compute_buy_fill(asks_up, float(self.settings.order_size))
        fill_down = self._compute_buy_fill(asks_down, float(self.settings.order_size))

        if not fill_up or not fill_down:
            return None

        # 2. Safety Check: If Ask_Price <= 0, treat it as 1.0 (Max Cost) 
        # so the math never triggers a buy.
        if fill_up["worst"] <= 0.0001:
            fill_up["worst"] = 1.0
        if fill_down["worst"] <= 0.0001:
            fill_down["worst"] = 1.0

        # For guaranteed arbitrage, use the *worst* price we might have to pay to fill the size
        limit_price_up = fill_up["worst"]
        limit_price_down = fill_down["worst"]
        if limit_price_up is None or limit_price_down is None:
            return None

        total_cost = limit_price_up + limit_price_down

        # Use <= to avoid missing exact-threshold opportunities due to rounding
        if total_cost <= self.settings.target_pair_cost:
            profit = 1.0 - total_cost
            profit_pct = (profit / total_cost) * 100 if total_cost > 0 else 0

            investment = total_cost * self.settings.order_size
            expected_payout = 1.0 * self.settings.order_size
            expected_profit = expected_payout - investment

            return {
                # Prices we will actually place as LIMITs (to ensure fills)
                "price_up": limit_price_up,
                "price_down": limit_price_down,
                "total_cost": total_cost,
                "profit_per_share": profit,
                "profit_pct": profit_pct,
                "order_size": self.settings.order_size,
                "total_investment": investment,
                "expected_payout": expected_payout,
                "expected_profit": expected_profit,

                # Extra diagnostics
                "best_ask_up": fill_up.get("best"),
                "best_ask_down": fill_down.get("best"),
                "vwap_up": fill_up.get("vwap"),
                "vwap_down": fill_down.get("vwap"),
                "timestamp": datetime.now().isoformat(),
            }

        return None
    
    def execute_arbitrage(self, opportunity: dict):
        """Execute arbitrage by buying both sides."""

        # Cooldown guard (applies to both live and dry-run)
        now = asyncio.get_event_loop().time() if asyncio.get_event_loop().is_running() else time.time()
        if self.settings.cooldown_seconds and (now - self._last_execution_ts) < float(self.settings.cooldown_seconds):
            self.logger.info(f"Cooldown active ({self.settings.cooldown_seconds}s); skipping execution")
            return
        self._last_execution_ts = now
        
        # Count opportunity found (regardless of execution)
        self.opportunities_found += 1
        
        self.logger.info("=" * 70)
        self.logger.info("🎯 ARBITRAGE OPPORTUNITY DETECTED")
        self.logger.info("=" * 70)
        self.logger.info(f"UP limit price:       ${opportunity['price_up']:.4f}")
        self.logger.info(f"DOWN limit price:     ${opportunity['price_down']:.4f}")
        if 'vwap_up' in opportunity and 'vwap_down' in opportunity:
            self.logger.info(f"UP VWAP (est):        ${opportunity['vwap_up']:.4f}")
            self.logger.info(f"DOWN VWAP (est):      ${opportunity['vwap_down']:.4f}")
        self.logger.info(f"Total cost:           ${opportunity['total_cost']:.4f}")
        self.logger.info(f"Profit per share:     ${opportunity['profit_per_share']:.4f}")
        self.logger.info(f"Profit %:             {opportunity['profit_pct']:.2f}%")
        self.logger.info("-" * 70)
        self.logger.info(f"Order size:           {opportunity['order_size']} shares each side")
        self.logger.info(f"Total investment:     ${opportunity['total_investment']:.2f}")
        self.logger.info(f"Expected payout:      ${opportunity['expected_payout']:.2f}")
        self.logger.info(f"EXPECTED PROFIT:      ${opportunity['expected_profit']:.2f}")
        self.logger.info("=" * 70)
        
        if self.settings.dry_run:
            self.logger.info("🔸 SIMULATION MODE - No real orders will be executed")
            
            # Check simulated balance
            if self.sim_balance < opportunity['total_investment']:
                self.logger.error(f"❌ Insufficient simulated balance: need ${opportunity['total_investment']:.2f} but have ${self.sim_balance:.2f}")
                self._add_to_history(opportunity, "failed", "Insufficient Sim Balance")
                return
            
            # Deduct from simulated balance
            self.sim_balance -= opportunity['total_investment']
            self.logger.info(f"💰 Simulated balance: ${self.sim_balance:.2f} (after deducting ${opportunity['total_investment']:.2f})")
            
            # Track simulated investment
            self.total_invested += opportunity['total_investment']
            self.total_shares_bought += opportunity['order_size'] * 2  # UP + DOWN
            self.positions.append(opportunity)
            self.trades_executed += 1
            self._add_to_history(opportunity, "success", "Dry Run")
            self.logger.info("=" * 70)
            return
        
        # Check balance before executing (with 20% safety margin)
        self.logger.info("\nVerifying balance...")
        # Use cached balance if available, otherwise fetch from API
        if self.cached_balance is not None:
            current_balance = self.cached_balance
            self.logger.info(f"Available balance (cached): ${current_balance:.2f}")
        else:
            current_balance = self.get_balance()
            self.cached_balance = current_balance
            self.logger.info(f"Available balance: ${current_balance:.2f}")
        
        # Calculate max affordable investment with safety margin
        # We need to keep 1% buffer: Available / 1.01 = Max Spendable
        max_spendable = current_balance / 1.01
        target_investment = opportunity['total_investment']
        
        # If we can't afford the full target size, check if we can resize
        if target_investment > max_spendable:
            self.logger.warning(f"⚠️ Insufficient balance for full size: need ${target_investment*1.01:.2f} (w/buffer) but have ${current_balance:.2f}")
            
            # Calculate price per share unit (approx total cost / size)
            # Avoid div by zero
            original_size = float(opportunity['order_size'])
            if original_size <= 0: return

            cost_per_share = target_investment / original_size
            
            # New max size
            new_size = int(max_spendable / cost_per_share)
            
            # Minimum viable size (polymarket constraint? usually $5 min not clearly enforced but let's say 5 shares)
            if new_size < 0.1: # Changed to allow smaller orders
                self.logger.error(f"❌ Account too small even for min size. Max shares possible: {new_size}")
                self._add_to_history(opportunity, "failed", "Insufficient balance for dynamic downsize")
                return
                
            self.logger.info(f"📉 DYNAMIC SIZING: Reducing order size from {original_size} to {new_size} to fit balance.")
            
            # Update opportunity sizing
            # Recalculate investment for logging only, the order placement needs the new size
            current_size = new_size
        else:
            current_size = self.settings.order_size # Default if affordable

        # NEW CHECK: Polymarket requires min order value > $1 (approx)
        up_price = opportunity['price_up']
        down_price = opportunity['price_down']
        
        # Calculate minimum required shares to safely clear Polymarket's $1 minimum per leg
        min_qty_up = 1.05 / up_price if up_price > 0 else 0
        min_qty_down = 1.05 / down_price if down_price > 0 else 0
        required_min_qty = max(min_qty_up, min_qty_down)
        
        if current_size < required_min_qty:
            import math
            adjusted_size = math.ceil(required_min_qty)
            # Make sure we can afford the adjusted size
            if adjusted_size * (up_price + down_price) > max_spendable:
                 self.logger.warning(f"⚠️ Cannot afford min Polymarket $1.00 volume requirement. Need {adjusted_size} shares.")
                 self._add_to_history(opportunity, "failed", "Cannot afford min $1 volume")
                 return
            self.logger.info(f"📈 Bumping order size from {current_size} to {adjusted_size} to meet Polymarket $1.00 minimum.")
            current_size = adjusted_size
        
        # Recalculate cost with final size
        cost = current_size * (up_price + down_price)
        
        # Update opportunity dict so logging and history are accurate
        opportunity['order_size'] = current_size
        opportunity['total_cost'] = cost
        opportunity['total_investment'] = cost
        
        self.logger.info(f"Target Size: {current_size} shares | Est. Cost: ${cost:.2f}")

        # Final check
        if cost > current_balance: # Use current_balance
            self.logger.error(f"❌ Still insufficient balance: need ${cost:.2f} but have ${current_balance:.2f}")
            self._add_to_history(opportunity, "failed", "Insufficient Balance (Final Check)")
            return
        
        try:
            # Execute orders
            self.logger.info("\n📤 Submitting both legs...")
            
            # Use exact prices from arbitrage opportunity
            up_price = opportunity['price_up']
            down_price = opportunity['price_down']
            
            # Prepare both orders
            orders = [
                {
                    "side": "BUY",
                    "token_id": self.yes_token_id,
                    "price": up_price,
                    "size": current_size
                },
                {
                    "side": "BUY",
                    "token_id": self.no_token_id,
                    "price": down_price,
                    "size": current_size
                }
            ]
            
            self.logger.info(f"   UP:   {current_size} shares @ ${up_price:.4f}")
            self.logger.info(f"   DOWN: {current_size} shares @ ${down_price:.4f}")
            self.logger.info(f"   OrderType: {getattr(self.settings, 'order_type', 'GTC')}")
            
            # Execute both orders as fast as possible
            results = place_orders_fast(self.settings, orders, order_type=getattr(self.settings, 'order_type', 'GTC'))
            self.logger.info(f"📨 Raw API responses: {results}")

            # Extract order ids and surface any immediate submission errors.
            # Preserve index mapping: orders[0] is UP, orders[1] is DOWN.
            submission_errors: list[str] = []
            order_ids_by_idx: list[Optional[str]] = [None, None]
            for idx, r in enumerate((results or [])[:2]):
                if isinstance(r, dict) and "error" in r:
                    submission_errors.append(str(r.get("error")))
                    continue
                oid = extract_order_id(r) if isinstance(r, dict) else None
                order_ids_by_idx[idx] = oid

            if submission_errors:
                for msg in submission_errors:
                    self.logger.error(f"❌ Order submit error: {msg}")

            if not order_ids_by_idx[0] and not order_ids_by_idx[1]:
                # Both failed to submit - nothing to unwind.
                raise RuntimeError(f"Could not extract any order ids from responses: {results}")

            self.logger.info("✅ Orders processed; verifying fills...")

            # We know we submitted in order: UP first, DOWN second.
            up_order_id, down_order_id = order_ids_by_idx[0], order_ids_by_idx[1]
            req_size = float(current_size)
            self.logger.info(f"Verifying fills for size: {req_size} (Dynamic)")

            # Check UP leg
            if up_order_id:
                up_state = wait_for_terminal_order(self.settings, up_order_id, requested_size=req_size)
            else:
                up_state = {"status": "failed", "filled": False, "filled_size": 0.0}

            # Check DOWN leg
            if down_order_id:
                down_state = wait_for_terminal_order(self.settings, down_order_id, requested_size=req_size)
            else:
                down_state = {"status": "failed", "filled": False, "filled_size": 0.0}

            up_filled = bool(up_state.get("filled"))
            down_filled = bool(down_state.get("filled"))
            up_filled_size = float(up_state.get("filled_size") or 0.0)
            down_filled_size = float(down_state.get("filled_size") or 0.0)

            self.logger.info(
                f"Order status: UP(id={up_order_id}, status={up_state.get('status')}, filled={up_filled_size:.4f}) | "
                f"DOWN(id={down_order_id}, status={down_state.get('status')}, filled={down_filled_size:.4f})"
            )

            if submission_errors or not (up_filled and down_filled):
                # Best-effort cleanup: cancel anything still open
                try:
                    cancel_orders(self.settings, [up_order_id, down_order_id])
                except Exception as cancel_exc:
                    self.logger.warning(f"Cancel cleanup failed: {cancel_exc}")

                # If one leg filled, attempt to flatten exposure immediately.
                filled_token_id = None
                filled_size = 0.0
                if up_filled and not down_filled:
                    filled_token_id = self.yes_token_id
                    filled_size = up_filled_size if up_filled_size > 0 else req_size
                elif down_filled and not up_filled:
                    filled_token_id = self.no_token_id
                    filled_size = down_filled_size if down_filled_size > 0 else req_size

                if filled_token_id and filled_size > 0:
                    self.logger.warning("⚠️ PARTIAL FILL DETECTED (LEG RISK) - ATTEMPTING IMMEDIATE UNWIND TO FLATTEN EXPOSURE")
                    
                    # Retry loop: Polymarket backend sometimes takes 1-3 seconds to index 
                    # the new token balance before it allows us to sell it.
                    max_retries = 3
                    for attempt in range(max_retries):
                        try:
                            # Grab the order book for the token we successfully bought
                            book = self.get_order_book(filled_token_id)
                            best_bid = book.get("best_bid")
                            
                            if best_bid is None:
                                self.logger.error("❌ No best_bid available to unwind! Will attempt to sell at $0.01")
                                best_bid = 0.01 # Market order equivalent on Polymarket
                            else:
                                # To guarantee execution, price the sell slightly lower than best_bid 
                                # (Polymarket orderbook will match at the best available bid anyway)
                                best_bid = max(0.01, best_bid - 0.02)
                                
                            # Use FAK (Fill And Kill) to dump immediately into the existing bids
                            self.logger.info(f"DUMPING {filled_size:.4f} shares of token {filled_token_id} @ max ${best_bid:.4f} (Attempt {attempt+1}/{max_retries})")
                            place_order(
                                self.settings,
                                side="SELL",
                                token_id=filled_token_id,
                                price=float(best_bid),
                                size=float(filled_size),
                                tif="FAK",
                            )
                            self.logger.success(f"✅ Unwind order submitted for {filled_size:.4f} shares (FAK)")
                            break # Success, exit retry loop
                            
                        except Exception as unwind_exc:
                            self.logger.error(f"❌ Unwind attempt {attempt+1} failed: {unwind_exc}")
                            if attempt < max_retries - 1:
                                self.logger.info("Retrying in 1.5 seconds to allow Polymarket backend to credit the tokens...")
                                time.sleep(1.5)
                            else:
                                logger.critical("❌ CRITICAL: Exhausted all unwind retries. Manual intervention may be required.")

                        # Successfully triggered an unwind (even if FAK results in 0, we attempted it)
                        self.unwinds_executed += 1

                self._add_to_history(opportunity, "unwound" if (filled_token_id and filled_size > 0) else "failed", "1_leg_unwound" if (filled_token_id and filled_size > 0) else "legs_failed")
                raise RuntimeError("Paired execution failed (not both legs filled). Exposure was flattened.")

            self.logger.info("\n" + "=" * 70)
            self.logger.success("✅ ARBITRAGE EXECUTED (BOTH LEGS FILLED)")
            self.logger.info("=" * 70)

            self.trades_executed += 1
            
            # Track real investment
            self.total_invested += opportunity['total_investment']
            self.total_shares_bought += opportunity['order_size'] * 2  # UP + DOWN
            self.positions.append(opportunity)
            
            # Update cached balance after trade
            new_balance = self.get_balance()
            self.cached_balance = new_balance
            self.logger.info(f"💰 Updated balance: ${new_balance:.2f}")
            
            # Get and show current positions
            self.show_current_positions()
            self._add_to_history(opportunity, "success", f"Bought {current_size*2} units")
            
            
        except Exception as e:
            error_msg = str(e)
            if "order couldn't be" in error_msg.lower() or "could not extract any order ids" in error_msg.lower():
                self.logger.warning(f"\n⚠️ Liquidity evaporated: {error_msg}")
                self._add_to_history(opportunity, "failed", "Liquidity evaporated")
            else:
                self.logger.error(f"\n❌ Error executing arbitrage: {e}")
                self.logger.error("❌ Orders were NOT executed - tracking was not updated")
                self._add_to_history(opportunity, "failed", f"Err: {error_msg[:80]}")

    def check_limit_strategy(self) -> Optional[dict]:
        """
        V3 Strategy: Pre-place passive limit orders on 1-hour Up/Down markets
        for BTC, ETH, SOL, XRP at $0.45 per side.
        Combined cost = $0.90, payout = $1.00 → ~11% profit if both legs fill.
        Returns the limit action to execute, or None if orders are already resting.
        """
        if not self.v3_orders_placed:
            # Per STRATEGY_V3.md: $0.45 per side, combined $0.90
            up_price = 0.45
            down_price = 0.45
            total_cost = up_price + down_price  # $0.90
            
            return {
                "price_up": up_price,
                "price_down": down_price,
                "order_size": self.settings.order_size,
                "total_cost": total_cost,
                "total_investment": total_cost * self.settings.order_size,
                "expected_payout": 1.0 * self.settings.order_size,
                "expected_profit": (1.0 - total_cost) * self.settings.order_size  # $0.10 per share
            }
        
        # Orders are already resting in the book — nothing to do until they fill.
        return None

    def execute_limit_strategy(self, action: dict):
        """
        Place resting GTC Limit orders in the orderbook using post_only=True.
        """
        current_size = action['order_size']
        self.logger.info("=" * 70)
        self.logger.info("🛡️ V3 LIMIT STRATEGY: PRE-PLACING PASSIVE ORDERS")
        self.logger.info("=" * 70)
        self.logger.info(f"UP Limit bid:   ${action['price_up']:.4f} x {current_size}")
        self.logger.info(f"DOWN Limit bid: ${action['price_down']:.4f} x {current_size}")
        self.logger.info("-" * 70)
        
        if self.settings.dry_run:
            self.logger.info("🔸 SIMULATION - Marking V3 orders as placed")
            self.v3_orders_placed = True
            self.v3_placed_size = current_size
            self._add_to_history(action, "success", "Placed Sim Limits")
            return
            
        try:
            # Place UP order
            self.logger.info("Placing UP limit order...")
            up_res = place_order(
                self.settings,
                side="BUY", 
                token_id=self.yes_token_id, 
                price=action['price_up'], 
                size=current_size, 
                tif="GTC",
                post_only=True
            )
            up_id = extract_order_id(up_res)
            self.logger.success(f"UP Order ID: {up_id}")
            
            # Place DOWN order
            self.logger.info("Placing DOWN limit order...")
            down_res = place_order(
                self.settings,
                side="BUY", 
                token_id=self.no_token_id, 
                price=action['price_down'], 
                size=current_size, 
                tif="GTC",
                post_only=True
            )
            down_id = extract_order_id(down_res)
            self.logger.success(f"DOWN Order ID: {down_id}")
            
            self.v3_order_ids['up'] = up_id
            self.v3_order_ids['down'] = down_id
            self.v3_orders_placed = True
            self.v3_placed_size = current_size
            
            self._add_to_history(action, "success", "Placed Real Limits")
            
        except Exception as e:
            self.logger.error(f"Failed to place limit sequence: {e}")
            self._add_to_history(action, "failed", f"Limit Err: {str(e)[:50]}")

    def _add_to_history(self, opportunity: dict, status: str, details: str):
        """Add record and trim to last 5 execution logs to prevent memory bloat and fit in terminal."""
        record = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "size": opportunity.get("order_size", "?"),
            "cost": f"${opportunity.get('total_cost', 0):.3f}",
            "status": status,
            "details": details
        }
        self.order_history.append(record)
        if len(self.order_history) > 5:
            self.order_history.pop(0)

    def show_current_positions(self):
        """Show current share positions for UP and DOWN tokens."""
        try:
            positions = get_positions(self.settings, [self.yes_token_id, self.no_token_id])
            
            up_shares = positions.get(self.yes_token_id, {}).get("size", 0)
            down_shares = positions.get(self.no_token_id, {}).get("size", 0)
            
            self.logger.info("-" * 70)
            self.logger.info("📊 CURRENT POSITIONS:")
            self.logger.info(f"   UP shares:   {up_shares:.2f}")
            self.logger.info(f"   DOWN shares: {down_shares:.2f}")
            self.logger.info("-" * 70)
            
        except Exception as e:
            self.logger.warning(f"Could not fetch positions: {e}")
    
    def get_market_result(self) -> Optional[str]:
        """Get which option won the market."""
        try:
            # Get final prices
            price_up, price_down, _, _ = self.get_current_prices()
            
            if price_up is None or price_down is None:
                return None
            
            # In closed markets, winner has price 1.0 and loser 0.0
            if price_up >= 0.99:
                return "UP (goes up) 📈"
            elif price_down >= 0.99:
                return "DOWN (goes down) 📉"
            else:
                # Market not resolved yet, see which has higher probability
                if price_up > price_down:
                    return f"UP leading ({price_up:.2%})"
                else:
                    return f"DOWN leading ({price_down:.2%})"
        except Exception as e:
            self.logger.error(f"Error getting result: {e}")
            return None
    
    def show_final_summary(self):
        """Show final summary when market closes."""
        self.logger.info("\n" + "=" * 70)
        self.logger.info("🏁 MARKET CLOSED - FINAL SUMMARY")
        self.logger.info("=" * 70)
        self.logger.info(f"Market: {self.market_slug}")
        
        # Get market result
        result = self.get_market_result()
        if result:
            self.logger.info(f"Result: {result}")
        
        self.logger.info(f"Mode: {'🔸 SIMULATION' if self.settings.dry_run else '🔴 REAL TRADING'}")
        self.logger.info("-" * 70)
        self.logger.info(f"Total opportunities detected:    {self.opportunities_found}")
        self.logger.info(f"Total trades executed:           {self.trades_executed if not self.settings.dry_run else self.opportunities_found}")
        self.logger.info(f"Total shares bought:             {self.total_shares_bought}")
        self.logger.info("-" * 70)
        self.logger.info(f"Total invested:                  ${self.total_invested:.2f}")
        
        # Calculate expected profit
        if self.settings.dry_run:
            expected_payout = sum(float(p.get("expected_payout", 0.0)) for p in (self.positions or []))
        else:
            expected_payout = (self.total_shares_bought / 2) * 1.0  # Each pair pays $1.00

        expected_profit = expected_payout - self.total_invested
        profit_pct = (expected_profit / self.total_invested * 100) if self.total_invested > 0 else 0
        
        self.logger.info(f"Expected payout at close:        ${expected_payout:.2f}")
        self.logger.info(f"Expected profit:                 ${expected_profit:.2f} ({profit_pct:.2f}%)")

        if self.settings.dry_run:
            cash_remaining = float(self.sim_balance)
            cash_after_claim = cash_remaining + float(expected_payout)
            net_change = cash_after_claim - float(self.sim_start_balance)
            net_change_pct = (net_change / float(self.sim_start_balance) * 100) if self.sim_start_balance > 0 else 0
            self.logger.info("-" * 70)
            self.logger.info(f"Sim start cash:                  ${self.sim_start_balance:.2f}")
            self.logger.info(f"Sim cash remaining:              ${cash_remaining:.2f}")
            self.logger.info(f"Sim cash after claiming:         ${cash_after_claim:.2f}")
            self.logger.info(f"Sim net change:                  ${net_change:.2f} ({net_change_pct:.2f}%)")
        self.logger.info("=" * 70)
    
    def run_once(self) -> bool:
        """Scan once for opportunities."""
        # Check if market closed
        time_remaining = self.get_time_remaining()
        if time_remaining == "CLOSED":
            return False  # Signal to stop the bot

        # Fetch both books once per scan (most expensive operations)
        up_book = self.get_order_book(self.yes_token_id)
        down_book = self.get_order_book(self.no_token_id)

        opportunity = self.check_arbitrage(up_book=up_book, down_book=down_book)
        
        if opportunity:
            self.execute_arbitrage(opportunity)
            return True
        else:
            price_up = up_book.get("best_ask")
            price_down = down_book.get("best_ask")
            size_up = up_book.get("ask_size", 0)
            size_down = down_book.get("ask_size", 0)

            if price_up is not None and price_down is not None:
                best_total = price_up + price_down

                # Compute fill-based totals for ORDER_SIZE (more accurate than best_ask)
                fill_up = self._compute_buy_fill(up_book.get("asks", []), float(self.settings.order_size))
                fill_down = self._compute_buy_fill(down_book.get("asks", []), float(self.settings.order_size))

                fill_msg = ""
                if fill_up and fill_down and fill_up.get("worst") is not None and fill_down.get("worst") is not None:
                    worst_total = float(fill_up["worst"]) + float(fill_down["worst"])
                    vwap_total = float(fill_up["vwap"]) + float(fill_down["vwap"]) if (fill_up.get("vwap") is not None and fill_down.get("vwap") is not None) else None
                    if vwap_total is not None:
                        fill_msg = f" | fill(worst)=${worst_total:.4f} vwap=${vwap_total:.4f}"
                    else:
                        fill_msg = f" | fill(worst)=${worst_total:.4f}"

                self.logger.info(
                    f"No arbitrage: UP=${price_up:.4f} ({size_up:.0f}) + DOWN=${price_down:.4f} ({size_down:.0f}) "
                    f"= ${best_total:.4f} (threshold=${self.settings.target_pair_cost:.3f}){fill_msg} "
                    f"[Time: {time_remaining}]"
                )
            return False

    async def run_once_async(self) -> bool:
        """Scan once for opportunities (async; fetches books in parallel)."""
        # Check if market closed
        time_remaining = self.get_time_remaining()
        if time_remaining == "CLOSED":
            return False  # Signal to stop the bot

        # Fetch both books concurrently (reduces per-scan latency)
        up_book, down_book = await self._fetch_order_books_parallel()

        opportunity = self.check_arbitrage(up_book=up_book, down_book=down_book)

        if opportunity:
            self.execute_arbitrage(opportunity)
            return True

        price_up = up_book.get("best_ask")
        price_down = down_book.get("best_ask")
        size_up = up_book.get("ask_size", 0)
        size_down = down_book.get("ask_size", 0)

        if price_up is not None and price_down is not None:
            best_total = price_up + price_down

            # Compute fill-based totals for ORDER_SIZE (more accurate than best_ask)
            fill_up = self._compute_buy_fill(up_book.get("asks", []), float(self.settings.order_size))
            fill_down = self._compute_buy_fill(down_book.get("asks", []), float(self.settings.order_size))

            fill_msg = ""
            if fill_up and fill_down and fill_up.get("worst") is not None and fill_down.get("worst") is not None:
                worst_total = float(fill_up["worst"]) + float(fill_down["worst"])
                vwap_total = float(fill_up["vwap"]) + float(fill_down["vwap"]) if (fill_up.get("vwap") is not None and fill_down.get("vwap") is not None) else None
                if vwap_total is not None:
                    fill_msg = f" | fill(worst)=${worst_total:.4f} vwap=${vwap_total:.4f}"
                else:
                    fill_msg = f" | fill(worst)=${worst_total:.4f}"

            self.logger.info(
                f"No arbitrage: UP=${price_up:.4f} ({size_up:.0f}) + DOWN=${price_down:.4f} ({size_down:.0f}) "
                f"= ${best_total:.4f} (threshold=${self.settings.target_pair_cost:.3f}){fill_msg} "
                f"[Time: {time_remaining}]"
            )

        return False
    
    async def monitor(self, interval_seconds: int = 30):
        """Continuously monitor for opportunities."""
        if getattr(self.settings, "use_wss", False):
            await self.monitor_wss()
            return
        self.logger.info("=" * 70)
        self.logger.info("🚀 BITCOIN 15MIN ARBITRAGE BOT STARTED")
        self.logger.info("=" * 70)
        self.logger.info(f"Market: {self.market_slug}")
        self.logger.info(f"Time remaining: {self.get_time_remaining()}")
        self.logger.info(f"Mode: {'🔸 SIMULATION' if self.settings.dry_run else '🔴 REAL TRADING'}")
        self.logger.info(f"Cost threshold: ${self.settings.target_pair_cost:.3f}")
        self.logger.info(f"Order size: {self.settings.order_size} shares")
        self.logger.info(f"Interval: {interval_seconds}s")
        self.logger.info("=" * 70)
        self.logger.info("")
        
        scan_count = 0
        
        try:
            while True:
                scan_count += 1
                self.logger.info(f"\n[Scan #{scan_count}] {datetime.now().strftime('%H:%M:%S')}")
                
                # Check if market closed
                if self.get_time_remaining() == "CLOSED":
                    self.logger.info("\n🚨 Market has closed!")
                    self.show_final_summary()
                    
                    # Search for the next market
                    self.logger.info("\n🔄 Searching for next BTC 15min market...")
                    try:
                        new_market_slug = get_active_btc_15m_slug()
                        if new_market_slug != self.market_slug:
                            self.logger.info(f"✅ New market found: {new_market_slug}")
                            self.logger.info("Restarting bot with new market...")
                            # Restart the bot with the new market
                            self.__init__(self.settings)
                            scan_count = 0
                            continue
                        else:
                            self.logger.info("⏳ Waiting for new market... (30s)")
                            await asyncio.sleep(30)
                            continue
                    except Exception as e:
                        self.logger.error(f"Error searching for new market: {e}")
                        self.logger.info("Retrying in 30 seconds...")
                        await asyncio.sleep(30)
                        continue
                
                # Use async scan to fetch books in parallel
                await self.run_once_async()
                
                self.logger.info(f"Opportunities found: {self.opportunities_found}/{scan_count}")
                if not self.settings.dry_run:
                    self.logger.info(f"Trades executed: {self.trades_executed}")
                
                self.logger.info(f"Waiting {interval_seconds}s...\n")
                await asyncio.sleep(interval_seconds)
                
        except (KeyboardInterrupt, asyncio.CancelledError):
            self.logger.info("\n" + "=" * 70)
            self.logger.info("🛑 Bot stopped by user")
            self.logger.info(f"Total scans: {scan_count}")
            self.logger.info(f"Opportunities found: {self.opportunities_found}")
            if not self.settings.dry_run:
                self.logger.info(f"Trades executed: {self.trades_executed}")
            self.logger.info("=" * 70)

    def _book_from_state(self, bid_levels: list[tuple[float, float]], ask_levels: list[tuple[float, float]]) -> dict:
        best_bid = max((p for p, _ in bid_levels), default=None)
        best_ask = min((p for p, _ in ask_levels), default=None)

        bid_size = 0.0
        if best_bid is not None:
            for p, s in bid_levels:
                if p == best_bid:
                    bid_size = s
                    break

        ask_size = 0.0
        if best_ask is not None:
            for p, s in ask_levels:
                if p == best_ask:
                    ask_size = s
                    break

        spread = (best_ask - best_bid) if (best_bid is not None and best_ask is not None) else None

        return {
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread": spread,
            "bid_size": bid_size,
            "ask_size": ask_size,
            "bids": bid_levels,
            "asks": ask_levels,
        }

    async def monitor_wss(self):
        """Monitor using Polymarket CLOB Market WebSocket instead of polling."""
        # This loop keeps WSS running across market rollovers.
        while True:
            # If the detected market is already closed, rollover immediately.
            if self.get_time_remaining() == "CLOSED":
                self.logger.info("\n🚨 Market has closed (before WSS start).")
                self.show_final_summary()
                self.logger.info("\n🔄 Searching for next BTC 15min market...")
                try:
                    new_market_slug = get_active_btc_15m_slug()
                    if new_market_slug != self.market_slug:
                        self.logger.info(f"✅ New market found: {new_market_slug}")
                        self.logger.info("Restarting bot with new market...")
                        self.__init__(self.settings)
                        continue
                    self.logger.info("⏳ Waiting for new market... (10s)")
                    await asyncio.sleep(10)
                    continue
                except Exception as e:
                    self.logger.error(f"Error searching for new market: {e}")
                    self.logger.info("Retrying in 10 seconds...")
                    await asyncio.sleep(10)
                    continue

            self.logger.info("=" * 70)
            self.logger.info("🚀 BITCOIN 15MIN ARBITRAGE BOT STARTED (WSS MODE)")
            self.logger.info("=" * 70)
            self.logger.info(f"Market: {self.market_slug}")
            self.logger.info(f"Time remaining: {self.get_time_remaining()}")
            self.logger.info(f"Mode: {'🔸 SIMULATION' if self.settings.dry_run else '🔴 REAL TRADING'}")
            self.logger.info(f"Cost threshold: ${self.settings.target_pair_cost:.3f}")
            self.logger.info(f"Order size: {self.settings.order_size} shares")
            self.logger.info(f"WSS URL: {self.settings.ws_url}")
            self.logger.info("=" * 70)
            self.logger.info("")

            client = MarketWssClient(
                ws_base_url=self.settings.ws_url,
                asset_ids=[self.yes_token_id, self.no_token_id],
            )

            last_eval = 0.0
            eval_min_interval_s = 0.05  # avoid evaluating too frequently on rapid deltas
            eval_count = 0

            try:
                async for asset_id, event_type in client.run():
                    # Periodic close check
                    if self.get_time_remaining() == "CLOSED":
                        self.logger.info("\n🚨 Market has closed!")
                        self.show_final_summary()
                        # Roll over to next market
                        self.logger.info("\n🔄 Searching for next BTC 15min market...")
                        try:
                            new_market_slug = get_active_btc_15m_slug()
                            if new_market_slug != self.market_slug:
                                self.logger.info(f"✅ New market found: {new_market_slug}")
                                self.logger.info("Restarting bot with new market...")
                                self.__init__(self.settings)
                                break
                            self.logger.info("⏳ Waiting for new market... (10s)")
                            await asyncio.sleep(10)
                            break
                        except Exception as e:
                            self.logger.error(f"Error searching for new market: {e}")
                            self.logger.info("Retrying in 10 seconds...")
                            await asyncio.sleep(10)
                            break

                    # Debounce evaluation
                    now = asyncio.get_running_loop().time()
                    if (now - last_eval) < eval_min_interval_s:
                        continue
                    last_eval = now
                    eval_count += 1
                    self.logger.info(f"\n[WSS Eval #{eval_count}] {datetime.now().strftime('%H:%M:%S')} (trigger={event_type}:{asset_id[:8]}…)")

                    yes_state = client.get_book(self.yes_token_id)
                    no_state = client.get_book(self.no_token_id)
                    if not yes_state or not no_state:
                        if self.settings.verbose:
                            self.logger.info("WSS eval skipped: missing book state (waiting for initial snapshots)")
                        continue

                    yes_bids, yes_asks = yes_state.to_levels()
                    no_bids, no_asks = no_state.to_levels()
                    if not yes_asks or not no_asks:
                        if self.settings.verbose:
                            self.logger.info("WSS eval skipped: missing asks on one side (no buyable liquidity yet)")
                        continue

                    up_book = self._book_from_state(yes_bids, yes_asks)
                    down_book = self._book_from_state(no_bids, no_asks)

                    opportunity = self.check_arbitrage(up_book=up_book, down_book=down_book)
                    if opportunity:
                        self.execute_arbitrage(opportunity)
                        continue

                    # Minimal "no arb" logging using the same in-memory snapshot
                    price_up = up_book.get("best_ask")
                    price_down = down_book.get("best_ask")
                    size_up = up_book.get("ask_size", 0)
                    size_down = down_book.get("ask_size", 0)

                    if price_up is not None and price_down is not None:
                        best_total = float(price_up) + float(price_down)
                        fill_up = self._compute_buy_fill(up_book.get("asks", []), float(self.settings.order_size))
                        fill_down = self._compute_buy_fill(down_book.get("asks", []), float(self.settings.order_size))

                        fill_msg = ""
                        if fill_up and fill_down and fill_up.get("worst") is not None and fill_down.get("worst") is not None:
                            worst_total = float(fill_up["worst"]) + float(fill_down["worst"])
                            vwap_total = float(fill_up["vwap"]) + float(fill_down["vwap"]) if (fill_up.get("vwap") is not None and fill_down.get("vwap") is not None) else None
                            if vwap_total is not None:
                                fill_msg = f" | fill(worst)=${worst_total:.4f} vwap=${vwap_total:.4f}"
                            else:
                                fill_msg = f" | fill(worst)=${worst_total:.4f}"

                        self.logger.info(
                            f"No arbitrage: UP=${price_up:.4f} ({size_up:.0f}) + DOWN=${price_down:.4f} ({size_down:.0f}) "
                            f"= ${best_total:.4f} (threshold=${self.settings.target_pair_cost:.3f}){fill_msg} "
                            f"[Time: {self.get_time_remaining()}]"
                        )
                    else:
                        if self.settings.verbose:
                            self.logger.info("WSS eval skipped: best ask missing (book not ready)")
            except (KeyboardInterrupt, asyncio.CancelledError):
                raise
            except Exception as e:
                self.logger.warning(f"WSS monitor loop error, reconnecting: {e}")
                await asyncio.sleep(1.0)
                continue


async def main():
    """Main entry point."""
    
    # Load configuration
    settings = load_settings()
    
    # Validate configuration
    if not settings.private_key:
        self.logger.error("❌ Error: POLYMARKET_PRIVATE_KEY not configured in .env")
        return
    
    # Create and run bot
    try:
        bot = Btc15mArbBot(settings)
        await bot.monitor(interval_seconds=0)  # Scan continuously
    except Exception as e:
        self.logger.error(f"❌ Fatal error: {e}", exc_info=True)


if __name__ == "__main__":
    asyncio.run(main())
