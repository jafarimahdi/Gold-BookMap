"""
Live Gold (COMEX) market data reader using Rithmic + async_rithmic — v2 (reviewed & fixed).

Fixes vs v1:
  1. GATEWAY/SYSTEM presets matched to account type. v1 used
     rituz00100.rithmic.com:443 with system "Rithmic Paper Trading" — that gateway
     only serves the "Rithmic Test" system (the 2026-09-04 run proved it: the
     gateway itself reported valid systems: ['Rithmic Test']).
     v2.1: default preset is now PAPER (rprotocol.rithmic.com:443) — the regional
     hostname tried first (de.rithmic.com) turned out to be dead in public DNS.
  2. Credentials read from environment variables (never hardcode passwords).
  3. BBO printing bug fixed (a single BBO message can carry BOTH bid and ask).
  4. L2 order book is aggregated into a local book instead of printing raw
     protobuf floods; a compact top-of-book summary prints every 5 seconds.
  5. Clear diagnostics when login fails, so you can tell a wrong-gateway error
     apart from a permission/app-authorization error.

Gateway reference (DNS-verified 2026-09-04):
    TEST  -> rituz00100.rithmic.com:443   system "Rithmic Test" (free dev system,
             NO live market data; credentials are issued by Rithmic)
    PAPER -> rprotocol.rithmic.com:443    system "Rithmic Paper Trading"
             (the production connection point; geo-distributed cluster of ~20 IPs,
             usable from anywhere including Europe)

    WARNING: the regional hostnames async_rithmic shipped in 2024 (de.rithmic.com,
    jp.rithmic.com, sg.rithmic.com, hk., au., br., in., kr., za., ie., colo75.)
    NO LONGER RESOLVE in public DNS — they were decommissioned. Do not use them.
    If you need a dedicated regional endpoint, ask Rithmic (rapi@rithmic.com).

ACCESS NOTES — read before debugging the code:
  * You must have signed the digital agreements once (a normal R|Trader Pro /
    NinjaTrader login with your demo credentials does this).
  * Rithmic requires custom apps to pass "conformance" before connecting to the
    Paper Trading system. Until your app_name is authorized, a correct URL will
    still likely produce:  RithmicErrorResponse ... rpCode ['13', 'permission denied']
    That is an account/permission issue, NOT a code bug — contact rapi@rithmic.com.
  * Free 14-day trial accounts may not include API access at all (reported by
    another user with the identical setup, Aug 2025).
  * Rithmic maintenance: daily 17:20-17:45 ET (23:20-23:45 Budapest). CME Globex
    gold halt: 17:00-18:00 ET (23:00-00:00 Budapest) — no ticks during the halt.

Setup:
    pip install async_rithmic          # needs Python 3.10+

    # Git Bash (MINGW64 — your shell):
    export RITHMIC_USER="your_email@example.com"
    export RITHMIC_PASSWORD="your_password"
    export RITHMIC_PRESET="PAPER"             # or TEST
    python gold_depth_reader_v2.py

    # Windows cmd:
    set RITHMIC_USER=your_email@example.com
    set RITHMIC_PASSWORD=your_password
    set RITHMIC_PRESET=PAPER
    python gold_depth_reader_v2.py
"""

import asyncio
import os
from datetime import datetime

from async_rithmic import (
    RithmicClient,
    DataType,
    LastTradePresenceBits,
    BestBidOfferPresenceBits,
    SysInfraType,
)

# ── Connection presets ───────────────────────────────────────────────────────
GATEWAYS = {
    # preset          (system_name,                gateway url)
    "TEST":          ("Rithmic Test",          "rituz00100.rithmic.com:443"),
    "PAPER":         ("Rithmic Paper Trading", "rprotocol.rithmic.com:443"),
    "PAPER_CHICAGO": ("Rithmic Paper Trading", "rprotocol.rithmic.com:443"),  # alias
}
# NOTE (2026-09-04): regional hostnames from the library's old 2024 list
# (de/jp/sg/hk/au/br/in/kr/za/ie/colo75 .rithmic.com) no longer resolve in public
# DNS — verified dead. rprotocol.rithmic.com is the endpoint to use from any region.

PRESET = os.environ.get("RITHMIC_PRESET", "PAPER").upper()
if PRESET not in GATEWAYS:
    raise SystemExit(f"Unknown RITHMIC_PRESET '{PRESET}'. Choose one of: {', '.join(GATEWAYS)}")
SYSTEM_NAME, GATEWAY_URL = GATEWAYS[PRESET]

USERNAME = os.environ.get("RITHMIC_USER")                # demo login = signup email (case-sensitive)
PASSWORD = os.environ.get("RITHMIC_PASSWORD")
APP_NAME = os.environ.get("RITHMIC_APP_NAME", "gold_depth_reader")
# NOTE: after passing Rithmic's conformance they usually issue a 4-char prefix,
# e.g. "abcd:gold_depth_reader" — set RITHMIC_APP_NAME to exactly what they send.

