# -*- coding: utf-8 -*-
import requests
import json
import time
import os
import sys
import numpy as np
from datetime import datetime
try:
    from plyer import notification
except ImportError:
    notification = None  # Desktop notifications unavailable (e.g. on server)

# File paths
CONFIG_FILE = 'config.json'
DATA_FILE = 'market_data.json'

# MEXC API URL
BASE_URL = "https://contract.mexc.com"
KLINE_ENDPOINT = "/api/v1/contract/kline/{symbol}"

def send_notification(symbol, rsi_value, condition):
    """Send a Windows notification."""
    if notification is None:
        return  # plyer not available (server environment)
    try:
        notification.notify(
            title=f"MEXC RSI ALERT: {symbol}",
            message=f"{symbol} is {condition}! RSI: {rsi_value:.2f}",
            app_name="MEXC Screener",
            timeout=10
        )
    except Exception as e:
        print(f"[ERROR] Failed to send notification: {e}")

def load_config():
    """Load configuration from json file."""
    if not os.path.exists(CONFIG_FILE):
        print(f"[ERROR] {CONFIG_FILE} not found!")
        return None
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"[ERROR] Failed to load config: {e}")
        return None

def load_market_data(current_timeframe):
    """Load cached market data from json file."""
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, 'r') as f:
            data = json.load(f)
            
        # Check metadata
        meta = data.get("_metadata", {})
        cached_timeframe = meta.get("timeframe")
        
        if cached_timeframe != current_timeframe:
            print(f"[INFO] Timeframe changed ({cached_timeframe} -> {current_timeframe}). Wiping cache.")
            return {}
            
        return data
    except Exception as e:
        print(f"[WARNING] Failed to load market data, starting fresh: {e}")
        return {}

def save_market_data(data):
    """Save market data to json file."""
    try:
        with open(DATA_FILE, 'w') as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"[ERROR] Failed to save market data: {e}")

def get_interval_str(minutes):
    """Convert minutes integer to MEXC interval string."""
    mapping = {
        1: "Min1",
        5: "Min5",
        15: "Min15",
        30: "Min30",
        60: "Min60",
        240: "Hour4",
        480: "Hour8",
        1440: "Day1",
        10080: "Week1",
        43200: "Month1"
    }
    return mapping.get(minutes, "Min15") # Default to Min15 if not found

def fetch_kline_data(symbol, interval_str, count=250):
    """Fetch kline data from MEXC."""
    # Calculate time range
    # MEXC API uses seconds for timestamps
    end_time = int(time.time())
    
    # Estimate start time to ensure we get at least 'count' candles.
    # We add some buffer. 
    # Note: Interval parsing logic would be needed for exact calculation, 
    # but for simplicity we'll just request a large enough window or rely on the API 
    # returning the available data in that range.
    # However, MEXC 'kline' endpoint behavior with just start/end:
    # We need to map interval_str back to seconds to calculate start time.
    
    interval_seconds_map = {
        "Min1": 60, "Min5": 300, "Min15": 900, "Min30": 1800,
        "Min60": 3600, "Hour4": 14400, "Hour8": 28800,
        "Day1": 86400, "Week1": 604800, "Month1": 2592000
    }
    interval_seconds = interval_seconds_map.get(interval_str, 900)
    
    start_time = end_time - (count * interval_seconds) 

    url = f"{BASE_URL}{KLINE_ENDPOINT.format(symbol=symbol)}"
    params = {
        "interval": interval_str,
        "start": start_time,
        "end": end_time
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }
    
    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data and data.get("success") and "data" in data:
                # data["data"] is likely lists of [time, open, low, high, close] or similar
                # We assume standard OHLCV or similar.
                # Documentation says: Get K-Line Data. Response usually contains lists.
                # Let's verify structure if possible, but mostly it's:
                # [time, open, close, high, low, vol, amount] usually.
                # But let's assume standard based on usage.
                # Actually, typically it's specific keys or a list of values. 
                # Contracts API usually returns: 
                # { "success": true, "code": 0, "data": { "time": [...], "open": [...], ... } } OR list of objects.
                # Let's look at standard MEXC contract kline response structure.
                # Since documentation was read but didn't show response example, 
                # I will implement robust handling or assume list of lists/dicts.
                # Common pattern for MEXC Contract is: 
                # data: { "time": [t1, t2], "close": [c1, c2], ... } -> Column based?
                # OR List of candles.
                # Let's treat it as if we need to inspect it. 
                # I'll add a print for the first run if needed, but standard 'kline' usually returns list of [t, o, h, l, c, v].
                # Wait, MEXC Contract API often returns:
                # { ... "data": { "time": [...], "high": [...], "low": [...], "close": [...], "open": [...], "vol": [...], "amount": [...] } }
                # Let's code for this Column-Oriented structure which is common in high-perf APIs, 
                # BUT standard CCXT/others normalize it. 
                # I'll assume it returns a list of dictionaries based on recent patterns or the column-structure.
                # Let's assume Column-Oriented based on "contract" API usually being optimized.
                # I'll check the return type at runtime or start with a safe guess.
                # Actually, I'll write a small helper to detecting the format.
                
                return data["data"]
            else:
                print(f"[ERROR] API returned error for {symbol}: {data}")
                return None
        else:
            print(f"[ERROR] HTTP Error {response.status_code} for {symbol}")
            return None
    except Exception as e:
        print(f"[ERROR] Exception fetching {symbol}: {e}")
        return None

