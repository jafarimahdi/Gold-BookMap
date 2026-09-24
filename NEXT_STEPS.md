# DO THIS NOW — yesterday in the browser, one double-click away

Today is **Fri 2026-09-24** in Budapest, so the day to grade = **2026-09-23**.
Your last preflight: `RESULT: 25 ok, 5 warnings, 0 blocking` → the day is fully gradeable.

Git Bash rules: `/` not `\`, type file names plain, and in `.env` the **last** line of a
duplicated key wins (exactly what `config.py` does).

---

**One command to prove the whole setup is alive on any morning: `bash morning_check.sh`**
(before starting) and `bash morning_check.sh --live` (15 min after starting `main.py`).
It checks installed file sizes, `.env`, compile, the Budapest session guard, whether
`ticks.csv` is growing, trade prints per hour, MT5 artefacts, and whether the robot's
own log went silent. It never writes anything.

**If you are lost: `START_HERE_NOW.txt` (inside the kit) is the same route, compressed to 7 steps,
with the exact line you should see after each one.**

## STEP 1 — re-copy the kit (the visual layer is new)

```
cd ~/Downloads
rm -rf Gold-BookMap-AUDIT-KIT && unzip -o Gold-BookMap-AUDIT-KIT.zip
cd Gold-BookMap-AUDIT-KIT
cp audit_day.py dashboard.py preflight.sh judge_panel.py daily_check.sh daily_check.bat dashboard.bat OPEN_DASHBOARD_FIRST.txt /a/gitHub/Gold-BookMap/
mkdir -p /a/gitHub/Gold-BookMap/tools
cp tools/check_history.py tools/dashboard.py tools/selftest_audit_day.py /a/gitHub/Gold-BookMap/tools/
cd /a/gitHub/Gold-BookMap
ls -la audit_day.py dashboard.py daily_check.sh
```
Sizes must be `audit_day.py    92995`, `dashboard.py 1226` (loader), `daily_check.sh   5927`;
`tools/dashboard.py` must be 31843 - that is the file that draws the dashboard.
If a size differs the copy failed — stop and send me that `ls` line.

## STEP 2 — prove the tester itself is honest (10 s, fake data, writes nothing of yours)

```
bash daily_check.sh --check
```
Must print:
```
[OK] audit_day.py compiles
[OK] visual layer loads, ... bytes of style, ... bytes of script
[demo] verdict: auditor OK - every test produced a real grade and the dashboard rendered
SELFTEST PASSED: all 15 tests produced real grades
[OK] auditor and selftest both clean - every number it prints is trustworthy
```
That is the "is everything correct before I start testing" gate: the auditor is graded on
a day the tester invented, so no number you will read later is a placeholder.

## STEP 3 — grade yesterday; a browser tab opens by itself

```
bash daily_check.sh 2026-09-23
```
You get: preflight block, the 15 tests, the history table, the file list — and a new tab:
`data/day_report_2026-09-23.html` with the banner, 4 gauges, 10 KPI tiles, 5 tabs
(Scoreboard / Judges / Hours & guards / By hand / Full text) and the day-before-day table.

Then:
- click the top-right **[Copy everything for chat]** and paste it to me — that single
  paste contains the header, the 15 tests, the judge table, the what-if replay and the
  by-hand list, exactly as the .txt file holds it;
- double-click `data/index.html` any time to see **every** day you have ever audited
  (it runs nothing, it only reads the json files) — bookmark it.

If no tab appeared: `bash daily_check.sh --open`, or double-click the file in Explorer.

## STEP 4 — the A/B I promised: was gate 50 the right gate?

```
python audit_day.py --date 2026-09-23 --gate 63 --out r63.txt
python audit_day.py --date 2026-09-23 --gate 45 --out r45.txt
grep -E "if ALL|gate test" day_report_2026-09-23.txt r63.txt r45.txt
```
Each file's header proves the gate used (`conf gate 50%` / `63%` / `45%`).
Three lines to compare: total points, win rate, drawdown.

## STEP 5 — kill the `.env` warnings (no code) — run the dedupe TWICE if you append first

```
cd /a/gitHub/Gold-BookMap
cp .env .env.bak.$(date +%F)
echo "AI_MAX_CALLS_PER_DAY=2000" >> .env
python - <<'EOF'
from pathlib import Path
# GENERIC dedupe: keeps the LAST occurrence of EVERY duplicated key - same rule config.py
# uses. Do not whitelist keys here: a fixed list is how AI_MAX_CALLS_PER_DAY=0 survived
# a first pass and stayed in the file next to =2000.
lines = Path(".env").read_text().splitlines()
seen, out = set(), []
keys = {ln.split("=",1)[0].strip() for ln in lines if "=" in ln}
dups = {k for k in keys if sum(1 for ln in lines if ln.split("=",1)[0].strip() == k) > 1}
for ln in reversed(lines):
    k = ln.split("=",1)[0].strip() if "=" in ln else ""
    if k in dups:
        if k in seen:
            continue          # drop the earlier, dead copy
        seen.add(k)
    out.append(ln)
