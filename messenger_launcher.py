from __future__ import annotations

import os


TRUE_VALUES = {"1", "true", "yes", "on", "да"}


def runtime_enabled(value: str | None = None) -> bool:
    raw = os.getenv("MESSENGER_RUNTIME_ENABLED", "0") if value is None else value
    return str(raw).strip().lower() in TRUE_VALUES


def runtime_port(value: str | None = None) -> int:
    raw = os.getenv("MESSENGER_RUNTIME_PORT", "8081") if value is None else value
    port = int(raw)
    if not 1 <= port <= 65535:
        raise ValueError("MESSENGER_RUNTIME_PORT должен быть в диапазоне 1..65535")
    return port


def main() -> None:
    if not runtime_enabled():
        from telegram_bot import main as telegram_main

        telegram_main()
        return

    import uvicorn

    uvicorn.run(
        "messenger_runtime:app",
        host=os.getenv("MESSENGER_RUNTIME_HOST", "127.0.0.1"),
        port=runtime_port(),
        workers=1,
        log_level=os.getenv("MESSENGER_RUNTIME_LOG_LEVEL", "info"),
        access_log=runtime_enabled(os.getenv("MESSENGER_RUNTIME_ACCESS_LOG", "0")),
    )


if __name__ == "__main__":
    main()
