from __future__ import annotations

"""Process-wide runtime limits shared by Telegram, MAX, VK and web/API.

The application intentionally stays single-process. A single set of gates is
therefore enough to enforce the real server limits without Redis/Celery or an
external coordinator.
"""

import asyncio
import os
import threading
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable, TypeVar

T = TypeVar("T")


class AsyncThreadGate:
    """Async context manager backed by a process-wide threading semaphore."""

    def __init__(self, semaphore: threading.BoundedSemaphore) -> None:
        self._semaphore = semaphore

    async def __aenter__(self) -> "AsyncThreadGate":
        acquire_task = asyncio.create_task(asyncio.to_thread(self._semaphore.acquire))
        try:
            await asyncio.shield(acquire_task)
        except BaseException:
            # If cancellation happens while a worker is waiting, wait until the
            # acquire finishes and immediately return the permit. Otherwise a
            # cancelled request could permanently reduce process capacity.
            try:
                acquired = await acquire_task
            except BaseException:
                acquired = False
            if acquired:
                self._semaphore.release()
            raise
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self._semaphore.release()


@dataclass(slots=True)
class RuntimeResources:
    gfs_limit: int
    geocode_limit: int
    meteogram_limit: int
    _gfs_gate: threading.BoundedSemaphore
    _geocode_gate: threading.BoundedSemaphore
    _meteogram_gate: threading.BoundedSemaphore
    gfs_semaphore: AsyncThreadGate
    geocode_semaphore: AsyncThreadGate
    meteogram_semaphore: AsyncThreadGate

    @classmethod
    def from_limits(
        cls,
        *,
        gfs: int = 2,
        geocode: int = 2,
        meteogram: int = 2,
    ) -> "RuntimeResources":
        gfs_limit = max(1, int(gfs))
        geocode_limit = max(1, int(geocode))
        meteogram_limit = max(1, int(meteogram))
        gfs_gate = threading.BoundedSemaphore(gfs_limit)
        geocode_gate = threading.BoundedSemaphore(geocode_limit)
        meteogram_gate = threading.BoundedSemaphore(meteogram_limit)
        return cls(
            gfs_limit=gfs_limit,
            geocode_limit=geocode_limit,
            meteogram_limit=meteogram_limit,
            _gfs_gate=gfs_gate,
            _geocode_gate=geocode_gate,
            _meteogram_gate=meteogram_gate,
            gfs_semaphore=AsyncThreadGate(gfs_gate),
            geocode_semaphore=AsyncThreadGate(geocode_gate),
            meteogram_semaphore=AsyncThreadGate(meteogram_gate),
        )

    @classmethod
    def from_env(cls) -> "RuntimeResources":
        return cls.from_limits(
            gfs=int(os.getenv("MAX_CONCURRENT_GFS", "2")),
            geocode=int(os.getenv("MAX_CONCURRENT_GEOCODE", "2")),
            meteogram=int(os.getenv("MAX_CONCURRENT_METEOGRAM", "2")),
        )

    def snapshot(self) -> dict[str, int]:
        return {
            "gfs": self.gfs_limit,
            "geocode": self.geocode_limit,
            "meteogram": self.meteogram_limit,
        }

    def _wrap_blocking(
        self,
        func: Callable[..., T],
        gate: threading.BoundedSemaphore,
        kind: str,
    ) -> Callable[..., T]:
        marker = (kind, id(gate))
        if getattr(func, "__gfs_runtime_gate__", None) == marker:
            return func
        original = getattr(func, "__gfs_runtime_original__", func)

        @wraps(original)
        def limited(*args: Any, **kwargs: Any) -> T:
            with gate:
                return original(*args, **kwargs)

        setattr(limited, "__gfs_runtime_gate__", marker)
        setattr(limited, "__gfs_runtime_original__", original)
        return limited

    def wrap_blocking_gfs(self, func: Callable[..., T]) -> Callable[..., T]:
        return self._wrap_blocking(func, self._gfs_gate, "gfs")

    def wrap_blocking_geocode(self, func: Callable[..., T]) -> Callable[..., T]:
        return self._wrap_blocking(func, self._geocode_gate, "geocode")

    def wrap_blocking_meteogram(self, func: Callable[..., T]) -> Callable[..., T]:
        return self._wrap_blocking(func, self._meteogram_gate, "meteogram")

    def configure_router(self, router: Any) -> Any:
        """Attach the shared GFS/geocoder limits to a common messenger router."""

        router.gfs_semaphore = self.gfs_semaphore
        if hasattr(router, "deps") and getattr(router.deps, "geocode", None):
            router.deps.geocode = self.wrap_blocking_geocode(router.deps.geocode)
        router.runtime_resources = self
        return router


_SHARED_RESOURCES: RuntimeResources | None = None
_SHARED_LOCK = threading.Lock()


def get_runtime_resources() -> RuntimeResources:
    global _SHARED_RESOURCES
    if _SHARED_RESOURCES is None:
        with _SHARED_LOCK:
            if _SHARED_RESOURCES is None:
                _SHARED_RESOURCES = RuntimeResources.from_env()
    return _SHARED_RESOURCES


def set_runtime_resources_for_tests(resources: RuntimeResources | None) -> None:
    """Replace the process singleton. Intended only for deterministic tests."""

    global _SHARED_RESOURCES
    with _SHARED_LOCK:
        _SHARED_RESOURCES = resources
