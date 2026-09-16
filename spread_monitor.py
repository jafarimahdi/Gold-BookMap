"""
spread_monitor.py
=================
Futures <-> CFD basis monitor.

The bot analyses CME GC/MGC futures but trades XAUUSD CFDs. Those two prices
are normally aligned (arbitrage keeps the basis small and stable). A suddenly
WIDENING basis is an early warning that one side is dislocated (illiquidity,
news, a stale feed) — exactly when you should NOT trust cross-market signals.

    monitor.update(futures_price, cfd_price)   # call whenever both are known
    monitor.is_wide()                          # True beyond SPREAD_ALERT_PCT %
    monitor.report()                           # one-line summary (logged)

Wire points: step4 (MT5 tick vs snapshot futures price) and optionally the
providers when a CFD quote is available.
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone
from typing import Optional

import config

logger = logging.getLogger(__name__)

_HISTORY = 100


class SpreadMonitor:
    """Tracks the futures-CFD basis (in % of futures price)."""

    def __init__(self, alert_pct: Optional[float] = None):
        self.alert_pct = alert_pct if alert_pct is not None \
            else config.SPREAD_ALERT_PCT
        self._basis: deque = deque(maxlen=_HISTORY)
        self._last: Optional[dict] = None

    def update(self, futures_price: float, cfd_price: float) -> Optional[float]:
        """Record one (futures, cfd) pair; return basis % or None if invalid."""
        try:
            fp, cp = float(futures_price), float(cfd_price)
        except (TypeError, ValueError):
            return None
        if fp <= 0 or cp <= 0:
            return None
        basis = (cp - fp) / fp * 100.0
        self._basis.append(basis)
        self._last = {"ts": datetime.now(timezone.utc).isoformat(),
                      "futures": fp, "cfd": cp, "basis_pct": basis}
        return basis

    def last_basis_pct(self) -> Optional[float]:
        return self._basis[-1] if self._basis else None

    def mean_basis_pct(self) -> Optional[float]:
        if not self._basis:
            return None
        return sum(self._basis) / len(self._basis)

    def is_wide(self) -> bool:
        b = self.last_basis_pct()
        return b is not None and abs(b) > self.alert_pct

    def report(self) -> str:
        if self._last is None:
            return "spread monitor: no samples yet"
        b = self._last["basis_pct"]
        flag = " WIDE" if self.is_wide() else ""
        return (f"futures={self._last['futures']:.2f} "
                f"cfd={self._last['cfd']:.2f} "
                f"basis={b:+.3f}% (mean {self.mean_basis_pct():+.3f}%){flag}")


# module-level singleton for convenience
monitor = SpreadMonitor()
