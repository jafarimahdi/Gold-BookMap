"""Offline proof that all four teams work. No market, no broker, no orders."""
import types, signal_team as S, power_team as P, shooting_team as SH, escort_team as E

cfg = types.SimpleNamespace(L3_WHALE_THRESHOLD=10.0, ICEBERG_MIN_REFILLS=3.0,
      PM_TP_BUFFER_ATR=0.15, SHOOT_MIN_RR=1.2, SHOOT_MIN_CONF=0.30,
      NEWS_HIGH_WINDOW_MINUTES=120.0, QUEUE_POS_THRESHOLD=0.7)
price = 4414.10
book = [(4417,8),(4415,12),(4413,8),(4412.5,16),(4410,11),(4408,10),(4404,5),(4400,23)]
l3 = types.SimpleNamespace(
    order_book={"bids":[(p,float(s)) for p,s in book if p<price],
                "asks":[(p,float(s)) for p,s in book if p>price]},
    order_events=[], iceberg_levels={}, iceberg_meta={}, spoof_levels={},
    large_order_events=3, order_book_imbalance=-0.2, ofi=-300,
    aggressive_buy_volume=300.0, aggressive_sell_volume=800.0)
of = types.SimpleNamespace(delta=-250, cvd=-250, absorption_net=0,
                           microprice=4414.05, mid_price=4414.10)
fp = types.SimpleNamespace(delta_imbalance=-0.55, buying_levels=12, selling_levels=31)
vp = types.SimpleNamespace(vwap=4420.0, value_area_high=4419.0, value_area_low=4416.0,
                           vwap_zscore=-0.9, volume_rate_of_change=1.1, poc=4419.0)
nw = types.SimpleNamespace(impact_level="LOW", minutes_to_next_event=300)
ticks = [{"price":4414,"volume":(40 if i % 50 == 0 else 1),"side":"SELL"} for i in range(600)]
cndl = [{"high":4415.0,"low":4413.5,"close":4414.0} for _ in range(20)]

print("=" * 78)
print("OFFLINE CHECK - four teams, one pretend market, nothing sent anywhere")
print("=" * 78)
ok = True
for cyc in (1, 2, 3):
    m  = S.build_signal_map(l3, price, 3.18, cfg, of, spread=0.30)
    pw = P.decide(m, of, fp, l3, vp, None, None, nw, None, price=price, divergence=0.0,
                  mtf={"M5":"DOWN","M15":"DOWN","H1":"DOWN"}, config=cfg,
                  tick_data=ticks, candles=cndl, atr=3.18)
    sh = SH.plan_shot(m, pw, price, 3.18, spread=0.30, config=cfg, book=S.get_book())
    ec = E.escort_cycle(sh, m, price, 3.18, 0.30, of, fp, l3, nw, 0.0, S.get_book(), cfg)
    if cyc == 3:
        print("\n1) SCOUT   ", S.describe(m)[:150])
        print("2) LEGS    ", P.describe(pw)[:130])
        print("3) SHOOTER ", SH.describe(sh)[:140])
        print("4) ESCORT  ", E.describe(ec)[:130])
        for nm, val in (("scout", m), ("legs", pw), ("shooter", sh), ("escort", ec)):
            if not val:
                ok = False
                print(f"   !! {nm} returned nothing")
print("\n" + "=" * 78)
print("RESULT:", "ALL FOUR TEAMS WORKING" if ok else "SOMETHING IS BROKEN")
print("=" * 78)
