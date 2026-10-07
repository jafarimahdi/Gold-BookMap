"""Tests for the v8 upgrades: wall brackets + value gate, family gate shadow,
and the escort profit-lock ratchet (user decisions 2026-10-07)."""
import unittest
from types import SimpleNamespace

from wall_brackets import compute_wall_brackets, value_gate
from judge_families import family_gate, shadow_note

CONFIG = SimpleNamespace(ENTRY_MIN_RR=1.2, ENTRY_MIN_REWARD_ATR=1.0,
                         ESCORT_PROFIT_LOCK_ENABLE=True,
                         ESCORT_LOCK_TRIGGER_R=1.0, ESCORT_LOCK_GIVEBACK_ATR=0.5)


def snap(above=None, below=None):
    return SimpleNamespace(
        bid=4195.8, ask=4196.1,
        signal_map={"above": above or [], "below": below or []})


class WallBracketTests(unittest.TestCase):
    def test_buy_uses_walls_both_sides(self):
        s = snap(above=[{"price": 4205.0, "size": 11.0}],
                 below=[{"price": 4190.0, "size": 11.0}])
        sl, tp, notes = compute_wall_brackets("BUY", 4196.0, 2.0, s)
        self.assertAlmostEqual(tp, 4205.0 - 0.6)
        self.assertAlmostEqual(sl, 4190.0 - 0.6)
        self.assertIn("wall brackets", notes[0])

    def test_sell_mirrors(self):
        s = snap(above=[{"price": 4205.0, "size": 11.0}],
                 below=[{"price": 4190.0, "size": 11.0}])
        sl, tp, _ = compute_wall_brackets("SELL", 4196.0, 2.0, s)
        self.assertAlmostEqual(tp, 4190.0 + 0.6)
        self.assertAlmostEqual(sl, 4205.0 + 0.6)

    def test_no_wall_falls_back(self):
        sl, tp, notes = compute_wall_brackets("BUY", 4196.0, 2.0, snap())
        self.assertIsNone(sl)
        self.assertIsNone(tp)
        self.assertIn("fallback", notes[0])

    def test_small_wall_not_trusted(self):
        s = snap(above=[{"price": 4205.0, "size": 2.0}],
                 below=[{"price": 4190.0, "size": 11.0}])
        sl, tp, _ = compute_wall_brackets("BUY", 4196.0, 2.0, s)
        self.assertIsNone(tp)

    def test_no_wall_behind_uses_atr_fallback_stop(self):
        s = snap(above=[{"price": 4205.0, "size": 11.0}])
        sl, tp, notes = compute_wall_brackets("BUY", 4196.0, 2.0, s)
        self.assertAlmostEqual(sl, 4192.0)   # 2xATR behind entry
        self.assertAlmostEqual(tp, 4204.4)
        self.assertIn("2xATR", notes[0])


class ValueGateTests(unittest.TestCase):
    def test_worth_it_passes(self):
        ok, reason = value_gate("BUY", 4196.0, 4192.0, 4204.0, 2.0, CONFIG)
        self.assertTrue(ok)
        self.assertIn("R:R 2.00", reason)

    def test_reward_below_risk_refused(self):
        ok, reason = value_gate("BUY", 4196.0, 4192.0, 4198.0, 2.0, CONFIG)
        self.assertFalse(ok)
        self.assertIn("not worth the risk", reason)

    def test_tiny_reward_refused_even_with_good_rr(self):
        ok, reason = value_gate("BUY", 4196.0, 4195.5, 4196.9, 2.0, CONFIG)
        self.assertFalse(ok)   # rr 1.8 ok, but reward 0.9 < 1.0 ATR (2.0)
        self.assertIn("no value", reason)


