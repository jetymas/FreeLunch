"""Small in-memory throttles for credential failures."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable


class FailureThrottle:
    """Bounded per-key failure windows; keys are irreversibly process-hashed."""

    def __init__(
        self,
        limit: int,
        window_seconds: int,
        max_entries: int,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = max(int(limit), 1)
        self.window_seconds = max(int(window_seconds), 1)
        self.max_entries = max(int(max_entries), 1)
        self._clock = clock
        self._salt = secrets.token_bytes(32)
        self._entries: OrderedDict[str, tuple[int, float]] = OrderedDict()
        self._lock = threading.Lock()

    def _digest(self, key: str) -> str:
        return hmac.new(
            self._salt, key.encode("utf-8", errors="replace"), hashlib.sha256
        ).hexdigest()

    def retry_after(self, key: str) -> int:
        """Return positive seconds while blocked, else zero."""
        now = self._clock()
        digest = self._digest(key)
        with self._lock:
            entry = self._entries.get(digest)
            if entry is None:
                return 0
            count, started = entry
            remaining = started + self.window_seconds - now
            if remaining <= 0:
                del self._entries[digest]
                return 0
            self._entries.move_to_end(digest)
            return max(1, int(remaining + 0.999)) if count >= self.limit else 0

    def fail(self, key: str) -> None:
        now = self._clock()
        digest = self._digest(key)
        with self._lock:
            entry = self._entries.get(digest)
            if entry is None or now - entry[1] >= self.window_seconds:
                self._entries[digest] = (1, now)
            else:
                self._entries[digest] = (entry[0] + 1, entry[1])
            self._entries.move_to_end(digest)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def reset(self, key: str) -> None:
        digest = self._digest(key)
        with self._lock:
            self._entries.pop(digest, None)
