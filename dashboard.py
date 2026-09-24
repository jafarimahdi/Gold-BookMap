"""Root twin of tools/dashboard.py.

`audit_day.py` also copies tools/dashboard.py next to itself if it is missing, so this
file is mostly a convenience: it means `import dashboard` works from the project root
(older copies, other tools, or a manual python session) without needing tools/ on the
path, and there is still exactly ONE real implementation, in tools/dashboard.py.
"""
import importlib.util as _ilu
import sys as _sys
from pathlib import Path as _P

_here = _P(__file__).resolve().parent
for _cand in (_here / "tools" / "dashboard.py", _here.parent / "tools" / "dashboard.py"):
    if _cand.exists():
        _spec = _ilu.spec_from_file_location("dashboard_impl", _cand)
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _sys.modules[__name__] = _mod
        globals().update({k: v for k, v in vars(_mod).items() if not k.startswith("__")})
        break
else:                                                      # tools/ missing -> degrade
    def _missing(*_a, **_k):
        raise RuntimeError("tools/dashboard.py is missing - re-copy the tools folder from the kit")
    _STYLE = _JS = ""
    day_dashboard = write_index = history_block = _missing
    del _here, _cand
