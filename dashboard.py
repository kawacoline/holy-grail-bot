"""
Polymarket Strategy Bot — Client-centric dashboard.

Architecture:
  - Each client gets its own self-contained panel
  - Panel layout adapts to the client's strategy (V1 Arb, V3 Limit, etc.)
  - Main loop is continuous — no single strategy controls the lifecycle
  - Session reports are logged to file, not printed over the dashboard
"""

import os
import sys
import time
import asyncio
import json
from datetime import datetime

# Add src to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# LOAD ENV VARS EARLY (Essential for USE_PROXY check)
from dotenv import load_dotenv
load_dotenv()

# SETUP PROXY (Must be done before importing httpx/clob_client)
try:
    from src.proxy_config import get_proxy
    proxy_url = get_proxy()
    if proxy_url:
        os.environ["HTTP_PROXY"] = proxy_url
        os.environ["HTTPS_PROXY"] = proxy_url
        print(f"✅ Using Proxy: {proxy_url.split('@')[1]}")
    else:
        print("⚠️ No proxy configured in src.proxy_config")
except ImportError:
    print("⚠️ Could not load src.proxy_config")

# IMPORTANT: Apply Cloudflare bypass BEFORE importing clob client
import cloudflare_bypass  # noqa: F401

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text
from rich import box

from src.config import load_settings
from src.btc_15m_arb_bot import Btc15mArbBot, get_active_btc_15m_slug
from src.v3_limit_bot import V3LimitBot
from src.v4_orderflow_bot import V4OrderFlowBot
from src.logging_config import setup_logging
from src.session_logger import log_session_to_file, print_session_summary

from loguru import logger
setup_logging()

console = Console()


# ═══════════════════════════════════════════════════════════════════════════
#  Address helper
# ═══════════════════════════════════════════════════════════════════════════

_address_cache = {}

def _derive_address(private_key):
    """Derive wallet address from private key (cached)."""
    if not private_key:
        return None
    if private_key in _address_cache:
        return _address_cache[private_key]
    try:
        from eth_account import Account
        addr = Account.from_key(private_key).address
        _address_cache[private_key] = addr
        return addr
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════
#  Per-Client Panel Builder
# ═══════════════════════════════════════════════════════════════════════════

def _make_client_panel(b, index, v1_prices=None):
    """
    Build a self-contained panel for a single client.
    The panel adapts its layout based on the client's strategy.
    """
    addr = _derive_address(b.settings.private_key)
    label = f"C{index+1} ({addr[:6]}...{addr[-4:]})" if addr else f"C{index+1}"
    mode = "🔸 SIM" if b.settings.dry_run else "🟢 LIVE"
    scan_count = getattr(b, 'scan_count', 0)

    if b.settings.strategy == 4:
        return _make_v4_panel(b, label, mode, scan_count)
    elif b.settings.strategy == 3:
        return _make_v3_panel(b, label, mode, scan_count)
    else:
        return _make_v1_panel(b, label, mode, scan_count, v1_prices)


