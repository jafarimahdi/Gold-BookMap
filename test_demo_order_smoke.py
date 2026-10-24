"""Safety tests for the explicitly confirmed DEMO order smoke-test tool."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from tools import demo_order_smoke as smoke


class FakeMT5:
    ACCOUNT_TRADE_MODE_DEMO = 0
    ACCOUNT_TRADE_MODE_CONTEST = 1
    ACCOUNT_TRADE_MODE_REAL = 2
    SYMBOL_TRADE_MODE_DISABLED = 0
    SYMBOL_TRADE_MODE_LONGONLY = 1
    SYMBOL_TRADE_MODE_SHORTONLY = 2
    SYMBOL_TRADE_MODE_CLOSEONLY = 3
    SYMBOL_TRADE_MODE_FULL = 4
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1
    TRADE_ACTION_DEAL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_RETURN = 2
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_DONE_PARTIAL = 10010

    def __init__(self, now):
        self.now = now
        self.trade_mode = self.ACCOUNT_TRADE_MODE_DEMO
        self.positions = []
        self.orders = []
        self.send_calls = []
        self.check_calls = []
        self.initialized = False
        self.stale_seconds = 0.0
        self.profit = -2.0
        self.open_retcode = None
        self.orders_after_open = None
        self.close_retcode = None
        self.raise_on_open = False
        self.omit_protection = False
        self.fill_price = None

    def initialize(self, **kwargs):
        self.initialized = True
        return True

    def shutdown(self):
        self.initialized = False

    @staticmethod
    def last_error():
        return (0, "ok")

    def account_info(self):
        return SimpleNamespace(
            trade_mode=self.trade_mode, trade_allowed=True,
            equity=10_000.0, margin_free=10_000.0, currency="USD",
            login=12345, server="Demo-Server",
        )

    @staticmethod
    def terminal_info():
        return SimpleNamespace(connected=True, trade_allowed=True,
                               tradeapi_disabled=False)

    @staticmethod
    def symbol_select(symbol, select):
        return symbol == "XAUUSD" and select is True

    @staticmethod
    def symbol_info(symbol):
        if symbol != "XAUUSD":
            return None
        return SimpleNamespace(
            trade_mode=FakeMT5.SYMBOL_TRADE_MODE_FULL,
            volume_min=0.01, volume_step=0.01, volume_max=10.0,
            digits=2, point=0.01, trade_stops_level=0,
            filling_mode=2,
        )

    def symbol_info_tick(self, symbol):
        if symbol != "XAUUSD":
            return None
        stamp = self.now - self.stale_seconds
        return SimpleNamespace(bid=2000.0, ask=2000.2,
                               time=int(stamp), time_msc=int(stamp * 1000))

    def positions_get(self, symbol=None):
        if symbol not in (None, "XAUUSD"):
            return []
        return list(self.positions)

    def orders_get(self, symbol=None):
        if symbol not in (None, "XAUUSD"):
            return []
        return list(self.orders)

    def order_calc_profit(self, order_type, symbol, volume, entry, stop):
        return self.profit

    @staticmethod
    def order_calc_margin(order_type, symbol, volume, entry):
        return 1.0

    def order_check(self, request):
        self.check_calls.append(dict(request))
        return SimpleNamespace(retcode=0, comment="ok")

    def order_send(self, request):
        self.send_calls.append(dict(request))
        if "position" not in request and self.raise_on_open:
            raise RuntimeError("simulated client exception after send attempt")
        if "position" in request:
            if self.close_retcode is not None:
                return SimpleNamespace(retcode=self.close_retcode, order=0, deal=0,
                                       comment="simulated close refusal")
            ticket = request["position"]
            self.positions = [p for p in self.positions
                              if p.ticket != ticket]
            return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE,
                                   order=202, deal=302, comment="closed")
        if self.open_retcode is not None:
            if self.orders_after_open is not None:
                self.orders = list(self.orders_after_open)
            return SimpleNamespace(retcode=self.open_retcode, order=0, deal=0,
                                   comment="simulated uncertain/refused result")
        position_type = (self.POSITION_TYPE_BUY
                         if request["type"] == self.ORDER_TYPE_BUY
                         else self.POSITION_TYPE_SELL)
        position = SimpleNamespace(
            ticket=101, symbol=request["symbol"], magic=request["magic"],
            type=position_type, volume=request["volume"],
            price_open=(request["price"] if self.fill_price is None else self.fill_price),
            sl=(0.0 if self.omit_protection else request["sl"]),
            tp=(0.0 if self.omit_protection else request["tp"]),
        )
        self.positions.append(position)
        return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE,
                               order=201, deal=301, price=request["price"],
                               comment="opened")


def _cfg(data_dir: Path):
    return SimpleNamespace(
        DATA_DIR=data_dir,
        DATA_SOURCE="bookmapbridge",
        STALE_DATA_SECONDS=300,
        RL_ENABLED=False,
        TRADING_ENABLED=True,
        EXECUTION_MODE="python",
        ALLOW_LIVE_TRADING=False,
        MT5_SYMBOL="XAUUSD",
        SYMBOL="XAUUSD",
        MAX_LOT_SIZE=1.0,
        MAX_SPREAD_PCT=0.5,
        CONTRACT_SIZE=100.0,
        MIN_LOT_SKIP=True,
        ENTRY_MAX_REAL_RISK_PCT=1.5,
        ENTRY_MIN_TP_SPREAD_MULT=3.0,
        BASIS_MAX=50.0,
        BASIS_BUFFER_MULT=1.5,
        STOP_LOSS_ATR_MULT=2.0,
        TAKE_PROFIT_ATR_MULT=3.5,
        NEWS_WIDEN_STOP_MULT=1.5,
        NEWS_REDUCE_SIZE_PCT=0.5,
        RISK_PER_TRADE_PCT=1.0,
        VOL_ADJUSTED_LOTS=True,
        VOL_LOT_RISK_PCT=1.0,
        ENTRY_MIN_RR=1.2,
        ENTRY_MIN_REWARD_ATR=1.0,
        mt5_initialize=lambda module: module.initialize(),
    )


def _snapshot(data_dir: Path, now: float, *, age: float = 0.0,
              safety_status: str = "PASS"):
    data_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromtimestamp(now - age - 0.01, timezone.utc).isoformat()
    (data_dir / "market_snapshot.json").write_text(json.dumps({
        "timestamp": stamp,
        "price": 2000.0,
        "volatility": {"atr": 1.0},
        "news": {"news_state": "QUIET"},
        "data_quality": {"quote": "valid"},
        "quality_evidence": {
            "safety_gate_status": safety_status,
            "safety_gate_reason": "",
            "quote_status": "valid",
            "data_source": "bookmapbridge",
            "level2_status": "validated",
            "l2_diagnostics": {"status": "validated", "instrument_match": True},
            "feed_age_seconds_at_decision": 0.1,
            "quote_age_seconds_at_decision": 0.1,
            "depth_age_seconds_at_decision": 0.1,
        },
    }), encoding="utf-8")


def _args(*, send=False, side="BUY", max_loss=None):
    return SimpleNamespace(send=send, side=side, max_loss=max_loss)


class DummyLock:
    def __init__(self):
        self.released = False

    def _release_lock(self):
        self.released = True


def _env(tmp_path):
    now = time.time()
    _snapshot(tmp_path, now)
    fake = FakeMT5(now)
    cfg = _cfg(tmp_path)
    return now, fake, cfg


def _runtime_ok(_cfg):
    return None


def test_preflight_only_never_sends_an_order(tmp_path):
    now, fake, cfg = _env(tmp_path)
    output = []
    code = smoke.run_smoke_test(
        _args(), mt5_module=fake, cfg=cfg, output=output.append,
        now_fn=lambda: now, runtime_check=_runtime_ok,
    )
    assert code == 0
    assert fake.send_calls == []
    assert any("no order was sent" in line for line in output)


def test_real_or_unknown_account_mode_fails_closed(tmp_path):
    now, fake, cfg = _env(tmp_path)
    fake.trade_mode = fake.ACCOUNT_TRADE_MODE_REAL
    output = []
    code = smoke.run_smoke_test(
        _args(), mt5_module=fake, cfg=cfg, output=output.append,
        now_fn=lambda: now, runtime_check=_runtime_ok,
    )
    assert code == 2
    assert fake.send_calls == []
    assert any("not positively identified as DEMO" in line for line in output)


def test_allow_live_trading_true_refuses_smoke_order(tmp_path):
    now, fake, cfg = _env(tmp_path)
    cfg.ALLOW_LIVE_TRADING = True
    output = []
    code = smoke.run_smoke_test(
        _args(send=True, max_loss=50), mt5_module=fake, cfg=cfg,
        output=output.append, now_fn=lambda: now, runtime_check=_runtime_ok,
        interactive=True, lock_acquire=DummyLock,
    )
    assert code == 2
    assert fake.send_calls == []


def test_stale_broker_quote_refuses_preflight(tmp_path):
    now, fake, cfg = _env(tmp_path)
    fake.stale_seconds = 6.0
    output = []
    code = smoke.run_smoke_test(
        _args(), mt5_module=fake, cfg=cfg, output=output.append,
        now_fn=lambda: now, runtime_check=_runtime_ok,
    )
    assert code == 2
    assert fake.send_calls == []
    assert any("broker quote is 6.00s old" in line for line in output)


def test_stale_bookmap_snapshot_refuses_preflight(tmp_path):
    now, fake, cfg = _env(tmp_path)
    _snapshot(tmp_path, now, age=smoke.MAX_SNAPSHOT_AGE_SECONDS + 1)
    output = []
    code = smoke.run_smoke_test(
        _args(), mt5_module=fake, cfg=cfg, output=output.append,
        now_fn=lambda: now, runtime_check=_runtime_ok,
    )
    assert code == 2
    assert fake.send_calls == []
    assert any("BookMap snapshot is" in line for line in output)


def test_existing_position_blocks_isolated_test(tmp_path):
    now, fake, cfg = _env(tmp_path)
    fake.positions = [SimpleNamespace(symbol="XAUUSD", ticket=9, magic=234000)]
    output = []
    code = smoke.run_smoke_test(
        _args(send=True, max_loss=50), mt5_module=fake, cfg=cfg,
        output=output.append, now_fn=lambda: now, runtime_check=_runtime_ok,
        interactive=True, lock_acquire=DummyLock,
    )
    assert code == 2
    assert fake.send_calls == []
    assert any("open XAUUSD position" in line for line in output)


def test_excess_risk_for_minimum_lot_refuses_preflight(tmp_path):
    now, fake, cfg = _env(tmp_path)
    fake.profit = -500.0
    output = []
    code = smoke.run_smoke_test(
        _args(), mt5_module=fake, cfg=cfg, output=output.append,
        now_fn=lambda: now, runtime_check=_runtime_ok,
    )
    assert code == 2
    assert fake.send_calls == []
    assert any("above the configured per-trade risk budget" in line
               for line in output)


def test_blocked_normal_pipeline_receipt_refuses_smoke_order(tmp_path):
    now, fake, cfg = _env(tmp_path)
    _snapshot(tmp_path, now, safety_status="BLOCKED")
    output = []
    code = smoke.run_smoke_test(
        _args(send=True, max_loss=50), mt5_module=fake, cfg=cfg,
        output=output.append, now_fn=lambda: now,
        runtime_check=_runtime_ok, interactive=True,
        lock_acquire=DummyLock,
    )
    assert code == 2
    assert fake.send_calls == []
    assert any("normal pipeline safety/feed gate is not PASS" in line
               for line in output)


def test_runtime_risk_gate_failure_refuses_before_mt5_initialization(tmp_path):
    now, fake, cfg = _env(tmp_path)
    output = []
    lock = DummyLock()

    def blocked(_cfg):
        raise smoke.SmokeTestError("risk halt active")

    code = smoke.run_smoke_test(
        _args(send=True, max_loss=50), mt5_module=fake, cfg=cfg,
        output=output.append, now_fn=lambda: now, runtime_check=blocked,
        interactive=True, lock_acquire=lambda: lock,
    )
    assert code == 2
    assert not fake.initialized
    assert fake.send_calls == []
    assert lock.released
    assert any("risk halt active" in line for line in output)


def test_user_loss_cap_is_enforced_before_confirmation(tmp_path):
    now, fake, cfg = _env(tmp_path)
    output = []
    code = smoke.run_smoke_test(
        _args(send=True, max_loss=0.01), mt5_module=fake, cfg=cfg,
        output=output.append, now_fn=lambda: now, runtime_check=_runtime_ok,
        interactive=True, lock_acquire=DummyLock,
    )
    assert code == 2
    assert fake.send_calls == []
    assert any("exceeds your --max-loss cap" in line for line in output)


def test_mismatched_typed_confirmation_sends_nothing(tmp_path):
    now, fake, cfg = _env(tmp_path)
    lock = DummyLock()
    output = []
    code = smoke.run_smoke_test(
        _args(send=True, max_loss=50), mt5_module=fake, cfg=cfg,
        input_fn=lambda _prompt: "yes", output=output.append,
        now_fn=lambda: now, runtime_check=_runtime_ok,
        interactive=True, lock_acquire=lambda: lock,
    )
    assert code == 1
    assert fake.send_calls == []
    assert lock.released


def test_uncertain_order_result_is_not_retried(tmp_path):
    now, fake, cfg = _env(tmp_path)
    fake.open_retcode = 10030
    lock = DummyLock()
    output = []
    challenge = "OPEN DEMO BUY XAUUSD 0.01"
    code = smoke.run_smoke_test(
        _args(send=True, max_loss=50), mt5_module=fake, cfg=cfg,
        input_fn=lambda _prompt: challenge, output=output.append,
        now_fn=lambda: now, runtime_check=_runtime_ok,
        interactive=True, lock_acquire=lambda: lock,
    )
    assert code == 3
    assert len(fake.send_calls) == 1
    assert fake.positions == []
    assert lock.released
    assert not (tmp_path / "demo_order_test_pending.json").exists()
    assert any("will not resend" in line for line in output)


def test_successful_one_shot_opens_verifies_records_and_closes(tmp_path):
    now, fake, cfg = _env(tmp_path)
    lock = DummyLock()
    output = []
    confirmations = iter(("OPEN DEMO BUY XAUUSD 0.01", ""))
    code = smoke.run_smoke_test(
        _args(send=True, max_loss=50), mt5_module=fake, cfg=cfg,
        input_fn=lambda _prompt: next(confirmations), output=output.append,
        now_fn=lambda: now, runtime_check=_runtime_ok,
        interactive=True, lock_acquire=lambda: lock,
        sleep_fn=lambda _seconds: None, monotonic_fn=lambda: now,
    )
    assert code == 0
    assert len(fake.send_calls) == 2  # one open and one explicitly requested close
    assert fake.send_calls[0]["magic"] == smoke.TEST_MAGIC
    assert fake.send_calls[0]["comment"] == smoke.TEST_COMMENT
    assert fake.send_calls[0]["sl"] < fake.send_calls[0]["price"] < fake.send_calls[0]["tp"]
    assert fake.positions == []
    assert lock.released
    result = json.loads((tmp_path / "demo_order_test_result.json").read_text())
    assert result["position_ids"] == [101]
    assert result["tests"][-1]["status"] == "CLOSED"
    assert not (tmp_path / "demo_order_test_pending.json").exists()
    assert any("SUCCESS: DEMO position is open" in line for line in output)
    assert any("CLOSED: smoke-test position" in line for line in output)


def test_manual_close_is_verified_without_a_second_close_order(tmp_path):
    now, fake, cfg = _env(tmp_path)
    lock = DummyLock()
    output = []
    confirmations = iter(("OPEN DEMO BUY XAUUSD 0.01", ""))

    def operator_input(_prompt):
        answer = next(confirmations)
        if len(fake.send_calls) == 1:
            # Simulate the operator closing the position directly in MT5.
            fake.positions = []
        return answer

    code = smoke.run_smoke_test(
        _args(send=True, max_loss=50), mt5_module=fake, cfg=cfg,
        input_fn=operator_input, output=output.append,
        now_fn=lambda: now, runtime_check=_runtime_ok,
        interactive=True, lock_acquire=lambda: lock,
    )
    assert code == 0
    assert len(fake.send_calls) == 1
    assert fake.positions == []
    assert lock.released
    result = json.loads((tmp_path / "demo_order_test_result.json").read_text())
    assert result["position_ids"] == [101]
    assert result["tests"][-1]["status"] == "CLOSED"
    assert not (tmp_path / "demo_order_test_pending.json").exists()
    assert any("CLOSED: smoke-test position" in line for line in output)


def test_close_refusal_holds_app_lock_until_manual_close_verified(tmp_path):
    now, fake, cfg = _env(tmp_path)
    fake.close_retcode = 10030
    lock = DummyLock()
    output = []
    count = {"calls": 0}
    challenge = "OPEN DEMO BUY XAUUSD 0.01"

    def operator_input(_prompt):
        count["calls"] += 1
        if count["calls"] == 1:
            return challenge
        if count["calls"] == 3:
            # The third prompt is the manual-close recovery prompt.
            fake.positions = []
        return ""

    code = smoke.run_smoke_test(
        _args(send=True, max_loss=50), mt5_module=fake, cfg=cfg,
        input_fn=operator_input, output=output.append,
        now_fn=lambda: now, runtime_check=_runtime_ok,
        interactive=True, lock_acquire=lambda: lock,
    )
    assert code == 0
    assert count["calls"] == 3
    assert len(fake.send_calls) == 2  # one open; one refused close, no retry
    assert fake.positions == []
    assert lock.released
    result = json.loads((tmp_path / "demo_order_test_result.json").read_text())
    assert result["tests"][-1]["status"] == "CLOSED"
    assert not (tmp_path / "demo_order_test_pending.json").exists()
    assert any("app lock remains held" in line for line in output)


def test_interrupted_send_marker_blocks_strategy_and_requires_recovery(tmp_path, monkeypatch):
    import main
    import config as global_config

    now, fake, cfg = _env(tmp_path)
    fake.raise_on_open = True
    lock = DummyLock()
    output = []
    challenge = "OPEN DEMO BUY XAUUSD 0.01"
    code = smoke.run_smoke_test(
        _args(send=True, max_loss=50), mt5_module=fake, cfg=cfg,
        input_fn=lambda _prompt: challenge, output=output.append,
        now_fn=lambda: now, runtime_check=_runtime_ok,
        interactive=True, lock_acquire=lambda: lock,
    )
    assert code == 2
    marker_path = tmp_path / "demo_order_test_pending.json"
    marker = json.loads(marker_path.read_text())
    assert marker["status"] == "PENDING"
    assert lock.released
    blocked_output = []
    blocked = smoke.run_smoke_test(
        _args(), mt5_module=fake, cfg=cfg, output=blocked_output.append,
        now_fn=lambda: now, runtime_check=_runtime_ok,
    )
    assert blocked == 2
    assert len(fake.send_calls) == 1
    assert any("previous DEMO test is unresolved" in line for line in blocked_output)

    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main.config, "TRADING_ENABLED", True)
    assert "unresolved" in main._demo_order_test_pending_reason()
    allowed, gate_reason = main._safety_gates(None)
    assert not allowed
    assert "unresolved" in gate_reason
    monkeypatch.setattr(global_config, "DATA_DIR", tmp_path)
    from step4_mt5_execution import MT5Executor
    result = MT5Executor().execute(SimpleNamespace(confidence=100, action="BUY"),
                                   SimpleNamespace(price=2000),
                                   execution_authorized=True)
    assert result.status == "SKIPPED"
    assert "unresolved DEMO order smoke test" in result.reason

    recovery_output = []
    recovery_lock = DummyLock()
    recovery_challenge = "RESOLVE DEMO TEST XAUUSD 12345"
    recovered = smoke.run_recovery(
        mt5_module=fake, cfg=cfg, input_fn=lambda _prompt: recovery_challenge,
        output=recovery_output.append, interactive=True,
        lock_acquire=lambda: recovery_lock,
    )
    assert recovered == 0
    assert not marker_path.exists()
    assert recovery_lock.released
    assert main._demo_order_test_pending_reason() == ""


def test_uncertain_send_with_pending_order_keeps_marker_until_resolved(tmp_path):
    now, fake, cfg = _env(tmp_path)
    fake.open_retcode = 10030
    fake.orders_after_open = [SimpleNamespace(ticket=505, symbol="XAUUSD", magic=smoke.TEST_MAGIC)]
    lock = DummyLock()
    output = []
    answers = iter(("OPEN DEMO BUY XAUUSD 0.01", ""))

    def operator_input(_prompt):
        try:
            return next(answers)
        except StopIteration:
            raise EOFError

    code = smoke.run_smoke_test(
        _args(send=True, max_loss=50), mt5_module=fake, cfg=cfg,
        input_fn=operator_input, output=output.append,
        now_fn=lambda: now, runtime_check=_runtime_ok,
        interactive=True, lock_acquire=lambda: lock,
    )

    assert code == 3
    assert len(fake.send_calls) == 1
    assert (tmp_path / "demo_order_test_pending.json").exists()
    assert fake.orders
    assert lock.released
    assert any("pending order(s)" in line for line in output)


def test_recovery_refuses_to_clear_marker_while_pending_order_exists(tmp_path):
    now, fake, cfg = _env(tmp_path)
    attempt_id = "attempt-123"
    (tmp_path / "demo_order_test_pending.json").write_text(json.dumps({
        "status": "PENDING", "attempt_id": attempt_id,
        "symbol": "XAUUSD", "magic": smoke.TEST_MAGIC,
        "account_login": 12345, "account_server": "Demo-Server",
    }))
    fake.orders = [SimpleNamespace(ticket=505, symbol="XAUUSD", magic=smoke.TEST_MAGIC)]
    lock = DummyLock()
    output = []
    calls = {"n": 0}

    def operator_input(_prompt):
        calls["n"] += 1
        if calls["n"] == 1:
            return ""
        raise EOFError

    code = smoke.run_recovery(
        mt5_module=fake, cfg=cfg, input_fn=operator_input,
        output=output.append, interactive=True,
        lock_acquire=lambda: lock,
    )

    assert code == 4
    assert (tmp_path / "demo_order_test_pending.json").exists()
    assert fake.orders
    assert lock.released
    assert any("Pending order(s) found" in line for line in output)


def test_position_without_attached_stops_is_closed_immediately(tmp_path):
    now, fake, cfg = _env(tmp_path)
    fake.omit_protection = True
    lock = DummyLock()
    output = []
    challenge = "OPEN DEMO BUY XAUUSD 0.01"
    code = smoke.run_smoke_test(
        _args(send=True, max_loss=50), mt5_module=fake, cfg=cfg,
        input_fn=lambda _prompt: challenge, output=output.append,
        now_fn=lambda: now, runtime_check=_runtime_ok,
        interactive=True, lock_acquire=lambda: lock,
    )

    assert code == 4
    assert len(fake.send_calls) == 2  # one open attempt, one immediate close
    assert fake.positions == []
    assert lock.released
    result = json.loads((tmp_path / "demo_order_test_result.json").read_text())
    assert result["tests"][-1]["status"] == "CLOSED"
    assert not (tmp_path / "demo_order_test_pending.json").exists()
    assert any("filled position stop-loss is missing or invalid" in line for line in output)
    assert any("SAFETY CLOSE COMPLETE" in line for line in output)


def test_post_fill_slippage_cannot_bypass_operator_loss_cap(tmp_path):
    now, fake, cfg = _env(tmp_path)
    fake.fill_price = 2001.8
    lock = DummyLock()
    output = []
    challenge = "OPEN DEMO BUY XAUUSD 0.01"
    code = smoke.run_smoke_test(
        _args(send=True, max_loss=3.0), mt5_module=fake, cfg=cfg,
        input_fn=lambda _prompt: challenge, output=output.append,
        now_fn=lambda: now, runtime_check=_runtime_ok,
        interactive=True, lock_acquire=lambda: lock,
    )

    assert code == 4
    assert len(fake.send_calls) == 2
    assert fake.positions == []
    assert lock.released
    assert not (tmp_path / "demo_order_test_pending.json").exists()
    assert any("post-fill estimated loss" in line and "operator cap" in line
               for line in output)
    assert any("SAFETY CLOSE COMPLETE" in line for line in output)


def test_non_bookmap_feed_is_refused_even_with_a_pass_receipt(tmp_path):
    now, fake, cfg = _env(tmp_path)
    path = tmp_path / "market_snapshot.json"
    payload = json.loads(path.read_text())
    payload["quality_evidence"]["data_source"] = "demo"
    path.write_text(json.dumps(payload), encoding="utf-8")
    cfg.DATA_SOURCE = "demo"
    output = []

    code = smoke.run_smoke_test(
        _args(), mt5_module=fake, cfg=cfg, output=output.append,
        now_fn=lambda: now, runtime_check=_runtime_ok,
    )

    assert code == 2
    assert fake.send_calls == []
    assert any("not a recognized BookMap bridge" in line for line in output)
