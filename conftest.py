"""
conftest.py
===========
Automatic pytest sandbox isolation so running `pytest` on a trading machine
can NEVER create or mutate live state/log files inside `./data` or `./logs`
(e.g. `trade_guard_state.json`, `ai_calls.json`, `pm_state.json`,
`session_levels.json`, `trade_memory.json`, `tca_log.csv`).

Individual tests may still override `config.DATA_DIR`, `trade_guard.STATE_FILE`,
etc. via their own `monkeypatch` calls inside the test body.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

import config

_LIVE_REPO_ROOT = Path(__file__).resolve().parent
_LIVE_DATA_DIR = (_LIVE_REPO_ROOT / "data").resolve()
_LIVE_LOGS_DIR = (_LIVE_REPO_ROOT / "logs").resolve()


def _points_into_live_dir(val, live_dir: Path) -> bool:
    if val is None:
        return False
    try:
        p = Path(val).resolve()
        return p == live_dir or live_dir in p.parents
    except Exception:
        return False


@pytest.fixture(autouse=True)
def _isolate_live_data_and_logs(tmp_path, monkeypatch):
    iso_data = tmp_path / "_pytest_isolated_data"
    iso_logs = tmp_path / "_pytest_isolated_logs"
    iso_data.mkdir(parents=True, exist_ok=True)
    iso_logs.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(config, "DATA_DIR", iso_data)
    monkeypatch.setattr(config, "LOGS_DIR", iso_logs)
    monkeypatch.setattr(config, "CME_DAILY_BREAK_AUTO", False, raising=False)

    main_mod = sys.modules.get("main")
    if main_mod is not None and hasattr(main_mod, "_CACHED_MT5_EXECUTOR"):
        monkeypatch.setattr(main_mod, "_CACHED_MT5_EXECUTOR", None, raising=False)

    # Redirect any already-imported module bindings that do NOT dynamically
    # resolve config.DATA_DIR on every call (e.g. main / risk_manager constants).
    # Modules with dynamic _resolve_state_file() (trade_guard, maintenance,
    # position_manager, trade_history, step3_ai_decision) follow config.DATA_DIR
    # automatically so per-test config.DATA_DIR overrides continue to work.
    for mod_name in ("main", "risk_manager"):
        mod = sys.modules.get(mod_name)
        if mod is None:
            continue
        if _points_into_live_dir(getattr(mod, "DATA_DIR", None), _LIVE_DATA_DIR):
            monkeypatch.setattr(mod, "DATA_DIR", iso_data, raising=False)
        if _points_into_live_dir(getattr(mod, "LOGS_DIR", None), _LIVE_LOGS_DIR):
            monkeypatch.setattr(mod, "LOGS_DIR", iso_logs, raising=False)
        for attr in (
            "STATE_FILE", "_OUTCOME_KEYS_FILE", "_POSITION_IDS_FILE",
            "_PLUMBING_DEALS_FILE", "POSITION_IDS_FILE", "PLUMBING_DEALS_FILE",
        ):
            cur = getattr(mod, attr, None)
            if _points_into_live_dir(cur, _LIVE_DATA_DIR):
                monkeypatch.setattr(mod, attr, iso_data / Path(cur).name, raising=False)
            elif _points_into_live_dir(cur, _LIVE_LOGS_DIR):
                monkeypatch.setattr(mod, attr, iso_logs / Path(cur).name, raising=False)
    yield
