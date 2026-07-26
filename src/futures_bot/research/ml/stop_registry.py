"""Cooperative training-stop flags.

Python threads can't be hard-killed, so a "Stop" click can't actually
interrupt a training loop from the outside. Instead, every training loop in
`training.py` (tree-by-chunk, round-by-round, epoch-by-epoch -- all already
structured incrementally so this works) checks `should_stop(model_id)`
between steps and raises `TrainingStopped` if it's been set. A small
in-process map is enough here: training jobs run in this same process's
job-executor threads (see `api/jobs.py`), never in a separate worker.
"""

from __future__ import annotations

import threading

_flags: dict[str, threading.Event] = {}
_lock = threading.Lock()


def register(model_id: str) -> None:
    with _lock:
        _flags[model_id] = threading.Event()


def request_stop(model_id: str) -> bool:
    """Returns True if a training run for this model_id is registered (and
    therefore stoppable), False if there's nothing to stop (already
    finished, or never started)."""
    with _lock:
        flag = _flags.get(model_id)
    if flag is None:
        return False
    flag.set()
    return True


def should_stop(model_id: str) -> bool:
    with _lock:
        flag = _flags.get(model_id)
    return flag.is_set() if flag is not None else False


def clear(model_id: str) -> None:
    with _lock:
        _flags.pop(model_id, None)
