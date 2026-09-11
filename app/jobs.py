"""In-process background workers (daemon threads + queue).

Daemon threads never block server shutdown; work interrupted by a restart is failed at the next
startup by each job type's own cleanup. One pool per job type, so slow verification jobs can't
starve discovery runs. Single app instance assumed until jobs move to an external queue.
"""

import logging
import queue
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class WorkerPool:
    def __init__(self, name: str, workers: int) -> None:
        self.name = name
        self.size = workers
        self._queue: "queue.Queue[tuple[Callable[..., Any], tuple]]" = queue.Queue()
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()

    def _loop(self) -> None:
        while True:
            fn, args = self._queue.get()
            try:
                fn(*args)
            except Exception:  # noqa: BLE001 — a worker must survive any job
                logger.exception("%s job %s%r crashed", self.name, getattr(fn, "__name__", fn), args)

    def submit(self, fn: Callable[..., Any], *args: Any) -> None:
        with self._lock:
            while len(self._threads) < self.size:
                thread = threading.Thread(target=self._loop, name=f"{self.name}-{len(self._threads)}", daemon=True)
                thread.start()
                self._threads.append(thread)
        self._queue.put((fn, args))
