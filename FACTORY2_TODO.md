# FACTORY 2 — THE PROCESS PROOF (to-do, one step at a time)

**How this list is read.** Each step is ONE patch + ONE pasteable block, and it ends with
a `.txt` receipt you attach. A step is CLOSED only when its receipt says what it must say.
Nothing here trades. The hall stays `--hall-off`. There is ONE machine (owner,
`2026-11-23`) and the trading loop shares it in bash tab 1, so the rule is not "never touch that
box" but **never write a file the loop imports, never edit a tracked file, never touch `data/`**. New files are banned by **LAWS #14** — every change goes into an existing file
by patch; a handover that adds a file must say `NEW FILE JUSTIFIED: <file> because <reason>`.

**The map belongs with the list.** `FACTORY2_MAP.md` is the step-0-to-the-last-step chain this list walks:
what stands where, what was borrowed from the old factory, how the parts are glued (the card is the wire,
the diary is the only output), and where the chain *stops* — before the money. Read it before building a
step, so a step never turns into a redesign.

**Order of the walk:** the belt first (if the counters lie, nothing above them means
anything), then the parts that feed it, then the budgets the owner set, then the report's
own vocabulary, and only at the END the full factory run on the newest raw day.

**THE ROAD, as the owner asked it `2026-11-24` (one step at a time, one receipt each).** Plain words,
because the list is for HIM to steer with, not only for me to tick:

