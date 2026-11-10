"""Stage X5 -- the breaker may only use numbers the broker agrees with.

Regression origin (`2026-11-10`): position `99353331` was a `+1.84` pt winner.
`PositionManager._record_gone()` asked MT5 for that ticket with
`history_deals_get(date_from, date_to, position=...)`; MT5 silently ignores the
`position` filter when a date range is present, so the reply was *every* deal in
the 48 h window. The volume-weighted average of the two unrelated `11-09` exits
(`4123.88` and `4115.88`) is `4119.88`; `4075.39 - 4119.88 = -44.49` -> the
`-12` pt Stage-4R breaker latched and blocked all entries for 11 hours, while
the account had actually made money.

These tests encode the real numbers from that day.
"""

import datetime as dt
import json

import config
import position_manager as pm_mod
import risk_manager as rm_mod
import trade_history as th

ENTRY_PX = 4075.39
REAL_EXIT_PX = 4073.55
REAL_GAIN_PTS = 1.84
FOREIGN_EXIT_A = 4123.88   # 11-09 position 99112123
FOREIGN_EXIT_B = 4115.88   # 11-09 position 99133194
POISONED = round(ENTRY_PX - (FOREIGN_EXIT_A + FOREIGN_EXIT_B) / 2.0, 2)


class _Deal:
    def __init__(self, ticket, position_id, entry, price, profit,
                 volume=0.01, comment=""):
        self.ticket = ticket
        self.position_id = position_id
        self.entry = entry
        self.price = price
        self.profit = profit
        self.swap = 0.0
        self.commission = 0.0
        self.volume = volume
        self.comment = comment
        self.time = int(dt.datetime(2026, 11, 10, 11, 10, tzinfo=dt.timezone.utc).timestamp())


def _pm(tickets_returning):
    """A PositionManager whose MT5 double answers by position only."""
    pm = pm_mod.PositionManager.__new__(pm_mod.PositionManager)
    pm._state = {}
    pm._hist = []

    class _MT5:
        @staticmethod
        def history_deals_get(_from=None, _to=None, position=None):
            return list(tickets_returning.get(int(position or 0), []))

    pm._mt5 = _MT5()
    return pm


def _state():
    return {"side": "SELL", "entry": ENTRY_PX, "last_sl": 4073.55,
            "last_volume": 0.01, "last_gain_pts": 1.20}


def test_poisoned_number_is_exactly_the_observed_44_49():
    """The arithmetic of the incident, pinned so nobody 'rounds' it away."""
    assert POISONED == -44.49


def test_record_gone_uses_only_this_positions_deals(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path, raising=False)
    monkeypatch.setattr(config, "CONTRACT_SIZE", 100.0, raising=False)
    pm = _pm({99353331: [
        _Deal(66702307, 99353331, 0, ENTRY_PX, 0.0, comment="gold-trading-bot"),
        _Deal(66703935, 99353331, 1, REAL_EXIT_PX, 1.84, comment="[sl 4073.55]"),
    ]})
    pm._record_gone("99353331", _state())
    rec = th.closed_trades(limit=1)[0]
    assert rec["close_price"] == REAL_EXIT_PX
    assert abs(rec["gain_pts"] - REAL_GAIN_PTS) < 1e-6
    assert rec["estimated"] is False
    assert "sl" in rec["exit_reason"]


def test_foreign_deals_in_a_date_window_are_filtered_out(tmp_path, monkeypatch):
    """MT5's broken mixed call: the reply contains everybody's deals."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path, raising=False)
    monkeypatch.setattr(config, "CONTRACT_SIZE", 100.0, raising=False)

    everything = [
        _Deal(1, 99112123, 1, FOREIGN_EXIT_A, 3.88),
        _Deal(2, 99133194, 1, FOREIGN_EXIT_B, -5.41),
    ]

    class _BlindMT5:
        """Answers only the mixed/dated form -- with everyone's deals."""
        @staticmethod
        def history_deals_get(_from=None, _to=None, position=None):
            if position is None:
                return everything
            return everything + [
                _Deal(66702307, 99353331, 0, ENTRY_PX, 0.0),
                _Deal(66703935, 99353331, 1, REAL_EXIT_PX, 1.84),
            ]

    pm = pm_mod.PositionManager.__new__(pm_mod.PositionManager)
    pm._state = {}
    pm._hist = []
    pm._mt5 = _BlindMT5()
    pm._record_gone("99353331", _state())
    rec = th.closed_trades(limit=1)[0]
    assert abs(rec["gain_pts"] - REAL_GAIN_PTS) < 1e-6, "foreign deals leaked in"
    assert rec["close_price"] == REAL_EXIT_PX


