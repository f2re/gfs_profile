"""ASGI entrypoint for platforms expecting `main:app` (e.g., Railway defaults)."""

from app import app

__all__ = ["app"]
