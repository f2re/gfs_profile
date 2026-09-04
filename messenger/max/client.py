from __future__ import annotations

import random
import time
from pathlib import Path
from typing import Any

import requests

from messenger.errors import (
    PlatformAuthError,
    PlatformPermanentError,
    PlatformRateLimitError,
    PlatformTemporaryError,
)


class MaxApiClient:
    def __init__(
        self,
        token: str,
        *,
        base_url: str = "https://platform-api2.max.ru",
        timeout: float = 20.0,
        retries: int = 3,
        session: requests.Session | None = None,
    ) -> None:
        if not token:
            raise ValueError("MAX token is required")
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.timeout = max(1.0, float(timeout))
        self.retries = max(0, int(retries))
        self.session = session or requests.Session()

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": self.token}

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        url = self.base_url + path
        merged_headers = dict(self.headers)
        if headers:
            merged_headers.update(headers)
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                response = self.session.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    headers=merged_headers,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.retries:
                    raise PlatformTemporaryError(f"MAX network error: {exc}") from exc
                self._sleep(attempt)
                continue

            if response.status_code == 401:
                raise PlatformAuthError("MAX authorization failed")
            if response.status_code == 429:
                last_error = PlatformRateLimitError("MAX rate limit")
                if attempt >= self.retries:
                    raise last_error
                self._sleep(attempt, response.headers.get("Retry-After"))
                continue
            if response.status_code >= 500:
                last_error = PlatformTemporaryError(f"MAX HTTP {response.status_code}")
                if attempt >= self.retries:
                    raise last_error
                self._sleep(attempt)
                continue
            if response.status_code >= 400:
                raise PlatformPermanentError(f"MAX HTTP {response.status_code}: {response.text[:300]}")

            try:
                payload = response.json()
            except ValueError as exc:
                raise PlatformPermanentError("MAX returned invalid JSON") from exc
            if isinstance(payload, dict) and payload.get("success") is False:
                message = str(payload.get("message") or "MAX API returned success=false")
                if "rate" in message.lower() or "limit" in message.lower():
                    raise PlatformRateLimitError(message)
                raise PlatformPermanentError(message)
            return payload if isinstance(payload, dict) else {"response": payload}

        raise PlatformTemporaryError(str(last_error or "MAX request failed"))

    @staticmethod
    def _sleep(attempt: int, retry_after: str | None = None) -> None:
        if retry_after:
            try:
                delay = min(8.0, max(0.2, float(retry_after)))
            except ValueError:
                delay = 0.0
        else:
            delay = min(8.0, 0.5 * (2**attempt))
        time.sleep(delay + random.uniform(0.0, 0.25))

    @staticmethod
    def recipient_params(chat_id: str) -> dict[str, int]:
        raw = str(chat_id)
        if raw.startswith("user:"):
            return {"user_id": int(raw.split(":", 1)[1])}
        if raw.startswith("chat:"):
            return {"chat_id": int(raw.split(":", 1)[1])}
        return {"chat_id": int(raw)}

    def send_message(self, chat_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/messages", params=self.recipient_params(chat_id), json=body)

    def edit_message(self, message_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("PUT", "/messages", params={"message_id": message_id}, json=body)

    def answer_callback(self, callback_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request("POST", "/answers", params={"callback_id": callback_id}, json=body or {})

    def create_upload(self, media_type: str) -> dict[str, Any]:
        if media_type not in {"image", "video", "audio", "file"}:
            raise ValueError(f"Unsupported MAX upload type: {media_type}")
        return self._request("POST", "/uploads", params={"type": media_type})

    def upload(self, path: Path, media_type: str) -> dict[str, Any]:
        slot = self.create_upload(media_type)
        upload_url = str(slot.get("url") or "")
        if not upload_url:
            raise PlatformPermanentError("MAX upload slot has no url")
        with Path(path).open("rb") as file_obj:
            try:
                response = self.session.post(
                    upload_url,
                    files={"data": (Path(path).name, file_obj)},
                    timeout=max(self.timeout, 60.0),
                )
            except requests.RequestException as exc:
                raise PlatformTemporaryError(f"MAX upload error: {exc}") from exc
        if response.status_code >= 500:
            raise PlatformTemporaryError(f"MAX upload HTTP {response.status_code}")
        if response.status_code >= 400:
            raise PlatformPermanentError(f"MAX upload HTTP {response.status_code}: {response.text[:300]}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise PlatformPermanentError("MAX upload returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise PlatformPermanentError("MAX upload returned invalid payload")
        token = payload.get("token") or slot.get("token")
        if not token:
            raise PlatformPermanentError("MAX upload returned no token")
        return {"token": str(token)}

    def subscribe(self, url: str, secret: str, update_types: list[str]) -> dict[str, Any]:
        return self._request(
            "POST",
            "/subscriptions",
            json={"url": url, "secret": secret, "update_types": update_types},
        )
