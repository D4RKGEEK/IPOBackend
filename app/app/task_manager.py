"""
Background task manager — SQLAlchemy ORM persistence + in-memory write-through.

Long ops (scrape, resolve, parse) run in daemon threads. State is persisted
via the BackgroundTask ORM model so polls survive uvicorn --reload, process
crashes, and Fly deploys. Same DB as ipo_master (SQLite locally, Postgres
in production via DATABASE_URL).
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any, Optional

from app.db.models import BackgroundTask
from app.db.engine import get_session

logger = logging.getLogger(__name__)


def _row_to_dict(row: BackgroundTask) -> dict:
    return {
        "id": row.id,
        "type": row.type,
        "label": row.label,
        "status": row.status,
        "progress": row.progress,
        "progress_label": row.progress_label,
        "message": row.message,
        "result": row.result_json,
        "error": row.error,
        "created_at": row.created_at,
        "started_at": row.started_at,
        "completed_at": row.completed_at,
    }


class _TaskStore:
    """ORM-backed task store with an in-memory cache (write-through)."""

    def __init__(self, max_tasks: int = 200):
        self.max_tasks = max_tasks
        self._cache_lock = threading.Lock()
        self._cache: dict[str, dict] = {}
        self._reload_recent()

    def _reload_recent(self) -> None:
        try:
            with get_session() as s:
                rows = (
                    s.query(BackgroundTask)
                    .order_by(BackgroundTask.created_at.desc())
                    .limit(self.max_tasks)
                    .all()
                )
                with self._cache_lock:
                    for r in rows:
                        d = _row_to_dict(r)
                        self._cache[d["id"]] = d
        except Exception as e:
            logger.warning("task_manager: cache reload failed: %s", e)

    def _persist(self, task: dict) -> None:
        try:
            with get_session() as s:
                row = s.query(BackgroundTask).filter(BackgroundTask.id == task["id"]).first()
                if row is None:
                    row = BackgroundTask(id=task["id"], created_at=task["created_at"])
                    s.add(row)
                row.type = task["type"]
                row.label = task.get("label", "") or ""
                row.status = task["status"]
                row.progress = task.get("progress", 0.0)
                row.progress_label = task.get("progress_label", "") or ""
                row.message = task.get("message", "") or ""
                row.result_json = task.get("result")
                row.error = task.get("error")
                # Don't overwrite a previously-set started_at with None
                if task.get("started_at") is not None:
                    row.started_at = task["started_at"]
                if task.get("completed_at") is not None:
                    row.completed_at = task["completed_at"]
                s.commit()
        except Exception as e:
            logger.warning("task_manager: persist failed for %s: %s", task.get("id"), e)

    def _prune_old(self) -> None:
        """Trim cache + DB to max_tasks newest rows."""
        try:
            with get_session() as s:
                # Find IDs to keep
                keep_ids = [
                    r.id for r in
                    s.query(BackgroundTask.id)
                     .order_by(BackgroundTask.created_at.desc())
                     .limit(self.max_tasks).all()
                ]
                if keep_ids:
                    s.query(BackgroundTask).filter(~BackgroundTask.id.in_(keep_ids)).delete(synchronize_session=False)
                    s.commit()
        except Exception as e:
            logger.debug("task_manager: prune skipped: %s", e)
        with self._cache_lock:
            if len(self._cache) > self.max_tasks:
                keep = sorted(self._cache.values(), key=lambda t: t["created_at"], reverse=True)[:self.max_tasks]
                self._cache = {t["id"]: t for t in keep}

    # ─── Public API ────────────────────────────────────────────

    def create(self, task_type: str, label: str = "") -> str:
        task_id = uuid.uuid4().hex[:12]
        task = {
            "id": task_id, "type": task_type, "label": label,
            "status": "pending", "progress": 0.0, "progress_label": "",
            "message": "", "result": None, "error": None,
            "created_at": time.time(), "started_at": None, "completed_at": None,
        }
        with self._cache_lock:
            self._cache[task_id] = task
        self._persist(task)
        # Cheap prune trigger
        if int(task["created_at"]) % 13 == 0:
            self._prune_old()
        return task_id

    def _mutate(self, task_id: str, mutator) -> Optional[dict]:
        with self._cache_lock:
            t = self._cache.get(task_id)
            if not t:
                return None
            mutator(t)
            snapshot = dict(t)
        self._persist(snapshot)
        return snapshot

    def start(self, task_id: str) -> None:
        self._mutate(task_id, lambda t: t.update(status="running", started_at=time.time()))

    def update(self, task_id: str, progress: float = 0.0, progress_label: str = "", message: str = "") -> None:
        def _apply(t):
            t["progress"] = progress
            if progress_label: t["progress_label"] = progress_label
            if message: t["message"] = message
        self._mutate(task_id, _apply)

    def complete(self, task_id: str, result: Any = None) -> None:
        def _apply(t):
            t["status"] = "completed"
            t["progress"] = 1.0
            t["result"] = result
            t["completed_at"] = time.time()
        self._mutate(task_id, _apply)

    def fail(self, task_id: str, error: str) -> None:
        def _apply(t):
            t["status"] = "failed"
            t["error"] = str(error)[:1000]
            t["completed_at"] = time.time()
        self._mutate(task_id, _apply)

    def get(self, task_id: str) -> Optional[dict]:
        with self._cache_lock:
            t = self._cache.get(task_id)
            if t:
                return dict(t)
        # Cache miss → load from DB (handles polls after process restart)
        try:
            with get_session() as s:
                row = s.query(BackgroundTask).filter(BackgroundTask.id == task_id).first()
        except Exception:
            return None
        if not row:
            return None
        d = _row_to_dict(row)
        with self._cache_lock:
            self._cache[task_id] = d
        return dict(d)

    def list_recent(self, limit: int = 20) -> list[dict]:
        try:
            with get_session() as s:
                rows = (
                    s.query(BackgroundTask)
                    .order_by(BackgroundTask.created_at.desc())
                    .limit(max(1, min(limit, self.max_tasks)))
                    .all()
                )
                return [_row_to_dict(r) for r in rows]
        except Exception as e:
            logger.warning("task_manager: list_recent fallback to cache: %s", e)
            with self._cache_lock:
                sorted_tasks = sorted(self._cache.values(), key=lambda t: t["created_at"], reverse=True)
                return [dict(t) for t in sorted_tasks[:limit]]


# ─── Singleton ─────────────────────────────────────────────────

_store: Optional[_TaskStore] = None
_store_lock = threading.Lock()


def get_manager() -> _TaskStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = _TaskStore()
    return _store


async def run_in_background(task_id: str, fn, *args, **kwargs) -> None:
    """Run a function in a daemon thread, tracking progress + persisting state."""
    manager = get_manager()
    manager.start(task_id)

    def _run():
        try:
            result = fn(task_id, manager, *args, **kwargs)
            manager.complete(task_id, result)
        except Exception as e:
            logger.exception("Task %s failed", task_id)
            manager.fail(task_id, str(e))

    threading.Thread(target=_run, daemon=True, name=f"task-{task_id}").start()