Path(".env").write_text("\n".join(reversed(out)) + "\n")
print("dropped", len(lines)-len(out), "dead line(s);", " ".join(sorted(dups)) or "no duplicates")
EOF
bash preflight.sh 2026-09-23 | tail -6
```
**Run it again if preflight still names a duplicate** — appending a key that already exists
in `.env` creates a fresh pair (`AI_MAX_CALLS_PER_DAY=0` sat under `=2000` exactly this way;
test 11 prints `AMBIGUOUS .env key` when it happens). The script above is generic now, so a
second pass always converges, and it never touches `GEMINI_API_KEY`.
`GEMINI_API_KEY` already reads `[OK] ... (53 chars)` — do not paste it again.
Only the quota (a live key with `AI_MAX_CALLS_PER_DAY=0` = unlimited calls) and the 10
duplicate keys remain. Do not touch `CONFIDENCE_THRESHOLD=50`, `AI_MIN_SIGNAL_STRENGTH=6`,
`BOOKMAP_WINDOW_SECONDS=10800`, spread cap `0.50` — your P3 is active and correct.

## STEP 6 — apply v7.1 so test 9 stops being a retro-parse

Your `main.py` is still 47665 (P3): the diary keeps `notes[:50]` and no `judge_votes`, so the
judges are reconstructed from their sentence. 48991 + `judge_panel.py` writes them structurally.

```
cd ~/Downloads && rm -rf Gold-BookMap-v7.1-JUDGE-PATCH && unzip -o Gold-BookMap-v7.1-JUDGE-PATCH.zip
cd Gold-BookMap-v7.1-JUDGE-PATCH
cp /a/gitHub/Gold-BookMap/main.py /a/gitHub/Gold-BookMap/main.py.bak
cp main.py judge_panel.py /a/gitHub/Gold-BookMap/
cd /a/gitHub/Gold-BookMap
python -m py_compile main.py judge_panel.py audit_day.py dashboard.py tools/dashboard.py tools/check_history.py && echo COMPILE_OK
python -c "import main, judge_panel; print('IMPORT-OK', len(main._judge_panel_from_notes(['footprint delta +0.4 dominant 2032.4 strength 0.5 -> BUY'])))"
```
`COMPILE_OK` + `IMPORT-OK 1` = done. Never copy while `main.py` is running. Leave
`step2_market_analysis.py` alone (keep your 200646 base). Revert: `cp main.py.bak main.py && rm judge_panel.py`.

## STEP 7 — send me

1. STEP 2's four OK lines
2. the STEP 3 console output (or just the clipboard paste from the tab)
3. the STEP 4 `grep` lines
4. the new preflight `RESULT:` line

After that your daily line is `bash daily_check.sh` (or double-click `dashboard.bat`), and the
history table shows prints, snapshots, diary, MT5 orders, gate, what-if pts, WR%, PF,
best/worst judge, latency and flags for every day you audit.
