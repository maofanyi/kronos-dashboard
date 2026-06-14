from __future__ import annotations

import pytest

from api import server


@pytest.fixture(autouse=True)
def isolate_btc_live_cache(monkeypatch):
    monkeypatch.setenv("DISABLE_POLYMARKET_RTDS", "1")
    with server.BTC_LIVE_LOCK:
        server.BTC_LIVE_WORKER.update({"started": False, "thread": None})
        server.BTC_LIVE_CACHE.clear()
        server.BTC_LIVE_CACHE.update(
            {
                "price": None,
                "timestamp": None,
                "received_at": None,
                "source": None,
                "status": "idle",
                "error": None,
            }
        )
        server.BTC_LIVE_TICKS.clear()
    yield
