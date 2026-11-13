#!/usr/bin/env python3
"""Tests for committee.py - the three-gate committee (STRATEGY Entry 4, Code Day 1).
Run:  python test_committee.py   (or pytest test_committee.py)   [17 tests]
Every expectation cites the Book law it freezes (HOLY_BOOK.md, STRATEGY, Entry 4)."""
from __future__ import annotations

import committee as C


def test_g3_closed_club_is_exactly_18_and_real():
    # GATE 3 JUDGE SIGNING: 18 eyes, all present in the engine's own table.
    assert len(C.G3_ALL_EYES) == 18
    import judge_panel
    names = {j for j, _ in judge_panel._JUDGE_PATTERNS}
    assert set(C.G3_ALL_EYES) <= names, set(C.G3_ALL_EYES) - names


def test_g1_never_silent():
    card = C.evaluate_mood({}, None, 1)  # every witness abstains
    assert card["card"].startswith("MOOD=") and "| LEAN=" in card["card"]
    assert card["mood"] in ("BELLY", "WALK", "SLEEP") and card["certainty"] == 0


def test_g1_flat_lake_forces_belly():
    # shadow_flat_lake guard law: ER < 0.22 -> hard BELLY, even vs TREND
    card = C.evaluate_mood({"regime": "TREND", "er": 0.15, "slope": 1}, None, 2)
    assert card["mood"] == "BELLY" and card["certainty"] > 0


def test_g1_walk_and_certainty():
    sigs = {"regime": "TREND", "slope": 1, "fuel_roc": 12.0, "fuel_dir": 1,
            "migration": 1, "open_vs_yva": "outside_up", "ib_ratio": 2.5}
    card = C.evaluate_mood(sigs, None, 3)
    assert card["mood"] == "WALK" and card["certainty"] >= 60 and card["lean"] == "UP"
    again = C.evaluate_mood(sigs, dict(card), 4)
    assert again["since"] == 1 and again["moving"] in ("FIRM", "SETTLING")


def _rungs():
    return [{"name": "wallA", "price": 4000.0, "kind": "walls", "accepted": 1,
             "anchors_near": 2},
            {"name": "fused_soft", "price": 4000.05, "kind": "vwap_bands"},
            {"name": "far", "price": 4010.0, "kind": "pdh"}]


def test_g2_standing_and_yard():
    atr = 2.0
    rr = C.build_rungs(_rungs(), atr)
    g = C.evaluate_location(4000.4, atr, rr)
    assert g["pass"] and g["rung"].name == "wallA" and g["size_hint"] == 1.0
    assert "QUALITY=strong" in g["card"]  # hard + accepted + 0 touches => strong
    w = C.evaluate_location(4004.0, atr, rr)
    assert not w["pass"] and "LOCATION=NONE" in w["card"]
    # 2*ATR cutoff: 4010 is 5 ATR away => not even a candidate
    assert not any(r.name == "far" for r in w["candidates"])


def test_g2_one_duck_one_chair():
    # 0.05*ATR = 0.1 fuse radius: wallA and the soft band rung merge, HARD names it
    rr = C.build_rungs(_rungs(), 2.0)
    assert len(rr) == 2 and rr[0].name == "wallA" and "fused[2]" in rr[0].note


def test_g2_sleep_lock_and_middle_path():
    rr = C.build_rungs([{"name": "softy", "price": 4000.0, "kind": "va_dev"}], 2.0)
    g = C.evaluate_location(4000.0, 2.0, rr, mood="SLEEP")
    assert not g["pass"] and "SLEEP-LOCK" in g["card"]
    rr2 = C.build_rungs(_rungs(), 2.0)
    g2 = C.evaluate_location(4000.0, 2.0, rr2, mood="SLEEP")
    # Q3 middle path: fortress ACCEPTED live wall => one fish, HALF size
    assert g2["pass"] and g2["size_hint"] == 0.5 and "SLEEP-EXCEPTION" in g2["card"]


def _jd(name, d, w):
    return {"judge": name, "dir": d, "weight": w, "raw": ""}


def test_g3_break_needs_toll_and_three_desks():
    rung = C.Rung(name="wallA", price=4000.0, kind="walls")
    judges = [_jd("footprint_delta", 1, 1.0), _jd("l3_net_flow", 1, 1.5),
              _jd("l3_ofi_streak", 1, 1.0), _jd("microprice", 1, 0.6),
              _jd("footprint_levels", 1, 0.4)]
    paid = C.evaluate_fight(rung, judges, {"closes_beyond": 2}, {})
    assert paid["verdict"] == "BREAK" and "TOLL=paid" in paid["card"]
    unpaid = C.evaluate_fight(rung, judges, {"closes_beyond": 1}, {})
    # the toll law: no acceptance => the strong push may NOT be called a break
    assert unpaid["verdict"] == "UNDECIDED"


def test_g3_bounce_by_absorption_and_tired():
    rung = C.Rung(name="floor", price=4000.0, kind="walls")
    judges = [_jd("footprint_delta", 1, 1.0), _jd("absorption", -1, 0.6),
              _jd("cvd_divergence", -1, 0.6)]
    v = C.evaluate_fight(rung, judges, {}, {"snapback": False})
    assert v["verdict"] == "BOUNCE"


