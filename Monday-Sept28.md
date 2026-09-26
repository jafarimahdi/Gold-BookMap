# Monday-Sept28 — TO-DO LIST
**Created Fri 2026-09-25 night. Everything below is PENDING until marked done.**
When you ask for "the to-do list", this is the file.

Market opens **08:00 Budapest**. First full clean day with both new teams live.

---

## A. BEFORE 08:00 — get the day recording properly

- [ ] **A1. Push Friday's work to GitHub** (nothing since 26j is committed — that is
      four teams, three new judges and nine bug fixes sitting only on your disk)
```bash
cd /a/gitHub/Gold-BookMap
git status --short
git check-ignore -v .env
git add -A
git commit -m "SIGNAL scout with memory + POWER legs; nine measured fixes"
git tag good-2026-09-25c
git tag | tail -3
git push origin main
git push origin good-2026-09-25c
```

- [ ] **A2. Start the loop before 08:00** so the whole session is one clean recording
```bash
ls -la data/bot.lock      # remove only if stale
python main.py --loop
```

- [ ] **A3. Confirm both new teams are alive** (first 3 cycles, second window)
```bash
python - <<'PY'
import json
r = json.loads([l for l in open("data/snapshots_history.jsonl",encoding="utf-8") if l.strip()][-1])
sm, pw = r.get("signal_map") or {}, r.get("power") or {}
print("scout:", "OK" if sm else "MISSING", "| tracked:", sm.get("tracked"), "| cycles:", sm.get("cycles"))
print("legs :", "OK" if pw else "MISSING", "| pick:", pw.get("pick"), "| conf:", pw.get("confidence"))
PY
```

- [ ] **A4. DO NOT change any setting during the day.** One clean day is worth more than
      three tuned ones. Write down anything you want to change and do it Monday night.

---

## B. QUESTIONS TO ANSWER (asked 2026-09-25)

- [ ] **B1. How does the scout's notebook work — is it keyed by price?**
      **ANSWERED Friday:** yes, one page per EXACT price (rounded to 2 decimals), held in a
      dictionary. Each page stores side, size now, size when first seen, times seen, age,
      times attacked, who spotted it, spoof flag, cycles missing. Life cycle:
      born → updated each minute → fades when missing → torn out after 15 missing minutes,
      beyond 4 ATR, or when flagged as a lie. Caps: 8 pages per side, 3 reported per side.
      **Two weaknesses found while answering — see C1 and C2. Those are the real work.**

---

## C. THE TWO WEAKNESSES FOUND IN THE NOTEBOOK (highest value fixes)

- [ ] **C1. A wall that moves ONE TICK gives the scout amnesia.**
      The key is the exact price. A 30-lot wall at 4325.30 that shifts to 4325.40 — same
      trader, same wall — becomes a brand-new page. `seen 12x` → `seen 1x`, `growing` → `new`,
      hold score collapses. Big players move orders constantly, so the memory may be getting
      erased continuously.
      **Fix to design:** cluster nearby prices into one door (a "zone" of ±1 tick, or ±0.1),
      and let the door keep its history while its price drifts. Careful: too wide and two
      genuinely different walls get merged.

- [ ] **C2. The notebook is only in RAM — every restart wipes it.**
      `signal_team.py` writes nothing to disk. Friday we restarted five times and the scout
      never got past 8 cycles of memory. **The whole point of the scout is persistence.**
      **Fix to design:** save the notebook to `data/signal_book.json` each cycle and reload
      on start, discarding anything older than N minutes so a stale book is never trusted.

---

