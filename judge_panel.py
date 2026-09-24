"""
judge_panel.py -- v7.1 add-on: turns Step-2 notes into a per-judge panel.
========================================================================

Optional and self-contained (standard library only). Two safe uses:
  * main.py calls parse_judge_panel(snapshot.notes) when writing the diary, so every
    judge vote (footprint, L3 net flow, iceberg, whale, VWAP, macro...) is recorded
    STRUCTURED in data/snapshots_history.jsonl from now on;
  * audit_day.py scores OLD diaries with the same function, so a day recorded before
    this patch can still be graded.

It never raises: a note matching no judge pattern is skipped, so a renamed or missing
note cannot break the trading loop. Delete this file and the diary field simply becomes
empty - nothing else changes.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List


_JUDGE_DEFAULT_W = {
    "footprint_delta": 1.0, "footprint_levels": 0.4, "l3_imbalance": 0.8,
    "l3_aggr_limit": 0.7, "l3_ofi_streak": 1.0, "l3_net_flow": 1.5,
    "l3_large_ofi": 0.6, "iceberg": 1.0, "iceberg_noise": 0.0,
    "iceberg_legacy": 0.6, "spoof_invert": 0.6, "spoof_invert_loose": 0.4,
    "whale_walls": 1.4, "whale_balanced": 0.0, "queue_pos": 0.5,
    "microprice": 0.6, "absorption": 0.6, "sweep": 0.9, "vwap_trend": 0.6,
    "vwap_bands": 0.8, "vwap_zscore": 0.5, "poc_day": 0.5,
    "supply_demand": 0.6, "value_area": 0.5, "htf_poc": 0.7,
    "cvd_divergence": 0.6, "cvd_momentum": 0.5, "delta_pressure": 0.6,
    "volume_roc": 0.4, "macro_yield": 0.8, "macro_dxy": 0.6, "macro_vix": 0.4,
    "macro_risk": 0.5, "news_sentiment": 1.0, "mtf": 0.5, "trend_macd": 0.5,
    "sma20": 0.4,
}

_JUDGE_PATTERNS = [
    # (judge, regex)  groups: (dir)(weight)  - "no vote" lines still score, dir=0
    ("footprint_delta",   r"footprint delta ([+-][\d.]+) dominant ([\d.]+) strength ([\d.]+) -> (BUY|SELL)"),
    ("footprint_levels",  r"footprint (buying|selling) levels (\d+) > (?:selling|buying) (\d+) -> (BUY|SELL)"),
    ("l3_imbalance",      r"(?:v6\.0 )?L3 (?:distance-weighted )?imbalance ([+-][\d.]+)(?:.*?-> (BUY|SELL))?"),
    ("l3_aggr_limit",     r"Aggressive vs limit: (buy|sell) ratio ([\d.]+) vol ([\d.]+) -> (BUY|SELL) power"),
    ("l3_ofi_streak",     r"L3 OFI time-weighted recent streak B(\d+)/S(\d+).*?-> weight ([\d.]+) ([+-][\d.]+)"),
    ("l3_net_flow",       r"L3 NET FLOW (BUY|SELL) ([+-][\d.]+) \(buys ([\d.]+) vs sells ([\d.]+)\) w ([\d.]+)"),
    ("l3_large_ofi",      r"L3 large orders (\d+) OFI ([+-][\d.]+) -> (BUY|SELL)"),
    ("iceberg",           r"ICEBERG_(SUPPORT|RESISTANCE) @?[ ]?([\d.]*)?.*?(?:refills (\d+))?.*?w ([\d.]+)(?: -> (BUY|SELL))?"),
    ("iceberg_noise",     r"ICEBERG weak (support|resistance) @ ([\d.]+).*?no vote"),
    ("iceberg_legacy",    r"L3 icebergs (\d+) imb ([+-][\d.]+) w ([\d.]+) -> (BUY|SELL)"),
    ("spoof_invert",      r"SPOOF_INVERT fake (bids|asks) (\d+).*?w ([\d.]+) -> (BUY|SELL)"),
    ("spoof_invert_loose", r"SPOOF_INVERT more fake (bids|asks) (\d+) vs"),
    ("whale_walls",       r"L3 whale (SUPPORT|RESISTANCE) (\d+) walls ([\d.]+) lots"),
    ("whale_balanced",    r"L3 whales balanced bid ([\d.]+) ask ([\d.]+)"),
    ("queue_pos",         r"QUEUE_POS good (bid|ask) ratio ([\d.]+)"),
    ("microprice",        r"microprice ([\d.]+) vs mid ([\d.]+) dev ([+-][\d.]+)bps -> (BUY|SELL)"),
    ("absorption",        r"absorption (?:net )?([+-][\d.]+).*?-> (BUY|SELL)"),
    ("sweep",             r"v6\.0 SWEEP (BULLISH|BEARISH).*?w([+-]?[\d.]+)"),
    ("vwap_trend",        r"VWAP trend (UP|DOWN) price ([\d.]+) vs VWAP ([\d.]+)"),
    ("vwap_bands",        r"v6\.0 VWAP (\+\d\.\d|\+\d|\+2\.5|\+2|\+1|-2\.5|-2|-1)\u03c3?.*?-> (BUY|SELL)"),
    ("vwap_zscore",       r"VWAP z-score ([+-]?[\d.]+)"),
    ("poc_day",           r"POC day ([\d.]+) price ([\d.]+) -> (above|below)"),
    ("supply_demand",     r"near (supply|demand) zone ([\d.]+)"),
    ("value_area",        r"near (VAH|VAL) ([\d.]+)|price ([\d.]+) (?:above VAH|below VAL) ([\d.]+)"),
    ("htf_poc",           r"HTF H(1|4) POC ([\d.]+) (?:far )?(above|below) price.*?magnet.*?-> (BUY|SELL)|(?:mean reversion|strong| ) (BUY|SELL)"),
    ("cvd_divergence",    r"(bullish|bearish) CVD divergence"),
    ("cvd_momentum",      r"CVD (rising|falling) delta ([+-]?[\d.]+) CVD ([+-]?[\d.]+)"),
    ("delta_pressure",    r"Delta (Buy|Sell)% ([\d.]+) >60% -> (BUY|SELL)"),
    ("volume_roc",        r"volume RoC \+([\d.]+)% with price (up|down) -> (bullish|bearish)"),
    ("macro_yield",       r"10Y (rising|falling) ([\d.]+)%/5d \(w ([\d.]+)\)"),
    ("macro_dxy",         r"DXY (rising|falling) ([\d.]+)%/5d \(w ([\d.]+)\)"),
    ("macro_vix",         r"VIX stress \+([\d.]+)%"),
    ("macro_risk",        r"risk-(off|on)"),
    ("news_sentiment",    r"HIGH impact sentiment ([+-][\d.]+) weight ([\d.]+)"),
    ("mtf",               r"^MTF (.+)"),
    ("trend_macd",        r"^(?:v6\.0 )?trend[^\n]*MACD[^\n]*"),
    ("sma20",             r"^SMA20[^\n]*"),
]


def parse_judge_panel(notes: List[str]) -> List[Dict[str, Any]]:
    """Derive the per-judge panel from the Step-2 notes.

    Returns [{judge, dir (-1/0/+1), weight, raw}] in note order. Never raises:
    a note that matches nothing is simply not a judge line.
    """
    import re as _re
    out: List[Dict[str, Any]] = []
    for note in notes or []:
        if not isinstance(note, str) or len(note) > 400:
            continue
        for judge, pat in _JUDGE_PATTERNS:
            m = _re.search(pat, note)
            if not m:
                continue
            d, w = 0.0, 0.0
            g = m.groups()
            for token in g:
                if token in ("BUY", "bullish", "above", "rising_buy", "up"):
                    d = 1.0
                elif token in ("SELL", "bearish", "below", "down"):
                    d = -1.0
            # the applied weight is the only number we need to score the panel
            mw = _re.search(r"(?:\bw|weight)\s+([\d.]+)", note)
            if mw:
                try:
                    w = float(mw.group(1))
                except ValueError:
                    w = 0.0
            if not w:
                w = _JUDGE_DEFAULT_W.get(judge, 0.5)
            if judge in ("footprint_delta", "l3_imbalance", "l3_net_flow",
                         "l3_large_ofi", "spoof_invert_loose", "vwap_zscore"):
                try:
                    lead = float(g[0]) if g else 0.0
                    if judge == "footprint_delta" and g and g[0].lstrip("+-").replace(".", "").isdigit():
                        lead = float(g[0])
                except (TypeError, ValueError):
                    lead = 0.0
                if judge == "footprint_delta":
                    d = 1.0 if lead > 0 else (-1.0 if lead < 0 else 0.0)
                elif judge == "spoof_invert_loose":
                    d = -1.0 if (g and g[0] == "bids") else 1.0
                elif judge in ("l3_net_flow",):
                    d = 1.0 if (g and g[0] == "BUY") else -1.0
                elif lead:
                    d = 1.0 if lead > 0 else -1.0
            elif judge == "l3_ofi_streak":
                try:
                    b, s = float(g[0]), float(g[1])
                    d = 1.0 if b > s else (-1.0 if s > b else 0.0)
                except (TypeError, ValueError, IndexError):
                    pass
            elif judge in ("iceberg", "whale_walls"):
                d = 1.0 if (g and g[0] == "SUPPORT") else -1.0
            elif judge == "iceberg_noise":
                d = 0.0
            elif judge == "whale_balanced":
                d = 0.0
            elif judge == "sweep":
                d = 1.0 if (g and g[0] == "BULLISH") else -1.0
            elif judge == "vwap_trend":
                d = 1.0 if (g and g[0] == "UP") else -1.0
            elif judge == "poc_day":
                d = 1.0 if (g and g[2] == "above") else -1.0
            elif judge == "supply_demand":
                d = -1.0 if (g and g[0] == "supply") else 1.0
            elif judge == "value_area":
                g0 = next((x for x in g if x), "")
                d = 1.0 if g0 == "VAL" else (-1.0 if g0 == "VAH" else 0.0)
                if g0 == "":
                    d = 1.0 if "above VAH" in note else -1.0
            elif judge == "cvd_divergence":
                d = 1.0 if (g and g[0] == "bullish") else -1.0
            elif judge == "cvd_momentum":
                d = 1.0 if (g and g[0] == "rising") else -1.0
            elif judge == "delta_pressure":
                d = 1.0 if (g and g[0] == "Buy") else -1.0
            elif judge == "volume_roc":
                d = 1.0 if (g and g[-1] == "bullish" and g[1] == "up") else \
                    (1.0 if (g and g[-1] == "bearish" and g[1] == "down") else -1.0)
            elif judge in ("macro_yield", "macro_dxy"):
                d = -1.0 if (g and g[0] == "rising") else 1.0
            elif judge == "macro_risk":
                d = 1.0 if (g and g[0] == "off") else -1.0
            elif judge == "queue_pos":
                d = 1.0 if (g and g[0] == "bid") else -1.0
            elif judge == "microprice":
                d = 1.0 if (g and g[-1] == "BUY") else -1.0
            out.append({"judge": judge, "dir": float(d), "weight": float(w or 0.5),
                        "raw": note[:160]})
            break
    return out


# ----------------------------------------------------------------------------- #
# 12. MAIN ORCHESTRATION
# ----------------------------------------------------------------------------- #


def _as_array(value: Any) -> np.ndarray:
    if isinstance(value, pd.DataFrame):
        return value.to_numpy()
    return np.asarray(value, dtype=float)


def _extract_candles(market_data: Dict[str, Any]) -> Tuple[np.ndarray, ...]:
    candles = market_data.get("candles", {})
    if isinstance(candles, pd.DataFrame):
        df = candles
        return (df["open"].to_numpy(dtype=float), df["high"].to_numpy(dtype=float),
                df["low"].to_numpy(dtype=float), df["close"].to_numpy(dtype=float),
                df["volume"].to_numpy(dtype=float))
    return (_as_array(candles.get("open", [])), _as_array(candles.get("high", [])),
            _as_array(candles.get("low", [])), _as_array(candles.get("close", [])),
            _as_array(candles.get("volume", [])))


def _detect_regime(adx: float) -> str:
    """TREND when ADX >= 25, RANGE when ADX < 20, else NEUTRAL."""
    if adx >= 25.0:
        return "TREND"
    if adx < 20.0:
        return "RANGE"
    return "NEUTRAL"


def _detect_tf_trend(close: np.ndarray) -> str:
    """Trend direction of one candle series (EMA 9 vs EMA 20).

    Returns "UP" / "DOWN" / "NEUTRAL" (empty series -> "NEUTRAL"). Used by the
    multi-timeframe confirmation filter to reject signals that fight the
    bigger picture.
    """
    close = np.asarray(close, dtype=float)
    if len(close) < 20:
        return "NEUTRAL"
    ta = TechnicalAnalyzer()
    ema9 = ta._ema(close, 9)
    ema20 = ta._ema(close, 20)
    if ema9 > ema20 * 1.0001:
        return "UP"
    if ema9 < ema20 * 0.9999:
        return "DOWN"
    return "NEUTRAL"



def _aggregate_m1(open_: np.ndarray, high: np.ndarray, low: np.ndarray,
                  close: np.ndarray, vol: np.ndarray,
                  factor: int) -> Tuple[np.ndarray, ...]:
    """v3: aggregate M1 candles into `factor`-minute candles (5 -> M5)."""
    n = (len(close) // factor) * factor
    if n < factor:
        return tuple(np.asarray([], dtype=float) for _ in range(5))
    o = open_[:n].reshape(-1, factor)[:, 0]
    h = high[:n].reshape(-1, factor).max(axis=1)
    l = low[:n].reshape(-1, factor).min(axis=1)
    c = close[:n].reshape(-1, factor)[:, -1]
    v = vol[:n].reshape(-1, factor).sum(axis=1)
    return o, h, l, c, v


def _derive_mtf_from_m1(open_: np.ndarray, high: np.ndarray, low: np.ndarray,
                        close: np.ndarray, vol: np.ndarray) -> Dict[str, str]:
    """v3: derive higher-timeframe trends from our OWN M1 candles.

    The futures bridge supplies only M1; resampling revives the MTF
    confirmation votes without a new data source. A timeframe votes only
    with >= 20 aggregated bars (same rule as the native MTF detector).
    M5 needs ~100 min of window, M15 ~5 h (set NT_WINDOW_SECONDS=28800).
    """
    out: Dict[str, str] = {}
    for tf_name, factor in (("M5", 5), ("M15", 15), ("H1", 60)):
        _o, h, l, c, v = _aggregate_m1(open_, high, low, close, vol, factor)
        if len(c) >= 20:
            t = _detect_tf_trend(c)
            if t in ("UP", "DOWN"):
                out[tf_name] = t
    return out


def _round_level_tier(price: float) -> Tuple[float, float, float, float]:
    """v3: nearest round-number level below & above, each with a tier weight.

    Tier = the largest grid the level divides into: x00 -> 1.0, x50 -> 0.8,
    x25 -> 0.6, x10 -> 0.4. Gold respects these levels; stops and options
    barriers cluster on them.
    """
    grids = ((100.0, 1.0), (50.0, 0.8), (25.0, 0.6), (10.0, 0.4))

    def tier(level: float) -> float:
        for g, tw in grids:
            if abs(level % g) < 1e-9 or abs(level % g - g) < 1e-9:
                return tw
        return 0.3

    below = above = None
    for g, _tw in grids:
        lb = float(np.floor(price / g) * g)
        la = lb + g
        if below is None or (price - lb) < (price - below):
            below = lb
        if above is None or (la - price) < (above - price):
            above = la
    return below, tier(below), above, tier(above)


def _asian_range_state(tick_data: List[Dict], price: float,
                       now: datetime) -> Dict[str, Any]:
    """v3: Asian-range box (00:00-07:00 UTC) + London-morning breakout state.

    Reads tick timestamps directly, so it works with any provider. With the
    default 2-hour window only the tail of Asia is visible -- widen
    NT_WINDOW_SECONDS to 28800 (8h) for the full range.
    """
    out: Dict[str, Any] = {}
    hi = lo = None
    for t in tick_data or []:
        p = float(t.get("price") or 0.0)
        raw = t.get("timestamp")
        if p <= 0 or not raw:
            continue
        try:
            ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if 0 <= ts.astimezone(timezone.utc).hour < 7:
            hi = p if hi is None else max(hi, p)
            lo = p if lo is None else min(lo, p)
    if hi is None or lo is None or hi <= lo:
        return out
    out["high"], out["low"] = hi, lo
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    hour = now.astimezone(timezone.utc).hour
    out["session"] = ("ASIA" if hour < 7
                      else ("LONDON_MORNING" if 8 <= hour < 12 else "OTHER"))
    if out["session"] == "LONDON_MORNING":
        out["breakout"] = 1 if price > hi else (-1 if price < lo else 0)
    return out


def _detect_mtf_trends(market_data: Dict[str, Any]) -> Dict[str, str]:
    """Trend direction for each higher timeframe present in market_data.

    Reads `candles_m5` / `candles_m15` / `candles_h1` (each a dict with a
    "close" array) and returns e.g. {"H1":"UP","M15":"DOWN","M5":"UP"}.
    Timeframes with no data are omitted (no votes in the signal engine).
    """
    out: Dict[str, str] = {}
    for tf_name in ("H1", "M15", "M5"):
        close = _as_array(
            (market_data.get(f"candles_{tf_name.lower()}") or {}).get("close", []))
        if len(close) >= 20:
            out[tf_name] = _detect_tf_trend(close)
    return out
