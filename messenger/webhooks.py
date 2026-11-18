from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import time
from collections import OrderedDict
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from .max import MaxApiClient, MaxGateway, normalize_max_update
from .platform_config import PlatformStatus, platform_statuses
from .router import MessengerRouter
from .vk import VkApiClient, VkGateway, normalize_vk_update


LOG = logging.getLogger(__name__)
MAX_WEBHOOK_BODY_BYTES = 1_048_576


async def _read_json_body(request: Request) -> dict[str, Any]:
    raw_length = request.headers.get("content-length")
    if raw_length:
        try:
            if int(raw_length) > MAX_WEBHOOK_BODY_BYTES:
                raise HTTPException(status_code=413, detail="webhook body is too large")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid content-length") from exc
    body = await request.body()
    if len(body) > MAX_WEBHOOK_BODY_BYTES:
        raise HTTPException(status_code=413, detail="webhook body is too large")
    try:
        payload = json.loads(body or b"{}")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="invalid webhook JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="webhook JSON must be an object")
    return payload


class EventDeduplicator:
    def __init__(self, ttl_seconds: int = 3600, max_items: int = 5000) -> None:
        self.ttl_seconds = max(60, int(ttl_seconds))
        self.max_items = max(100, int(max_items))
        self._items: OrderedDict[tuple[str, str], float] = OrderedDict()

    def accept(self, platform: str, event_id: str | None) -> bool:
        if not event_id:
            return True
        now = time.monotonic()
        self._cleanup(now)
        key = (platform, event_id)
        if key in self._items:
            return False
        self._items[key] = now
        while len(self._items) > self.max_items:
            self._items.popitem(last=False)
        return True

    def _cleanup(self, now: float) -> None:
        while self._items:
            key, created = next(iter(self._items.items()))
            if now - created <= self.ttl_seconds:
                break
            self._items.pop(key, None)


class TaskRegistry:
    def __init__(self) -> None:
        self.tasks: set[asyncio.Task[Any]] = set()

    def spawn(self, coro) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self._done)
        return task

    def _done(self, task: asyncio.Task[Any]) -> None:
        self.tasks.discard(task)
        if task.cancelled():
            return
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if exc is not None:
            LOG.error("Messenger event task failed: %s", exc, exc_info=(type(exc), exc, exc.__traceback__))

    async def shutdown(self) -> None:
        tasks = list(self.tasks)
        if not tasks:
            return
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()


class MessengerWebhookService:
    def __init__(
        self,
        router: MessengerRouter,
        *,
        max_gateway: MaxGateway | None = None,
        vk_gateway: VkGateway | None = None,
        max_secret: str | None = None,
        vk_group_id: str | None = None,
        vk_secret: str | None = None,
        vk_confirmation_code: str | None = None,
        platform_status: dict[str, PlatformStatus] | None = None,
        deduplicator: EventDeduplicator | None = None,
        tasks: TaskRegistry | None = None,
    ) -> None:
        self.router = router
        self.max_gateway = max_gateway
        self.vk_gateway = vk_gateway
        self.max_secret = max_secret or ""
        self.vk_group_id = str(vk_group_id or "")
        self.vk_secret = vk_secret or ""
        self.vk_confirmation_code = vk_confirmation_code or ""
        self.platform_status = platform_status or platform_statuses()
        self.deduplicator = deduplicator or EventDeduplicator()
        self.tasks = tasks or TaskRegistry()

    @classmethod
    def from_env(cls, router: MessengerRouter | None = None) -> "MessengerWebhookService":
        router = router or MessengerRouter.default()
        statuses = platform_statuses()

        max_gateway: MaxGateway | None = None
        if statuses["max"].ready:
            max_gateway = MaxGateway(MaxApiClient(os.getenv("MAX_BOT_TOKEN", "").strip()))
        elif statuses["max"].requested:
            LOG.warning("MAX disabled for this runtime: %s", statuses["max"].reason)

        vk_gateway: VkGateway | None = None
        if statuses["vk"].ready:
            vk_gateway = VkGateway(
                VkApiClient(
                    os.getenv("VK_BOT_TOKEN", "").strip(),
                    api_version=os.getenv("VK_API_VERSION", "5.199").strip() or "5.199",
                )
            )
        elif statuses["vk"].requested:
            LOG.warning("VK disabled for this runtime: %s", statuses["vk"].reason)

        return cls(
            router,
            max_gateway=max_gateway,
            vk_gateway=vk_gateway,
            max_secret=os.getenv("MAX_WEBHOOK_SECRET") if statuses["max"].ready else None,
            vk_group_id=os.getenv("VK_GROUP_ID") if statuses["vk"].ready else None,
            vk_secret=os.getenv("VK_CALLBACK_SECRET") if statuses["vk"].ready else None,
            vk_confirmation_code=os.getenv("VK_CONFIRMATION_CODE") if statuses["vk"].ready else None,
            platform_status=statuses,
        )

    def _unavailable_detail(self, platform: str) -> str:
        status = self.platform_status.get(platform)
        if status is None:
            return f"{platform.upper()} gateway is not configured"
        if status.state == "off":
            return f"{platform.upper()} is disabled"
        return f"{platform.upper()} is degraded: {status.reason}"

    def api_router(self) -> APIRouter:
        api = APIRouter()

        @api.post("/webhooks/max")
        async def max_webhook(request: Request):
            if self.max_gateway is None:
                raise HTTPException(status_code=503, detail=self._unavailable_detail("max"))
            if self.max_secret:
                supplied = request.headers.get("X-Max-Bot-Api-Secret", "")
                if not hmac.compare_digest(supplied, self.max_secret):
                    raise HTTPException(status_code=401, detail="invalid MAX webhook secret")
            payload = await _read_json_body(request)
            event = normalize_max_update(payload)
            if event and self.deduplicator.accept("max", event.raw_event_id):
                self.tasks.spawn(self.router.handle(event, self.max_gateway))
            return JSONResponse({"ok": True})

        @api.post("/webhooks/vk")
        async def vk_webhook(request: Request):
            payload = await _read_json_body(request)
            group_id = str(payload.get("group_id") or "")
            if self.vk_group_id and group_id != self.vk_group_id:
                raise HTTPException(status_code=403, detail="invalid VK group_id")
            if self.vk_secret:
                supplied = str(payload.get("secret") or "")
                if not hmac.compare_digest(supplied, self.vk_secret):
                    raise HTTPException(status_code=403, detail="invalid VK callback secret")
            if payload.get("type") == "confirmation":
                if not self.vk_confirmation_code:
                    raise HTTPException(status_code=503, detail=self._unavailable_detail("vk"))
                return PlainTextResponse(self.vk_confirmation_code)
            if self.vk_gateway is None:
                raise HTTPException(status_code=503, detail=self._unavailable_detail("vk"))
            event = normalize_vk_update(payload)
            if event and self.deduplicator.accept("vk", event.raw_event_id):
                self.tasks.spawn(self.router.handle(event, self.vk_gateway))
            return PlainTextResponse("ok")

        return api