def _make_v1_panel(b, label, mode, scan_count, prices):
    """Build panel for a V1 (BTC 15m Arb) client."""
    prices = prices or {}
    time_left = b.get_time_remaining()
    market = b.market_slug if hasattr(b, 'market_slug') else '--'

    price_up = prices.get("up")
    price_down = prices.get("down")
    size_up = prices.get("size_up") or 0
    size_down = prices.get("size_down") or 0
    safe_up = price_up if price_up is not None else 1.0
    safe_down = price_down if price_down is not None else 1.0
    total = safe_up + safe_down
    threshold = b.settings.target_pair_cost
    profit = max(0, 1.0 - total)
    profit_pct = (profit / total * 100) if total > 0 else 0

    if total > 0 and total <= threshold:
        total_style = "bold green"
    elif total > 0 and total <= threshold + 0.01:
        total_style = "yellow"
    else:
        total_style = "white"

    grid = Table.grid(padding=(0, 0))
    grid.add_column()

    # Row 1: Balance + Order Size
    info = Table(show_header=False, box=None, padding=(0, 1))
    info.add_column("L", style="bold")
    info.add_column("V")
    info.add_column("L2", style="bold")
    info.add_column("V2")
    info.add_row("Balance", f"${b.get_balance():.2f}", "Order Size", str(b.settings.order_size))
    info.add_row("Market", market, "Time Left", f"[bold]{time_left}[/bold]")
    grid.add_row(info)

    # Row 2: Order book inline
    book = Table(box=box.SIMPLE_HEAVY, padding=(0, 1))
    book.add_column("▲ UP", justify="center", style="green")
    book.add_column("▼ DOWN", justify="center", style="red")
    book.add_column("Total", justify="center")
    book.add_column("Threshold", justify="center")
    book.add_column("Profit", justify="center")
    book.add_row(
        f"${price_up:.4f} ({size_up:,.0f})" if price_up is not None else "--",
        f"${price_down:.4f} ({size_down:,.0f})" if price_down is not None else "--",
        f"[{total_style}]${total:.4f}[/{total_style}]",
        f"${threshold:.3f}",
        f"${profit:.4f} ({profit_pct:.2f}%)",
    )
    grid.add_row(book)

    # Row 3: Stats inline
    stats = Table(show_header=False, box=None, padding=(0, 1))
    stats.add_column("L", style="dim")
    stats.add_column("V")
    stats.add_column("L2", style="dim")
    stats.add_column("V2")
    stats.add_column("L3", style="dim")
    stats.add_column("V3")
    stats.add_row(
        "Scans", f"{scan_count:,}",
        "Opportunities", f"[green]{b.opportunities_found}[/green]",
        "Trades", f"[cyan]{b.trades_executed}[/cyan]",
    )
    grid.add_row(stats)

    # Row 4: Recent trades (last 3)
    grid.add_row(_make_trade_history(b, max_rows=3))

    # Closed state
    if time_left == "CLOSED":
        style = "yellow"
        title_extra = " ─── ⏳ CLOSED"
    else:
        style = "cyan"
        title_extra = ""

    return Panel(grid, title=f"{label} — V1 Arb ─── {mode}{title_extra}", box=box.ROUNDED, style=style)


def _make_v3_panel(b, label, mode, scan_count):
    """Build panel for a V3 (1h Multi-Asset Limit) client."""
    time_left = b.get_time_remaining()

    grid = Table.grid(padding=(0, 0))
    grid.add_column()

    # Row 1: Balance + Time
    info = Table(show_header=False, box=None, padding=(0, 1))
    info.add_column("L", style="bold")
    info.add_column("V")
    info.add_column("L2", style="bold")
    info.add_column("V2")
    placed = sum(1 for s in b.order_state.values() if s.get('placed'))
    info.add_row("Balance", f"${b.get_balance():.2f}", "Order Size", str(b.settings.order_size))
    info.add_row("Time Left", f"[bold]{time_left}[/bold]", "Orders", f"{placed}/{len(b.assets)} assets")
    grid.add_row(info)

    # Row 2: Per-asset market table
    mkt_table = Table(box=box.SIMPLE_HEAVY, padding=(0, 1))
    mkt_table.add_column("Asset", style="bold")
    mkt_table.add_column("Slug", style="dim")
    mkt_table.add_column("UP", justify="center")
    mkt_table.add_column("DOWN", justify="center")
    mkt_table.add_column("Status", justify="center")

    for asset in b.assets:
        mkt = b.active_markets.get(asset)
        state = b.order_state.get(asset, {})

        if mkt:
            slug_short = mkt.get("slug", "?")
            # Trim to last 30 chars for readability
            if len(slug_short) > 30:
                slug_short = "..." + slug_short[-27:]

            if state.get("placed"):
                up_label = "[green]✅ $0.45[/green]"
                down_label = "[green]✅ $0.45[/green]"
                status = "[green]ACTIVE[/green]"
            else:
                up_label = "[yellow]pending[/yellow]"
                down_label = "[yellow]pending[/yellow]"
                status = "[yellow]PENDING[/yellow]"
        else:
            slug_short = "[dim]not found[/dim]"
            up_label = "[dim]—[/dim]"
            down_label = "[dim]—[/dim]"
            status = "[red]NO MKT[/red]"

        mkt_table.add_row(asset.upper(), slug_short, up_label, down_label, status)

    grid.add_row(mkt_table)

    # Row 3: Stats
    stats = Table(show_header=False, box=None, padding=(0, 1))
    stats.add_column("L", style="dim")
    stats.add_column("V")
    stats.add_column("L2", style="dim")
    stats.add_column("V2")
    stats.add_row(
        "Scans", f"{scan_count:,}",
        "Trades", f"[cyan]{b.trades_executed}[/cyan]",
    )
    grid.add_row(stats)

    # Row 4: Recent trades (last 3)
    grid.add_row(_make_trade_history(b, max_rows=3))

    # Closed state
    if time_left == "CLOSED":
        style = "yellow"
        title_extra = " ─── ⏳ REDISCOVERING"
    else:
        style = "magenta"
        title_extra = ""

    return Panel(grid, title=f"{label} — V3 Limit ─── {mode}{title_extra}", box=box.ROUNDED, style=style)


