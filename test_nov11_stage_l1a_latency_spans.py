"""Stage L1a -- the invisible part of the cycle is now measured.

`audit_day.py` TEST 12 has failed on every audited day (`median ~2,069 ms`
against a `500 ms` target, `0/910` cycles under it) while only `STEP1`,
`STEP2` and `STEP3` were timed. `~1.1 s` of every cycle had no name, so no
patch could be aimed at it.

L1a adds ten spans around the untimed blocks of `run_pipeline()` (measurement
only, gated by the existing `LATENCY_LOG_ENABLED`) and `tools/latency_spans.py`
rebuilds a cycle from them. These tests pin both halves so a future refactor
cannot silently drop the measurement again.
"""

import importlib.util
from pathlib import Path

MAIN = Path(__file__).resolve().parent / "main.py"
SPANS_TOOL = Path(__file__).resolve().parent / "tools" / "latency_spans.py"

L1B_SPANS = [
    "GATE recheck + quality evidence",
    "RL size/pause check",
    "STEP4a position management",
    "EXEC-TIME feed recheck",
]

NEW_SPANS = [
    "PREFLIGHT env+idharvest+lock+maint",
    "STEP4 execute",
    "STEP5 monitor",
    "CLOSED-DEAL log (MT5 history)",
    "SIGNAL bridge + persist",
    "DECISION journal write",
    "SUMMARY blocks",
]


def _main_src() -> str:
    return MAIN.read_text(encoding="utf-8")


def test_every_new_span_is_emitted():
    src = _main_src()
    for span in NEW_SPANS:
        assert f'_record_latency("{span}"' in src, span


def test_spans_are_ordered_the_way_the_pipeline_runs():
    src = _main_src()
    body = src[src.index("def run_pipeline("):]
    positions = [body.index(f'_record_latency("{span}"') for span in NEW_SPANS]
    assert positions == sorted(positions), "span order no longer follows the pipeline"


def test_preflight_span_sits_before_step1_and_covers_the_harvest():
    src = _main_src()
    body = src[src.index("def run_pipeline("):]
    pre = body.index('_record_latency("PREFLIGHT')
    step1 = body.index('_record_latency("STEP1 acquire"')
    harvest = body.index("_load_tracked_position_ids()")
    assert harvest < pre < step1


def test_measurement_is_gated_by_the_existing_switch():
    src = _main_src()
    assert 'if not getattr(config, "LATENCY_LOG_ENABLED", True):' in src
    # L1a must not introduce a second, independent switch
    assert "L1A_ENABLED" not in src


def test_total_pipeline_is_still_the_authority():
    src = _main_src()
    assert '_record_latency("TOTAL pipeline", total_ms)' in src


def _load_tool():
    spec = importlib.util.spec_from_file_location("latency_spans", SPANS_TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_log(tmp_path, cycles):
    p = tmp_path / "trading_20261111.log"
    lines = []
    for idx, spans in enumerate(cycles):
        stamp = f"2026-11-11 10:{idx:02d}:00,000"
        for name, ms in spans.items():
            lines.append(f"{stamp} INFO    [main] LATENCY {name} {ms}ms")
        if "TOTAL pipeline" not in spans:
            total = sum(spans.values())
            lines.append(f"{stamp} INFO    [main] LATENCY TOTAL pipeline {total}ms target 500ms")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_analyzer_rebuilds_a_cycle_and_finds_the_leftover(tmp_path):
    tool = _load_tool()
    spans = {
        "PREFLIGHT env+idharvest+lock+maint": 600,
        "STEP1 acquire": 280,
        "STEP2 analyze": 660,
        "STEP3 AI": 2,
        "STEP4 execute": 40,
        "STEP5 monitor": 300,
        "CLOSED-DEAL log (MT5 history)": 500,
        "SIGNAL bridge + persist": 20,
        "DECISION journal write": 15,
        "SUMMARY blocks": 120,
        "TOTAL pipeline": 2660,   # 123 ms of nothing named
    }
    path = _fake_log(tmp_path, [spans])
    cycles = tool.parse_cycles(path)
    assert len(cycles) == 1
    text = tool.report(cycles, top=1)
    assert "LEFT OVER (unmeasured)" in text
    assert "123" in text.split("LEFT OVER")[1].splitlines()[0]


def test_analyzer_ignores_feed_age_lines_and_survives_empty_days(tmp_path):
    tool = _load_tool()
    p = tmp_path / "trading_20261111.log"
    p.write_text(
        "2026-11-11 10:00:00,000 INFO [main] LATENCY feed age at acquisition 0.573s 573ms\n"
        "2026-11-11 10:00:00,000 INFO [main] LATENCY TOTAL pipeline 900ms target 500ms\n",
        encoding="utf-8")
    cycles = tool.parse_cycles(p)
    assert len(cycles) == 1
    assert "feed age" not in str(cycles)
    empty = tmp_path / "empty.log"
    empty.write_text("", encoding="utf-8")
    assert "no complete cycles" in tool.report(tool.parse_cycles(empty), top=1)


# ---------------------------- Stage L1b ----------------------------------- #
# L1a left 523 ms of a 1,580 ms cycle completely unmeasured (the block between
# the STEP3 line and STEP4). L1b names it.

def test_l1b_spans_are_emitted():
    src = _main_src()
    for span in L1B_SPANS:
        assert f'_record_latency("{span}"' in src, span


def test_l1b_spans_sit_between_step3_and_step4():
    src = _main_src()
    body = src[src.index("def run_pipeline("):]
    step3 = body.index('_record_latency("STEP3 AI"')
    step4 = body.index('_record_latency("STEP4 execute"')
    for span in L1B_SPANS:
        pos = body.index(f'_record_latency("{span}"')
        assert step3 < pos < step4, span
    ordered = [body.index(f'_record_latency("{s}"') for s in L1B_SPANS]
    assert ordered == sorted(ordered), "L1b span order no longer follows the pipeline"


def test_l1b_gate_span_covers_the_safety_recheck_and_evidence():
    src = _main_src()
    body = src[src.index("def run_pipeline("):]
    gate = body.index('_record_latency("GATE recheck + quality evidence"')
    assert body.index("_safety_gates(data, _feed_elapsed)") < gate
    assert body.index("_attach_quality_evidence_to_shadow_candidates(snapshot)") < gate


def test_analyzer_reports_l1b_spans_in_order(tmp_path):
    tool = _load_tool()
    spans = {
        "PREFLIGHT env+idharvest+lock+maint": 353,
        "STEP1 acquire": 198,
        "STEP2 analyze": 405,
        "STEP3 AI": 1,
        "GATE recheck + quality evidence": 300,
        "RL size/pause check": 120,
        "STEP4a position management": 60,
        "EXEC-TIME feed recheck": 38,
        "STEP4 execute": 1,
        "TOTAL pipeline": 1480,
    }
    path = _fake_log(tmp_path, [spans])
    text = tool.report(tool.parse_cycles(path), top=1)
    order = [text.index(s) for s in tool.STAGE_ORDER if s in text]
    assert order == sorted(order)
    assert "LEFT OVER (unmeasured)" in text
