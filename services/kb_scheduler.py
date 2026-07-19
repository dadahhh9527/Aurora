"""Periodic local knowledge-base scans during the web application lifespan."""
from __future__ import annotations

import threading

from rag.vector_store import VectorStoreService
from utils.logger_handler import logger


class KnowledgeBaseScheduler:
    def __init__(self, interval_seconds: int, enabled: bool = True):
        self.interval_seconds = max(10, interval_seconds)
        self.enabled = enabled
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.vector_store = VectorStoreService()

    def start(self) -> None:
        if not self.enabled or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="aurora-kb-scanner",
            daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        # Scan immediately, then wait for the configured interval.
        while not self._stop.is_set():
            try:
                self.vector_store.load_document(trigger="scheduled", wait=True)
            except Exception as exc:  # noqa: BLE001
                logger.error("[KB scheduler] unexpected error: %s", exc, exc_info=True)
            if self._stop.wait(self.interval_seconds):
                break

    def trigger(self) -> dict:
        if self.vector_store.status().get("running"):
            return {"status": "busy"}

        def run_once() -> None:
            self.vector_store.load_document(trigger="admin", wait=False)

        threading.Thread(
            target=run_once,
            name="aurora-kb-manual-scan",
            daemon=True,
        ).start()
        return {"status": "started"}

    def status(self) -> dict:
        result = self.vector_store.status()
        result.update(
            enabled=self.enabled,
            interval_seconds=self.interval_seconds,
        )
        return result

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
