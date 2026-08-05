"""
Lightweight TTL cache keyed off the user-configurable refresh interval
(Settings page). Streamlit's own @st.cache_data needs a TTL fixed at
decoration time, which doesn't work when the TTL itself is a runtime
setting, so services call through `cached()` instead.
"""
from __future__ import annotations

import time
from typing import Any, Callable

import streamlit as st

_STORE_KEY = "_aegisops_cache_store"


def _store() -> dict:
    if _STORE_KEY not in st.session_state:
        st.session_state[_STORE_KEY] = {}
    return st.session_state[_STORE_KEY]


def cached(cache_key: str, ttl_seconds: int, loader: Callable[[], Any]) -> Any:
    store = _store()
    now = time.time()
    entry = store.get(cache_key)
    if entry is not None and (now - entry["ts"]) < max(ttl_seconds, 0):
        return entry["value"]
    value = loader()
    store[cache_key] = {"ts": now, "value": value}
    return value


def invalidate(cache_key: str | None = None) -> None:
    store = _store()
    if cache_key is None:
        store.clear()
    else:
        store.pop(cache_key, None)


def last_refreshed(cache_key: str) -> float | None:
    entry = _store().get(cache_key)
    return entry["ts"] if entry else None