def test_no_close_deal_yet_stays_an_estimate(tmp_path, monkeypatch):
    """The close deal can lag the disappearance: never invent a PnL."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path, raising=False)
    monkeypatch.setattr(config, "CONTRACT_SIZE", 100.0, raising=False)
    pm = _pm({99353331: [_Deal(66702307, 99353331, 0, ENTRY_PX, 0.0)]})
    pm._record_gone("99353331", _state())
    rec = th.closed_trades(limit=1)[0]
    assert rec["estimated"] is True          # flagged: NOT broker-verified
    assert rec["close_price"] == 0.0         # no invented exit price
    assert abs(rec["gain_pts"] - 1.20) < 1e-6  # last observed mark, not a lie
    assert rec["gain_pts"] != REAL_GAIN_PTS or rec["estimated"] is True


def test_broker_money_wins_over_price_route(tmp_path, monkeypatch):
    """Two routes: a close price that implies -44.49 vs broker money +1.84."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path, raising=False)
    monkeypatch.setattr(config, "CONTRACT_SIZE", 100.0, raising=False)
    pm = _pm({99353331: [
        _Deal(66702307, 99353331, 0, ENTRY_PX, 0.0),
        _Deal(66703935, 99353331, 1, REAL_EXIT_PX, 1.84, comment="[sl 4073.55]"),
    ]})
    bad_state = dict(_state(), entry=4119.88)  # the poisoned entry the bug implied
    pm._record_gone("99353331", bad_state)
    rec = th.closed_trades(limit=1)[0]
    assert abs(rec["gain_pts"] - REAL_GAIN_PTS) < 1e-6


def _write_memory(tmp_path, records):
    p = tmp_path / "trade_memory.json"
    p.write_text(json.dumps({"closed": records}), encoding="utf-8")
    return p


def _rec(ticket, gain, usd, ts, estimated=False, pos=None):
    return {"ticket": str(pos or ticket), "side": "SELL", "gain_pts": gain,
            "pnl_usd_est": usd, "volume": 0.01, "estimated": estimated,
            "closed_ts": ts, "exit_reason": "GONE"}


def _mgr(tmp_path):
    return rm_mod.RiskManager(state_file=tmp_path / "risk_state.json")


def test_breaker_prefers_broker_over_poisoned_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path, raising=False)
    monkeypatch.setattr(config, "CONTRACT_SIZE", 100.0, raising=False)
    now = dt.datetime.now(dt.timezone.utc)
    _write_memory(tmp_path, [_rec(99353331, POISONED, -1.61, now.timestamp())])
    mgr = _mgr(tmp_path)

    deal_in = _Deal(66702307, 99353331, 0, ENTRY_PX, 0.0)
    deal_out = _Deal(66703935, 99353331, 1, REAL_EXIT_PX, 1.84)
    mgr._query_mt5_session_deals = lambda *_a, **_k: [deal_in, deal_out]
    pts = mgr.daily_points_pnl(now)
    assert abs(pts - REAL_GAIN_PTS) < 1e-6, pts
    assert pts > -12.0, "the -12 pt breaker must not fire on a winning day"


def test_breaker_keeps_memory_when_the_broker_has_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path, raising=False)
    monkeypatch.setattr(config, "CONTRACT_SIZE", 100.0, raising=False)
    now = dt.datetime.now(dt.timezone.utc)
    _write_memory(tmp_path, [_rec(111111, -3.5, -3.5, now.timestamp())])
    mgr = _mgr(tmp_path)
    mgr._query_mt5_session_deals = lambda *_a, **_k: []
    assert abs(mgr.daily_points_pnl(now) - (-3.5)) < 1e-6


def test_breaker_counts_a_broker_only_position_once(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path, raising=False)
    monkeypatch.setattr(config, "CONTRACT_SIZE", 100.0, raising=False)
    now = dt.datetime.now(dt.timezone.utc)
    _write_memory(tmp_path, [_rec(99353331, POISONED, -1.61, now.timestamp())])
    mgr = _mgr(tmp_path)
    mgr._query_mt5_session_deals = lambda *_a, **_k: [
        _Deal(66702307, 99353331, 0, ENTRY_PX, 0.0),
        _Deal(66703935, 99353331, 1, REAL_EXIT_PX, 1.84),
        _Deal(9, 555555, 0, 4000.0, 0.0),
        _Deal(10, 555555, 1, 4002.0, 2.0),
    ]
    pts = mgr.daily_points_pnl(now)
    assert abs(pts - (REAL_GAIN_PTS + 2.0)) < 1e-6, pts


def test_memory_records_reader_maps_partial_tickets(tmp_path):
    now = dt.timezone.utc and dt.datetime.now(dt.timezone.utc)
    _write_memory(tmp_path, [
        _rec("99353331:0.01", 1.84, 1.84, now.timestamp()),
        _rec(444444, -1.0, -1.0, now.timestamp()),
    ])
    out = rm_mod._trade_memory_session_records(
        now.timestamp() - 60, now.timestamp() + 60, data_dir=tmp_path)
    ids = {r["pos_id"] for r in out}
    assert ids == {99353331, 444444}


def test_position_deals_falls_back_and_filters_by_id(tmp_path, monkeypatch):
    """Even when only the dated API works, foreign ids must not leak in."""
    class _OnlyDated:
        @staticmethod
        def history_deals_get(_from=None, _to=None, position=None):
            if position is not None:
                raise TypeError("unsupported")
            return [_Deal(1, 99112123, 1, FOREIGN_EXIT_A, 3.88),
                    _Deal(2, 99353331, 1, REAL_EXIT_PX, 1.84)]

    pm = pm_mod.PositionManager.__new__(pm_mod.PositionManager)
    pm._state = {}
    pm._hist = []
    pm._mt5 = _OnlyDated()
    deals = pm._position_deals(99353331)
    assert [d.ticket for d in deals] == [2]
