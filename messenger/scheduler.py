from __future__ import annotations

"""Single-process scheduler for MAX/VK common product snapshots.

The scheduler never calls platform-specific product runners. It executes the
same common services used by interactive requests, then renders through the
selected gateway. A missing/broken gateway marks only its own schedule failed.
"""

import asyncio
import logging
import os
from typing import Callable
from zoneinfo import ZoneInfo

from .contracts import MessengerGateway
from .product_executor import build_snapshot_result
from .profile_service import cleanup_product_result
from .runtime_resources import RuntimeResources, get_runtime_resources
from .schedule_store import MessengerSchedule, MessengerScheduleStore

LOG = logging.getLogger(__name__)


class ScheduleExecutor:
    def __init__(self, resources: RuntimeResources | None = None) -> None:
        self.resources = resources or get_runtime_resources()

    def _gate(self, product: str):
        return self.resources.meteogram_semaphore if product == "meteogram" else self.resources.gfs_semaphore

    async def execute(self, item: MessengerSchedule, gateway: MessengerGateway) -> bool:
        result = None
        try:
            async with self._gate(item.product):
                result = await asyncio.to_thread(build_snapshot_result, item.snapshot())
            await gateway.send_text(item.chat_id, "🕒 По расписанию\n" + result.summary)
            for attachment in result.attachments:
                if attachment.kind == "image":
                    await gateway.send_image(item.chat_id, attachment.path, caption=attachment.caption)
                elif attachment.kind == "animation":
                    await gateway.send_animation(item.chat_id, attachment.path, caption=attachment.caption)
                else:
                    await gateway.send_file(item.chat_id, attachment.path, caption=attachment.caption, filename=attachment.filename)
            return True
        finally:
            if result is not None:
                cleanup_product_result(result)


class MessengerScheduler:
    def __init__(
        self,
        *,
        store: MessengerScheduleStore | None = None,
        executor: ScheduleExecutor | None = None,
        gateways: Callable[[], dict[str, MessengerGateway | None]] | None = None,
        poll_seconds: int | None = None,
        max_late_minutes: int | None = None,
    ) -> None:
        self.store = store or MessengerScheduleStore()
        self.executor = executor or ScheduleExecutor()
        self.gateways = gateways or (lambda: {})
        self.poll_seconds = max(5, int(poll_seconds or os.getenv("MESSENGER_SCHEDULE_POLL_SECONDS", "30")))
        self.max_late_minutes = max(0, int(max_late_minutes if max_late_minutes is not None else os.getenv("MESSENGER_SCHEDULE_MAX_LATE_MINUTES", "180")))
        self._task: asyncio.Task | None = None
        self.last_error: str | None = None
        self._scheduled_gate = asyncio.Semaphore(max(1, int(os.getenv("MAX_CONCURRENT_SCHEDULED", "1"))))

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="messenger-scheduler")

    async def shutdown(self) -> None:
        if self._task is None:
            return
        if not self._task.done():
            self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def run_once(self) -> tuple[int, int]:
        due, skipped = self.store.claim_due(max_late_minutes=self.max_late_minutes)
        gateways = self.gateways()
        completed = 0
        for item in due:
            gateway = gateways.get(item.platform)
            if gateway is None:
                self.store.mark_result(item.schedule_id, success=False, error=f"platform {item.platform} unavailable")
                continue
            async with self._scheduled_gate:
                try:
                    ok = await self.executor.execute(item, gateway)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    LOG.exception("Scheduled %s/%s failed", item.platform, item.schedule_id)
                    self.store.mark_result(item.schedule_id, success=False, error=str(exc))
                    self.last_error = f"{item.platform}:{item.schedule_id}: {exc}"[:500]
                    try:
                        await gateway.send_text(
                            item.chat_id,
                            f"⚠ Ошибка автоматической отправки: {str(exc)[:240]}\nРасписание сохранено и будет запущено в следующий срок.",
                        )
                    except Exception:
                        pass
                    continue
                self.store.mark_result(item.schedule_id, success=bool(ok), error=None if ok else "продукт завершился без результата")
                if ok:
                    completed += 1
                    refreshed = self.store.get(item.platform, item.user_id, item.schedule_id)
                    if refreshed is not None:
                        local = refreshed.next_run_datetime_utc.astimezone(ZoneInfo(refreshed.timezone))
                        try:
                            await gateway.send_text(
                                item.chat_id,
                                f"🕒 Следующая отправка: {local:%d.%m %H:%M} · {refreshed.timezone}",
                            )
                        except Exception:
                            pass
        return completed, len(skipped)

    async def execute_now(self, item: MessengerSchedule, gateway: MessengerGateway) -> bool:
        """Manual run without changing ``next_run_utc``."""
        async with self._scheduled_gate:
            return await self.executor.execute(item, gateway)

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = str(exc)[:500]
                LOG.exception("Messenger scheduler loop failed")
            await asyncio.sleep(self.poll_seconds)
