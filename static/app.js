/**
 * MEXC RSI Screener — Frontend Logic
 * localStorage-based config & data persistence.
 * Per-client session with SSE streaming.
 */

// ── Storage Keys ─────────────────────────────────────────
const STORAGE_KEYS = {
    SESSION_ID: 'mexc_session_id',
    CONFIG: 'mexc_config',
    CACHED_DATA: 'mexc_cached_data',
    LAST_UPDATE: 'mexc_last_update',
};

// ── State ────────────────────────────────────────────────
let isRunning = false;
let eventSource = null;

// ── Sorting State ────────────────────────────────────────
let currentSortColumn = 'symbol';
let currentSortDirection = 'asc';
let assetDataCache = []; // Holds the latest data for all assets
let currentConfig = {};
let sessionId = '';

// ── DOM Elements ─────────────────────────────────────────
const statusBadge = document.getElementById('status-badge');
const countdownDisp = document.getElementById('countdown-display');
const btnStart = document.getElementById('btn-start');
const btnStop = document.getElementById('btn-stop');
const btnReset = document.getElementById('btn-reset');
const btnSettings = document.getElementById('btn-settings');
const btnClose = document.getElementById('btn-close-settings');
const btnSave = document.getElementById('btn-save-settings');
const btnCancel = document.getElementById('btn-cancel-settings');
const btnExport = document.getElementById('btn-export-settings');
const btnImport = document.getElementById('btn-import-settings');
const importFileInput = document.getElementById('import-file-input');
const btnClearLog = document.getElementById('btn-clear-log');
const overlay = document.getElementById('settings-overlay');
const settingsBody = document.getElementById('settings-body');
const dataBody = document.getElementById('data-body');
const logContainer = document.getElementById('log-container');
const lastUpdateEl = document.getElementById('last-update');


// ── localStorage Helpers ─────────────────────────────────
function saveToStorage(key, value) {
    try {
        localStorage.setItem(key, JSON.stringify(value));
    } catch (e) {
        console.warn('localStorage save failed:', e);
    }
}

function loadFromStorage(key) {
    try {
        const val = localStorage.getItem(key);
        return val ? JSON.parse(val) : null;
    } catch (e) {
        console.warn('localStorage load failed:', e);
        return null;
    }
}


// ── Session ID ───────────────────────────────────────────
function getOrCreateSessionId() {
    let id = loadFromStorage(STORAGE_KEYS.SESSION_ID);
    if (!id) {
        id = crypto.randomUUID ? crypto.randomUUID() : 'sess_' + Date.now() + '_' + Math.random().toString(36).slice(2);
        saveToStorage(STORAGE_KEYS.SESSION_ID, id);
    }
    return id;
}


// ── Config Management ────────────────────────────────────
async function loadConfig() {
    // Try localStorage first
    let config = loadFromStorage(STORAGE_KEYS.CONFIG);
    if (config && config.Assets && config.Assets.length > 0) {
        currentConfig = config;
        return;
    }

    // First visit — fetch defaults from server
    try {
        const res = await fetch('/api/defaults');
        config = await res.json();
        currentConfig = config;
        saveToStorage(STORAGE_KEYS.CONFIG, config);
    } catch (e) {
        console.error('Failed to load defaults:', e);
        // Hardcoded fallback
        currentConfig = {
            Timeframe: 15,
            Assets: ["BTC_USDT", "ETH_USDT"],
            RSI_Period: 14, RSI_Overbought: 70, RSI_Oversold: 30,
            Stoch_K_Period: 14, Stoch_K_Smooth: 3, Stoch_D_Smooth: 3,
            Stoch_Overbought: 80, Stoch_Oversold: 20, Stoch_Alert_Method: 1,
            EMA_Long_Period: 200, EMA_Short_Period: 21, EMA_Proximity_ATR_Ratio: 0.15,
            ATR_Period: 14,
            LR_Length: 200, LR_ATR_Length: 14, LR_R2_Threshold: 0.3,
            LR_Slope_Threshold: 0.5, LR_Sideways_Slope_Threshold: 0.2,
            LR_Volatility_MA_Length: 20, LR_Higher_Timeframe: 240,
        };
        saveToStorage(STORAGE_KEYS.CONFIG, currentConfig);
    }

    updateHeaderLabels();
}

