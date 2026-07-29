"""SIL Phase 4 "Intelligent Automation Layer" -- the "Automatic Work
Detection" piece: a background thread that periodically checks for
uncommitted changes (`git status --porcelain`, via `git_info.changed_files`)
not covered by any currently-active work item's `estimated_files`, and
files exactly one draft work item (`is_draft=True`) covering them.

Same hand-rolled daemon-thread shape `market_data/scheduler.py::MarketDataScheduler`
already established (`threading.Lock` + `threading.Event` for interruptible
sleep, a locked status snapshot) -- no new concurrency idiom.

Deliberately conservative:

- Never touches a non-draft work item -- only ever reads active items to
  compute "uncovered" files, never writes to them.
- Never auto-approves a draft. A draft is inert until a human/AI session
  calls `approve_draft_work_item`/`discard_draft_work_item` (via the API
  or CLI) -- this scheduler only ever creates or discards-and-recreates
  its own drafts.
- Maintains at most one outstanding auto-draft at a time: if the current
  uncovered file set exactly matches an existing draft's `estimated_files`,
  nothing happens (idempotent -- running the same cycle twice in a row
  never creates a duplicate). If it differs, the stale draft(s) are
  discarded and a fresh one is created reflecting the current set --
  self-healing rather than accumulating one draft per cycle.
- Never discards a draft just because uncovered went to empty (e.g. a
  real work item was just created covering those files, or the draft
  was already approved) -- an empty uncovered set is a no-op, not a
  cleanup trigger. Stale-draft cleanup on its own timer is
  `git_watcher.py`'s sibling, the Milestone F maintenance job
  (`collaboration/maintenance.py`), not this one.
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from . import git_info
from .store import CollaborationError, get_collaboration_store
from ..journal import LOGGER_NAME

log = logging.getLogger(LOGGER_NAME)


@dataclass
class _Status:
    running: bool = False
    last_cycle_at: Optional[str] = None
    last_result: Optional[str] = None
    last_error: Optional[str] = None
    cycles_completed: int = 0
    drafts_created_count: int = 0


def _summarize_areas(files: list[str]) -> str:
    """"Uncommitted changes: frontend, src, root files" -- the top-level
    directory of each file, deduplicated and sorted, so a human scanning
    Mission Control's draft list gets a sense of scope without reading
    every path. `"root files"` covers anything with no directory component."""
    areas = sorted({f.split("/", 1)[0] if "/" in f else "root files" for f in files})
    return ", ".join(areas)


class GitWatcherScheduler:
    """One instance watches this checkout's own working tree. Embedded in
    the API process, same lifecycle as `MarketDataScheduler`/
    `AutonomousPaperTrader` -- see `api/app.py::_maybe_start_automation`."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event: Optional[threading.Event] = None
        self._status = _Status()

    def start(self, interval_seconds: int = 300) -> None:
        with self._lock:
            if self._status.running:
                raise RuntimeError("The git-watcher is already running.")
            self._status = _Status(running=True)
            self._stop_event = threading.Event()
            stop_event = self._stop_event

        thread = threading.Thread(
            target=self._run, args=(interval_seconds, stop_event),
            daemon=True, name="futures-bot-git-watcher",
        )
        with self._lock:
            self._thread = thread
        thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        with self._lock:
            stop_event = self._stop_event
            thread = self._thread
        if stop_event is not None:
            stop_event.set()
        if thread is not None:
            thread.join(timeout=timeout)
        with self._lock:
            self._status.running = False

    def status(self) -> dict:
        with self._lock:
            return {
                "running": self._status.running,
                "last_cycle_at": self._status.last_cycle_at,
                "last_result": self._status.last_result,
                "last_error": self._status.last_error,
                "cycles_completed": self._status.cycles_completed,
                "drafts_created_count": self._status.drafts_created_count,
            }

    def _run(self, interval_seconds: int, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            self._run_cycle(datetime.now(timezone.utc))
            stop_event.wait(interval_seconds)

    def _run_cycle(self, now: datetime) -> None:
        try:
            created = self._reconcile()
            with self._lock:
                self._status.last_cycle_at = now.isoformat()
                self._status.last_result = (
                    f"draft created ({created['title']})" if created else "no uncovered changes"
                )
                self._status.last_error = None
                self._status.cycles_completed += 1
                if created:
                    self._status.drafts_created_count += 1
        except Exception as exc:  # noqa: BLE001 -- one bad cycle must not kill the thread
            log.error("git-watcher cycle failed: %s", exc, exc_info=True)
            with self._lock:
                self._status.last_cycle_at = now.isoformat()
                self._status.last_error = str(exc)

    def _reconcile(self) -> Optional[dict]:
        """Returns the newly-created draft (as a dict) if one was created
        this cycle, else `None`. See this module's own docstring for the
        exact dedup/supersede contract."""
        changed = git_info.changed_files()
        if not changed:
            return None

        store = get_collaboration_store()
        active = store.fetch_active_work_items()  # org_id=None: every org's active work, same repo either way
        covered: set[str] = set()
        for item in active:
            # A draft's own files must NOT count as "covered" here: they'd
            # self-suppress from `uncovered` on the very next cycle,
            # silently vanishing from any future (superseding) draft the
            # moment the old one gets discarded below. Only a real item
            # actually resolves a file -- see this module's own docstring.
            if item.get("is_draft"):
                continue
            covered.update(item.get("estimated_files") or [])
        uncovered = sorted(set(changed) - covered)
        if not uncovered:
            return None

        drafts = store.fetch_draft_work_items()
        if any(set(d.get("estimated_files") or []) == set(uncovered) for d in drafts):
            return None  # already covered by an existing, up-to-date draft -- idempotent no-op

        for stale in drafts:
            try:
                store.discard_draft_work_item(stale["id"])
            except CollaborationError:
                pass  # approved/discarded concurrently by a human/AI session -- fine, ignore

        item_id = uuid.uuid4().hex[:12]
        return store.create_work_item(
            item_id=item_id,
            title=f"Uncommitted changes: {_summarize_areas(uncovered)}",
            description=(
                f"Auto-detected by the SIL Phase 4 git-watcher: {len(uncovered)} file(s) with "
                "uncommitted changes not covered by any active work item. Approve to turn this "
                "into a real work item, or discard if it doesn't need one."
            ),
            estimated_files=uncovered, priority="low", is_draft=True,
        )


_watcher: Optional[GitWatcherScheduler] = None
_watcher_lock = threading.Lock()


def get_git_watcher() -> GitWatcherScheduler:
    global _watcher
    with _watcher_lock:
        if _watcher is None:
            _watcher = GitWatcherScheduler()
        return _watcher


def reset_git_watcher() -> None:
    """Test-only. Production code never calls this."""
    global _watcher
    with _watcher_lock:
        _watcher = None