def _make_v4_panel(b, label, mode, scan_count):
    """Build panel for a V4 (Order Flow Multi-Asset) client."""
    time_left = b.get_time_remaining()

    grid = Table.grid(padding=(0, 0))
    grid.add_column()

    # Row 1: Balance + Time
    info = Table(show_header=False, box=None, padding=(0, 1))
    info.add_column("L", style="bold")
    info.add_column("V")
    info.add_column("L2", style="bold")
    info.add_column("V2")
    placed = sum(1 for s in b.order_state.values() if s.get('placed'))
    info.add_row("Balance", f"${b.get_balance():.2f}", "Order Size", str(b.settings.order_size))
    info.add_row("Time Left", f"[bold]{time_left}[/bold]", "Orders", f"{placed}/{len(b.assets)} assets")
    grid.add_row(info)

    # Row 2: Per-asset market table
    mkt_table = Table(box=box.SIMPLE_HEAVY, padding=(0, 1))
    mkt_table.add_column("Asset", style="bold")
    mkt_table.add_column("Slug", style="dim")
    mkt_table.add_column("Status", justify="center")

    for asset in b.assets:
        mkt = b.active_markets.get(asset)
        state = b.order_state.get(asset, {})

        if mkt:
            slug_short = mkt.get("slug", "?")
            if len(slug_short) > 30:
                slug_short = "..." + slug_short[-27:]

            if state.get("placed"):
                status = "[green]TRIGGERED[/green]"
            else:
                status = "[yellow]WAITING FOR MMTM/DEPTH[/yellow]"
        else:
            slug_short = "[dim]not found[/dim]"
            status = "[red]NO MKT[/red]"

        mkt_table.add_row(asset.upper(), slug_short, status)

    grid.add_row(mkt_table)

    # Row 3: Stats
    stats = Table(show_header=False, box=None, padding=(0, 1))
    stats.add_column("L", style="dim")
    stats.add_column("V")
    stats.add_column("L2", style="dim")
    stats.add_column("V2")
    stats.add_row(
        "Scans", f"{scan_count:,}",
        "Trades", f"[cyan]{b.trades_executed}[/cyan]",
    )
    grid.add_row(stats)

    # Row 4: Recent trades (last 3)
    grid.add_row(_make_trade_history(b, max_rows=3))

    if time_left == "CLOSED":
        style = "yellow"
        title_extra = " ─── ⏳ REDISCOVERING"
    else:
        style = "green"
        title_extra = ""

    return Panel(grid, title=f"{label} — V4 OrderFlow ─── {mode}{title_extra}", box=box.ROUNDED, style=style)


def _make_trade_history(b, max_rows=3):
    """Build a compact trade history table for a single client."""
    history = getattr(b, "order_history", None)
    if not history:
        return Text("  📜 No trades yet", style="dim")

    ht = Table(box=box.SIMPLE, padding=(0, 1), expand=True)
    ht.add_column("Time", style="dim", width=8)
    ht.add_column("Status", justify="center", width=8)
    ht.add_column("Size", justify="right", width=5)
    ht.add_column("Cost", justify="right", width=8)
    ht.add_column("Details", ratio=1, no_wrap=True)

    for record in reversed(history[-max_rows:]):
        sc = "green" if record['status'] == 'success' else ("red" if record['status'] == 'failed' else "yellow")
        ht.add_row(
            record['time'],
            f"[{sc}]{record['status'].upper()}[/{sc}]",
            str(record['size']),
            record['cost'],
            record['details'][:55],
        )
    return ht


# ═══════════════════════════════════════════════════════════════════════════
#  Main Dashboard Assembler
# ═══════════════════════════════════════════════════════════════════════════