function saveConfig(config) {
    currentConfig = config;
    saveToStorage(STORAGE_KEYS.CONFIG, config);
}


// ── Cached Data (persists analysis results) ──────────────
function cacheAssetData(assetResult) {
    let cached = loadFromStorage(STORAGE_KEYS.CACHED_DATA) || {};
    cached[assetResult.symbol] = assetResult;
    saveToStorage(STORAGE_KEYS.CACHED_DATA, cached);
}

function loadCachedData() {
    return loadFromStorage(STORAGE_KEYS.CACHED_DATA) || {};
}

function clearCachedData() {
    saveToStorage(STORAGE_KEYS.CACHED_DATA, {});
    saveToStorage(STORAGE_KEYS.LAST_UPDATE, null);
}


// ── SSE Connection ───────────────────────────────────────
function connectSSE() {
    if (eventSource) {
        eventSource.close();
    }

    eventSource = new EventSource(`/stream?session_id=${encodeURIComponent(sessionId)}`);

    eventSource.onmessage = (event) => {
        try {
            const msg = JSON.parse(event.data);
            handleEvent(msg.type, msg.data);
        } catch (e) {
            console.error('SSE parse error:', e);
        }
    };

    eventSource.onerror = () => {
        console.warn('SSE connection lost, reconnecting...');
    };
}

function handleEvent(type, data) {
    switch (type) {
        case 'asset_update':
            // Update cache array
            const existingIndex = assetDataCache.findIndex(a => a.symbol === data.symbol);
            if (existingIndex >= 0) {
                assetDataCache[existingIndex] = data;
            } else {
                assetDataCache.push(data);
            }
            renderTable();
            cacheAssetData(data); // Persist to localStorage
            break;
        case 'cycle_complete':
            const updateText = `Last: ${data.timestamp} — ${data.count}/${data.total} refreshed`;
            lastUpdateEl.textContent = updateText;
            saveToStorage(STORAGE_KEYS.LAST_UPDATE, updateText);
            break;
        case 'countdown':
            updateCountdown(data.seconds_left);
            break;
        case 'status':
            setRunningState(data.running);
            break;
        case 'log':
            addLog(data.message, data.level);
            break;
            clearCachedData();
            assetDataCache = [];
            dataBody.innerHTML = '<tr class="empty-row"><td colspan="14">Data reset. Click <strong>Start</strong> to begin.</td></tr>';
            countdownDisp.textContent = '—';
            lastUpdateEl.textContent = '—';
            break;
        case 'heartbeat':
            break;
    }
}


// ── Table Rendering & Sorting ─────────────────────────────
function handleSortClick(e) {
    const th = e.currentTarget;
    const sortKey = th.dataset.sort;

    if (currentSortColumn === sortKey) {
        currentSortDirection = currentSortDirection === 'asc' ? 'desc' : 'asc';
    } else {
        currentSortColumn = sortKey;
        currentSortDirection = 'asc';
    }

    // Update header classes
    document.querySelectorAll('th.sortable').forEach(el => {
        el.classList.remove('asc', 'desc');
    });
    th.classList.add(currentSortDirection);

    renderTable();
}

// Attach click listeners to headers
document.querySelectorAll('th.sortable').forEach(th => {
    th.addEventListener('click', handleSortClick);
});

