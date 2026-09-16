# 📋 To-Do — Tonight (Backtest Night) · 2026-09-16

Meet in the evening. Two parts: first check the patient, then run the experiment.

---

## Part 1 — Health check: how did today go? (~10 min)

**1. ✅ Confirm the GitHub push (done before tonight)**
- GitHub should now show v4.4.3 (was at v4.4 / 37bc062).

**2. Rotation check — did v4.4.3 save our data at ~17:00?**
- Open the robot's log (the console window) and search for TWO lines:
  ```
  rotated ticks.csv at 200 MB → ticks_2026....csv
  archived data/archive/ticks_2026....csv → gz
  ```
- ✅ Success = both lines exist AND `Gold-MT5\data\archive\` contains one `.gz` file.
- ❌ If missing → copy the last ~50 log lines, we'll look together. Don't worry, nothing is broken by waiting.

**3. Disk check — footprint should be bounded now**
- In Git Bash, from `Gold-MT5`:
  ```bash
  ls -lh ticks.csv
  ls -lh data/archive/
  ```
- ✅ Success = `ticks.csv` is small again (fresh file, MBs) + one archive `.gz` (~30–50 MB).

**4. News gates check — big test day (Retail Sales + FOMC!)**
- Robot should show WARNING → BLACKOUT around **14:15–15:00** (Retail Sales 14:30) and **19:45–20:50** (FOMC 20:00 + press conference 20:30) local time.
- ✅ Success = log shows the countdown + blackout + "gate HOLD" during those windows, no entries inside them.

**5. Trading review — paste for me**
- Today's trades (entry time, direction, price, exit, P/L) from log/journal.
- Any WARNING / ERROR lines.
- The last BUY/SELL scores before we meet.

**6. Housekeeping (if not done yet)**
- Delete the mess files (~550 MB): keep ONE of ticks_mess_20260916.csv / ticks_backup_20260916.csv, delete the other + old `Rhitmic\ticks.csv`.

---

## Part 2 — First backtest on real clean data (~15 min together)

**7. Run it — from `Gold-MT5` folder in Git Bash**

Today is split into two files (rotation!), so we run twice to cover the whole day:

```bash
# the morning chunk (from ~10:30 to ~17:00, the archived part)
PYTHONHASHSEED=0 python tools/backtest.py --file data/archive/ticks_20260916_*.csv.gz

# the evening part (17:00 → now, the live file)
PYTHONHASHSEED=0 python tools/backtest.py --file ticks.csv
```

- ✅ Success = report prints: cycles scanned, entries, trades, win rate, profit factor, net result.
- ⚠️ Known quirk: tiny differences between runs (live calendar fetch) are NORMAL — not a bug.

**8. Read the report together**
- I'll explain each number: win rate, profit factor, expectancy — what "good" looks like for a ≤1h scalper.
- We note: which conditions produced the winning vs losing entries.

**9. First brick for the votes/weights session** 🔒
- We don't tune anything tonight — we just note surprises (a vote that was always wrong, a pattern that repeats).
- Real tuning = after 1–2 weeks of data. This list starts that notebook.

---

## Not tonight (so we don't forget)
- [ ] Depth-ladder hardening patch — on your word.
- [ ] Votes/weights tuning session — after 1–2 weeks of archives.
- [ ] **Bookmap experiment (new, start FREE):** Step 0 — free Digital tier, watch delayed full-depth MGC heatmap, learn to read walls/absorption (~1 week, $0). Step 1 — if useful: 1 month Global + real-time Rithmic. Step 2 — if proven: build Python addon bridge (depth.csv → robot depth votes); maybe later replace NT as data source entirely. **Port guide for the future AI: `docs/BOOKMAP_PORT_GUIDE.md` (verify it + the two .cs files are on GitHub).**
- [ ] Backlog review (MTF trail room, prior-day H/L, Telegram alerts...) — later.
