# Readonly Chainlink Market Chart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only BTC 5-minute Polymarket-style chart to the existing Live Dashboard using Chainlink price data.

**Architecture:** The Flask API exposes one new read-only chart payload built from Chainlink Candlestick data already configured in the sibling Kronos repo. The React dashboard renders that payload as a market chart, countdown, market switcher, and historical result strip without any trading or order controls.

**Tech Stack:** Flask, pandas, pytest, React, TypeScript, Tailwind, lucide-react.

---

### Task 1: API Contract Tests

**Files:**
- Create: `tests/test_btc_market_chart.py`
- Modify: none

- [ ] **Step 1: Write failing tests**

```python
from datetime import datetime, timezone

import pandas as pd

from api import server


def test_market_window_floors_to_five_minutes():
    now = datetime(2026, 6, 12, 10, 28, 37, tzinfo=timezone.utc)

    window = server._btc_market_window(now)

    assert window["start_ts"] == "2026-06-12T10:25:00+00:00"
    assert window["end_ts"] == "2026-06-12T10:30:00+00:00"


def test_chainlink_market_payload_is_readonly_and_calculates_prices():
    rows = pd.DataFrame(
        [
            {"timestamp": "2026-06-12T10:15:00+00:00", "open": 100.0, "high": 105.0, "low": 99.0, "close": 101.0},
            {"timestamp": "2026-06-12T10:20:00+00:00", "open": 101.0, "high": 106.0, "low": 100.0, "close": 104.0},
            {"timestamp": "2026-06-12T10:25:00+00:00", "open": 104.0, "high": 107.0, "low": 103.0, "close": 106.0},
        ]
    )
    now = datetime(2026, 6, 12, 10, 28, tzinfo=timezone.utc)

    payload = server._build_btc_market_chart_payload(rows, now=now)

    assert payload["source"] == "chainlink_candlestick"
    assert payload["market"]["start_ts"] == "2026-06-12T10:25:00+00:00"
    assert payload["target_price"] == 106.0
    assert payload["current_price"] == 106.0
    assert payload["delta"] == 0.0
    assert payload["history"][0]["result"] == "UP"
    assert all(key not in payload for key in ("buy", "sell", "order", "trade", "clob"))


def test_api_btc_market_chart_uses_readonly_chainlink_payload(monkeypatch):
    rows = pd.DataFrame(
        [
            {"timestamp": "2026-06-12T10:20:00+00:00", "open": 100.0, "high": 102.0, "low": 98.0, "close": 101.0},
            {"timestamp": "2026-06-12T10:25:00+00:00", "open": 101.0, "high": 103.0, "low": 100.0, "close": 102.0},
        ]
    )
    monkeypatch.setattr(server, "_fetch_chainlink_btc_candles", lambda **kwargs: rows)

    response = server.app.test_client().get("/api/btc/market-chart?now=2026-06-12T10:28:00Z")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["symbol"] == "BTCUSD"
    assert payload["readonly"] is True
    assert "trading" not in payload
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m pytest tests/test_btc_market_chart.py -q`

Expected: FAIL because `_btc_market_window`, `_build_btc_market_chart_payload`, and `/api/btc/market-chart` do not exist yet.

### Task 2: Flask Read-Only Chainlink Payload

**Files:**
- Modify: `api/server.py`

- [ ] **Step 1: Implement minimal helpers and endpoint**

Add:
- `_kronos_root()`
- `_ensure_kronos_src_on_path()`
- `_load_kronos_env()`
- `_btc_market_window(now)`
- `_fetch_chainlink_btc_candles(...)`
- `_build_btc_market_chart_payload(frame, now)`
- `@app.route("/api/btc/market-chart")`

The endpoint must only read Chainlink Candlestick data and return chart state. It must not call CLOB, relayer, order, buy, sell, wallet, or settlement mutation APIs.

- [ ] **Step 2: Run tests to verify pass**

Run: `python -m pytest tests/test_btc_market_chart.py -q`

Expected: PASS.

### Task 3: React Read-Only Chart

**Files:**
- Create: `web/src/components/BTCMarketChart.tsx`
- Modify: `web/src/pages/Live.tsx`

- [ ] **Step 1: Add component**

The component fetches `/api/btc/market-chart`, renders:
- BTC market header
- target price
- current price
- signed delta
- live countdown
- animated SVG line chart with target line
- previous/current/next market switcher
- historical results

The component must not render order buttons, buy buttons, sell buttons, quantity inputs, wallet actions, or trading forms.

- [ ] **Step 2: Embed on Live page**

Replace the existing small `BTCChart` slot with the new `BTCMarketChart` full-width section near the top of `Live.tsx`.

### Task 4: Verification

**Files:**
- No planned source changes.

- [ ] **Step 1: Python tests**

Run: `python -m pytest tests -q`

Expected: all dashboard tests pass.

- [ ] **Step 2: Frontend build**

Run from `web`: `npm run build`

Expected: TypeScript and Vite build pass.

- [ ] **Step 3: Browser check**

Open `http://localhost:63331/` or the running dashboard URL. Confirm the Live Dashboard shows the read-only Chainlink BTC chart and contains no trading controls.
