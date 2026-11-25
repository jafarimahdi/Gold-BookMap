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