def test_g3_snapback_stamp_is_a_first_class_confirm():
    rung = C.Rung(name="floor", price=4000.0, kind="walls")
    judges = [_jd("footprint_delta", 1, 1.0)]
    v = C.evaluate_fight(rung, judges, {}, {"snapback": True})
    assert v["verdict"] == "BOUNCE" and "SNAPBACK=1" in v["card"]


def test_g3_desk_collapse_no_sibling_double_vote():
    # five delta-family judges in a row still make ONE desk voice
    judges = [_jd("footprint_delta", 1, 1.0), _jd("l3_net_flow", 1, 1.5),
              _jd("delta_pressure", 1, 0.6), _jd("l3_large_ofi", 1, 0.6),
              _jd("cvd_momentum", 1, 0.5)]
    v = C.desk_voices(judges)
    assert v["delta"] == 1 and all(v[k] == 0 for k in
                                   ("absorb", "iceberg", "tired", "creak",
                                    "scarecrow", "tilt"))


def _good_prefire(cyc=1):
    return {"age_s": 1.0, "bar_closed": True, "sensors_ok": True, "spread_pct": 0.001,
            "news_blackout": False, "position_count": 0, "max_positions": 2,
            "daily_loss_ok": True, "kill_switch": False, "diary": lambda rec: None,
            "symbol": "XAUUSD", "cycle_id": cyc}


def test_fire_laws_stale_eyes_pocket_the_ticket():
    rung = C.Rung(name="wallA", price=4000.0, kind="walls")
    v = {"verdict": "BREAK", "card": "RUNG=wallA | L3Q=ok | VERDICT=BREAK"}
    led = C.TicketLedger()
    pre = _good_prefire()
    pre["age_s"] = 999.0
    f = C.attempt_fire(v, rung, pre, led)
    assert not f["allow"] and "FRESH-EYES" in f["fails"]


def test_fire_laws_l3q_and_idempotency():
    led = C.TicketLedger()
    rung = C.Rung(name="wallA", price=4000.0, kind="walls")
    ok = {"verdict": "BREAK", "card": "... | L3Q=ok | ..."}
    stale = {"verdict": "BREAK", "card": "... | L3Q=stale | ..."}
    assert not C.attempt_fire(stale, rung, _good_prefire(2), led)["allow"]
    f1 = C.attempt_fire(ok, rung, _good_prefire(3), led)
    assert f1["allow"] and f1["ticket"] == "XAUUSD:3:G3"
    f2 = C.attempt_fire(ok, rung, _good_prefire(3), led)   # same cycle retry
    assert not f2["allow"] and "ONE-JUMP" in f2["fails"]
    assert led.seen() == 1


def test_committee_chain_and_g2_veto():
    led = C.TicketLedger()
    cyc = {"cycle_id": 10, "symbol": "XAUUSD", "price": 4000.0, "atr": 2.0,
           "sigs": {"regime": "RANGE"},
           "levels": [{"name": "wallA", "price": 4000.0, "kind": "walls",
                       "accepted": 1, "anchors_near": 2}],
           "judges": [_jd("footprint_delta", 1, 1.0), _jd("l3_ofi_streak", 1, 1.0),
                      _jd("microprice", 1, 0.6), _jd("footprint_levels", 1, 0.4)],
           "toll": {"closes_beyond": 2}, "trap": {}, "l3q": "ok",
           "prefire": _good_prefire(10)}
    r = C.committee_evaluate(cyc, led)
    assert r["g1"].startswith("MOOD=") and "LOCATION=wallA" in r["g2"]
    assert r["final"]["allow"] and r["final"]["ticket"] == "XAUUSD:10:G3"
    r2 = C.committee_evaluate({**cyc, "price": 4006.0}, led)  # off the ladder
    assert not r2["final"]["allow"] and r2["final"]["fails"] == ["G2-VETO"]
    assert "SKIPPED" in r2["g3"]


def test_committee_fail_closed_on_garbage():
    r = C.committee_evaluate({"price": None}, C.TicketLedger())
    assert r["final"]["fails"] == ["COMMITTEE-ERROR"] and r["g1"].startswith("MOOD=")
    assert r["final"]["ticket"] is None


def test_g3_card_carries_every_book_field():
    # MANAGER CHECK-UP parity: the G3 card must print the FULL signed job card
    rung = C.Rung(name="wallA", price=4000.0, kind="walls")
    v = C.evaluate_fight(rung, [_jd("footprint_delta", 1, 1.0)],
                         {"closes_beyond": 1, "holds_minutes": 6.0}, {},
                         l3q="partial", effort=2.34)
    for k in ("RUNG=", "PUSH=", "DESKS ", "TOLL=", "EXCESS=c1/h6m", "EFFORT=2.34",
              "OI=BLIND", "SNAPBACK=", "L3Q=partial", "VERDICT="):
        assert k in v["card"], k


def test_g1_zero_er_is_data_not_abstention():
    m = C.evaluate_mood({"er": 0.0}, None, 1)   # 0.0 = a GLASS-FLAT lake, real data
    assert "er" not in m["abstained"] and m["mood"] == "BELLY"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as exc:
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
    print(f"\n{len(fns) - failed}/{len(fns)} tests passed")
    raise SystemExit(1 if failed else 0)
