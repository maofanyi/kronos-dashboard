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


def test_api_btc_market_chart_future_window_uses_live_chart_without_target(monkeypatch):
    rows = pd.DataFrame(
        [
            {"timestamp": "2026-06-12T10:20:00+00:00", "open": 100.0, "high": 102.0, "low": 98.0, "close": 101.0},
            {"timestamp": "2026-06-12T10:25:00+00:00", "open": 101.0, "high": 103.0, "low": 100.0, "close": 102.0},
        ]
    )
    server.BTC_LIVE_TICKS.clear()
    server.BTC_LIVE_TICKS.extend(
        [
            {"timestamp": "2026-06-12T10:27:45+00:00", "price": 103.1, "source": "polymarket_rtds_chainlink"},
            {"timestamp": "2026-06-12T10:28:00+00:00", "price": 103.4, "source": "polymarket_rtds_chainlink"},
        ]
    )
    monkeypatch.setattr(
        server,
        "_latest_btc_live_price",
        lambda now=None: {
            "price": 103.4,
            "timestamp": "2026-06-12T10:28:00+00:00",
            "source": "polymarket_rtds_chainlink",
            "status": "fresh",
            "error": None,
        },
    )

    payload = server._build_btc_market_chart_payload(
        rows,
        now=datetime(2026, 6, 12, 10, 28, tzinfo=timezone.utc),
        start_ts="2026-06-12T10:30:00Z",
    )

    assert payload["market"]["start_ts"] == "2026-06-12T10:30:00+00:00"
    assert payload["target_price"] is None
    assert payload["target_source"] == "upcoming"
    assert payload["delta"] is None
    assert payload["current_price"] == 103.4
    assert payload["live_status"] == "fresh"
    assert [item["price"] for item in payload["ticks"]][-2:] == [103.1, 103.4]
    assert payload["markets"][0]["result"] == "PENDING"
    assert payload["markets"][1]["result"] == "UPCOMING"


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
    assert "text-3xl font-semibold" in source
    assert "h-10 w-10" in source
    assert re.search(r'(?<!max-)h-\[360px\]', source) is None
    assert "max-w-[1320px]" not in source
    assert "lg:grid-cols-[minmax(0,1fr)_260px]" not in source


def test_frontend_market_chart_has_refresh_and_animation_cues():
    root = Path(__file__).resolve().parents[1]
    source = (root / "web" / "src" / "components" / "BTCMarketChart.tsx").read_text(encoding="utf-8")
    css = (root / "web" / "src" / "index.css").read_text(encoding="utf-8")

    assert "usePolling<MarketChartPayload>(url, selectedStart ? 15000 : 3000)" in source
    assert "chainlink-line-draw" in source
    assert "chainlink-odometer-value" in source
    assert "chainlink-price-pulse" in source
    assert "Last refresh" in source
    assert "@keyframes chainlink-line-draw" in css
    assert "@keyframes chainlink-odometer-roll" in css
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


def test_frontend_market_chart_updates_incrementally_without_remounting_line():
    root = Path(__file__).resolve().parents[1]
    source = (root / "web" / "src" / "components" / "BTCMarketChart.tsx").read_text(encoding="utf-8")
    css = (root / "web" / "src" / "index.css").read_text(encoding="utf-8")

    assert "hasRenderedChart" in source
    assert "chainlink-line-live" in source
    assert "chainlink-latest-marker" in source
    assert "key={`area-${chartAnimationKey}`}" not in source
    assert "key={`line-${chartAnimationKey}`}" not in source
    assert "key={`dot-${chartAnimationKey}`}" not in source
    assert "@keyframes chainlink-marker-pulse" in css
    assert "transition: transform 80ms linear" not in css


def test_frontend_market_chart_marker_does_not_lag_behind_path():
    root = Path(__file__).resolve().parents[1]
    css = (root / "web" / "src" / "index.css").read_text(encoding="utf-8")

    marker_block = re.search(r"\.chainlink-latest-marker\s*\{(?P<body>[^}]+)\}", css)

    assert marker_block is not None
    assert "transition" not in marker_block.group("body")


def test_frontend_market_chart_slides_time_axis_between_price_refreshes():
    root = Path(__file__).resolve().parents[1]
    source = (root / "web" / "src" / "components" / "BTCMarketChart.tsx").read_text(encoding="utf-8")

    assert "LIVE_CHART_WINDOW_MS" in source
    assert "useLiveChartNow" in source
    assert "requestAnimationFrame" in source
    assert "animationNowMs" in source
    assert "useChartGeometry(data, animationNowMs)" in source
    assert "timestamp: new Date(chartNowMs).toISOString()" in source
    assert "1 - (chartNowMs - ts) / LIVE_CHART_WINDOW_MS" in source


def test_frontend_market_chart_grows_from_left_before_live_window_fills():
    root = Path(__file__).resolve().parents[1]
    source = (root / "web" / "src" / "components" / "BTCMarketChart.tsx").read_text(encoding="utf-8")

    assert "const liveWindowFilled = elapsedMs >= LIVE_CHART_WINDOW_MS" in source
    assert "? 1 - (chartNowMs - ts) / LIVE_CHART_WINDOW_MS" in source
    assert ": (ts - chartStartMs) / LIVE_CHART_WINDOW_MS" in source


