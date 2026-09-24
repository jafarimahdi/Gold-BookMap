GOLD-BOOKMAP AUDIT KIT -- double-click, read the day, copy it to chat
======================================================================

DAILY RITUAL (one thing)
------------------------
  cd /a/gitHub/Gold-BookMap
  bash daily_check.sh              # audits the newest log day, opens the browser tab
  bash daily_check.sh 2026-09-23   # or any specific day
  bash daily_check.sh --open       # just re-open the dashboard, run nothing
  bash daily_check.sh --quiet      # console only, no browser
  bash daily_check.sh --check      # prove the tester itself is honest (fake day)

On Windows you can also double-click **dashboard.bat** (same thing, it opens the
tab) or **daily_check.bat** (console only, no tab).

WHAT THE BROWSER TAB SHOWS  (file: data/day_report_<date>.html)
----------------------------------------------------------------
  top bar      [Copy everything for chat]  [Download .txt]  [Print / PDF]
  banner       one verdict in words: CLEAN DAY / GOOD DAY, WITH NOTES /
               ALIVE, NOT YET MEASURABLE / STOP - the day is not gradeable
  4 gauges     tests clean % | what-if win rate | feed coverage | signals over gate
  chips        gate used, AI gate, spread cost, hours awake, biggest silence,
               cycles, errors, median cycle time
  WHY         the one honest paragraph, in plain words
  THE DAY IN NUMBERS   10 KPI tiles: prints, MBO lines, snapshots, diary, over gate,
                       orders to MT5, what-if pts, WR, profit factor, drawdown
  5 tabs       Scoreboard (15 test cards, click one = its details)
               Judges (votes, buy/sell split, accuracy bar, points after spread)
               Hours & guards (24-cell heat strip + session split + "why it said NO")
               By hand (the 7 things the machine cannot see - ticks persist)
               Full text (exactly what a .txt file holds)
  history      "How the robot was doing before": one row per audited day

Everything is one file. No internet, no CDN, no fonts, no install. Copying the
html to another PC or emailing it keeps it looking identical.

[COPY EVERYTHING FOR CHAT] copies the plain-text report of that day to your
clipboard, so you can paste it here in one go. Nothing is uploaded: the copy is
done by your own browser. Notes and the checklist are kept in localStorage on
your PC only.

OTHER FILES WRITTEN EACH RUN
----------------------------
  day_report_<date>.txt              text twin of the tab (paste-able)
  data/day_metrics_<date>.json       machine-readable (history + index read these)
  data/judge_panel_<date>.csv        per-judge votes and accuracy
  data/judge_weight_suggestions.json what each judge should weigh, if >= 20 votes
  data/history_report.html           trend table, styled, day names are links
  data/index.html                    ALL audited days as cards - double-click it
                                     any time, it runs nothing

MANUAL RUNS
-----------
  python audit_day.py --date 2026-09-23            # that day
  python audit_day.py --latest                     # newest log day
  python audit_day.py --date 2026-09-23 --gate 63  # A/B: "what if the old gate?"
  python audit_day.py --date 2026-09-23 --no-html  # text only
  python audit_day.py --open                       # reopen dashboard, no audit
  python audit_day.py --demo --keep                # fake day, keep the files to look at
  python tools/check_history.py --days 14 --html   # trend table only

STEP 0 - COPY THE FILES (they are NOT in your older downloads)
---------------------------------------------------------------
  cd ~/Downloads/Gold-BookMap-AUDIT-KIT
  cp audit_day.py dashboard.py preflight.sh judge_panel.py daily_check.sh daily_check.bat dashboard.bat OPEN_DASHBOARD_FIRST.txt /a/gitHub/Gold-BookMap/
  mkdir -p /a/gitHub/Gold-BookMap/tools
  cp tools/check_history.py tools/dashboard.py tools/selftest_audit_day.py /a/gitHub/Gold-BookMap/tools/
  ls -la /a/gitHub/Gold-BookMap/audit_day.py /a/gitHub/Gold-BookMap/dashboard.py
must say `audit_day.py 88423`, `dashboard.py 1226` (that root file is a small loader
that imports tools/dashboard.py - the real 31843-byte visual layer lives in tools/).
If you forget the root loader, audit_day.py copies tools/dashboard.py there itself,
so a half-copied kit still gives you the dashboard - but copying both is cleaner.

GIT BASH RULES
  * forward slashes: tools/check_history.py   (a bare \ eats the next letter)
  * type file names plain; never paste a [name](http://name) link as a filename
  * in .env the LAST line of a duplicated key wins - that is how config.py reads it

EXPECTED FILE SIZES (so you can see nothing is half-copied)
  audit_day.py    91999 bytes   <- this kit
  dashboard.py     1226 bytes   <- this kit (root loader; real code is tools/dashboard.py 31843)
  preflight.sh     8266 bytes   <- this kit (checks AI quota + v7.1 state)
  daily_check.sh   5927 | daily_check.bat 720 | dashboard.bat 837
  tools/check_history.py 9177 ; tools/selftest_audit_day.py 12004
root dashboard.py 1226 (loader)
  judge_panel.py  15606 bytes   <- also used by the v7.1 diary patch
  main.py         48991 bytes   = v7.1 judge-patched | 47665 = your P3 (works, less exact)
  step2_market_analysis.py 210267 = patched | ~200646 = base (do NOT overwrite your base)

WHAT THE 15 TESTS MEASURE
  1 ALIVE        was it awake 08:00-23:00, no long silences
  2 FEED         did BookMap actually deliver price (ticks.csv for that day)
  3 PIPELINE     STEP1..STEP5 + MT5 bridge all finished per cycle
  4 DATA QUALITY no zero prices, no duplicate/catch-up floods
  5 GUARDS       every "NO trade" and the excuse it wrote
  6 SIGNAL       strength/confidence spread, how many cleared 50% / AI gate 6
  7 WHAT-IF      replay of every BUY/SELL on that day's M5 candles, SL 2xATR
                 TP 3.5xATR, minus 0.50 spread; above vs below the gate
  8 TEAM         flow / whale / struct / trend accuracy
  9 JUDGE PANEL  per judge: votes, participation, BUY/SELL split, accuracy on its
                 own clock, points, agreement, hourly heat, weight suggestion
 10 DIARY COVERAGE  is the diary complete enough to trust test 9
 11 VERSION      .env really holds the P3 numbers
 12 SPEED        pipeline latency, warm-up cycle excluded
 13 MONEY        leftover open positions / half-written signals
 14 EVIDENCE     every notebook file: present, size, last write
 15 HOUR         which Budapest hour / session produced the loudest signals

[ -- ] means NO DATA. It never means "the strategy lost".