def calculate_rsi(prices, period=14):
    """Calculate RSI from a list of prices."""
    if len(prices) < period + 1:
        return None
    
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    # Smoothed RSI
    for i in range(period, len(prices) - 1):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        
    if avg_loss == 0:
        return 100.0
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calculate_stochastic(highs, lows, closes, k_period=14, k_smooth=3, d_smooth=3):
    """Calculate Stochastic %K and %D.
    Returns (smoothed_K, D) tuple or (None, None) if not enough data.
    """
    min_needed = k_period + k_smooth + d_smooth - 2
    if len(closes) < min_needed:
        return None, None
    
    # Step 1: Calculate raw %K values
    raw_k_values = []
    for i in range(k_period - 1, len(closes)):
        highest_high = max(highs[i - k_period + 1 : i + 1])
        lowest_low = min(lows[i - k_period + 1 : i + 1])
        if highest_high == lowest_low:
            raw_k_values.append(100.0)
        else:
            raw_k = ((closes[i] - lowest_low) / (highest_high - lowest_low)) * 100
            raw_k_values.append(raw_k)
    
    # Step 2: Smooth %K with SMA of k_smooth period
    if len(raw_k_values) < k_smooth:
        return None, None
    smoothed_k_values = []
    for i in range(k_smooth - 1, len(raw_k_values)):
        avg = sum(raw_k_values[i - k_smooth + 1 : i + 1]) / k_smooth
        smoothed_k_values.append(avg)
    
    # Step 3: Calculate %D as SMA of smoothed %K with d_smooth period
    if len(smoothed_k_values) < d_smooth:
        return None, None
    d_values = []
    for i in range(d_smooth - 1, len(smoothed_k_values)):
        avg = sum(smoothed_k_values[i - d_smooth + 1 : i + 1]) / d_smooth
        d_values.append(avg)
    
    return smoothed_k_values[-1], d_values[-1]

def calculate_ema(prices, period):
    """Calculate EMA from a list of prices.
    Returns the latest EMA value, or None if not enough data.
    """
    if len(prices) < period + 20:
        return None  # Not enough warm-up data
    
    multiplier = 2 / (period + 1)
    # Seed EMA with SMA of first 'period' prices
    ema = sum(prices[:period]) / period
    
    for price in prices[period:]:
        ema = (price - ema) * multiplier + ema
    
    return ema

