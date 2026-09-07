const REFRESH_INTERVAL_MS = 500;
const API_URL = 'http://localhost:8000/api/state';

// Elements
const elServerStatusText = document.getElementById('server-status-text');
const elServerStatusDot = document.querySelector('.status-dot');
const elMarket = document.getElementById('val-market');
const elMode = document.getElementById('val-mode');
const elTime = document.getElementById('val-time');
const elPriceUp = document.getElementById('val-price-up');
const elSizeUp = document.getElementById('val-size-up');
const elPriceDown = document.getElementById('val-price-down');
const elSizeDown = document.getElementById('val-size-down');
const elTotal = document.getElementById('val-total');
const elThreshold = document.getElementById('val-threshold');
const elProfit = document.getElementById('val-profit');
const elStatusPanel = document.getElementById('status-panel');
const elStatusIcon = document.getElementById('val-status-icon');
const elStatusText = document.getElementById('val-status-text');
const elScans = document.getElementById('val-scans');
const elOps = document.getElementById('val-opportunities');
const elTrades = document.getElementById('val-trades');
const elBalance = document.getElementById('val-balance');
const elOrderSize = document.getElementById('val-order-size');
const elErrorBanner = document.getElementById('error-banner');
const elErrorText = document.getElementById('val-error-text');
const elTimestamp = document.getElementById('val-timestamp');

let isConnected = false;

function setConnectionStatus(connected) {
    if (connected !== isConnected) {
        isConnected = connected;
        elServerStatusText.textContent = connected ? 'Connected' : 'Disconnected';
        if (connected) {
            elServerStatusDot.classList.remove('disconnected');
        } else {
            elServerStatusDot.classList.add('disconnected');
            // Show loading state
            document.querySelectorAll('p[id^="val-"]').forEach(el => el.classList.add('loading'));
        }
    }
}

function updateUI(data) {
    if (data.loading) {
        elStatusText.textContent = data.status;
        return;
    }
    
    if (data.error) {
        showError(data.error);
        return;
    }

    // Remove loading class from everything
    document.querySelectorAll('.loading').forEach(el => el.classList.remove('loading'));
    hideError();

    // Text formatting helpers
    const formatCurrency = (val, dec = 4) => `$${parseFloat(val).toFixed(dec)}`;
    const formatNumber = (val) => new Intl.NumberFormat().format(val);

    // Info Row
    elMarket.textContent = data.market_slug || '---';
    elMode.textContent = data.mode || '---';
    elMode.className = data.mode === 'SIMULATION' ? 'text-yellow' : 'text-red highlight-text';
    elTime.textContent = data.time_remaining || 'CLOSED';

    // Order Book
    elPriceUp.textContent = formatCurrency(data.price_up);
    elSizeUp.textContent = formatNumber(data.size_up);
    elPriceDown.textContent = formatCurrency(data.price_down);
    elSizeDown.textContent = formatNumber(data.size_down);

    // Arbitrage
    elTotal.textContent = formatCurrency(data.total);
    elTotal.className = `value huge ${data.total <= data.threshold && data.total > 0 ? 'text-green' : (data.total <= data.threshold + 0.01 && data.total > 0 ? 'text-yellow' : '')}`;
    elThreshold.textContent = formatCurrency(data.threshold, 3);
    elProfit.textContent = `${formatCurrency(data.profit)} (${data.profit_pct.toFixed(2)}%)`;

    // Status Panel
    elStatusIcon.textContent = data.status_icon;
    elStatusText.textContent = data.status_text;
    
    if (data.status_text.includes('EXECUTING') || data.status_text.includes('ARBITRAGE')) {
        elStatusPanel.classList.add('active');
    } else {
        elStatusPanel.classList.remove('active');
    }

    // Bottom Stats
    elScans.textContent = formatNumber(data.scan_count || 0);
    elOps.textContent = formatNumber(data.opportunities || 0);
    elTrades.textContent = formatNumber(data.trades || 0);
    elBalance.textContent = `$${parseFloat(data.balance || 0).toFixed(2)}`;
    elOrderSize.textContent = data.order_size || 0;

    // Error from bot
    if (data.last_error && data.last_error !== "None") {
        showError(data.last_error);
    }

    // Timestamp
    if (data.timestamp) {
        const d = new Date(data.timestamp);
        elTimestamp.textContent = d.toLocaleTimeString();
    }
}

function showError(msg) {
    elErrorText.textContent = msg;
    elErrorBanner.classList.remove('hidden');
}

function hideError() {
    elErrorBanner.classList.add('hidden');
}

async function fetchState() {
    try {
        // Add cache busting
        const response = await fetch(`${API_URL}?t=${new Date().getTime()}`);
        if (!response.ok) throw new Error('Network response was not ok');
        
        const data = await response.json();
        setConnectionStatus(true);
        updateUI(data);
    } catch (error) {
        console.error('Failed to fetch state:', error);
        setConnectionStatus(false);
    }
}

// Start polling
setInterval(fetchState, REFRESH_INTERVAL_MS);
fetchState();
