# FACTORY 2 — THE PROCESS PROOF (to-do, one step at a time)

**How this list is read.** Each step is ONE patch + ONE pasteable block, and it ends with
a `.txt` receipt you attach. A step is CLOSED only when its receipt says what it must say.
Nothing here trades. The hall stays `--hall-off`. The noisy house (Windows 1) is never
touched. New files are banned by **LAWS #14** — every change goes into an existing file
by patch; a handover that adds a file must say `NEW FILE JUSTIFIED: <file> because <reason>`.

**The map belongs with the list.** `FACTORY2_MAP.md` is the step-0-to-the-last-step chain this list walks:
what stands where, what was borrowed from the old factory, how the parts are glued (the card is the wire,
the diary is the only output), and where the chain *stops* — before the money. Read it before building a
step, so a step never turns into a redesign.

**Order of the walk:** the belt first (if the counters lie, nothing above them means
anything), then the parts that feed it, then the budgets the owner set, then the report's
own vocabulary, and only at the END the full factory run on the newest raw day.

| # | step | what we are asking the machine | lands in | state |
|---|---|---|---|---|
| **1** | **BELT + DETERMINISM** | does the sea account for every cycle it refused, per named cause; and does sailing the same tape twice give byte-identical paper | `tools/gates_report.py` (new gate), `test_factory2.py` (new test) | **Q1 belt CLOSED ✅ (0 fails, 3 days)** · Q2 CLOSED ✅ by `belt1b` on the owner's rig (`cmp exit 0`, changed lines=2 = mirror only, `55 passed`) → **STEP 1 DONE** |
| 2 | EYES / STALENESS — **CLOSED ✅ by the owner's own receipt** | two freshness laws were found (15 s doorman, inert in replay BY CONSTRUCTION; 240 s tape rule at `:873`). v1 theory ("the market went quiet") FALSIFIED by the owner; my v2 probe then failed too (`arm B = 0` everywhere, and its +20/day was MY guessed `--window 08:00-23:00`). FINAL: `stale` = the grid walking past the tape's last row — `floor((cells_end − (last_row+240))/180)+1` = **27 / 2 / 65 / 72**, the owner's four papers exactly. Patch = split the WORD (`pre-first-print` / `post-last-row`), name the constant, make the paper print the tape's last row; the teacher knows the old word so history is never retroactively a violation | `tools/gates_replay.py` + `tools/gates_report.py` + `test_factory2.py` (`nov17_factory2_code_day10_eyes2d.patch`) | **CLOSED `2026-11-17`, owner's `data/eyes2d_20261117.txt`: APPLIES-CLEAN → stamps `.2026-11-17.2`; 11-06 re-sailed on their tape = `cycles fed 124 (skipped 84: post-last-row 65, warm-up 19)`, `grid 208 = 124 + 84`, `stale:0`, `pre-first-print:0`; `NIGHT GRADE 7 PASS / 0 WARN / 0 FAIL`; `55 passed in 176.05s`; live mirror `2026-11-17_00:58:04` before AND after; `verdicts == episodes 8/8` unchanged from step 1. OPEN, deliberately: TODO 3-12 |
| 3 | DOORMAN tally — **MEASURED ✅ on the owner's receipt; the DIARY passed, the PAPER did not, and the cause is OUR code** | `NO-VERDICT + NO-EDGE + allowed = cycles`, every day, **asserted** not read: `committee.py:398-410` makes `fails` a LIST (a cycle can carry `FRESH-EYES` + `DECIDE-AT-THE-CLOSE`), while "allowed" exists only as `tickets` (`gates_replay.py:1048-1049`) — so the equation can lie three ways and all three are now counted | read-only measure first, then a patch to `tools/gates_replay.py` + the report | **MEASURE CUT `2026-11-17`: `nov17_doorman_tally_block.sh` (167 L, sha `b22eb17a5f36a005`), no dials, no re-sail; rehearsed PASS on a hand-computed fixture (`156 = 0 + 156`) and flinch-tested on 3 mutated diaries. RUN ✅ `2026-11-17 02:34Z` (`uploads/doorman_20261117.txt`, 81 L, diaries=6 papers=168): the diary is honest on ALL 1,815 cycles (`0 | 0 | 0` on all three lies) and EVERY paper printed `DOES NOT ADD UP` — because `gates_replay.py:1027` filed `(allowed)` INSIDE the refusal tally. **TALLY-2 shipped as the patch `nov17_factory2_code_day11_tally2.patch` (261 L / 17,698 B / sha `4c6247b0227309cd`, stamps → `…11-17.3`, `56 passed` on a rebuilt tree + 4 mutations caught).** 3b CLOSED by the owner (their own paste): applied clean (`check rc=0`, `apply rc=0`, `150 insertions/13 deletions`, both stamps `.3`) and **`56 passed in 142.60s`**. 3c DELIVERED: `nov17_tally2_resail_block.sh` (284 L / 16,200 B / sha `f8b1e4d80eca651d`) — dials read from YOUR paper, new file names only, mirror hashed; rehearsed PASS on a real 261-cycle sail and made to flinch 4 ways (incl. a SELF-consistent lying paper, caught only by the new cross-check). OPEN: [you] run it -> the verdict must read `ROW 3 ASSERTED on fresh paper`; then row 3 closes and the papers from 11-06 stop saying `DOES NOT ADD UP` (law 55). My block's own `THE DIARY DOES NOT AGREE WITH ITSELF` label was MY scope error (a `replay:summary` is PER-RUN, 5 of 6 days flagged wrongly) — repaired in the same block, law 53 + law 12 |
| **3d** | PUSH THE STATE TO GITHUB (owner's request, `2026-11-17`) | **8 explicit paths**, no wildcards: `committee.py` (M), `factory2_gates.py`, `test_factory2.py`, `tools/gates_helpers.py`, `tools/gates_replay.py`, `tools/gates_report.py`, `report_evening_gates.sh`, `tools/v8f_payoff.py` - the import closure (law 60) PLUS the entry points someone is told to run (law 63), minus the 31 files their tip already tracks. Their `git add -An` proved why no wildcard: it would have staged `_attic_2026-10-17/`, a `.jar`, `Workspace/*.bmw`, six old patches and `daily_audit_1111.txt` (law 62). The Book goes SECOND: their `HOLY_BOOK.md` is ` M` with +224/-1 of their own writing on a 3,113-line base against my 4,372, so I merge their hunks - `cp` would have buried them (law 61 family). `data/*` is `.gitignore`d, so 8.17 GB of tapes/receipts can never ride along; the map/TODO don't exist in their repo at all, so they arrive in commit 2 | explicit `git add --`, `git diff --cached --name-only`, `git commit`, `git push -u origin strategy-v2` | **CODE PUBLISHED ✅ `f70728d` (8 files, byte-identical to my measured tree) · PAGES MERGED ✅ (Book 4,490 L: their 224 lines and my 1,355 in one file, 0 lost either way; their laws are now 66/67/68, laws 59/63 backfilled, law 69 is the merge rule itself) — STILL OPEN: commit 2 = the 3 pages they install from my delivery, three explicit paths and never `git add .`; then 3c**
| 4 | MAP / rung band | the ±$80 law: lines counted in and out, and `--rung-band 0` = whole map | report + test | queued |
| 5 | L3 / ORDER FEED | order rows consumed vs `Mbo` fake-id rows ignored; a day with no tape must say L3 was BLIND | report | queued |
| 6 | LEDGER / committee | defs count, the 55 tests (was 54 before Room A's belt work; step 2d added two scenarios INSIDE an existing test, so the count moved once, not twice), and the `REPLAY/` arming boundary (live path byte-identical) | test | queued |
| 7 | BUDGET (the owner's caps) | `CPU_PCT_BUDGET = 0.3` measured on a fat day, `MAX_LINE_BYTES = 1100` on real lines | report | queued |
| 8 | STAMPS / provenance | every file in a run carries one stamp; unknown stamp ⇒ WARN; a receipt can be re-derivable from paper alone | report | queued |
| 9 | REFUSALS are loud | no silent skip anywhere: unknown flags, missing folder, thin tape all name themselves | replay + test | queued |
| 10 | **THE FACTORY RUNS** | a fresh raw day (the newest in `data/archive`) end-to-end, receipt green, 11 gates read | no code — the whole list proves itself | final |
| **11** | **THE MISSING TAIL** | the chain currently ENDS at the ticket. No order, no position, no exit, no realised P&L — verified: `grep -ci "stop_loss\|take_profit\|SL\|TP\|chandelier\|trail"` = **0 in all four factory files**. Steps 11-14 (order → open → manage → close) are UNBUILT by design (mindmap 3.1: "no executor van"), and Law #1 + the ROPE CHAPTER + the open STEP 2g audit already fix their SHAPE. | a decision before any code | **OPENED `2026-11-14`, deliberately not scheduled** |
| 12 | ROOM A, LIVE PANE (narrowed) | **`2026-11-14` finding: the parser is ALREADY the old factory's** — `gates_replay.py:765-790` imports `bookmap_bridge_provider`, makes a thread-free `_BridgeTail` via `__new__` and feeds tape rows to `tail._handle_line` (`:881`), exactly as `raw_replay.py:2186-2293` and `backtest.py:11` do. So this step is NOT a parser: it is the file plumbing — `--from-raw-tail` tailing a growing `ticks.csv` with the bridge's three habits (`f.seek(live_offset)` `:630`, `st_mtime` skip `:527-540`, file-identity rotation `:678-692`). Plus a parity call: borrow `raw_replay._discover_raw_files` (`:165`)/`discover_available_days`(`:271`)/`_iter_filtered_csv_events`(`:511`) instead of our own glob at `:67-116` (one copy Room A did not need to keep — decide after step 2) | `tools/gates_replay.py` (patch) | **OPENED, scope narrowed `2026-11-14`** — after steps 1b → 2 || 13 | WAKE THE LAW IN LIVE? | `grep -rn "REPLAY/"` has ONE writer (`tools/gates_replay.py:973`), so v8f's acceptance flip is ASLEEP in the hall by design — a live day is judged by the OLDER rules. Decision (not code): arm it in `factory2_gates.py`, or keep live conservative and say so in every report | `factory2_gates.py` **only if you sign it** | **OPENED `2026-11-14`** — must be settled before Room D's numbers mean anything live |

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
