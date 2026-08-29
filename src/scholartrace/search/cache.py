"""Response caches that never persist credentials or private request parameters."""

from __future__ import annotations

import base64
import hashlib
import json
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

SAFE_RESPONSE_HEADERS = {
    "content-type",
    "etag",
    "last-modified",
    "retry-after",
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
}


@dataclass(frozen=True, slots=True)
class CachedResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes
    stored_at: datetime


class ResponseCache(Protocol):
    def get(self, key: str) -> CachedResponse | None: ...

    def set(self, key: str, response: CachedResponse) -> None: ...


def make_cache_key(*, source: str, url: str, public_params: dict[str, str]) -> str:
    payload = json.dumps(
        {"source": source, "url": url, "params": public_params},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def safe_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        name.lower(): value
        for name, value in headers.items()
        if name.lower() in SAFE_RESPONSE_HEADERS
    }


class MemoryResponseCache:
    def __init__(self) -> None:
        self._entries: dict[str, CachedResponse] = {}

    def get(self, key: str) -> CachedResponse | None:
        return self._entries.get(key)

    def set(self, key: str, response: CachedResponse) -> None:
        self._entries[key] = response


class JsonFileResponseCache:
    """Small deterministic cache for public metadata responses."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        if len(key) != 64 or any(character not in "0123456789abcdef" for character in key):
            raise ValueError("cache key must be a lowercase SHA-256")
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> CachedResponse | None:
        path = self._path(key)
        try:
            payload = json.loads(path.read_text("utf-8"))
            return CachedResponse(
                status_code=int(payload["status_code"]),
                headers={str(k): str(v) for k, v in payload["headers"].items()},
                body=base64.b64decode(payload["body_base64"], validate=True),
                stored_at=datetime.fromisoformat(payload["stored_at"]),
            )
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return None

    def set(self, key: str, response: CachedResponse) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "1.0",
            "status_code": response.status_code,
            "headers": safe_headers(response.headers),
            "body_base64": base64.b64encode(response.body).decode("ascii"),
            "stored_at": response.stored_at.astimezone(UTC).isoformat(),
        }
        serialized = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{key}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(serialized)
                temporary.write("\n")
                temporary_name = temporary.name
            Path(temporary_name).replace(path)
        finally:
            if temporary_name is not None:
                temporary_path = Path(temporary_name)
                if temporary_path.exists():
                    temporary_path.unlink()


def cached_response(
    *,
    status_code: int,
    headers: dict[str, str],
    body: bytes,
    stored_at: datetime | None = None,
) -> CachedResponse:
    return CachedResponse(
        status_code=status_code,
        headers=safe_headers(headers),
        body=body,
        stored_at=stored_at or datetime.now(UTC),
    )
