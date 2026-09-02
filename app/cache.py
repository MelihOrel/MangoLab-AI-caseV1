"""A small in-process TTL cache.

Deliberately boring: one process, one dict, no eviction thread. It exists to
satisfy "a repeat of the same question should not re-ask the upstream", and
its two rules are the ones that matter for correctness:

  * the key includes the date, so a rate is never served for a day it does not
    belong to (this is exactly the bug in tool.py);
  * entries expire, so a rate published this morning does not outlive the
    ECB's next publication.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any, Callable, Hashable


class TTLCache:
    def __init__(self, max_entries: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._max_entries = max(1, max_entries)
        self._clock = clock
        self._entries: "OrderedDict[Hashable, tuple[float, Any]]" = OrderedDict()

    def get(self, key: Hashable) -> Any | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if self._clock() >= expires_at:
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return value

    def set(self, key: Hashable, value: Any, ttl_seconds: float) -> None:
        self._entries[key] = (self._clock() + ttl_seconds, value)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)