def make_dashboard(bots, last_error=None, v1_prices=None):
    """
    Assemble the full dashboard from per-client panels.
    Each client gets its own panel — no shared strategy sections.
    """
    # Header
    header = Panel(
        Text("POLYMARKET STRATEGY BOT (24/7)", justify="center", style="bold cyan"),
        box=box.DOUBLE, style="cyan",
    )

    # Per-client panels
    client_panels = []
    for i, b in enumerate(bots):
        try:
            panel = _make_client_panel(b, i, v1_prices=v1_prices)
            client_panels.append(panel)
        except Exception as e:
            client_panels.append(Panel(
                Text(f"Error rendering: {e}", style="red"),
                title=f"C{i+1} — ERROR", box=box.ROUNDED, style="red",
            ))

    # Aggregate footer
    total_balance = sum(b.get_balance() for b in bots)
    total_trades = sum(b.trades_executed for b in bots)

    footer_info = f"Aggregate: ${total_balance:.2f}  │  Trades: {total_trades}"
    footer_time = f"Last Update: {datetime.now().strftime('%H:%M:%S')}  │  Press Ctrl+C to stop"
    if last_error:
        footer_time = f"[red]Error: {last_error[:60]}[/red]"

    # Assemble
    output = Table.grid(padding=1)
    output.add_column()
    output.add_row(header)
    for panel in client_panels:
        output.add_row(panel)
    output.add_row(Text(footer_info, style="bold", justify="center"))
    output.add_row(Text(footer_time, style="dim", justify="center"))

    return Panel(output, box=box.DOUBLE, style="cyan", padding=(0, 1))


# ═══════════════════════════════════════════════════════════════════════════
#  Main Loop — Strategy-Agnostic, Continuous
# ═══════════════════════════════════════════════════════════════════════════

