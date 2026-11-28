#!/usr/bin/env python3
"""mutate_hands - law 52 harness: disarm each guard, each borrow, and watch for the flinch.

Builds a scratch copy of the ROOM E tree (symlinked dirs, real copies of the files it mutates),
applies ONE mutation, runs ONLY test_factory2_hands.py, and reports which tests failed. A mutation
that changes NOTHING means the test was a decoration. Three of the mutations are inside the
BORROWED organ - they prove this room really calls the bridge rather than imitating it.
"""
import os, shutil, subprocess, sys, tempfile, pathlib

# The tree to arm: this file's own folder. An absolute path of the author's box is a handover
# bug - the owner's run died on `FileNotFoundError: \\home\\user\\f2_build` (`2026-11-23`).
# Overridable with SRC=/path/to/repo for a rehearsal in a scratch worktree.
SRC = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else
                 __import__("os").environ.get("SRC", str(pathlib.Path(__file__).resolve().parent)))

MUT = [
    ("M1  a fake SL instead of the bridge's geometry", "tools/factory2_hands.py",
     '"entry": price, "target": float(tp), "stop": float(sl),',
     '"entry": price, "target": float(tp), "stop": float(price - 100.0),',
     "test_the_real_door_hands_us_a_ticket_and_the_plan_is_analysis_only"),
    ("M2  the stage guard off (a LIVE plan could pass)", "tools/factory2_hands.py",
     'if plan.get("execution_status") != "PLAN_ONLY":', 'if False:',
     "test_a_forked_plan_is_refused_before_the_fill"),
    ("M4  turn the hall lever ON", "tools/factory2_hands.py",
     'HALL_ARMED = False', 'HALL_ARMED = True',
     "test_the_hall_lever_has_no_on_switch_in_this_file"),
    ("M5  build a plan with no book to write in", "tools/factory2_hands.py",
     'if pen is None or not getattr(pen, "callable_ok", callable(pen)):', 'if False:',
     "test_law_6_applies_here_too_no_diary_no_position"),
    ("M6  default the equity instead of refusing", "tools/factory2_hands.py",
     'if eq <= 0:\n        raise RuntimeError("no equity on the cycle")',
     'eq = eq if eq > 0 else 10000.0',
     "test_no_equity_means_BLIND_sizing_not_a_default"),
    ("M7  move the TP in manage (unfreeze the goal post)", "tools/factory2_hands.py",
     'out = {"close_rule": "", "sl": float(pos["sl"]), "tp": float(pos["tp"]), "events": []}',
     'out = {"close_rule": "", "sl": float(pos["sl"]), "tp": float(pos["tp"]) + 1.0, "events": []}',
     "test_tp_is_frozen_even_at_break_even"),
    ("M8  drop the time stop", "tools/factory2_hands.py",
     'if held_min >= ts_min and r_now < 1.0:', 'if False:',
     "test_a_stalled_trade_times_out_on_the_bridges_own_dial"),
    ("M9  let an unmarked symbol through", "tools/factory2_hands.py",
     'if not str(plan["symbol"]).startswith(("REPLAY/", "SHADOW/")):', 'if False:',
     "test_an_unmarked_symbol_is_forced_into_the_shadow_prefix"),
    ("M10 a blind bridge becomes a quiet zero", "tools/factory2_hands.py",
     'return {"pm": None, "pes": None, "ex": None, "config": None,\n                "error": f"{type(exc).__name__}: {exc}"}',
     'return {"pm": None, "pes": None, "ex": None, "config": None, "error": ""}',
     "test_no_bridge_means_BLIND_not_a_quiet_zero"),
    ("M11 stage returns the caller's dict (a fill mutates the plan)", "tools/factory2_hands.py",
     '    staged = copy.deepcopy(plan)', '    staged = plan',
     "test_stage_does_not_mutate_the_plan"),
    ("M12 BORROWED organ: drop ALREADY_FILLED", "paper_entry_simulator.py",
     'if _LAST_FILLED_KEY == desired_key:', 'if False:',
     "test_opening_the_same_plan_twice_is_not_two_positions"),
    ("M13 BORROWED organ: drop the drift revalidation", "paper_entry_simulator.py",
     'if abs(current - reference) > max_drift:', 'if False:',
     "test_a_moving_market_cancels_a_stale_plan"),
    ("M14 BORROWED organ: accept a non-PLAN_ONLY plan", "paper_entry_simulator.py",
     'if plan.get("execution_status") != "PLAN_ONLY":', 'if False:',
     "test_the_fill_really_goes_through_the_borrowed_organ"),
    # 12c: the WIRING is only as safe as its guard, so the guard gets disarmed like any other lock.
    ("M15 the OFF guard always passes (Room E on every live minute)", "factory2_gates.py",
     'if shadow is not None:', 'if True:',
     "test_the_hall_never_imports_the_hands_until_someone_hands_it_a_shadow_dict"),
    ("M16 a broken hand is swallowed instead of named", "factory2_gates.py",
     '            except Exception as exc:                # law 8 AND law 83: never kill a heartbeat,',
     '            except ZeroDivisionError as exc:        # wrong-class: the hands error escapes',
     "test_a_broken_hand_is_named_in_the_book_rather_than_swallowed"),
]
MUT = [m for m in MUT if m[2] and m[4]]


