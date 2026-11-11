"""Stage L1c -- the journal harvest stops re-reading 25 MB, and the last two
opaque blocks get named children.

Measured on this rig (`2026-11-11`) with a real-shaped 33-column journal of
137,970 rows / 24.0 MB:

    OLD  full `csv.DictReader` parse every cycle ....... 559 ms
    NEW  first cycle (full scan) ...................... 418 ms
    NEW  steady state (only the appended tail) ....... 0.09 ms

The saving is only honest if the answer is *identical*, so these tests pin the
equivalence first (multi-line rationales, CRLF, unknown schema, trimmed and
replaced files, half-written last row) and the span bookkeeping second (a child
span must never be added to `NAMED` or `LEFT OVER` would go negative).
"""

import csv
import importlib.util
import os
from pathlib import Path

import main

MAIN = Path(__file__).resolve().parent / "main.py"
SPANS_TOOL = Path(__file__).resolve().parent / "tools" / "latency_spans.py"

FIELDS = list(main._DECISION_LOG_FIELDS)

L1C_SPANS = [
    "PREFLIGHT env reload",
    "PREFLIGHT id harvest",
    "PREFLIGHT lock+maint",
    "GATE session+switch check",
    "GATE feed recheck",
    "GATE risk check (MT5)",
    "GATE RL loss-streak check",
    "GATE trade guard",
    "GATE quality evidence",
    "EXEC-TIME data recheck",
    "EXEC-TIME quality evidence",
]


def _journal(tmp_path, rows=()):
    path = tmp_path / "decisions_log.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            full = {key: "" for key in FIELDS}
            full.update(row)
            writer.writerow(full)
    return path