def calculate_atr(highs, lows, closes, period=14):
    """Calculate Average True Range (ATR) using Wilder's smoothing.
    Returns the latest ATR value, or None if not enough data.
    """
    if len(closes) < period + 1:
        return None
    
    # Calculate True Range series
    true_ranges = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )
        true_ranges.append(tr)
    
    if len(true_ranges) < period:
        return None
    
    # Seed ATR with SMA of first 'period' true ranges
    atr = sum(true_ranges[:period]) / period
    
    # Wilder's smoothing
    for i in range(period, len(true_ranges)):
        atr = (atr * (period - 1) + true_ranges[i]) / period
    
    return atr

def parse_ohlc(kline_data):
    """Extract high, low, and close prices from kline data.
    Returns (highs, lows, closes) tuple or (None, None, None) on failure.
    """
    try:
        # Column-based format: {"close": [...], "high": [...], "low": [...], ...}
        if isinstance(kline_data, dict):
            if ("close" in kline_data and isinstance(kline_data["close"], list)
                    and "high" in kline_data and isinstance(kline_data["high"], list)
                    and "low" in kline_data and isinstance(kline_data["low"], list)):
                highs = [float(p) for p in kline_data["high"]]
                lows = [float(p) for p in kline_data["low"]]
                closes = [float(p) for p in kline_data["close"]]
                return highs, lows, closes
        
        # Row-based format: list of dicts or list of lists
        if isinstance(kline_data, list):
            if len(kline_data) == 0:
                return [], [], []
            
            first_item = kline_data[0]
            
            if isinstance(first_item, dict):
                if all(k in first_item for k in ["close", "high", "low"]):
                    highs = [float(x["high"]) for x in kline_data]
                    lows = [float(x["low"]) for x in kline_data]
                    closes = [float(x["close"]) for x in kline_data]
                    return highs, lows, closes
                if all(k in first_item for k in ["c", "h", "l"]):
                    highs = [float(x["h"]) for x in kline_data]
                    lows = [float(x["l"]) for x in kline_data]
                    closes = [float(x["c"]) for x in kline_data]
                    return highs, lows, closes
        
        return None, None, None
    except Exception as e:
        print(f"[ERROR] Parsing OHLC data failed: {e}")
        return None, None, None

def calculate_linear_regression(closes, length):
    """Compute linear regression over the last 'length' closing prices.
    Uses numpy polyfit for vectorized least-squares fit.
    Returns (slope, intercept, r_squared) or (None, None, None) if insufficient data.
    """
    if closes is None or len(closes) < length:
        return None, None, None
    
    y = np.array(closes[-length:], dtype=np.float64)
    x = np.arange(length, dtype=np.float64)
    
    # Least-squares linear fit: y = slope * x + intercept
    slope, intercept = np.polyfit(x, y, 1)
    
    # R-squared: coefficient of determination
    y_pred = slope * x + intercept
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    
    if ss_tot == 0:
        r_squared = 0.0
    else:
        r_squared = 1.0 - (ss_res / ss_tot)
    
    return slope, intercept, r_squared

def compute_atr_series(highs, lows, closes, period):
    """Compute a full ATR series using Wilder's smoothing.
    Returns a list of ATR values (one per candle starting from index 'period'),
    or an empty list if insufficient data.
    """
    if len(closes) < period + 1:
        return []
    
    # True range series (starts from index 1)
    true_ranges = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )
        true_ranges.append(tr)
    
    if len(true_ranges) < period:
        return []
    
    # Seed with SMA
    atr = sum(true_ranges[:period]) / period
    atr_values = [atr]
    
    # Wilder's smoothing for the rest
    for i in range(period, len(true_ranges)):
        atr = (atr * (period - 1) + true_ranges[i]) / period
        atr_values.append(atr)
    
    return atr_values

