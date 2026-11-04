"""Check registry — the plugin seam for future checks.

Each entry is a callable returning a Verdict. Adding a new check is:
  1. write a module with a run_*_check() function,
  2. register it here (and in the CLI's `check` command),
  3. add a fixture mode for it, if it needs one.
No core file has to change its shape to accept a new check.
"""

from __future__ import annotations

from .burst import run_burst_check
from .error_shape import run_error_shape_check
from .pagination import run_pagination_check

CHECKS = ("burst", "pagination", "error-shape")

__all__ = [
    "CHECKS",
    "run_burst_check",
    "run_error_shape_check",
    "run_pagination_check",
]