GOLD_ROOT = os.environ.get("RITHMIC_SYMBOL", "GC")       # "MGC" = Micro Gold
EXCHANGE = "COMEX"

# OrderBook.UpdateType values (from order_book.proto)
CLEAR_ORDER_BOOK, NO_BOOK, SNAPSHOT_IMAGE, BEGIN, MIDDLE, END, SOLO = 1, 2, 3, 4, 5, 6, 7


def warn_if_maintenance() -> None:
    """Best-effort warning if we're inside a Rithmic maintenance / Globex halt window."""
    try:
        from zoneinfo import ZoneInfo  # Windows may need: pip install tzdata
        now = datetime.now(ZoneInfo("America/New_York"))
        t = now.hour * 60 + now.minute
        if now.weekday() < 5:  # Mon-Fri
            if 17 * 60 + 20 <= t <= 17 * 60 + 45:
                print("WARNING: Rithmic daily maintenance (17:20-17:45 ET) — logins/data may fail right now.")
            elif 17 * 60 <= t <= 18 * 60:
                print("NOTE: CME Globex halt (17:00-18:00 ET) — no gold ticks until 18:00 ET.")
        else:
            print("NOTE: weekend — Rithmic/CME maintenance may be in effect until ~12:00 ET Sunday.")
    except Exception:
        pass  # timezone info unavailable — skip the check


# ── Level 1: last trade + best bid/offer ─────────────────────────────────────
async def on_tick(data: dict):
    if data["data_type"] == DataType.LAST_TRADE:
        if data["presence_bits"] & LastTradePresenceBits.LAST_TRADE:
            print(f"[TRADE] {data['symbol']} px={data.get('trade_price')} "
                  f"qty={data.get('trade_size')} @ {data['datetime']}")
    elif data["data_type"] == DataType.BBO:
        bits = data["presence_bits"]
        # One BBO message can carry both sides — check each independently.
        if bits & BestBidOfferPresenceBits.BID:
            print(f"[BID  ] {data['symbol']} {data.get('bid_price')} x {data.get('bid_size')}")
        if bits & BestBidOfferPresenceBits.ASK:
            print(f"[ASK  ] {data['symbol']} {data.get('ask_price')} x {data.get('ask_size')}")


# ── Level 2: aggregated order book ───────────────────────────────────────────
class Book:
    """Maintains a local price->size book from OrderBook protobuf updates."""

    def __init__(self):
        self.bids = {}   # price -> size
        self.asks = {}
        self.updates_seen = 0

    def apply(self, msg) -> None:
        self.updates_seen += 1
        ut = int(msg.update_type)

        if ut == CLEAR_ORDER_BOOK:
            self.bids.clear()
            self.asks.clear()
            return
        if ut == NO_BOOK:
            print("[BOOK] NO_BOOK — symbol has no L2 data, or symbol is invalid.")
            return
        # BEGIN / MIDDLE / END / SOLO / SNAPSHOT_IMAGE all carry levels to apply.
        # Size 0 at a price means the level is gone.
        for price, size in zip(msg.bid_price, msg.bid_size):
            if size:
                self.bids[price] = size
            else:
                self.bids.pop(price, None)
        for price, size in zip(msg.ask_price, msg.ask_size):
            if size:
                self.asks[price] = size
            else:
                self.asks.pop(price, None)

    def top(self, n=5):
        bids = sorted(self.bids.items(), key=lambda kv: -kv[0])[:n]
        asks = sorted(self.asks.items(), key=lambda kv: kv[0])[:n]
        return bids, asks


async def book_reporter(book: Book, interval: float = 5.0):
    """Print a compact top-of-book summary instead of every raw message."""
    while True:
        await asyncio.sleep(interval)
        bids, asks = book.top()
        if not bids and not asks:
            continue
        bid_str = "  ".join(f"{p:,.2f}x{s}" for p, s in bids)
        ask_str = "  ".join(f"{p:,.2f}x{s}" for p, s in asks)
        print(f"[BOOK] bids {bid_str}  |  asks {ask_str}  ({book.updates_seen} updates)")


