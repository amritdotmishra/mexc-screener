"""
MEXC RSI Screener - Web Dashboard
Flask backend with per-client sessions and Server-Sent Events.
Each browser visitor gets their own independent screener session.
"""
from flask import Flask, render_template, jsonify, request, Response
import threading
import json
import time
import os
import queue
import uuid
from datetime import datetime

# Import analysis functions from the existing screener
from mexc_rsi_screener import (
    load_market_data, save_market_data,
    get_interval_str, fetch_kline_data, parse_ohlc,
    calculate_rsi, calculate_stochastic, calculate_ema, calculate_atr,
    classify_trend
)

app = Flask(__name__)

# ── Default Config (used when a new user visits for the first time) ────
DEFAULT_CONFIG = {
    "Timeframe": 15,
    "Assets": ["BTC_USDT", "ETH_USDT"],
    "RSI_Period": 14,
    "RSI_Overbought": 70,
    "RSI_Oversold": 30,
    "Stoch_K_Period": 14,
    "Stoch_K_Smooth": 3,
    "Stoch_D_Smooth": 3,
    "Stoch_Overbought": 80,
    "Stoch_Oversold": 20,
    "Stoch_Alert_Method": 1,
    "EMA_Long_Period": 200,
    "EMA_Short_Period": 21,
    "EMA_Proximity_ATR_Ratio": 0.15,
    "ATR_Period": 14,
    "LR_Length": 200,
    "LR_ATR_Length": 14,
    "LR_R2_Threshold": 0.3,
    "LR_Slope_Threshold": 0.5,
    "LR_Sideways_Slope_Threshold": 0.2,
    "LR_Volatility_MA_Length": 20,
    "LR_Higher_Timeframe": 240,
}


# ── Per-Client Session Store ──────────────────────────────────
# Each session: { thread, running, queue, config, data }
sessions = {}
sessions_lock = threading.Lock()


def get_session(session_id):
    """Get or create a session by ID."""
    with sessions_lock:
        if session_id not in sessions:
            sessions[session_id] = {
                "thread": None,
                "running": False,
                "queue": queue.Queue(maxsize=500),
                "config": None,
                "data": {},
            }
        return sessions[session_id]


def push_event(session_id, event_type, data):
    """Push an SSE event to a specific client's queue."""
    with sessions_lock:
        session = sessions.get(session_id)
        if not session:
            return
    try:
        event_data = json.dumps({"type": event_type, "data": data})
        session["queue"].put_nowait(event_data)
    except queue.Full:
        pass  # Drop events if queue is full


