"""Small, atomic, schema-versioned JSON cache for model subsets (no secrets)."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable


def cache_key(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


@contextmanager
def _locked(path: Path):
    # Production is Linux/systemd. flock also protects simultaneous web/bot readers.
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix('.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def cached_json(path: Path, ttl: int, build: Callable[[], Any], validate: Callable[[Any], None]) -> Any:
    with _locked(path):
        try:
            if time.time() - path.stat().st_mtime <= ttl:
                value = json.loads(path.read_text(encoding='utf-8'))
                validate(value)
                return value
        except (OSError, ValueError, TypeError, KeyError, RuntimeError):
            path.unlink(missing_ok=True)
        value = build()
        validate(value)  # Never cache HTML, empty or structurally corrupt results.
        fd, name = tempfile.mkstemp(prefix=path.stem + '-', suffix='.tmp', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump(value, handle, ensure_ascii=False, default=str, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)
        return value
