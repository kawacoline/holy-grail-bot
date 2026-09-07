/**
 * Holy Grail Dashboard — Live Frontend
 * WebSocket for real-time bot state + REST for balances/positions
 */

// ═══════════════════════════════════════════════════════════
//  State
// ═══════════════════════════════════════════════════════════

let ws = null;
let wsReconnectTimer = null;
let lastBotState = null;
let balanceData = null;
let positionsData = null;
let currentView = 'dashboard';
let openOrdersData = null;
let openOrdersTimer = null;

const STRATEGY_NAMES = { 1: 'V1 Arb', 3: 'V3 Limit', 4: 'V4 OrderFlow' };
const STRATEGY_COLORS = { 1: '#06b6d4', 3: '#a78bfa', 4: '#22c55e' };

// ═══════════════════════════════════════════════════════════
//  Init
// ═══════════════════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', () => {
    setupNav();
    connectWebSocket();
    fetchBalances();
    fetchBotState();

    // Auto-refresh balances every 30s
    setInterval(fetchBalances, 30000);
    // Auto-refresh bot state via REST as fallback every 5s
    setInterval(fetchBotState, 5000);
});

// ═══════════════════════════════════════════════════════════
//  Navigation
// ═══════════════════════════════════════════════════════════

function setupNav() {
    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const view = item.dataset.view;
            switchView(view);
        });
    });
}

function switchView(view) {
    currentView = view;
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.querySelector(`[data-view="${view}"]`)?.classList.add('active');
    document.querySelectorAll('.view-section').forEach(s => s.classList.remove('active'));
    document.getElementById(`view-${view}`)?.classList.add('active');

    if (view === 'positions' && !positionsData) fetchPositions();
    if (view === 'orders') { fetchOpenOrders(); startOrdersPolling(); }
    else { stopOrdersPolling(); }
    if (view === 'history') fetchTradeLog();
}

// ═══════════════════════════════════════════════════════════
//  WebSocket
// ═══════════════════════════════════════════════════════════