function sortData() {
    const data = [...assetDataCache];

    data.sort((a, b) => {
        let valA = a[currentSortColumn];
        let valB = b[currentSortColumn];

        // Handle nested/derived fields
        if (currentSortColumn === 'alerts_count') {
            valA = a.alerts ? a.alerts.length : 0;
            valB = b.alerts ? b.alerts.length : 0;
        }

        // Keep nulls at the bottom
        if (valA == null && valB != null) return 1;
        if (valA != null && valB == null) return -1;
        if (valA == null && valB == null) return 0;

        if (typeof valA === 'string') {
            return currentSortDirection === 'asc'
                ? valA.localeCompare(valB)
                : valB.localeCompare(valA);
        } else {
            return currentSortDirection === 'asc'
                ? valA - valB
                : valB - valA;
        }
    });

    return data;
}

function renderTable() {
    if (assetDataCache.length === 0) {
        if (!dataBody.querySelector('tr:not(.empty-row)')) {
            return;
        }
    }

    const sortedData = sortData();
    dataBody.innerHTML = ''; // Clear current rows

    if (sortedData.length === 0 && !isRunning) {
        dataBody.innerHTML = '<tr class="empty-row"><td colspan="14">Click <strong>Start</strong> to begin scanning</td></tr>';
        return;
    } else if (sortedData.length === 0 && isRunning) {
        dataBody.innerHTML = '<tr class="empty-row"><td colspan="14">Waiting for data...</td></tr>';
        return;
    }

    sortedData.forEach(asset => {
        const row = document.createElement('tr');
        row.id = `row-${asset.symbol}`;

        row.innerHTML = `
            <td class="cell-symbol">${asset.symbol}</td>
            <td class="cell-cyan">${formatPrice(asset.price)}</td>
            <td class="${rsiClass(asset.rsi)}">${asset.rsi != null ? asset.rsi.toFixed(2) : '<span class="cell-note">—</span>'}</td>
            <td class="${stochClass(asset.stoch_k)}">${asset.stoch_k != null ? asset.stoch_k.toFixed(2) : '<span class="cell-note">—</span>'}</td>
            <td class="${stochClass(asset.stoch_d)}">${asset.stoch_d != null ? asset.stoch_d.toFixed(2) : '<span class="cell-note">—</span>'}</td>
            <td>${formatEmaLong(asset)}</td>
            <td>${formatEmaShort(asset)}</td>
            <td>${asset.atr_ratio != null ? asset.atr_ratio.toFixed(2) : '<span class="cell-note">—</span>'}</td>
            <td class="${trendClass(asset.lr_trend)}">${formatTrend(asset.lr_trend, asset.lr_note)}</td>
            <td class="${confidenceClass(asset.lr_confidence)}">${asset.lr_confidence != null ? asset.lr_confidence : '<span class="cell-note">—</span>'}</td>
            <td>${asset.lr_r_squared != null ? asset.lr_r_squared : '<span class="cell-note">—</span>'}</td>
            <td class="${volatilityClass(asset.lr_volatility)}">${asset.lr_volatility || '<span class="cell-note">—</span>'}</td>
            <td class="${trendClass(asset.lr_htf_trend)}">${formatTrend(asset.lr_htf_trend, asset.lr_htf_note)}</td>
            <td>${formatAlerts(asset.alerts)}</td>
        `;
        dataBody.appendChild(row);

        // Browser notification for alerts (only issue them once per update if needed)
        // Note: in a fully reactive render this might re-trigger notifications on sort.
        // We handle that by only sending notifications in the SSE handler but for simplicity 
        // we'll leave it here as is for now, or just remove from render loop and move to handler.
    });
}

function formatPrice(p) {
    if (p == null) return '<span class="cell-note">—</span>';
    if (p >= 1) return p.toFixed(4);
    if (p >= 0.001) return p.toFixed(6);
    return p.toFixed(8);
}

function rsiClass(rsi) {
    if (rsi == null) return 'cell-neutral';
    if (rsi > 70) return 'cell-below';
    if (rsi < 30) return 'cell-above';
    return 'cell-neutral';
}

function stochClass(val) {
    if (val == null) return 'cell-neutral';
    if (val > 80) return 'cell-below';
    if (val < 20) return 'cell-above';
    return 'cell-neutral';
}

