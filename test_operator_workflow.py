"""Operator CLI checks: doctor is non-trading; explicit no-new-entry mode routes safely."""
from __future__ import annotations

import sys
import types

import main


def test_doctor_cli_returns_before_lock_logging_or_pipeline(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["main.py", "--doctor"])
    monkeypatch.setattr(main, "setup_logging",
                        lambda: (_ for _ in ()).throw(AssertionError("logging started")))
    monkeypatch.setattr(main, "_acquire_lock",
                        lambda: (_ for _ in ()).throw(AssertionError("lock acquired")))
    monkeypatch.setattr(main, "_write_run_config",
                        lambda: (_ for _ in ()).throw(AssertionError("config written")))
    monkeypatch.setattr(main, "run_pipeline",
                        lambda **_kwargs: (_ for _ in ()).throw(
                            AssertionError("pipeline started")))

    assert main.main() in (0, 1)
    output = capsys.readouterr().out
    assert "LOCAL READINESS CHECK" in output
    assert "no AI request, MT5 connection, live-feed check, or order" in output
    assert "actual MT5 account type not checked" in output
    assert "GEMINI_API_KEY" not in output


def test_doctor_rejects_runtime_mode_flags(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["main.py", "--doctor", "--loop"])
    try:
        main.main()
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("--doctor --loop should be rejected")


def test_no_new_entries_cli_passes_mode_to_single_cycle(monkeypatch):
    captured = {}
    monkeypatch.setattr(sys, "argv", ["main.py", "--no-new-entries"])
    monkeypatch.setattr(main, "setup_logging", lambda: None)
    monkeypatch.setattr(main.config, "credentials_configured", lambda: {})
    monkeypatch.setattr(main, "_acquire_lock", lambda: True)
    monkeypatch.setattr(main, "_write_run_config", lambda: None)
    monkeypatch.setattr(main, "_log_session_start",
                        lambda mode: captured.setdefault("session_mode", mode))
    monkeypatch.setattr(main, "_session_end_summary", lambda: None)
    monkeypatch.setattr(main, "_release_lock", lambda: None)
    monkeypatch.setattr(main, "run_pipeline",
                        lambda *, allow_new_entries=True:
                        captured.setdefault("allow_new_entries", allow_new_entries))

    assert main.main() == 0
    assert captured["allow_new_entries"] is False
    assert "no-new-entries" in captured["session_mode"]


def test_pipeline_summary_adds_one_short_entry_status_line(monkeypatch, capsys):
    monkeypatch.setattr(main, "STATUS", [
        ("ENTRY MODE", "NO NEW ENTRIES; position management/monitoring continue"),
        ("STEP 4  EXECUTION", "SKIPPED — new-entry authorization blocked: stale quote"),
    ])

    main.print_summary()

    output = capsys.readouterr().out
    assert "QUICK STATUS:" in output
    assert "NO NEW ENTRIES" in output
    assert "stale quote" in output


def test_no_new_entries_loop_keeps_mode_for_each_cycle(monkeypatch):
    captured = []
    monkeypatch.setattr(sys, "argv", ["main.py", "--no-new-entries", "--loop"])
    monkeypatch.setattr(main, "setup_logging", lambda: None)
    monkeypatch.setattr(main.config, "credentials_configured", lambda: {})
    monkeypatch.setattr(main, "_acquire_lock", lambda: True)
    monkeypatch.setattr(main, "_write_run_config", lambda: None)
    monkeypatch.setattr(main, "_log_session_start", lambda _mode: None)
    monkeypatch.setattr(main, "_session_end_summary", lambda: None)
    monkeypatch.setattr(main, "_release_lock", lambda: None)
    monkeypatch.setattr(main, "run_pipeline",
                        lambda *, allow_new_entries=True:
                        captured.append(allow_new_entries))

    monitor = types.ModuleType("step5_monitoring")
    monitor.TradeMonitor = lambda: types.SimpleNamespace(
        run_loop=lambda callback: callback())
    monkeypatch.setitem(sys.modules, "step5_monitoring", monitor)

    assert main.main() == 0
    assert captured == [False]