def build(dst: pathlib.Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)      # NOT created by the symlinks: on a box without the
    (dst / "tools").mkdir(exist_ok=True)        # symlink privilege there ARE no symlinks (law 85)
    for e in sorted(SRC.iterdir()):
        if e.name in (".git", "__pycache__") or e.name.endswith(".pyc"):
            continue
        d = dst / e.name
        skip = ("tools", "test_factory2_hands.py", "paper_entry_simulator.py", "mutate_hands.py")
        if e.name in skip:
            continue
        if not e.name.endswith(".py") or e.name == "data":
            continue          # only modules matter; a symlinked data/ could let a test read tapes
        try:                  # a box without the symlink privilege (Windows, no dev mode) lands
            d.symlink_to(e)  # in the except: copy the .py, never die. Found by SIMULATING that
        except OSError:      # error on my own box - the owner's box is exactly that shape
            shutil.copy(e, d)
    for name in sorted((SRC / "tools").iterdir()):
        if name.name.endswith(".py") and name.name != "mutate_hands.py":
            shutil.copy(name, dst / "tools" / name.name)
    for f in ("test_factory2_hands.py", "paper_entry_simulator.py"):
        shutil.copy(SRC / f, dst / f)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):     # belt: never die on an unencodable glyph,
        try:                                     # whatever the console code page is
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    no_flinch = []
    for label, target, find, repl, expect in MUT:
        with tempfile.TemporaryDirectory(prefix="mut") as td:
            dst = pathlib.Path(td) / "repo"
            dst.mkdir()
            build(dst)
            f = dst / target
            if f.is_symlink():
                f.unlink()
            shutil.copy(SRC / target, f)
            s = f.read_text(encoding="utf-8")
            if s.count(find) != 1:
                print(f"STALE ANCHOR {label}: found {s.count(find)} copies in {target}")
                no_flinch.append(label + " (stale anchor)")
                continue
            f.write_text(s.replace(find, repl, 1), encoding="utf-8")
            child = dict(os.environ, PYTHONIOENCODING="utf-8")   # LAW 86, the other half: the CHILD
            r = subprocess.run([sys.executable, "-m", "pytest", "test_factory2_hands.py", "-q",  # writes the
                                "--no-header", "-p", "no:cacheprovider"],                           # test file's
                               cwd=str(dst), capture_output=True, text=True, timeout=600,          # em-dashes, and
                               env=child, errors="replace")                                          # a cp1252 parent
            #                                                                                          # would crash here
            #  (found by running THIS harness under PYTHONIOENCODING=cp1252, i.e. on the owner's console)
            failed = [l for l in r.stdout.splitlines() if l.startswith("FAILED")]
            summary = [l for l in r.stdout.splitlines() if " passed" in l or " failed" in l][-1:]
            flinch = any(expect in x for x in failed)
            # ASCII ONLY in what we print: on a Windows console on the default code page
            # (charmap/cp1252) "\u2713" and "\u2717" are NOT ENCODABLE, so the harness CRASHED
            # for the owner AFTER his 22 passed - the guard worked, the printing died. LAW 85:
            # the console is part of the box, and I had not shipped it.
            mark = "FLINCH" if flinch else "NO FLINCH"
            line = summary[0] if summary else r.stderr[:80].replace("\n", " ")
            print(f"{label}\n    expected flinch in {expect[:46]}\n"
                  f"    {mark} failed={len(failed)} | {line}")
            if not flinch:
                no_flinch.append(label)
    print("\nmutations that did NOT flinch:", len(no_flinch), no_flinch)
    return 1 if no_flinch else 0


if __name__ == "__main__":
    sys.exit(main())