function formatEmaLong(asset) {
    if (asset.ema_long == null) return `<span class="cell-note">${asset.ema_long_note || '—'}</span>`;
    const cls = asset.ema_long_position === 'ABOVE' ? 'cell-above' : 'cell-below';
    return `<span class="${cls}">${asset.ema_long_position}</span>`;
}

function formatEmaShort(asset) {
    if (asset.ema_short == null) return `<span class="cell-note">${asset.ema_short_note || '—'}</span>`;
    if (asset.ema_proximity) {
        return `<span class="alert-tag alert-tag-warning">${asset.ema_proximity}</span>`;
    }
    return `<span class="cell-neutral">${asset.ema_short}</span>`;
}

function formatAlerts(alerts) {
    if (!alerts || alerts.length === 0) return '<span class="cell-note">—</span>';
    return alerts.map(a =>
        `<span class="alert-tag alert-tag-${a.level}">${a.type}</span>`
    ).join('');
}

function trendClass(trend) {
    if (trend === 'Uptrend') return 'cell-uptrend';
    if (trend === 'Downtrend') return 'cell-downtrend';
    if (trend === 'Sideways') return 'cell-sideways';
    return 'cell-neutral';
}

function formatTrend(trend, note) {
    if (trend == null) return `<span class="cell-note">${note || '—'}</span>`;
    return trend;
}

function confidenceClass(conf) {
    if (conf == null) return 'cell-neutral';
    if (conf >= 0.7) return 'cell-above';
    if (conf >= 0.4) return 'cell-cyan';
    if (conf >= 0.15) return 'cell-sideways';
    return 'cell-dim';
}

function volatilityClass(regime) {
    if (regime === 'HIGH') return 'cell-below';
    if (regime === 'LOW') return 'cell-cyan';
    return 'cell-neutral';
}


// ── Countdown ────────────────────────────────────────────
function updateCountdown(seconds) {
    if (seconds <= 0) {
        countdownDisp.textContent = 'Scanning...';
        return;
    }
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    countdownDisp.textContent = `Next in ${m}:${s.toString().padStart(2, '0')}`;
}


// ── Logging ──────────────────────────────────────────────
function addLog(message, level = 'info') {
    const entry = document.createElement('div');
    const time = new Date().toLocaleTimeString();
    entry.className = `log-entry log-${level}`;
    entry.innerHTML = `<span class="log-time">${time}</span>${message}`;
    logContainer.appendChild(entry);
    logContainer.scrollTop = logContainer.scrollHeight;

    while (logContainer.children.length > 100) {
        logContainer.removeChild(logContainer.firstChild);
    }
}


// ── Button State ─────────────────────────────────────────
function setRunningState(running) {
    isRunning = running;
    btnStart.disabled = running;
    btnStop.disabled = !running;

    if (running) {
        statusBadge.textContent = 'Running';
        statusBadge.className = 'badge badge-running';
    } else {
        statusBadge.textContent = 'Stopped';
        statusBadge.className = 'badge badge-stopped';
        countdownDisp.textContent = '—';
    }
}


// ── Button Handlers ──────────────────────────────────────
btnStart.addEventListener('click', async () => {
    btnStart.disabled = true;
    try {
        const res = await fetch('/api/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session_id: sessionId,
                config: currentConfig
            })
        });
        if (!res.ok) {
            addLog(`Failed to start screener: Server returned ${res.status} ${res.statusText}`, 'error');
            btnStart.disabled = false;
            return;
        }
        const data = await res.json();
        if (data.status === 'started' || data.status === 'already_running') {
            setRunningState(true);
            addLog('Screener started.', 'success');
        }
    } catch (e) {
        addLog('Failed to start screener: ' + e.message, 'error');
        btnStart.disabled = false;
    }
});

