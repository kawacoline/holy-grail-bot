"""
Session Logger — saves per-session stats to per-client log files and
prints a rich summary table to the terminal after each 15m market closes.

Log files live under:
    logs/session_history.jsonl            (legacy / aggregate)
    logs/client_<N>_session_history.jsonl (per-client)
"""

import json
import os
from datetime import datetime

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box


LOGS_DIR = "logs"
SESSION_LOG_FILE = os.path.join(LOGS_DIR, "session_history.jsonl")


def _client_log_path(client_index: int) -> str:
    """Return the per-client log file path."""
    return os.path.join(LOGS_DIR, f"client_{client_index}_session_history.jsonl")


def log_session_to_file(session_data: dict, client_index: int | None = None):
    """
    Append a single session record as a JSON line.
    
    If client_index is provided, also writes to the per-client log file.
    Always writes to the aggregate log file for backward compatibility.
    """
    os.makedirs(LOGS_DIR, exist_ok=True)
    
    # Always write to aggregate
    try:
        with open(SESSION_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(session_data, default=str) + "\n")
    except Exception as e:
        print(f"⚠️ Could not write session log: {e}")
    
    # Also write to per-client log
    if client_index is not None:
        try:
            path = _client_log_path(client_index)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(session_data, default=str) + "\n")
        except Exception as e:
            print(f"⚠️ Could not write client {client_index} session log: {e}")


