from __future__ import annotations

from pathlib import Path

from api import server


def _clear_response_cache() -> None:
    cache = getattr(server, "_DASHBOARD_RESPONSE_CACHE", None)
    if isinstance(cache, dict):
        cache.clear()


def test_live_safety_route_uses_short_response_cache(monkeypatch):
    _clear_response_cache()
    now = {"value": 1000.0}
    calls = {"count": 0}

    def fake_safety_report_summary():
        calls["count"] += 1
        return {"ok": True, "sequence": calls["count"]}

    monkeypatch.setenv("DASHBOARD_LIVE_SAFETY_CACHE_SECONDS", "10")
    monkeypatch.setattr(server.time, "time", lambda: now["value"])
    monkeypatch.setattr(server, "_safety_report_summary", fake_safety_report_summary)

    with server.app.test_client() as client:
        first = client.get("/api/live-safety").get_json()
        second = client.get("/api/live-safety").get_json()
        now["value"] += 11
        third = client.get("/api/live-safety").get_json()

    assert first["sequence"] == 1
    assert second["sequence"] == 1
    assert third["sequence"] == 2
    assert calls["count"] == 2


def test_strategy_comparison_route_uses_query_scoped_response_cache(monkeypatch):
    _clear_response_cache()
    now = {"value": 2000.0}
    calls = {"count": 0}

    def fake_strategy_payload(*, args=None):
        calls["count"] += 1
        return {
            "ok": True,
            "sequence": calls["count"],
            "window": args.get("window") if args else None,
        }

    monkeypatch.setenv("DASHBOARD_STRATEGY_COMPARISON_CACHE_SECONDS", "20")
    monkeypatch.setattr(server.time, "time", lambda: now["value"])
    monkeypatch.setattr(server, "_strategy_comparison_payload", fake_strategy_payload)

    with server.app.test_client() as client:
        first = client.get("/api/strategy-comparison?window=24h").get_json()
        second = client.get("/api/strategy-comparison?window=24h").get_json()
        different_query = client.get("/api/strategy-comparison?window=7d").get_json()
        now["value"] += 21
        third = client.get("/api/strategy-comparison?window=24h").get_json()

    assert first["sequence"] == 1
    assert second["sequence"] == 1
    assert different_query["sequence"] == 2
    assert third["sequence"] == 3
    assert calls["count"] == 3


def test_frontend_uses_slower_polling_for_heavy_dashboard_endpoints():
    status_bar = Path("web/src/components/StatusBar.tsx").read_text(encoding="utf-8")
    live_page = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert 'usePolling<SafetyData>("/api/live-safety", 30000)' in status_bar
    assert 'usePolling<LiveSafety>("/api/live-safety", 30000)' in live_page


def test_strategy_compare_frontend_fetches_on_demand_without_polling():
    compare_page = Path("web/src/pages/Compare.tsx").read_text(encoding="utf-8")

    assert 'import { usePolling } from "../hooks/usePolling";' not in compare_page
    assert "usePolling<StrategyComparisonData>" not in compare_page
    assert "fetchStrategyComparison" in compare_page
    assert "Refresh" in compare_page
    assert "showRecent" in compare_page


def test_strategy_compare_frontend_shows_live_matched_real_comparison():
    compare_page = Path("web/src/pages/Compare.tsx").read_text(encoding="utf-8")

    assert "live_matched" in compare_page
    assert "Live-Matched" in compare_page
    assert "Actual PnL" in compare_page
    assert "Normalized PnL" in compare_page


def test_strategy_compare_frontend_separates_today_actual_and_scored_pnl():
    compare_page = Path("web/src/pages/Compare.tsx").read_text(encoding="utf-8")

    assert 'useState<StrategyFilters["window"]>("today")' in compare_page
    assert '(["today", "7d", "14d", "all"] as const)' in compare_page
    assert '"24h" |' not in compare_page
    assert "live trading day" in compare_page
    assert "Actual Live PnL" in compare_page
    assert "Scored PnL" in compare_page
    assert "Scored Win Rate" in compare_page