def test_frontend_market_chart_animates_y_axis_range_changes():
    root = Path(__file__).resolve().parents[1]
    source = (root / "web" / "src" / "components" / "BTCMarketChart.tsx").read_text(encoding="utf-8")

    assert "Y_RANGE_ANIMATION_MS" in source
    assert "useAnimatedChartRange" in source
    assert "easeOutCubic" in source
    assert "setDisplayRange" in source
    assert "range: animatedRange ?? model.range" in source


def test_frontend_market_chart_uses_current_price_line_and_polymarket_axis_ticks():
    root = Path(__file__).resolve().parents[1]
    source = (root / "web" / "src" / "components" / "BTCMarketChart.tsx").read_text(encoding="utf-8")

    assert "BTC_PRICE_AXIS_MIN_STEP = 50" in source
    assert "PRICE_TICK_COUNT = 4" in source
    assert "buildPriceAxis" in source
    assert "priceTicks" in source
    assert "currentY" in source
    assert "chainlink-current-line" in source
    assert "chainlink-target-line" in source
    assert "chainlink-target-pill" in source
    assert "data?.target_price != null) prices.push(data.target_price)" not in source


def test_frontend_market_chart_uses_odometer_metrics_for_price_difference_and_clock():
    root = Path(__file__).resolve().parents[1]
    source = (root / "web" / "src" / "components" / "BTCMarketChart.tsx").read_text(encoding="utf-8")
    css = (root / "web" / "src" / "index.css").read_text(encoding="utf-8")

    assert "useAnimatedNumber" in source
    assert "AnimatedMetric" in source
    assert "CurrentPriceMetric" in source
    assert "RollingText" in source
    assert "ODOMETER_ANIMATION_MS" in source
    assert "<CurrentPriceMetric price={data?.current_price} delta={data?.delta}" in source
    assert "<RollingText value={statusLabel}" in source
    assert "valueKey={chartAnimationKey}" not in source
    assert "chainlink-odometer-value" in css
    assert "chainlink-odometer-glyph" in css
    assert "chainlink-clock-odometer" in css
    assert "@keyframes chainlink-odometer-roll" in css


def test_frontend_current_price_uses_custom_odometer_interpolation_not_number_pop_in():
    root = Path(__file__).resolve().parents[1]
    source = (root / "web" / "src" / "components" / "BTCMarketChart.tsx").read_text(encoding="utf-8")
    css = (root / "web" / "src" / "index.css").read_text(encoding="utf-8")
    current_price_fn = re.search(r"function CurrentPriceMetric\([\s\S]+?\nfunction ResultPill", source)

    assert current_price_fn is not None
    assert "const animatedPrice = useAnimatedNumber(price)" in current_price_fn.group(0)
    assert "const animatedDelta = useAnimatedNumber(delta)" in current_price_fn.group(0)
    assert "<RollingText value={signedMoney(animatedDelta)} />" in current_price_fn.group(0)
    assert "<RollingText value={compactMoney(animatedPrice)} />" in current_price_fn.group(0)
    assert "replayKey" not in source
    assert "key={replayKey ?? value}" not in source
    assert "@keyframes chainlink-odometer-roll" in css
    assert "animation: chainlink-odometer-roll" in css
    assert "t-digit-pop-in" not in css


def test_frontend_market_chart_uses_dollar_only_price_format_and_inline_delta():
    root = Path(__file__).resolve().parents[1]
    source = (root / "web" / "src" / "components" / "BTCMarketChart.tsx").read_text(encoding="utf-8")
    css = (root / "web" / "src" / "index.css").read_text(encoding="utf-8")

    assert "numberFormat" in source
    assert "return `$${numberFormat.format(value)}`" in source
    assert "currency: \"USD\"" not in source
    assert "CurrentPriceMetric" in source
    assert "chainlink-current-price-metric" in source
    assert "chainlink-current-delta" in source
    assert "Triangle" in source
    assert "<AnimatedMetric label=\"Difference\"" not in source
    assert "Difference" not in source
    assert "chainlink-current-price-metric" in css
    assert "chainlink-current-delta" in css


def test_frontend_market_chart_hides_target_for_upcoming_and_colors_switcher_results():
    root = Path(__file__).resolve().parents[1]
    source = (root / "web" / "src" / "components" / "BTCMarketChart.tsx").read_text(encoding="utf-8")

    assert "isUpcomingWindow" in source
    assert "showTargetPrice" in source
    assert "showDelta={!isUpcomingWindow}" in source
    assert "{showTargetPrice && (" in source
    assert "{showTargetPrice && geometry.targetY != null" in source
    assert "<ResultPill result={item.result} />" in source
    assert '<span className="text-xs opacity-70">{item.result}</span>' not in source
    assert 'if (result === "PENDING")' in source
    assert 'if (result === "UPCOMING")' in source


