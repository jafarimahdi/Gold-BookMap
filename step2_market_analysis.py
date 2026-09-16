"""
STEP 2: ENHANCED MARKET DATA COLLECTION & ANALYSIS
===================================================
Processes raw market data and extracts 25+ trading signals.

Output -> Step 3 (AI Decision Making)

COMPONENTS
----------
 1. Data structures        : OrderFlow / Footprint / Level3 / Volatility / Trend /
                             VolumeProfile / Macro / NewsAndEvents / MarketSnapshot
 2. TechnicalAnalyzer      : ATR, Bollinger Bands, MAs (SMA/EMA), ADX(+DI/-DI),
                             MACD, RSI
 3. OrderFlowAnalyzer      : CVD, Delta, buy/sell pressure, L2 depth, large orders,
                             tick-rule side classification
 4. OrderBookDepthAnalyzer : LEVEL 2 analytics - microprice, depth imbalance,
                             liquidity slope, OFI, liquidity walls, absorption
 5. FootprintBuilder       : buy/sell volume per price level, dominant level, imbalance
 6. Level3OrderBookAnalyzer: LEVEL 3 analytics - NEW/CANCEL/MODIFY/FILL events,
                             book reconstruction, event OFI, aggressor flow ratio,
                             streaks, icebergs, large orders, absorption
 7. VolumeProfileAnalyzer  : VWAP, POC, Value Area, volume RoC, OBV, A/D,
                             VWAP std-dev + z-score bands
 8. MacroAnalyzer          : gold vs USD / 10Y yields / VIX correlations (log-returns),
                             real yields, risk sentiment
 9. NewsAnalyzer           : headline sentiment (TextBlob with lexicon fallback)
10. EconomicCalendar       : upcoming events + NEWS-TIME STATE (QUIET/WARNING/BLACKOUT)
11. SignalEngine           : composite score -> strength / direction / confidence,
                             regime-aware, divergence-aware, news-gated
12. analyze_market()       : orchestrates every analyzer into one MarketSnapshot

All external dependencies degrade gracefully (requests/textblob optional).
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

import config   # v4.4: signal weights/thresholds are .env-tunable

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

try:
    from textblob import TextBlob
except ImportError:  # pragma: no cover
    TextBlob = None

logger = logging.getLogger("step2_market_analysis")

__all__ = [
    "OrderFlowMetrics", "FootprintMetrics", "Level3Events", "VolatilityMetrics",
    "TrendMetrics", "VolumeProfileMetrics", "MacroMetrics", "NewsAndEvents",
    "MarketSnapshot", "TechnicalAnalyzer", "OrderFlowAnalyzer",
    "OrderBookDepthAnalyzer", "FootprintBuilder", "Level3OrderBookAnalyzer",
    "VolumeProfileAnalyzer", "MacroAnalyzer", "NewsAnalyzer", "EconomicCalendar",
    "SignalEngine", "analyze_market", "snapshot_to_dict", "snapshot_to_json",
    "format_snapshot",
]

# ----------------------------------------------------------------------------- #
# 1. DATA STRUCTURES
# ----------------------------------------------------------------------------- #


@dataclass
class OrderFlowMetrics:
    """Order flow and Level-2 depth analysis."""
    cvd: float                        # Cumulative Volume Delta
    delta: float                      # Buy volume - Sell volume
    buying_pressure: float            # Buy vol / Total vol (%)
    selling_pressure: float           # Sell vol / Total vol (%)
    level2_bid_depth: float           # Sum of bid volumes (top 5)
    level2_ask_depth: float           # Sum of ask volumes (top 5)
    bid_ask_ratio: float              # Bid depth / Ask depth
    large_orders: int                 # Orders > 100 contracts
    timestamp: Optional[datetime] = None
    # ---- Level 2 analytics (filled by OrderBookDepthAnalyzer) ----
    mid_price: float = 0.0            # simple mid of best bid/ask
    microprice: float = 0.0           # size-weighted mid (fair value)
    depth_imbalance: float = 0.0      # (bid-ask)/(bid+ask) over top N levels
    book_slope: float = 0.0           # liquidity falloff slope (higher = deeper)
    liquidity_concentration: float = 0.0  # top-level size / top-N size (0..1)
    ofi: float = 0.0                  # cumulative Order Flow Imbalance (L2)
    absorption_events: int = 0        # walls being eaten without price move
    absorption_net: int = 0           # ask-absorption - bid-absorption
    tick_rule_classified: int = 0     # ticks whose side was inferred (tick rule)


@dataclass
class FootprintMetrics:
    """Market footprint (buy/sell volume at each price)."""
    price_levels: Dict[float, Dict[str, float]] = field(default_factory=dict)
    dominant_level: float = 0.0                  # Price with highest traded volume
    buying_levels: List[float] = field(default_factory=list)
    selling_levels: List[float] = field(default_factory=list)
    footprint_strength: float = 0.0              # Dominant level vol / total vol
    delta_imbalance: float = 0.0                 # (buy-sell)/(buy+sell) over all levels
    timestamp: Optional[datetime] = None


@dataclass
class Level3Events:
    """Individual order events and book reconstruction (Level 3)."""
    order_book: Dict[str, List] = field(default_factory=lambda: {"bids": [], "asks": []})
    order_events: List[Dict] = field(default_factory=list)
    market_orders: int = 0            # Number of market orders
    limit_orders: int = 0             # Number of limit orders
    order_book_imbalance: float = 0.0 # (Total bids - Total asks) / Total
    aggressive_buys: int = 0          # Market buy orders
    aggressive_sells: int = 0         # Market sell orders
    aggressive_buy_volume: float = 0.0   # size of market buys
    aggressive_sell_volume: float = 0.0  # size of market sells
    aggressive_flow_ratio: float = 0.5   # buy vol / (buy+sell) vol
    ofi: float = 0.0                  # cumulative Order Flow Imbalance (events)
    buy_streak: int = 0               # consecutive aggressive buys
    sell_streak: int = 0              # consecutive aggressive sells
    large_order_events: int = 0       # events with size > threshold
    iceberg_events: int = 0           # repeated same-size orders at same price
    timestamp: Optional[datetime] = None


@dataclass
class VolatilityMetrics:
    """Volatility measurements."""
    atr: float = 0.0                  # Average True Range (14-period)
    atr_percent: float = 0.0          # ATR as % of price
    bb_upper: float = 0.0             # Bollinger Band upper
    bb_middle: float = 0.0            # Moving average
    bb_lower: float = 0.0             # Bollinger Band lower
    bb_width: float = 0.0             # (Upper - Lower) / Middle
    volatility_rank: float = 0.0      # Current vol vs 52-week range (0-1)
    timestamp: Optional[datetime] = None


@dataclass
class TrendMetrics:
    """Trend analysis."""
    sma_9: float = 0.0
    sma_20: float = 0.0
    sma_50: float = 0.0
    ema_12: float = 0.0
    ema_26: float = 0.0
    adx: float = 0.0
    plus_di: float = 0.0
    minus_di: float = 0.0
    macd: float = 0.0
    macd_signal: float = 0.0
    macd_histogram: float = 0.0
    rsi: float = 50.0
    trend_direction: str = "NEUTRAL"  # "UP", "DOWN", "NEUTRAL"
    trend_strength: float = 0.0       # 0-100
    timestamp: Optional[datetime] = None


@dataclass
class VolumeProfileMetrics:
    """Volume profile & price action."""
    vwap: float = 0.0
    poc: float = 0.0                  # Point of Control
    value_area_high: float = 0.0
    value_area_low: float = 0.0
    volume_rate_of_change: float = 0.0
    on_balance_volume: float = 0.0
    accumulation_distribution: float = 0.0
    vwap_std: float = 0.0             # std-dev of volume-weighted prices
    vwap_zscore: float = 0.0          # (last price - vwap) / vwap_std
    timestamp: Optional[datetime] = None


@dataclass
class MacroMetrics:
    """Macro and correlation metrics."""
    usd_index: float = 0.0
    us_10y_yield: float = 0.0
    vix_index: float = 0.0
    dxy_correlation: float = 0.0
    yield_correlation: float = 0.0
    vix_correlation: float = 0.0
    inflation_expectation: float = 0.0
    real_yields: float = 0.0          # nominal yield - inflation expectation
    risk_sentiment: str = "NEUTRAL"   # "RISK_ON", "RISK_OFF", "NEUTRAL"
    usd_change_5d: float = 0.0        # relative DXY change, last 5 sessions
    yield_change_5d: float = 0.0      # relative 10Y yield change, last 5 sessions
    vix_spike: float = 0.0            # VIX vs its 20-session median (>0 = stress rising)
    timestamp: Optional[datetime] = None


@dataclass
class NewsAndEvents:
    """Economic events and sentiment (incl. news-time state)."""
    event_type: str = "None"          # "FOMC", "CPI", "Jobs", ...
    impact_level: str = "LOW"         # "HIGH", "MEDIUM", "LOW"
    sentiment_score: float = 0.0      # -1.0 .. 1.0
    sentiment_label: str = "NEUTRAL"  # "BEARISH", "NEUTRAL", "BULLISH"
    upcoming_events: List[Dict] = field(default_factory=list)
    news_headlines: List[str] = field(default_factory=list)
    news_state: str = "QUIET"         # "QUIET" | "WARNING" | "BLACKOUT"
    minutes_to_next_event: float = 0.0  # minutes until next event (neg = after)
    next_event_title: str = ""
    timestamp: Optional[datetime] = None


@dataclass
class MarketSnapshot:
    """Complete market analysis snapshot -> consumed by Step 3."""
    timestamp: datetime
    price: float
    bid: float
    ask: float
    volume: float
    order_flow: OrderFlowMetrics
    footprint: FootprintMetrics
    level3: Level3Events
    volatility: VolatilityMetrics
    trend: TrendMetrics
    volume_profile: VolumeProfileMetrics
    macro: MacroMetrics
    news: NewsAndEvents
    signal_strength: float = 0.0      # 0-100
    signal_direction: str = "NEUTRAL" # "BUY", "SELL", "NEUTRAL"
    confidence: float = 0.0           # 0-100
    regime: str = "NEUTRAL"           # "TREND" | "RANGE" | "NEUTRAL"
    divergence: float = 0.0           # +1 bullish / -1 bearish CVD divergence
    macro_bias: float = 0.0           # -1..+1 macro backdrop (+ = bullish gold)
    mtf_trends: Dict[str, str] = field(default_factory=dict)  # {"H1":"UP","M15":"DOWN","M5":"UP"}
    # v4.1: higher-timeframe Points of Control — the 1h and 4h volume magnets
    # the big timeframe players trade around ("POC in the bigger candles")
    htf_poc: Dict[str, float] = field(default_factory=dict)  # {"H1": 4352.5, "H4": 4348.0}
    spread_pct: float = 0.0           # bid-ask spread as % of price (0 = unknown)
    order_blocks: List[Dict] = field(default_factory=list)  # supply/demand zones
    nearest_support: float = 0.0      # nearest demand zone bottom below price
    nearest_resistance: float = 0.0   # nearest supply zone top above price
    symbol: str = ""
    # market identity — DATA side (futures feed) vs TRADE side (MT5 CFD)
    data_symbol: str = ""
    trade_symbol: str = ""
    data_market: str = ""
    trade_market: str = ""
    notes: List[str] = field(default_factory=list)
    # Data-quality labels prevent estimated CFD flow from being mistaken for
    # exchange trade prints or Level 3 data.
    data_quality: Dict[str, str] = field(default_factory=dict)


# ----------------------------------------------------------------------------- #
# 2. TECHNICAL ANALYSIS ENGINE
# ----------------------------------------------------------------------------- #


class TechnicalAnalyzer:
    """All technical indicators (ATR, BB, MAs, ADX, MACD, RSI)."""

    def __init__(self, lookback_periods: int = 200):
        self.lookback = lookback_periods

    # -- ATR (Wilder-smoothed) -------------------------------------------------
    def compute_atr(self, high: np.ndarray, low: np.ndarray,
                    close: np.ndarray, period: int = 14) -> float:
        if len(high) < period + 1:
            return 0.0
        high = np.asarray(high, dtype=float)
        low = np.asarray(low, dtype=float)
        close = np.asarray(close, dtype=float)

        prev_close = np.roll(close, 1)
        prev_close[0] = close[0]
        tr = np.maximum(high - low,
                        np.maximum(np.abs(high - prev_close),
                                   np.abs(low - prev_close)))

        atr = float(np.mean(tr[:period]))          # Wilder seed
        for i in range(period, len(tr)):           # Wilder smoothing
            atr = (atr * (period - 1) + tr[i]) / period
        return float(atr)

    # -- Bollinger Bands -------------------------------------------------------
    def compute_bollinger_bands(self, close: np.ndarray, period: int = 20,
                                std_dev: float = 2.0) -> Tuple[float, float, float]:
        close = np.asarray(close, dtype=float)
        if len(close) < period:
            last = float(close[-1])
            return last, last, last
        window = close[-period:]
        sma = float(np.mean(window))
        std = float(np.std(window, ddof=1)) if len(window) > 1 else 0.0
        return float(sma + std_dev * std), sma, float(sma - std_dev * std)

    # -- Moving averages -------------------------------------------------------
    def compute_moving_averages(self, close: np.ndarray) -> Dict[str, float]:
        close = np.asarray(close, dtype=float)
        last = float(close[-1])
        return {
            "sma_9": float(np.mean(close[-9:])) if len(close) >= 9 else last,
            "sma_20": float(np.mean(close[-20:])) if len(close) >= 20 else last,
            "sma_50": float(np.mean(close[-50:])) if len(close) >= 50 else last,
            "ema_12": float(self._ema(close, 12)) if len(close) >= 12 else last,
            "ema_26": float(self._ema(close, 26)) if len(close) >= 26 else last,
        }

    # -- ADX / +DI / -DI (Wilder-smoothed) -------------------------------------
    def compute_adx(self, high: np.ndarray, low: np.ndarray,
                    close: np.ndarray, period: int = 14) -> Tuple[float, float, float]:
        high = np.asarray(high, dtype=float)
        low = np.asarray(low, dtype=float)
        close = np.asarray(close, dtype=float)
        if len(high) < 2 * period + 1:
            return 25.0, 25.0, 25.0

        up = np.diff(high)
        down = -np.diff(low)
        plus_dm = np.where((up > down) & (up > 0), up, 0.0)
        minus_dm = np.where((down > up) & (down > 0), down, 0.0)

        prev_close = close[:-1]
        tr = np.maximum(high[1:] - low[1:],
                        np.maximum(np.abs(high[1:] - prev_close),
                                   np.abs(low[1:] - prev_close)))

        tr_s = self._wilder_series(tr, period)
        pdm_s = self._wilder_series(plus_dm, period)
        mdm_s = self._wilder_series(minus_dm, period)

        plus_di = 100.0 * pdm_s / (tr_s + 1e-10)
        minus_di = 100.0 * mdm_s / (tr_s + 1e-10)
        dx = 100.0 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)

        valid_dx = dx[~np.isnan(dx)]
        if len(valid_dx) < period:
            return 25.0, float(plus_di[-1]), float(minus_di[-1])
        adx = self._wilder_series(valid_dx, period, seed="mean")

        return (float(np.clip(adx[-1], 0.0, 100.0)),
                float(plus_di[-1]), float(minus_di[-1]))

    # -- MACD ------------------------------------------------------------------
    def compute_macd(self, close: np.ndarray, fast: int = 12, slow: int = 26,
                     signal_period: int = 9) -> Tuple[float, float, float]:
        close = np.asarray(close, dtype=float)
        if len(close) < slow + signal_period:
            return 0.0, 0.0, 0.0

        ema_fast = self._ema_series(close, fast)
        ema_slow = self._ema_series(close, slow)
        macd_line = ema_fast - ema_slow

        valid = macd_line[slow - 1:]               # MACD valid from index slow-1
        signal_line = self._ema_series(valid, signal_period)

        macd_val = float(macd_line[-1])
        signal_val = float(signal_line[-1])
        return macd_val, signal_val, float(macd_val - signal_val)

    # -- RSI (Wilder-smoothed) --------------------------------------------------
    def compute_rsi(self, close: np.ndarray, period: int = 14) -> float:
        close = np.asarray(close, dtype=float)
        if len(close) < period + 1:
            return 50.0

        deltas = np.diff(close)
        gains = np.clip(deltas, 0.0, None)
        losses = np.clip(-deltas, 0.0, None)

        avg_gain = float(np.mean(gains[:period]))
        avg_loss = float(np.mean(losses[:period]))
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return float(np.clip(100.0 - 100.0 / (1.0 + rs), 0.0, 100.0))

    # -- Trend summary ---------------------------------------------------------
    def compute_trend(self, close: np.ndarray, high: np.ndarray, low: np.ndarray
                      ) -> Tuple[str, float]:
        """Return ('UP'|'DOWN'|'NEUTRAL', strength 0-100) from MA stack + ADX."""
        close = np.asarray(close, dtype=float)
        if len(close) < 50:
            return "NEUTRAL", 0.0
        ma = self.compute_moving_averages(close)
        adx, pdi, mdi = self.compute_adx(high, low, close)

        if ma["sma_9"] > ma["sma_20"] > ma["sma_50"] and pdi > mdi:
            direction = "UP"
        elif ma["sma_9"] < ma["sma_20"] < ma["sma_50"] and mdi > pdi:
            direction = "DOWN"
        else:
            direction = "NEUTRAL"

        strength = float(np.clip(adx, 0.0, 100.0))
        if direction == "NEUTRAL":
            strength *= 0.5
        return direction, strength

    # -- helpers ---------------------------------------------------------------
    def _ema(self, data: np.ndarray, period: int) -> float:
        series = self._ema_series(np.asarray(data, dtype=float), period)
        return float(series[-1])

    @staticmethod
    def _ema_series(data: np.ndarray, period: int) -> np.ndarray:
        if len(data) < period:
            raise ValueError("not enough data for EMA")
        out = np.full(len(data), np.nan)
        multiplier = 2.0 / (period + 1)
        out[period - 1] = float(np.mean(data[:period]))
        for i in range(period, len(data)):
            out[i] = data[i] * multiplier + out[i - 1] * (1.0 - multiplier)
        return out

    @staticmethod
    def _wilder_series(data: np.ndarray, period: int, seed: str = "sum") -> np.ndarray:
        """Wilder-smoothed series.

        seed="sum"  : smoothed SUM  (TR/DM):  out = out_prev*(p-1)/p + data[i]
        seed="mean" : smoothed MEAN (ADX):   out = (out_prev*(p-1) + data[i]) / p
        """
        out = np.full(len(data), np.nan)
        if seed == "mean":
            out[period - 1] = float(np.mean(data[:period]))
            for i in range(period, len(data)):
                out[i] = (out[i - 1] * (period - 1) + data[i]) / period
        else:
            out[period - 1] = float(np.sum(data[:period]))
            for i in range(period, len(data)):
                out[i] = out[i - 1] - out[i - 1] / period + data[i]
        return out


# ----------------------------------------------------------------------------- #
# 3. ORDER FLOW ANALYZER  (CVD / Delta / tick rule / L2 depth)
# ----------------------------------------------------------------------------- #


class OrderFlowAnalyzer:
    """Order flow and CVD analysis."""

    def __init__(self, large_order_threshold: float = 100.0):
        self.cumulative_volume_delta = 0.0
        self.large_order_threshold = large_order_threshold

    @staticmethod
    def _normalize_side(side: Any) -> str:
        s = str(side or "").strip().upper()
        if s in ("BUY", "B", "BID", "1"):
            return "BUY"
        if s in ("SELL", "S", "ASK", "-1"):
            return "SELL"
        return "BUY"

    @staticmethod
    def classify_by_tick_rule(price: float, prev_price: Optional[float]) -> str:
        """Infer aggressor side when the feed does not label it.

        Tick rule: trade at/above the offer (price rising) is buyer-initiated;
        trade at/below the bid (price falling) is seller-initiated.
        """
        if prev_price is None:
            return "BUY"
        if price > prev_price:
            return "BUY"
        if price < prev_price:
            return "SELL"
        return "BUY"

    def analyze_tick_data(self, tick_data: List[Dict],
                          bid_depth: Optional[Dict] = None,
                          ask_depth: Optional[Dict] = None,
                          l2_metrics: Optional[Dict[str, Any]] = None
                          ) -> OrderFlowMetrics:
        """Process tick data into order-flow metrics."""
        now = datetime.now(timezone.utc)
        if not tick_data:
            # no ticks, but still carry over the Level-2 depth analytics
            l2e = l2_metrics or {}
            return OrderFlowMetrics(
                cvd=self.cumulative_volume_delta, delta=0.0, buying_pressure=0.0,
                selling_pressure=0.0, level2_bid_depth=0.0, level2_ask_depth=0.0,
                bid_ask_ratio=1.0, large_orders=0,
                mid_price=float(l2e.get("mid_price", 0.0)),
                microprice=float(l2e.get("microprice", 0.0)),
                depth_imbalance=float(l2e.get("depth_imbalance", 0.0)),
                book_slope=float(l2e.get("book_slope", 0.0)),
                liquidity_concentration=float(l2e.get("liquidity_concentration", 0.0)),
                ofi=float(l2e.get("ofi", 0.0)),
                absorption_events=int(l2e.get("absorption_events", 0)),
                absorption_net=int(l2e.get("absorption_net", 0)),
                tick_rule_classified=0,
                timestamp=now)

        buy_volume = 0.0
        sell_volume = 0.0
        tick_rule_classified = 0
        prev_price: Optional[float] = None
        for t in tick_data:
            vol = float(t.get("volume", 0) or 0)
            raw_side = t.get("side")
            if raw_side is None or str(raw_side).strip() == "":
                side = self.classify_by_tick_rule(
                    float(t.get("price", 0) or 0), prev_price)
                tick_rule_classified += 1
            else:
                side = self._normalize_side(raw_side)
            if side == "BUY":
                buy_volume += vol
            else:
                sell_volume += vol
            prev_price = float(t.get("price", 0) or prev_price or 0)

        total_volume = buy_volume + sell_volume or 1.0
        delta = buy_volume - sell_volume
        self.cumulative_volume_delta += delta

        bid_depth = bid_depth or {}
        ask_depth = ask_depth or {}
        bid_top5 = sorted(bid_depth.items(), key=lambda kv: float(kv[0]), reverse=True)[:5]
        ask_top5 = sorted(ask_depth.items(), key=lambda kv: float(kv[0]))[:5]
        bid_sum = sum(float(v) for _, v in bid_top5)
        ask_sum = sum(float(v) for _, v in ask_top5)

        large_orders = sum(1 for t in tick_data
                           if float(t.get("volume", 0) or 0) > self.large_order_threshold)

        l2 = l2_metrics or {}
        return OrderFlowMetrics(
            cvd=self.cumulative_volume_delta,
            delta=delta,
            buying_pressure=100.0 * buy_volume / total_volume,
            selling_pressure=100.0 * sell_volume / total_volume,
            level2_bid_depth=bid_sum,
            level2_ask_depth=ask_sum,
            bid_ask_ratio=(bid_sum / ask_sum) if ask_sum > 0 else 1.0,
            large_orders=large_orders,
            mid_price=float(l2.get("mid_price", 0.0)),
            microprice=float(l2.get("microprice", 0.0)),
            depth_imbalance=float(l2.get("depth_imbalance", 0.0)),
            book_slope=float(l2.get("book_slope", 0.0)),
            liquidity_concentration=float(l2.get("liquidity_concentration", 0.0)),
            ofi=float(l2.get("ofi", 0.0)),
            absorption_events=int(l2.get("absorption_events", 0)),
            absorption_net=int(l2.get("absorption_net", 0)),
            tick_rule_classified=tick_rule_classified,
            timestamp=now,
        )


# ----------------------------------------------------------------------------- #
# 4. LEVEL 2 DEPTH ANALYZER  (microprice / imbalance / OFI / walls / absorption)
# ----------------------------------------------------------------------------- #


class OrderBookDepthAnalyzer:
    """Level-2 order-book analytics from successive depth snapshots.

    Produces: microprice, depth imbalance, liquidity slope/concentration,
    cumulative Order Flow Imbalance (OFI) and absorption detection.
    """

    def __init__(self, levels: int = 5, wall_size: float = 100.0):
        self.levels = levels
        self.wall_size = wall_size
        self._prev_bids: Dict[float, float] = {}
        self._prev_asks: Dict[float, float] = {}
        self.cumulative_ofi = 0.0
        self.absorption_events = 0
        self.absorption_net = 0
        self._prev_bb_price: Optional[float] = None
        self._prev_bb_size: float = 0.0
        self._prev_ba_price: Optional[float] = None
        self._prev_ba_size: float = 0.0
        self._bid_eaten = 0
        self._ask_eaten = 0

    # -- helpers ---------------------------------------------------------------
    @staticmethod
    def _normalize(levels: Any) -> Dict[float, float]:
        """Accept a dict {price: size} or a list of (price, size) tuples."""
        if levels is None:
            return {}
        if isinstance(levels, dict):
            return {float(k): float(v) for k, v in levels.items() if float(v) > 0}
        return {float(p): float(s) for p, s in levels if float(s) > 0}

    def _top(self, book: Dict[float, float], side: str) -> List[Tuple[float, float]]:
        if not book:
            return []
        if side == "bid":
            return sorted(book.items(), key=lambda kv: kv[0], reverse=True)[: self.levels]
        return sorted(book.items(), key=lambda kv: kv[0])[: self.levels]

    # -- microprice ------------------------------------------------------------
    @staticmethod
    def microprice(bids: Any, asks: Any) -> float:
        """Size-weighted mid price (Stoikov microprice).

        Weights the bid/ask by the *opposite* side's size, so the fair value
        leans toward the ask when bid liquidity is larger (bullish pressure).
        """
        bids = OrderBookDepthAnalyzer._normalize(bids)
        asks = OrderBookDepthAnalyzer._normalize(asks)
        if not bids or not asks:
            return 0.0
        best_bid = max(bids)
        best_ask = min(asks)
        bid_size = bids[best_bid]
        ask_size = asks[best_ask]
        if bid_size + ask_size <= 0:
            return (best_bid + best_ask) / 2.0
        w_bid = ask_size / (bid_size + ask_size)
        return best_bid * w_bid + best_ask * (1.0 - w_bid)

    # -- depth imbalance -------------------------------------------------------
    def depth_imbalance(self, bids: Any, asks: Any) -> float:
        bids = self._normalize(bids)
        asks = self._normalize(asks)
        bid_sum = sum(s for _, s in self._top(bids, "bid"))
        ask_sum = sum(s for _, s in self._top(asks, "ask"))
        total = bid_sum + ask_sum
        return (bid_sum - ask_sum) / total if total > 0 else 0.0

    # -- liquidity shape -------------------------------------------------------
    def liquidity_shape(self, bids: Any, asks: Any) -> Tuple[float, float]:
        """Return (book_slope, liquidity_concentration).

        book_slope: normalized size change across levels (positive = deeper).
        concentration: how much liquidity sits only at the top level (0..1).
        """
        bid_top = [s for _, s in self._top(self._normalize(bids), "bid")]
        ask_top = [s for _, s in self._top(self._normalize(asks), "ask")]
        slopes = []
        for sizes in (bid_top, ask_top):
            if len(sizes) < 2 or sizes[0] <= 0:
                continue
            slopes.append((sizes[-1] - sizes[0]) / ((len(sizes) - 1) * sizes[0]))
        slope = float(np.mean(slopes)) if slopes else 0.0

        top1 = sum(sizes[0] for sizes in (bid_top, ask_top) if sizes)
        total = sum(sum(sizes) for sizes in (bid_top, ask_top))
        concentration = top1 / total if total > 0 else 0.0
        return slope, concentration

    # -- OFI -------------------------------------------------------------------
    def _ofi_against_prev(self, bids: Dict[float, float], asks: Dict[float, float]
                          ) -> float:
        """Order Flow Imbalance for one snapshot transition.

        OFI = sum(change in bid sizes) - sum(change in ask sizes) over the
        top `levels` of each side (Cont, Kukanov & Stoikov). Positive OFI is
        bullish net order flow.
        """
        if not self._prev_bids and not self._prev_asks:
            return 0.0
        prev_bid_top = dict(self._top(self._prev_bids, "bid"))
        prev_ask_top = dict(self._top(self._prev_asks, "ask"))
        cur_bid_top = dict(self._top(bids, "bid"))
        cur_ask_top = dict(self._top(asks, "ask"))

        bid_delta = 0.0
        for p in set(prev_bid_top) | set(cur_bid_top):
            bid_delta += cur_bid_top.get(p, 0.0) - prev_bid_top.get(p, 0.0)
        ask_delta = 0.0
        for p in set(prev_ask_top) | set(cur_ask_top):
            ask_delta += cur_ask_top.get(p, 0.0) - prev_ask_top.get(p, 0.0)
        return bid_delta - ask_delta

    # -- absorption ------------------------------------------------------------
    def _update_absorption(self, bids: Dict[float, float], asks: Dict[float, float]):
        """Detect liquidity walls being eaten without price moving through.

        bid absorption = a large resting bid keeps shrinking at the same price
        (sellers absorbing it) -> bearish. ask absorption -> bullish.
        """
        bid_top = self._top(bids, "bid")
        ask_top = self._top(asks, "ask")
        bb_price, bb_size = bid_top[0] if bid_top else (None, 0.0)
        ba_price, ba_size = ask_top[0] if ask_top else (None, 0.0)

        # -- bid side ----------------------------------------------------------
        if bb_size >= self.wall_size and bb_price is not None:
            if self._prev_bb_price is not None and abs(bb_price - self._prev_bb_price) < 1e-9:
                if bb_size < self._prev_bb_size - 1e-9:
                    self._bid_eaten += 1
                else:
                    self._bid_eaten = max(0, self._bid_eaten - 1)
                if self._bid_eaten >= 2:
                    self.absorption_events += 1
                    self.absorption_net -= 1          # sellers absorbing bids
                    self._bid_eaten = 0
            else:
                self._bid_eaten = 0
        else:
            self._bid_eaten = 0
        self._prev_bb_price, self._prev_bb_size = bb_price, bb_size

        # -- ask side ----------------------------------------------------------
        if ba_size >= self.wall_size and ba_price is not None:
            if self._prev_ba_price is not None and abs(ba_price - self._prev_ba_price) < 1e-9:
                if ba_size < self._prev_ba_size - 1e-9:
                    self._ask_eaten += 1
                else:
                    self._ask_eaten = max(0, self._ask_eaten - 1)
                if self._ask_eaten >= 2:
                    self.absorption_events += 1
                    self.absorption_net += 1          # buyers absorbing asks
                    self._ask_eaten = 0
            else:
                self._ask_eaten = 0
        else:
            self._ask_eaten = 0
        self._prev_ba_price, self._prev_ba_size = ba_price, ba_size

    # -- main update -----------------------------------------------------------
    def update(self, bids: Any, asks: Any) -> None:
        """Feed one depth snapshot; accumulate OFI and absorption."""
        bids = self._normalize(bids)
        asks = self._normalize(asks)
        self.cumulative_ofi += self._ofi_against_prev(bids, asks)
        self._update_absorption(bids, asks)
        self._prev_bids, self._prev_asks = bids, asks

    def summary(self, bids: Any, asks: Any) -> Dict[str, Any]:
        """Static metrics for the current snapshot (plus accumulated state)."""
        bids_n, asks_n = self._normalize(bids), self._normalize(asks)
        slope, concentration = self.liquidity_shape(bids_n, asks_n)
        micro = self.microprice(bids_n, asks_n)
        best_bid = max(bids_n) if bids_n else 0.0
        best_ask = min(asks_n) if asks_n else 0.0
        mid = ((best_bid + best_ask) / 2.0) if best_bid and best_ask else 0.0
        return {
            "mid_price": mid,
            "microprice": micro,
            "depth_imbalance": self.depth_imbalance(bids_n, asks_n),
            "book_slope": slope,
            "liquidity_concentration": concentration,
            "ofi": self.cumulative_ofi,
            "absorption_events": self.absorption_events,
            "absorption_net": self.absorption_net,
        }


# ----------------------------------------------------------------------------- #
# 5. FOOTPRINT BUILDER
# ----------------------------------------------------------------------------- #


class FootprintBuilder:
    """Market footprint: buy/sell volume aggregated at each price level."""

    def __init__(self, price_resolution: float = 0.1):
        self.price_resolution = price_resolution
        self.price_levels: Dict[float, Dict[str, float]] = defaultdict(
            lambda: {"buy": 0.0, "sell": 0.0})

    def add_trade(self, price: float, volume: float, side: str) -> None:
        bucket = round(float(price) / self.price_resolution) * self.price_resolution
        side_key = "buy" if self._normalize_side(side) == "BUY" else "sell"
        self.price_levels[bucket][side_key] += float(volume)

    @staticmethod
    def _normalize_side(side: Any) -> str:
        s = str(side or "").strip().upper()
        return "BUY" if s in ("BUY", "B", "BID", "1") else "SELL"

    def build_footprint(self, tick_data: List[Dict]) -> FootprintMetrics:
        self.price_levels.clear()
        for tick in tick_data:
            self.add_trade(tick.get("price", 0.0),
                           tick.get("volume", 0.0),
                           tick.get("side", "BUY"))

        buying_levels: List[float] = []
        selling_levels: List[float] = []
        dominant_level = 0.0
        max_volume = 0.0
        total_traded = 0.0
        buy_total = sell_total = 0.0

        for price, vols in self.price_levels.items():
            total = vols["buy"] + vols["sell"]
            total_traded += total
            buy_total += vols["buy"]
            sell_total += vols["sell"]
            if total > max_volume:
                max_volume = total
                dominant_level = price
            if vols["buy"] > vols["sell"]:
                buying_levels.append(price)
            elif vols["sell"] > vols["buy"]:
                selling_levels.append(price)

        buying_levels.sort(reverse=True)
        selling_levels.sort()

        return FootprintMetrics(
            price_levels=dict(self.price_levels),
            dominant_level=dominant_level,
            buying_levels=buying_levels,
            selling_levels=selling_levels,
            footprint_strength=(max_volume / total_traded) if total_traded > 0 else 0.0,
            delta_imbalance=((buy_total - sell_total) / (buy_total + sell_total)
                             if (buy_total + sell_total) > 0 else 0.0),
            timestamp=datetime.now(timezone.utc),
        )


# ----------------------------------------------------------------------------- #
# 6. LEVEL 3 ORDER BOOK ANALYZER  (events / OFI / aggressor flow / icebergs)
# ----------------------------------------------------------------------------- #


class Level3OrderBookAnalyzer:
    """Real-time order-book reconstruction from individual order events (L3)."""

    def __init__(self, large_size: float = 100.0):
        self.order_book: Dict[str, List] = {"bids": [], "asks": []}
        self.order_events: List[Dict] = []
        self.market_orders = 0
        self.limit_orders = 0
        self.aggressive_buys = 0
        self.aggressive_sells = 0
        self.aggressive_buy_volume = 0.0
        self.aggressive_sell_volume = 0.0
        self.ofi = 0.0
        self.buy_streak = 0
        self.sell_streak = 0
        self.large_order_events = 0
        self.iceberg_events = 0
        self.large_size = large_size
        self._last_aggressor: Optional[str] = None
        self._last_new_key: Optional[tuple] = None

    # -- OFI accounting --------------------------------------------------------
    def _ofi_add(self, side: str, size: float) -> None:
        """Resting liquidity ADDED: bid -> +OFI, ask -> -OFI."""
        self.ofi += size if side in ("bid", "bids") else -size

    def _ofi_remove(self, side: str, size: float) -> None:
        """Resting liquidity REMOVED: bid -> -OFI, ask -> +OFI."""
        self.ofi += -size if side in ("bid", "bids") else size

    @staticmethod
    def _side_key(side: str) -> str:
        s = str(side or "").upper()
        return "bids" if s in ("BUY", "BID", "B") else "asks"

    # -- event processing ------------------------------------------------------
    def process_order_event(self, event: Dict) -> None:
        """Apply a NEW / CANCEL / MODIFY / FILL event to the book.

        Convention: `side` is the order's direction. For FILL events with
        `is_market_order=True`, `side` is the *taker* (aggressive) side --
        a market BUY lifts resting ASKs, a market SELL hits resting BIDs.
        """
        event_type = str(event.get("type", "")).upper()
        side = str(event.get("side", "")).upper()
        price = float(event.get("price", 0.0) or 0.0)
        size = float(event.get("size", 0.0) or 0.0)

        is_buy = side in ("BUY", "BID", "B")
        book_side = "bids" if is_buy else "asks"

        if size >= self.large_size:
            self.large_order_events += 1

        if event_type in ("NEW", "ADD", "OPEN"):
            self.limit_orders += 1
            self._ofi_add(book_side, size)
            self.order_book[book_side].append((price, size))
            # iceberg detection: repeated NEW at same price/size
            key = (book_side, price, size)
            if self._last_new_key == key:
                self.iceberg_events += 1
            self._last_new_key = key

        elif event_type in ("CANCEL", "CANCELED", "DELETE"):
            self._ofi_remove(book_side, size)
            self._remove_from_book(book_side, price, size)

        elif event_type in ("MODIFY", "MODIFIED", "REPLACE", "UPDATE"):
            old_size = float(event.get("old_size", 0.0) or 0.0)
            self._ofi_remove(book_side, old_size)
            self._ofi_add(book_side, size)
            self._remove_from_book(book_side, price, old_size)
            self.order_book[book_side].append((price, size))

        elif event_type in ("FILL", "TRADE", "EXECUTED", "MATCH"):
            if bool(event.get("is_market_order", False)):
                self.market_orders += 1
                if is_buy:
                    self.aggressive_buys += 1
                    self.aggressive_buy_volume += size
                    self._ofi_remove("asks", size)      # lifted resting asks
                    self._remove_from_book("asks", price, size)
                else:
                    self.aggressive_sells += 1
                    self.aggressive_sell_volume += size
                    self._ofi_remove("bids", size)      # hit resting bids
                    self._remove_from_book("bids", price, size)
                # aggressor streak tracking
                agg = "BUY" if is_buy else "SELL"
                if self._last_aggressor == agg:
                    if is_buy:
                        self.buy_streak += 1
                    else:
                        self.sell_streak += 1
                else:
                    self.buy_streak = 1 if is_buy else 0
                    self.sell_streak = 0 if is_buy else 1
                self._last_aggressor = agg
            else:
                # resting limit order filled on its own side
                self._ofi_remove(book_side, size)
                self._remove_from_book(book_side, price, size)

        self.order_events.append(event)

    def _remove_from_book(self, side: str, price: float, size: float) -> None:
        levels = self.order_book[side]
        for i, (p, s) in enumerate(levels):
            if abs(p - price) < 1e-9:
                remaining = s - size
                if remaining <= 1e-9:
                    levels.pop(i)
                else:
                    levels[i] = (p, remaining)
                return

    def update_order_book(self, bids: Sequence[Tuple], asks: Sequence[Tuple]) -> None:
        self.order_book["bids"] = [(float(p), float(s)) for p, s in bids]
        self.order_book["asks"] = [(float(p), float(s)) for p, s in asks]

    def compute_imbalance(self, top_n: int = 10) -> float:
        bids = sorted(self.order_book["bids"], key=lambda x: x[0], reverse=True)[:top_n]
        asks = sorted(self.order_book["asks"], key=lambda x: x[0])[:top_n]
        bid_sum = sum(s for _, s in bids)
        ask_sum = sum(s for _, s in asks)
        total = bid_sum + ask_sum
        return (bid_sum - ask_sum) / total if total > 0 else 0.0

    def analyze(self) -> Level3Events:
        aggr_vol = self.aggressive_buy_volume + self.aggressive_sell_volume
        return Level3Events(
            order_book={"bids": list(self.order_book["bids"]),
                        "asks": list(self.order_book["asks"])},
            order_events=list(self.order_events),
            market_orders=self.market_orders,
            limit_orders=self.limit_orders,
            order_book_imbalance=self.compute_imbalance(),
            aggressive_buys=self.aggressive_buys,
            aggressive_sells=self.aggressive_sells,
            aggressive_buy_volume=self.aggressive_buy_volume,
            aggressive_sell_volume=self.aggressive_sell_volume,
            aggressive_flow_ratio=(self.aggressive_buy_volume / aggr_vol
                                   if aggr_vol > 0 else 0.5),
            ofi=self.ofi,
            buy_streak=self.buy_streak,
            sell_streak=self.sell_streak,
            large_order_events=self.large_order_events,
            iceberg_events=self.iceberg_events,
            timestamp=datetime.now(timezone.utc),
        )


# ----------------------------------------------------------------------------- #
# 7. VOLUME PROFILE ANALYZER  (VWAP / POC / Value Area / OBV / A/D / bands)
# ----------------------------------------------------------------------------- #


class VolumeProfileAnalyzer:
    """Volume profile & price action metrics."""

    def __init__(self, num_bins: int = 50, value_area_pct: float = 0.70):
        self.num_bins = num_bins
        self.value_area_pct = value_area_pct

    @staticmethod
    def compute_vwap(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                     volume: np.ndarray) -> float:
        high = np.asarray(high, dtype=float)
        low = np.asarray(low, dtype=float)
        close = np.asarray(close, dtype=float)
        volume = np.asarray(volume, dtype=float)
        if len(high) == 0 or volume.sum() == 0:
            return float(close[-1]) if len(close) else 0.0
        typical = (high + low + close) / 3.0
        return float(np.sum(typical * volume) / np.sum(volume))

    @staticmethod
    def compute_vwap_std(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                         volume: np.ndarray, vwap: float) -> float:
        """Standard deviation of volume-weighted typical prices around VWAP."""
        high = np.asarray(high, dtype=float)
        low = np.asarray(low, dtype=float)
        close = np.asarray(close, dtype=float)
        volume = np.asarray(volume, dtype=float)
        if len(high) == 0 or volume.sum() <= 0:
            return 0.0
        typical = (high + low + close) / 3.0
        var = float(np.sum(volume * (typical - vwap) ** 2) / np.sum(volume))
        return float(np.sqrt(var))

    def compute_poc_and_value_area(self, high: np.ndarray, low: np.ndarray,
                                   volume: np.ndarray
                                   ) -> Tuple[float, float, float]:
        high = np.asarray(high, dtype=float)
        low = np.asarray(low, dtype=float)
        volume = np.asarray(volume, dtype=float)

        # guard: drop non-finite rows (empty candles) so the profile can't crash on NaN
        mask = np.isfinite(high) & np.isfinite(low) & np.isfinite(volume)
        if not mask.all():
            high, low, volume = high[mask], low[mask], volume[mask]
        if len(high) == 0:
            return 0.0, 0.0, 0.0

        lo, hi = float(low.min()), float(high.max())
        if not (np.isfinite(lo) and np.isfinite(hi)) or hi <= lo:
            return lo, hi, lo

        bins = np.linspace(lo, hi, self.num_bins + 1)
        centers = (bins[:-1] + bins[1:]) / 2.0
        hist = np.zeros(self.num_bins)
        for h, l, v in zip(high, low, volume):
            idx = int(np.clip((((h + l) / 2.0) - lo) / (hi - lo) * self.num_bins,
                              0, self.num_bins - 1))
            hist[idx] += float(v)

        poc_idx = int(np.argmax(hist))
        poc = float(centers[poc_idx])

        total_vol = float(hist.sum())
        if total_vol <= 0:
            return poc, hi, lo
        target = total_vol * self.value_area_pct
        captured = float(hist[poc_idx])
        low_idx = high_idx = poc_idx
        while captured < target and (low_idx > 0 or high_idx < self.num_bins - 1):
            if high_idx < self.num_bins - 1 and (
                    low_idx == 0 or hist[high_idx + 1] >= hist[low_idx - 1]):
                high_idx += 1
                captured += float(hist[high_idx])
            elif low_idx > 0:
                low_idx -= 1
                captured += float(hist[low_idx])
            else:
                break
        return poc, float(centers[high_idx]), float(centers[low_idx])

    @staticmethod
    def compute_obv(close: np.ndarray, volume: np.ndarray) -> float:
        close = np.asarray(close, dtype=float)
        volume = np.asarray(volume, dtype=float)
        if len(close) < 2:
            return 0.0
        direction = np.sign(np.diff(close))
        obv = np.cumsum(np.concatenate(([0.0], direction * volume[1:])))
        return float(obv[-1])

    @staticmethod
    def compute_accumulation_distribution(high: np.ndarray, low: np.ndarray,
                                          close: np.ndarray,
                                          volume: np.ndarray) -> float:
        high = np.asarray(high, dtype=float)
        low = np.asarray(low, dtype=float)
        close = np.asarray(close, dtype=float)
        volume = np.asarray(volume, dtype=float)
        rng = (high - low) + 1e-10
        clv = ((close - low) - (high - close)) / rng
        return float(np.cumsum(clv * volume)[-1])

    @staticmethod
    def compute_volume_roc(volume: np.ndarray, period: int = 1) -> float:
        volume = np.asarray(volume, dtype=float)
        if len(volume) < period + 1 or volume[-period - 1] == 0:
            return 0.0
        return float(volume[-1] / volume[-period - 1] - 1.0)

    def analyze(self, high: np.ndarray, low: np.ndarray, close: np.ndarray,
                volume: np.ndarray) -> VolumeProfileMetrics:
        high = np.asarray(high, dtype=float)
        low = np.asarray(low, dtype=float)
        close = np.asarray(close, dtype=float)
        volume = np.asarray(volume, dtype=float)

        if len(close) == 0:
            return VolumeProfileMetrics(timestamp=datetime.now(timezone.utc))

        vwap = self.compute_vwap(high, low, close, volume)
        vwap_std = self.compute_vwap_std(high, low, close, volume, vwap)
        zscore = ((close[-1] - vwap) / vwap_std) if vwap_std > 0 else 0.0
        poc, vah, val = self.compute_poc_and_value_area(high, low, volume)

        return VolumeProfileMetrics(
            vwap=vwap,
            poc=poc,
            value_area_high=vah,
            value_area_low=val,
            volume_rate_of_change=self.compute_volume_roc(volume),
            on_balance_volume=self.compute_obv(close, volume),
            accumulation_distribution=self.compute_accumulation_distribution(
                high, low, close, volume),
            vwap_std=vwap_std,
            vwap_zscore=float(zscore),
            timestamp=datetime.now(timezone.utc),
        )


# ----------------------------------------------------------------------------- #
# 8. MACRO ANALYZER  (log-return correlations vs USD / yields / VIX)
# ----------------------------------------------------------------------------- #


class MacroAnalyzer:
    """Macro regime & correlation metrics (gold vs DXY / 10Y yield / VIX)."""

    def __init__(self, correlation_window: int = 30):
        self.correlation_window = correlation_window

    @staticmethod
    def pearson(a: Optional[Sequence[float]], b: Optional[Sequence[float]],
                use_returns: bool = True) -> float:
        """Pearson correlation over the common aligned tail.

        use_returns=True correlates log-returns (stationary) instead of raw
        price levels (non-stationary) -- the only statistically meaningful
        choice for financial time series.
        """
        if a is None or b is None:
            return 0.0
        a = np.asarray(a, dtype=float).ravel()
        b = np.asarray(b, dtype=float).ravel()
        n = min(len(a), len(b))
        if n < 3:
            return 0.0
        a, b = a[-n:], b[-n:]
        if use_returns:
            a = np.diff(np.log(np.clip(a, 1e-12, None)))
            b = np.diff(np.log(np.clip(b, 1e-12, None)))
        a = a - a.mean()
        b = b - b.mean()
        denom = np.sqrt((a * a).sum() * (b * b).sum())
        return float(np.dot(a, b) / denom) if denom > 1e-12 else 0.0

    @staticmethod
    def risk_sentiment_from_vix(vix: float) -> str:
        if vix <= 0:
            return "NEUTRAL"
        if vix < 15:
            return "RISK_ON"
        if vix > 25:
            return "RISK_OFF"
        return "NEUTRAL"

    @staticmethod
    def _rel_change(series, periods: int = 5) -> float:
        """Relative change over the last `periods` points: (last-prev)/|prev|."""
        if series is None:
            return 0.0
        arr = np.asarray(series, dtype=float).ravel()
        if len(arr) < periods + 1:
            periods = len(arr) - 1
        if periods < 1 or arr[-1 - periods] == 0:
            return 0.0
        return float((arr[-1] - arr[-1 - periods]) / abs(arr[-1 - periods]))

    @staticmethod
    def _vix_spike(series, current: float) -> float:
        """(current - median(last 20)) / median: >0 means stress rising."""
        if series is None or current <= 0:
            return 0.0
        arr = np.asarray(series, dtype=float).ravel()[-20:]
        if len(arr) < 5:
            return 0.0
        med = float(np.median(arr))
        return float((current - med) / med) if med > 0 else 0.0

    def analyze(self, macro: Dict[str, Any]) -> MacroMetrics:
        """macro expects keys: usd_index, us_10y_yield, vix_index,
        inflation_expectation (optional), and optional series
        gold_series / usd_series / yield_series / vix_series."""
        usd = float(macro.get("usd_index", 0.0) or 0.0)
        yld = float(macro.get("us_10y_yield", 0.0) or 0.0)
        vix = float(macro.get("vix_index", 0.0) or 0.0)
        inf_exp = float(macro.get("inflation_expectation", 0.0) or 0.0)
        usd_chg = self._rel_change(macro.get("usd_series"))
        yld_chg = self._rel_change(macro.get("yield_series"))
        vix_spk = self._vix_spike(macro.get("vix_series"), vix)

        gold_s = macro.get("gold_series")
        has_gold = gold_s is not None and len(np.asarray(gold_s)) > 0

        if has_gold:
            dxy_corr = self.pearson(gold_s, macro.get("usd_series"))
            yld_corr = self.pearson(gold_s, macro.get("yield_series"))
            vix_corr = self.pearson(gold_s, macro.get("vix_series"))
        else:
            dxy_corr = yld_corr = vix_corr = 0.0

        if not has_gold and macro.get("levels_gold") is not None:
            dxy_corr = self.pearson(macro.get("levels_gold"), macro.get("levels_usd"))
            yld_corr = self.pearson(macro.get("levels_gold"), macro.get("levels_yield"))
            vix_corr = self.pearson(macro.get("levels_gold"), macro.get("levels_vix"))

        return MacroMetrics(
            usd_index=usd,
            us_10y_yield=yld,
            vix_index=vix,
            dxy_correlation=dxy_corr,
            yield_correlation=yld_corr,
            vix_correlation=vix_corr,
            inflation_expectation=inf_exp,
            real_yields=yld - inf_exp,
            risk_sentiment=self.risk_sentiment_from_vix(vix),
            usd_change_5d=usd_chg,
            yield_change_5d=yld_chg,
            vix_spike=vix_spk,
            timestamp=datetime.now(timezone.utc),
        )


# ----------------------------------------------------------------------------- #
# 9. NEWS ANALYZER  (headline sentiment)
# ----------------------------------------------------------------------------- #


class NewsAnalyzer:
    """Headline sentiment using TextBlob with a financial-lexicon fallback."""

    _POSITIVE_WORDS = {
        "rally", "surge", "soar", "jump", "gain", "gains", "strong", "strength",
        "bullish", "higher", "rise", "rises", "rising", "upbeat", "beat", "beats",
        "exceed", "exceeds", "record", "optimism", "optimistic", "dovish", "cut",
        "cuts", "stimulus", "safe haven", "demand", "buying", "outperform",
        "upgrade", "rebound", "recover", "recovery", "calm", "stable", "bull",
    }
    _NEGATIVE_WORDS = {
        "plunge", "plunges", "tumble", "tumbles", "drop", "drops", "fall", "falls",
        "weak", "weakness", "bearish", "lower", "miss", "misses", "disappoint",
        "slump", "slumps", "crisis", "recession", "hawkish", "hike", "hikes",
        "inflation", "fear", "selloff", "sell-off", "concern", "warning", "warns",
        "downgrade", "crash", "panic", "turmoil", "bear", "risk", "uncertainty",
    }

    @staticmethod
    def _textblob_sentiment(text: str) -> Optional[float]:
        if TextBlob is None:
            return None
        try:
            blob = TextBlob(text)
            return float(np.clip(blob.sentiment.polarity, -1.0, 1.0))
        except Exception:  # pragma: no cover
            return None

    @classmethod
    def _lexicon_sentiment(cls, text: str) -> float:
        lowered = text.lower()
        pos = sum(1 for w in cls._POSITIVE_WORDS if w in lowered)
        neg = sum(1 for w in cls._NEGATIVE_WORDS if w in lowered)
        total = pos + neg
        return (pos - neg) / total if total > 0 else 0.0

    @classmethod
    def score_headline(cls, headline: str) -> float:
        lex = cls._lexicon_sentiment(headline)
        tb = cls._textblob_sentiment(headline)
        if tb is None:
            return lex
        return float(np.clip(0.6 * lex + 0.4 * tb, -1.0, 1.0))

    def analyze_headlines(self, headlines: List[str]) -> Tuple[float, str]:
        if not headlines:
            return 0.0, "NEUTRAL"
        scores = [self.score_headline(h) for h in headlines if h]
        if not scores:
            return 0.0, "NEUTRAL"
        score = float(np.mean(scores))
        if score > 0.15:
            label = "BULLISH"
        elif score < -0.15:
            label = "BEARISH"
        else:
            label = "NEUTRAL"
        return score, label


# ----------------------------------------------------------------------------- #
# 10. ECONOMIC CALENDAR  (+ news-time state machine)
# ----------------------------------------------------------------------------- #


# v4.4.2: bump when the cached-event format changes, so old caches are
# treated as stale and refetched immediately
_CALENDAR_CACHE_VERSION = 2


class EconomicCalendar:
    """Upcoming economic events + a news-time state machine.

    news_state() classifies "now" relative to upcoming high-impact events:
        QUIET    : no event nearby
        WARNING  : approaching an event (within the warning window)
        BLACKOUT : inside the no-trade window around an event
    """

    # Try several mirrors — some ISPs/DNS resolvers can't reach the CDN host.
    LIVE_URLS = [
        "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
        "https://cdn-nfs.faireconomy.media/ff_calendar_thisweek.json",
    ]
    LIVE_URL = LIVE_URLS[0]

    def __init__(self, timeout: int = 8):
        self.timeout = timeout

    # -- local cache (the free calendar host rate-limits, so fetch rarely) ----
    def _read_cached_events(self) -> tuple:
        """Return (events, is_fresh) from the on-disk cache."""
        from pathlib import Path
        path = Path(__file__).resolve().parent / "data" / "calendar_cache.json"
        try:
            if path.exists():
                state = json.loads(path.read_text(encoding="utf-8"))
                if state.get("v") != _CALENDAR_CACHE_VERSION:
                    return [], False     # v4.4.2: old-format cache is stale
                events = state.get("events") or []
                fetched = float(state.get("fetched_at", 0) or 0)
                fresh = bool(events) and (time.time() - fetched) < 3600.0
                return events, fresh
        except (json.JSONDecodeError, OSError, ValueError):
            pass
        return [], False

    def _write_cached_events(self, events: List[Dict]) -> None:
        from pathlib import Path
        path = Path(__file__).resolve().parent / "data" / "calendar_cache.json"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(
                {"v": _CALENDAR_CACHE_VERSION, "fetched_at": time.time(),
                 "events": events}), encoding="utf-8")
        except OSError:
            pass

    # -- live fetch ------------------------------------------------------------
    def fetch_live_events(self, max_events: int = 20) -> List[Dict]:
        if requests is None:
            return []
        cached, fresh = self._read_cached_events()
        if fresh:
            return cached[:max_events]

        last_exc: Optional[Exception] = None
        for url in self.LIVE_URLS:
            try:
                resp = requests.get(url, timeout=self.timeout,
                                    headers={"User-Agent": "market-analysis/2.0"})
                resp.raise_for_status()
                data = resp.json()
                # v4.4.2: the week file starts on SUNDAY. The old code took
                # the FIRST max_events items, so by mid-week every event was
                # days old, news_state() saw nothing upcoming, and the news
                # perimeter (WARNING / BLACKOUT / PM tighten) was stuck in
                # QUIET all week. Keep only events that have not long
                # passed, sort by event time, THEN take the first N (the
                # nearest upcoming ones).
                now = datetime.now(timezone.utc)
                keep_from = now - timedelta(hours=2)  # let just-released events finish their window
                parsed_events: List[Tuple[Optional[datetime], Dict]] = []
                for item in data:
                    title = str(item.get("title", "") or item.get("event", "") or "").strip()
                    if not title:
                        continue
                    impact = str(item.get("impact", "") or "").upper()
                    if impact in ("HIGH", "HOCH", "ALTO"):
                        impact = "HIGH"
                    elif impact in ("MEDIUM", "MITTEL", "MEDIO"):
                        impact = "MEDIUM"
                    else:
                        impact = "LOW"
                    event = {
                        "title": title,
                        "country": str(item.get("country", "") or ""),
                        "impact": impact,
                        "date": str(item.get("date", "") or ""),
                        "forecast": item.get("forecast", ""),
                        "previous": item.get("previous", ""),
                    }
                    dt = self._parse_event_time(event["date"], now)
                    if dt is None or dt >= keep_from:
                        parsed_events.append((dt, event))
                far_future = datetime.max.replace(tzinfo=timezone.utc)
                parsed_events.sort(key=lambda p: p[0] or far_future)
                events = [e for _, e in parsed_events[:max_events]]
                self._write_cached_events(events)
                return events
            except Exception as exc:
                last_exc = exc
                continue

        if cached:   # stale real events are still better than fake placeholders
            logger.info("Economic calendar live fetch failed (%s); "
                        "using cached copy.", last_exc)
            return cached[:max_events]
        logger.info("Economic calendar live fetch failed (%s); using fallback.",
                    last_exc)
        return []

    # -- offline fallback ------------------------------------------------------
    def fallback_events(self) -> List[Dict]:
        now = datetime.now(timezone.utc)
        return [
            {"title": "FOMC Interest Rate Decision", "country": "USD",
             "impact": "HIGH", "date": (now + timedelta(days=2)).isoformat(),
             "forecast": "", "previous": ""},
            {"title": "CPI Inflation (YoY)", "country": "USD",
             "impact": "HIGH", "date": (now + timedelta(days=4)).isoformat(),
             "forecast": "", "previous": ""},
            {"title": "Non-Farm Payrolls", "country": "USD",
             "impact": "HIGH", "date": (now + timedelta(days=6)).isoformat(),
             "forecast": "", "previous": ""},
            {"title": "Unemployment Claims", "country": "USD",
             "impact": "MEDIUM", "date": (now + timedelta(days=3)).isoformat(),
             "forecast": "", "previous": ""},
            {"title": "Retail Sales (MoM)", "country": "USD",
             "impact": "MEDIUM", "date": (now + timedelta(days=5)).isoformat(),
             "forecast": "", "previous": ""},
        ]

    def get_upcoming_events(self, max_events: int = 20) -> List[Dict]:
        events = self.fetch_live_events(max_events=max_events)
        return events or self.fallback_events()

    # -- datetime parsing ------------------------------------------------------
    @staticmethod
    def _parse_event_time(date_value: Any, now: datetime) -> Optional[datetime]:
        if isinstance(date_value, datetime):
            dt = date_value
        else:
            s = str(date_value or "").strip()
            if not s:
                return None
            try:
                dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            except ValueError:
                try:
                    dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=now.tzinfo if now.tzinfo else timezone.utc)
        return dt

    # -- news-time state -------------------------------------------------------
    def news_state(self, events: List[Dict], now: Optional[datetime] = None,
                   warning_minutes: Optional[float] = None,
                   blackout_before: Optional[float] = None,
                   blackout_after: Optional[float] = None
                   ) -> Tuple[str, float, str]:
        """Classify the news-time state.

        Returns (state, minutes_to_next_event, next_event_title) where state is
        QUIET / WARNING / BLACKOUT. Windows come from config when available.
        """
        try:
            import config as _cfg
            warning_minutes = warning_minutes if warning_minutes is not None \
                else _cfg.NEWS_WARNING_MINUTES
            blackout_before = blackout_before if blackout_before is not None \
                else _cfg.NEWS_BLACKOUT_BEFORE_MINUTES
            blackout_after = blackout_after if blackout_after is not None \
                else _cfg.NEWS_BLACKOUT_AFTER_MINUTES
        except ImportError:
            warning_minutes = warning_minutes or 30.0
            blackout_before = blackout_before or 15.0
            blackout_after = blackout_after or 30.0

        now = now or datetime.now(timezone.utc)
        if not events:
            return "QUIET", 0.0, ""

        # v4.4.2: LOW-impact events (housing data, bond auctions, speeches)
        # must never gate trading — the perimeter is for events that move
        # gold. Tunable: NEWS_PERIMETER_IGNORE_LOW=0 in .env restores the
        # old react-to-everything behavior.
        ignore_low = True
        try:
            ignore_low = getattr(config, "NEWS_PERIMETER_IGNORE_LOW", True)
        except Exception:
            pass

        timed = []
        for e in events:
            if ignore_low and str(e.get("impact", "")).upper() == "LOW":
                continue
            dt = self._parse_event_time(e.get("date"), now)
            if dt is not None:
                timed.append((dt, e))
        timed.sort(key=lambda x: x[0])

        for dt, e in timed:
            minutes = (dt - now).total_seconds() / 60.0
            if minutes < -blackout_after:
                continue                      # event long passed
            title = str(e.get("title", ""))
            if -blackout_after <= minutes <= blackout_before:
                return "BLACKOUT", minutes, title
            if blackout_before < minutes <= warning_minutes:
                return "WARNING", minutes, title
            return "QUIET", minutes, title     # far in the future

        return "QUIET", 0.0, ""

    # -- event classification --------------------------------------------------
    @classmethod
    def classify(cls, events: List[Dict]) -> Tuple[str, str]:
        """Return (event_type, impact_level) of the most important upcoming event."""
        if not events:
            return "None", "LOW"
        high = [e for e in events if str(e.get("impact", "")).upper() == "HIGH"]
        pool = high or events
        impact_rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        pool = sorted(pool, key=lambda e: (impact_rank.get(
            str(e.get("impact", "")).upper(), 2), str(e.get("date", ""))))
        top = pool[0]
        title = str(top.get("title", "")).lower()
        if any(k in title for k in ("fomc", "fed", "rate decision")):
            event_type = "FOMC"
        elif "cpi" in title or "inflation" in title:
            event_type = "CPI"
        elif "nonfarm" in title or "nfp" in title or "payroll" in title:
            event_type = "Jobs"
        elif "gdp" in title:
            event_type = "GDP"
        else:
            event_type = title.title()[:40]
        return event_type, str(top.get("impact", "LOW")).upper()


# ----------------------------------------------------------------------------- #
# 11. SIGNAL ENGINE  (regime-aware, divergence-aware, news-gated)
# ----------------------------------------------------------------------------- #


class SignalEngine:
    """Aggregates all metric groups into a single trading signal."""

    BUY_THRESHOLD = 15.0    # fallback only — real value comes from
    SELL_THRESHOLD = -15.0  # .env SIGNAL_BUY/SELL_THRESHOLD (v4.4)

    def _thresholds(self):
        """Entry thresholds, tunable from .env without touching code."""
        return (float(getattr(config, "SIGNAL_BUY_THRESHOLD", self.BUY_THRESHOLD)),
                float(getattr(config, "SIGNAL_SELL_THRESHOLD", self.SELL_THRESHOLD)))

    def aggregate(self, price: float, volatility: VolatilityMetrics,
                  trend: TrendMetrics, order_flow: OrderFlowMetrics,
                  footprint: FootprintMetrics, level3: Level3Events,
                  volume_profile: VolumeProfileMetrics, macro: MacroMetrics,
                  news: NewsAndEvents, regime: str = "NEUTRAL",
                  divergence: float = 0.0,
                  mtf_trends: Optional[Dict[str, str]] = None,
                  nearest_support: float = 0.0,
                  nearest_resistance: float = 0.0,
                  recent_closes: Optional[np.ndarray] = None,
                  asia_range: Optional[Dict[str, Any]] = None
                  ) -> Tuple[float, str, float, List[str]]:
        """Return (signal_strength 0-100, direction, confidence 0-100, notes)."""
        votes: List[Tuple[float, float]] = []
        macro_pairs: List[Tuple[float, float]] = []   # macro votes, for the opposition rule
        notes: List[str] = []

        # ---- 0) NEWS-TIME GATE ----------------------------------------------
        if news.news_state == "BLACKOUT":
            notes.append("news BLACKOUT -> no signal (event in %.0f min)"
                         % news.minutes_to_next_event)
            return 0.0, "NEUTRAL", 0.0, notes

        # ---- 0b) multi-timeframe confirmation (H1 -> M15 -> M5 vs M1) --------
        # Each higher timeframe votes WITH or AGAINST the M1 signal, weighted by
        # its importance (H1 counts most). Stacked agreement = strong signal;
        # disagreement weakens it — this filters trades that fight the trend.
        mtf_trends = mtf_trends or {}
        tf_weights = {"H1": float(getattr(config, "SIGNAL_W_H1", 1.0)),
                      "M15": float(getattr(config, "SIGNAL_W_M15", 0.8)),
                      "M5": float(getattr(config, "SIGNAL_W_M5", 0.6))}
        for tf_name in ("H1", "M15", "M5"):
            tf_dir = mtf_trends.get(tf_name)
            if tf_dir == "UP":
                votes.append((+1.0, tf_weights[tf_name]))
            elif tf_dir == "DOWN":
                votes.append((-1.0, tf_weights[tf_name]))
        if mtf_trends:
            summary = " ".join(f"{k}:{v}" for k, v in sorted(mtf_trends.items()))
            notes.append(f"MTF {summary}")

        # ---- 1) Trend / momentum ---------------------------------------------
        if trend.trend_direction == "UP":
            votes.append((+1.0, float(getattr(config, "SIGNAL_W_TREND", 0.5))
                         + 0.5 * (trend.trend_strength / 100.0)))
        elif trend.trend_direction == "DOWN":
            votes.append((-1.0, float(getattr(config, "SIGNAL_W_TREND", 0.5))
                         + 0.5 * (trend.trend_strength / 100.0)))
        else:
            notes.append("trend neutral")

        votes.append((np.clip(trend.macd_histogram * 10.0, -1.0, 1.0),
                   float(getattr(config, "SIGNAL_W_MACD", 0.6))))
        if trend.rsi > 55:
            votes.append((+1.0, float(getattr(config, "SIGNAL_W_EMA_CROSS", 0.5))))
        elif trend.rsi < 45:
            votes.append((-1.0, float(getattr(config, "SIGNAL_W_EMA_CROSS", 0.5))))
        votes.append((np.sign(price - trend.sma_50) if trend.sma_50 else 0.0,
                   float(getattr(config, "SIGNAL_W_SMA50", 0.7))))
        votes.append((np.sign(price - trend.sma_20) if trend.sma_20 else 0.0,
                   float(getattr(config, "SIGNAL_W_SMA20", 0.5))))

        # ---- 2) Order flow (tick) --------------------------------------------
        # volume-RELATIVE aggression (buy%% - sell%%): immune to thin-market
        # distortion (100 contracts means nothing without volume context)
        votes.append((np.clip((order_flow.buying_pressure -
                               order_flow.selling_pressure) / 100.0, -1.0, 1.0),
                   float(getattr(config, "SIGNAL_W_PRESSURE", 0.8))))
        votes.append((np.sign(order_flow.cvd),
                   float(getattr(config, "SIGNAL_W_CVD", 0.6))))
        votes.append((np.clip((order_flow.bid_ask_ratio - 1.0) * 2.0, -1.0, 1.0),
                   float(getattr(config, "SIGNAL_W_BIDASK", 0.6))))

        # ---- 3) LEVEL 2 depth analytics --------------------------------------
        # order-flow imbalance (leading indicator of short-term direction)
        book_size = order_flow.level2_bid_depth + order_flow.level2_ask_depth
        votes.append((np.tanh(order_flow.ofi / max(book_size, 1.0) * 5.0),
                   float(getattr(config, "SIGNAL_W_OFI", 0.9))))
        votes.append((np.clip(order_flow.depth_imbalance * 2.5, -1.0, 1.0),
                   float(getattr(config, "SIGNAL_W_DEPTH", 0.7))))
        # microprice: where fair value sits vs last price
        if order_flow.microprice:
            scale = volatility.atr or (price * 0.001)
            votes.append((np.clip((price - order_flow.microprice) / scale, -1.0, 1.0),
                   float(getattr(config, "SIGNAL_W_MICRO", 0.5))))
        # absorption: net ask-absorption (buyers) vs bid-absorption (sellers)
        votes.append((np.clip(order_flow.absorption_net * 0.5, -1.0, 1.0),
                   float(getattr(config, "SIGNAL_W_ABSORB", 0.5))))

        # ---- 4) Footprint ----------------------------------------------------
        votes.append((footprint.delta_imbalance,
                   float(getattr(config, "SIGNAL_W_FOOTPRINT", 0.7))))

        # ---- 5) LEVEL 3 order events -----------------------------------------
        votes.append((np.clip(level3.order_book_imbalance * 2.0, -1.0, 1.0),
                   float(getattr(config, "SIGNAL_W_L3_IMB", 0.6))))
        aggr_vol = level3.aggressive_buy_volume + level3.aggressive_sell_volume
        votes.append((np.tanh(level3.ofi / max(aggr_vol, 1.0) * 5.0),
                   float(getattr(config, "SIGNAL_W_L3_OFI", 0.8))))
        ar = level3.aggressive_flow_ratio
        votes.append((np.clip((ar - 0.5) * 4.0, -1.0, 1.0),
                   float(getattr(config, "SIGNAL_W_L3_AGGR", 0.8))))
        # streaks: momentum in aggressor flow
        if level3.buy_streak >= 3:
            votes.append((+1.0, 0.3))
        if level3.sell_streak >= 3:
            votes.append((-1.0, 0.3))

        # ---- 6) CVD-price divergence -----------------------------------------
        if divergence > 0:
            votes.append((+1.0, float(getattr(config, "SIGNAL_W_DIVERGENCE", 1.2))))
            notes.append("bullish CVD divergence (price down, buying up)")
        elif divergence < 0:
            votes.append((-1.0, float(getattr(config, "SIGNAL_W_DIVERGENCE", 1.2))))
            notes.append("bearish CVD divergence (price up, selling up)")

        # ---- 7) Volume profile (regime-aware) --------------------------------
        if volume_profile.vwap:
            votes.append((np.sign(price - volume_profile.vwap),
                   float(getattr(config, "SIGNAL_W_VWAP", 0.6))))
        if volume_profile.poc:
            votes.append((np.sign(price - volume_profile.poc), 0.4))
        # VWAP z-score mean-reversion: only fade extremes in a RANGE regime
        z = volume_profile.vwap_zscore
        if regime == "RANGE" and abs(z) > 1.5:
            # v3: compressed volatility strengthens the fade; an expanding
            # regime (high volatility_rank) weakens it -- breakouts run.
            fade_w = 0.8 if float(volatility.volatility_rank or 0.0) < 0.5 else 0.4
            votes.append((-np.sign(z) * min(abs(z) / 2.0, 1.0), fade_w))
            notes.append("VWAP z-score %.1f -> mean reversion fade" % z)

        # ---- 7b) order blocks / supply-demand zones --------------------------
        # Price near a demand zone -> likely bounce (bullish). Price near a
        # supply zone -> likely rejection (bearish). Zones are the "smart
        # money" levels where the big players previously acted.
        atr_zone = volatility.atr or (price * 0.002)
        if nearest_support and 0 < (price - nearest_support) <= atr_zone:
            votes.append((+1.0, 0.6))
            notes.append("near demand zone %.1f (bounce)" % nearest_support)
        if nearest_resistance and 0 < (nearest_resistance - price) <= atr_zone:
            votes.append((-1.0, 0.6))
            notes.append("near supply zone %.1f (rejection)" % nearest_resistance)

        # ---- 7c) round numbers (3-state, flow-dependent) ----------------------
        # Gold front-runs round numbers: magnet on approach, fade at a defended
        # level, ride a break ONLY with flow confirmation. The number is the
        # location; the flow is the judge.
        atr_rn = volatility.atr or (price * 0.002)
        lb, wlb, la, wla = _round_level_tier(price)
        level, tier_w = (lb, wlb) if (price - lb) <= (la - price) else (la, wla)
        if level and tier_w > 0:
            dist = abs(price - level)
            flow_dir = float(np.sign(order_flow.delta)) if order_flow.delta else 0.0
            recent = (np.asarray(recent_closes, dtype=float)
                      if recent_closes is not None else np.asarray([], dtype=float))
            broke = (len(recent) >= 3 and
                     (recent[-3] < level <= price or recent[-3] > level >= price))
            if broke and dist <= 1.5 * atr_rn:
                brk_dir = 1.0 if price > level else -1.0
                if flow_dir == brk_dir:
                    votes.append((brk_dir, 0.6 * tier_w))
                    notes.append("broke round %.0f with flow -> continuation" % level)
                else:
                    votes.append((-brk_dir, 0.4 * tier_w))
                    notes.append("failed break of round %.0f (no flow) -> snapback" % level)
            elif dist <= 0.5 * atr_rn:
                if flow_dir != 0:
                    votes.append((flow_dir, 0.6 * tier_w))
                    notes.append("round %.0f tested, flow %s -> follow flow"
                                 % (level, "up" if flow_dir > 0 else "down"))
                else:
                    votes.append((-1.0 if level > price else 1.0, 0.4 * tier_w))
                    notes.append("round %.0f defended (no flow) -> fade" % level)
            elif dist <= 2.5 * atr_rn:
                votes.append((1.0 if level > price else -1.0, 0.3 * tier_w))
                notes.append("magnet: price drifting toward round %.0f" % level)

        # ---- 7d) Asian range / London breakout ---------------------------------
        # Asia builds the box (00:00-07:00 UTC); London morning often delivers
        # the directional move of the day when it escapes that box.
        if asia_range and asia_range.get("session") == "LONDON_MORNING":
            brk = asia_range.get("breakout", 0)
            if brk:
                votes.append((float(brk), 0.8))
                notes.append("London breakout of Asian range (%.1f/%.1f) -> %s"
                             % (asia_range.get("high", 0.0),
                                asia_range.get("low", 0.0),
                                "UP" if brk > 0 else "DOWN"))

        # ---- 8) Macro (CHANGE-based: gold trades the DIRECTION of the macro
        # driver, not its level -- see docs/GOLD_MARKET_DRIVERS.md) -----------
        # Rising yields raise gold's opportunity cost -> bearish (~2% relative
        # 5-session move in the 10Y = full-strength vote).
        y_chg = macro.yield_change_5d
        if abs(y_chg) > 1e-6:
            v = (-np.clip(y_chg / 0.02, -1.0, 1.0), 0.8)
            votes.append(v); macro_pairs.append(v)
            notes.append("10Y %s %.2f%%/5d" %
                         ("rising" if y_chg > 0 else "falling", abs(y_chg) * 100.0))
        # Rising dollar pressures USD-priced gold -> bearish (~1% = full vote).
        d_chg = macro.usd_change_5d
        if abs(d_chg) > 1e-6:
            v = (-np.clip(d_chg / 0.01, -1.0, 1.0), 0.6)
            votes.append(v); macro_pairs.append(v)
            notes.append("DXY %s %.2f%%/5d" %
                         ("rising" if d_chg > 0 else "falling", abs(d_chg) * 100.0))
        # VIX stress spike vs its own 20-session median -> safe-haven bid.
        v_spk = macro.vix_spike
        if v_spk > 0.05:
            v = (+np.clip(v_spk / 0.20, 0.0, 1.0), 0.4)
            votes.append(v); macro_pairs.append(v)
            notes.append("VIX stress +%.0f%% vs 20d median" % (v_spk * 100.0))
        if macro.risk_sentiment == "RISK_OFF":
            v = (+1.0, 0.5)
            votes.append(v); macro_pairs.append(v)
            notes.append("risk-off -> safe haven bid")
        elif macro.risk_sentiment == "RISK_ON":
            v = (-1.0, 0.4)
            votes.append(v); macro_pairs.append(v)

        # ---- 9) News sentiment ----------------------------------------------
        votes.append((news.sentiment_score, 0.4))

        # ---- weighted composite ----------------------------------------------
        total_weight = sum(w for _, w in votes) or 1.0
        score = sum(s * w for s, w in votes) / total_weight
        score_scaled = float(np.clip(score, -1.0, 1.0) * 100.0)

        buy_thr, sell_thr = self._thresholds()
        if score_scaled >= buy_thr:
            direction = "BUY"
        elif score_scaled <= sell_thr:
            direction = "SELL"
        else:
            direction = "NEUTRAL"

        # ---- macro-opposition rule -------------------------------------------
        # "You may fight the macro, but only with flow evidence." Opposition
        # shrinks the score up to 50%; EXTREME opposition (>0.8) without order-
        # flow confirmation (delta or OFI agreeing with the direction) caps the
        # signal to NEUTRAL.
        self.last_macro_bias = 0.0   # v4.4: fresh reading for the PM
        if direction in ("BUY", "SELL"):
            dir_sign = 1.0 if direction == "BUY" else -1.0
            m_w = sum(w for _, w in macro_pairs)
            if m_w > 0:
                macro_bias = sum(s * w for s, w in macro_pairs) / m_w  # -1..+1
                self.last_macro_bias = float(macro_bias)
                opposition = max(0.0, -dir_sign * macro_bias)          # 0..1
                if opposition > 0.05:
                    score_scaled *= (1.0 - 0.5 * opposition)
                    notes.append("macro against %s -> score scaled to %.0f%%"
                                 % (direction, (1.0 - 0.5 * opposition) * 100.0))
                    if opposition > 0.8:
                        flow_confirms = (np.sign(order_flow.delta) == dir_sign or
                                         np.sign(order_flow.ofi) == dir_sign)
                        if not flow_confirms:
                            notes.append("extreme macro opposition without flow "
                                         "confirmation -> capped to NEUTRAL")
                            score_scaled = 0.0
                if score_scaled >= buy_thr:
                    direction = "BUY"
                elif score_scaled <= sell_thr:
                    direction = "SELL"
                else:
                    direction = "NEUTRAL"

        # confidence = agreement + strength
        agreement = sum(1 for s, _ in votes if (direction == "BUY" and s > 0)
                        or (direction == "SELL" and s < 0)
                        or (direction == "NEUTRAL" and abs(s) < 0.3))
        confidence = 100.0 * agreement / max(len(votes), 1)
        confidence *= 0.5 + 0.5 * (abs(score_scaled) / 100.0)

        # no information at all (empty feed) -> no confidence, no signal
        if not any(abs(s) > 1e-9 for s, _ in votes):
            confidence = 0.0

        # news-time modifiers
        if news.news_state == "WARNING":
            confidence *= 0.7
            notes.append("news WARNING -> confidence reduced")
        if news.impact_level == "HIGH":
            confidence *= 0.9
            notes.append("high-impact event upcoming -> confidence reduced")

        return (round(abs(score_scaled), 2), direction,
                round(float(np.clip(confidence, 0, 100)), 2), notes)


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


def _detect_order_blocks(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                         open_: np.ndarray, k: int = 3,
                         max_zones: int = 8) -> List[Dict]:
    """Detect supply (resistance) and demand (support) zones from swing points.

    A transparent "smart money" approximation built from candles: a swing high
    forms a SUPPLY zone (where sellers stepped in), a swing low forms a DEMAND
    zone (where buyers stepped in). Each zone stores its top/bottom and its bar
    index (recency). The most recent zones act as support/resistance.
    """
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)
    open_ = np.asarray(open_, dtype=float)
    n = len(close)
    if n < 2 * k + 4:
        return []
    zones: List[Dict] = []
    for i in range(k, n - k):
        win_h = high[i - k:i + k + 1]
        win_l = low[i - k:i + k + 1]
        if high[i] == win_h.max():
            zones.append({"type": "supply",
                          "top": float(high[i]),
                          "bottom": float(min(open_[i], close[i])),
                          "bar": int(i)})
        if low[i] == win_l.min():
            zones.append({"type": "demand",
                          "top": float(max(open_[i], close[i])),
                          "bottom": float(low[i]),
                          "bar": int(i)})
    zones.sort(key=lambda z: z["bar"], reverse=True)   # most recent first
    return zones[:max_zones]


def _nearest_zones(zones: List[Dict], price: float) -> Tuple[float, float]:
    """Return (nearest_support, nearest_resistance) relative to `price`."""
    supports = [z for z in zones if z["type"] == "demand" and z["bottom"] < price]
    resistances = [z for z in zones if z["type"] == "supply" and z["top"] > price]
    support = max((z["bottom"] for z in supports), default=0.0)
    resistance = min((z["top"] for z in resistances), default=0.0)
    return support, resistance


# --------------------------------------------------------------------------- #
# Persistent Level-2 state (makes OFI & absorption work ACROSS cycles)
# --------------------------------------------------------------------------- #
_PERSISTENT_L2: Optional[OrderBookDepthAnalyzer] = None
_BOOK_STATE_PATH = None


def _book_state_file():
    global _BOOK_STATE_PATH
    if _BOOK_STATE_PATH is None:
        from pathlib import Path
        _BOOK_STATE_PATH = Path(__file__).resolve().parent / "data" / \
            "last_book_state.json"
    return _BOOK_STATE_PATH


def _get_persistent_l2() -> OrderBookDepthAnalyzer:
    """Return the process-wide L2 analyzer, restoring the previous book state.

    This is what makes Order Flow Imbalance (OFI) and absorption detection
    actually work: without remembering the PREVIOUS order book, OFI is always
    zero. The previous book is restored from a small state file so it also
    survives a restart.
    """
    global _PERSISTENT_L2
    if _PERSISTENT_L2 is None:
        _PERSISTENT_L2 = OrderBookDepthAnalyzer(levels=5)
        try:
            path = _book_state_file()
            if path.exists():
                state = json.loads(path.read_text(encoding="utf-8"))
                bids = {float(k): float(v)
                        for k, v in state.get("bids", {}).items()}
                asks = {float(k): float(v)
                        for k, v in state.get("asks", {}).items()}
                if bids or asks:
                    _PERSISTENT_L2._prev_bids = bids
                    _PERSISTENT_L2._prev_asks = asks
        except Exception:
            pass
    return _PERSISTENT_L2


def _save_book_state(bids: Dict[float, float], asks: Dict[float, float]) -> None:
    try:
        path = _book_state_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"bids": {str(k): v for k, v in bids.items()},
                                    "asks": {str(k): v for k, v in asks.items()}}),
                        encoding="utf-8")
    except Exception:
        pass


def _detect_divergence(close: np.ndarray, cvd: float, lookback: int = 15) -> float:
    """CVD-price divergence: +1 bullish, -1 bearish, 0 none."""
    close = np.asarray(close, dtype=float)
    if len(close) < lookback or cvd == 0:
        return 0.0
    price_roc = close[-1] / close[-lookback] - 1.0
    if price_roc > 0.001 and cvd < 0:
        return -1.0          # price rising on net selling -> bearish
    if price_roc < -0.001 and cvd > 0:
        return +1.0          # price falling on net buying -> bullish
    return 0.0


def analyze_market(market_data: Dict[str, Any],
                   now: Optional[datetime] = None) -> MarketSnapshot:
    """Orchestrate every analyzer into a single MarketSnapshot.

    Expected `market_data` keys:
      symbol        : str
      price/bid/ask : float
      volume        : float (current bar/session volume)
      tick_data     : list[dict]  ({price, volume, side}) - side optional (tick rule)
      bid_depth     : dict {price: size}         # Level 2
      ask_depth     : dict {price: size}
      book_updates  : list[dict] ({bids, asks})  # successive L2 snapshots -> OFI
      order_events  : list[dict]  ({type, side, price, size, is_market_order,
                                    old_size})  # Level 3
      order_book    : dict {"bids": [(p,s),...], "asks": [(p,s),...]}
      candles       : dict/DataFrame {open, high, low, close, volume}
      macro         : dict (see MacroAnalyzer.analyze)
      news          : dict {"headlines": [...], "events": [...],
                            "fetch_calendar": bool}
    `now` overrides the clock (used to simulate news-time states in tests).
    """
    now = now or datetime.now(timezone.utc)
    price = float(market_data.get("price") or market_data.get("bid") or 0.0)
    bid = float(market_data.get("bid") or price)
    ask = float(market_data.get("ask") or price)
    volume = float(market_data.get("volume") or 0.0)
    symbol = str(market_data.get("symbol", ""))
    notes: List[str] = []

    tick_data = market_data.get("tick_data") or []
    open_, high, low, close, vol = _extract_candles(market_data)

    # ---- technicals ---------------------------------------------------------
    ta = TechnicalAnalyzer()
    volatility = VolatilityMetrics(timestamp=now)
    trend = TrendMetrics(timestamp=now)

    if len(close) >= 2:
        atr = ta.compute_atr(high, low, close)
        # ATR fallback ladder: compute_atr needs 15 candles; on a fresh
        # NT bridge file there may be only a handful. A dead ATR (0.0)
        # silently inflates every stop distance downstream (the manager
        # would fall back to 0.5% of price ~ 21 pts — far too wide for
        # gold intraday). Use the best available estimate instead:
        #   1. mean M1 range (same scale as a true M1 ATR)
        #   2. traded tick range * 0.25 (very rough, minutes of data)
        if atr <= 0:
            atr = float(np.mean(np.asarray(high, dtype=float) -
                                np.asarray(low, dtype=float)))
            notes.append(f"ATR estimated from M1 range ({atr:.2f})")
        bb_u, bb_m, bb_l = ta.compute_bollinger_bands(close)
        atr_pct = (atr / close[-1] * 100.0) if close[-1] else 0.0
        rng = float(np.ptp(close[-52:])) if len(close) >= 2 else 0.0
        vol_rank = float(np.clip(atr / (rng + 1e-10), 0.0, 1.0)) if rng > 0 else 0.0
        volatility = VolatilityMetrics(
            atr=atr, atr_percent=atr_pct, bb_upper=bb_u, bb_middle=bb_m,
            bb_lower=bb_l, bb_width=((bb_u - bb_l) / bb_m) if bb_m else 0.0,
            volatility_rank=vol_rank, timestamp=now)

        mas = ta.compute_moving_averages(close)
        adx, pdi, mdi = ta.compute_adx(high, low, close)
        macd, macd_sig, macd_hist = ta.compute_macd(close)
        rsi = ta.compute_rsi(close)
        t_dir, t_strength = ta.compute_trend(close, high, low)
        trend = TrendMetrics(
            sma_9=mas["sma_9"], sma_20=mas["sma_20"], sma_50=mas["sma_50"],
            ema_12=mas["ema_12"], ema_26=mas["ema_26"], adx=adx, plus_di=pdi,
            minus_di=mdi, macd=macd, macd_signal=macd_sig, macd_histogram=macd_hist,
            rsi=rsi, trend_direction=t_dir, trend_strength=t_strength,
            timestamp=now)
    else:
        notes.append("insufficient candle data -> technicals defaulted")

    # ATR ladder step 2: no candles at all, but ticks exist -> rough range
    if volatility.atr <= 0 and tick_data:
        prices = [float(t.get("price", 0.0) or 0.0) for t in tick_data[-400:]]
        prices = [p for p in prices if p > 0]
        if len(prices) >= 20:
            rough = (max(prices) - min(prices)) * 0.25
            if rough > 0:
                volatility.atr = float(rough)
                notes.append(f"ATR estimated from tick range ({rough:.2f})")

    # visibility: a shallow candle history silently disables ATR, MTF,
    # order blocks and HTF POC — say so, so it is obvious when it heals
    if 2 <= len(close) < 60:
        notes.append(f"candle history shallow ({len(close)} M1 bars) — "
                     f"ATR/MTF/order blocks/HTF POC limited until the "
                     f"window fills")

    # ---- Level 2 depth analytics (OFI / microprice / imbalance / absorption) -
    # Uses a PERSISTENT analyzer so OFI and absorption have a previous book to
    # compare against (a fresh analyzer each cycle would always see OFI == 0).
    l2 = _get_persistent_l2()
    for upd in market_data.get("book_updates") or []:
        l2.update(upd.get("bids"), upd.get("asks"))
    bid_depth = market_data.get("bid_depth") or {}
    ask_depth = market_data.get("ask_depth") or {}
    book = market_data.get("order_book")
    if (not bid_depth or not ask_depth) and book:
        bid_depth = bid_depth or dict(book.get("bids", []))
        ask_depth = ask_depth or dict(book.get("asks", []))
    if bid_depth or ask_depth:
        l2.update(bid_depth, ask_depth)      # final snapshot (also feeds OFI)
        _save_book_state(bid_depth, ask_depth)
    l2_metrics = l2.summary(bid_depth, ask_depth)

    # ---- order flow (ticks) --------------------------------------------------
    order_flow = OrderFlowAnalyzer().analyze_tick_data(
        tick_data, bid_depth, ask_depth, l2_metrics=l2_metrics)

    # ---- footprint -----------------------------------------------------------
    footprint = FootprintBuilder().build_footprint(tick_data)

    # ---- level 3 -------------------------------------------------------------
    l3 = Level3OrderBookAnalyzer()
    for ev in market_data.get("order_events") or []:
        l3.process_order_event(ev)
    if book:
        l3.update_order_book(book.get("bids", []), book.get("asks", []))
    level3 = l3.analyze()

    # ---- volume profile ------------------------------------------------------
    vp = VolumeProfileAnalyzer().analyze(high, low, close, vol) if len(close) else \
        VolumeProfileMetrics(timestamp=now)

    # ---- macro ---------------------------------------------------------------
    macro = MacroAnalyzer().analyze(market_data.get("macro") or {})

    # ---- news & events (+ news-time state) ------------------------------------
    news_data = market_data.get("news") or {}
    headlines = list(news_data.get("headlines") or [])
    events = list(news_data.get("events") or [])
    if not events and news_data.get("fetch_calendar", True):
        events = EconomicCalendar().get_upcoming_events()
    sent_score, sent_label = NewsAnalyzer().analyze_headlines(headlines)
    event_type, impact = EconomicCalendar.classify(events)
    news_state, minutes_to_event, next_title = EconomicCalendar().news_state(
        events, now=now)
    news = NewsAndEvents(
        event_type=event_type, impact_level=impact, sentiment_score=sent_score,
        sentiment_label=sent_label, upcoming_events=events,
        news_headlines=headlines, news_state=news_state,
        minutes_to_next_event=minutes_to_event, next_event_title=next_title,
        timestamp=now)

    # ---- regime & divergence --------------------------------------------------
    regime = _detect_regime(trend.adx)
    divergence = _detect_divergence(close, order_flow.cvd)

    # ---- multi-timeframe confirmation + spread -------------------------------
    mtf_trends = _detect_mtf_trends(market_data)
    # v3: no provider-side higher timeframes -> derive them from our own M1
    if len(close) >= 5:
        for tf, tf_dir in _derive_mtf_from_m1(open_, high, low, close, vol).items():
            mtf_trends.setdefault(tf, tf_dir)
    spread_pct = float(market_data.get("spread_pct") or 0.0)
    if spread_pct > 0:
        notes.append("spread %.3f%%" % spread_pct)

    # ---- order blocks / supply-demand zones -----------------------------------
    ob_enabled = True
    try:
        import config as _cfg
        ob_enabled = getattr(_cfg, "ORDER_BLOCKS_ENABLED", True)
    except ImportError:
        pass
    order_blocks: List[Dict] = []
    nearest_support = nearest_resistance = 0.0
    if ob_enabled:
        order_blocks = _detect_order_blocks(high, low, close, open_)
        nearest_support, nearest_resistance = _nearest_zones(order_blocks, price)

    # ---- v4.1: higher-timeframe POC (trailing 1h / 4h volume profiles) -------
    # The session POC above is computed over the whole data window; these are
    # the volume magnets of the BIGGER timeframes the user asked for: POC of
    # the trailing 60 M1 bars (H1 profile) and 240 M1 bars (H4 profile).
    # H4 needs ~4h of window (NT_WINDOW_SECONDS=28800 provides 8h).
    htf_poc: Dict[str, float] = {}
    try:
        _vpa = VolumeProfileAnalyzer()
        for _tf, _n in (("H1", 60), ("H4", 240)):
            if len(close) >= _n:
                _p, _vh, _vl = _vpa.compute_poc_and_value_area(
                    high[-_n:], low[-_n:], vol[-_n:])
                if _p > 0:
                    htf_poc[_tf] = float(_p)
    except Exception:
        htf_poc = {}

    # ---- v3 structure: Asian range + recent closes ----------------------------
    asia_range = _asian_range_state(tick_data, price, now)
    if asia_range:
        notes.append("Asia range %.1f-%.1f%s" % (
            asia_range["low"], asia_range["high"],
            " (London breakout)" if asia_range.get("breakout") else ""))

    # ---- aggregate signal ----------------------------------------------------
    sig_engine = SignalEngine()
    strength, direction, confidence, sig_notes = sig_engine.aggregate(
        price, volatility, trend, order_flow, footprint, level3, vp, macro,
        news, regime=regime, divergence=divergence, mtf_trends=mtf_trends,
        nearest_support=nearest_support, nearest_resistance=nearest_resistance,
        recent_closes=(close[-8:] if len(close) else None),
        asia_range=asia_range or None)
    notes.extend(sig_notes)

    # v4.4: expose the macro backdrop (-1..+1) to the position manager
    # (macro-aware defense). Default 0.0 = no macro read = old behavior.
    try:
        macro_bias_now = float(getattr(sig_engine, "last_macro_bias", 0.0))
    except Exception:
        macro_bias_now = 0.0

    snapshot = MarketSnapshot(
        timestamp=now, price=price, bid=bid, ask=ask, volume=volume,
        order_flow=order_flow, footprint=footprint, level3=level3,
        volatility=volatility, trend=trend, volume_profile=vp, macro=macro,
        news=news, signal_strength=strength, signal_direction=direction,
        confidence=confidence, regime=regime, divergence=divergence,
        macro_bias=macro_bias_now,
        mtf_trends=mtf_trends, spread_pct=spread_pct,
        order_blocks=order_blocks, nearest_support=nearest_support,
        nearest_resistance=nearest_resistance,
        htf_poc=htf_poc,
        symbol=symbol,
        data_symbol=str(market_data.get("data_symbol") or symbol),
        trade_symbol=str(market_data.get("trade_symbol") or ""),
        data_market=str(market_data.get("data_market") or ""),
        trade_market=str(market_data.get("trade_market") or ""),
        notes=notes,
        data_quality=dict(market_data.get("data_quality") or {}))

    logger.info("analyze_market() -> %s (strength=%s, confidence=%s, news=%s)",
                direction, strength, confidence, news_state)
    return snapshot


# ----------------------------------------------------------------------------- #
# 13. SERIALIZATION / FORMATTING HELPERS
# ----------------------------------------------------------------------------- #


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def snapshot_to_dict(snapshot: MarketSnapshot) -> Dict[str, Any]:
    return _json_safe(asdict(snapshot))


def _session_line() -> str:
    """One display line describing day / time / session (safe if unavailable)."""
    try:
        from session import session_context
        ctx = session_context()
        return (f"  Session: {ctx['day_of_week']} {ctx['time_of_day_utc']} UTC "
                f"{ctx['session']} ({ctx['session_overlap']}) "
                f"[{'OPEN' if ctx['market_open'] == 'yes' else 'CLOSED'}]")
    except Exception:
        return ""


def snapshot_to_json(snapshot: MarketSnapshot, indent: int = 2) -> str:
    return json.dumps(snapshot_to_dict(snapshot), indent=indent)


def format_snapshot(snapshot: MarketSnapshot) -> str:
    """Human-readable multi-line summary."""
    t = snapshot.trend
    v = snapshot.volatility
    vp = snapshot.volume_profile
    m = snapshot.macro
    n = snapshot.news
    of = snapshot.order_flow
    f = snapshot.footprint
    l3 = snapshot.level3
    data_market = str(snapshot.data_market or "").upper()
    data_symbol = str(snapshot.data_symbol or "").upper()
    data_label = "MT5 CFD" if data_market == "XAUUSD" or data_symbol.startswith("XAU") else "futures feed"
    quality = snapshot.data_quality or {}

    lines = [
        "=" * 72,
        f"MARKET SNAPSHOT  {snapshot.symbol}  @ {snapshot.timestamp.isoformat()}",
        "=" * 72,
        f"DATA ({data_label}): {snapshot.data_market or '?'} "
        f"({snapshot.data_symbol or '?'})   ->   "
        f"TRADE (MT5 CFD): {snapshot.trade_market or '?'} "
        f"({snapshot.trade_symbol or '?'})",
        f"Analysis price ({snapshot.data_symbol or 'data'}): {snapshot.price:,.2f}   "
        f"Bid: {snapshot.bid:,.2f}   Ask: {snapshot.ask:,.2f}   "
        f"Volume: {snapshot.volume:,.0f}   Regime: {snapshot.regime}",
        f"Data quality: L2={quality.get('level2', 'unknown')}   "
        f"trade prints={quality.get('trade_prints', 'unknown')}   "
        f"order flow={quality.get('order_flow', 'unknown')}   "
        f"L3={quality.get('level3', 'unknown')}",
        "",
        "--- TREND ---",
        f"  SMA 9/20/50      : {t.sma_9:,.2f} / {t.sma_20:,.2f} / {t.sma_50:,.2f}",
        f"  EMA 12/26        : {t.ema_12:,.2f} / {t.ema_26:,.2f}",
        f"  ADX / +DI / -DI  : {t.adx:.1f} / {t.plus_di:.1f} / {t.minus_di:.1f}",
        f"  MACD / sig / hist: {t.macd:.3f} / {t.macd_signal:.3f} / {t.macd_histogram:.3f}",
        f"  RSI              : {t.rsi:.1f}",
        f"  Direction        : {t.trend_direction}  (strength {t.trend_strength:.1f})",
        "",
        "--- VOLATILITY ---",
        f"  ATR: {v.atr:,.2f} ({v.atr_percent:.2f}%)   BB: {v.bb_lower:,.2f} / "
        f"{v.bb_middle:,.2f} / {v.bb_upper:,.2f} (width {v.bb_width:.3f})",
        "",
        "--- ORDER FLOW ---",
        f"  CVD: {of.cvd:,.0f}   Delta: {of.delta:,.0f}   Buy%: {of.buying_pressure:.1f} "
        f"  Sell%: {of.selling_pressure:.1f}",
        f"  L2 bid/ask depth : {of.level2_bid_depth:,.0f} / {of.level2_ask_depth:,.0f} "
        f"(ratio {of.bid_ask_ratio:.2f})   Large orders: {of.large_orders}",
        "",
        "--- LEVEL 2 (depth analytics) ---",
        f"  Microprice: {of.microprice:,.2f}   Mid: {of.mid_price:,.2f}   "
        f"Imbalance: {of.depth_imbalance:+.3f}",
        f"  OFI (L2): {of.ofi:+,.0f}   Slope: {of.book_slope:+.3f}   "
        f"Concentration: {of.liquidity_concentration:.2f}",
        f"  Absorption events: {of.absorption_events} (net {of.absorption_net:+d})   "
        f"Tick-rule classified: {of.tick_rule_classified}",
        "",
        "--- FOOTPRINT ---",
        f"  Dominant level: {f.dominant_level:,.2f}   Strength: {f.footprint_strength:.3f}"
        f"   Delta imb: {f.delta_imbalance:+.3f}",
        f"  Buying levels: {len(f.buying_levels)}   Selling levels: {len(f.selling_levels)}",
        "",
        "--- LEVEL 3 (order events) ---",
        f"  Imbalance: {l3.order_book_imbalance:+.3f}   Market: {l3.market_orders} "
        f"  Limit: {l3.limit_orders}",
        f"  Aggressive buys/sells: {l3.aggressive_buys}/{l3.aggressive_sells} "
        f"(flow ratio {l3.aggressive_flow_ratio:.2f})",
        f"  OFI (L3): {l3.ofi:+,.0f}   Streaks B/S: {l3.buy_streak}/{l3.sell_streak}",
        f"  Large order events: {l3.large_order_events}   Icebergs: {l3.iceberg_events}",
        "",
        "--- VOLUME PROFILE ---",
        f"  VWAP: {vp.vwap:,.2f}   POC: {vp.poc:,.2f}   VA: {vp.value_area_low:,.2f} - "
        f"{vp.value_area_high:,.2f}",
        f"  VWAP z-score: {vp.vwap_zscore:+.2f} (std {vp.vwap_std:,.2f})   "
        f"Vol RoC: {vp.volume_rate_of_change:.3f}",
        f"  OBV: {vp.on_balance_volume:,.0f}   A/D: {vp.accumulation_distribution:,.0f}",
        "",
        "--- MACRO ---",
        f"  DXY: {m.usd_index:,.2f}   10Y: {m.us_10y_yield:.3f}%   VIX: {m.vix_index:.2f}",
        f"  Corr (DXY/yield/VIX): {m.dxy_correlation:+.2f} / {m.yield_correlation:+.2f} / "
        f"{m.vix_correlation:+.2f}",
        f"  Real yields: {m.real_yields:.3f}%   Risk: {m.risk_sentiment}",
        f"  5d change: DXY {m.usd_change_5d:+.2%}   10Y {m.yield_change_5d:+.2%}   "
        f"VIX vs 20d med: {m.vix_spike:+.0%}",
        "",
        "--- NEWS & EVENTS ---",
        f"  Sentiment: {n.sentiment_score:+.2f} ({n.sentiment_label})   "
        f"Event: {n.event_type} ({n.impact_level})",
        f"  NEWS STATE: {n.news_state}   next event in {n.minutes_to_next_event:.0f} min"
        f" ({n.next_event_title or 'none'})",
        f"  Upcoming events: {len(n.upcoming_events)}   Headlines: {len(n.news_headlines)}",
        "",
        "--- SIGNAL ---",
        f"  >>> {snapshot.signal_direction}   strength={snapshot.signal_strength:.1f} "
        f"  confidence={snapshot.confidence:.1f}   divergence={snapshot.divergence:+.0f}",
        f"  MTF align: {' '.join(f'{k}:{v}' for k, v in sorted(snapshot.mtf_trends.items())) or 'n/a'}   "
        f"spread: {snapshot.spread_pct:.3f}%",
        f"  Zones: support {snapshot.nearest_support:.2f} | resistance "
        f"{snapshot.nearest_resistance:.2f}",
        _session_line(),
    ]
    if snapshot.notes:
        lines.append(f"  notes: {'; '.join(snapshot.notes)}")
    lines.append("=" * 72)
    return "\n".join(lines)


# ----------------------------------------------------------------------------- #
# 14. SELF-TEST (synthetic data)
# ----------------------------------------------------------------------------- #

def _synthetic_market_data(seed: int = 42,
                           event_minutes: Optional[float] = None
                           ) -> Dict[str, Any]:
    """Generate realistic synthetic gold data for a self-test.

    event_minutes: if set, add a high-impact event that many minutes from now
    (used to exercise the news-time state machine).
    """
    rng = np.random.default_rng(seed)
    n = 260
    base = 2000.0
    drift = 0.0003
    rets = rng.normal(drift, 0.006, n)
    close = base * np.exp(np.cumsum(rets))
    open_ = np.concatenate(([close[0]], close[:-1]))
    high = np.maximum(open_, close) * (1 + rng.uniform(0.0005, 0.004, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0.0005, 0.004, n))
    volume = rng.integers(500, 5000, n).astype(float)

    # recent uptrend bias so the demo produces a meaningful signal
    close[-30:] += np.linspace(0, 25, 30)
    high[-30:] += np.linspace(0, 25, 30)
    low[-30:] += np.linspace(0, 25, 30)

    last_price = float(close[-1])
    tick_data = []
    for _ in range(120):
        side = "BUY" if rng.random() > 0.45 else "SELL"
        tick_data.append({
            "price": round(last_price + rng.normal(0, 0.4), 2),
            "volume": round(float(rng.integers(1, 60)), 2),
            "side": side,
        })

    bid_depth = {round(last_price - i * 0.1, 2): round(float(rng.integers(10, 200)), 2)
                 for i in range(1, 8)}
    ask_depth = {round(last_price + i * 0.1, 2): round(float(rng.integers(10, 200)), 2)
                 for i in range(1, 8)}

    # successive L2 snapshots so OFI / absorption have something to work with
    book_updates = []
    for k in range(4):
        shift = rng.integers(-30, 40)
        bd = {round(last_price - i * 0.1, 2):
              round(max(0.0, float(bid_depth.get(round(last_price - i * 0.1, 2), 0)) + shift), 2)
              for i in range(1, 8)}
        ad = {round(last_price + i * 0.1, 2):
              round(max(0.0, float(ask_depth.get(round(last_price + i * 0.1, 2), 0)) - shift), 2)
              for i in range(1, 8)}
        book_updates.append({"bids": bd, "asks": ad})

    order_events = [
        {"type": "NEW", "side": "BUY", "price": round(last_price - 0.2, 2),
         "size": 5.0, "is_market_order": False},
        {"type": "FILL", "side": "BUY", "price": round(last_price, 2),
         "size": 3.0, "is_market_order": True},
        {"type": "FILL", "side": "SELL", "price": round(last_price + 0.1, 2),
         "size": 2.0, "is_market_order": True},
        {"type": "NEW", "side": "SELL", "price": round(last_price + 0.3, 2),
         "size": 8.0, "is_market_order": False},
        {"type": "FILL", "side": "BUY", "price": round(last_price - 0.1, 2),
         "size": 4.0, "is_market_order": True},
    ]
    order_book = {
        "bids": [(round(last_price - i * 0.1, 2), float(rng.integers(10, 150)))
                 for i in range(1, 6)],
        "asks": [(round(last_price + i * 0.1, 2), float(rng.integers(10, 150)))
                 for i in range(1, 6)],
    }

    # correlated macro series (inverted relationships typical of gold)
    n_m = 60
    usd_series = 104 + np.cumsum(rng.normal(0, 0.15, n_m))
    gold_tail = close[-n_m:]
    usd_series = usd_series - 0.4 * ((gold_tail - gold_tail.mean()) / gold_tail.std())
    yield_series = 4.2 + np.cumsum(rng.normal(0, 0.05, n_m))
    vix_series = 15 + np.abs(np.cumsum(rng.normal(0, 0.4, n_m)))

    news_events = []
    if event_minutes is not None:
        news_events = [{
            "title": "FOMC Interest Rate Decision", "country": "USD",
            "impact": "HIGH",
            "date": (datetime.now(timezone.utc) + timedelta(minutes=event_minutes)).isoformat(),
            "forecast": "", "previous": "",
        }]

    return {
        "symbol": "XAUUSD",
        "price": last_price,
        "bid": last_price - 0.1,
        "ask": last_price + 0.1,
        "volume": float(volume[-1]),
        "tick_data": tick_data,
        "bid_depth": bid_depth,
        "ask_depth": ask_depth,
        "book_updates": book_updates,
        "order_events": order_events,
        "order_book": order_book,
        "candles": {"open": open_, "high": high, "low": low,
                    "close": close, "volume": volume},
        "macro": {
            "usd_index": float(usd_series[-1]),
            "us_10y_yield": float(yield_series[-1]),
            "vix_index": float(vix_series[-1]),
            "inflation_expectation": 2.3,
            "gold_series": gold_tail.tolist(),
            "usd_series": usd_series.tolist(),
            "yield_series": yield_series.tolist(),
            "vix_series": vix_series.tolist(),
        },
        "news": {
            "headlines": [
                "Gold rallies to a record high as the dollar weakens",
                "Central bank signals dovish stance, supporting bullion demand",
                "Gold prices surge on strong demand and rising safe-haven buying",
                "Precious metals climb as Treasury yields fall and optimism grows",
            ],
            "events": news_events,
            "fetch_calendar": False,   # keep the self-test fully offline
        },
        # market identity (futures data side vs CFD trade side)
        "data_symbol": "GC",
        "data_market": "GC",
        "trade_symbol": "XAUUSD",
        "trade_market": "XAUUSD",
        "data_quality": {
            "level2": "synthetic",
            "level3": "synthetic",
            "trade_prints": "synthetic",
            "order_flow": "synthetic",
        },
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    print("Running STEP 2 self-test with synthetic gold data ...\n")
    data = _synthetic_market_data()
    snap = analyze_market(data)

    print(format_snapshot(snap))

    from pathlib import Path
    json_out = snapshot_to_json(snap)
    out_path = Path(__file__).resolve().parent / "data" / "market_snapshot.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json_out)
    print(f"\nFull snapshot written to: {out_path}  ({len(json_out)} bytes)")
