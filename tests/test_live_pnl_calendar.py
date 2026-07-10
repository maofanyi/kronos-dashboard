from __future__ import annotations

import pytest

from api import server


def _records() -> list[dict]:
    return [
        {
            "order_id": "june-last",
            "status": "SETTLED",
            "settled_at": "2026-06-30T15:55:00Z",
            "pnl": 1.25,
            "won": True,
        },
        {
            "order_id": "july-win",
            "signal_id": "signal-win",
            "status": "SETTLED",
            "settled_at": "2026-06-30T16:05:00Z",
            "pnl": 5.10,
            "won": True,
            "market_slug": "btc-updown-5m",
            "token_outcome": "UP",
            "filled_size": 10.0,
            "average_fill_price": 0.49,
            "settlement_source": "official_chainlink",
        },
        {
            "order_id": "july-loss-repost",
            "signal_id": "signal-loss",
            "status": "CANCELLED",
            "settled_at": "2026-07-02T02:00:00Z",
            "pnl": -4.90,
            "won": False,
        },
        {
            "order_id": "july-loss",
            "status": "SETTLED",
            "settled_at": "2026-07-02T02:00:00Z",
            "pnl": -4.90,
            "won": False,
        },
        {
            "order_id": "ignored-open",
            "status": "OPEN",
            "created_at": "2026-07-02T03:00:00Z",
            "pnl": 1000.0,
        },
    ]


def test_month_calendar_uses_configured_trading_day_and_ignores_non_settled_attempts(monkeypatch):
    monkeypatch.setenv("DASHBOARD_TRADING_DAY_TZ", "Asia/Shanghai")

    payload = server._monthly_pnl_calendar_from_records(_records(), month_value="2026-07")

    by_date = {row["date"]: row for row in payload["days"]}
    assert len(payload["days"]) == 31
    assert payload["day_tz"] == "Asia/Shanghai"
    assert payload["available_months"] == ["2026-06", "2026-07"]
    assert by_date["2026-07-01"] == {
        "date": "2026-07-01", "pnl_usdc": 5.1, "settled": 1, "wins": 1, "losses": 0
    }
    assert by_date["2026-07-02"]["pnl_usdc"] == -4.9
    assert by_date["2026-07-03"]["settled"] == 0
    assert payload["total_pnl_usdc"] == 0.2
    assert payload["settled"] == 2


def test_month_calendar_returns_all_days_for_empty_leap_month(monkeypatch):
    monkeypatch.setenv("DASHBOARD_TRADING_DAY_TZ", "Asia/Shanghai")

    payload = server._monthly_pnl_calendar_from_records([], month_value="2028-02")

    assert len(payload["days"]) == 29
    assert payload["empty"] is True
    assert payload["total_pnl_usdc"] == 0.0


def test_daily_orders_reconcile_exactly_to_month_day(monkeypatch):
    monkeypatch.setenv("DASHBOARD_TRADING_DAY_TZ", "Asia/Shanghai")
    month = server._monthly_pnl_calendar_from_records(_records(), month_value="2026-07")
    day = server._daily_pnl_orders_from_records(_records(), day_value="2026-07-01")

    month_day = next(row for row in month["days"] if row["date"] == "2026-07-01")
    assert sum(row["pnl_usdc"] for row in day["orders"]) == pytest.approx(month_day["pnl_usdc"])
    assert day["total_pnl_usdc"] == month_day["pnl_usdc"]
    assert day["orders"][0]["settlement_source"] == "official_chainlink"


def test_calendar_rounds_totals_only_after_raw_pnl_accumulation(monkeypatch):
    monkeypatch.setenv("DASHBOARD_TRADING_DAY_TZ", "Asia/Shanghai")
    records = [
        {
            "order_id": "rounding-one",
            "status": "SETTLED",
            "settled_at": "2026-07-01T00:00:00Z",
            "pnl": 0.0000000049,
            "won": True,
        },
        {
            "order_id": "rounding-two",
            "status": "SETTLED",
            "settled_at": "2026-07-01T00:01:00Z",
            "pnl": 0.0000000049,
            "won": True,
        },
    ]

    month = server._monthly_pnl_calendar_from_records(records, month_value="2026-07")
    day = server._daily_pnl_orders_from_records(records, day_value="2026-07-01")

    month_day = next(row for row in month["days"] if row["date"] == "2026-07-01")
    assert month_day["pnl_usdc"] == 0.00000001
    assert month["total_pnl_usdc"] == 0.00000001
    assert day["orders"][0]["pnl_usdc"] == pytest.approx(0.0000000049)
    assert day["orders"][1]["pnl_usdc"] == pytest.approx(0.0000000049)
    assert round(sum(row["pnl_usdc"] for row in day["orders"]), 8) == day["total_pnl_usdc"]
    assert day["total_pnl_usdc"] == 0.00000001


def test_calendar_retains_duplicate_qualifying_settled_rows(monkeypatch):
    monkeypatch.setenv("DASHBOARD_TRADING_DAY_TZ", "Asia/Shanghai")
    duplicate = {
        "order_id": "same-order",
        "signal_id": "same-signal",
        "status": "SETTLED",
        "settled_at": "2026-07-01T00:00:00Z",
        "pnl": 1.25,
        "won": True,
    }

    month = server._monthly_pnl_calendar_from_records([duplicate, dict(duplicate)], month_value="2026-07")
    day = server._daily_pnl_orders_from_records([duplicate, dict(duplicate)], day_value="2026-07-01")

    month_day = next(row for row in month["days"] if row["date"] == "2026-07-01")
    assert month_day["settled"] == 2
    assert month_day["pnl_usdc"] == 2.5
    assert day["settled"] == 2
    assert len(day["orders"]) == 2
    assert day["total_pnl_usdc"] == 2.5


def test_calendar_ledger_path_selects_only_supported_sources(monkeypatch, tmp_path):
    live_path = tmp_path / "live.json"
    paper_path = tmp_path / "paper.json"
    monkeypatch.setattr(server, "_live_real_ledger_path", lambda: live_path)
    monkeypatch.setattr(server, "_paper_ledger_path", lambda: paper_path)

    assert server._calendar_ledger_path("live_real") == live_path
    assert server._calendar_ledger_path("paper_monitor") == paper_path
    with pytest.raises(ValueError, match="unsupported source"):
        server._calendar_ledger_path("unknown")