btnStop.addEventListener('click', async () => {
    btnStop.disabled = true;
    try {
        const res = await fetch('/api/stop', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: sessionId })
        });
        if (!res.ok) {
            addLog(`Failed to stop screener: Server returned ${res.status}`, 'error');
            return;
        }
        setRunningState(false);
        addLog('Screener stopped.', 'warning');
    } catch (e) {
        addLog('Failed to stop screener: ' + e.message, 'error');
    }
});

btnReset.addEventListener('click', async () => {
    if (!confirm('This will stop the screener and clear all cached data. Continue?')) return;
    btnReset.disabled = true;
    try {
        await fetch('/api/reset', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: sessionId })
        });
        setRunningState(false);
        clearCachedData();
        addLog('Data reset.', 'warning');
    } catch (e) {
        addLog('Failed to reset: ' + e.message, 'error');
    }
    btnReset.disabled = false;
});

btnClearLog.addEventListener('click', () => {
    logContainer.innerHTML = '';
    addLog('Log cleared.', 'info');
});


// ── Settings Panel ───────────────────────────────────────
const SETTING_GROUPS = {
    'General': ['Timeframe'],
    'Assets': ['Assets'],
    'RSI': ['RSI_Period', 'RSI_Overbought', 'RSI_Oversold'],
    'Stochastic': ['Stoch_K_Period', 'Stoch_K_Smooth', 'Stoch_D_Smooth', 'Stoch_Overbought', 'Stoch_Oversold', 'Stoch_Alert_Method'],
    'EMA': ['EMA_Long_Period', 'EMA_Short_Period', 'EMA_Proximity_ATR_Ratio'],
    'ATR': ['ATR_Period'],
    'Linear Regression': ['LR_Length', 'LR_ATR_Length', 'LR_R2_Threshold', 'LR_Slope_Threshold', 'LR_Sideways_Slope_Threshold', 'LR_Volatility_MA_Length', 'LR_Higher_Timeframe']
};

const SETTING_LABELS = {
    'Timeframe': 'Timeframe (minutes)',
    'RSI_Period': 'Period',
    'RSI_Overbought': 'Overbought',
    'RSI_Oversold': 'Oversold',
    'Stoch_K_Period': '%K Period',
    'Stoch_K_Smooth': '%K Smoothing',
    'Stoch_D_Smooth': '%D Smoothing',
    'Stoch_Overbought': 'Overbought',
    'Stoch_Oversold': 'Oversold',
    'Stoch_Alert_Method': 'Alert Method (1 or 2)',
    'EMA_Long_Period': 'Long Period',
    'EMA_Short_Period': 'Short Period',
    'EMA_Proximity_ATR_Ratio': 'Proximity ATR Ratio',
    'ATR_Period': 'Period',
    'LR_Length': 'Length (candles)',
    'LR_ATR_Length': 'ATR Length',
    'LR_R2_Threshold': 'R² Threshold',
    'LR_Slope_Threshold': 'Slope Threshold',
    'LR_Sideways_Slope_Threshold': 'Sideways Slope Threshold',
    'LR_Volatility_MA_Length': 'Volatility MA Length',
    'LR_Higher_Timeframe': 'Higher Timeframe (minutes)'
};

btnSettings.addEventListener('click', () => openSettings());
btnClose.addEventListener('click', () => overlay.classList.add('hidden'));
btnCancel.addEventListener('click', () => overlay.classList.add('hidden'));

function openSettings() {
    overlay.classList.remove('hidden');

    let html = '';
    for (const [group, keys] of Object.entries(SETTING_GROUPS)) {
        html += `<div class="setting-group">`;
        html += `<div class="setting-group-title">${group}</div>`;

        for (const key of keys) {
            const val = currentConfig[key];
            const label = SETTING_LABELS[key] || key;

            if (key === 'Assets') {
                html += `
                    <div class="setting-row" style="flex-direction: column; align-items: flex-start;">
                        <span class="setting-label">${label} (comma-separated)</span>
                        <input class="setting-input setting-input-wide" data-key="${key}" data-type="array"
                               value="${Array.isArray(val) ? val.join(', ') : val}" />
                    </div>`;
            } else {
                html += `
                    <div class="setting-row">
                        <span class="setting-label">${label}</span>
                        <input class="setting-input" data-key="${key}" data-type="number"
                               type="number" step="any" value="${val}" />
                    </div>`;
            }
        }
        html += `</div>`;
    }

    settingsBody.innerHTML = html;
}

