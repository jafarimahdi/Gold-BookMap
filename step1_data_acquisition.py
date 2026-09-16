"""
step1_data_acquisition.py
==========================
STEP 1: DATA ACQUISITION (BookMap / Databento / Rithmic / demo)

Thin wrapper over `data_providers.py`, which contains the actual vendor
adapters and the market_data schema. This module is kept for a stable import
surface: `DataAcquisition(source=...)` -> `.acquire_market_data(symbol)`.

Switch provider by setting DATA_SOURCE in .env:
    bookmapbridge - BookMap Python addon -> ticks.csv (PRIMARY, real-time Rithmic via BookMap)
    demo          - synthetic data (no credentials)
    rithmic       - direct Rithmic R|API (legacy)
    databento     - Databento MBO/MBP-10 (DATABENTO_API_KEY)
    ninjabridge   - legacy alias for bookmapbridge (backward compat)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import config
from data_providers import BaseProvider, SUPPORTED_SOURCES, get_provider

logger = logging.getLogger(__name__)


class DataAcquisition:
    """Acquires raw market data from the configured source."""

    def __init__(self, source: str = "demo"):
        if source not in SUPPORTED_SOURCES:
            raise ValueError(f"Unknown data source '{source}'. "
                             f"Choose from {SUPPORTED_SOURCES}")
        self.source = source
        self.provider: BaseProvider = get_provider(source)

    # ------------------------------------------------------------------ #
    def acquire_market_data(self, symbol: Optional[str] = None,
                            **kwargs) -> Dict[str, Any]:
        """Return the raw market_data dict for `symbol` (Step 2 input schema).

        The symbol is the DATA-side (futures feed) market. When omitted, each
        provider uses its OWN correct default (Rithmic -> DATA_SYMBOL/GC,
        Databento -> DATABENTO_SYMBOL/GC.n.0), so the trade symbol (XAUUSD)
        is never accidentally used as the data market.
        """
        if symbol:
            data = self.provider.acquire(symbol, **kwargs)
        else:
            data = self.provider.acquire(**kwargs)
        logger.info("STEP 1: acquired %s data for %s (%d ticks)",
                    self.source, data.get("data_symbol", symbol or "?"),
                    len(data.get("tick_data", [])))
        return data