async def run_dashboard():
    """
    Main dashboard loop.
    
    Runs continuously — each client manages its own lifecycle:
      - V1 clients: loop discovers new 15m markets, scans for arb
      - V3 clients: re-discover 1h markets when they close
    
    Session reports are logged to file, never take over the screen.
    """
    settings_list = load_settings()
    console.print(f"[cyan]Starting Polymarket Strategy Bot for {len(settings_list)} client(s)...[/cyan]")

    # ── Startup redemption ─────────────────────────────────────────────
    try:
        from src.redeem import auto_redeem
        console.print("[cyan]🔄 Checking for unredeemed tokens from previous sessions...[/cyan]")
        total_gained = 0
        total_redeemed = 0
        for s in settings_list:
            if s.private_key:
                redeem_result = auto_redeem(
                    s.private_key,
                    relayer_api_key=s.relayer_api_key,
                    relayer_api_key_address=s.relayer_api_key_address,
                )
                total_gained += redeem_result.get("gained_total", 0) or redeem_result.get("gained_usdce", 0)
                total_redeemed += redeem_result.get("redeemed", 0)

        if total_redeemed > 0:
            console.print(f"[green]💰 Redeemed {total_redeemed} market(s), gained ${total_gained:.4f} (pUSD + USDCe) total[/green]")
        else:
            console.print("[dim]   No unredeemed tokens found on startup.[/dim]")
    except Exception as e:
        console.print(f"[yellow]⚠️ Startup auto-redeem error: {e}[/yellow]")

    # ── Initialize bots ────────────────────────────────────────────────
    bots = []

    for s in settings_list:
        if s.strategy == 4:
            try:
                v4_bot = V4OrderFlowBot(s)
                v4_bot.discover_markets()
                v4_bot.scan_count = 0
                bots.append(v4_bot)
                console.print(f"[green]✅ V4 Order Flow Bot initialized ({len(v4_bot.active_markets)} markets)[/green]")
            except Exception as e:
                console.print(f"[red]❌ V4 bot init failed: {e}[/red]")
        elif s.strategy == 3:
            try:
                v3_bot = V3LimitBot(s)
                v3_bot.discover_markets()
                v3_bot.scan_count = 0
                bots.append(v3_bot)
                console.print(f"[green]✅ V3 Limit Bot initialized ({len(v3_bot.active_markets)} markets)[/green]")
            except Exception as e:
                console.print(f"[red]❌ V3 bot init failed: {e}[/red]")
        else:
            # V1 bots — placeholder until market is found
            # We create the bot once a market is discovered
            try:
                slug = get_active_btc_15m_slug()
                v1_bot = Btc15mArbBot(s)
                v1_bot.scan_count = 0
                v1_bot._session_start = datetime.now()
                bots.append(v1_bot)
                console.print(f"[green]✅ V1 Arb Bot initialized ({v1_bot.market_slug})[/green]")
            except Exception as e:
                console.print(f"[yellow]⏳ V1 bot waiting for market: {e}[/yellow]")
                # Create a placeholder — will be replaced when market found
                try:
                    v1_bot = Btc15mArbBot.__new__(Btc15mArbBot)
                    v1_bot.settings = s
                    v1_bot.market_slug = "searching..."
                    v1_bot.market_end_timestamp = None
                    v1_bot.scan_count = 0
                    v1_bot.trades_executed = 0
                    v1_bot.opportunities_found = 0
                    v1_bot.unwinds_executed = 0
                    v1_bot.total_invested = 0.0
                    v1_bot.total_shares_bought = 0.0
                    v1_bot.order_history = []
                    v1_bot.cached_balance = None
                    v1_bot.sim_balance = s.sim_balance if s.sim_balance > 0 else 100.0
                    v1_bot.sim_start_balance = v1_bot.sim_balance
                    v1_bot._needs_market = True
                    v1_bot._session_start = datetime.now()
                    bots.append(v1_bot)
                except Exception:
                    pass

    if not bots:
        console.print("[red]❌ No bots could be initialized. Exiting.[/red]")
        return

    last_error = None
    v1_prices = {}

    # ── Continuous main loop ───────────────────────────────────────────
    with Live(make_dashboard(bots, v1_prices=v1_prices), refresh_per_second=4, console=console) as live:
        while True:
            try:
                for i, b in enumerate(bots):
                    b.scan_count = getattr(b, 'scan_count', 0) + 1

                    if b.settings.strategy in (3, 4):
                        # ── V3 / V4 Strategies ──────────────────────────
                        time_left = b.get_time_remaining()

                        if time_left == "CLOSED":
                            # Market closed → log session, rediscover
                            _log_session_quiet(b, i)
                            b.active_markets.clear()
                            b.order_state.clear()
                            b.discover_markets()
                            b.scan_count = 0
                        else:
                            action = b.check_limit_strategy()
                            if action:
                                b.execute_limit_strategy(action)
                    else:
                        # ── V1 Arb Strategy ────────────────────────────
                        needs_market = getattr(b, '_needs_market', False)
                        time_left = b.get_time_remaining() if not needs_market else "CLOSED"

                        if time_left == "CLOSED" or needs_market:
                            # Cooldown: only search for a new market every 60 seconds
                            last_search = getattr(b, '_last_market_search', 0)
                            if time.time() - last_search < 60 and needs_market:
                                continue  # Skip this iteration, wait for cooldown

                            if not needs_market:
                                _log_session_quiet(b, i)
                                _auto_redeem_quiet(b)
                            try:
                                b._last_market_search = time.time()
                                slug = get_active_btc_15m_slug()
                                new_bot = Btc15mArbBot(b.settings)
                                new_bot.scan_count = 0
                                new_bot._session_start = datetime.now()
                                new_bot._needs_market = False
                                # Preserve order history from previous session
                                new_bot.order_history = getattr(b, 'order_history', [])
                                bots[i] = new_bot
                                b = new_bot
                            except Exception:
                                b._needs_market = True
                                # Don't spam — just let it show "searching..." in the panel
                        else:
                            # Active market — scan for arb
                            try:
                                up_book = b.get_order_book(b.yes_token_id)
                                down_book = b.get_order_book(b.no_token_id)
                                v1_prices = {
                                    "up": up_book.get("best_ask"),
                                    "down": down_book.get("best_ask"),
                                    "size_up": up_book.get("ask_size", 0),
                                    "size_down": down_book.get("ask_size", 0),
                                }
                                opportunity = b.check_arbitrage(up_book=up_book, down_book=down_book)
                                if opportunity:
                                    b.execute_arbitrage(opportunity)
                            except Exception as e:
                                last_error = str(e)

                # Update dashboard
                live.update(make_dashboard(bots, last_error=last_error, v1_prices=v1_prices))
                last_error = None

                # Dump state for web dashboard
                _dump_state(bots, v1_prices)

            except Exception as e:
                last_error = str(e)
                live.update(make_dashboard(bots, last_error=last_error, v1_prices=v1_prices))

            await asyncio.sleep(0.1)


