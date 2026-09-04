from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence


@dataclass(frozen=True)
class Location:
    lat: float
    lon: float


@dataclass(frozen=True)
class NormalizedEvent:
    platform: str
    raw_event_id: str | None
    event_type: str
    user_id: str
    chat_id: str
    message_id: str | None = None
    text: str | None = None
    command: str | None = None
    callback_payload: str | None = None
    callback_id: str | None = None
    location: Location | None = None
    timestamp: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UiButton:
    text: str
    action: str
    payload: str | None = None
    url: str | None = None

    def __post_init__(self) -> None:
        if self.action not in {"callback", "request_location", "text", "link"}:
            raise ValueError(f"Unsupported button action: {self.action}")
        if self.action == "callback" and not self.payload:
            raise ValueError("callback button requires payload")
        if self.action == "link" and not self.url:
            raise ValueError("link button requires url")


@dataclass(frozen=True)
class UiKeyboard:
    rows: tuple[tuple[UiButton, ...], ...]

    @classmethod
    def from_rows(cls, rows: Sequence[Sequence[UiButton]]) -> "UiKeyboard":
        return cls(tuple(tuple(row) for row in rows))


@dataclass(frozen=True)
class PlatformMessage:
    platform: str
    chat_id: str
    message_id: str


@dataclass(frozen=True)
class ProgressEvent:
    stage: str
    message: str = ""
    current: int | None = None
    total: int | None = None
    data: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProductRequest:
    product: str
    point: Any
    lead_from: int | None = None
    lead_to: int | None = None
    step: int | None = None
    run: Any | None = None
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProductAttachment:
    kind: str
    path: Path
    filename: str
    caption: str = ""
    mime_type: str | None = None


@dataclass
class CommonProductResult:
    product: str
    summary: str
    attachments: list[ProductAttachment]
    metadata: dict[str, Any]
    repeat_command: str | None = None
    actions: list[UiButton] = field(default_factory=list)


class MessengerGateway(Protocol):
    platform: str

    async def send_text(
        self,
        chat_id: str,
        text: str,
        *,
        keyboard: UiKeyboard | None = None,
        parse_mode: str | None = None,
    ) -> PlatformMessage: ...

    async def edit_text(
        self,
        chat_id: str,
        message_id: str,
        text: str,
        *,
        keyboard: UiKeyboard | None = None,
        parse_mode: str | None = None,
    ) -> PlatformMessage: ...

    async def send_image(
        self,
        chat_id: str,
        path: Path,
        *,
        caption: str = "",
    ) -> PlatformMessage: ...

    async def send_file(
        self,
        chat_id: str,
        path: Path,
        *,
        caption: str = "",
        filename: str | None = None,
    ) -> PlatformMessage: ...

    async def send_animation(
        self,
        chat_id: str,
        path: Path,
        *,
        caption: str = "",
    ) -> PlatformMessage: ...

    async def answer_callback(
        self,
        event: NormalizedEvent,
        *,
        text: str | None = None,
    ) -> None: ...