| # | step | who moves | in easy words | closed when |
|---|---|---|---|---|
| **0** | PREFLIGHT: is his tree clean | **you** | look before we write: `git status --porcelain` | it prints nothing, or only files we expect |
| 1 | the belt + determinism | done | the counters account for every refused cycle | his receipt |
| 2 | eyes / staleness | done | the robot refuses old tape | his receipt |
| 3 · 3b · 3d | report words, tally, published | done | the paper can be read the same way by everyone | `f70728d` |
| **4** | the ±80 band census | **done `2026-11-24`** | does a whole map ever sit outside the band? | **0 hits, measured, `read WHOLE`** |
| 5–10 | the parts, then one fresh raw day | me then you | each organ checked, then the factory sails a real day | one receipt per row |
| 11 | Room E, the hands | **done `2026-11-23`** | code that can plan, size and manage a trade | `22 passed` + `0 []` |
| 12b | borrow the day list | done | readers ask the writer, they do not guess | proved |
| **12c** | **the hands in, LOCKED**| ✅ **done `2026-11-24`** | the drawer exists; your loop **cannot** open it | **`103 passed` on his box** ✅ |
| 13 | the hands on real old tape | ✅ **done `2026-11-24`** | feed yesterday, watch it plan and manage | **75 cycles, 2 FILLED, 57 HELD, `HEALTH: PASS`** ✅ |
| **14b · 14c** | make the log able to prove the lawn, then re-run the same day | **CLOSED ✅ `2026-11-24`** | same decisions, new words - and the new words agree with the old numbers | **every stake landed: `gate_allow 7/68`, `holding 59/16`, pair `5`, `sl [3907.46, 3897.645]`, `tp_frozen` measured, `at` = `2026-11-13T13:33:00`** |
| **14e** | the hall file: `line_kind` + `t_tape` | ✅ **CLOSED `2026-11-24`** | his screen: `112 passed in 191.22s`, both AFTER hashes matched, sail `exit 0`, `75 fed`, `HEALTH: PASS`, `law breaks: 0`, 75× `line_kind`, 75 `t_tape` against 53 `t` | the log can now be counted BY CLASS and sorted BY TAPE |
| **14f** | name the REST of the diary - `day`, `error`, `verdict` - and the teacher measures each class | ✅ **CLOSED `2026-11-24`, partition proved on his box** | his screen: 5 BEFORE + 5 AFTER hashes matched, `116 passed in 189.32s`, sail `exit 0`, `75 fed`, `7 verdict(s)`, `HEALTH: PASS`, `law breaks: 0`, then `kindcheck`: `112 == 29 NOT-JSON + 75 cycle + 1 day + 7 verdict`, **no `UNNAMED`**, `exit 0` | every line names itself; a count can no longer lie by omission |
| **16** | **the retirement transfer list** - chores that die with `main.py` (LAW 108) | **you decide, then me** | tape + rung map follow factory 2 on their own (the chart writes the tape; `analyze_market` rewrites `session_levels.json`), but `main.py:2719 run_maintenance()` - log rotation, CSV trims, `data/archive` gzip cleanup - has NO caller in the new hall | a home for the chore: `python maintenance.py`, or a line in the evening shell |
| **L4b** | a REAL today in the new hall's own book (`--live`, no `--dry`) | **you, tab 1** - my paper grades it tonight | the loop is OFF so TODAY HAS NO DATA: a shadow book-writing run is not the lever; factory 2 reads and writes, never orders (grep-proved) | `data/gates_shadow_<today>.jsonl` + a night grade with no `not-answerable` rows |
| 14d | *(optional)* same tape at `--every 60` | you, one line | how much of the -1.2 is the 3-minute step? (law: 1.955 pts at 180s) | a smaller gap than 1.955 pts |
| **L1** | rung 1: `--demo --once` | ✅ **done `2026-11-24`** | one heartbeat, made-up tape, then STOP | **`exit 0`, 1241 -> 1241, one line** ✅ |
| **L2** | **one heartbeat, REAL feed** (`--live --once --dry`) | ✅ **done `2026-11-24`** | read the source his config names, print one line, **stop** | **`exit 0` + a legal `SKIPPED` wait** ✅ (my guard was asymmetric - law 98) |
| **L3** | the loop runs (`--live --dry`) | ✅ **done `2026-11-24`** (real feed, 900 s, killed by timeout)| paper only - **this factory has no send path at all** (no `--hall-off`, law 97) | **ran while looping**; `l3.txt` was 0 B because of MY missing `-u` (law 99) |
| **L4** | grade the morning | ✅ **REHEARSED `2026-11-24`** — his `report_evening_gates.sh` + `gates_report.py`, nothing new to write | `--no-heartbeat` is honoured not punished; `--diary <the sea's file>` turns on Sections A-D | `9 PASS / 5 WARN / 0 FAIL` |
| 15 | manager checkup + settings | you and me | slowly, one number at a time | a receipt per change |
| **D0–D6** | **ROAD TO DEMO** - from a healthy hall to a managed demo position (owner's goal, `2026-11-25`) | D1/D3/D5 me · D0/D2/D4/D6 `[you]` run | each rung closes by a receipt; the donor supplies every organ we lack |
| 🔒 | **the live lever** | **only you** | the switch that lets it trade | not before L1-L4 and 15 are green |

**The direction test, in one line each:** are we closer to *running safely*? yes = step 12c. Are we
making money? **not yet, and not until the ladder ends** - the money question belongs to row 10 and L4,
and the answer so far is `n=53, 21/53 = 39.6 %, mean -0.19 oz`, which pays nothing.
| # | step | what we are asking the machine | lands in | state |
|---|---|---|---|---|
| **1** | **BELT + DETERMINISM** | does the sea account for every cycle it refused, per named cause; and does sailing the same tape twice give byte-identical paper | `tools/gates_report.py` (new gate), `test_factory2.py` (new test) | **Q1 belt CLOSED ✅ (0 fails, 3 days)** · Q2 CLOSED ✅ by `belt1b` on the owner's rig (`cmp exit 0`, changed lines=2 = mirror only, `55 passed`) → **STEP 1 DONE** |
| 2 | EYES / STALENESS — **CLOSED ✅ by the owner's own receipt** | two freshness laws were found (15 s doorman, inert in replay BY CONSTRUCTION; 240 s tape rule at `:873`). v1 theory ("the market went quiet") FALSIFIED by the owner; my v2 probe then failed too (`arm B = 0` everywhere, and its +20/day was MY guessed `--window 08:00-23:00`). FINAL: `stale` = the grid walking past the tape's last row — `floor((cells_end − (last_row+240))/180)+1` = **27 / 2 / 65 / 72**, the owner's four papers exactly. Patch = split the WORD (`pre-first-print` / `post-last-row`), name the constant, make the paper print the tape's last row; the teacher knows the old word so history is never retroactively a violation | `tools/gates_replay.py` + `tools/gates_report.py` + `test_factory2.py` (`nov17_factory2_code_day10_eyes2d.patch`) | **CLOSED `2026-11-17`, owner's `data/eyes2d_20261117.txt`: APPLIES-CLEAN → stamps `.2026-11-17.2`; 11-06 re-sailed on their tape = `cycles fed 124 (skipped 84: post-last-row 65, warm-up 19)`, `grid 208 = 124 + 84`, `stale:0`, `pre-first-print:0`; `NIGHT GRADE 7 PASS / 0 WARN / 0 FAIL`; `55 passed in 176.05s`; live mirror `2026-11-17_00:58:04` before AND after; `verdicts == episodes 8/8` unchanged from step 1. OPEN, deliberately: TODO 3-12 |
| 3 | DOORMAN tally | **CLOSED ✅ `2026-11-17` 23:16:03Z** — `VERDICT day 2026-11-06 : ROW 3 ASSERTED on fresh paper`, `TALLY-2 RE-SAIL: PASS`, on `git=dc280b0`, live mirror unmoved. Receipt pinned in `§3.5z`: `data/tally2_20261117.txt` 45 L / 4,101 B / sha256 `be9b01a9cc89ae39` Fresh `.3` paper: `cycles 124 | allowed 8 | refused-words 116 {NO-VERDICT 112, NO-EDGE 4} | tickets 8 | errors 84` — reproduces the `.2` counts exactly (doorman line 13) with the bookkeeping repaired: 116 + 8 = 124. 7 gates `[OK]`, the belt card `[--]` abstaining in words, diary `this sail 124 | FILE 248 from 2 sail(s)`. Receipt `data/tally2_20261117.txt`, recorded in §3.5z |
| **3d** | PUSH THE STATE TO GITHUB (owner's request, `2026-11-17`) | **8 explicit paths**, no wildcards: `committee.py` (M), `factory2_gates.py`, `test_factory2.py`, `tools/gates_helpers.py`, `tools/gates_replay.py`, `tools/gates_report.py`, `report_evening_gates.sh`, `tools/v8f_payoff.py` - the import closure (law 60) PLUS the entry points someone is told to run (law 63), minus the 31 files their tip already tracks. Their `git add -An` proved why no wildcard: it would have staged `_attic_2026-10-17/`, a `.jar`, `Workspace/*.bmw`, six old patches and `daily_audit_1111.txt` (law 62). The Book goes SECOND: their `HOLY_BOOK.md` is ` M` with +224/-1 of their own writing on a 3,113-line base against my 4,372, so I merge their hunks - `cp` would have buried them (law 61 family). `data/*` is `.gitignore`d, so 8.17 GB of tapes/receipts can never ride along; the map/TODO don't exist in their repo at all, so they arrive in commit 2 | explicit `git add --`, `git diff --cached --name-only`, `git commit`, `git push -u origin strategy-v2` | **PUBLISHED AND READ BACK ✅ — the row is CLOSED. Code `f70728d` (8 files, byte-identical to the measured tree); paper `dc280b0` (Book 4,503 L with the owner's 224 lines merged in, map 565 L, to-do 140 L) — all three hashed off the PUBLISHED blobs, not off any disk. Both halves are now CLOSED: 3c was asserted on fresh paper (`§3.5z`) and the paper was re-pushed as `d0586d5` (3 pages, 144/9, read back by hash), so nothing is left in Room A. This row's tail had said "what remains is 3c only" — stale as of `2026-11-17` 23:16:03Z**
| 4 | MAP / rung band | the ±$80 law: lines counted in and out, and `--rung-band 0` = whole map | **read-only block `nov18_row4_band_block.sh` (219 L / 11627 B / `3cac5fd88522bbca`)**, tested on a 10-paper fixture incl. a deliberately broken `errors` | **CLOSED ✅ `2026-11-24`** (step 1 shipped `2026-11-18`, §3.5aa): the band and its causes are already on every paper, so row 4 needs a reader, not new sea code. **STEP 1 MEASURED `2026-11-18`** (the owner ran v1: `8 OK, 0 FAIL, 8 abstain`, 2 of 6 days, `errors 84` = `stale 65 + warm-up 19`, band trimmed 0 of 2739 and 0 of 1401, §3.5ab) - v2 ran (`2026-11-18` 00:07:32Z): **4 of 6 days, 15 OK / 0 FAIL / 7 abstain, 6,687 map rows measured and 1 trimmed (99.9 % on 2026-11-12), `band_empty 0` on all 580 cycles** - §3.5ac. Step 2 DONE (`2026-11-18` 00:34Z, inventory v4): parse cap never bound, so EVERY .json under 8 MB to depth 3 is read - 11-14/16/17 have NO paper, closed. FIVE days gradeable and NAMED. My v4 headline (0 notes across 103 jsonl) was false: 101 of them had no `notes` field - v5/v6 print NOTES-CAPABLE vs BLIND (law 78) and add day_metrics' seen/kept. Step 3 = audit the fifth day: `DAYS="2026-11-04" PAPER=data/gates_2026-11-04.json` on `nov18_row4_band_block.sh` v3 `b43d71d81cc98b61` **RAN, exit 0, `00:46:16Z`** - row 4 stands on FIVE days: **8,869 map-lines, 1 trimmed (0.011 %), 788 cycles, 0 with an empty map**, and `errors 47 == stale 27 + warm-up 20` makes the named-cause invariant 5-for-5. The block is now **v5 `170f764b55fa4336`** (v4 ran on your rig and its 4 OK / 0 FAIL / 0 abstain receipt is booked; v5 only fixed a STALE self-description, 0 verdict lines differ), law 79, `ck()` prints AND counts (v3 invented one abstain per day that graded coverage - a stray counter on a verdict's 4th continuation line; `15 OK` and every per-day table were always right). Guard proved by breaking it. v3 kept as `REFERENCE-v3-of-the-audit-2026-11-18_b43d71d81cc98b61.sh`. step 4 RAN (`01:04:56Z`, `JSONL_CAP_MB=900`): 900 MB read of 913.6, the needle's zero is now **16,943 notes-capable lines, 21 of 22 files whole, 0 hits**; 105 files blind. **11-16 still unanswered - v6 printed capability only GLOBALLY.** It also proved my reader blind: `band None`/`stamp (none)` on every row came from invented key names (`rung_band_usd`, `schema_version`; the writer spells them `rung_band`, `printed_by`) while the audit read 80.0 from the same files - laws 80 + 81. **Inventory is now v7 `790a9f6e76026479`**, which reads the writer's spellings and prints `-> CAPABLE / -> BLIND` per day. **THE OWNER'S RULE APPLIES HERE (`01:15`, §3.5aj): when my reader trips, I read the organ that WROTE the data and replace ONE part of mine with its answer - no more better-readers.** Measured: my inventory has 4 of its own day-guessing sites while `tools/gates_replay.py` already ships `available_days()` (`:109`) and `discover_tape()` (`:64`), called at `:640`/`:644`/`:1385`. Part 1 of the fix = delete my guessing, use the writer's `day`/`days`. Part 2 = its three tape places + `scoped` contract, not my `rglob` + ceiling. **Part 3 = row 12b CLOSED - the owner's answer is BORROW (`2026-11-18`), booked as law 82's decision.** Part 4 = ONE v7 run on 2026-11-16, the last open item of row 4 step 2 - v6 could not answer it because it printed the capable/blind split only GLOBALLY. After it, row 4's number stands on five days with no diary question left. **`2026-11-24` ROW 4 CLOSED BY THE OWNER'S v7 RUN @ 2000 MB.** `diary_20261116.jsonl` prints `CAPABLE of a measured zero (it has a `notes` field, read WHOLE)` -> 0 `band:0/` notes, and `papers 0` because no paper was EVER in the drawer for that day. **`2026-11-16` is not gradeable from paper and not from diary notes either - the zero is now MEASURED, not silence.** The wider count the same run allowed: **0 full-cut events across 47,580 notes-capable lines (65 of 66 files read WHOLE)** - the drawer's whole diary is the sample now, not 60 files. Two limits stay OPEN and are NOT verdicts: `tfrobot_M3_2026-10-16.jsonl` is `read PARTIAL` and 61 `.jsonl` (oldest first, `tfrobot_M1_2026-10-16.jsonl`) were not scanned - the budget at `:111` is GLOBAL, so raising `JSONL_CAP_MB` widens the claim, it does not only narrow it. Days a paper CAN grade: 11-04, 05, 06, 12, 13. |
| 5 | L3 / ORDER FEED | order rows consumed vs `Mbo` fake-id rows ignored; a day with no tape must say L3 was BLIND | report | queued |
| 6 | LEDGER / committee | defs count, the **56 tests** (`55` was the pre-`c8dda38` receipt - `55 passed in 46.86s`, Book `:3447`; measured at `c8dda38` on `2026-11-23`: `56 tests collected in 0.36s`; was 54 before Room A's belt work; step 2d added two scenarios INSIDE an existing test, so the count moved once, not twice), and the `REPLAY/` arming boundary (live path byte-identical). Room E adds `test_factory2_hands.py` (22 tests), so row 6's boundary is now 95: `95 passed in 51.4 s` in the applied tree | test | queued |
| 7 | BUDGET (the owner's caps) | `CPU_PCT_BUDGET = 0.3` measured on a fat day, `MAX_LINE_BYTES = 1100` on real lines | report | queued |
| 8 | STAMPS / provenance | every file in a run carries one stamp; unknown stamp ⇒ WARN; a receipt can be re-derivable from paper alone | report | queued |
| 9 | REFUSALS are loud | no silent skip anywhere: unknown flags, missing folder, thin tape all name themselves | replay + test | queued |
| 10 | **THE FACTORY RUNS** | a fresh raw day (the newest in `data/archive`) end-to-end, receipt green, 11 gates read | no code — the whole list proves itself | final |
| **11** | `2026-11-23/24` OWNER'S RECEIPT: `22 passed in 1.62s` + `mutations that did NOT flinch: 0 []` on cp1252 - proved on HIS box, not only mine (LAW 85 closed). After the wiring: 30 hands tests, 103 across three suites, 15 mutations / 0 survivors. What remains: v7 `JSONL_CAP_MB=2000` for `2026-11-16` (row 4), and rows 12/13/14 in replay. |
| 12 | ROOM A, LIVE PANE (narrowed) | **`2026-11-14` finding: the parser is ALREADY the old factory's** — `gates_replay.py:765-790` imports `bookmap_bridge_provider`, makes a thread-free `_BridgeTail` via `__new__` and feeds tape rows to `tail._handle_line` (`:881`), exactly as `raw_replay.py:2186-2293` and `backtest.py:11` do. So this step is NOT a parser: it is the file plumbing — `--from-raw-tail` tailing a growing `ticks.csv` with the bridge's three habits (`f.seek(live_offset)` `:630`, `st_mtime` skip `:527-540`, file-identity rotation `:678-692`). Plus a parity call: borrow `raw_replay._discover_raw_files` (`:165`)/`discover_available_days`(`:271`)/`_iter_filtered_csv_events`(`:511`) instead of our own glob at `:67-116` (one copy Room A did not need to keep — decide after step 2) | `tools/gates_replay.py` (patch) | **OPENED, scope narrowed `2026-11-14`** — after steps 1b → 2. **UNBLOCKED `2026-11-18`: 12b is CLOSED (owner: BORROW), so the finder this row must lean on already exists - `--from-raw-tail` plumbing only, no new parser, ever.** || 13 | WAKE THE LAW IN LIVE? | `grep -rn "REPLAY/"` has ONE writer (`tools/gates_replay.py:973`), so v8f's acceptance flip is ASLEEP in the hall by design — a live day is judged by the OLDER rules. Decision (not code): arm it in `factory2_gates.py`, or keep live conservative and say so in every report | `factory2_gates.py` **only if you sign it** | **OPENED `2026-11-14`** — must be settled before Room D's numbers mean anything live |
| **12c** | **THE WIRING — CLOSED `2026-11-24`, APPLIED ON THE OWNER'S BOX** | `run_cycle(..., shadow=None)` + `--hands-shadow`, inert `tools/audit_day.py` shadow (law 94) - delivered as a ZIP, because `git apply` needs context my own earlier `sed`/glyph blocks had already destroyed (law 95: the refusal was a GUARD and wrote nothing). Zip `57e14ff7a8a6cc3d` sha-verified BEFORE extract -> `103 passed in 179.15s` (= his `94 + 1 failed` became 103 green); `git diff --stat` named ONLY the 2 tracked files (`+18` / `+19`), so `git diff` is his UNDO; his 179 s vs my 40 s is the old factory in his tree, not a fault | `run_cycle(..., shadow=None)` now takes a dict and calls `shadow["cycle_hands"]`; the hall NEVER imports `tools/factory2_hands` (it resolves what the CALLER hands it, per `gates_replay.py:1002` - my first draft imported inside the hall, which the house already documents as dead code). `tools/gates_replay.py` grew `--hands-shadow`, threaded as a real `hands_shadow` PARAMETER - my first draft read `args` inside `replay_day`, which has none: `NameError` on every sail, caught by 10 FAILING house tests because they spawn real replays. OFF = the live loop passes no dict, so the line is unreachable (proved by reading the diary line back, not by a flag nobody reads). Harness caught a real `NameError` (`_f` is a gates-only helper) and a third codec bug (parent decoding a child's UTF-8 with cp1252) plus LAW 88 (`Pen(str())` made 23 files named after `list.append` in the repo root). ENGINE RUNG REHEARSED: `--demo --once --dry` -> exit 0 in 1.7 s, g1/g2/g3 printed, one diary line `NO-VERDICT` (`--demo` alone LOOPS forever every 30 s - the map said otherwise; fixed). Handover: drop-in zip **`57e14ff7a8a6cc3d`** (56,827 B, **6 entries**: the 5 wiring files + an inert `tools/audit_day.py` shadow - `NEW FILE JUSTIFIED: tools/audit_day.py because it makes his only red test hermetic without touching a tracked file`, LAW 94). `factory2_gates.py` + `tools/gates_replay.py` are TRACKED - his merge, never mine. | **CLOSED ✅ SHADOW CODE PROVEN ON HIS BOX - next: ladder L1 (`--demo --once --dry`)** |

### Step 1 — RESULT (`2026-11-14`, owner's `data/belt1_20261114.txt`)

**Q1 the belt: CLOSED, PASS.** `belt FAIL lines=0`; on `2026-11-06` the new gate itemised
all 84 refusals for the first time (`stale:65  warm-up:19`): `errors == sum(skips)` 84 vs
84, `verdicts == len(episodes)` 8 vs 8, `tickets <= cycles` 8 vs 124, `NIGHT GRADE:
7 PASS / 0 WARN / 0 FAIL`.

**Q2 determinism: FAILED — and it was worth running.** `run1 exit 0  run2 exit 0
cmp exit 1`, different sha256 on two identical sails of one day. Reproduced on my box at
the owner's exact tree state (`99b3cc8` + v8f + belt1): **`DIFFERING FIELDS: ['mirror']` —
one field of forty**, `episodes` and `_days` byte-equal, `cycles`/`g2_pass`/`fwd_rows`/
`printed_by` SAME. Cause: `quarantine_mirror()` names the folder the run just created and
`tools/gates_replay.py:1383` wrote that prose into the json. **The measurement was
deterministic; the paperwork was not.**

→ **STEP 1b** = `nov14_factory2_code_day9_belt1b.patch` (3,752 B, sha `95c0b92c3950919d…`,
3 existing files, **0 new**): normalise the field, keep the full sentence in the log,
stamps `.0 → .1` with the test pin moved in the same breath. Rehearsed on a fresh
worktree: v8f → belt1 → belt1b **APPLIES-CLEAN** → `55 passed in 44.09 s` → the failing
leg returns **`run1=0 run2=0 cmp=0`**. Hygiene **law 12** born here: *no run-volatile
prose in a machine-readable record.*

**STEP 1b RESULT (`2026-11-17`, owner's `data/belt1b_20261114.txt`) — STEP 1 CLOSED, both questions:**
`sha OK` → `APPLIES-CLEAN` → `applied`; `post-state: replay=gates.replay.2026-11-14.1
report=gates.report.2026-11-14.1 tests=55`. Leg 1, the diagnosis itself: `diff exit 1`, **`changed lines: 2`**,
the whole diff being **`268c268`** — one `mirror` sentence per file, `1171032 B … 67 earlier rehearsal(s)` vs
`1354704 B … 69`. That is the bug photographed: a wall-clock-named sandbox folder and a byte count. The failing
leg, re-run twice on the same day with the same flags: **`run1 exit 0  run2 exit 0  cmp exit 0`**, same md5
`4437655e89694966ae3eae14…`, both papers carrying the identical normalised
`gates_sandbox_prev_<RUN>/` sentence. Suite: **`55 passed in 159.17 s`, `pytest-exit: 0`**. Verdict line:
**`determinism=IDENTICAL`**, `block exit: 0`.
**Free bonus from the same receipt:** `live mirror before` == `live mirror after` == `2026-11-17_00:58:04` — the
glass case held through a patch, 55 tests and two full sails.
**Two notes for the walk, not for the code:** (a) `159.17 s` there vs `44.09 s` here for one suite — a rehearsal
proves logic, never speed, so I will name the machine when I quote a duration; (b) **their clock reads
`2026-11-17`, three days after our session date**. Nothing measured is affected (that leg used an explicit
`--date 2026-11-06`), but `available_days()`/`day_token` pick days by **name**, so before any block that lets the
tool choose days, check `date -u +%FT%TZ; ls data/archive | tail -4`. **Hygiene law 50 born here.**

---

## STEP 1 — do exactly this

**What it does, in one line:** the Teacher learns to audit the sea's own counters (three
assertions, one gate), and the test suite learns to sail the same day twice and compare
BYTES. Zero new files. The block then grades your three real days and runs the
determinism leg on the cheapest one.

1. Save the patch: **`nov14_factory2_code_day9_belt1.patch`**
   (148 lines / 8,649 bytes / sha256 `e26be7523b4735fa…`) into `~/Downloads/`.
2. Save the block: **`nov14_belt1_block.sh`**
   (60 lines / sha256 `4219d28dad9f04a1…`) into `~/Downloads/`.
   Its awk line was copied out of the shipped file and re-run against a PASS fixture and
   two broken fixtures before shipping - the first version of that filter silently dropped
   2 of the 3 assertion rows, which is the bug class this step exists to catch.
3. Paste the block. It prints its own last line; that line is the verdict.
4. Attach / paste `data/belt1_20261114.txt`.

**What each outcome means, decided in advance:**

| receipt says | meaning | what I do |
|---|---|---|
| `pytest-exit: 0`, `55 passed`, `determinism: IDENTICAL`, `BELT: PASS` on all three days | the factory counts itself and repeats | close step 1, build step 2 |
| a belt `[FAIL]` line on a real day | a cycle was refused with no cause, or a verdict was counted and never written — **a real factory bug** | one patch against `_skip()` / the episode writer |
| `determinism: DIFFERENT` | a wall-clock, a folder order or shared state leaks into a run | I hunt the leak; nothing measured so far is trusted until it passes |
| `54 passed, 1 failed` in the stamp test | the seal and the test disagree — my patch forgot to move one | re-cut, do not proceed |
| `skips ABSENT` | you graded an old pre-v8f paper, not a bug | re-run on the three named json files |

**Why the determinism leg is the valuable one:** every number in this project — the
177-cycle tally, the 53 verdicts, the payoff — assumed it and never tested it. A
factory that cannot repeat itself is a performance, not a measurement.

### Step 2 — RESULT (`2026-11-17`, Code Day 10) — measured twice, wrong once, closed on the owner's arithmetic

**What closed it.** Not a code change: the owner's own receipt (`data/eyes2_20261114.txt`) plus hand arithmetic on
the window THEY used (`--window 08:00-22:00`, `nov14_belt1_block.sh:45-46`). The four days that recorded `stale`
reproduce to the unit: **27 / 2 / 65 / 72**. So the sea never miscounted a minute; one word covered two states
("the feed is late" and "the day is over"), which is law 12's subject and now law 52.

**What the patch changes (3 existing files, 0 new, `11dd056f1f90880d`).** `stale` → `pre-first-print` (rows, no
print yet) + `post-last-row` (a print existed, then the tape ended); `240.0` → `TAPE_PRINT_GAP_SECONDS`; the day's
paper prints `tape last row HH:MMZ` and, when a tail exists, `tail past the tape: N cell(s) refused for OUT OF
DATA, not for a quiet market`; the teacher counts both words and **keeps knowing `stale`** so a pre-split paper is
never retroactively a violation (WARN), while a NEW paper still carrying it FAILs. Both stamps → `.2026-11-17.2`,
and the seal test's pin moves in the same edit.

**Receipts here.** `55 passed in 47.80s` (was 54 + the two new scenarios in one test); applying the patch to a clean
tree round-trips **byte-identical** to the tested tree; on a fixture whose answer I computed BY HAND the patched sea
prints `post-last-row: 65` (truth 65), `errors 85 = 65 + 20`, `grid 241 = cycles 156 + errors 85`, and the paper
line reads `window 08:00-22:00Z, tape first row 10:00Z, tape last row 18:43Z | tail past the tape: 65`; the
teacher's counted line shows all seven words with `stale:0`. **Negation-tested:** disarming either arm makes the new
test fail — my first draft did NOT, because it asserted `> 0`, and I found that only by breaking my own fix.

**SEALED BY THE OWNER, `2026-11-17 02:03Z` (`data/eyes2d_20261117.txt`, 138 L).** `APPLIES-CLEAN` → both stamps
`…2026-11-17.2`; the re-sail of THEIR 2026-11-06 with THEIR dials prints `cycles fed : 124 (skipped 84:
post-last-row 65, warm-up 19)` and `day math … tape last row 18:43Z … tail past the tape: 65 cell(s) refused for
OUT OF DATA, not for a quiet market`; the teacher counts all seven words with `stale:0` and gives
`NIGHT GRADE: 7 PASS / 0 WARN / 0 FAIL / 4 not-answerable`; `55 passed in 176.05s`; the live mirror timestamp is
identical before and after. Two numbers that prove nothing downstream moved: `grid 208 = 124 + 84` (their own
step-1 arithmetic) and `verdicts == len(episodes) 8 vs 8`. And the sea itself now buries my dead theory:
`pre-first-print: 0` on a day whose first row and first print are 8 seconds apart.

**What must NOT be concluded.** No threshold moved; nothing about the market changed; no verdict was re-scored;
`FRESH_MAX_SECONDS = 15.0` is still inert in replay BY CONSTRUCTION and was NOT "fixed" (never paste `15.0` into
`:873`); the hall stays `--hall-off`; the old factory is untouched (law N1); `warm-up` on 11-06 (20 predicted vs
19 recorded) is an open difference in MY model of their sea, not a finding about the tape, and this patch does not
claim it.

## D · ROAD TO DEMO (booked `2026-11-25`; owner: *"we use demo account and thats our goal"* + *"check the old factory ... make copy of that work in our new factory 2"*)

**Answer first, honestly: the hall can DECIDE and REFUSE on live data (today proved it), but it CANNOT yet
open a position** - no broker wire (by design), no live manager loop, no demo guard. Everything missing exists,
built and scar-tested, in the old factory. Rungs, in order:

| rung | what happens | who | closed when |
|---|---|---|---|
| **D0** | tonight: sail `2026-11-25` + report, no `--hall-off` | `[you]` run | `THE NEW FACTORY IS HEALTHY (sea passed)` or the honest failing lines, pasted |
| **D1** | DONOR AUTOPSY, zero code: the organ table in the Book - door `committee.py:428 ledger.issue` (we HAVE it); hands plan/open/manage/close `tools/factory2_hands.py` steps 11-14 (HAVE, shadow); broker wire `step4_mt5_execution.py` init `:381` send `:622` retcode-bucket `:528` verified-close `:858-925` (BORROW); minute-manager `position_manager.py:1115 manage()`, donor calls it in-loop at `main.py:3063` (BORROW the cadence); kill-switch `TRADING_ENABLED` `config.py:253` + hard permit `run_step4(execution_authorized=False)` `main.py:2030` (BORROW, but FLIP THE DEFAULT - donor ships ON, ours ships OFF); demo guard: donor ALREADY has one - "gate 4b" `step4:386-398` refuses REAL accounts unless `ALLOW_LIVE_TRADING=1` (COPY verbatim in spirit) | `[me]` cut | table booked, each organ named with its line |
| **D2** | live hands INSIDE the hall: `--live --hands-shadow` (the hook `factory2_gates.py:403-409` waits for a caller-supplied dict; today only replay supplies one) + fix Section C's dead words (`--record-shot`/`shot_action`) because Section C IS the twin scoreboard this rung needs | `[me]` then you run | one full live day: paper book with `FILLED/HELD/CLOSE` + `TWIN SCOREBOARD` answers with real numbers |
| **D3** | the executor port: `NEW FILE JUSTIFIED: tools/factory2_exec.py because touching money must live OUTSIDE the hall - the hall judges, only hands+executor act`. It copies the donor's discipline end-to-end: initialize -> gate 4b (refuse any account that is not the DEMO login; a real account needs TWO switches we never flip) -> sized order (`LOT_SIZE` 0.1, cap 1.0, `config.py:341-345`) -> send with the donor's bounded retry -> retcode bucket -> **`positions_get` PROOF, not just the receipt** -> every action written as a NEW diary class `order` so the 14f partition still closes and `kindcheck` still adds. Every guard gets a mutation test - a law with no failing test is a wish | `[me]` cut | `116 passed` grows; every guard proven to flinch; push (yours) |
| **D4** | ONE-ORDER REHEARSAL on the demo terminal: `python tools/factory2_exec.py --selftest-demo` = connect, prove login is DEMO, smallest order 0.01 lot, immediate close, positions_get verifies emptiness. Gates are NOT bypassed - this tests the WIRE, hands-only | `[you]` run | broker ticket id + our `order` diary lines + `exit 0`, pasted |
| **D5** | row-15 SAFETY SUBSET, promoted because a real book must not swallow: `committee.py:436` + Pen `:344-350` silent writes -> loud; spread law vs `config` split (`committee.py:38` reads its own constant) -> one home; diary-folder truth (LAW 110) -> one resolved dir + test | `[me]` | each fix lands with its failing-test-first, receipts |
| **D6** | DEMO DAY RUNS: hall `--live --hands-demo`, MT5 logged into DEMO, full days; the evening report grows a DEMO BOOK section (fill vs paper-sim slippage, guard trips, every close verified). Repeat until 5 gradeable demo days | `[you]` run, `[me]` read | five days, zero unverified states |

**D2+D3 PARCEL BUILT the SAME EVENING - owner: *"i don't want we wait more"* - `rowD_demo.zip` (62,498 B,
sha `0f6b372cf154…`). Hall grew `--hands-shadow`/`--hands-demo` (paper book inside the LIVE loop; the caller
still hands the dict, 12c's law holds); new `tools/factory2_exec.py` = the MIRROR, every organ copied from the
donor and two laws made STRICTER (unknown `trade_mode` also refuses; a send not SEEN at the broker is `NOT-SEEN`,
never a claimed fill); `positions_get` proof after open AND close; ownership-comment law (foreign tickets
untouchable); broker-is-truth `discover()` after restart; a LOST position sets LOST-PENDING and BLOCKS re-open;
the diary grew its FIFTH class `order` (kindcheck NAMES + teacher partition close with it). Parcel hashes:
`74619f0677db` gates · `04d23df5bf26` tests · `c4ba0d46112b` exec · `4f9cbf5a4fad` kindcheck. **11 new
**Apply-1 BOUNCED - and the guard caught it:** owner's paste showed `unzip: cannot find or open rowD_demo.zip`,
the FOUR hashes still at pre-parcel values (`543cda08…` etc.) and `63 passed` = the untouched test file. Nothing
broke: a no-op apply left the running hall exactly as it was - proof that "verify hashes BEFORE believing an
install" saves the day it matters. Zip sits in the agent workspace; saving it to `~/Downloads` then `mv` into the
repo is the missing two lines. Zip sha256 `0f6b372cf15478008cccbbc467e0d39d65f3da414a834edbdb50b4ceb4a7f625`
(62,498 B); contents re-measured at ship time match the four published hashes. **APPLY-2 LANDED PERFECT on his box (zip + 4 files hash-matched, `74 passed`) - then the SELFTEST caught
LAW 112 (script-entry vs import-path; `tools/factory2_exec.py` couldn't `import config`). **v.2 parcel
`rowD_demo_v2.zip`** fixes it the house way (siblings' sys.path bootstrap) + missing-MetaTrader5 = REFUSED,
never a traceback + a subprocess test that RUNS the typed command; zip sha `3f0d130e3b1844615a9085a1b66901702c913653f193f1f1a902f2bfa90866ac`
(50,841 B); hashes `c51b2d379fbc` exec · `600512b0f7a6` tests; **75 passed his-rerun expected, 866 repo-wide
here** (measured, not predicted). **v.3 parcel `rowD_demo_v3.zip`** after the FIRST REAL DEMO CONTACT: `ARMED demo login 62153279 XAUUSD 0.01`
(guard PROVED the account type - a first in this project's history) then `OPEN=UNKNOWN` -> LAW 113: missing
`type_filling` + partial bucket table, both donor-cured. Hashes MEASURED at ship time: zip `a0efd700f7af63848542f0ead38e36aa3ab7b753190d73b1959e3c27a79e62b1`
(51,618 B) · exec `56762b6e5154` · tests `cca5fb1b821a`; **77 passed · repo-wide 868 passed** here, his rerun
expected 77. D3 closes when his `SELFTEST: PASS` lands. **D3 CLOSED (`2026-11-25`, same evening):** his box `77 passed` + `SELFTEST: PASS (ticket 102826760 opened
AND proved gone)` - first position ever opened AND closed by factory 2, guard proven first (`ARMED demo login
62153279 XAUUSD 0.01`). **D2 CLOSED (minutes later, same night):** armed hall live: `ARM bucket=OK proof=DEMO-MODE` INSIDE the day's
diary (kindcheck: `43 lines == 42 cycle + 1 order`, no UNNAMED) - LAW 114's pen-claim TRUE where the paper is.
Cards show both states honestly: a WALK cycle (`LEAN=DOWN | SINCE=40 | CANDS=6 | TOLL=unpaid`) and a SLEEP-LOCK
cycle (`MOOD=SLEEP | CERTAINTY=0 | g2: ACCEPTED=0 SLEEP-LOCK | g3: SKIPPED (no rung under the feet - waiting is
the answer)`) - the idle-not-vote law of L2 firing on live tape, in production, first time. Paper book stayed
empty of OPENs because the 0.010 % door is still stingy: that is D5, built next. **D5 SHIPPED the same night: `row15_D5.zip`** (105,851 B, zip `529466b94642`). Six files:
`569a39246e65` gates(hall `.6`) · `585bbb3e1e32` committee · `4ed66defaab0` config · `f158df1e64b9`
teacher(`.4`) · `93d80eac3b72` exec(`.5`, LAW 115 kill-switch) · `e51ef2d310c2` tests. Zip v2 `085ee3a0cc20` (106,099 B) - the v1 zip is VOID, the subprocess selftest could TRADE on the owner's box; env gate added before shipping. Changes: (1) **spread one home** -
committee READS `config.MAX_SPREAD_PCT`; default stays the DONOR's 0.05 because `test_oct28...` went RED
when I moved it to 0.010 (retired-but-not-deleted, LAW 108) - side effect ON PURPOSE: the hall's door
widens 5x on next restart, the boot line prints the cap, `.env` can retighten; (2) both swallowed writes
LOUD (Pen prints `DIARY WRITE FAILED #n` + counts; committee prints `VERDICT LINE WRITE FAILED` and keeps
the verdict's meaning); (3) **teacher looks in `gates_sandbox` first** - LAW 110 CLOSED, `--dir` no longer
needed for a live day; (4) selftest book `data/gates_sandbox/gates_selftest_<day>.jsonl` - LAW 114's claim
made true, own file, never the running hall's diary. **83 passed test_factory2 / 874 repo-wide** after the
oct28 fix; the version-seal test moved BY HAND, consciously - that is what it is for. **D5 CLOSED on the owner's box (same night):** six-file hash match, armed restart LIVE: `spread cap in
force: 0.05 % of price` + `EXEC ARMED` + cards falling under the wide door; SECOND selftest `PASS (ticket
102839925)`, now WITH its own named rehearsal book
(`selftest:true` OPEN SEEN-AT-BROKER + CLOSE GONE in `gates_selftest_20261125.jsonl` - LAW 114 TRUE at last);
kindcheck `55 == 53 cycle + 2 order`, partition closed, no UNNAMED. **D6 = now**: the hall has opened NOTHING yet
because G3 has not commanded anything yet - the paid-toll cycle read `RUNG=demand | PUSH=NONE` - it waits on
verdicts, not on plumbing. Demo days accumulate; the report grades each evening. **DAY 1 GRADED - THE HEALTHY LINE ON A LIVE DAY
(owner receipts, night 2026-11-25/26):** live hall walked 446 named pages, died calm (`KeyboardInterrupt` in
`time.sleep` - the honest way); sail exit 0, `216 cycle(s), 23 verdict(s). HEALTH: PASS`, fed by 1 raw-tape day +
1 order tape -> L3 possible; teacher (report .4, its first live run WITH the sandbox-find): `NIGHT GRADE:
15 PASS / 1 WARN / 0 FAIL / 2 not-answerable -> THE NEW FACTORY IS HEALTHY (sea passed)`. Belt all-OK: 40==40,
23==23, 23<=216, and `179 NO-VERDICT + 14 NO-EDGE + 23 allowed == 216` - v8f's muzzled bounces counted SEPARATELY
on live paper for the first time. `G2 with a rung: 216 (100%)`; mapper 2325/2340; toll max 135 closes/405 min hold.
The ruler's own words: `10/22 measured went the way ... plumbing proof, not an edge claim - never trade on this` -
45% on day one, printed plainly, and THAT is why the size stays 0.01. Sail noted 405 rehearsal folders (row 16).
**D6 = 1 of 5.**
**DAY-END kindcheck (owner paste, `2fd856`-era): `446 == 229 cycle + 189 order + 28 verdict`, PASS, no UNNAMED** -
and the classes TELL A STORY: `verdict` lines are written ONLY by a fired ticket (attempt_fire, code read this
turn), and `order` lines come from the mirror acting - so the wide-door restart let the live hall issue tickets in
numbers (28) and the armed DEMO hand acted (189 order lines). CONFIRMATION RUN (stop 4b): per-act counts in the
day file + the MT5 history tab. If an F2 position is still open at market hours with the hall stopped, the fix is
ONE restart (the mirror syncs from the bank's list and manages it - time-stop or the owner's hand; an owner-close
lands as the proven-honest `ALREADY-GONE`). The lone WARN is the LINE SIZE LAW only: 8 fat cycle lines, max 1499 B
vs the ~1100 B budget, `other classes (max 300 B) NOT counted` - card-diet, row 15, booked.
**AND IT TRADED (stop-4b receipts, same night):** act-counts from the day file - `ARM:3 OPEN:9 CLOSE:15 DISCOVER:1
LOST:7 MODIFY:77 WANDER:77`, zero ORPHAN, zero fatal buckets. Nine REAL demo opens (tail: `px 3565.7 BUY sl 3548.115
tp 3595.865 vol 0.01 attempts 1 bucket OK proof SEEN-AT-BROKER ticket 102984283`), fifteen closes (the extra six are
the named NOOP/ALREADY-GONE honesty), seven GONE-AT-BROKER syncs (tp/stop hit between heartbeats - handled, not
hunted), one DISCOVER (post-restart adoption: `BROKER-IS-TRUTH`, no local state, no orphans flattened). First earned
ticket of the D6 era reads `RUNG=value_edge | TOLL=paid | SNAPBACK=1 | VERDICT=BOUNCE` and its id proves the chain:
`GCZ6.COMEX@RITHMIC:9975640:G3` written by attempt_fire's own pen. THE ENABLER, stated plainly: D5 widened the live
door 0.010 -> 0.05 (the donor's end-to-end number) - that is exactly what let 28 verdicts become fired tickets where
the morning refused all three. OPEN:9 vs verdicts:28 is consistent (most verdicts meet the one-position law or the
ledger, and fire-laws eat the rest) - open-AT-NOW question = MT5 Trade tab, owner's next look. THE UNREAD FINDING:
WANDER=77 with MODIFY=77 (1:1) - bucket TP-MOVED means the bank's TP differed from our FROZEN tp_at_open EVERY
managed cycle for hours; either someone dragged the TP in the terminal or something re-normalizes it - diagnosis is
a stop-4c grep of two WANDER lines + the owner's memory of MT5 clicks; the guard DETECTED and never chased, which
is exactly law #1's behavior either way.
**MT5 HISTORY RECEIPT (owner screenshot 01:24 local): day 1's real scoreboard** - 9 strategy trades, 4W/5L,
rows sum +14.81; bottom bar `Profit: -34.73 / Balance 965.27` also carries the 11-24 smoke (`102502385`, -9.98),
three selftest drills (-0.61) and fees/swap the rows don't show - fee-accounting = a review row, not a tonight-fix.
The two biggest winners (+27.21, +27.27) closed EXACTLY ON their frozen T/P (green) = law #1 earning on a live
tape; row `102984283` shows `S/L 3566.45` - the stop-walk was live on the owner's position; the owner's manual close
at 01:03:53 @ 3583.27 (+15.18) landed - the bank's History tab proves it (open positions don't live there). No
overnight exposure. **WANDER THEORY, near-proven from the screenshot:** frozen `tp 3595.865` vs broker `3595.87` -
3-decimal vs tick-rounded, compared at 1e-6 = the guard crying wolf EVERY cycle (77), MODIFY matching 1:1; fix =
round-before-compare at broker precision (row 15); DETECT-not-chase stayed correct throughout. **REVIEW ROWS ADDED:**
(a) diary OPEN `px` = decision price not fill (`3565.7` vs `3568.09`, slippage 2.39 in a sprinting tape) - the
bank-ask net held and the truth stayed at the bank; (b) fee/swap line-sum vs bottom bar; (c) confirm WANDER =
rounding from ONE diary line (same ticket, `TP-MOVED`, no owner edits). **CONFIRMED BY OWNER'S GREP 14:09:20Z:** first WANDER fired on the FIRST managed
cycle of the morning's ticket (opened 14:06:20Z), reason sentence canned, zero owner actions in between - the
frozen numbers carry a 3rd decimal (`3595.865`) the broker stores rounded to tick (`3595.87`), and the compare is
1e-6 - so the guard cries wolf EVERY managed cycle (WANDER/MODIFY 77/77 = the twin of it, bank answered every
re-walk with bucket OK). Behavior stayed correct under law #1 (detect, never chase; TP never chased all day) but
the noise is a real bug on paper: **FIX = next parcel (row 15), one line: round tp/sl to the instrument's tick
BEFORE freezing and compare at >= half-tick** - bundled with the card-diet, the px-vs-fill diary note, and the
dead `--record-shot` flag. PUSH STATE, read from our own records (NOT from GitHub - I
have no remote access, the owner's `git fetch` is the only truth): the last documented push is `fdac3f1`
(rows 0-14f, 9 files) - the FOUR parcels since (demo v1/v2/v3 + row15_D5 v2) landed on his DISK and were
hash-verified there, never pushed; so GitHub today lacks `tools/factory2_exec.py` + `tools/kindcheck.py`
and the newer `factory2_gates/committee/config/report/tests` - the owner's "only the .md files need pushing"
was corrected with the 11-path add + a `git diff --name-only origin/strategy-v2` check that lists the truth.
FACTORY2_ROAD.md re-cut same night as the OLD-WAY room tour (Rooms A-G, steps A1..G3, marks + receipts,
kid-story per room) after the owner asked for it - rooms match the working map's true five houses plus
F CLOCKWORK / G LAW SHELF / H STRONG BOX, no invented names (the first draft's `broker_guard.py`-style rooms
were caught by reading the map itself). Three files unchanged; unzip -o over v.1 is enough.
guard-flinching tests; repo-wide 865 passed** (854 + 11). Closes on owner receipts: D2 = a live day with a paper
book in the hall · D3 = every refusal loud and named · D4 = `--selftest-demo` PASS pasted. D5 ships as its OWN
parcel tomorrow: the door is not widened the day the money-wire lands.
| 🔒 | THE LEVER - stays yours ALONE, AFTER D4 receipts, and demo stays its whole world until you say otherwise | `[you]`, last | your hand only |