# ═══════════════════════════════════════════════════════════════════════════
#  Helpers (non-disruptive session logging, redemption, state dump)
# ═══════════════════════════════════════════════════════════════════════════

def _log_session_quiet(b, index):
    """Log session to file without printing to console (non-disruptive)."""
    try:
        session_start = getattr(b, '_session_start', datetime.now())
        session_end = datetime.now()
        duration_secs = (session_end - session_start).total_seconds()
        minutes = int(duration_secs // 60)
        seconds = int(duration_secs % 60)

        addr = _derive_address(b.settings.private_key)
        client_label = f"Client {index+1} ({addr[:8]}...)" if addr else f"Client {index+1}"

        # Fetch position P&L
        pnl_data = None
        try:
            from src.positions import summarize_positions, get_wallet_address
            wallet = b.settings.profile_wallet if hasattr(b.settings, 'profile_wallet') else None
            if not wallet and b.settings.private_key:
                wallet = get_wallet_address(b.settings.private_key)
            if wallet:
                pnl_data = summarize_positions(wallet)
        except Exception:
            pass

        session_data = {
            "client_index": index + 1,
            "client_label": client_label,
            "market_slug": b.market_slug if hasattr(b, 'market_slug') else "v3-multi-asset",
            "mode": "SIMULATION" if b.settings.dry_run else "REAL TRADING",
            "session_start": session_start.strftime("%H:%M:%S"),
            "session_end": session_end.strftime("%H:%M:%S"),
            "duration": f"{minutes}m {seconds}s",
            "scan_count": getattr(b, 'scan_count', 0),
            "opportunities": b.opportunities_found,
            "trades": b.trades_executed,
            "unwinds": getattr(b, 'unwinds_executed', 0),
            "error_count": 0,
            "best_total": None,
            "worst_total": None,
            "threshold": b.settings.target_pair_cost,
            "balance_start": b.get_balance(),
            "balance_end": b.get_balance(),
            "order_size": b.settings.order_size,
            "total_invested": getattr(b, 'total_invested', 0),
            "total_shares_bought": getattr(b, 'total_shares_bought', 0),
            "order_history": b.order_history if hasattr(b, "order_history") else [],
            "position_pnl": pnl_data,
        }

        log_session_to_file(session_data, client_index=index + 1)
    except Exception:
        pass  # Never crash the main loop for logging


def _auto_redeem_quiet(b):
    """Auto-redeem after market close, non-disruptive."""
    try:
        from src.redeem import auto_redeem
        if b.settings.private_key and not b.settings.dry_run:
            auto_redeem(
                b.settings.private_key,
                relayer_api_key=b.settings.relayer_api_key,
                relayer_api_key_address=b.settings.relayer_api_key_address,
            )
    except Exception:
        pass


def _dump_state(bots, v1_prices):
    """Dump state to JSON for web dashboard — non-disruptive."""
    try:
        os.makedirs("data", exist_ok=True)
        clients = []
        for i, b in enumerate(bots):
            addr = _derive_address(b.settings.private_key)

            # Build recent trade history (last 5)
            history = []
            for record in (getattr(b, 'order_history', None) or [])[-5:]:
                history.append({
                    "time": record.get("time", ""),
                    "status": record.get("status", ""),
                    "size": record.get("size", ""),
                    "cost": record.get("cost", ""),
                    "details": record.get("details", "")[:80],
                })

            clients.append({
                "index": i + 1,
                "address": addr[:10] if addr else None,
                "full_address": addr if addr else None,
                "strategy": b.settings.strategy,
                "mode": "SIMULATION" if b.settings.dry_run else "LIVE",
                "balance": b.get_balance(),
                "order_size": b.settings.order_size,
                "trades": b.trades_executed,
                "opportunities": getattr(b, 'opportunities_found', 0),
                "scan_count": getattr(b, 'scan_count', 0),
                "time_remaining": b.get_time_remaining(),
                "market_slug": getattr(b, 'market_slug', None),
                "recent_trades": history,
            })

        state_data = {
            "timestamp": datetime.now().isoformat(),
            "clients": clients,
            "v1_prices": v1_prices,
            "aggregate_balance": sum(b.get_balance() for b in bots),
        }
        with open("data/dashboard_state.json", "w") as f:
            json.dump(state_data, f)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        asyncio.run(run_dashboard())
    except KeyboardInterrupt:
        console.print("\n[cyan]Bot stopped. Goodbye![/cyan]\n")