class FamilyGateTests(unittest.TestCase):
    def _panel(self):
        return [
            {"judge": "footprint_delta", "dir": 1.0, "weight": 1.0},   # TAPE
            {"judge": "cvd_momentum", "dir": 1.0, "weight": 0.5},      # TAPE
            {"judge": "l3_net_flow", "dir": 1.0, "weight": 1.5},       # BOOK
            {"judge": "vwap_trend", "dir": 1.0, "weight": 0.6},        # MAP
            {"judge": "news_sentiment", "dir": -1.0, "weight": 1.0},   # WORLD
        ]

    def test_family_vote_beats_head_count(self):
        g = family_gate(self._panel())
        self.assertEqual(g["new"]["direction"], "BUY")
        self.assertGreater(g["new"]["confidence"], 80.0)

    def test_empty_family_is_skipped(self):
        g = family_gate([{"judge": "footprint_delta", "dir": 1.0, "weight": 1.0}])
        self.assertEqual(g["new"]["direction"], "BUY")
        self.assertEqual(g["new"]["confidence"], 100.0)
        self.assertEqual(g["speaking"], ["TAPE"])

    def test_dead_zone_family_stays_silent(self):
        panel = [{"judge": "footprint_delta", "dir": 1.0, "weight": 1.0},
                 {"judge": "cvd_momentum", "dir": -1.0, "weight": 3.0}]
        g = family_gate(panel)   # TAPE internal avg = (1-3)/4 = -0.5 -> SELL
        self.assertEqual(g["families"]["TAPE"], -1.0)

    def test_retired_and_unknown_judges_ignored(self):
        panel = [{"judge": "macro_dxy", "dir": 1.0, "weight": 1.0},
                 {"judge": "not_a_judge", "dir": 1.0, "weight": 9.0},
                 {"judge": "l3_net_flow", "dir": -1.0, "weight": 1.5}]
        g = family_gate(panel)
        self.assertEqual(g["new"]["direction"], "SELL")
        self.assertEqual(g["speaking"], ["BOOK"])

    def test_shadow_note_is_one_line(self):
        note = shadow_note(self._panel())
        self.assertIn("SHADOW GATE", note)
        self.assertIn("families", note)


class EscortProfitLockTests(unittest.TestCase):
    def _trade(self):
        from escort_team import PaperTrade
        plan = {"shot": "GO", "side": "BUY", "entry": 4196.0,
                "target": 4210.0, "stop": 4195.0, "entry_style": "AGGRESSIVE_MARKET",
                "entry_filled": True, "execution_status": "PAPER_ONLY",
                "paper_fill_model": "aggressive_quote_estimate",
                "door": {"price": 4210.0, "size": 10.0}}
        t = PaperTrade(plan, 4196.0)
        t.peak, t.trough = 4199.0, 4195.5
        return t

    def test_lock_moves_stop_to_trail_once_proven(self):
        from escort_team import _profit_lock
        t = self._trade()          # risk = 1.0; price 4198.5 -> R 2.5
        a = _profit_lock(t, 4198.5, 1.0, 0.3, CONFIG)
        self.assertIsNotNone(a)
        self.assertEqual(a["action"], "TIGHTEN")
        self.assertAlmostEqual(a["new_stop"], 4199.0 - 0.5)   # peak - giveback
        self.assertGreater(a["new_stop"], t.entry)            # closes POSITIVE

    def test_no_lock_before_trigger(self):
        from escort_team import _profit_lock
        t = self._trade()
        self.assertIsNone(_profit_lock(t, 4196.5, 1.0, 0.3, CONFIG))  # R 0.5 < 1

    def test_disabled_by_config(self):
        from escort_team import _profit_lock
        cfg = SimpleNamespace(ESCORT_PROFIT_LOCK_ENABLE=False)
        t = self._trade()
        self.assertIsNone(_profit_lock(t, 4198.5, 1.0, 0.3, cfg))

    def test_one_law_applies_it(self):
        from escort_team import _profit_lock, _apply
        t = self._trade()
        a = _profit_lock(t, 4198.5, 1.0, 0.3, CONFIG)
        self.assertTrue(_apply(t, a, 4198.5))
        self.assertGreater(t.stop, 4196.0)   # locked above entry


if __name__ == "__main__":
    unittest.main()