def _append(path, rows):
    with open(path, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        for row in rows:
            full = {key: "" for key in FIELDS}
            full.update(row)
            writer.writerow(full)


def _old_full_parse(path) -> set:
    """The pre-L1c implementation, kept here as the reference oracle."""
    ids = set()
    with open(path, "r", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if (row.get("exec_status") or "").strip() != "EXECUTED":
                continue
            number = main._as_position_id(row.get("order_id"))
            if number:
                ids.add(number)
    return ids


def _reset_cache():
    main._JOURNAL_TAIL.update(path="", inode=None, header=b"", offset=0,
                              anchor=b"", ids=frozenset())


TRICKY_ROWS = [
    {"exec_status": "EXECUTED", "order_id": "111", "ai_rationale": "plain"},
    {"exec_status": "SKIPPED", "order_id": "",
     "ai_rationale": "line1\nline2 inside the rationale"},
    {"exec_status": "EXECUTED", "order_id": "222",
     "ai_rationale": "multi\nline, with comma and \"quotes\""},
    {"exec_status": " skipped ", "order_id": "333", "ai_rationale": ""},
    {"exec_status": "EXECUTED", "order_id": "not-a-number", "ai_rationale": ""},
    {"exec_status": "EXECUTED", "order_id": "444", "ai_rationale": "sep \u2028 here"},
    {"exec_status": "EXECUTED", "order_id": "555", "ai_rationale": ""},
]


def test_tail_read_matches_the_full_parse_on_a_hostile_journal(tmp_path):
    _reset_cache()
    path = _journal(tmp_path, TRICKY_ROWS)
    assert main._journal_executed_order_ids(path) == _old_full_parse(path)
    assert main._journal_executed_order_ids(path) == {111, 222, 444, 555}


def test_appended_rows_are_picked_up_without_a_rescan(tmp_path):
    _reset_cache()
    path = _journal(tmp_path, TRICKY_ROWS)
    first = main._journal_executed_order_ids(path)
    _append(path, [{"exec_status": "EXECUTED", "order_id": "777"}])
    second = main._journal_executed_order_ids(path)
    assert second == first | {777} == _old_full_parse(path)


def test_a_trimmed_journal_is_rescanned(tmp_path):
    _reset_cache()
    path = _journal(tmp_path, TRICKY_ROWS)
    main._journal_executed_order_ids(path)
    kept = path.read_text(encoding="utf-8").splitlines(True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(kept[:1] + kept[-2:]), encoding="utf-8", newline="")
    os.replace(tmp, path)                      # maintenance trims this way
    assert main._journal_executed_order_ids(path) == _old_full_parse(path)


def test_a_replaced_journal_is_detected_by_its_own_inode(tmp_path):
    _reset_cache()
    path = _journal(tmp_path, TRICKY_ROWS)
    main._journal_executed_order_ids(path)
    # A schema migration writes a temporary file and os.replace()s it in, so the
    # inode changes even when the byte length is identical.
    tmp = path.with_suffix(".tmp")
    text = path.read_text(encoding="utf-8").replace("111", "999")
    tmp.write_text(text, encoding="utf-8", newline="")
    os.replace(tmp, path)
    assert main._journal_executed_order_ids(path) == _old_full_parse(path)


def test_an_in_place_shrink_is_detected_by_the_offset_guard(tmp_path):
    _reset_cache()
    path = _journal(tmp_path, TRICKY_ROWS)
    main._journal_executed_order_ids(path)
    kept = path.read_text(encoding="utf-8").splitlines(True)
    path.write_text("".join(kept[:1] + kept[-1:]), encoding="utf-8", newline="")
    assert main._journal_executed_order_ids(path) == _old_full_parse(path)


def test_the_journal_is_only_ever_appended_or_rewritten_wholesale():
    """The one situation an offset reader cannot see (an in-place edit *behind*
    the offset that leaves the file at least as long) cannot occur in this app:
    the writer appends, and both maintenance and migration replace or shrink."""
    main_src = MAIN.read_text(encoding="utf-8")
    log_decision = main_src[main_src.index("def log_decision("):]
    assert 'open(path, "a", newline="", encoding="utf-8")' in log_decision
    assert "os.replace(temporary, path)" in main_src
    maintenance_src = (MAIN.parent / "maintenance.py").read_text(encoding="utf-8")
    trim = maintenance_src[maintenance_src.index("def _trim_csv("):]
    assert "keep = rows[-max_rows:]" in trim
    assert 'path.write_text("\\n".join([header] + keep)' in trim


def test_a_half_written_last_row_is_not_counted_until_it_is_complete(tmp_path):
    _reset_cache()
    path = _journal(tmp_path, [{"exec_status": "EXECUTED", "order_id": "111"}])
    assert main._journal_executed_order_ids(path) == {111}
    partial = f'2026-11-11T10:00:00+00:00,"EXECUTED","20'
    with open(path, "a", newline="", encoding="utf-8") as fh:
        fh.write(partial)                      # no newline yet
    assert main._journal_executed_order_ids(path) == {111}
    with open(path, "a", newline="", encoding="utf-8") as fh:
        fh.write('00"\n')                      # row completed
    assert main._journal_executed_order_ids(path) == _old_full_parse(path)


def test_a_journal_with_an_unknown_schema_returns_no_ids(tmp_path):
    _reset_cache()
    path = tmp_path / "decisions_log.csv"
    path.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    assert main._journal_executed_order_ids(path) == set()


def test_missing_journal_is_not_an_error(tmp_path):
    _reset_cache()
    assert main._journal_executed_order_ids(tmp_path / "nope.csv") == set()


def test_load_tracked_position_ids_still_excludes_plumbing_ids(tmp_path, monkeypatch):
    _reset_cache()
    path = _journal(tmp_path, TRICKY_ROWS)
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "_load_plumbing_test_position_ids", lambda: {222})
    monkeypatch.setattr(main, "_POSITION_IDS_FILE", tmp_path / "position_ids.json")
    ids = main._load_tracked_position_ids()
    assert ids == {111, 444, 555}
    assert ids == _old_full_parse(path) - {222}


def test_every_l1c_span_is_emitted():
    src = MAIN.read_text(encoding="utf-8")
    for span in L1C_SPANS:
        assert f'_record_latency("{span}"' in src, span


def test_children_are_timed_inside_their_parents():
    src = MAIN.read_text(encoding="utf-8")
    gate = src[src.index("def _safety_gates("):src.index("_DECISION_LOG_FIELDS")]
    # every gate child is recorded inside _safety_gates itself
    for span in L1C_SPANS[:3]:
        pass
    for span in ["GATE session+switch check", "GATE feed recheck",
                 "GATE risk check (MT5)", "GATE RL loss-streak check",
                 "GATE trade guard"]:
        assert f'_record_latency("{span}"' in gate, span
    body = src[src.index("def run_pipeline("):]
    for child, parent in [("PREFLIGHT env reload", 'PREFLIGHT env+idharvest+lock+maint'),
                          ("PREFLIGHT id harvest", 'PREFLIGHT env+idharvest+lock+maint'),
                          ("PREFLIGHT lock+maint", 'PREFLIGHT env+idharvest+lock+maint'),
                          ("GATE quality evidence", 'GATE recheck + quality evidence')]:
        assert body.index(f'_record_latency("{child}"') < \
            body.index(f'_record_latency("{parent}"'), child


def test_the_latency_log_is_cleared_before_the_first_child_span():
    src = MAIN.read_text(encoding="utf-8")
    body = src[src.index("def run_pipeline("):]
    assert body.index("_LATENCY_LOG.clear()") < \
        body.index('_record_latency("PREFLIGHT env reload"')


def _load_tool():
    spec = importlib.util.spec_from_file_location("latency_spans_l1c", SPANS_TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_analyzer_counts_a_child_span_once(tmp_path):
    tool = _load_tool()
    for child in L1C_SPANS:
        assert child in tool.STAGE_ORDER
        assert tool.CHILD_OF.get(child), child
    cycles = [("2026-11-11 10:00:00", {
        "PREFLIGHT env+idharvest+lock+maint": 200.0,
        "PREFLIGHT id harvest": 180.0,
        "STEP1 acquire": 100.0,
        "GATE recheck + quality evidence": 300.0,
        "GATE risk check (MT5)": 250.0,
        "TOTAL pipeline": 700.0,
    })]
    text = tool.report(cycles, 1)
    named = [line for line in text.splitlines() if line.startswith("NAMED")][0]
    leftover = [line for line in text.splitlines() if line.startswith("LEFT OVER")][0]
    # NAMED = 200 + 100 + 300 = 600 (children are inside those parents)
    assert "   600" in named
    assert "   100" in leftover