def classify_trend(closes, highs, lows, lr_config):
    """Perform linear regression trend classification on a single asset/timeframe.
    
    lr_config is a dict with keys:
        length, atr_length, r2_threshold, slope_threshold,
        sideways_slope_threshold, volatility_ma_length
    
    Returns a dict with:
        slope, normalized_slope, r_squared, atr, volatility_regime,
        trend, confidence
    Or None if data is insufficient.
    """
    length = lr_config["length"]
    atr_length = lr_config["atr_length"]
    r2_threshold = lr_config["r2_threshold"]
    slope_threshold = lr_config["slope_threshold"]
    sideways_slope_threshold = lr_config["sideways_slope_threshold"]
    volatility_ma_length = lr_config["volatility_ma_length"]
    
    # --- Linear regression ---
    slope, intercept, r_squared = calculate_linear_regression(closes, length)
    if slope is None:
        return None
    
    # --- ATR series for normalization and volatility regime ---
    atr_series = compute_atr_series(highs, lows, closes, atr_length)
    if not atr_series:
        return None
    
    current_atr = atr_series[-1]
    
    # Guard against zero / near-zero ATR
    if current_atr < 1e-12:
        return None
    
    normalized_slope = slope / current_atr
    
    # --- Volatility regime ---
    if len(atr_series) >= volatility_ma_length:
        atr_ma = sum(atr_series[-volatility_ma_length:]) / volatility_ma_length
        volatility_regime = "HIGH" if current_atr > atr_ma else "LOW"
    else:
        # Not enough ATR history for MA, default to unknown
        volatility_regime = "N/A"
    
    # --- Trend classification ---
    abs_norm_slope = abs(normalized_slope)
    
    if abs_norm_slope < sideways_slope_threshold or r_squared < r2_threshold:
        trend = "Sideways"
    elif normalized_slope > slope_threshold and r_squared >= r2_threshold:
        trend = "Uptrend"
    elif normalized_slope < -slope_threshold and r_squared >= r2_threshold:
        trend = "Downtrend"
    else:
        trend = "Sideways"
    
    # --- Confidence score: r² * min(|norm_slope|, 1.0), clamped [0, 1] ---
    confidence = r_squared * min(abs_norm_slope, 1.0)
    confidence = max(0.0, min(1.0, confidence))
    
    return {
        "slope": slope,
        "normalized_slope": normalized_slope,
        "r_squared": r_squared,
        "atr": current_atr,
        "volatility_regime": volatility_regime,
        "trend": trend,
        "confidence": confidence,
    }

def format_confidence_label(confidence):
    """Return a color-coded text label for confidence score."""
    if confidence >= 0.7:
        return f"\033[92m{confidence:.4f} (Strong)\033[0m"
    elif confidence >= 0.4:
        return f"\033[96m{confidence:.4f} (Moderate)\033[0m"
    elif confidence >= 0.15:
        return f"\033[93m{confidence:.4f} (Weak)\033[0m"
    else:
        return f"\033[90m{confidence:.4f} (Very Weak)\033[0m"

def format_r2_label(r2):
    """Return a color-coded text label for R² value."""
    if r2 >= 0.7:
        return f"\033[92m{r2:.4f} (Excellent fit)\033[0m"
    elif r2 >= 0.4:
        return f"\033[96m{r2:.4f} (Good fit)\033[0m"
    elif r2 >= 0.2:
        return f"\033[93m{r2:.4f} (Fair fit)\033[0m"
    else:
        return f"\033[90m{r2:.4f} (Poor fit)\033[0m"

def format_volatility_label(regime):
    """Return a color-coded volatility regime label."""
    if regime == "HIGH":
        return f"\033[91mHIGH\033[0m"
    elif regime == "LOW":
        return f"\033[96mLOW\033[0m"
    else:
        return f"\033[90mN/A\033[0m"

