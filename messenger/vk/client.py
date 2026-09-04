from __future__ import annotations

import json
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


class VkApiClient:
    def __init__(
        self,
        token: str,
        *,
        api_version: str = "5.199",
        base_url: str = "https://api.vk.com/method",
        timeout: float = 20.0,
        retries: int = 3,
        session: requests.Session | None = None,
    ) -> None:
        if not token:
            raise ValueError("VK token is required")
        self.token = token
        self.api_version = api_version
        self.base_url = base_url.rstrip("/")
        self.timeout = max(1.0, float(timeout))
        self.retries = max(0, int(retries))
        self.session = session or requests.Session()

    def call(self, method: str, **params: Any) -> Any:
        payload = {key: value for key, value in params.items() if value is not None}
        payload["access_token"] = self.token
        payload["v"] = self.api_version
        url = f"{self.base_url}/{method}"
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                response = self.session.post(url, data=payload, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.retries:
                    raise PlatformTemporaryError(f"VK network error: {exc}") from exc
                self._sleep(attempt)
                continue
            if response.status_code >= 500:
                last_error = PlatformTemporaryError(f"VK HTTP {response.status_code}")
                if attempt >= self.retries:
                    raise last_error
                self._sleep(attempt)
                continue
            if response.status_code >= 400:
                raise PlatformPermanentError(f"VK HTTP {response.status_code}: {response.text[:300]}")
            try:
                body = response.json()
            except ValueError as exc:
                raise PlatformPermanentError("VK returned invalid JSON") from exc
            error = body.get("error") if isinstance(body, dict) else None
            if error:
                code = int(error.get("error_code") or 0)
                message = str(error.get("error_msg") or "VK API error")
                if code in {5, 27, 28}:
                    raise PlatformAuthError(message)
                if code in {6, 9, 10, 29}:
                    last_error = PlatformRateLimitError(message)
                    if attempt >= self.retries:
                        raise last_error
                    self._sleep(attempt)
                    continue
                if code in {1, 2}:
                    last_error = PlatformTemporaryError(message)
                    if attempt >= self.retries:
                        raise last_error
                    self._sleep(attempt)
                    continue
                raise PlatformPermanentError(f"VK API {code}: {message}")
            if not isinstance(body, dict) or "response" not in body:
                raise PlatformPermanentError("VK response has no response field")
            return body["response"]
        raise PlatformTemporaryError(str(last_error or "VK request failed"))

    @staticmethod
    def _sleep(attempt: int) -> None:
        time.sleep(min(8.0, 0.5 * (2**attempt)) + random.uniform(0.0, 0.25))

    @staticmethod
    def random_id() -> int:
        return random.randint(1, 2_147_483_647)

    def send_message(self, peer_id: str, message: str = "", *, keyboard: str | None = None, attachment: str | None = None) -> int:
        response = self.call(
            "messages.send",
            peer_id=int(peer_id),
            random_id=self.random_id(),
            message=message,
            keyboard=keyboard,
            attachment=attachment,
        )
        if isinstance(response, int):
            return response
        if isinstance(response, dict):
            value = response.get("message_id") or response.get("conversation_message_id") or response.get("id")
            if value is not None:
                return int(value)
        raise PlatformPermanentError("VK messages.send returned no message id")

    def edit_message(self, peer_id: str, message_id: str, message: str, *, keyboard: str | None = None) -> bool:
        return bool(self.call(
            "messages.edit",
            peer_id=int(peer_id),
            message_id=int(message_id),
            message=message,
            keyboard=keyboard,
        ))

    def answer_event(self, event_id: str, user_id: str, peer_id: str, text: str | None = None) -> bool:
        event_data = json.dumps({"type": "show_snackbar", "text": text}, ensure_ascii=False) if text else None
        return bool(self.call(
            "messages.sendMessageEventAnswer",
            event_id=event_id,
            user_id=int(user_id),
            peer_id=int(peer_id),
            event_data=event_data,
        ))

    def upload_photo(self, peer_id: str, path: Path) -> str:
        slot = self.call("photos.getMessagesUploadServer", peer_id=int(peer_id))
        upload_url = str(slot.get("upload_url") or "") if isinstance(slot, dict) else ""
        if not upload_url:
            raise PlatformPermanentError("VK photo upload server has no upload_url")
        with Path(path).open("rb") as file_obj:
            try:
                response = self.session.post(
                    upload_url,
                    files={"photo": (Path(path).name, file_obj)},
                    timeout=max(self.timeout, 60.0),
                )
            except requests.RequestException as exc:
                raise PlatformTemporaryError(f"VK photo upload error: {exc}") from exc
        if response.status_code >= 400:
            raise PlatformTemporaryError(f"VK photo upload HTTP {response.status_code}")
        uploaded = response.json()
        saved = self.call(
            "photos.saveMessagesPhoto",
            photo=uploaded.get("photo"),
            server=uploaded.get("server"),
            hash=uploaded.get("hash"),
        )
        item = saved[0] if isinstance(saved, list) and saved else saved
        return _attachment_id("photo", item)

    def upload_document(self, peer_id: str, path: Path, title: str | None = None) -> str:
        slot = self.call("docs.getMessagesUploadServer", type="doc", peer_id=int(peer_id))
        upload_url = str(slot.get("upload_url") or "") if isinstance(slot, dict) else ""
        if not upload_url:
            raise PlatformPermanentError("VK docs upload server has no upload_url")
        with Path(path).open("rb") as file_obj:
            try:
                response = self.session.post(
                    upload_url,
                    files={"file": (Path(path).name, file_obj)},
                    timeout=max(self.timeout, 60.0),
                )
            except requests.RequestException as exc:
                raise PlatformTemporaryError(f"VK document upload error: {exc}") from exc
        if response.status_code >= 400:
            raise PlatformTemporaryError(f"VK document upload HTTP {response.status_code}")
        uploaded = response.json()
        saved = self.call("docs.save", file=uploaded.get("file"), title=title or Path(path).name)
        item = saved.get("doc") if isinstance(saved, dict) and isinstance(saved.get("doc"), dict) else saved
        return _attachment_id("doc", item)


def _attachment_id(prefix: str, item: Any) -> str:
    if not isinstance(item, dict):
        raise PlatformPermanentError(f"VK {prefix} save returned invalid payload")
    owner_id = item.get("owner_id")
    media_id = item.get("id")
    if owner_id is None or media_id is None:
        raise PlatformPermanentError(f"VK {prefix} save returned no owner/id")
    value = f"{prefix}{owner_id}_{media_id}"
    if item.get("access_key"):
        value += f"_{item['access_key']}"
    return value
