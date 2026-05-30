"""
Simple background task manager with progress tracking.

Long-running operations (scrape, resolve, parse) run in background threads.
The API returns a task_id immediately. Clients poll GET /api/tasks/{id} for status.
"""
import asyncio
import logging
import threading
import time
import uuid
from typing import Any, Optional

logger = logging.getLogger(__name__)


class TaskManager:
    """In-memory task store. Thread-safe."""

    def __init__(self):
        self._lock = threading.Lock()
        self._tasks: dict[str, dict] = {}
        self._max_tasks = 100

    def create(self, task_type: str, label: str = "") -> str:
        task_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._tasks[task_id] = {
                "id": task_id, "type": task_type, "label": label,
                "status": "pending", "progress": 0.0, "progress_label": "",
                "message": "", "result": None, "error": None,
                "created_at": time.time(), "started_at": None, "completed_at": None,
            }
            if len(self._tasks) > self._max_tasks:
                sorted_ids = sorted(self._tasks.keys(), key=lambda k: self._tasks[k]["created_at"])
                for old_id in sorted_ids[:-self._max_tasks]:
                    del self._tasks[old_id]
        return task_id

    def start(self, task_id: str):
        with self._lock:
            t = self._tasks.get(task_id)
            if t: t["status"] = "running"; t["started_at"] = time.time()

    def update(self, task_id: str, progress: float = 0.0, progress_label: str = "", message: str = ""):
        with self._lock:
            t = self._tasks.get(task_id)
            if t:
                t["progress"] = progress
                if progress_label: t["progress_label"] = progress_label
                if message: t["message"] = message

    def complete(self, task_id: str, result: Any = None):
        with self._lock:
            t = self._tasks.get(task_id)
            if t: t["status"] = "completed"; t["progress"] = 1.0; t["result"] = result; t["completed_at"] = time.time()

    def fail(self, task_id: str, error: str):
        with self._lock:
            t = self._tasks.get(task_id)
            if t: t["status"] = "failed"; t["error"] = str(error)[:500]; t["completed_at"] = time.time()

    def get(self, task_id: str) -> Optional[dict]:
        with self._lock:
            t = self._tasks.get(task_id)
            return dict(t) if t else None

    def list_recent(self, limit: int = 20) -> list[dict]:
        with self._lock:
            sorted_tasks = sorted(self._tasks.values(), key=lambda t: t["created_at"], reverse=True)
            return [dict(t) for t in sorted_tasks[:limit]]


_manager = TaskManager()


def get_manager() -> TaskManager:
    return _manager


async def run_in_background(task_id: str, fn, *args, **kwargs):
    """Run a function in a background thread, tracking progress."""
    manager = get_manager()
    manager.start(task_id)

    def _run():
        try:
            result = fn(task_id, manager, *args, **kwargs)
            manager.complete(task_id, result)
        except Exception as e:
            logger.error(f"Task {task_id} failed: {e}")
            manager.fail(task_id, str(e))

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
