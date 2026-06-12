from __future__ import annotations

import re
import hmac
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from api import server


def test_market_window_floors_to_five_minutes():
    now = datetime(2026, 6, 12, 10, 28, 37, tzinfo=timezone.utc)

    window = server._btc_market_window(now)

    assert window["start_ts"] == "2026-06-12T10:25:00+00:00"
    assert window["end_ts"] == "2026-06-12T10:30:00+00:00"
    assert window["previous_start_ts"] == "2026-06-12T10:20:00+00:00"
    assert window["next_start_ts"] == "2026-06-12T10:30:00+00:00"


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
    assert payload["symbol"] == "BTCUSD"
    assert payload["readonly"] is True
    assert payload["market"]["start_ts"] == "2026-06-12T10:25:00+00:00"
    assert payload["market"]["label"] == "10:25-10:30"
    assert payload["target_price"] == 106.0
    assert payload["current_price"] == 106.0
    assert payload["delta"] == 0.0
    assert payload["candles"][-1]["close"] == 106.0
    assert payload["history"][0]["result"] == "UP"
    assert all(key not in payload for key in ("buy", "sell", "order", "trade", "clob", "wallet"))


def test_chainlink_streams_auth_headers_match_official_hmac_shape():
    path = "/api/v1/ws?feedIDs=0xabc"
    headers = server._chainlink_streams_auth_headers(
        "GET",
        path,
        "stream-user-id",
        "stream-secret",
        timestamp_ms=1716211845123,
    )
    body_hash = hashlib.sha256(b"").hexdigest()
    string_to_sign = f"GET {path} {body_hash} stream-user-id 1716211845123"
    expected = hmac.new(b"stream-secret", string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    assert headers["Authorization"] == "stream-user-id"
    assert headers["X-Authorization-Timestamp"] == "1716211845123"
    assert headers["X-Authorization-Signature-SHA256"] == expected


def test_chainlink_streams_status_requires_feed_id_without_leaking_secrets(monkeypatch):
    monkeypatch.delenv("CHAINLINK_STREAMS_BTC_FEED_ID", raising=False)
    monkeypatch.setenv("CHAINLINK_STREAMS_USER_ID", "secret-user")
    monkeypatch.setenv("CHAINLINK_STREAMS_SECRET", "secret-value")

    status = server._chainlink_streams_config_status()

    assert status["status"] == "missing_feed_id"
    assert "secret-user" not in str(status)
    assert "secret-value" not in str(status)


def test_chainlink_live_price_overlays_current_market_payload(monkeypatch):
    rows = pd.DataFrame(
        [
            {"timestamp": "2026-06-12T10:20:00+00:00", "open": 100.0, "high": 102.0, "low": 98.0, "close": 101.0},
            {"timestamp": "2026-06-12T10:25:00+00:00", "open": 101.0, "high": 103.0, "low": 100.0, "close": 102.0},
        ]
    )
    monkeypatch.setattr(
        server,
        "_latest_chainlink_streams_price",
        lambda now=None: {
            "price": 104.25,
            "timestamp": "2026-06-12T10:28:12+00:00",
            "source": "chainlink_streams_ws",
            "status": "fresh",
        },
    )

    payload = server._build_btc_market_chart_payload(rows, now=datetime(2026, 6, 12, 10, 28, tzinfo=timezone.utc))

    assert payload["current_price"] == 104.25
    assert payload["current_price_ts"] == "2026-06-12T10:28:12+00:00"
    assert payload["live_source"] == "chainlink_streams_ws"
    assert payload["live_status"] == "fresh"
    assert payload["candles"][-1]["close"] == 104.25


def test_polymarket_rtds_chainlink_message_updates_btc_live_cache(monkeypatch):
    monkeypatch.setattr(server, "_btc_live_now", lambda: 1000.0)
    server.BTC_LIVE_TICKS.clear()

    server._handle_polymarket_rtds_message(
        {
            "topic": "crypto_prices_chainlink",
            "type": "update",
            "timestamp": 1781268202080,
            "payload": {"symbol": "btc/usd", "timestamp": 1781268201000, "value": 63408.4657},
        }
    )
    price = server._latest_polymarket_chainlink_price(now=datetime.fromtimestamp(1781268202, tz=timezone.utc), start_worker=False)

    assert price["price"] == 63408.4657
    assert price["source"] == "polymarket_rtds_chainlink"
    assert price["status"] == "fresh"
    assert list(server.BTC_LIVE_TICKS)[-1]["price"] == 63408.4657


def test_polymarket_rtds_snapshot_backfills_live_ticks(monkeypatch):
    monkeypatch.setattr(server, "_btc_live_now", lambda: 1000.0)
    server.BTC_LIVE_TICKS.clear()

    server._handle_polymarket_rtds_message(
        {
            "payload": {
                "data": [
                    {"timestamp": 1781268201000, "value": 63408.46},
                    {"timestamp": 1781268202000, "value": 63408.17},
                ]
            }
        }
    )

    assert [item["price"] for item in server.BTC_LIVE_TICKS][-2:] == [63408.46, 63408.17]
    assert server.BTC_LIVE_CACHE["price"] == 63408.17


def test_polymarket_rtds_subscription_uses_unfiltered_update_stream():
    subscription = server._polymarket_rtds_subscription()
    filters = [item["filters"] for item in subscription["subscriptions"]]

    assert filters == [""]


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
    assert payload["source"] == "chainlink_candlestick"
    assert "trading" not in payload


def test_api_btc_market_chart_can_select_market_window(monkeypatch):
    rows = pd.DataFrame(
        [
            {"timestamp": "2026-06-12T10:15:00+00:00", "open": 98.0, "high": 101.0, "low": 97.0, "close": 100.0},
            {"timestamp": "2026-06-12T10:20:00+00:00", "open": 100.0, "high": 104.0, "low": 99.0, "close": 103.0},
            {"timestamp": "2026-06-12T10:25:00+00:00", "open": 103.0, "high": 105.0, "low": 101.0, "close": 102.0},
        ]
    )
    monkeypatch.setattr(server, "_fetch_chainlink_btc_candles", lambda **kwargs: rows)

    response = server.app.test_client().get(
        "/api/btc/market-chart?now=2026-06-12T10:28:00Z&start_ts=2026-06-12T10:20:00Z"
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["market"]["start_ts"] == "2026-06-12T10:20:00+00:00"
    assert payload["market"]["end_ts"] == "2026-06-12T10:25:00+00:00"
    assert payload["target_price"] == 103.0
    assert payload["current_price"] == 102.0
    assert payload["delta"] == -1.0


def test_frontend_market_chart_is_readonly_and_uses_chart_api():
    root = Path(__file__).resolve().parents[1]
    component = root / "web" / "src" / "components" / "BTCMarketChart.tsx"
    live_page = root / "web" / "src" / "pages" / "Live.tsx"

    source = component.read_text(encoding="utf-8")
    live_source = live_page.read_text(encoding="utf-8")
    lowered = source.lower()

    assert "/api/btc/market-chart" in source
    assert "BTCMarketChart" in live_source
    assert "from \"../components/BTCMarketChart\"" in live_source
    assert "BTCChart" not in live_source
    for forbidden in ("buy", "sell", "order", "trade", "clob", "wallet"):
        assert re.search(rf"(?<![a-z]){forbidden}(?![a-z])", lowered) is None
    for forbidden_text in ("下单", "买入", "卖出"):
        assert forbidden_text not in source


def test_frontend_market_chart_uses_compact_dashboard_layout():
    root = Path(__file__).resolve().parents[1]
    source = (root / "web" / "src" / "components" / "BTCMarketChart.tsx").read_text(encoding="utf-8")

    assert "max-w-[1180px]" in source
    assert "mx-auto" in source
    assert "h-[340px]" in source
    assert "lg:grid-cols-[minmax(0,1fr)_220px]" in source
    assert "max-h-[420px]" in source
    assert "font-mono text-xl font-semibold" in source
    assert "h-10 w-10" in source
    assert re.search(r'(?<!max-)h-\[360px\]', source) is None
    assert "max-w-[1320px]" not in source
    assert "lg:grid-cols-[minmax(0,1fr)_260px]" not in source


def test_frontend_market_chart_has_refresh_and_animation_cues():
    root = Path(__file__).resolve().parents[1]
    source = (root / "web" / "src" / "components" / "BTCMarketChart.tsx").read_text(encoding="utf-8")
    css = (root / "web" / "src" / "index.css").read_text(encoding="utf-8")

    assert "usePolling<MarketChartPayload>(url, selectedStart ? 15000 : 1000)" in source
    assert "chainlink-line-draw" in source
    assert "chainlink-price-flash" in source
    assert "chainlink-price-pulse" in source
    assert "Last refresh" in source
    assert "@keyframes chainlink-line-draw" in css
    assert "@keyframes chainlink-price-flash" in css
    assert "@keyframes chainlink-price-pulse" in css


def test_frontend_market_chart_matches_live_polymarket_motion_cues():
    root = Path(__file__).resolve().parents[1]
    source = (root / "web" / "src" / "components" / "BTCMarketChart.tsx").read_text(encoding="utf-8")
    css = (root / "web" / "src" / "index.css").read_text(encoding="utf-8")

    assert "smoothPath(" in source
    assert "live_source" in source
    assert "live_status" in source
    assert "Streaming" in source
    assert "Candlestick fallback" in source
    assert "chainlink-latest-dot" in source
    assert "stopOpacity=\"0.12\"" in source
    assert "@keyframes chainlink-dot-enter" in css


def test_frontend_current_market_button_keeps_live_polling():
    root = Path(__file__).resolve().parents[1]
    source = (root / "web" / "src" / "components" / "BTCMarketChart.tsx").read_text(encoding="utf-8")

    assert "itemIsLiveWindow" in source
    assert "setSelectedStart(itemIsLiveWindow ? null : item.start_ts)" in source


def test_use_polling_deduplicates_shared_urls_and_ignores_stale_responses():
    root = Path(__file__).resolve().parents[1]
    source = (root / "web" / "src" / "hooks" / "usePolling.ts").read_text(encoding="utf-8")

    assert "pollingEntries" in source
    assert "inFlight" in source
    assert "refCount" in source
    assert "requestSeq" in source
    assert "lastStartedAt" in source
    assert "subscribePollingEntry" in source


def test_chainlink_candles_are_cached_between_chart_refreshes():
    server.BTC_CANDLE_CACHE.clear()
    calls = {"count": 0}

    def fetcher():
        calls["count"] += 1
        return pd.DataFrame(
            [{"timestamp": "2026-06-12T10:25:00+00:00", "open": 1, "high": 2, "low": 1, "close": 2}]
        )

    first = server._get_cached_btc_candles(("BTCUSD", "2026-06-12T10:25:00+00:00"), 60, fetcher)
    second = server._get_cached_btc_candles(("BTCUSD", "2026-06-12T10:25:00+00:00"), 60, fetcher)

    assert calls["count"] == 1
    assert first.equals(second)