def print_session_summary(console: Console, data: dict, client_label: str = ""):
    """Print a rich formatted summary after a 15m market closes."""

    # ── Header ──
    title = f"🏁 SESSION REPORT — {client_label}" if client_label else "🏁 SESSION REPORT"
    header = Text(title, style="bold cyan", justify="center")
    console.print()
    console.print(Panel(header, box=box.DOUBLE, style="cyan"))

    # ── Market info ──
    info = Table(show_header=False, box=None, padding=(0, 2))
    info.add_column("Label", style="bold")
    info.add_column("Value")
    info.add_row("Market", data.get("market_slug", "—"))
    info.add_row("Mode", data.get("mode", "—"))
    info.add_row("Duration", data.get("duration", "—"))
    info.add_row("Session", f"{data.get('session_start', '?')}  →  {data.get('session_end', '?')}")
    console.print(info)

    # ── Stats table ──
    stats = Table(title="📊 Session Stats", box=box.ROUNDED, title_style="bold cyan")
    stats.add_column("Metric", style="bold")
    stats.add_column("Value", justify="right")

    stats.add_row("Total Scans", f"{data.get('scan_count', 0):,}")
    stats.add_row("Opportunities Found", f"[green]{data.get('opportunities', 0)}[/green]")
    stats.add_row("Trades Executed", f"[cyan]{data.get('trades', 0)}[/cyan]")
    stats.add_row("Scan Errors", f"[red]{data.get('error_count', 0)}[/red]")
    console.print(stats)

    # ── Price analysis ──
    price_tbl = Table(title="💹 Price Analysis", box=box.ROUNDED, title_style="bold cyan")
    price_tbl.add_column("Metric", style="bold")
    price_tbl.add_column("Value", justify="right")

    best_total = data.get("best_total")
    worst_total = data.get("worst_total")
    threshold = data.get("threshold", 0.99)

    if best_total is not None:
        gap = best_total - threshold
        gap_style = "green" if gap <= 0 else ("yellow" if gap <= 0.02 else "white")
        price_tbl.add_row("Best (lowest) UP+DOWN", f"[{gap_style}]${best_total:.4f}[/{gap_style}]")
        price_tbl.add_row("Gap to Threshold", f"[{gap_style}]{'+' if gap > 0 else ''}{gap:.4f}[/{gap_style}]")
    else:
        price_tbl.add_row("Best (lowest) UP+DOWN", "—")

    if worst_total is not None:
        price_tbl.add_row("Worst (highest) UP+DOWN", f"${worst_total:.4f}")
    price_tbl.add_row("Threshold", f"${threshold:.3f}")
    console.print(price_tbl)

    # ── Balance ──
    bal_tbl = Table(title="💰 Balance", box=box.ROUNDED, title_style="bold cyan")
    bal_tbl.add_column("Metric", style="bold")
    bal_tbl.add_column("Value", justify="right")
    bal_start = data.get("balance_start", 0)
    bal_end = data.get("balance_end", 0)
    change = bal_end - bal_start
    change_style = "green" if change > 0 else ("red" if change < 0 else "white")
    bal_tbl.add_row("Start", f"${bal_start:.2f}")
    bal_tbl.add_row("End", f"${bal_end:.2f}")
    bal_tbl.add_row("Change", f"[{change_style}]{'+' if change > 0 else ''}{change:.2f}[/{change_style}]")
    console.print(bal_tbl)

    # ── Position P&L (if available) ──
    pnl_data = data.get("position_pnl")
    if pnl_data:
        pnl_tbl = Table(title="📈 Position P&L", box=box.ROUNDED, title_style="bold cyan")
        pnl_tbl.add_column("Metric", style="bold")
        pnl_tbl.add_column("Value", justify="right")

        pnl_tbl.add_row("Active Positions", str(pnl_data.get("active_count", 0)))
        pnl_tbl.add_row("Active Value", f"${pnl_data.get('active_value', 0):.4f}")

        active_pnl = pnl_data.get("active_pnl", 0)
        ap_style = "green" if active_pnl > 0 else ("red" if active_pnl < 0 else "white")
        pnl_tbl.add_row("Active P&L", f"[{ap_style}]${active_pnl:.4f}[/{ap_style}]")

        pnl_tbl.add_row("Closed Positions", str(pnl_data.get("closed_count", 0)))
        
        closed_pnl = pnl_data.get("closed_realized_pnl", 0)
        cp_style = "green" if closed_pnl > 0 else ("red" if closed_pnl < 0 else "white")
        pnl_tbl.add_row("Closed Realized P&L", f"[{cp_style}]${closed_pnl:.4f}[/{cp_style}]")

        overall = pnl_data.get("overall_pnl", 0)
        ov_style = "green" if overall > 0 else ("red" if overall < 0 else "white")
        pnl_tbl.add_row("Overall P&L", f"[{ov_style}]${overall:.4f}[/{ov_style}]")

        console.print(pnl_tbl)
    elif pnl_data is None:
        console.print("[dim]  (No profile wallet configured — position P&L unavailable)[/dim]")

    # ── Trade details (if any) ──
    history = data.get("order_history", [])
    if history:
        trade_tbl = Table(title="📜 Trades This Session", box=box.ROUNDED, title_style="bold cyan", expand=True)
        trade_tbl.add_column("Time", style="dim")
        trade_tbl.add_column("Status", justify="center")
        trade_tbl.add_column("Size", justify="right")
        trade_tbl.add_column("Cost", justify="right")
        trade_tbl.add_column("Details")
        for record in history:
            sc = "green" if record.get("status") == "success" else ("red" if record.get("status") == "failed" else "yellow")
            trade_tbl.add_row(
                record.get("time", "?"),
                f"[{sc}]{record.get('status', '?').upper()}[/{sc}]",
                str(record.get("size", "?")),
                str(record.get("cost", "?")),
                record.get("details", ""),
            )
        console.print(trade_tbl)

    # ── Verdict ──
    if data.get("opportunities", 0) == 0:
        verdict = "No arbitrage opportunities appeared this session. The market stayed efficient."
        verdict_style = "yellow"
    elif data.get("trades", 0) > 0 and change > 0:
        verdict = f"Profitable session! Netted ${change:.2f}."
        verdict_style = "bold green"
    elif data.get("trades", 0) > 0:
        verdict = "Trades were executed but profit is pending market resolution."
        verdict_style = "cyan"
    else:
        verdict = "Opportunities were detected but no trades completed."
        verdict_style = "red"

    console.print(Panel(Text(verdict, justify="center", style=verdict_style), title="VERDICT", box=box.ROUNDED, style=verdict_style))
    console.print()