- [x] **C3. DONE Fri night — the market-maker problem (owner's Bookmap screenshot).**
      The owner spotted that the levels nearest price are market-maker / broker quotes:
      always present, 7-16 lots, no profit even if touched. The real target in his
      screenshot was **4400.0 holding 23 lots, 14.1 points away, against neighbours of 4**.
      The old scout would have collected four quotes and **thrown 4400 away as too far**.
      Fixed in 26y with four rules:
      - **outlier, not big** - a door must be >= 2.5x the median of its neighbours (4400 = 5.8x)
      - **profit filter** - closer than 10x spread is never offered as a target
        (4415 was 3x spread away, 4412.5 was 5x, 4400 was 47x)
      - **wider band** - max(4 x ATR, 0.35% of price), because a pure ATR band goes blind
      - **round-number bonus** - institutions cluster on 4400.0, not on 4403.5
      Result on his screenshot: **one door, 4400.0** - exactly the one he pointed at.
      **Still to verify on Monday: does this hold on a live, fast-moving book?**

## D. MONDAY EVENING — mark the homework

- [ ] **D1. The big one: are walls really targets?**
```bash
python tools/signal_map.py --date 2026-09-28
```
      Needs 30+ observations for a verdict (Friday gave 16). Watch **Question 3** — the same
      walls traded two opposite ways, scored in ATR:
      *GO TO the wall* vs *BOUNCE off it* (what `whale_walls` votes today).
      **If "go to" wins again, `whale_walls` has been voting backwards and flipping its sign
      is worth more than any new feature.**

- [ ] **D2. Did the wall judges finally speak all day?**
```bash
python tools/signal_health.py --date 2026-09-28 --teams --quality
```
      Friday's threshold fix woke `whale_walls` (14 votes, first ever). Still silent:
      `iceberg`, `spoof_invert`, `queue_pos`, `l3_large_ofi`. Check whether a full day
      changes that. Also check `--quality`: how degraded is ATR on a real session?

- [ ] **D3. What weight did each judge really carry?**
```bash
python tools/real_roster.py --date 2026-09-28
```

- [ ] **D4. Does force predict distance yet?**
```bash
python tools/force_study.py --date 2026-09-28 --independent
```
      Friday: 44 independent 15-min windows per day. Needs ~3 days for a real answer, so
      expect "NOT ENOUGH DATA" — that is correct behaviour, not a failure.

- [ ] **D5. The one report**
```bash
bash daily_check.sh 2026-09-28
```
      Then open `data/report_2026-09-28.html` and nothing else.

- [ ] **D6. Score the POWER picks.** Not built yet: a tool that reads every `power` entry in
      the diary and asks the tape *"did it pick the door price actually touched first?"*
      Per group (PUSH / TERRAIN / WEATHER) and per judge.
      **Prediction on record: PUSH beats TERRAIN, and PUSH alone beats the blended panel.**

---

## D7. THE BENCH — ✅ ALL DONE Friday night (audit 2026-09-25)

An end-to-end audit of the four modules found **19 of 25 judges actually change an answer.
Six do not.** Four of those six look like they are simply on the wrong team.

- [x] **D7a. DONE — `poc_day`, `htf_poc`, `supply_demand` — wrong team.**
      They sit in POWER but they **name prices** (a POC is a price; a supply zone is a
      price). Naming a price is a CREATOR's job, which is the scout's work. They are
      *remembered* doors where the scout holds *live* ones.
      **Recommendation: move them into SIGNAL as historical door-makers.** This also
      directly serves the owner's rule L2 - *"what happened previously when the price was
      here before"* - which nothing currently answers.

- [x] **D7b. DONE — `queue_pos` — wrong team.** It sits in SIGNAL but its superpower is
      *"would WE get filled here"*. That is an execution question, not a map question.
      **Recommendation: move it to the SHOOTER**, as a last check before taking the shot.

- [x] **D7c. DONE — `vwap_trend` — redundant.** Recorded but never used. `vwap_bands` (the
      z-score) already measures stretch, and measures it better. Either give it a real job
      or retire it honestly.

- [x] **D7d. DONE — `macro_risk` — marginal.** Recorded but never used. Slow, 120-minute horizon,
      rarely decisive inside five minutes. It is the last survivor of the macro group.
      Keep as a record, or retire it.

- [x] **D7e. DONE — Doc vs code mismatch:** `sweep` and `absorption` are listed in the escort's
      five jobs in the documents but are **not wired into `escort_team.py`**. Either wire
      them or correct the charter.

## D8. THREE NEW JUDGES HIRED Friday night — watch them Monday

All three are live in 27f. None of them existed before Friday.

- [ ] **D8a. `road_vacuum`** (SCOUT, group "change"). Watches the corridor EMPTY.
      Everyone else hunts for walls; this is the only judge that notices walls
      **disappearing**, which is what lets price run. **Currently MEASURED ONLY — nothing
      acts on it** (Law 7). Monday: check whether an emptying road came before real moves.
      If yes, wire it into the shooter.

- [ ] **D8b. `big_prints`** (LEGS, role DIRECTION). Are the actual TRADES big, or algo
      churn? 100 lots in one print is an institution; 100 prints of 1 lot is noise —
      and every other judge sees those two as identical. **Already carries 0.10 of the
      push.** Monday: does its direction agree with what price did?

- [ ] **D8c. `stall_clock`** (LEGS, role REGIME). How long have we been coiled in one
      narrow range? **Already multiplies confidence by up to 1.25x.** Monday: were the
      coiled moments actually the better trades?

- [ ] **D8d. Build the charter-vs-code checker.** D7e happened TWICE on Friday — the
      second time while fixing the first. A document that can drift will drift. Make the
      audit a tool that can be run any time, not something done by hand when asked.

- [ ] **D8e. The full judge audit the owner asked for** — all 25 judges, one by one:
      what each uniquely sees, whether its team can use that, keep / move / retire.
      D7 did this for six of them; the other nineteen have not been examined.

## E. STILL PENDING FROM THE ROADMAP

- [ ] **E1.** `BOOKMAP_WINDOW_SECONDS=25200` and `BOOKMAP_CATCHUP_MB=24` — your call, not applied
- [ ] **E2.** Model cooldown in step3 (C5) — proposed, never answered. Friday evidence:
      two Gemini keys hit 504 and burned **33 seconds** of a 60-second cycle
- [ ] **E3.** `BUDAPEST_UTC_OFFSET=2` is a fixed number and **DST ends 25 October** — it will
      silently be wrong by one hour
- [ ] **E4.** `MAX_SPREAD_PCT=0.05` vs `V6_CFD_SPREAD_MAX=0.50` — two spread limits, 10x apart
- [ ] **E5.** `whale_balanced`, `trend_macd`, `sma20` — parsed but carry no weight
- [ ] **E6.** `TEST_SYSTEM_AUDIT` D8 "no dedup across tape sources" — the one item of 16 never shipped
- [ ] **E7.** Phase 4b: wall-aware TP/SL. Two unanswered questions: when a wall blocks the
      entry, **skip or shrink the TP?** And first version: **TP only, or TP and SL?**
- [ ] **E8.** ESCORT team — designed in full, built last, waits on Phase 4b
- [ ] **E9.** Judge-correlation matrix — largely solved by the POWER/SIGNAL split; now a
      verification tool rather than a prerequisite

---

## F. THINGS THAT ARE ALREADY TRUE (do not redo)

- Phase 0 done: `V6_4TEAMS_ENABLED=0`. Ensemble lines **608 → 1**, forced NEUTRAL **318 → 0**
- Phase 1 done: thresholds **10 / 5 / 8**. `whale_walls` spoke for the first time ever
- Phase 2 done: news window, absorption note + reset, loud tick-collapse warning
- Phase 2b done: all four diagnostics embedded in the one daily HTML
- Sell-side ratchet bug fixed — the robot can sell again after a losing day
- SIGNAL team (scout) and POWER team (legs) both live, both recording, **neither can trade**
- `RL_PAUSE` is active (4 consecutive losses, size halved). That is the safety layer, not a fault