btnSave.addEventListener('click', () => {
    const inputs = settingsBody.querySelectorAll('.setting-input');
    const newConfig = {};

    inputs.forEach(input => {
        const key = input.dataset.key;
        const type = input.dataset.type;

        if (type === 'array') {
            newConfig[key] = input.value.split(',').map(s => s.trim()).filter(Boolean);
        } else {
            const num = parseFloat(input.value);
            newConfig[key] = isNaN(num) ? input.value : num;
        }
    });

    saveConfig(newConfig);
    updateHeaderLabels();
    addLog('Settings saved to browser. Changes take effect on next Start.', 'success');
    overlay.classList.add('hidden');
});

// ── UI Helpers ───────────────────────────────────────────
function updateHeaderLabels() {
    const tfElem = document.getElementById('th-trend-tf');
    const htfElem = document.getElementById('th-htf-tf');
    if (tfElem) tfElem.textContent = `(${currentConfig.Timeframe || 15}m)`;
    if (htfElem) htfElem.textContent = `(${currentConfig.LR_Higher_Timeframe || 240}m)`;
}


// ── Export / Import Settings ─────────────────────────────
btnExport.addEventListener('click', () => {
    const dataStr = JSON.stringify(currentConfig, null, 4);
    const blob = new Blob([dataStr], { type: 'application/json' });
    const url = URL.createObjectURL(blob);

    const a = document.createElement('a');
    a.href = url;
    const timestamp = new Date().toISOString().slice(0, 10);
    a.download = `mexc_screener_settings_${timestamp}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);

    addLog('Settings exported to JSON file.', 'success');
});

btnImport.addEventListener('click', () => {
    importFileInput.click();
});

importFileInput.addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (event) => {
        try {
            const imported = JSON.parse(event.target.result);

            // Validate: must have at least an Assets array
            if (!imported.Assets || !Array.isArray(imported.Assets)) {
                addLog('Import failed: JSON must contain an "Assets" array.', 'error');
                return;
            }

            saveConfig(imported);
            addLog(`Settings imported from "${file.name}". ${imported.Assets.length} assets loaded.`, 'success');

            // Refresh settings panel if open
            if (!overlay.classList.contains('hidden')) {
                openSettings();
            }
        } catch (err) {
            addLog(`Import failed: Invalid JSON file — ${err.message}`, 'error');
        }
    };
    reader.readAsText(file);

    // Reset file input so same file can be re-imported
    importFileInput.value = '';
});

// ── Browser Notifications ────────────────────────────────
function sendBrowserNotification(symbol, message) {
    if (Notification.permission === 'granted') {
        new Notification(`MEXC Alert: ${symbol}`, { body: message, icon: '📊' });
    }
}

document.addEventListener('click', () => {
    if (Notification.permission === 'default') {
        Notification.requestPermission();
    }
}, { once: true });


// ── Restore Cached Data on Load ──────────────────────────
function restoreCachedData() {
    const cached = loadCachedData();
    if (cached && Object.keys(cached).length > 0) {
        for (const [symbol, asset] of Object.entries(cached)) {
            updateAssetRow(asset);
        }
        addLog('Restored previous analysis data from cache.', 'info');
    }

    const lastUpdate = loadFromStorage(STORAGE_KEYS.LAST_UPDATE);
    if (lastUpdate) {
        lastUpdateEl.textContent = lastUpdate;
    }
}


// ── Init ─────────────────────────────────────────────────
async function init() {
    sessionId = getOrCreateSessionId();
    await loadConfig();
    connectSSE();
    restoreCachedData();
}

init();