def print_lr_result(symbol, tf_label, lr_result):
    """Print a formatted, multi-line linear regression result block."""
    trend_color = {"Uptrend": "\033[92m", "Downtrend": "\033[91m", "Sideways": "\033[93m"}
    tc = trend_color.get(lr_result['trend'], "")
    
    print(f"   ┌─ LR({tf_label})")
    print(f"   │  Trend:       {tc}{lr_result['trend']}\033[0m")
    print(f"   │  Slope:       {lr_result['slope']:.6f}  (normalized: {lr_result['normalized_slope']:.4f})")
    print(f"   │  R²:          {format_r2_label(lr_result['r_squared'])}")
    print(f"   │  ATR:         {lr_result['atr']:.4f}")
    print(f"   │  Volatility:  {format_volatility_label(lr_result['volatility_regime'])}")
    print(f"   │  Confidence:  {format_confidence_label(lr_result['confidence'])}")
    print(f"   └─")

def main():
    # Ensure Unicode box-drawing characters display properly on Windows
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding='utf-8')
    
    print("--- MEXC RSI Screener Bot Started ---")
    
    while True:
        config = load_config()
        if not config:
            print("Config missing or invalid. Retrying in 60s...")
            time.sleep(60)
            continue
            
        assets = config.get("Assets", [])
        timeframe_mins = config.get("Timeframe", 15)
        rsi_period = config.get("RSI_Period", 14)
        rsi_overbought = config.get("RSI_Overbought", 70)
        rsi_oversold = config.get("RSI_Oversold", 30)
        
        # Stochastic settings
        stoch_k_period = config.get("Stoch_K_Period", 14)
        stoch_k_smooth = config.get("Stoch_K_Smooth", 3)
        stoch_d_smooth = config.get("Stoch_D_Smooth", 3)
        stoch_overbought = config.get("Stoch_Overbought", 80)
        stoch_oversold = config.get("Stoch_Oversold", 20)
        stoch_alert_method = config.get("Stoch_Alert_Method", 1)
        
        # EMA settings
        ema_long_period = config.get("EMA_Long_Period", 200)
        ema_short_period = config.get("EMA_Short_Period", 21)
        ema_proximity_atr_ratio = config.get("EMA_Proximity_ATR_Ratio", 0.5)
        
        # ATR settings
        atr_period = config.get("ATR_Period", 14)
        
        # Linear Regression settings
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
        market_data = load_market_data(timeframe_mins)
        
        refreshed_count = 0
        
        for symbol in assets:
            cached_asset = market_data.get(symbol, {})
            last_updated = cached_asset.get("last_updated", 0)
            now = time.time()
            
            print(f"\n{'═' * 50}")
            print(f" ■ {symbol}  [{datetime.now().strftime('%H:%M:%S')}]")
            print(f"{'═' * 50}")
            
            time.sleep(0.15)  # Rate limit: MEXC allows 20 req/2s, this keeps us safely under
            raw_data = fetch_kline_data(symbol, interval_str)
            if raw_data:
                highs, lows, closes = parse_ohlc(raw_data)
                
                if closes:
                    market_data[symbol] = {
                        "last_updated": now,
                        "highs": highs,
                        "lows": lows,
                        "prices": closes,
                    }
                    refreshed_count += 1
                else:
                    print(f" > {symbol}: Failed to parse updated data.")
            else:
                print(f" > {symbol}: Failed to fetch data.")

            current_asset_data = market_data.get(symbol)
            if not current_asset_data or "prices" not in current_asset_data:
                if now - last_updated < (timeframe_mins * 60):
                    pass
                continue
            
            prices = current_asset_data["prices"]
            highs = current_asset_data.get("highs", [])
            lows = current_asset_data.get("lows", [])
            current_price = prices[-1] if prices else None
            
            # --- RSI Analysis ---
            print(f"\n ┌─ RSI")
            rsi = calculate_rsi(prices, rsi_period)
            if rsi is not None:
                print(f" │  RSI({rsi_period}): {rsi:.2f}")
                if rsi > rsi_overbought:
                    print(f" │  \033[91m[ALERT] RSI {rsi:.2f} (> {rsi_overbought}) — OVERBOUGHT!\033[0m")
                    send_notification(symbol, rsi, "RSI OVERBOUGHT")
                elif rsi < rsi_oversold:
                    print(f" │  \033[92m[ALERT] RSI {rsi:.2f} (< {rsi_oversold}) — OVERSOLD!\033[0m")
                    send_notification(symbol, rsi, "RSI OVERSOLD")
            else:
                print(f" │  Not enough data for RSI")
            print(f" └─")
            
            # --- EMA Analysis ---
            print(f"\n ┌─ EMA")
            ema_long = calculate_ema(prices, ema_long_period)
            ema_short = calculate_ema(prices, ema_short_period)
            
            if ema_long is not None and current_price is not None:
                position_long = "ABOVE" if current_price > ema_long else "BELOW"
                pos_color = "\033[92m" if current_price > ema_long else "\033[91m"
                print(f" │  EMA({ema_long_period}): {ema_long:.4f}  →  Price is {pos_color}{position_long}\033[0m")
            else:
                print(f" │  EMA({ema_long_period}): Not enough data")
            
            if ema_short is not None and current_price is not None:
                atr = calculate_atr(highs, lows, prices, atr_period)
                distance = abs(current_price - ema_short)
                position_short = "above" if current_price > ema_short else "below"
                
                if atr is not None and atr > 0:
                    atr_ratio = distance / atr
                    print(f" │  EMA({ema_short_period}): {ema_short:.4f}  →  Distance/ATR: {atr_ratio:.2f}")
                    if atr_ratio <= ema_proximity_atr_ratio:
                        print(f" │  \033[93m[INFO] Price is close to EMA({ema_short_period}), {position_short} it\033[0m")
                else:
                    print(f" │  EMA({ema_short_period}): {ema_short:.4f}  →  ATR: not enough data")
            else:
                print(f" │  EMA({ema_short_period}): Not enough data")
            print(f" └─")
            
            # --- Stochastic Analysis ---
            print(f"\n ┌─ Stochastic")
            if highs and lows:
                stoch_k, stoch_d = calculate_stochastic(highs, lows, prices, stoch_k_period, stoch_k_smooth, stoch_d_smooth)
                
                if stoch_k is not None and stoch_d is not None:
                    print(f" │  %K: {stoch_k:.2f}  |  %D: {stoch_d:.2f}")
                    
                    stoch_is_overbought = stoch_k > stoch_overbought or stoch_d > stoch_overbought
                    stoch_is_oversold = stoch_k < stoch_oversold or stoch_d < stoch_oversold
                    
                    if stoch_alert_method == 1:
                        if stoch_is_overbought:
                            print(f" │  \033[91m[ALERT] %K:{stoch_k:.2f} %D:{stoch_d:.2f} (> {stoch_overbought}) — OVERBOUGHT!\033[0m")
                            send_notification(symbol, stoch_k, "STOCH OVERBOUGHT")
                        elif stoch_is_oversold:
                            print(f" │  \033[92m[ALERT] %K:{stoch_k:.2f} %D:{stoch_d:.2f} (< {stoch_oversold}) — OVERSOLD!\033[0m")
                            send_notification(symbol, stoch_d, "STOCH OVERSOLD")
                    
                    elif stoch_alert_method == 2:
                        if ema_long is None:
                            print(f" │  [INFO] Method 2 requires EMA({ema_long_period}), falling back to method 1")
                            if stoch_is_overbought:
                                print(f" │  \033[91m[ALERT] %K:{stoch_k:.2f} %D:{stoch_d:.2f} (> {stoch_overbought}) — OVERBOUGHT!\033[0m")
                                send_notification(symbol, stoch_k, "STOCH OVERBOUGHT")
                            elif stoch_is_oversold:
                                print(f" │  \033[92m[ALERT] %K:{stoch_k:.2f} %D:{stoch_d:.2f} (< {stoch_oversold}) — OVERSOLD!\033[0m")
                                send_notification(symbol, stoch_d, "STOCH OVERSOLD")
                        else:
                            if stoch_is_oversold and current_price > ema_long:
                                print(f" │  \033[92m[ALERT] %K:{stoch_k:.2f} %D:{stoch_d:.2f} (< {stoch_oversold}) + Above EMA({ema_long_period}) — OVERSOLD BUY!\033[0m")
                                send_notification(symbol, stoch_d, f"STOCH OVERSOLD + Above EMA({ema_long_period})")
                            elif stoch_is_oversold and current_price <= ema_long:
                                print(f" │  \033[90m[INFO] Oversold but below EMA({ema_long_period}) — filtered\033[0m")
                            
                            if stoch_is_overbought and current_price < ema_long:
                                print(f" │  \033[91m[ALERT] %K:{stoch_k:.2f} %D:{stoch_d:.2f} (> {stoch_overbought}) + Below EMA({ema_long_period}) — OVERBOUGHT SELL!\033[0m")
                                send_notification(symbol, stoch_d, f"STOCH OVERBOUGHT + Below EMA({ema_long_period})")
                            elif stoch_is_overbought and current_price >= ema_long:
                                print(f" │  \033[90m[INFO] Overbought but above EMA({ema_long_period}) — filtered\033[0m")
                else:
                    print(f" │  Not enough data for Stochastic")
            else:
                print(f" │  No high/low data available")
            print(f" └─")
            
            # --- Linear Regression Analysis (Default Timeframe) ---
            print()  # spacing
            tf_label = get_interval_str(timeframe_mins)
            lr_result = classify_trend(prices, highs, lows, lr_config)
            if lr_result:
                print_lr_result(symbol, tf_label, lr_result)
            else:
                print(f"   ┌─ LR({tf_label})")
                print(f"   │  Not enough data")
                print(f"   └─")
            
            # --- Linear Regression Analysis (Higher Timeframe) ---
            if lr_higher_tf != timeframe_mins:
                time.sleep(0.15)  # Rate limit guard
                htf_raw = fetch_kline_data(symbol, lr_higher_interval_str, count=lr_config["length"] + 50)
                if htf_raw:
                    htf_highs, htf_lows, htf_closes = parse_ohlc(htf_raw)
                    if htf_closes:
                        lr_htf_result = classify_trend(htf_closes, htf_highs, htf_lows, lr_config)
                        if lr_htf_result:
                            print_lr_result(symbol, lr_higher_interval_str, lr_htf_result)
                        else:
                            print(f"   ┌─ LR({lr_higher_interval_str})")
                            print(f"   │  Not enough data")
                            print(f"   └─")
                    else:
                        print(f"   ┌─ LR({lr_higher_interval_str})")
                        print(f"   │  Failed to parse data")
                        print(f"   └─")
                else:
                    print(f"   ┌─ LR({lr_higher_interval_str})")
                    print(f"   │  Failed to fetch data")
                    print(f"   └─")
        
        # Save updated data
        if refreshed_count > 0:
            market_data["_metadata"] = {"timeframe": timeframe_mins}
            save_market_data(market_data)
            print(f"\nRefreshed {refreshed_count} assets.")
        
        # Calculate time to next interval
        now_ts = int(time.time())
        interval_seconds = timeframe_mins * 60
        next_interval = ((now_ts // interval_seconds) + 1) * interval_seconds
        sleep_seconds = next_interval - now_ts
        
        print("-" * 30)
        # Countdown loop
        for i in range(sleep_seconds, 0, -1):
            print(f"\rNext check in {i}s...   ", end="", flush=True)
            time.sleep(1)
        print("\rChecking for updates...   ", end="", flush=True)
        print() 

if __name__ == "__main__":
    main()
