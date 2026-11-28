"""Shadow copy for tests only - NOT the real finder.

WHY THIS FILE EXISTS (law 14). ``tools/gates_replay.available_days()`` opens with
``import audit_day`` and asks ``audit_day.all_ticks_files(None)`` for days. That finder resolves
``ROOT = Path(__file__).resolve().parent`` - the folder the finder itself sits in - so in a checkout
that has a populated ``data/`` it answers with the OWNER'S real tape days no matter what temp fixture
the test passed in. Measured 2026-11-24:

  owner's repo            -> 1 failed, 94 passed   (test_day_grammar_is_canonical_for_the_sea)
  this file + same tree   -> 103 passed           (hermetic again, nothing else changed)
  my recreation, no shadow-> 1 passed             (an EMPTY data/ hides the bug: the leak needs real tape)

It never breaks a real run either: the two test call sites wrap the import in ``try/except Exception``,
and the sail entry point sets ``scoped=True``, which never reaches this path at all.

It has NO behaviour: every entry point raises, so callers fall back to the folders they were handed.
"""

import sys
from pathlib import Path


class _NoFinder(RuntimeError):
    """Raised by every entry point: 'there is no ambient drawer here, use the path you were given'."""


def _refuse(*_a, **_k):
    raise _NoFinder("test shadow: ambient tape discovery is disabled; pass an explicit data dir")


def __getattr__(name):                                  # PEP 562: refuse ANY attribute, not just ours
    raise _NoFinder(f"test shadow: audit_day.{name} is not available here")


all_ticks_files = _refuse
find_ticks_file = _refuse
DATA_DIR = _refuse
ROOT = Path(__file__).resolve().parent                   # an empty drawer, on purpose