# ── Main ─────────────────────────────────────────────────────────────────────
async def main():
    if not USERNAME or not PASSWORD:
        raise SystemExit(
            "Set RITHMIC_USER and RITHMIC_PASSWORD environment variables first "
            "(see the docstring at the top of this file)."
        )

    warn_if_maintenance()
    print("Connecting to Rithmic...")
    print(f"  preset      : {PRESET}")
    print(f"  system_name : {SYSTEM_NAME}")
    print(f"  gateway     : {GATEWAY_URL}")
    print(f"  app_name    : {APP_NAME}")

    client = RithmicClient(
        user=USERNAME,
        password=PASSWORD,
        system_name=SYSTEM_NAME,
        app_name=APP_NAME,
        app_version="1.0",
        url=GATEWAY_URL,
    )

    try:
        # Only the ticker plant is needed to read data (L1 + L2 + depth all
        # flow through it).
        await client.connect(plants=[SysInfraType.TICKER_PLANT])
    except Exception as e:
        print(f"\nConnection failed: {e}\n")
        msg = str(e).lower()
        if "system_name" in msg or "system name" in msg:
            print("DIAGNOSIS: the gateway does not serve that system_name.")
            print("  -> Match the preset to your ACCOUNT type (a 'Rithmic Test' gateway")
            print("     will never accept 'Rithmic Paper Trading' and vice versa).")
        elif "permission denied" in msg or ("heartbeat_interval" in msg and "nonetype" in msg):
            # NOTE: when Rithmic denies the login (rpCode 13), the library logs the
            # real error ("rpCode: ['13', 'permission denied']") but the exception
            # that propagates can be a confusing
            # AttributeError: 'NoneType' object has no attribute 'heartbeat_interval'.
            print("DIAGNOSIS: Rithmic DENIED the login (see 'permission denied' / rpCode 13")
            print("  in the log lines above). You reached the RIGHT system — this is an")
            print("  account/permission issue, not a code bug. Usual causes:")
            print("   1. Agreements not signed — log into R|Trader Pro / NinjaTrader once")
            print("      with these credentials and accept everything.")
            print("   2. app_name not authorized — Rithmic requires custom apps to pass")
            print("      'conformance' before connecting to Paper Trading.")
            print("   3. Free 14-day trial accounts may not include API access at all.")
            print("  -> Email rapi@rithmic.com: ask whether your trial allows API access,")
            print("     and how to authorize your app_name (conformance).")
        return
    print("Connected.")

    # Sanity check: confirm which exchanges your account can see.
    try:
        exchanges = await client.list_exchanges()
        print("Exchange entitlements:", exchanges)
        for ex in (exchanges if isinstance(exchanges, (list, tuple)) else [exchanges]):
            if getattr(ex, "exchange", "") == EXCHANGE:
                flag = str(getattr(ex, "entitlement_flag", ""))
                if flag not in ("1", "ENABLED"):
                    print(f"WARNING: {EXCHANGE} entitlement flag = {flag} — you may receive no data.")
    except Exception as e:
        print(f"(Could not list exchanges: {e})")

    # Resolve today's front-month Gold contract, e.g. "GCV6".
    # NOTE: front month = nearest expiry, which is NOT always the most liquid
    # contract (volume often rolls to the next quarter-month first).
    try:
        security_code = await client.get_front_month_contract(GOLD_ROOT, EXCHANGE)
    except Exception as e:
        print(f"Could not resolve front-month contract: {e}")
        print("(Common cause: run during the 17:20-17:45 ET maintenance window.)")
        await client.disconnect()
        return
    print(f"Front-month Gold contract: {security_code}")

    client.on_tick += on_tick
    book = Book()
    client.on_order_book += book.apply

    # Level 1: trades + best bid/ask
    await client.subscribe_to_market_data(
        security_code, EXCHANGE, DataType.LAST_TRADE | DataType.BBO
    )
    # Level 2: full order book depth
    await client.subscribe_to_market_data(
        security_code, EXCHANGE, DataType.ORDER_BOOK
    )

    reporter = asyncio.create_task(book_reporter(book))

    print("\nStreaming live Gold data - press Ctrl+C to stop.\n")
    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        reporter.cancel()
        try:
            await client.unsubscribe_from_market_data(
                security_code, EXCHANGE, DataType.LAST_TRADE | DataType.BBO
            )
            await client.unsubscribe_from_market_data(
                security_code, EXCHANGE, DataType.ORDER_BOOK
            )
        finally:
            await client.disconnect()
        print("Disconnected.")


# ── OPTIONAL — order-queue detail at ONE specific price ─────────────────────
# Rithmic's depth-by-order at a single price level. The price MUST be a current
# market price (e.g. the best bid you just saw in on_tick), and the subscription
# goes stale as the market moves away from that price — for anything serious,
# use the full ORDER_BOOK stream above instead.
async def watch_one_price_level(price: float):
    async def on_market_depth(response):
        print(f"[DEPTH @ {price}] {response}")

    client = RithmicClient(
        user=USERNAME, password=PASSWORD, system_name=SYSTEM_NAME,
        app_name=APP_NAME, app_version="1.0", url=GATEWAY_URL,
    )
    await client.connect(plants=[SysInfraType.TICKER_PLANT])

    security_code = await client.get_front_month_contract(GOLD_ROOT, EXCHANGE)
    client.on_market_depth += on_market_depth

    snapshot = await client.request_market_depth(security_code, EXCHANGE, price)
    print("Initial snapshot:", snapshot)

    await client.subscribe_to_market_depth(security_code, EXCHANGE, price)
    try:
        while True:
            await asyncio.sleep(1)
    finally:
        await client.unsubscribe_from_market_depth(security_code, EXCHANGE, price)
        await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
        # For the optional per-price queue view, comment the line above and
        # uncomment this (set a REAL current price first):
        # asyncio.run(watch_one_price_level(price=0.0))
    except KeyboardInterrupt:
        print("\nStopped.")