def test_frontend_market_chart_uses_dark_selected_market_button():
    root = Path(__file__).resolve().parents[1]
    source = (root / "web" / "src" / "components" / "BTCMarketChart.tsx").read_text(encoding="utf-8")

    assert "border-zinc-500 bg-zinc-800/80 text-zinc-50 shadow-inner shadow-black/20" in source
    assert "bg-zinc-100" not in source
    assert "text-zinc-950" not in source
    assert "border-zinc-200" not in source


def test_frontend_market_chart_keeps_price_axis_separate_from_live_line():
    root = Path(__file__).resolve().parents[1]
    source = (root / "web" / "src" / "components" / "BTCMarketChart.tsx").read_text(encoding="utf-8")

    assert "CHART_PLOT_RIGHT = 900" in source
    assert "CHART_PRICE_LABEL_X = 988" in source
    assert "CHART_TARGET_PILL_X = 878" in source
    assert "const innerWidth = CHART_PLOT_RIGHT - CHART_LEFT" in source
    assert 'x2={CHART_PLOT_RIGHT}' in source
    assert 'x={CHART_PRICE_LABEL_X}' in source
    assert 'x2="930"' not in source
    assert 'x="838"' not in source


def test_frontend_market_chart_uses_polymarket_style_target_tag():
    root = Path(__file__).resolve().parents[1]
    source = (root / "web" / "src" / "components" / "BTCMarketChart.tsx").read_text(encoding="utf-8")
    css = (root / "web" / "src" / "index.css").read_text(encoding="utf-8")

    assert "CHART_TARGET_PILL_WIDTH = 82" in source
    assert "CHART_TARGET_PILL_HEIGHT = 26" in source
    assert "CHART_TARGET_PILL_NOTCH = 10" in source
    assert "targetPillPath" in source
    assert "chainlink-target-pill-chevron" in source
    assert '<rect className="chainlink-target-pill"' not in source
    assert '<path className="chainlink-target-pill"' in source
    assert ".chainlink-target-pill-chevron" in css


def test_frontend_market_chart_formats_price_axis_as_whole_dollars():
    root = Path(__file__).resolve().parents[1]
    source = (root / "web" / "src" / "components" / "BTCMarketChart.tsx").read_text(encoding="utf-8")

    assert "axisMoney" in source
    assert "maximumFractionDigits: 0" in source
    assert "{axisMoney(tick.value)}" in source
    assert "{compactMoney(tick.value)}" not in source


def test_frontend_market_chart_uses_inter_data_font_instead_of_monospace():
    root = Path(__file__).resolve().parents[1]
    source = (root / "web" / "src" / "components" / "BTCMarketChart.tsx").read_text(encoding="utf-8")
    css = (root / "web" / "src" / "index.css").read_text(encoding="utf-8")

    assert "chainlink-market-chart" in source
    assert ".chainlink-market-chart" in css
    assert 'font-family: Inter, "Inter Fallback"' in css
    assert "font-feature-settings" in css
    assert "font-mono" not in source
    assert "font-family: ui-monospace" not in css


def test_frontend_market_chart_uses_transitions_dev_motion_primitives():
    root = Path(__file__).resolve().parents[1]
    source = (root / "web" / "src" / "components" / "BTCMarketChart.tsx").read_text(encoding="utf-8")
    css = (root / "web" / "src" / "index.css").read_text(encoding="utf-8")

    assert "--tabs-dur" in css
    assert "--panel-open-dur" in css
    assert ".t-tabs-pill" in css
    assert '.t-panel-slide[data-open="true"]' in css
    assert "chainlink-market-switcher t-tabs" in source
    assert "t-tabs-pill" in source
    assert "marketPillStyle" in source
    assert "aria-selected" in source
    assert "t-panel-slide" in source
    assert "key={value}" not in source


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


def test_chainlink_candle_cache_revalidates_incomplete_settlement_frame():
    server.BTC_CANDLE_CACHE.clear()
    calls = {"count": 0}

    missing_settle = pd.DataFrame(
        [{"timestamp": "2026-06-12T10:35:00+00:00", "open": 100, "high": 101, "low": 99, "close": 100}]
    )
    with_settle = pd.DataFrame(
        [
            {"timestamp": "2026-06-12T10:35:00+00:00", "open": 100, "high": 101, "low": 99, "close": 100},
            {"timestamp": "2026-06-12T10:40:00+00:00", "open": 102, "high": 103, "low": 101, "close": 102},
        ]
    )
    required_ts = server._utc_timestamp("2026-06-12T10:40:00+00:00")

    def fetcher():
        calls["count"] += 1
        return missing_settle if calls["count"] == 1 else with_settle

    def has_settle(frame):
        normalized = server._normalize_ohlc_frame(frame)
        return required_ts in normalized.index

    first = server._get_cached_btc_candles(("BTCUSD", "2026-06-12T10:40:00+00:00"), 60, fetcher, cache_validator=has_settle)
    second = server._get_cached_btc_candles(("BTCUSD", "2026-06-12T10:40:00+00:00"), 60, fetcher, cache_validator=has_settle)

    assert calls["count"] == 2
    assert server._normalize_ohlc_frame(first).index[-1] < required_ts
    assert required_ts in server._normalize_ohlc_frame(second).index