# ── Screener Loop (runs per-client) ──────────────────────────
def screener_loop(session_id):
    """Run analysis for a specific client session using their config."""
    session = get_session(session_id)

    push_event(session_id, "status", {"running": True})
    push_event(session_id, "log", {"message": "Screener started.", "level": "success"})

    while session["running"]:
        config = session.get("config")
        if not config:
            push_event(session_id, "log", {
                "message": "No config received. Stopping.",
                "level": "error"
            })
            break

        # Read config values
        assets           = config.get("Assets", [])
        timeframe_mins   = config.get("Timeframe", 15)
        rsi_period       = config.get("RSI_Period", 14)
        rsi_overbought   = config.get("RSI_Overbought", 70)
        rsi_oversold     = config.get("RSI_Oversold", 30)

        stoch_k_period   = config.get("Stoch_K_Period", 14)
        stoch_k_smooth   = config.get("Stoch_K_Smooth", 3)
        stoch_d_smooth   = config.get("Stoch_D_Smooth", 3)
        stoch_overbought = config.get("Stoch_Overbought", 80)
        stoch_oversold   = config.get("Stoch_Oversold", 20)
        stoch_alert_method = config.get("Stoch_Alert_Method", 1)

        ema_long_period  = config.get("EMA_Long_Period", 200)
        ema_short_period = config.get("EMA_Short_Period", 21)
        ema_proximity_atr_ratio = config.get("EMA_Proximity_ATR_Ratio", 0.5)
        atr_period       = config.get("ATR_Period", 14)

        lr_config = {
            "length": config.get("LR_Length", 200),
            "atr_length": config.get("LR_ATR_Length", 14),
            "r2_threshold": config.get("LR_R2_Threshold", 0.3),
            "slope_threshold": config.get("LR_Slope_Threshold", 0.5),
            "sideways_slope_threshold": config.get("LR_Sideways_Slope_Threshold", 0.2),
            "volatility_ma_length": config.get("LR_Volatility_MA_Length", 20),
        }
        lr_higher_tf = config.get("LR_Higher_Timeframe", 240)
        lr_higher_interval_str = get_interval_str(lr_higher_tf)

        interval_str = get_interval_str(timeframe_mins)

        results = {}
        refreshed_count = 0

        for symbol in assets:
            if not session["running"]:
                return

            push_event(session_id, "log", {
                "message": f"Fetching data for {symbol}...",
                "level": "info"
            })

            time.sleep(0.15)  # Rate limit
            raw_data = fetch_kline_data(symbol, interval_str)

            highs, lows, closes = [], [], []
            if raw_data:
                highs, lows, closes = parse_ohlc(raw_data)
                if closes:
                    refreshed_count += 1
                else:
                    push_event(session_id, "log", {
                        "message": f"{symbol}: Failed to parse data.",
                        "level": "error"
                    })
            else:
                push_event(session_id, "log", {
                    "message": f"{symbol}: Failed to fetch data.",
                    "level": "error"
                })

            if not closes:
                continue

            prices = closes
            h_data = highs
            l_data = lows
            current_price = prices[-1] if prices else None

            result = {"symbol": symbol, "price": current_price, "alerts": []}

            # ── RSI ──
            rsi = calculate_rsi(prices, rsi_period)
            if rsi is not None:
                result["rsi"] = round(rsi, 2)
                if rsi > rsi_overbought:
                    result["alerts"].append({"type": "RSI OVERBOUGHT", "level": "danger"})
                elif rsi < rsi_oversold:
                    result["alerts"].append({"type": "RSI OVERSOLD", "level": "success"})
            else:
                result["rsi"] = None
                result["rsi_note"] = "Not enough data"

            # ── EMA Long ──
            ema_long = calculate_ema(prices, ema_long_period)
            if ema_long is not None and current_price is not None:
                result["ema_long"] = round(ema_long, 4)
                result["ema_long_position"] = "ABOVE" if current_price > ema_long else "BELOW"
            else:
                result["ema_long"] = None
                result["ema_long_note"] = f"Not old enough for EMA({ema_long_period})"

            # ── EMA Short + ATR Proximity ──
            ema_short = calculate_ema(prices, ema_short_period)
            if ema_short is not None and current_price is not None:
                result["ema_short"] = round(ema_short, 4)
                atr = calculate_atr(h_data, l_data, prices, atr_period)
                distance = abs(current_price - ema_short)
                pos = "above" if current_price > ema_short else "below"

                if atr is not None and atr > 0:
                    atr_ratio = distance / atr
                    result["atr"] = round(atr, 4)
                    result["atr_ratio"] = round(atr_ratio, 2)
                    if atr_ratio <= ema_proximity_atr_ratio:
                        result["ema_proximity"] = f"Price is {pos} EMA({ema_short_period})"
                        result["alerts"].append({
                            "type": f"EMA({ema_short_period}) Proximity",
                            "level": "warning"
                        })
                else:
                    result["atr"] = None
                    result["atr_ratio"] = None
            else:
                result["ema_short"] = None
                result["ema_short_note"] = f"Not old enough for EMA({ema_short_period})"

            # ── Stochastic ──
            if h_data and l_data:
                stoch_k, stoch_d = calculate_stochastic(
                    h_data, l_data, prices,
                    stoch_k_period, stoch_k_smooth, stoch_d_smooth
                )
                if stoch_k is not None and stoch_d is not None:
                    result["stoch_k"] = round(stoch_k, 2)
                    result["stoch_d"] = round(stoch_d, 2)

                    is_ob = stoch_k > stoch_overbought or stoch_d > stoch_overbought
                    is_os = stoch_k < stoch_oversold or stoch_d < stoch_oversold

                    if stoch_alert_method == 1:
                        if is_ob:
                            result["alerts"].append({"type": "STOCH OVERBOUGHT", "level": "danger"})
                        elif is_os:
                            result["alerts"].append({"type": "STOCH OVERSOLD", "level": "success"})
                    elif stoch_alert_method == 2:
                        if ema_long is None:
                            if is_ob:
                                result["alerts"].append({"type": "STOCH OVERBOUGHT", "level": "danger"})
                            elif is_os:
                                result["alerts"].append({"type": "STOCH OVERSOLD", "level": "success"})
                        else:
                            if is_os and current_price > ema_long:
                                result["alerts"].append({
                                    "type": f"STOCH OVERSOLD + Above EMA({ema_long_period})",
                                    "level": "success"
                                })
                            elif is_ob and current_price < ema_long:
                                result["alerts"].append({
                                    "type": f"STOCH OVERBOUGHT + Below EMA({ema_long_period})",
                                    "level": "danger"
                                })
                else:
                    result["stoch_k"] = None
                    result["stoch_d"] = None
                    result["stoch_note"] = "Not enough data"

            # ── Linear Regression (Default TF) ──
            lr_result = classify_trend(prices, h_data, l_data, lr_config)
            if lr_result:
                tf_label = get_interval_str(timeframe_mins)
                result["lr_trend"] = lr_result["trend"]
                result["lr_confidence"] = round(lr_result["confidence"], 4)
                result["lr_r_squared"] = round(lr_result["r_squared"], 4)
                result["lr_norm_slope"] = round(lr_result["normalized_slope"], 4)
                result["lr_volatility"] = lr_result["volatility_regime"]
                result["lr_tf_label"] = tf_label
            else:
                result["lr_trend"] = None
                result["lr_note"] = "Not enough data"

            # ── Linear Regression (Higher TF) ──
            if lr_higher_tf != timeframe_mins:
                time.sleep(0.15)  # Rate limit
                htf_raw = fetch_kline_data(symbol, lr_higher_interval_str, count=lr_config["length"] + 50)
                if htf_raw:
                    htf_highs, htf_lows, htf_closes = parse_ohlc(htf_raw)
                    if htf_closes:
                        lr_htf = classify_trend(htf_closes, htf_highs, htf_lows, lr_config)
                        if lr_htf:
                            result["lr_htf_trend"] = lr_htf["trend"]
                            result["lr_htf_confidence"] = round(lr_htf["confidence"], 4)
                            result["lr_htf_r_squared"] = round(lr_htf["r_squared"], 4)
                            result["lr_htf_volatility"] = lr_htf["volatility_regime"]
                            result["lr_htf_label"] = lr_higher_interval_str
                        else:
                            result["lr_htf_trend"] = None
                            result["lr_htf_note"] = "Not enough data"
                    else:
                        result["lr_htf_trend"] = None
                        result["lr_htf_note"] = "Failed to parse"
                else:
                    result["lr_htf_trend"] = None
                    result["lr_htf_note"] = "Failed to fetch"

            results[symbol] = result
            push_event(session_id, "asset_update", result)

        session["data"] = results
        push_event(session_id, "cycle_complete", {
            "count": refreshed_count,
            "total": len(assets),
            "timestamp": datetime.now().strftime("%H:%M:%S")
        })

        # ── Countdown ──
        now_ts = int(time.time())
        interval_seconds = timeframe_mins * 60
        next_interval = ((now_ts // interval_seconds) + 1) * interval_seconds
        sleep_seconds = next_interval - now_ts

        for i in range(sleep_seconds, 0, -1):
            if not session["running"]:
                return
            if i % 5 == 0 or i <= 10:
                push_event(session_id, "countdown", {"seconds_left": i})
            time.sleep(1)

        push_event(session_id, "log", {"message": "Checking for updates...", "level": "info"})

    push_event(session_id, "status", {"running": False})
    push_event(session_id, "log", {"message": "Screener stopped.", "level": "warning"})


# ── Cleanup stale sessions ────────────────────────────────────
def cleanup_sessions():
    """Remove sessions that haven't been active for 30+ minutes."""
    while True:
        time.sleep(300)  # Check every 5 minutes
        now = time.time()
        with sessions_lock:
            stale = [
                sid for sid, s in sessions.items()
                if not s["running"] and s.get("last_active", 0) < now - 1800
            ]
            for sid in stale:
                del sessions[sid]


cleanup_thread = threading.Thread(target=cleanup_sessions, daemon=True)
cleanup_thread.start()


# ── Flask Routes ──────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/defaults', methods=['GET'])
def get_defaults():
    """Return the default config for first-time visitors."""
    return jsonify(DEFAULT_CONFIG)


@app.route('/api/start', methods=['POST'])
def start_screener():
    """Start a screener for a specific client session.
    Expects JSON body: { session_id: "...", config: { ... } }
    """
    body = request.get_json()
    session_id = body.get("session_id")
    config = body.get("config")

    if not session_id or not config:
        return jsonify({"error": "Missing session_id or config"}), 400

    session = get_session(session_id)
    session["last_active"] = time.time()

    if session["running"]:
        return jsonify({"status": "already_running"})

    session["config"] = config
    session["running"] = True
    session["data"] = {}

    t = threading.Thread(target=screener_loop, args=(session_id,), daemon=True)
    session["thread"] = t
    t.start()

    return jsonify({"status": "started"})


@app.route('/api/stop', methods=['POST'])
def stop_screener():
    """Stop a specific client's screener."""
    body = request.get_json()
    session_id = body.get("session_id")
    if not session_id:
        return jsonify({"error": "Missing session_id"}), 400

    session = get_session(session_id)
    session["running"] = False
    session["last_active"] = time.time()
    return jsonify({"status": "stopped"})


@app.route('/api/reset', methods=['POST'])
def reset_screener():
    """Reset a specific client's screener."""
    body = request.get_json()
    session_id = body.get("session_id")
    if not session_id:
        return jsonify({"error": "Missing session_id"}), 400

    session = get_session(session_id)
    session["running"] = False
    time.sleep(1.5)
    session["data"] = {}
    session["last_active"] = time.time()

    push_event(session_id, "reset", {})
    push_event(session_id, "log", {"message": "Data reset complete.", "level": "warning"})
    return jsonify({"status": "reset_complete"})


@app.route('/stream')
def stream():
    """SSE endpoint — per-client event stream."""
    session_id = request.args.get("session_id")
    if not session_id:
        return Response("Missing session_id", status=400)

    session = get_session(session_id)
    session["last_active"] = time.time()

    def event_stream():
        q = session["queue"]
        try:
            while True:
                try:
                    data = q.get(timeout=25)
                    yield f"data: {data}\n\n"
                except queue.Empty:
                    yield f"data: {json.dumps({'type': 'heartbeat', 'data': {}})}\n\n"
        except GeneratorExit:
            pass

    resp = Response(event_stream(), mimetype='text/event-stream')
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['X-Accel-Buffering'] = 'no'
    resp.headers['Connection'] = 'keep-alive'
    return resp


if __name__ == '__main__':
    print("=" * 50)
    print("  MEXC RSI Screener — Web Dashboard")
    print("  Open http://localhost:5000 in your browser")
    print("=" * 50)
    app.run(debug=False, host='127.0.0.1', port=5000, threaded=True)
