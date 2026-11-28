#!/usr/bin/env python3
"""tools/factory2_exec.py - the MIRROR. Rung D3 of the ROAD TO DEMO (TODO section D).

WHAT THIS ROOM IS
-----------------
The hall judges. Room E (`tools/factory2_hands.py`) runs a PAPER book: plan -> fill ->
manage -> close. This file is the ONLY place in factory 2 that may touch a broker, and it
has NO mind of its own: it reconciles the paper book against the account and mirrors the
difference - OPEN a mirror order, MOVE a stop, CLOSE when the paper closed, FLATTEN orphans.
Every organ was copied from the old factory; the donor has the scars, we do not re-earn them:

  gate 4b, demo guard      step4_mt5_execution.py:386-398 - REAL account -> refuse.
                           OURS IS STRICTER: unknown mode also refuses (prove DEMO or die).
  bounded retry + buckets  step4:512-545, 596-630 - retcode buckets; 10004/10020/10021 get
                           a bounded retry with a FRESH tick price; PARTIAL/PLACED are
                           terminal; FATAL is never retried.
  a receipt is not proof   step4:784, 858-925 - after every fill and every close we re-read
                           `positions_get()` and only THEN believe it.

OWNERSHIP LAW: our order comment prefix (and our magic) is what makes a position ours. A
broker position without the stamp is FOREIGN SOIL - printed as ORPHAN, never touched.

STATE OF TRUTH: the broker. We keep no private ledger besides `held`; after a restart
`discover()` finds our still-open ticket AT THE BROKER, and the next `sync()` adopts or
flattens it. A position can never be lost between restarts, because we never owned it -
the broker did.

DIARY LAW: every action writes ONE line, `line_kind: "order"` - the FIFTH named class
(kindcheck NAMES + the teacher's partition accept it; a day's equation must stay TRUE with
money-lines inside it).

It never decides, never widens a stop, never sizes above config.MAX_LOT_SIZE, never trades
a symbol the plan didn't name, and unless the hall was explicitly started with
`--hands-demo` this module is not even imported. `TRADING_ENABLED`-style arming exists
NOWHERE here: the donor ships its switch ON by default (config.py:253) - ours ships OFF,
and no single env edit arms it. Only the flag + a provably-DEMO account do.
"""
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# v.2 (LAW 112): a `tools/` script gets sys.path[0]=tools/, NOT the repo root - `import config`
# died on the owner's box while every import-based test was green. Same bootstrap the sibling
# tools use (gates_replay.py:34-38), copied verbatim. A tool tested only by import has never
# run as a script.
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for _p in (str(ROOT), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

EXEC_VERSION = "factory2.exec.2026-11-28.6"
OURS_COMMENT = "F2/"          # ownership stamp - the ONLY comment we may act on
MAGIC = 20261125              # our magic number; doubles as the road-to-demo date


class ExecRefused(Exception):
    """Raised when a guard says no. The name is the finding; it must never be swallowed."""


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


class DemoMirror:
    """Mirror of Room E's paper book onto a DEMO MT5 account. All guards, no opinions."""

    # retcode buckets - the donor's numbers, verbatim (step4:512-525)
    _RC_OK = 10009            # TRADE_RETCODE_DONE
    _RC_PARTIAL = 10010       # TRADE_RETCODE_DONE_PARTIAL (money IS live)
    _RC_PLACED = 10008        # TRADE_RETCODE_PLACED
    _RC_RETRYABLE = frozenset({10004, 10020, 10021})
    _RC_FATAL = frozenset({10006, 10007, 10016, 10019, 10013, 10014, 10015, 10018})
    _SEND_BUDGET = 30        # a day's knocks, all actions (v.6; RTS 6 calls it a throttle)
    _STREAK_FREEZE = 3       # this many answers NO dictionary could name -> the hand sits down

    def __init__(self, mt5mod=None, config_mod=None, pen=None, print_fn=None):
        self._mt5 = mt5mod
        self._cfg = config_mod
        self.pen = pen
        self.say = print_fn or (lambda s: print("[factory2] exec:", s))
        self.held: Optional[int] = None      # broker ticket we manage; -1 = LOST-PENDING
        self.symbol: Optional[str] = None
        self.account_login: Optional[int] = None
        self.lot: float = 0.0
        # --- v.6 (2026-11-28, the Thanksgiving parcel): every field below exists because a REAL
        # diary line demanded it on 11-26/27 - the decimals, the empty reason=, the 1e-6 doubt.
        self.digits: Optional[int] = None            # the BANK's price ruler, read once, cached
        self._basis = 0.0                            # bank_px - eye_px at our open (two-golds delta)
        self._eye_px = 0.0
        self._pending: Optional[Dict[str, Any]] = None    # deferred proof (verify next breath)
        self._names_map: Optional[Dict[int, str]] = None  # the broker module's OWN retcode words
        self._sends, self._sends_day = 0, ""         # the knock budget, per day
        self._unk_streak = 0
        self._frozen = False
        self._kill_on = False

    # -------------------------------------------------------------- plumbing --
    def _m(self):
        if self._mt5 is None:                # lazy: the hall must run where no broker exists
            try:
                import MetaTrader5 as mt5    # noqa: WPS433 - analysis never imports this
            except ImportError as exc:       # unknown machine = a REFUSAL, never a traceback
                raise ExecRefused(f"MetaTrader5 not importable here: {exc}")
            self._mt5 = mt5
        if self._cfg is None:
            import config                     # noqa: WPS433 - house settings
            self._cfg = config
        return self._mt5

    def _note(self, rec: Dict[str, Any]) -> None:
        rec = {"line_kind": "order", "v": EXEC_VERSION, "t": _iso(time.time()), **rec}
        if self.pen is not None:              # the diary line is NEVER swallowed (D5 law, now)
            try:
                self.pen(dict(rec))
            except Exception as exc:
                self.say(f"ORDER LINE LOST: {type(exc).__name__}: {exc}")
        try:
            self.say(" ".join(f"{k}={rec[k]}" for k in
                              ("act", "bucket", "proof", "retcode", "reason") if k in rec) or "note")
        except Exception:
            pass

    def _bucket(self, retcode) -> str:         # donor's _retcode_taxonomy, same buckets
        if retcode is None:
            return "NONE"
        if retcode == self._RC_OK:
            return "OK"
        if retcode == 10016:                     # donor's named, operator-visible codes
            return "INVALID-STOP"                # (step4:520, :544-545 - NO_MONEY / STOPS)
        if retcode == 10019:
            return "NO-MONEY"
        if retcode == self._RC_PARTIAL:
            return "PARTIAL"
        if retcode == self._RC_PLACED:
            return "PLACED"
        if retcode in self._RC_RETRYABLE:
            return "RETRYABLE"
        if retcode in self._RC_FATAL:
            return "FATAL"
        try:                                        # v.6: never bake a code-table - ASK the
            nm = self._names().get(int(retcode))     # broker's own module what its codes mean
        except Exception:
            nm = None
        if nm:
            return nm
        return "UNKNOWN"

    def _px(self, want_ask: bool) -> float:
        mt5 = self._m()
        tick = getattr(mt5, "symbol_info_tick", lambda s: None)(self.symbol)
        if tick is None:
            return 0.0
        a = (getattr(tick, "ask", 0.0) or getattr(tick, "last", 0.0) or 0.0) if want_ask \
            else (getattr(tick, "bid", 0.0) or getattr(tick, "last", 0.0) or 0.0)
        return float(a or 0.0)

    # ------------------------------------------- v.6: the ruler, the mapper, the names --
    def _dg(self) -> int:
        if self.digits is None:
            si = None
            try:
                si = getattr(self._m(), "symbol_info", lambda s: None)(self.symbol)
            except Exception:
                si = None
            self.digits = int(getattr(si, "digits", 2) or 2) if si is not None else 2
        return max(0, min(8, self.digits))

    def _q(self, x: float) -> float:
        """The bank's ruler before the bank's counter: 3-decimal wishes became 61 diary lines
        and a liar's `NOT-CONFIRMED` on 2026-11-26. One helper, three places, gone."""
        try:
            return round(float(x), self._dg())
        except Exception:
            return float(x)

    def _mp(self, eye_val: float) -> float:
        """Eye currency -> bank currency -> bank ruler. Trade 2 of the 26th lost 0.76 because
        a rope tied in eye-numbers sat 0.87 from the bank's true entry. Never again."""
        return self._q(float(eye_val) + self._basis)

    def _eq(self, a: float, b: float) -> bool:
        return abs(float(a) - float(b)) <= 0.5 / (10 ** self._dg())

    def _new_trade(self) -> None:
        self._pending, self._basis, self._eye_px = None, 0.0, 0.0

    def _names(self) -> Dict[int, str]:
        if self._names_map is None:
            d: Dict[int, str] = {}
            try:
                for a in dir(self._m()):
                    if a.startswith("TRADE_RETCODE_"):
                        try:
                            d[int(getattr(self._m(), a))] = a[len("TRADE_RETCODE_"):].replace("_", "-")
                        except Exception:
                            continue
            except Exception:
                pass
            self._names_map = d
        return self._names_map

    # ------------------------------------------------------- filling + buckets --
    # v.3 (LAW 113): the donor's close request ALWAYS carries `type_filling`
    # (step4:885-896 + _select_filling_mode :1028-1037: IOC first if the symbol allows,
    # else FOK, else RETURN). v.1 never set it - a demo broker answering `UNKNOWN` is the
    # price of shipping a request the donor already knew the shape of.
    @staticmethod
    def _filling(mt5, info) -> int:
        raw = int(getattr(info, "filling_mode", 0) or 0) if info is not None else 0
        if raw & 2:                                    # SYMBOL_FILLING_IOC
            return getattr(mt5, "ORDER_FILLING_IOC", 1)
        if raw & 1:                                    # SYMBOL_FILLING_FOK
            return getattr(mt5, "ORDER_FILLING_FOK", 0)
        return getattr(mt5, "ORDER_FILLING_RETURN", 2)

    @staticmethod
    def _rc(result) -> int:
        try:
            return int(getattr(result, "retcode", -1) or -1)
        except Exception:
            return -1

    # ----------------------------------------------------------------- guards --
    def arm(self, lot: Optional[float] = None) -> Dict[str, Any]:
        """Connect and PROVE this is a demo world. Any proof missing -> ExecRefused."""
        mt5 = self._m()
        cfg = self._cfg
        ok = cfg.mt5_initialize(mt5) if hasattr(cfg, "mt5_initialize") else mt5.initialize()
        if not ok:
            raise ExecRefused(f"mt5.initialize failed: {mt5.last_error()!r}")
        acc = mt5.account_info()
        if acc is None:
            raise ExecRefused("no account_info() - cannot prove whose money this is")
        demo = getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", 0)
        contest = getattr(mt5, "ACCOUNT_TRADE_MODE_CONTEST", 1)
        real = getattr(mt5, "ACCOUNT_TRADE_MODE_REAL", 2)
        tm = getattr(acc, "trade_mode", None)
        if tm not in (demo, contest):
            if not bool(getattr(cfg, "ALLOW_LIVE_TRADING", False)):
                if tm == real:
                    raise ExecRefused(
                        "REAL (live) account detected - refusing to trade. Log the MT5 "
                        "terminal into the DEMO account. (donor gate 4b, step4:386-398)")
                raise ExecRefused(
                    f"cannot PROVE the account is DEMO (trade_mode={tm!r}) - refusing")
        sym = str(getattr(cfg, "MT5_SYMBOL", "") or getattr(cfg, "SYMBOL", "") or "XAUUSD")
        if getattr(mt5, "symbol_info", lambda s: None)(sym) is None:
            raise ExecRefused(f"symbol_info({sym}) is None - the terminal has no such symbol")
        self.symbol = sym
        self.account_login = int(getattr(acc, "login", 0) or 0)
        want = float(lot if lot is not None else getattr(cfg, "LOT_SIZE", 0.1) or 0.1)
        self.lot = max(0.01, min(want, float(getattr(cfg, "MAX_LOT_SIZE", 1.0) or 1.0)))
        info = {"v": EXEC_VERSION, "login": self.account_login, "symbol": self.symbol,
                "lot": self.lot, "trade_mode": int(tm) if tm is not None else None}
        self._note({"act": "ARM", "bucket": "OK", "proof": "DEMO-MODE", **info})
        return info

    def _live(self) -> Dict[int, Any]:
        mt5 = self._m()
        got = mt5.positions_get(symbol=self.symbol)
        if got is None:
            raise ExecRefused(f"positions_get failed: {mt5.last_error()!r}")
        return {int(p.ticket): p for p in got
                if str(getattr(p, "comment", "")).startswith(OURS_COMMENT)}

    def discover(self) -> List[int]:
        """Reconcile `held` with the broker after a start. The broker owns truth."""
        live = self._live()
        ours = sorted(live)
        if ours:
            self.held = ours[0]
            self._note({"act": "DISCOVER", "ticket": self.held, "proof": "BROKER-IS-TRUTH",
                        "reason": f"{len(ours)} of ours at broker, no local state"})
            for t in ours[1:]:                # one-position law: extras get flattened, loudly
                self._note({"act": "ORPHAN", "ticket": t, "reason": "second of ours at broker"})
        return ours

    # -------------------------------------------------------------- the send --
    def _send(self, req: Dict[str, Any]):
        """One order. v.6 wraps the donor's bounded retry in the three laws this file was
        missing, each earned by a real line in a real diary: the BANK-TICKET CHECK (the broker's
        own tick freshness - eyes and hand can watch DIFFERENT markets, see trade 2 of 11-26),
        the DAY BUDGET (a polite loop must still be a bounded one), and THE NAMED ANSWER
        (rc + the module's word + comment - `reason=` was empty seven times on the 26th)."""
        mt5, cfg = self._m(), self._cfg
        tk = getattr(mt5, "symbol_info_tick", lambda s: None)(self.symbol) if self.symbol else None
        t_age = int(getattr(tk, "time", 0) or 0) if tk is not None else 0
        if t_age > 0 and time.time() - t_age > 900.0:
            return None, "TAPE-COLD", (f"bank tick is {int(time.time() - t_age)}s old - the market "
                                       "the hand would knock on looks closed (v.6 skip)"), 0
        day = time.strftime("%Y%m%d")
        if day != self._sends_day:
            self._sends_day, self._sends = day, 0
        if self._sends >= self._SEND_BUDGET:
            return None, "BUDGET-HOLD", (f"{self._SEND_BUDGET} sends already today - the hand sits "
                                         "down until tomorrow (v.6 throttle)"), 0
        max_retry = int(getattr(cfg, "MT5_RETRY_MAX", 2) or 0)
        backoff = float(getattr(cfg, "MT5_RETRY_BACKOFF_MS", 250) or 0) / 1000.0
        dev = int(getattr(cfg, "MT5_DEVIATION", 20) or 20)
        req = dict(req)
        req["deviation"] = dev
        attempts, result, reason = 0, None, ""
        while True:
            attempts += 1
            self._sends += 1
            try:
                result = mt5.order_send(dict(req))
            except Exception as exc:            # network/IPC failure mid-send
                result, reason = None, f"order_send raised {type(exc).__name__}: {exc}"
            bucket = self._bucket(getattr(result, "retcode", None))
            if reason == "" and bucket not in ("OK", "PARTIAL", "PLACED"):
                rc = self._rc(result)
                nm = self._names().get(rc, "")
                cmt = str(getattr(result, "comment", "") or "") if result is not None else ""
                reason = " ".join(x for x in (f"rc {rc}", nm, f"'{cmt}'" if cmt else "") if x)
            if bucket == "UNKNOWN":
                self._unk_streak += 1
                if self._unk_streak >= self._STREAK_FREEZE and not self._frozen:
                    self._frozen = True
                    self._note({"act": "HAND-FROZEN", "bucket": "STREAK", "retcode": self._rc(result),
                                "reason": f"{self._STREAK_FREEZE} consecutive answers no dictionary "
                                          "could name - hands sit until the hall restarts; the judge "
                                          "keeps writing"})
            elif bucket in ("OK", "PARTIAL", "PLACED"):
                self._unk_streak = 0
            if bucket in ("OK", "PARTIAL", "PLACED", "FATAL", "UNKNOWN") or attempts > max_retry:
                return result, bucket, reason, attempts
            time.sleep(backoff)                  # retryable: re-quote and re-derive the bracket
            old = float(req.get("price") or 0.0)
            fresh = self._px(bool(req.get("type") == getattr(mt5, "ORDER_TYPE_BUY", 0)))
            if fresh > 0:
                d = fresh - old
                req["price"] = round(fresh, 3)
                for k in ("sl", "tp"):
                    if req.get(k):
                        req[k] = round(float(req[k]) + d, 3)
            req["deviation"] = dev

    def _wait_ticket(self, ticket: int, present: bool) -> bool:
        cfg = self._cfg
        t_end = time.time() + float(getattr(cfg, "MT5_CLOSE_VERIFY_SECONDS", 3.0) or 0.5)
        while True:
            try:
                live = self._live()
            except ExecRefused:
                return False
            if (int(ticket) in live) is present:
                return True
            if time.time() >= t_end:
                return False
            time.sleep(0.2)

    def _round_vol(self, v: float) -> float:
        mt5 = self._m()
        si = getattr(mt5, "symbol_info", lambda s: None)(self.symbol)
        step = float(getattr(si, "volume_step", 0.01) or 0.01) if si else 0.01
        mn = float(getattr(si, "volume_min", step) or step) if si else step
        return max(mn, round(round(max(mn, float(v)) / step) * step, 8))

    # ---------------------------------------------------------------- actions --
    def open_mirror(self, side: str, price: float, sl: float, tp: float, volume: float):
        if self.held is not None:
            self._note({"act": "OPEN", "bucket": "REFUSED",
                        "reason": "one-position law: a ticket is already held or LOST-PENDING"})
            return None
        if self._frozen:
            self._note({"act": "OPEN", "bucket": "REFUSED",
                        "reason": "hand frozen after unnamed bank answers (v.6 streak law)"})
            return None
        mt5 = self._m()
        buy = str(side).upper() == "BUY"
        t = getattr(mt5, "ORDER_TYPE_BUY", 0) if buy else getattr(mt5, "ORDER_TYPE_SELL", 1)
        px = float(price or 0.0) or self._px(buy)
        req = {"action": getattr(mt5, "TRADE_ACTION_DEAL", 1), "symbol": self.symbol,
               "volume": self._round_vol(volume or self.lot), "type": t, "price": self._q(px),
               "sl": self._mp(sl), "tp": self._mp(tp), "magic": MAGIC,
               "type_time": getattr(mt5, "ORDER_TIME_GTC", 0),
               "type_filling": self._filling(mt5, getattr(mt5, "symbol_info", lambda s: None)(self.symbol)),
               "comment": OURS_COMMENT + "live"}
        result, bucket, reason, attempts = self._send(req)
        if bucket not in ("OK", "PARTIAL", "PLACED"):
            self._note({"act": "OPEN", "bucket": bucket, "attempts": attempts,
                        "retcode": self._rc(result),
                        "reason": reason or ("order send failed at the broker"
                                             if self._rc(result) >= 0 else "no result object")})
            return None
        ticket = int(getattr(result, "order", 0) or 0)
        if ticket and self._wait_ticket(ticket, True):
            self.held = ticket
            self._new_trade()
            bp = self._live().get(ticket)
            bank_px = float(getattr(bp, "price_open", 0.0) or 0.0) if bp is not None else 0.0
            if bank_px > 0 and px > 0:
                self._basis, self._eye_px = round(bank_px - px, 6), float(px)   # the delta, MEASURED
            self._note({"act": "OPEN", "bucket": bucket, "proof": "SEEN-AT-BROKER",
                        "ticket": ticket, "side": "BUY" if buy else "SELL",
                        "px": req["price"], "sl": req["sl"], "tp": req["tp"],
                        "vol": req["volume"], "attempts": attempts,
                        **({"px_bank": self._q(bank_px), "basis": round(self._basis, self._dg() + 1)}
                           if bank_px > 0 else {})})
            return ticket
        # accepted but NOT SEEN: loud, and we do not claim what we cannot prove
        self._note({"act": "OPEN", "bucket": bucket, "proof": "NOT-SEEN", "ticket": ticket,
                    "reason": "order_send answered but positions_get has no such ticket",
                    "attempts": attempts})
        return None

    def close_mirror(self) -> bool:
        if self.held is None or self.held == -1:
            self._note({"act": "CLOSE", "bucket": "NOOP", "reason": "nothing held"})
            return False
        mt5 = self._m()
        live = self._live()
        p = live.get(self.held)
        if p is None:
            self._note({"act": "CLOSE", "bucket": "NOOP", "proof": "ALREADY-GONE",
                        "ticket": self.held})
            self.held = None
            self._new_trade()
            return True
        buy_pos = int(getattr(p, "type", 0)) == int(getattr(mt5, "ORDER_TYPE_BUY", 0))
        t = getattr(mt5, "ORDER_TYPE_SELL", 1) if buy_pos else getattr(mt5, "ORDER_TYPE_BUY", 0)
        req = {"action": getattr(mt5, "TRADE_ACTION_DEAL", 1), "symbol": self.symbol,
               "volume": float(getattr(p, "volume", self.lot)), "type": t,
               "price": round(self._px(not buy_pos), 3), "position": int(self.held),
               "magic": MAGIC, "type_time": getattr(mt5, "ORDER_TIME_GTC", 0),
               "type_filling": self._filling(mt5, getattr(mt5, "symbol_info", lambda s: None)(self.symbol)),
               "comment": OURS_COMMENT + "close"}
        result, bucket, reason, attempts = self._send(req)
        gone = self._wait_ticket(self.held, False)
        if gone:
            self._note({"act": "CLOSE", "bucket": bucket, "proof": "GONE",
                        "ticket": int(self.held), "reason": reason, "attempts": attempts})
            self.held = None
            self._new_trade()
            return True
        self._note({"act": "CLOSE", "bucket": bucket, "proof": "STILL-THERE",
                    "ticket": int(self.held), "reason": "positions_get still shows it",
                    "attempts": attempts})
        return False

    def modify_sl(self, new_sl: float) -> bool:
        if self.held is None or self.held == -1:
            return False
        if self._frozen:
            self._note({"act": "MODIFY", "bucket": "REFUSED",
                        "reason": "hand frozen after unnamed bank answers (v.6 streak law)"})
            return False
        mt5 = self._m()
        live = self._live()
        p = live.get(self.held)
        if p is None:
            self._note({"act": "MODIFY", "bucket": "NOOP", "proof": "ALREADY-GONE",
                        "ticket": self.held})
            self.held = -1
            return False
        want = self._mp(new_sl)                 # mapped to bank currency AND to its ruler (v.6)
        if self._eq(float(getattr(p, "sl", 0.0) or 0.0), want):
            return True                         # equal to the ruler = nothing to ask; the storm dies
        req = {"action": getattr(mt5, "TRADE_ACTION_SLTP", 5), "position": int(self.held),
               "symbol": self.symbol, "sl": want,
               "tp": self._q(float(getattr(p, "tp", 0.0) or 0.0))}
        result, bucket, reason, attempts = self._send(req)
        ok = False
        for _ in range(4):                      # ONE short glance now; the rest waits for the
            p2 = self._live().get(self.held)    # next breath - brokers are eventually-true, and
            if p2 is not None and self._eq(float(getattr(p2, "sl", 0.0) or 0.0), want):
                ok = True
                break
            time.sleep(0.15)                    # 1e-6-vs-rounded doubt cost 61 lines on 11-26
        if not ok and bucket in ("OK", "PARTIAL"):
            self._pending = {"ticket": int(self.held), "want": want, "at": time.time()}
        self._note({"act": "MODIFY", "bucket": bucket,
                    "proof": ("VERIFIED" if ok else
                              "PENDING-NEXT-BREATH" if self._pending else "NOT-CONFIRMED"),
                    "ticket": int(self.held), "sl": want, "retcode": self._rc(result),
                    "reason": reason, "attempts": attempts})
        return ok

    # ------------------------------------------------------------------- sync --
    def sync(self, shadow: Dict[str, Any], price: float, now_ts: Optional[float] = None) -> List[str]:
        """The ONLY entry the hall calls per cycle. Dumb on purpose: the paper book leads,
        the broker follows, divergence gets a NAME, never a guess."""
        acts: List[str] = []
        pos = shadow.get("pos") if isinstance(shadow, dict) else None
        try:                                        # v.6 THE PADDLE: a KILL file in the sandbox
            _kill = (ROOT / "data" / "gates_sandbox" / "KILL").exists()
        except OSError:
            _kill = False
        if _kill:
            if not self._kill_on:
                self._kill_on = True
                self._note({"act": "KILL", "bucket": "ON",
                            "reason": "KILL file seen - hands frozen; the judge keeps writing; "
                                      "delete data/gates_sandbox/KILL to release"})
            return ["FROZEN"]
        if self._kill_on:
            self._kill_on = False
            self._note({"act": "KILL", "bucket": "OFF", "reason": "paddle up - hands resume"})
        try:
            live = self._live()
        except ExecRefused as exc:
            self._note({"act": "SYNC", "bucket": "ERROR", "reason": str(exc)})
            return ["SYNC-ERROR"]
        if self.held not in (None, -1) and self.held not in live:
            self._note({"act": "LOST", "bucket": "GONE-AT-BROKER", "ticket": self.held,
                        "reason": "SL/TP hit or operator closed it; the paper must catch up "
                                  "before any new open (LOST-PENDING gate)"})
            self.held = -1
            self._pending, self._basis = None, 0.0      # (v.6) no trade to map while unheld
            acts.append("LOST")
        if self.held == -1:
            if not pos:
                self.held = None
                acts.append("LOST-CLEARED")
            else:
                acts.append("LOST-PENDING")
            return acts
        if pos and self.held is None:
            t = self.open_mirror(str(pos.get("side") or "BUY"), float(price or 0.0),
                                 float(pos.get("sl") or 0.0), float(pos.get("tp") or 0.0),
                                 float(pos.get("volume") or self.lot or 0.01))
            acts.append("OPENED" if t else "OPEN-FAILED")
            return acts
        if pos and self.held is not None:
            p = live.get(self.held)
            if p is not None:
                if self._pending and self._pending.get("ticket") == self.held \
                        and self._eq(float(getattr(p, "sl", 0.0) or 0.0), self._pending["want"]):
                    self._note({"act": "MODIFY", "bucket": "OK", "proof": "VERIFIED-AT-BREATH",
                                "ticket": int(self.held), "sl": self._pending["want"],
                                "reason": "the bank applied it between breaths (v.6 deferred proof)"})
                    self._pending = None
                    return acts
                tp0 = float(pos.get("tp_at_open") or pos.get("tp") or 0.0)
                if tp0 and not self._eq(float(getattr(p, "tp", 0.0) or 0.0), self._mp(tp0)):
                    self._note({"act": "WANDER", "bucket": "TP-MOVED",
                                "reason": "broker TP differs from the FROZEN tp_at_open "
                                          "(mapped to the bank's ruler, v.6) - someone touched "
                                          "our position", "ticket": self.held})
                    acts.append("WANDER")
                want_sl = float(pos.get("sl") or 0.0)
                want_q = self._mp(want_sl)
                if want_sl and not self._eq(float(getattr(p, "sl", 0.0) or 0.0), want_q):
                    if self._pending and self._pending.get("want") == want_q:
                        acts.append("MOVE-PENDING")   # awaiting proof - never knock twice (v.6)
                    else:
                        acts.append("MOVED" if self.modify_sl(want_sl) else "MOVE-UNVERIFIED")
            return acts
        if not pos and self.held is not None:
            acts.append("CLOSED" if self.close_mirror() else "CLOSE-UNVERIFIED")
            return acts
        return acts


def _selftest_pen():
    """LAW 114, made true: the REHEARSAL is paper too - its lines go to the sandbox's OWN
    selftest book, NEVER into a running hall's diary (one writer per file, one law)."""
    try:
        dd = ROOT / "data" / "gates_sandbox"
        dd.mkdir(parents=True, exist_ok=True)
        path = dd / f"gates_selftest_{time.strftime('%Y%m%d')}.jsonl"
        def _pen(rec: Dict[str, Any]) -> None:
            rec = dict(rec)
            rec.setdefault("selftest", True)
            with open(path, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        _pen.path = path                      # tests and humans may ask WHERE the proof lives
        return _pen
    except OSError:
        return None


def main(argv=None) -> int:
    """`--selftest-demo`: the WIRE test the owner can run any minute, gates never bypassed.
    Smallest possible order, then close, then PROVE emptiness at the broker."""
    import argparse
    ap = argparse.ArgumentParser(description="factory2 mirror: demo-account self test")
    ap.add_argument("--selftest-demo", action="store_true")
    ap.add_argument("--lot", type=float, default=0.01)
    a = ap.parse_args(argv)
    if not a.selftest_demo:
        print("[factory2] exec: nothing to do - pass --selftest-demo"); return 2
    import os
    if str(os.environ.get("F2_SELFTEST_DISABLE", "")).strip().lower() in ("1", "yes", "true"):
        # LAW 115: a machine WITH a broker must be able to promise its tests never trade.
        # On the owner's box this very test WAS a trade - the env is the kill-switch that
        # makes running pytest safe forever, on any machine, with the terminal open.
        print("[factory2] exec: SELFTEST DISABLED by env (tests must never trade)")
        return 8
    m = DemoMirror(pen=_selftest_pen())
    try:
        info = m.arm(lot=a.lot)
        print("[factory2] exec: ARMED demo login", info["login"], info["symbol"], info["lot"])
        m.discover()
        if m.held is not None:
            print("[factory2] exec: SELFTEST REFUSED - a position is ours at the broker; "
                  "flatten it first, the mirror will manage it, a test never joins a fight")
            return 3
        px = m._px(True)
        if px <= 0:
            print("[factory2] exec: no tick price - is the terminal online?"); return 4
        t = m.open_mirror("BUY", px, round(px - 0.5, 3), round(px + 0.5, 3), a.lot)
        if not t:
            print("[factory2] exec: OPEN failed - see the order line above"); return 5
        ok = m.close_mirror()
        print("[factory2] exec: SELFTEST:", "PASS (ticket", t, "opened AND proved gone)"
              if ok else f"OPEN WAS PROVEN, CLOSE UNVERIFIED (ticket {t}) - look at the terminal")
        return 0 if ok else 6
    except ExecRefused as exc:
        print("[factory2] exec: REFUSED -", exc)
        try:                    # even a REFUSAL writes its line (LAW 114: no claim unbacked)
            m._note({"act": "ARM", "bucket": "REFUSED", "reason": str(exc)})
        except Exception:
            pass
        return 7


if __name__ == "__main__":
    raise SystemExit(main())