function connectWebSocket() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${location.host}/ws`;
    
    try {
        ws = new WebSocket(url);
    } catch (e) {
        setConnectionStatus('disconnected', 'WS Error');
        scheduleReconnect();
        return;
    }

    ws.onopen = () => {
        setConnectionStatus('connected', 'Live');
        if (wsReconnectTimer) { clearTimeout(wsReconnectTimer); wsReconnectTimer = null; }
    };

    ws.onmessage = (event) => {
        try {
            const msg = JSON.parse(event.data);
            if (msg.type === 'state' && msg.data) {
                lastBotState = msg.data;
                renderDashboard();
            }
        } catch (e) { /* ignore parse errors */ }
    };

    ws.onclose = () => {
        setConnectionStatus('disconnected', 'Disconnected');
        scheduleReconnect();
    };

    ws.onerror = () => {
        setConnectionStatus('disconnected', 'Error');
    };
}

function scheduleReconnect() {
    if (wsReconnectTimer) return;
    wsReconnectTimer = setTimeout(() => {
        wsReconnectTimer = null;
        connectWebSocket();
    }, 3000);
}

function setConnectionStatus(status, text) {
    const badge = document.getElementById('connection-badge');
    const textEl = document.getElementById('connection-text');
    badge.className = 'connection-badge ' + status;
    textEl.textContent = text;
}

// ═══════════════════════════════════════════════════════════
//  REST Fetchers
// ═══════════════════════════════════════════════════════════

async function fetchBalances() {
    try {
        const resp = await fetch('/api/balance');
        balanceData = await resp.json();
        updateBalanceCards();
    } catch (e) { console.warn('Balance fetch failed:', e); }
}

async function fetchBotState() {
    try {
        const resp = await fetch('/api/bot-state');
        const data = await resp.json();
        if (data && !data.error) {
            lastBotState = data;
            renderDashboard();
        } else if (!lastBotState) {
            renderDashboard(); // render empty state
        }
    } catch (e) { /* silent */ }
}

async function fetchPositions() {
    try {
        const container = document.getElementById('positions-container');
        container.innerHTML = '<div class="loading-state"><div class="spinner"></div><p>Loading positions...</p></div>';
        const resp = await fetch('/api/positions');
        positionsData = await resp.json();
        renderPositions();
        updatePnlCards();
    } catch (e) {
        document.getElementById('positions-container').innerHTML = '<div class="empty-state"><p>Failed to load positions.</p></div>';
    }
}

async function fetchTradeLog() {
    try {
        const resp = await fetch('/api/trade-log');
        const data = await resp.json();
        renderTradeLog(data);
    } catch (e) { /* silent */ }
    // Also load positions for closed table
    if (!positionsData) {
        try {
            const resp = await fetch('/api/positions');
            positionsData = await resp.json();
            renderClosedPositions();
        } catch (e) { /* silent */ }
    } else {
        renderClosedPositions();
    }
}

function forceRefresh() {
    fetchBalances();
    fetchBotState();
    if (positionsData) fetchPositions();
    if (currentView === 'orders') fetchOpenOrders();
    if (currentView === 'history') fetchTradeLog();
}

// ═══════════════════════════════════════════════════════════
//  Render — Top Stat Cards
// ═══════════════════════════════════════════════════════════

function updateBalanceCards() {
    if (!balanceData?.balances) return;
    let totalBalance = 0;
    const details = [];
    for (const b of balanceData.balances) {
        totalBalance += b.total;
        details.push(`${b.client}: $${b.total.toFixed(2)}`);
    }
    const el = document.getElementById('total-balance');
    el.textContent = `$${totalBalance.toFixed(2)}`;
    el.style.color = totalBalance > 0 ? 'var(--green)' : 'var(--text-primary)';
    document.getElementById('balance-detail').textContent = details.join(' · ');
    document.getElementById('client-count').textContent = balanceData.balances.length;
}

function updatePnlCards() {
    if (!positionsData?.clients) return;
    let totalPnl = 0, totalWins = 0, totalLosses = 0, totalVolume = 0, totalActiveValue = 0;
    for (const c of positionsData.clients) {
        totalPnl += c.stats.closed_pnl;
        totalWins += c.stats.wins;
        totalLosses += c.stats.losses;
        totalVolume += c.stats.closed_volume;
        totalActiveValue += c.stats.active_value;
    }
    const pnlEl = document.getElementById('total-pnl');
    pnlEl.textContent = `$${totalPnl.toFixed(4)}`;
    pnlEl.style.color = totalPnl >= 0 ? 'var(--green)' : 'var(--red)';

    const totalPairs = totalWins + totalLosses;
    const winRate = totalPairs > 0 ? Math.round(totalWins / totalPairs * 100) : 0;
    document.getElementById('pnl-detail').textContent = `${winRate}% win rate (${totalWins}W/${totalLosses}L)`;
}

function updateBotStatCards() {
    if (!lastBotState?.clients) return;
    let totalTrades = 0, totalScans = 0;
    for (const c of lastBotState.clients) {
        totalTrades += c.trades || 0;
        totalScans += c.scan_count || 0;
    }
    document.getElementById('total-trades').textContent = totalTrades.toLocaleString();
    document.getElementById('total-scans').textContent = totalScans.toLocaleString();
    document.getElementById('last-update').textContent = new Date().toLocaleTimeString();

    // Update live badge
    const badge = document.getElementById('live-badge');
    const stale = lastBotState.stale || lastBotState.age_seconds > 15;
    if (stale) {
        badge.className = 'live-badge stale';
        badge.innerHTML = '<span class="live-dot"></span> STALE';
    } else {
        badge.className = 'live-badge';
        badge.innerHTML = '<span class="live-dot"></span> LIVE';
    }
}

// ═══════════════════════════════════════════════════════════
//  Render — Dashboard (Client Panels)
// ═══════════════════════════════════════════════════════════

function renderDashboard() {
    updateBotStatCards();
    const container = document.getElementById('client-panels');

    if (!lastBotState?.clients?.length) {
        container.innerHTML = `
            <div class="empty-state">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" opacity="0.3">
                    <circle cx="12" cy="12" r="10"/><path d="M12 8v4l3 3"/>
                </svg>
                <p>Bot is not running or no state data available yet.</p>
                <p style="font-size:0.72rem;color:var(--text-dim)">Start the bot with START.bat, then this dashboard will update automatically.</p>
            </div>`;
        return;
    }

    let html = '';
    for (const client of lastBotState.clients) {
        const strategyName = STRATEGY_NAMES[client.strategy] || `V${client.strategy}`;
        const isLive = client.mode === 'LIVE';
        const addr = client.address || '???';

        // Find matching balance
        let bal = null;
        if (balanceData?.balances) {
            bal = balanceData.balances.find(b => b.eoa?.startsWith(addr) || b.client?.toLowerCase().includes(`client${client.index}`));
        }
        const displayBalance = bal ? bal.total : (client.balance || 0);
        const balanceSource = bal ? `On-chain: $${bal.onchain?.total?.toFixed(2) || '?'} · CLOB: $${bal.clob_balance?.toFixed(2) || '?'}` : '';

        html += `
        <div class="client-panel">
            <div class="panel-header">
                <div class="panel-title">
                    <h3>Client ${client.index} <span style="color:var(--text-dim);font-weight:400">(${addr})</span></h3>
                    <span class="badge-strategy">${strategyName}</span>
                </div>
                <span class="panel-badge ${isLive ? 'badge-live' : 'badge-sim'}">${client.mode}</span>
            </div>
            <div class="panel-body">
                <div class="panel-stats">
                    <div class="panel-stat">
                        <div class="panel-stat-label">Balance</div>
                        <div class="panel-stat-value ${displayBalance > 0 ? 'green' : ''}" title="${balanceSource}">$${displayBalance.toFixed(2)}</div>
                    </div>
                    <div class="panel-stat">
                        <div class="panel-stat-label">Order Size</div>
                        <div class="panel-stat-value">${client.order_size || '—'}</div>
                    </div>
                    <div class="panel-stat">
                        <div class="panel-stat-label">Trades</div>
                        <div class="panel-stat-value cyan">${client.trades || 0}</div>
                    </div>
                    <div class="panel-stat">
                        <div class="panel-stat-label">Scans</div>
                        <div class="panel-stat-value">${(client.scan_count || 0).toLocaleString()}</div>
                    </div>
                    <div class="panel-stat">
                        <div class="panel-stat-label">Time Left</div>
                        <div class="panel-stat-value ${client.time_remaining === 'CLOSED' ? 'yellow' : ''}">${client.time_remaining || '—'}</div>
                    </div>
                </div>
                ${renderV1OrderBook(client)}
                ${renderMiniTrades(client)}
            </div>
        </div>`;
    }
    container.innerHTML = html;
}

function renderV1OrderBook(client) {
    if (client.strategy !== 1 || !lastBotState.v1_prices) return '';
    const p = lastBotState.v1_prices;
    if (!p.up && !p.down) return '';

    const up = p.up != null ? p.up.toFixed(4) : '--';
    const down = p.down != null ? p.down.toFixed(4) : '--';
    const sizeUp = p.size_up ? Math.round(p.size_up).toLocaleString() : '0';
    const sizeDown = p.size_down ? Math.round(p.size_down).toLocaleString() : '0';
    const total = (p.up || 1) + (p.down || 1);
    const profit = Math.max(0, 1 - total);

    return `
    <div class="order-book">
        <div class="order-book-side up">
            <div class="ob-label">▲ UP</div>
            <div class="ob-price text-green">$${up}</div>
            <div class="ob-size">${sizeUp} shares</div>
        </div>
        <div class="order-book-side down">
            <div class="ob-label">▼ DOWN</div>
            <div class="ob-price text-red">$${down}</div>
            <div class="ob-size">${sizeDown} shares</div>
        </div>
    </div>
    <div class="panel-stats">
        <div class="panel-stat">
            <div class="panel-stat-label">Total Cost</div>
            <div class="panel-stat-value ${total <= 0.99 ? 'green' : ''}">\$${total.toFixed(4)}</div>
        </div>
        <div class="panel-stat">
            <div class="panel-stat-label">Profit/Pair</div>
            <div class="panel-stat-value ${profit > 0 ? 'green' : ''}">\$${profit.toFixed(4)}</div>
        </div>
    </div>`;
}

function renderMiniTrades(client) {
    const trades = client.recent_trades || [];
    if (!trades.length) {
        return `<div class="mini-trades">
            <div class="mini-trades-title">Recent Activity</div>
            <div class="no-trades">Monitoring... trades will appear here.</div>
        </div>`;
    }
    let rows = '';
    for (const t of trades) {
        const statusClass = t.status === 'success' ? 'green' : (t.status === 'failed' ? 'red' : 'yellow');
        rows += `<tr>
            <td>${t.time || '—'}</td>
            <td><span style="color:var(--${statusClass})">${(t.status || '—').toUpperCase()}</span></td>
            <td>${t.size || '—'}</td>
            <td>${t.cost || '—'}</td>
            <td style="font-size:0.7rem;max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${t.details || ''}">${t.details || '—'}</td>
        </tr>`;
    }
    return `<div class="mini-trades">
        <div class="mini-trades-title">Recent Activity</div>
        <div class="table-wrapper" style="margin-top:6px">
            <table class="data-table" style="font-size:0.75rem">
                <thead><tr><th>Time</th><th>Status</th><th>Size</th><th>Cost</th><th>Details / Trigger</th></tr></thead>
                <tbody>${rows}</tbody>
            </table>
        </div>
    </div>`;
}

// ═══════════════════════════════════════════════════════════
//  Render — Positions
// ═══════════════════════════════════════════════════════════

function renderPositions() {
    const container = document.getElementById('positions-container');
    if (!positionsData?.clients?.length) {
        container.innerHTML = '<div class="empty-state"><p>No position data available.</p></div>';
        return;
    }

    let html = '';
    for (const clientData of positionsData.clients) {
        const c = clientData.client;
        const stats = clientData.stats;

        html += `<div class="client-panel">
            <div class="panel-header">
                <div class="panel-title">
                    <h3>${c.name}</h3>
                    <span class="badge-strategy">Strategy ${c.strategy}</span>
                </div>
                <span class="panel-badge ${c.dry_run ? 'badge-sim' : 'badge-live'}">${c.dry_run ? 'SIM' : 'LIVE'}</span>
            </div>
            <div class="panel-body">
                <div class="panel-stats">
                    <div class="panel-stat">
                        <div class="panel-stat-label">Active</div>
                        <div class="panel-stat-value">${stats.active_count}</div>
                    </div>
                    <div class="panel-stat">
                        <div class="panel-stat-label">Active Value</div>
                        <div class="panel-stat-value green">$${stats.active_value.toFixed(2)}</div>
                    </div>
                    <div class="panel-stat">
                        <div class="panel-stat-label">True PnL</div>
                        <div class="panel-stat-value ${stats.closed_pnl >= 0 ? 'green' : 'red'}">$${stats.closed_pnl.toFixed(2)}</div>
                    </div>
                    <div class="panel-stat">
                        <div class="panel-stat-label">Win Rate</div>
                        <div class="panel-stat-value">${stats.win_rate}% (${stats.wins}W/${stats.losses}L)</div>
                    </div>
                    <div class="panel-stat">
                        <div class="panel-stat-label">Volume</div>
                        <div class="panel-stat-value">$${(stats.total_bought || stats.closed_volume || 0).toFixed(2)}</div>
                    </div>
                    <div class="panel-stat">
                        <div class="panel-stat-label">Redeemable</div>
                        <div class="panel-stat-value ${stats.redeemable > 0 ? 'yellow' : ''}">${stats.redeemable}</div>
                    </div>
                </div>
                ${renderActivePositionsTable(clientData.active)}
            </div>
        </div>`;
    }
    container.innerHTML = html;
}

function renderActivePositionsTable(positions) {
    if (!positions?.length) return '<div class="no-trades">No active positions</div>';
    let rows = '';
    for (const p of positions.slice(0, 20)) {
        const title = p.title || p.eventSlug || '—';
        const size = parseFloat(p.size || 0).toFixed(2);
        const avgPrice = parseFloat(p.avgPrice || 0).toFixed(4);
        const curValue = parseFloat(p.currentValue || 0).toFixed(4);
        rows += `<tr>
            <td style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${title}</td>
            <td>${size}</td>
            <td>$${avgPrice}</td>
            <td>$${curValue}</td>
        </tr>`;
    }
    return `<div class="table-wrapper" style="margin-top:12px">
        <table class="data-table">
            <thead><tr><th>Market</th><th>Size</th><th>Avg Price</th><th>Value</th></tr></thead>
            <tbody>${rows}</tbody>
        </table>
    </div>`;
}

// ═══════════════════════════════════════════════════════════
//  Render — Trade Log
// ═══════════════════════════════════════════════════════════

function renderTradeLog(data) {
    const tbody = document.getElementById('trade-log-body');
    const empty = document.getElementById('trade-log-empty');

    if (!data?.trades?.length) {
        tbody.innerHTML = '';
        empty.style.display = 'flex';
        return;
    }

    empty.style.display = 'none';
    let html = '';
    for (const t of data.trades.slice(0, 100)) {
        const time = t.timestamp ? new Date(t.timestamp).toLocaleString() : '—';
        const isLive = !t.dry_run;
        const triggerData = t.trigger_data || '—';
        html += `<tr>
            <td>${time}</td>
            <td>${t.strategy || '—'}</td>
            <td>${(t.asset || '—').toUpperCase()}</td>
            <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${t.slug || t.market_slug || t.market || '—'}</td>
            <td>$${parseFloat(t.price || 0).toFixed(4)}</td>
            <td>${t.size || '—'}</td>
            <td>$${parseFloat(t.combined_cost || t.pair_cost || 0).toFixed(4)}</td>
            <td><span class="panel-badge ${isLive ? 'badge-live' : 'badge-sim'}">${isLive ? 'LIVE' : 'SIM'}</span></td>
            <td style="font-size:0.7rem;color:var(--text-secondary);max-width:250px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${triggerData}">${triggerData}</td>
        </tr>`;
    }
    tbody.innerHTML = html;
}

function renderClosedPositions() {
    const tbody = document.getElementById('closed-body');
    const empty = document.getElementById('closed-empty');
    if (!positionsData?.clients) return;

    let allClosed = [];
    for (const c of positionsData.clients) {
        for (const p of (c.closed || [])) {
            allClosed.push({ ...p, clientName: c.client.name });
        }
    }

    if (!allClosed.length) {
        tbody.innerHTML = '';
        empty.style.display = 'flex';
        return;
    }

    empty.style.display = 'none';
    let html = '';
    for (const p of allClosed.slice(0, 100)) {
        const pnl = parseFloat(p.realizedPnl || 0);
        const invested = parseFloat(p.totalBought || 0);
        html += `<tr>
            <td>${p.clientName}</td>
            <td style="max-width:250px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${p.title || p.eventSlug || '—'}</td>
            <td>${p.outcome || '—'}</td>
            <td>$${invested.toFixed(4)}</td>
            <td>$${(invested + pnl).toFixed(4)}</td>
            <td class="${pnl >= 0 ? 'text-green' : 'text-red'}">${pnl >= 0 ? '+' : ''}$${pnl.toFixed(4)}</td>
        </tr>`;
    }
    tbody.innerHTML = html;
}

// ═══════════════════════════════════════════════════════════
//  Actions
// ═══════════════════════════════════════════════════════════

async function redeemAll() {
    const statusEl = document.getElementById('redeem-status');
    const btn = document.getElementById('btn-redeem-all');
    btn.disabled = true;
    statusEl.textContent = 'Redeeming...';
    statusEl.style.color = 'var(--yellow)';

    try {
        const resp = await fetch('/api/redeem-all', { method: 'POST' });
        const data = await resp.json();
        if (data.results) {
            const total = data.results.reduce((sum, r) => sum + (r.result?.redeemed || 0), 0);
            statusEl.textContent = `✅ Redeemed ${total} position(s)`;
            statusEl.style.color = 'var(--green)';
            setTimeout(() => fetchBalances(), 2000);
        }
    } catch (e) {
        statusEl.textContent = `❌ Error: ${e.message}`;
        statusEl.style.color = 'var(--red)';
    }
    btn.disabled = false;
    setTimeout(() => { statusEl.textContent = ''; }, 8000);
}

async function resetData() {
    if (!confirm('Archive and clear trade history?')) return;
    try {
        const resp = await fetch('/api/reset', { method: 'POST' });
        const data = await resp.json();
        alert(data.message || 'Done');
        if (currentView === 'history') fetchTradeLog();
    } catch (e) {
        alert('Error: ' + e.message);
    }
}

// ═══════════════════════════════════════════════════════════
//  Open Orders — Fetch, Render, Auto-Poll
// ═══════════════════════════════════════════════════════════

async function fetchOpenOrders() {
    const container = document.getElementById('orders-container');
    if (!openOrdersData) {
        container.innerHTML = '<div class="loading-state"><div class="spinner"></div><p>Fetching orders from CLOB...</p></div>';
    }
    try {
        const resp = await fetch('/api/open-orders');
        openOrdersData = await resp.json();
        renderOpenOrders();
    } catch (e) {
        container.innerHTML = `<div class="orders-empty"><p>Failed to fetch open orders</p><p class="hint">${e.message}</p></div>`;
    }
}

function startOrdersPolling() {
    stopOrdersPolling();
    openOrdersTimer = setInterval(fetchOpenOrders, 10000);
}

function stopOrdersPolling() {
    if (openOrdersTimer) { clearInterval(openOrdersTimer); openOrdersTimer = null; }
}

function renderOpenOrders() {
    const container = document.getElementById('orders-container');
    const badge = document.getElementById('orders-count-badge');
    
    if (!openOrdersData?.orders) {
        container.innerHTML = '<div class="orders-empty"><p>No data</p></div>';
        badge.textContent = '0';
        return;
    }

    const orders = openOrdersData.orders.filter(o => !o.error);
    const errors = openOrdersData.orders.filter(o => o.error);
    badge.textContent = orders.length;

    if (orders.length === 0) {
        container.innerHTML = `
            <div class="orders-empty">
                <svg width="56" height="56" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                    <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>
                    <polyline points="14,2 14,8 20,8"/>
                    <line x1="16" y1="13" x2="8" y2="13"/>
                    <line x1="16" y1="17" x2="8" y2="17"/>
                </svg>
                <p>No open orders on the book</p>
                <p class="hint">
                    The V4 OrderFlow strategy places GTC limit orders at $0.45 on both UP and DOWN sides
                    when it detects momentum and a clear path. Orders appear here once placed and disappear
                    when filled or the market closes.
                </p>
                ${errors.length ? '<p class="hint text-yellow">⚠️ ' + errors.map(e => e.client + ': ' + e.error).join(' | ') + '</p>' : ''}
            </div>`;
        return;
    }

    let html = '<div class="orders-grid">';
    for (const o of orders) {
        const isBuy = o.side === 'BUY';
        const fillPct = o.original_size > 0 ? Math.round((o.size_matched / o.original_size) * 100) : 0;
        const statusClass = o.status === 'LIVE' ? 'status-live' : o.status === 'MATCHED' ? 'status-matched' : 'status-cancelled';
        const sideColor = isBuy ? 'var(--green)' : 'var(--red)';
        const sideLabel = isBuy ? '▲ BUY' : '▼ SELL';
        const created = o.created_at ? new Date(o.created_at).toLocaleTimeString() : '—';
        const orderId = o.order_id ? o.order_id.substring(0, 12) + '...' : '—';

        html += `
        <div class="order-card ${isBuy ? 'order-buy' : 'order-sell'}">
            <div class="order-card-header">
                <h4>${o.client} <span>· ${orderId}</span></h4>
                <span class="order-status-badge ${statusClass}">${o.status || 'LIVE'}</span>
            </div>
            <div class="order-card-body">
                <div class="order-card-stats">
                    <div class="order-card-stat">
                        <div class="label">Side</div>
                        <div class="value" style="color:${sideColor}">${sideLabel}</div>
                    </div>
                    <div class="order-card-stat">
                        <div class="label">Price</div>
                        <div class="value">$${o.price.toFixed(2)}</div>
                    </div>
                    <div class="order-card-stat">
                        <div class="label">Size</div>
                        <div class="value">${o.original_size.toFixed(1)}</div>
                    </div>
                </div>
                <div class="order-fill-bar">
                    <div class="order-fill-bar-inner" style="width:${fillPct}%"></div>
                </div>
                <div class="order-fill-label">
                    <span>Filled: ${o.size_matched.toFixed(2)} / ${o.original_size.toFixed(2)}</span>
                    <span>${fillPct}%</span>
                </div>
                <div class="order-meta">
                    <span>Type: ${o.order_type || 'GTC'}</span>
                    <span>Created: ${created}</span>
                    <span>Asset: ${o.asset_id}</span>
                </div>
            </div>
        </div>`;
    }
    html += '</div>';

    if (errors.length) {
        html += '<div style="margin-top:16px;font-size:0.75rem;color:var(--yellow)">⚠️ ' + errors.map(e => e.client + ': ' + e.error).join(' | ') + '</div>';
    }

    container.innerHTML = html;
}
