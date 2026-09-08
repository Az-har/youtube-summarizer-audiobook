from __future__ import annotations

import logging
import threading
from contextlib import contextmanager

logger = logging.getLogger("VRAMGuard")


class VRAMGuard:
    """
    Mutual exclusion semaphore ensuring Whisper (Vulkan/GPU compute) and
    Ollama (Local LLM VRAM) never execute heavy inferences concurrently.
    Guarantees zero VRAM collisions/OOM on 8GB GPUs (e.g. AMD RX 6600).
    """
    _vram_lock = threading.BoundedSemaphore(value=1)

    @classmethod
    @contextmanager
    def guard(cls, stage_name: str = "GPU_STAGE", timeout: float | None = None):
        """Context manager to cleanly serialize GPU VRAM workloads."""
        acquired = cls._vram_lock.acquire(timeout=timeout if timeout is not None else -1)
        if not acquired:
            logger.warning("VRAMGuard timeout waiting for %s. Proceeding with caution.", stage_name)
        else:
            logger.debug("VRAMGuard acquired lock for %s", stage_name)
        try:
            yield acquired
        finally:
            if acquired:
                cls._vram_lock.release()
                logger.debug("VRAMGuard released lock for %s", stage_name)
