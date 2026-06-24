from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from api import server


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_dashboard_today_risk_uses_configured_trading_day_timezone(monkeypatch):
    monkeypatch.setenv("DASHBOARD_TRADING_DAY_TZ", "Asia/Shanghai")
    now = datetime(2026, 6, 21, 16, 30, tzinfo=timezone.utc)
    records = [
        {
            "status": "SETTLED",
            "pnl": 1.0,
            "settled_at": "2026-06-21T16:05:00Z",
        },
        {
            "status": "SETTLED",
            "pnl": -1.0,
            "settled_at": "2026-06-21T15:55:00Z",
        },
    ]

    summary = server._risk_summary_from_records(
        records,
        limits_override={"max_daily_loss_usdc": 10, "max_daily_trades": 10},
        now=now,
    )

    assert summary["metrics"]["day"] == "2026-06-22"
    assert summary["metrics"]["day_tz"] == "Asia/Shanghai"
    assert summary["metrics"]["day_start_utc"] == "2026-06-21T16:00:00Z"
    assert summary["metrics"]["day_end_utc"] == "2026-06-22T16:00:00Z"
    assert summary["metrics"]["daily_trades"] == 1
    assert summary["metrics"]["daily_pnl_usdc"] == 1.0


def test_dashboard_risk_summary_allows_managed_open_order_at_limit():
    summary = server._risk_summary_from_records(
        [{"status": "OPEN", "created_at": "2026-06-24T19:30:00Z"}],
        limits_override={
            "max_daily_loss_usdc": 60,
            "max_daily_trades": 100,
            "max_consecutive_losses": 10,
            "max_open_or_pending_orders": 1,
        },
        now=datetime(2026, 6, 24, 19, 31, tzinfo=timezone.utc),
    )

    open_pending = next(check for check in summary["checks"] if check["key"] == "risk_open_or_pending")
    assert open_pending["ok"] is True
    assert open_pending["value"] == 1
    assert open_pending["expected"] == "<= 1"
    assert summary["ok"] is True


def test_dashboard_today_db_fallback_filters_by_trading_day_window(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHBOARD_TRADING_DAY_TZ", "Asia/Shanghai")
    db_path = tmp_path / "dashboard.db"
    monkeypatch.setattr(server, "DB_PATH", db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            pnl REAL,
            won INTEGER,
            regime TEXT,
            direction TEXT,
            size REAL,
            entry_bar TEXT,
            settle_bar TEXT,
            created_at TEXT,
            details TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            kline_n INTEGER,
            action TEXT,
            dir5 TEXT,
            dir4 TEXT,
            regime TEXT,
            filt_passed INTEGER,
            reason TEXT,
            created_at TEXT,
            details TEXT
        )"""
    )
    conn.execute(
        """INSERT INTO trades (source, pnl, won, regime, direction, size, entry_bar, settle_bar, created_at, details)
           VALUES ('live', 1.0, 1, 'r', 'UP', 10, '2026-06-21T16:00:00Z', '2026-06-21T16:05:00Z', '2026-06-21T16:05:01Z', '{}')"""
    )
    conn.execute(
        """INSERT INTO trades (source, pnl, won, regime, direction, size, entry_bar, settle_bar, created_at, details)
           VALUES ('live', -1.0, 0, 'r', 'DOWN', 10, '2026-06-21T15:50:00Z', '2026-06-21T15:55:00Z', '2026-06-21T15:55:01Z', '{}')"""
    )
    conn.execute(
        """INSERT INTO events (source, kline_n, action, dir5, dir4, regime, filt_passed, reason, created_at, details)
           VALUES ('live', 1, 'BUY_UP', 'UP', 'UP', 'r', 1, '', '2026-06-21T16:05:00Z', '{}')"""
    )
    conn.execute(
        """INSERT INTO events (source, kline_n, action, dir5, dir4, regime, filt_passed, reason, created_at, details)
           VALUES ('live', 2, 'HOLD', 'DOWN', 'DOWN', 'r', 0, 'old', '2026-06-21T15:55:00Z', '{}')"""
    )
    conn.commit()
    conn.close()

    day = date(2026, 6, 22)

    trades = server._dashboard_today_trade_records(day)
    events = server._dashboard_today_decision_events(day)

    assert [trade["pnl"] for trade in trades] == [1.0]
    assert [event["action"] for event in events] == ["BUY_UP"]


def test_live_analytics_daily_series_uses_dashboard_trading_day(monkeypatch):
    monkeypatch.setenv("DASHBOARD_TRADING_DAY_TZ", "Asia/Shanghai")
    facts = [
        {
            "ts": datetime(2026, 6, 21, 15, 55, tzinfo=timezone.utc),
            "pnl": -1.0,
            "won": False,
            "settled": True,
        },
        {
            "ts": datetime(2026, 6, 21, 16, 5, tzinfo=timezone.utc),
            "pnl": 1.0,
            "won": True,
            "settled": True,
        },
    ]

    rows = server._analytics_daily_series(facts)

    assert [row["date"] for row in rows] == ["2026-06-21", "2026-06-22"]
    assert rows[0]["total_pnl_usdc"] == -1.0
    assert rows[1]["total_pnl_usdc"] == 1.0


def test_live_safety_labels_paper_source_without_calling_it_real_live(monkeypatch, tmp_path):
    report_dir = tmp_path / "data" / "reports"
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir.mkdir(parents=True)
    checkpoint_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setenv("DASHBOARD_RUN_SOURCE", "paper_aligned_prod_shift1")
    monkeypatch.setattr(
        server,
        "_process_summary",
        lambda patterns: {"running": False, "matches": [], "started_at": None, "uptime_seconds": None, "patterns": patterns},
    )
    payload = server._safety_report_summary()

    assert payload["run_source"] == "paper_aligned_prod_shift1"
    assert payload["mode"] == "paper"
    assert payload["source_label"] == "Paper"
    assert payload["real_orders_enabled"] is False


def test_live_safety_detects_real_orders_from_formal_child_process(monkeypatch, tmp_path):
    report_dir = tmp_path / "data" / "reports"
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir.mkdir(parents=True)
    checkpoint_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.delenv("KRONOS_ENABLE_REAL_ORDERS", raising=False)
    monkeypatch.setenv("DASHBOARD_RUN_SOURCE", "paper_aligned_prod_shift1")

    def fake_process_summary(patterns):
        command = (
            "python scripts\\run_prediction_bound_live_order.py "
            "--watch --submit --environment-enabled YES "
            "--output data\\reports\\prediction_bound_live_formal_latest.json"
        )
        running = "run_prediction_bound_live_order.py" in patterns
        return {
            "running": running,
            "matches": [{"pid": 111, "command": command, "command_full": command}] if running else [],
            "started_at": "2026-06-21T16:00:00Z" if running else None,
            "uptime_seconds": 300,
            "patterns": patterns,
        }

    monkeypatch.setattr(server, "_process_summary", fake_process_summary)
    _write_json(checkpoint_dir / "live_real_orders_current_next.json", [])
    _write_json(
        report_dir / "prediction_bound_live_formal_latest.json",
        {
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "ok": False,
            "reason": "latest prediction did not produce a would_place_order intent",
            "submitted": False,
        },
    )

    payload = server._safety_report_summary()

    assert payload["real_orders_enabled"] is True
    assert payload["run_source"] == "live_real"
    assert payload["mode"] == "live"
    assert payload["source_label"] == "Live Real"
    assert payload["kill_switch"]["state"] == "armed"
    assert payload["kill_switch"]["source"] == "formal_process"


def test_status_legacy_live_query_reads_paper_source_and_labels(tmp_path, monkeypatch):
    db_path = tmp_path / "dashboard.db"
    monkeypatch.setattr(server, "DB_PATH", db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            balance REAL,
            trades_count INTEGER,
            wr REAL,
            cooldown_left INTEGER,
            created_at TEXT
        )"""
    )
    conn.execute(
        """INSERT INTO snapshots (source, balance, trades_count, wr, cooldown_left, created_at)
           VALUES ('paper', 501.0, 7, 0.57, 0, '2026-06-14T00:00:00Z')"""
    )
    conn.commit()
    conn.close()

    with server.app.test_client() as client:
        payload = client.get("/api/status?source=live").get_json()

    assert payload["source"] == "paper"
    assert payload["source_label"] == "Paper"
    assert payload["balance"] == 501.0


def test_live_safety_includes_local_alert_report(monkeypatch, tmp_path):
    report = tmp_path / "live_alerts_latest.json"
    report.write_text(
        json.dumps({
            "active_count": 1,
            "selected_count": 1,
            "active": [{"key": "manual_cancel_required", "severity": "critical"}],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", tmp_path)

    payload = server._safety_report_summary()

    assert payload["alerts"]["available"] is True
    assert payload["alerts"]["active_count"] == 1
    assert payload["alerts"]["critical_count"] == 1


def test_live_safety_separates_live_real_from_paper_monitor(monkeypatch, tmp_path):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir = tmp_path / "data" / "reports"
    checkpoint_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setenv("DASHBOARD_RUN_SOURCE", "paper_aligned_prod_shift1")
    monkeypatch.setattr(
        server,
        "_process_summary",
        lambda patterns: {"running": False, "matches": [], "started_at": None, "uptime_seconds": None, "patterns": patterns},
    )
    _write_json(
        report_dir / "polymarket_clob_allowance_audit_20260611_182608.json",
        {
            "checks": [
                {"name": "minimum_balance", "ok": False, "value": 0.0, "expected": ">= 5.0"},
                {"name": "minimum_allowance", "ok": False, "value": 0.0, "expected": ">= 5.0"},
                {"name": "readonly_get_balance", "ok": True},
                {"name": "readonly_get_balance_allowance", "ok": True},
                {"name": "readonly_authenticated_client", "ok": True},
            ],
            "network": {
                "calls": [
                    {"name": "get_balance_allowance", "balance": 0.0, "min_allowance": 0.0, "allowance_count": 3}
                ]
            },
        },
    )
    now = datetime.now(timezone.utc).isoformat()
    _write_json(
        checkpoint_dir / "paper_aligned_prod_shift1.json",
        {
            "balance": 503.0,
            "pending_orders": [{"status": "PENDING", "created_at": now}],
            "open_orders": [{"status": "OPEN", "created_at": now}],
            "trades": [],
        },
    )
    _write_json(
        checkpoint_dir / "live_real_orders_current_next.json",
        [
            {
                "order_id": "0xnofill",
                "market_slug": "btc-updown-5m-test",
                "status": "NO_FILL",
                "created_at": now,
                "updated_at": now,
                "size": 5,
                "filled_size": 0,
                "price": 0.50,
                "direction": "UP",
                "execution_result": "no_fill",
                "actual_up_chainlink": True,
                "signal_would_have_won": True,
                "market_result_recorded_at": now,
            },
            {
                "order_id": "0xsettled",
                "market_slug": "btc-updown-5m-test",
                "status": "SETTLED",
                "created_at": now,
                "settled_at": now,
                "filled_size": 1,
                "pnl": -0.5,
                "direction": "DOWN",
            }
        ],
    )

    summary = server._safety_report_summary()

    assert summary["paper_monitor"]["orders"]["open"] == 1
    assert summary["paper_monitor"]["orders"]["pending"] == 1
    assert summary["live_real"]["orders"]["open_or_pending"] == 0
    assert summary["live_real"]["orders"]["settled"] == 1
    assert summary["live_real"]["orders"]["cancelled"] == 0
    assert summary["live_real"]["orders"]["no_fill_cancelled"] == 1
    assert summary["live_real"]["market_results"]["resolved"] == 1
    assert summary["live_real"]["market_results"]["wins"] == 1
    assert summary["live_real"]["market_results"]["win_rate"] == 1.0
    assert summary["live_real"]["risk"]["metrics"]["daily_pnl_usdc"] == -0.5
    assert summary["live_real"]["risk"]["metrics"]["wins"] == 0
    assert summary["live_real"]["risk"]["metrics"]["losses"] == 1
    assert summary["live_real"]["risk"]["metrics"]["win_rate"] == 0.0
    assert summary["live_real"]["stats"]["losses"] == 1
    assert summary["live_real"]["ledger"].endswith("live_real_orders_current_next.json")
    settled_record = next(record for record in summary["live_real"]["order_records"] if record["order_id"] == "0xsettled")
    assert settled_record["market_slug"] == "btc-updown-5m-test"
    assert settled_record["pnl"] == -0.5
    no_fill_record = next(record for record in summary["live_real"]["order_records"] if record["order_id"] == "0xnofill")
    assert no_fill_record["pnl"] is None
    assert no_fill_record["hypothetical_won"] is True
    assert no_fill_record["hypothetical_pnl"] == 2.5
    assert no_fill_record["hypothetical_pnl_basis"] == "unfilled_limit"


def test_live_analytics_summarizes_pnl_drawdown_and_no_fill_hypothetical(monkeypatch, tmp_path):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    checkpoint_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "_live_current_balance_value", lambda: (100.0, "test_balance"))
    _write_json(
        checkpoint_dir / "live_real_orders_current_next.json",
        [
            {
                "order_id": "win-049",
                "status": "SETTLED",
                "settled_at": "2026-06-14T00:05:00Z",
                "pnl": 5.0,
                "won": True,
                "price": 0.49,
                "price_tier": "maker_049",
                "direction": "BUY_UP",
                "settlement_source": "chainlink_candlestick",
            },
            {
                "order_id": "loss-050",
                "status": "SETTLED",
                "settled_at": "2026-06-14T00:10:00Z",
                "pnl": -2.0,
                "won": False,
                "price": 0.50,
                "price_tier": "taker_050",
                "direction": "BUY_DOWN",
                "settlement_source": "chainlink_candlestick",
            },
            {
                "order_id": "loss-049",
                "status": "SETTLED",
                "settled_at": "2026-06-14T00:15:00Z",
                "pnl": -3.0,
                "won": False,
                "price": 0.49,
                "price_tier": "maker_049",
                "direction": "BUY_UP",
                "settlement_source": "chainlink_candlestick",
            },
            {
                "order_id": "nofill-049",
                "status": "CANCELLED",
                "created_at": "2026-06-14T00:20:00Z",
                "execution_result": "no_fill",
                "size": 10,
                "filled_size": 0,
                "price": 0.49,
                "price_tier": "maker_049",
                "direction": "BUY_UP",
                "actual_up_chainlink": True,
                "signal_would_have_won": True,
            },
        ],
    )

    summary = server._live_analytics_summary()

    assert summary["summary"]["settled"] == 3
    assert summary["summary"]["wins"] == 1
    assert summary["summary"]["losses"] == 2
    assert summary["summary"]["total_pnl_usdc"] == 0.0
    assert summary["summary"]["profit_factor"] == 1.0
    assert summary["summary"]["max_drawdown_usdc"] == 5.0
    assert summary["equity"]["points"] == [100.0, 105.0, 103.0, 100.0]
    assert summary["equity"]["current_balance_source"] == "test_balance"
    price_tiers = {row["key"]: row for row in summary["breakdowns"]["price_tier"]}
    assert price_tiers["maker_049"]["count"] == 2
    assert price_tiers["maker_049"]["total_pnl_usdc"] == 2.0
    assert price_tiers["taker_050"]["count"] == 1
    assert price_tiers["taker_050"]["total_pnl_usdc"] == -2.0
    assert summary["hypothetical_no_fill"]["count"] == 1
    assert summary["hypothetical_no_fill"]["wins"] == 1
    assert summary["hypothetical_no_fill"]["total_pnl_usdc"] == 5.1

    with server.app.test_client() as client:
        payload = client.get("/api/live-analytics").get_json()
    assert payload["summary"]["settled"] == 3


def test_paper_monitor_exposes_total_win_rate_from_checkpoint():
    checkpoint = {
        "balance": 502.0,
        "open_orders": [],
        "pending_orders": [],
        "trades": [
            {"status": "SETTLED", "pnl": 1.0, "created_at": "2026-06-14T01:00:00Z"},
            {"status": "SETTLED", "pnl": -0.5, "created_at": "2026-06-14T01:05:00Z"},
            {"status": "PENDING", "created_at": "2026-06-14T01:10:00Z"},
        ],
    }

    summary = server._paper_monitor_summary(checkpoint=checkpoint, risk_summary={}, today_summary={})

    assert summary["stats"]["settled"] == 2
    assert summary["stats"]["wins"] == 1
    assert summary["stats"]["losses"] == 1
    assert summary["stats"]["win_rate"] == 0.5


def test_live_order_records_sort_by_market_settle_time_not_reconcile_time():
    records = server._live_order_records(
        [
            {
                "order_id": "older-market",
                "status": "SETTLED",
                "settle_ts": "2026-06-16T16:05:00Z",
                "settled_at": "2026-06-17T07:53:00Z",
            },
            {
                "order_id": "newer-market",
                "status": "SETTLED",
                "settle_ts": "2026-06-16T16:10:00Z",
                "settled_at": "2026-06-17T07:53:00Z",
            },
        ]
    )

    assert [record["order_id"] for record in records] == ["newer-market", "older-market"]


def test_live_order_records_display_final_filled_rows_as_settled():
    records = server._live_order_records(
        [
            {
                "order_id": "chainlink-final-fill",
                "status": "FILLED",
                "risk_excluded": True,
                "filled_size": 10,
                "settle_ts": "2026-06-20T21:00:00Z",
                "settled_at": "2026-06-20T21:04:01Z",
                "settlement_source": "chainlink_candlestick",
                "pnl": -4.9,
            }
        ]
    )

    assert records[0]["status"] == "SETTLED"
    assert records[0]["exchange_final_status"] == "FILLED"
    assert records[0]["risk_excluded"] is True


def test_live_order_records_group_multiple_attempts_by_signal():
    records = server._live_order_records(
        [
            {
                "order_id": "maker-049",
                "signal_id": "aligned_prod_current_next:20260622T093500Z:SHORT",
                "status": "CANCELLED",
                "execution_result": "no_fill",
                "direction": "DOWN",
                "price": 0.49,
                "size": 5,
                "filled_size": 0,
                "remaining_size": 5,
                "created_at": "2026-06-22T09:30:10Z",
                "entry_ts": "2026-06-22T09:35:00Z",
                "settle_ts": "2026-06-22T09:40:00Z",
            },
            {
                "order_id": "taker-050",
                "signal_id": "aligned_prod_current_next:20260622T093500Z:SHORT",
                "repost_parent_order_id": "maker-049",
                "status": "SETTLED",
                "direction": "DOWN",
                "price": 0.50,
                "size": 5,
                "filled_size": 5,
                "remaining_size": 0,
                "pnl": 2.5,
                "settled_at": "2026-06-22T09:45:00Z",
                "settlement_source": "chainlink_candlestick",
                "created_at": "2026-06-22T09:34:20Z",
                "entry_ts": "2026-06-22T09:35:00Z",
                "settle_ts": "2026-06-22T09:40:00Z",
            },
            {
                "order_id": "standalone",
                "status": "NO_FILL",
                "direction": "UP",
                "price": 0.49,
                "size": 5,
                "created_at": "2026-06-22T09:20:00Z",
                "entry_ts": "2026-06-22T09:25:00Z",
                "settle_ts": "2026-06-22T09:30:00Z",
            },
        ]
    )

    assert [record["order_id"] for record in records] == ["taker-050", "standalone"]
    grouped = records[0]
    assert grouped["status"] == "SETTLED"
    assert grouped["signal_id"] == "aligned_prod_current_next:20260622T093500Z:SHORT"
    assert grouped["attempts"] == 2
    assert grouped["order_chain_count"] == 2
    assert [order["order_id"] for order in grouped["order_chain"]] == ["maker-049", "taker-050"]
    assert grouped["filled_size"] == 5
    assert grouped["remaining_size"] == 0
    assert grouped["pnl"] == 2.5


def test_paper_monitor_exposes_total_signal_pass_rate_from_checkpoint_events(monkeypatch, tmp_path):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    checkpoint_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setenv("DASHBOARD_RUN_SOURCE", "paper_aligned_prod_shift1")
    events_path = checkpoint_dir / "paper_aligned_prod_shift1_events.jsonl"
    events_path.write_text(
        "\n".join([
            json.dumps({"type": "decision", "filt_passed": 1, "created_at": "2026-06-14T01:00:00Z"}),
            json.dumps({"type": "decision", "filt_passed": 0, "created_at": "2026-06-14T01:05:00Z"}),
            json.dumps({"type": "decision", "filt_passed": 1, "created_at": "2026-06-14T01:10:00Z"}),
        ]),
        encoding="utf-8",
    )

    summary = server._paper_monitor_summary(checkpoint={"balance": 500, "trades": []}, risk_summary={}, today_summary={})

    assert summary["signals"]["total"] == 3
    assert summary["signals"]["passed"] == 2
    assert summary["signals"]["blocked"] == 1
    assert summary["signals"]["pass_rate"] == 0.6667


def test_live_real_risk_limits_follow_latest_preflight_profile(monkeypatch, tmp_path):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir = tmp_path / "data" / "reports"
    checkpoint_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setattr(
        server,
        "_process_summary",
        lambda patterns: {"running": False, "matches": [], "started_at": None, "uptime_seconds": None, "patterns": patterns},
    )
    _write_json(checkpoint_dir / "live_real_orders_current_next.json", [])
    _write_json(
        report_dir / "live_preflight_chain_latest.json",
        {
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "ok": True,
            "submitted": False,
            "blockers": [],
            "summary": {"gate_ready": True, "smoke_mode": "preview", "open_orders": 0, "settled": 0, "risk_ok": True},
            "components": {
                "risk": {
                    "ok": True,
                    "metrics": {
                        "limits": {
                            "max_daily_loss_usdc": 26.0,
                            "max_daily_trades": 20,
                            "max_consecutive_losses": 3,
                            "max_open_or_pending_orders": 1,
                        }
                    },
                }
            },
        },
    )

    summary = server._safety_report_summary()
    risk = summary["live_real"]["risk"]

    assert risk["limits"]["max_daily_loss_usdc"] == 26.0
    assert risk["limits"]["max_daily_trades"] == 20
    assert risk["checks"][0]["expected"] == "> -26.0"


def test_live_real_prefers_formal_current_next_limits_and_runtime(monkeypatch, tmp_path):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir = tmp_path / "data" / "reports"
    config_dir = tmp_path / "data" / "config"
    log_dir = tmp_path / "data" / "logs"
    checkpoint_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    config_dir.mkdir(parents=True)
    log_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setattr(server, "KRONOS_CONFIG_DIR", config_dir)
    monkeypatch.setattr(server, "KRONOS_LOG_DIR", log_dir)
    monkeypatch.setattr(
        server,
        "_process_summary",
        lambda patterns: {
            "running": "run_prediction_bound_live_formal_supervisor.ps1" in patterns
            or "run_prediction_bound_live_order.py" in patterns,
            "matches": [{"pid": 900, "started_at": "2026-06-16T15:50:00Z"}]
            if "run_prediction_bound_live_formal_supervisor.ps1" in patterns
            else [{"pid": 111, "started_at": "2026-06-16T16:00:00Z"}]
            if "run_prediction_bound_live_order.py" in patterns
            else [],
            "started_at": "2026-06-16T15:50:00Z"
            if "run_prediction_bound_live_formal_supervisor.ps1" in patterns
            else "2026-06-16T16:00:00Z"
            if "run_prediction_bound_live_order.py" in patterns
            else None,
            "uptime_seconds": 660
            if "run_prediction_bound_live_formal_supervisor.ps1" in patterns
            else 60
            if "run_prediction_bound_live_order.py" in patterns
            else None,
            "patterns": patterns,
        },
    )
    _write_json(checkpoint_dir / "live_real_orders_current_next.json", [])
    _write_json(
        report_dir / "live_preflight_chain_latest.json",
        {
            "created_at": "2026-06-14T22:42:28Z",
            "components": {
                "risk": {
                    "metrics": {
                        "limits": {
                            "max_daily_loss_usdc": 26.0,
                            "max_daily_trades": 20,
                            "max_consecutive_losses": 3,
                            "max_open_or_pending_orders": 1,
                        }
                    }
                }
            },
        },
    )
    _write_json(
        config_dir / "aligned_prod_current_next_chainlink_30d30d_m049_shares5_live_params.json",
        {
            "live_restart_contract": {
                "max_daily_loss_usdc": 60.0,
                "max_open_or_pending_orders": 1,
            }
        },
    )
    (log_dir / "prediction_bound_live_formal_supervisor_latest.log").write_text(
        "\n".join(
            [
                "2026-06-16T23:50:58+08:00 formal_live_supervisor started "
                "order_size_shares=10 min_price=0.49 active_max_price=0.50 "
                "max_price=0.52 max_notional=5.2 max_daily_loss=60 "
                "max_daily_trades=100 max_consecutive_losses=10 max_open_or_pending=1 "
                "max_smoke_drawdown=50 same_direction_loss_cooldown_count=4 "
                "same_direction_loss_cooldown_minutes=30 signal_max_age=360 "
                "reference_price_source=chainlink",
                "2026-06-16T23:50:58+08:00 run_start stamp=20260616T155058Z",
            ]
        ),
        encoding="utf-8",
    )
    _write_json(
        report_dir / "prediction_bound_live_formal_latest.json",
        {
            "created_at": "2026-06-16T16:05:21Z",
            "ok": False,
            "submitted": False,
            "reason": "latest prediction did not produce a would_place_order intent",
            "attempts": 3,
            "prediction": {
                "action": "HOLD",
                "reason_code": "no_side_passed",
                "decision_id": "formal:20260616T160000Z",
                "decision_bar_ts": "2026-06-16T16:00:00Z",
                "entry_ts": "2026-06-16T16:10:00Z",
                "settle_ts": "2026-06-16T16:15:00Z",
                "execution_market_shift": "next_period",
            },
        },
    )

    summary = server._safety_report_summary()
    live_real = summary["live_real"]

    assert live_real["risk"]["limits"]["max_daily_loss_usdc"] == 60.0
    assert live_real["risk"]["limits"]["max_daily_trades"] == 100
    assert live_real["risk"]["limits"]["max_consecutive_losses"] == 10
    assert live_real["risk_controls"]["source"] == "supervisor_log"
    assert live_real["risk_controls"]["summary"]["order_size_shares"] == 10
    assert live_real["risk_controls"]["summary"]["min_price"] == 0.49
    assert live_real["risk_controls"]["summary"]["active_max_price"] == 0.50
    assert live_real["risk_controls"]["summary"]["hard_max_price"] == 0.52
    assert live_real["risk_controls"]["summary"]["max_notional_usdc"] == 5.2
    assert live_real["risk_controls"]["summary"]["max_daily_loss_usdc"] == 60.0
    assert live_real["risk_controls"]["summary"]["max_smoke_drawdown_usdc"] == 50.0
    assert live_real["risk_controls"]["summary"]["same_direction_loss_cooldown_count"] == 4
    assert live_real["risk_controls"]["summary"]["same_direction_loss_cooldown_minutes"] == 30
    assert live_real["risk_controls"]["summary"]["signal_max_age_seconds"] == 360
    assert live_real["risk_controls"]["summary"]["reference_price_source"] == "chainlink"
    assert live_real["risk_controls"]["summary"]["execution_market_shift"] == "next_period"
    assert live_real["runtime"]["running"] is True
    assert live_real["runtime"]["role"] == "formal_supervisor"
    assert live_real["runtime"]["started_at"] == "2026-06-16T15:50:00Z"
    assert "run_prediction_bound_live_formal_supervisor.ps1" in live_real["runtime"]["patterns"]
    assert live_real["runtime"]["child_runtime"]["started_at"] == "2026-06-16T16:00:00Z"
    assert live_real["formal"]["available"] is True
    assert live_real["formal"]["latest_action"] == "HOLD"
    assert live_real["formal"]["latest_reason"] == "no_side_passed"


def test_live_safety_surfaces_smoke_drawdown_resilience_blocker(monkeypatch, tmp_path):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir = tmp_path / "data" / "reports"
    log_dir = tmp_path / "data" / "logs"
    checkpoint_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    log_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setattr(server, "KRONOS_LOG_DIR", log_dir)
    monkeypatch.setattr(
        server,
        "_process_summary",
        lambda patterns: {"running": True, "matches": [], "started_at": "2026-06-21T00:00:00Z", "uptime_seconds": 3600, "patterns": patterns},
    )
    _write_json(
        checkpoint_dir / "live_real_orders_current_next.json",
        [
            {
                "order_id": "drawdown-loss-1",
                "status": "SETTLED",
                "direction": "DOWN",
                "pnl": -54.25,
                "entry_ts": "2026-06-21T20:55:00Z",
                "settled_at": "2026-06-21T21:00:00Z",
            }
        ],
    )
    (log_dir / "prediction_bound_live_formal_supervisor_latest.log").write_text(
        (
            "2026-06-21T00:00:00+00:00 formal_live_supervisor started "
            "max_daily_loss=60 max_daily_trades=100 max_consecutive_losses=10 "
            "max_open_or_pending=1 max_smoke_drawdown=50 "
            "same_direction_loss_cooldown_count=8 same_direction_loss_cooldown_minutes=30"
        )
        + "\n",
        encoding="utf-8",
    )
    _write_json(
        report_dir / "prediction_bound_live_formal_latest.json",
        {
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "ok": False,
            "submitted": False,
            "reason": "latest prediction did not produce a would_place_order intent",
            "prediction": {"action": "HOLD", "reason_code": "no_side_passed"},
        },
    )

    summary = server._safety_report_summary()
    risk = summary["live_real"]["risk"]
    readiness_keys = {item["key"]: item for item in summary["checklist"]}

    assert risk["ok"] is False
    assert risk["resilience"]["ok"] is False
    assert "max_smoke_drawdown_usdc" in risk["resilience"]["failures"]
    assert readiness_keys["risk_smoke_drawdown"]["ok"] is False
    assert readiness_keys["risk_smoke_drawdown"]["value"] == 54.25
    assert summary["readiness_summary"]["risk_blockers"] >= 1


def test_live_trading_status_uses_formal_sync_risk_not_stale_preflight(monkeypatch, tmp_path):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir = tmp_path / "data" / "reports"
    log_dir = tmp_path / "data" / "logs"
    runtime_dir = tmp_path / "data" / "runtime"
    checkpoint_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    log_dir.mkdir(parents=True)
    runtime_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setattr(server, "KRONOS_LOG_DIR", log_dir)
    monkeypatch.setattr(server, "KRONOS_RUNTIME_DIR", runtime_dir, raising=False)
    monkeypatch.setenv("KRONOS_ENABLE_REAL_ORDERS", "YES")
    now = datetime.now(timezone.utc)

    def fake_process_summary(patterns):
        text = " ".join(patterns)
        running = any(
            key in text
            for key in (
                "run_prediction_bound_live_formal_supervisor.ps1",
                "run_prediction_bound_live_order.py",
                "run_live_order_sync_loop.py",
            )
        )
        return {
            "running": running,
            "matches": [{"pid": 1234, "command": text}] if running else [],
            "started_at": server._iso_utc(now - timedelta(hours=2)) if running else None,
            "uptime_seconds": 7200 if running else None,
            "patterns": patterns,
        }

    monkeypatch.setattr(server, "_process_summary", fake_process_summary)
    monkeypatch.setattr(
        server,
        "_btc_live_market_data_summary",
        lambda: {"ready": True, "price_age_seconds": 1, "received_age_seconds": 1, "status": "fresh"},
    )
    monkeypatch.setattr(
        server,
        "_live_clob_account_snapshot",
        lambda: {
            "ok": True,
            "source": "test",
            "account": {
                "authenticated": True,
                "orders_read_ok": True,
                "balance_read_ok": True,
                "allowance_read_ok": True,
                "open_orders_count": 0,
                "usdc_balance": 100.0,
                "min_allowance": 100.0,
                "allowance_count": 3,
            },
        },
    )
    _write_json(checkpoint_dir / "live_real_orders_current_next.json", [])
    (log_dir / "prediction_bound_live_formal_supervisor_latest.log").write_text(
        (
            f"{server._iso_utc(now - timedelta(hours=2))} formal_live_supervisor started "
            "order_size_shares=10 min_price=0.49 active_max_price=0.5 max_price=0.52 "
            "max_notional=5.2 max_daily_loss=60 max_daily_trades=100 max_consecutive_losses=10 "
            "max_open_or_pending=1 max_smoke_drawdown=50 same_direction_loss_cooldown_count=8 "
            "same_direction_loss_cooldown_minutes=30 signal_max_age=360 reference_price_source=chainlink"
        ),
        encoding="utf-8",
    )
    _write_json(
        report_dir / "prediction_bound_live_formal_latest.json",
        {
            "created_at": server._iso_utc(now - timedelta(seconds=10)),
            "ok": False,
            "submitted": False,
            "reason": "latest prediction did not produce a would_place_order intent",
            "prediction": {
                "action": "HOLD",
                "reason_code": "no_side_passed",
                "created_at": server._iso_utc(now - timedelta(seconds=10)),
                "execution_market_shift": "next_period",
            },
        },
    )
    _write_json(
        report_dir / "live_order_sync_latest.json",
        {
            "created_at": server._iso_utc(now - timedelta(seconds=20)),
            "ok": True,
            "summary": {"open_orders": 0, "reconcile_errors": 0, "settlement_source": "chainlink"},
        },
    )
    _write_json(
        report_dir / "live_preflight_chain_latest.json",
        {
            "created_at": server._iso_utc(now - timedelta(days=3)),
            "ok": False,
            "submitted": False,
            "blockers": ["stale_manual_preflight"],
        },
    )

    status = server._safety_report_summary()["live_trading_status"]
    blocker_keys = {item["key"] for item in status["blockers"]}

    assert status["ok"] is True
    assert status["status"] == "running"
    assert status["label"] == "Running"
    assert status["formal"]["running"] is True
    assert status["formal"]["fresh"] is True
    assert status["sync"]["running"] is True
    assert status["sync"]["ok"] is True
    assert status["sync"]["fresh"] is True
    assert "live_preflight_fresh" not in blocker_keys
    assert "stale_manual_preflight" not in blocker_keys


def test_live_safety_summaries_include_source_scoped_equity_curves(monkeypatch, tmp_path):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir = tmp_path / "data" / "reports"
    checkpoint_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setenv("DASHBOARD_RUN_SOURCE", "paper_aligned_prod_shift1")
    monkeypatch.setattr(
        server,
        "_process_summary",
        lambda patterns: {"running": False, "matches": [], "started_at": None, "uptime_seconds": None, "patterns": patterns},
    )
    _write_json(
        checkpoint_dir / "paper_aligned_prod_shift1.json",
        {
            "balance": 515.0,
            "trades": [
                {
                    "id": "paper-1",
                    "status": "settled",
                    "created_at": "2026-06-14T00:00:00Z",
                    "settled_at": "2026-06-14T00:05:00Z",
                    "pnl": -10.0,
                    "balance_after": 490.0,
                },
                {
                    "id": "paper-2",
                    "status": "settled",
                    "created_at": "2026-06-14T00:05:00Z",
                    "settled_at": "2026-06-14T00:10:00Z",
                    "pnl": 25.0,
                    "balance_after": 515.0,
                },
            ],
        },
    )
    _write_json(
        checkpoint_dir / "live_real_orders_current_next.json",
        [
            {
                "order_id": "cancelled",
                "status": "CANCELLED",
                "created_at": "2026-06-14T00:00:00Z",
                "pnl": 99.0,
            },
            {
                "order_id": "settled",
                "status": "SETTLED",
                "created_at": "2026-06-14T00:05:00Z",
                "settled_at": "2026-06-14T00:20:00Z",
                "pnl": -0.5,
                "filled_size": 1,
            },
        ],
    )
    _write_json(
        report_dir / "live_preflight_chain_latest.json",
        {
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "ok": True,
            "submitted": False,
            "components": {
                "gate": {
                    "account": {"usdc_balance": 48.5},
                    "funding_requirements": {"funding_ready": True},
                }
            },
        },
    )

    summary = server._safety_report_summary()

    assert summary["paper_monitor"]["equity"]["points"] == [500.0, 490.0, 515.0]
    assert summary["paper_monitor"]["equity"]["pnl_usdc"] == 15.0
    assert summary["paper_monitor"]["equity"]["settled"] == 2
    assert summary["live_real"]["equity"]["points"] == [49.0, 48.5]
    assert summary["live_real"]["equity"]["pnl_usdc"] == -0.5
    assert summary["live_real"]["equity"]["settled"] == 1


def test_live_real_summary_includes_recent_week_pnl_calendar(monkeypatch, tmp_path):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    checkpoint_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "_live_formal_summary", lambda: {"available": False})
    monkeypatch.setattr(server, "_live_formal_report", lambda: (tmp_path / "missing_formal.json", {}))
    monkeypatch.setattr(server, "_live_soak_summary", lambda: {"available": False})
    monkeypatch.setattr(
        server,
        "_live_process_runtime",
        lambda **_kwargs: {"running": False, "matches": [], "started_at": None, "uptime_seconds": None},
    )
    monkeypatch.setattr(server, "_polymarket_account_activity_summary", lambda cash_balance=None: {"available": False})
    day_info = server._dashboard_day_info(now=datetime.now(timezone.utc))
    today = day_info["day"]
    start_utc = day_info["start_utc"]
    _write_json(
        checkpoint_dir / "live_real_orders_current_next.json",
        [
            {
                "order_id": "old",
                "status": "SETTLED",
                "settled_at": server._iso_utc(start_utc - timedelta(days=8) + timedelta(hours=1)),
                "pnl": 99.0,
                "won": True,
            },
            {
                "order_id": "win",
                "status": "SETTLED",
                "settled_at": server._iso_utc(start_utc - timedelta(days=1) + timedelta(hours=1)),
                "pnl": 2.5,
                "won": True,
            },
            {
                "order_id": "loss",
                "status": "SETTLED",
                "settled_at": server._iso_utc(start_utc + timedelta(hours=2)),
                "pnl": -5.2,
                "won": False,
            },
            {
                "order_id": "open",
                "status": "OPEN",
                "created_at": server._iso_utc(start_utc + timedelta(hours=3)),
                "pnl": 1000.0,
            },
        ],
    )

    summary = server._live_real_summary(current_balance=50.0)

    calendar = summary["live_real"]["weekly_pnl_calendar"] if "live_real" in summary else summary["weekly_pnl_calendar"]
    assert len(calendar["days"]) == 7
    assert calendar["total_pnl_usdc"] == -2.7
    assert calendar["settled"] == 2
    assert calendar["wins"] == 1
    assert calendar["losses"] == 1
    assert calendar["days"][-1]["date"] == today.isoformat()
    assert calendar["day_tz"] == "Asia/Shanghai"
    assert calendar["days"][-1]["pnl_usdc"] == -5.2
    assert calendar["days"][-1]["settled"] == 1
    assert calendar["days"][-1]["losses"] == 1
    assert calendar["days"][-2]["pnl_usdc"] == 2.5
    assert calendar["days"][0]["pnl_usdc"] == 0.0


def test_live_safety_readiness_uses_live_real_risk_not_paper_risk(monkeypatch, tmp_path):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir = tmp_path / "data" / "reports"
    checkpoint_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setenv("DASHBOARD_RUN_SOURCE", "paper_aligned_prod_shift1")
    monkeypatch.setattr(
        server,
        "_process_summary",
        lambda patterns: {"running": False, "matches": [], "started_at": None, "uptime_seconds": None, "patterns": patterns},
    )
    _write_json(
        checkpoint_dir / "paper_aligned_prod_shift1.json",
        {
            "trades": [
                {
                    "id": f"paper-{idx}",
                    "status": "settled",
                    "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "settled_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "pnl": 1.0,
                }
                for idx in range(24)
            ]
        },
    )
    _write_json(checkpoint_dir / "live_real_orders_current_next.json", [])
    _write_json(
        report_dir / "live_preflight_chain_latest.json",
        {
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "ok": True,
            "submitted": False,
            "blockers": [],
            "components": {
                "gate": {"account": {"usdc_balance": 48.0}, "funding_requirements": {"funding_ready": True}},
                "risk": {"ok": True, "metrics": {"limits": {"max_daily_loss_usdc": 26.0, "max_daily_trades": 20}}},
            },
        },
    )

    summary = server._safety_report_summary()

    top_risk = summary["risk"]
    paper_risk = summary["paper_monitor"]["risk"]
    readiness_keys = {item["key"]: item for item in summary["checklist"]}

    assert top_risk["metrics"]["daily_trades"] == 0
    assert readiness_keys["risk_daily_trades"]["value"] == 0
    assert readiness_keys["risk_daily_trades"]["ok"] is True
    assert paper_risk["metrics"]["daily_trades"] == 24
    assert paper_risk["checks"][1]["ok"] is False


def test_live_safety_includes_runtime_for_live_soak_and_paper_runner(monkeypatch, tmp_path):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir = tmp_path / "data" / "reports"
    checkpoint_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setenv("DASHBOARD_RUN_SOURCE", "paper_aligned_prod_shift1")

    def fake_process_summary(patterns):
        joined = " ".join(patterns)
        if "run_prediction_bound_live_soak.py" in joined:
            return {
                "running": True,
                "matches": [{"pid": 111, "started_at": "2026-06-14T08:00:00Z"}],
                "started_at": "2026-06-14T08:00:00Z",
                "uptime_seconds": 600,
                "patterns": patterns,
            }
        return {
            "running": True,
            "matches": [{"pid": 222, "started_at": "2026-06-14T07:00:00Z"}],
            "started_at": "2026-06-14T07:00:00Z",
            "uptime_seconds": 4200,
            "patterns": patterns,
        }

    monkeypatch.setattr(server, "_process_summary", fake_process_summary)
    _write_json(checkpoint_dir / "paper_aligned_prod_shift1.json", {"balance": 500.0})
    _write_json(
        report_dir / "prediction_bound_live_soak_latest.json",
        {
            "source": "prediction_bound_live_soak",
            "submit_enabled": True,
            "terminal_reason": "running",
            "attempts": 3,
            "submitted_count": 1,
            "started_at": "2026-06-14T08:00:00Z",
            "elapsed_seconds": 600,
            "latest_report": {
                "prediction": {"action": "BUY_DOWN", "reason_code": "short_passed"},
                "blockers": [],
            },
        },
    )

    summary = server._safety_report_summary()

    assert summary["live_real"]["runtime"]["running"] is True
    assert summary["live_real"]["runtime"]["started_at"] == "2026-06-14T08:00:00Z"
    assert summary["live_real"]["soak"]["submitted_count"] == 1
    assert summary["live_real"]["soak"]["latest_action"] == "BUY_DOWN"
    assert summary["paper_monitor"]["runtime"]["running"] is True
    assert summary["paper_monitor"]["runtime"]["uptime_seconds"] == 4200
    assert summary["paper_monitor"]["checkpoint_age_seconds"] is not None


def test_paper_monitor_auto_detects_latest_aligned_prod_checkpoint(monkeypatch, tmp_path):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    checkpoint_dir.mkdir(parents=True)
    monkeypatch.delenv("DASHBOARD_RUN_SOURCE", raising=False)
    monkeypatch.delenv("RUN_SOURCE", raising=False)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)

    old_path = checkpoint_dir / "paper_live_1h4h.json"
    latest_path = checkpoint_dir / "paper_aligned_prod_shift1.json"
    _write_json(old_path, {"balance": 500.0})
    _write_json(latest_path, {"balance": 711.22})
    os.utime(old_path, (1_000, 1_000))
    os.utime(latest_path, (2_000, 2_000))

    summary = server._paper_monitor_summary()

    assert summary["run_source"] == "paper_aligned_prod_shift1"
    assert summary["checkpoint"].endswith("paper_aligned_prod_shift1.json")
    assert summary["balance"] == 711.22


def test_live_safety_includes_dryrun_ledger_and_gate_summary(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir = tmp_path / "data" / "reports"
    checkpoint_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setenv("DASHBOARD_RUN_SOURCE", "live_dryrun_aligned_prod_shift1")

    _write_json(
        checkpoint_dir / "live_dryrun_aligned_prod_shift1_ledger.json",
        [
            {"status": "would_place", "would_place_order": True, "submitted": False},
            {"status": "blocked", "block_reason": "market_end_mismatch", "submitted": False},
        ],
    )
    _write_json(
        report_dir / "live_trade_gate_latest.json",
        {
            "ok": False,
            "ready_for_live_smoke": False,
            "blockers": ["balance_meets_minimum", "allowance_meets_minimum"],
            "account": {
                "authenticated": True,
                "orders_read_ok": True,
                "balance_read_ok": True,
                "allowance_read_ok": True,
                "open_orders_count": 0,
                "usdc_balance": 0.0,
                "min_allowance": 0.0,
                "allowance_count": 3,
                "min_allowance_spender": "spender-a",
            },
            "funding_requirements": {
                "required_min_balance_usdc": 10.0,
                "required_smoke_notional_usdc": 2.6,
                "required_min_allowance_usdc": 10.0,
                "balance_shortfall_usdc": 10.0,
                "smoke_notional_shortfall_usdc": 2.6,
                "allowance_shortfall_usdc": 10.0,
                "funding_ready": False,
            },
            "market_probes": [
                {"direction": "UP", "ok": True, "quote_executable": True},
                {"direction": "DOWN", "ok": True, "quote_executable": True},
            ],
            "checks": [
                {"name": "clob_account_authenticated", "ok": True},
                {"name": "account_balance_read_ok", "ok": True},
                {"name": "account_allowance_read_ok", "ok": True},
                {"name": "account_open_orders_read_ok", "ok": True},
                {"name": "balance_meets_minimum", "ok": False, "value": 0.0, "expected": ">= 10.0"},
                {"name": "allowance_meets_minimum", "ok": False, "value": 0.0, "expected": ">= 10.0"},
            ],
        },
    )

    summary = server._safety_report_summary()
    checks = {item["key"]: item for item in summary["checklist"]}

    assert summary["dryrun"]["ledger_count"] == 2
    assert summary["dryrun"]["would_place_count"] == 1
    assert summary["dryrun"]["blocked_count"] == 1
    assert summary["dryrun"]["submitted_count"] == 0
    assert summary["live_gate"]["available"] is True
    assert summary["live_gate"]["ready_for_live_smoke"] is False
    assert summary["live_gate"]["blockers"] == ["balance_meets_minimum", "allowance_meets_minimum"]
    assert summary["funding"]["balance"] == 0.0
    assert summary["funding"]["min_allowance"] == 0.0
    assert summary["funding"]["allowance_count"] == 3
    assert summary["funding"]["min_allowance_spender"] == "spender-a"
    assert summary["funding"]["balance_shortfall_usdc"] == 10.0
    assert summary["funding"]["smoke_notional_shortfall_usdc"] == 2.6
    assert summary["funding"]["allowance_shortfall_usdc"] == 10.0
    assert summary["funding"]["funding_ready"] is False
    assert checks["dryrun_no_submitted_orders"]["ok"] is True
    assert checks["live_trade_gate_ready"]["ok"] is False
    assert checks["minimum_balance"]["ok"] is False
    assert checks["minimum_balance"]["value"] == 0.0
    assert checks["minimum_allowance"]["ok"] is False
    assert checks["minimum_allowance"]["value"] == 0.0


def test_live_safety_funding_balance_uses_gate_usdc_balance(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir = tmp_path / "data" / "reports"
    checkpoint_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setattr(
        server,
        "_process_summary",
        lambda patterns: {"running": False, "matches": [], "started_at": None, "uptime_seconds": None, "patterns": patterns},
    )
    _write_json(
        report_dir / "polymarket_clob_allowance_audit_20260611_182608.json",
        {
            "checks": [
                {"name": "minimum_balance", "ok": False, "value": 0.0, "expected": ">= 5.0"},
                {"name": "minimum_allowance", "ok": False, "value": 0.0, "expected": ">= 5.0"},
                {"name": "readonly_get_balance", "ok": True},
                {"name": "readonly_get_balance_allowance", "ok": True},
                {"name": "readonly_authenticated_client", "ok": True},
            ],
            "network": {
                "calls": [
                    {"name": "get_balance_allowance", "balance": 0.0, "min_allowance": 0.0, "allowance_count": 3}
                ]
            },
        },
    )

    _write_json(
        report_dir / "live_trade_gate_latest.json",
        {
            "ok": True,
            "ready_for_live_smoke": True,
            "blockers": [],
            "account": {
                "authenticated": True,
                "orders_read_ok": True,
                "balance_read_ok": True,
                "allowance_read_ok": True,
                "open_orders_count": 0,
                "usdc_balance": 48.367711,
                "min_allowance": 15.0,
                "allowance_count": 3,
            },
            "funding_requirements": {"funding_ready": True},
            "market_probes": [{"direction": "UP", "ok": True, "quote_executable": True}],
            "checks": [
                {"name": "clob_account_authenticated", "ok": True},
                {"name": "account_balance_read_ok", "ok": True},
                {"name": "account_allowance_read_ok", "ok": True},
                {"name": "account_open_orders_read_ok", "ok": True},
                {"name": "balance_meets_minimum", "ok": True, "value": 48.367711, "expected": ">= 1.0"},
                {"name": "allowance_meets_minimum", "ok": True, "value": 15.0, "expected": ">= 1.0"},
            ],
        },
    )

    summary = server._safety_report_summary()
    checks = {item["key"]: item for item in summary["checklist"]}

    assert summary["funding"]["balance"] == 48.367711
    assert summary["funding"]["min_allowance"] == 15.0
    assert checks["minimum_balance"]["value"] == 48.367711
    assert checks["minimum_allowance"]["value"] == 15.0


def test_live_safety_funding_prefers_fresh_preflight_gate_account(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir = tmp_path / "data" / "reports"
    checkpoint_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setattr(
        server,
        "_process_summary",
        lambda patterns: {"running": False, "matches": [], "started_at": None, "uptime_seconds": None, "patterns": patterns},
    )

    _write_json(
        report_dir / "live_trade_gate_latest.json",
        {
            "ok": True,
            "ready_for_live_smoke": True,
            "blockers": [],
            "account": {
                "authenticated": True,
                "orders_read_ok": True,
                "balance_read_ok": True,
                "allowance_read_ok": True,
                "open_orders_count": 0,
                "usdc_balance": 48.867711,
                "min_allowance": 15.0,
                "allowance_count": 3,
            },
            "funding_requirements": {"funding_ready": True},
            "market_probes": [{"direction": "UP", "ok": True, "quote_executable": True}],
            "checks": [
                {"name": "clob_account_authenticated", "ok": True},
                {"name": "account_balance_read_ok", "ok": True},
                {"name": "account_allowance_read_ok", "ok": True},
                {"name": "account_open_orders_read_ok", "ok": True},
                {"name": "balance_meets_minimum", "ok": True, "value": 48.867711, "expected": ">= 1.0"},
                {"name": "allowance_meets_minimum", "ok": True, "value": 15.0, "expected": ">= 1.0"},
            ],
        },
    )
    _write_json(
        report_dir / "live_preflight_chain_latest.json",
        {
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "ok": True,
            "submitted": False,
            "blockers": [],
            "summary": {
                "gate_ready": True,
                "smoke_mode": "preview",
                "open_orders": 0,
                "settled": 0,
                "risk_ok": True,
            },
            "components": {
                "gate": {
                    "ok": True,
                    "ready_for_live_smoke": True,
                    "blockers": [],
                    "account": {
                        "authenticated": True,
                        "orders_read_ok": True,
                        "balance_read_ok": True,
                        "allowance_read_ok": True,
                        "open_orders_count": 0,
                        "usdc_balance": 48.367711,
                        "min_allowance": 25.0,
                        "allowance_count": 3,
                    },
                    "funding_requirements": {
                        "funding_ready": True,
                        "balance_shortfall_usdc": 0.0,
                        "allowance_shortfall_usdc": 0.0,
                    },
                    "market_probes": [{"direction": "UP", "ok": True, "quote_executable": True}],
                    "checks": [
                        {"name": "clob_account_authenticated", "ok": True},
                        {"name": "account_balance_read_ok", "ok": True},
                        {"name": "account_allowance_read_ok", "ok": True},
                        {"name": "account_open_orders_read_ok", "ok": True},
                        {"name": "balance_meets_minimum", "ok": True, "value": 48.367711, "expected": ">= 1.0"},
                        {"name": "allowance_meets_minimum", "ok": True, "value": 25.0, "expected": ">= 1.0"},
                    ],
                },
                "guarded_smoke": {"submitted": False, "mode": "preview"},
            },
        },
    )

    summary = server._safety_report_summary()
    checks = {item["key"]: item for item in summary["checklist"]}

    assert summary["funding"]["balance"] == 48.367711
    assert summary["funding"]["min_allowance"] == 25.0
    assert checks["minimum_balance"]["value"] == 48.367711
    assert checks["minimum_allowance"]["value"] == 25.0


def test_live_safety_funding_uses_latest_preflight_gate_account_even_when_stale(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir = tmp_path / "data" / "reports"
    checkpoint_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setattr(
        server,
        "_process_summary",
        lambda patterns: {"running": False, "matches": [], "started_at": None, "uptime_seconds": None, "patterns": patterns},
    )

    _write_json(
        report_dir / "live_trade_gate_latest.json",
        {
            "ok": True,
            "ready_for_live_smoke": True,
            "blockers": [],
            "account": {
                "authenticated": True,
                "orders_read_ok": True,
                "balance_read_ok": True,
                "allowance_read_ok": True,
                "open_orders_count": 0,
                "usdc_balance": 48.867711,
                "min_allowance": 15.0,
                "allowance_count": 3,
            },
            "funding_requirements": {"funding_ready": True},
            "market_probes": [{"direction": "UP", "ok": True, "quote_executable": True}],
            "checks": [
                {"name": "clob_account_authenticated", "ok": True},
                {"name": "account_balance_read_ok", "ok": True},
                {"name": "account_allowance_read_ok", "ok": True},
                {"name": "account_open_orders_read_ok", "ok": True},
                {"name": "balance_meets_minimum", "ok": True, "value": 48.867711, "expected": ">= 1.0"},
                {"name": "allowance_meets_minimum", "ok": True, "value": 15.0, "expected": ">= 1.0"},
            ],
        },
    )
    _write_json(
        report_dir / "live_preflight_chain_latest.json",
        {
            "created_at": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
            "ok": True,
            "submitted": False,
            "blockers": [],
            "summary": {
                "gate_ready": True,
                "smoke_mode": "preview",
                "open_orders": 0,
                "settled": 0,
                "risk_ok": True,
            },
            "components": {
                "gate": {
                    "ok": True,
                    "ready_for_live_smoke": True,
                    "blockers": [],
                    "account": {
                        "authenticated": True,
                        "orders_read_ok": True,
                        "balance_read_ok": True,
                        "allowance_read_ok": True,
                        "open_orders_count": 0,
                        "usdc_balance": 48.367711,
                        "min_allowance": 25.0,
                        "allowance_count": 3,
                    },
                    "funding_requirements": {
                        "funding_ready": True,
                        "balance_shortfall_usdc": 0.0,
                        "allowance_shortfall_usdc": 0.0,
                    },
                    "market_probes": [{"direction": "UP", "ok": True, "quote_executable": True}],
                    "checks": [
                        {"name": "clob_account_authenticated", "ok": True},
                        {"name": "account_balance_read_ok", "ok": True},
                        {"name": "account_allowance_read_ok", "ok": True},
                        {"name": "account_open_orders_read_ok", "ok": True},
                        {"name": "balance_meets_minimum", "ok": True, "value": 48.367711, "expected": ">= 1.0"},
                        {"name": "allowance_meets_minimum", "ok": True, "value": 25.0, "expected": ">= 1.0"},
                    ],
                },
                "guarded_smoke": {"submitted": False, "mode": "preview"},
            },
        },
    )

    summary = server._safety_report_summary()
    checks = {item["key"]: item for item in summary["checklist"]}

    assert summary["preflight_chain"]["fresh"] is False
    assert summary["funding"]["balance"] == 48.367711
    assert summary["funding"]["min_allowance"] == 25.0
    assert checks["minimum_balance"]["value"] == 48.367711
    assert checks["minimum_allowance"]["value"] == 25.0


def test_live_safety_includes_readiness_blocker_summary(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir = tmp_path / "data" / "reports"
    checkpoint_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setenv("DASHBOARD_RUN_SOURCE", "paper_live")
    monkeypatch.setattr(
        server,
        "RISK_LIMITS",
        {
            "max_daily_loss_usdc": 10.0,
            "max_daily_trades": 20,
            "max_consecutive_losses": 3,
            "max_open_or_pending_orders": 1,
        },
    )
    _write_json(
        checkpoint_dir / "paper_live.json",
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "open_orders": [{"status": "OPEN", "created_at": datetime.now(timezone.utc).isoformat()}],
        },
    )
    _write_json(
        checkpoint_dir / "live_real_orders_current_next.json",
        [
            {"status": "OPEN", "created_at": datetime.now(timezone.utc).isoformat()},
            {"status": "PENDING", "created_at": datetime.now(timezone.utc).isoformat()},
        ],
    )

    summary = server._safety_report_summary()
    readiness = summary["readiness_summary"]

    assert readiness["total"] == len(summary["checklist"])
    assert readiness["passed"] == sum(1 for item in summary["checklist"] if item["ok"])
    assert readiness["blockers"] == readiness["total"] - readiness["passed"]
    assert readiness["by_severity"]["critical"] >= 1
    assert readiness["by_severity"]["funding"] >= 1
    assert readiness["by_severity"]["risk"] >= 1
    assert readiness["critical_blockers"] == readiness["by_severity"]["critical"]
    assert readiness["ready"] is False
    assert readiness["top_blockers"]
    assert readiness["top_blockers"][0]["severity"] == "critical"
    assert {"key", "label", "severity", "value"}.issubset(readiness["top_blockers"][0])
    assert len(readiness["top_blockers"]) <= 6
    top_keys = [item["key"] for item in readiness["top_blockers"]]
    assert top_keys.index("clob_authenticated") < top_keys.index("account_read_ok")
    clob_blocker = next(item for item in readiness["top_blockers"] if item["key"] == "clob_authenticated")
    assert clob_blocker["action"] == "Run CLOB read-only audit"


def test_live_safety_includes_clob_readonly_audit_summary(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir = tmp_path / "data" / "reports"
    checkpoint_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setenv("DASHBOARD_RUN_SOURCE", "paper_live")

    _write_json(
        report_dir / "live_trade_gate_latest.json",
        {
            "ok": False,
            "ready_for_live_smoke": False,
            "blockers": ["one quote not executable"],
            "account": {
                "authenticated": True,
                "orders_read_ok": True,
                "balance_read_ok": True,
                "allowance_read_ok": True,
                "open_orders_count": 0,
                "usdc_balance": 12.5,
                "min_allowance": 15.0,
                "allowance_count": 3,
                "min_allowance_spender": "spender-a",
            },
            "funding_requirements": {
                "required_min_balance_usdc": 10.0,
                "required_smoke_notional_usdc": 2.6,
                "required_min_allowance_usdc": 10.0,
                "balance_shortfall_usdc": 0.0,
                "smoke_notional_shortfall_usdc": 0.0,
                "allowance_shortfall_usdc": 0.0,
                "funding_ready": True,
            },
            "market_probes": [
                {
                    "direction": "UP",
                    "ok": True,
                    "quote_executable": True,
                    "best_bid": 0.49,
                    "best_ask": 0.51,
                    "price": 0.5,
                    "token_id": "token-up",
                },
                {
                    "direction": "DOWN",
                    "ok": True,
                    "quote_executable": False,
                    "best_bid": 0.44,
                    "best_ask": 0.56,
                    "reason": "wide spread",
                    "token_id": "token-down",
                },
            ],
            "checks": [
                {"name": "clob_account_authenticated", "ok": True},
                {"name": "account_balance_read_ok", "ok": True},
                {"name": "account_allowance_read_ok", "ok": True},
                {"name": "account_open_orders_read_ok", "ok": True},
                {"name": "balance_meets_minimum", "ok": True, "value": 12.5, "expected": ">= 10.0"},
                {"name": "allowance_meets_minimum", "ok": True, "value": 15.0, "expected": ">= 10.0"},
            ],
        },
    )

    summary = server._safety_report_summary()
    audit = summary["clob_readonly"]

    assert audit["available"] is True
    assert audit["ready"] is False
    assert audit["authenticated"] is True
    assert audit["account_read_ok"] is True
    assert audit["allowance_read_ok"] is True
    assert audit["open_orders_read_ok"] is True
    assert audit["open_orders_count"] == 0
    assert audit["balance"] == 12.5
    assert audit["min_allowance"] == 15.0
    assert audit["allowance_count"] == 3
    assert audit["min_allowance_spender"] == "spender-a"
    assert audit["market_probe_count"] == 2
    assert audit["quote_executable_count"] == 1
    assert audit["quote_executable_rate"] == 0.5
    assert audit["status_reason"] == "quote_probe_blocked"
    assert audit["next_action"] == "Review CLOB quote probes"
    assert audit["quote_probes"] == [
        {
            "direction": "UP",
            "ok": True,
            "quote_executable": True,
            "best_bid": 0.49,
            "best_ask": 0.51,
            "price": 0.5,
            "token_id": "token-up",
            "reason": "",
            "error": "",
        },
        {
            "direction": "DOWN",
            "ok": True,
            "quote_executable": False,
            "best_bid": 0.44,
            "best_ask": 0.56,
            "price": None,
            "token_id": "token-down",
            "reason": "wide spread",
            "error": "",
        },
    ]
    assert audit["funding_ready"] is True
    assert audit["blockers"] == ["one quote not executable"]


def test_live_safety_clob_readonly_marks_stale_report(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir = tmp_path / "data" / "reports"
    checkpoint_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)

    report_path = report_dir / "live_trade_gate_latest.json"
    _write_json(
        report_path,
        {
            "ok": True,
            "ready_for_live_smoke": True,
            "blockers": [],
            "account": {
                "authenticated": True,
                "orders_read_ok": True,
                "balance_read_ok": True,
                "allowance_read_ok": True,
                "open_orders_count": 0,
                "usdc_balance": 12.5,
                "min_allowance": 15.0,
            },
            "funding_requirements": {"funding_ready": True},
            "market_probes": [
                {"direction": "UP", "ok": True, "quote_executable": True},
                {"direction": "DOWN", "ok": True, "quote_executable": True},
            ],
            "checks": [
                {"name": "clob_account_authenticated", "ok": True},
                {"name": "account_balance_read_ok", "ok": True},
                {"name": "account_allowance_read_ok", "ok": True},
                {"name": "account_open_orders_read_ok", "ok": True},
                {"name": "balance_meets_minimum", "ok": True},
                {"name": "allowance_meets_minimum", "ok": True},
            ],
        },
    )
    stale_ts = (datetime.now(timezone.utc) - timedelta(minutes=10)).timestamp()
    os.utime(report_path, (stale_ts, stale_ts))

    audit = server._safety_report_summary()["clob_readonly"]

    assert audit["report_mtime"]
    assert audit["report_age_seconds"] >= 600
    assert audit["fresh"] is False
    assert audit["max_age_seconds"] == server.CLOB_READONLY_MAX_AGE_SECONDS
    assert audit["ready"] is False
    assert audit["status_reason"] == "stale_report"
    assert audit["next_action"] == "Refresh live trade gate report"


def test_live_safety_clob_readonly_uses_fresh_account_refresh_when_gate_report_is_stale(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir = tmp_path / "data" / "reports"
    checkpoint_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setattr(server, "CLOB_READONLY_MAX_AGE_SECONDS", 300)

    gate_path = report_dir / "live_trade_gate_latest.json"
    _write_json(
        gate_path,
        {
            "ok": True,
            "ready_for_live_smoke": True,
            "blockers": [],
            "account": {
                "authenticated": True,
                "orders_read_ok": True,
                "balance_read_ok": True,
                "allowance_read_ok": True,
                "open_orders_count": 0,
                "usdc_balance": 12.5,
                "min_allowance": 15.0,
            },
            "funding_requirements": {
                "required_min_balance_usdc": 10.0,
                "required_smoke_notional_usdc": 2.6,
                "required_min_allowance_usdc": 10.0,
                "balance_shortfall_usdc": 0.0,
                "smoke_notional_shortfall_usdc": 0.0,
                "allowance_shortfall_usdc": 0.0,
                "funding_ready": True,
            },
            "market_probes": [
                {"direction": "UP", "ok": True, "quote_executable": True},
                {"direction": "DOWN", "ok": True, "quote_executable": True},
            ],
            "checks": [
                {"name": "clob_account_authenticated", "ok": True},
                {"name": "account_balance_read_ok", "ok": True},
                {"name": "account_allowance_read_ok", "ok": True},
                {"name": "account_open_orders_read_ok", "ok": True},
                {"name": "balance_meets_minimum", "ok": True},
                {"name": "allowance_meets_minimum", "ok": True},
            ],
        },
    )
    stale_ts = (datetime.now(timezone.utc) - timedelta(minutes=10)).timestamp()
    os.utime(gate_path, (stale_ts, stale_ts))

    def fake_snapshot():
        return {
            "ok": True,
            "source": "live_clob_account_refresh",
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "account": {
                "authenticated": True,
                "orders_read_ok": True,
                "balance_read_ok": True,
                "allowance_read_ok": True,
                "open_orders_count": 0,
                "open_orders": [],
                "usdc_balance": 84.022643,
                "min_allowance": 15.0,
                "allowance_count": 3,
            },
        }

    monkeypatch.setattr(server, "_live_clob_account_snapshot", fake_snapshot)

    audit = server._safety_report_summary()["clob_readonly"]

    assert audit["account_source"] == "live_clob_account_refresh"
    assert audit["fresh"] is True
    assert audit["ready"] is True
    assert audit["status_reason"] == "ready"
    assert audit["next_action"] == "Ready for guarded preflight"
    assert audit["report_age_seconds"] <= server.CLOB_READONLY_MAX_AGE_SECONDS


def test_live_safety_clob_readonly_marks_missing_report_next_action(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir = tmp_path / "data" / "reports"
    checkpoint_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)

    audit = server._safety_report_summary()["clob_readonly"]

    assert audit["available"] is False
    assert audit["ready"] is False
    assert audit["status_reason"] == "missing_report"
    assert audit["next_action"] == "Run live trade gate report"


def test_live_safety_clob_readonly_marks_ready_next_action(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir = tmp_path / "data" / "reports"
    checkpoint_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)

    _write_json(
        report_dir / "live_trade_gate_latest.json",
        {
            "ok": True,
            "ready_for_live_smoke": True,
            "blockers": [],
            "account": {
                "authenticated": True,
                "orders_read_ok": True,
                "balance_read_ok": True,
                "allowance_read_ok": True,
                "open_orders_count": 0,
                "usdc_balance": 12.5,
                "min_allowance": 15.0,
                "allowance_count": 3,
            },
            "funding_requirements": {"funding_ready": True},
            "market_probes": [
                {"direction": "UP", "ok": True, "quote_executable": True},
                {"direction": "DOWN", "ok": True, "quote_executable": True},
            ],
            "checks": [
                {"name": "clob_account_authenticated", "ok": True},
                {"name": "account_balance_read_ok", "ok": True},
                {"name": "account_allowance_read_ok", "ok": True},
                {"name": "account_open_orders_read_ok", "ok": True},
                {"name": "balance_meets_minimum", "ok": True},
                {"name": "allowance_meets_minimum", "ok": True},
            ],
        },
    )

    audit = server._safety_report_summary()["clob_readonly"]

    assert audit["ready"] is True
    assert audit["status_reason"] == "ready"
    assert audit["next_action"] == "Ready for guarded preflight"


def test_live_safety_clob_readonly_blocks_on_live_gate_blockers(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir = tmp_path / "data" / "reports"
    checkpoint_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)

    _write_json(
        report_dir / "live_trade_gate_latest.json",
        {
            "ok": False,
            "ready_for_live_smoke": False,
            "blockers": ["market closed"],
            "account": {
                "authenticated": True,
                "orders_read_ok": True,
                "balance_read_ok": True,
                "allowance_read_ok": True,
                "open_orders_count": 0,
                "usdc_balance": 12.5,
                "min_allowance": 15.0,
            },
            "funding_requirements": {"funding_ready": True},
            "market_probes": [
                {"direction": "UP", "ok": True, "quote_executable": True},
                {"direction": "DOWN", "ok": True, "quote_executable": True},
            ],
            "checks": [
                {"name": "clob_account_authenticated", "ok": True},
                {"name": "account_balance_read_ok", "ok": True},
                {"name": "account_allowance_read_ok", "ok": True},
                {"name": "account_open_orders_read_ok", "ok": True},
                {"name": "balance_meets_minimum", "ok": True},
                {"name": "allowance_meets_minimum", "ok": True},
            ],
        },
    )

    audit = server._safety_report_summary()["clob_readonly"]

    assert audit["ready"] is False
    assert audit["status_reason"] == "live_gate_blocked"
    assert audit["next_action"] == "Clear live gate blockers"


def test_live_safety_includes_first_order_rail(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir = tmp_path / "data" / "reports"
    checkpoint_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setenv("DASHBOARD_RUN_SOURCE", "paper_live")

    summary = server._safety_report_summary()
    rail = summary["first_order_rail"]
    stages = rail["stages"]

    assert [stage["key"] for stage in stages] == [
        "dry_run",
        "read_only_audit",
        "funding",
        "preflight",
        "tiny_guarded_order",
        "settle_tracking",
    ]
    assert rail["current_key"] == "read_only_audit"
    assert rail["ready_for_manual_confirmation"] is False
    assert stages[0]["status"] == "complete"
    assert stages[1]["status"] == "blocked"
    assert stages[1]["action"] == "Run CLOB read-only audit"
    assert "clob_authenticated" in stages[1]["blocker_keys"]
    guarded = next(stage for stage in stages if stage["key"] == "tiny_guarded_order")
    assert guarded["requires_confirmation"] is True
    assert guarded["status"] == "waiting"
    assert guarded["action"] == "Wait for explicit user confirmation"

    operator = summary["operator_summary"]
    assert operator["ready"] is False
    assert operator["status"] == "blocked"
    assert operator["current_stage_key"] == "read_only_audit"
    assert operator["current_stage_label"] == "CLOB read-only audit"
    assert operator["next_action"] == "Run CLOB read-only audit"
    assert operator["primary_blocker_key"] == "clob_authenticated"
    assert operator["primary_blocker"] == "CLOB authenticated"
    assert operator["critical_blockers"] > 0
    assert operator["readiness_passed"] == summary["readiness_summary"]["passed"]
    assert operator["readiness_total"] == summary["readiness_summary"]["total"]


def test_live_safety_first_order_rail_requires_manual_confirmation_when_ready(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir = tmp_path / "data" / "reports"
    checkpoint_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setenv("DASHBOARD_RUN_SOURCE", "paper_live")
    monkeypatch.setattr(server, "_btc_live_now", lambda: 1000.0)
    monkeypatch.setattr(
        server,
        "BTC_LIVE_CACHE",
        {
            "price": 63408.46,
            "timestamp": datetime.fromtimestamp(995, tz=timezone.utc).isoformat(),
            "received_at": 997.0,
            "source": "polymarket_rtds_chainlink",
            "status": "fresh",
            "error": None,
        },
    )

    _write_json(
        report_dir / "live_trade_gate_latest.json",
        {
            "ok": True,
            "ready_for_live_smoke": True,
            "blockers": [],
            "account": {
                "authenticated": True,
                "orders_read_ok": True,
                "balance_read_ok": True,
                "allowance_read_ok": True,
                "open_orders_count": 0,
                "usdc_balance": 12.0,
                "min_allowance": 12.0,
                "allowance_count": 3,
            },
            "funding_requirements": {
                "required_min_balance_usdc": 10.0,
                "required_smoke_notional_usdc": 2.6,
                "required_min_allowance_usdc": 10.0,
                "balance_shortfall_usdc": 0.0,
                "smoke_notional_shortfall_usdc": 0.0,
                "allowance_shortfall_usdc": 0.0,
                "funding_ready": True,
            },
            "checks": [
                {"name": "clob_account_authenticated", "ok": True},
                {"name": "account_balance_read_ok", "ok": True},
                {"name": "account_allowance_read_ok", "ok": True},
                {"name": "account_open_orders_read_ok", "ok": True},
                {"name": "balance_meets_minimum", "ok": True, "value": 12.0, "expected": ">= 10.0"},
                {"name": "allowance_meets_minimum", "ok": True, "value": 12.0, "expected": ">= 10.0"},
            ],
        },
    )
    _write_json(
        report_dir / "live_preflight_chain_latest.json",
        {
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "ok": True,
            "submitted": False,
            "blockers": [],
            "summary": {
                "gate_ready": True,
                "smoke_mode": "preview",
                "open_orders": 0,
                "settled": 0,
                "risk_ok": True,
            },
        },
    )

    summary = server._safety_report_summary()
    rail = summary["first_order_rail"]
    stages = {stage["key"]: stage for stage in rail["stages"]}

    assert rail["current_key"] == "tiny_guarded_order"
    assert rail["ready_for_manual_confirmation"] is True
    assert stages["preflight"]["status"] == "complete"
    assert stages["tiny_guarded_order"]["status"] == "manual"
    assert stages["tiny_guarded_order"]["requires_confirmation"] is True
    assert stages["tiny_guarded_order"]["action"] == "Wait for explicit user confirmation"
    assert stages["settle_tracking"]["status"] == "waiting"

    operator = summary["operator_summary"]
    assert operator["ready"] is True
    assert operator["status"] == "manual_confirmation"
    assert operator["current_stage_key"] == "tiny_guarded_order"
    assert operator["current_stage_label"] == "Tiny guarded order"
    assert operator["next_action"] == "Wait for explicit user confirmation"
    assert operator["primary_blocker_key"] is None
    assert operator["primary_blocker"] is None
    assert operator["critical_blockers"] == 0
    assert operator["readiness_passed"] == operator["readiness_total"]


def test_live_safety_marks_dryrun_submitted_order_as_critical(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir = tmp_path / "data" / "reports"
    checkpoint_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setenv("DASHBOARD_RUN_SOURCE", "live_dryrun_aligned_prod_shift1")

    _write_json(
        checkpoint_dir / "live_dryrun_aligned_prod_shift1_ledger.json",
        [{"status": "would_place", "would_place_order": True, "submitted": True}],
    )

    summary = server._safety_report_summary()
    checks = {item["key"]: item for item in summary["checklist"]}

    assert summary["dryrun"]["submitted_count"] == 1
    assert checks["dryrun_no_submitted_orders"]["ok"] is False
    assert checks["dryrun_no_submitted_orders"]["severity"] == "critical"


def test_live_safety_prefers_live_gate_funding_over_older_allowance_audit(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir = tmp_path / "data" / "reports"
    checkpoint_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)

    _write_json(
        report_dir / "polymarket_clob_allowance_audit_old.json",
        {
            "checks": [
                {"name": "minimum_balance", "ok": False, "value": 0.0, "expected": ">= 5.0"},
                {"name": "minimum_allowance", "ok": False, "value": 0.0, "expected": ">= 5.0"},
                {"name": "readonly_get_balance", "ok": True},
                {"name": "readonly_get_balance_allowance", "ok": True},
                {"name": "readonly_authenticated_client", "ok": True},
            ],
            "network": {
                "calls": [
                    {"name": "get_balance_allowance", "balance": 0.0, "min_allowance": 0.0, "allowance_count": 3}
                ]
            },
        },
    )
    _write_json(
        report_dir / "live_trade_gate_latest.json",
        {
            "ok": False,
            "ready_for_live_smoke": False,
            "blockers": ["balance_meets_minimum", "allowance_meets_minimum"],
            "account": {
                "authenticated": True,
                "orders_read_ok": True,
                "balance_read_ok": True,
                "allowance_read_ok": True,
                "open_orders_count": 0,
                "usdc_balance": 0.0,
                "min_allowance": 0.0,
                "allowance_count": 3,
            },
            "funding_requirements": {
                "required_min_balance_usdc": 10.0,
                "required_smoke_notional_usdc": 2.6,
                "required_min_allowance_usdc": 10.0,
                "balance_shortfall_usdc": 10.0,
                "smoke_notional_shortfall_usdc": 2.6,
                "allowance_shortfall_usdc": 10.0,
                "funding_ready": False,
            },
            "checks": [
                {"name": "clob_account_authenticated", "ok": True},
                {"name": "account_balance_read_ok", "ok": True},
                {"name": "account_allowance_read_ok", "ok": True},
                {"name": "account_open_orders_read_ok", "ok": True},
                {"name": "balance_meets_minimum", "ok": False, "value": 0.0, "expected": ">= 10.0"},
                {"name": "allowance_meets_minimum", "ok": False, "value": 0.0, "expected": ">= 10.0"},
            ],
        },
    )

    summary = server._safety_report_summary()
    checks = {item["key"]: item for item in summary["checklist"]}

    assert summary["funding"]["balance_expected"] == ">= 10.0"
    assert summary["funding"]["allowance_expected"] == ">= 10.0"
    assert checks["minimum_balance"]["expected"] == ">= 10.0"
    assert checks["minimum_allowance"]["expected"] == ">= 10.0"


def test_live_safety_refreshes_stale_gate_account_balance(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir = tmp_path / "data" / "reports"
    checkpoint_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setattr(server, "CLOB_READONLY_MAX_AGE_SECONDS", 300)

    gate_path = report_dir / "live_trade_gate_latest.json"
    _write_json(
        gate_path,
        {
            "ok": True,
            "ready_for_live_smoke": True,
            "blockers": [],
            "account": {
                "authenticated": True,
                "orders_read_ok": True,
                "balance_read_ok": True,
                "allowance_read_ok": True,
                "open_orders_count": 0,
                "usdc_balance": 48.867711,
                "min_allowance": 15.0,
                "allowance_count": 3,
            },
            "funding_requirements": {
                "required_min_balance_usdc": 10.0,
                "required_smoke_notional_usdc": 2.6,
                "required_min_allowance_usdc": 10.0,
                "balance_shortfall_usdc": 0.0,
                "smoke_notional_shortfall_usdc": 0.0,
                "allowance_shortfall_usdc": 0.0,
                "funding_ready": True,
            },
            "checks": [
                {"name": "clob_account_authenticated", "ok": True},
                {"name": "account_balance_read_ok", "ok": True},
                {"name": "account_allowance_read_ok", "ok": True},
                {"name": "account_open_orders_read_ok", "ok": True},
                {"name": "balance_meets_minimum", "ok": True, "value": 48.867711, "expected": ">= 10.0"},
                {"name": "allowance_meets_minimum", "ok": True, "value": 15.0, "expected": ">= 10.0"},
            ],
        },
    )
    old_ts = (datetime.now(timezone.utc) - timedelta(seconds=900)).timestamp()
    os.utime(gate_path, (old_ts, old_ts))

    def fake_snapshot():
        return {
            "ok": True,
            "source": "live_clob_account_refresh",
            "account": {
                "authenticated": True,
                "orders_read_ok": True,
                "balance_read_ok": True,
                "allowance_read_ok": True,
                "open_orders_count": 0,
                "usdc_balance": 40.698765,
                "min_allowance": 15.0,
                "allowance_count": 3,
                "min_allowance_spender": "spender-a",
            },
        }

    monkeypatch.setattr(server, "_live_clob_account_snapshot", fake_snapshot)

    summary = server._safety_report_summary()

    assert summary["funding"]["balance"] == 40.698765
    assert summary["clob_readonly"]["balance"] == 40.698765
    assert summary["clob_readonly"]["account_source"] == "live_clob_account_refresh"
    assert summary["funding"]["balance_shortfall_usdc"] == 0.0


def test_clob_local_env_overrides_stale_process_clob_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOB_API_KEY", "stale-key")
    env_path = tmp_path / ".env.clob.local"
    env_path.write_text("CLOB_API_KEY=fresh-key\nDASHBOARD_ONLY=keep-process\n", encoding="utf-8")

    server._load_env_file(env_path, override_keys=server.CLOB_ENV_KEYS)

    assert os.environ["CLOB_API_KEY"] == "fresh-key"
    assert os.environ["DASHBOARD_ONLY"] == "keep-process"


def test_live_safety_surfaces_current_clob_open_orders(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir = tmp_path / "data" / "reports"
    checkpoint_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setattr(server, "CLOB_READONLY_MAX_AGE_SECONDS", 300)

    gate_path = report_dir / "live_trade_gate_latest.json"
    _write_json(
        gate_path,
        {
            "ok": True,
            "ready_for_live_smoke": True,
            "blockers": [],
            "account": {
                "authenticated": True,
                "orders_read_ok": True,
                "balance_read_ok": True,
                "allowance_read_ok": True,
                "open_orders_count": 0,
                "usdc_balance": 40.0,
                "min_allowance": 15.0,
                "allowance_count": 3,
            },
            "funding_requirements": {"funding_ready": True},
            "checks": [
                {"name": "clob_account_authenticated", "ok": True},
                {"name": "account_balance_read_ok", "ok": True},
                {"name": "account_allowance_read_ok", "ok": True},
                {"name": "account_open_orders_read_ok", "ok": True},
                {"name": "balance_meets_minimum", "ok": True, "value": 40.0, "expected": ">= 10.0"},
                {"name": "allowance_meets_minimum", "ok": True, "value": 15.0, "expected": ">= 10.0"},
            ],
        },
    )
    old_ts = (datetime.now(timezone.utc) - timedelta(seconds=900)).timestamp()
    os.utime(gate_path, (old_ts, old_ts))

    def fake_snapshot():
        return {
            "ok": True,
            "source": "live_clob_account_refresh",
            "account": {
                "authenticated": True,
                "orders_read_ok": True,
                "balance_read_ok": True,
                "allowance_read_ok": True,
                "open_orders_count": 1,
                "open_orders": [
                    {
                        "id": "0xabc",
                        "market": "btc-updown-5m-1781466600",
                        "asset_id": "token-down",
                        "side": "BUY",
                        "outcome": "Down",
                        "price": "0.46",
                        "original_size": "5",
                        "size_matched": "1",
                        "status": "LIVE",
                        "created_at": "2026-06-14T19:50:00Z",
                        "expiration": "2026-06-14T19:55:00Z",
                    }
                ],
                "usdc_balance": 40.698765,
                "min_allowance": 15.0,
                "allowance_count": 3,
            },
        }

    monkeypatch.setattr(server, "_live_clob_account_snapshot", fake_snapshot)

    audit = server._safety_report_summary()["clob_readonly"]

    assert audit["open_orders_count"] == 1
    assert audit["open_orders"][0]["order_id"] == "0xabc"
    assert audit["open_orders"][0]["market"] == "btc-updown-5m-1781466600"
    assert audit["open_orders"][0]["price"] == 0.46
    assert audit["open_orders"][0]["original_size"] == 5.0
    assert audit["open_orders"][0]["matched_size"] == 1.0
    assert audit["open_orders"][0]["remaining_size"] == 4.0


def test_live_safety_includes_polymarket_account_activity_report(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir = tmp_path / "data" / "reports"
    checkpoint_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    _write_json(checkpoint_dir / "live_real_orders_current_next.json", [])
    _write_json(
        report_dir / "polymarket_account_activity_latest.json",
        {
            "ok": True,
            "source": "polymarket_data_api",
            "created_at": "2026-06-17T10:52:30Z",
            "user": "0xfunder",
            "summary": {
                "activity_count": 2,
                "positions_count": 1,
                "trade_count": 2,
                "redeem_count": 0,
            },
            "activity": [
                {
                    "timestamp": 1781692189,
                    "type": "TRADE",
                    "side": "BUY",
                    "outcome": "Down",
                    "price": 0.49,
                    "size": 2.97,
                    "usdcSize": 1.4553,
                    "slug": "btc-updown-5m-1781692200",
                    "title": "Bitcoin Up or Down",
                    "transactionHash": "0xtrade",
                }
            ],
            "positions": [
                {
                    "slug": "btc-updown-5m-1781692200",
                    "title": "Bitcoin Up or Down",
                    "outcome": "Down",
                    "size": 5,
                    "avgPrice": 0.49,
                    "currentValue": 1.05,
                    "cashPnl": -2.45,
                    "redeemable": True,
                }
            ],
        },
    )

    summary = server._safety_report_summary()
    account = summary["live_real"]["account_activity"]

    assert account["available"] is True
    assert account["ok"] is True
    assert account["user"] == "0xfunder"
    assert account["summary"]["trade_count"] == 2
    assert account["summary"]["active_positions_count"] == 0
    assert account["summary"]["redeemable_positions_count"] == 1
    assert account["portfolio"]["cash_balance_usdc"] == 0.0
    assert account["portfolio"]["positions_value_usdc"] == 1.05
    assert account["portfolio"]["total_value_usdc"] == 1.05
    assert account["recent_activity"][0]["slug"] == "btc-updown-5m-1781692200"
    assert account["recent_activity"][0]["usdc_size"] == 1.4553
    assert account["positions"] == []
    assert account["redeemable_positions"][0]["current_value"] == 1.05
    assert account["redeemable_positions"][0]["cash_pnl"] == -2.45


def test_live_safety_includes_preflight_chain_summary(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir = tmp_path / "data" / "reports"
    checkpoint_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)

    _write_json(
        report_dir / "live_preflight_chain_latest.json",
        {
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "ok": False,
            "submitted": False,
            "blockers": ["balance_meets_minimum", "live trade gate not ready"],
            "summary": {
                "gate_ready": False,
                "smoke_mode": "blocked",
                "open_orders": 0,
                "settled": 1,
                "risk_ok": True,
            },
            "components": {
                "guarded_smoke": {"submitted": False, "mode": "blocked"},
                "settlement_reconciliation": {"settled_count": 1},
            },
        },
    )

    summary = server._safety_report_summary()
    checks = {item["key"]: item for item in summary["checklist"]}

    assert summary["preflight_chain"]["available"] is True
    assert summary["preflight_chain"]["ok"] is False
    assert summary["preflight_chain"]["submitted"] is False
    assert summary["preflight_chain"]["created_at"]
    assert 0 <= summary["preflight_chain"]["age_seconds"] < 30
    assert summary["preflight_chain"]["fresh"] is True
    assert summary["preflight_chain"]["blockers"] == ["balance_meets_minimum", "live trade gate not ready"]
    assert summary["preflight_chain"]["smoke_mode"] == "blocked"
    assert summary["preflight_chain"]["settled"] == 1
    assert summary["reports"]["live_preflight"].endswith("live_preflight_chain_latest.json")
    assert checks["live_preflight_available"]["ok"] is True
    assert checks["live_preflight_fresh"]["ok"] is True
    assert checks["live_preflight_chain_ok"]["ok"] is False
    assert checks["live_preflight_no_submission"]["ok"] is True


def test_live_safety_includes_report_refresh_summary(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir = tmp_path / "data" / "reports"
    checkpoint_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)

    gate_path = report_dir / "live_trade_gate_latest.json"
    _write_json(
        gate_path,
        {
            "ok": False,
            "ready_for_live_smoke": False,
            "blockers": ["balance_meets_minimum"],
            "account": {
                "authenticated": True,
                "orders_read_ok": True,
                "balance_read_ok": True,
                "allowance_read_ok": True,
                "open_orders_count": 0,
            },
            "checks": [
                {"name": "clob_account_authenticated", "ok": True},
                {"name": "account_balance_read_ok", "ok": True},
                {"name": "account_allowance_read_ok", "ok": True},
                {"name": "account_open_orders_read_ok", "ok": True},
            ],
        },
    )
    _write_json(
        report_dir / "live_preflight_chain_latest.json",
        {
            "created_at": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
            "ok": True,
            "submitted": False,
            "blockers": [],
            "summary": {"gate_ready": True, "smoke_mode": "preview", "open_orders": 0, "settled": 0, "risk_ok": True},
        },
    )

    summary = server._safety_report_summary()
    refresh = summary["report_refresh"]
    legacy_refresh = summary["legacy_report_refresh"]
    items = {item["key"]: item for item in refresh["items"]}
    legacy_items = {item["key"]: item for item in legacy_refresh["items"]}

    assert refresh["ready"] is False
    assert refresh["total"] == 4
    assert "live_order_sync" in items
    assert "polymarket_account_activity" in items
    assert "live_alerts" in items
    assert items["formal_live"]["status"] == "missing"
    assert "live_gate" not in items
    assert "live_preflight" not in items
    assert "clob_readonly" not in items
    assert legacy_refresh["total"] == 3
    assert legacy_items["live_gate"]["report"].endswith("live_trade_gate_latest.json")
    assert legacy_items["live_gate"]["available"] is True
    assert legacy_items["live_gate"]["fresh"] is True
    assert legacy_items["live_gate"]["status"] == "blocked"
    assert legacy_items["live_gate"]["next_action"] == "Clear live gate blockers"
    assert legacy_items["live_gate"]["blockers"] == ["balance_meets_minimum"]
    assert legacy_items["live_preflight"]["fresh"] is False
    assert legacy_items["live_preflight"]["status"] == "stale"
    assert legacy_items["live_preflight"]["next_action"] == "Refresh live preflight chain"
    assert legacy_items["clob_readonly"]["status"] == "blocked"
    assert legacy_items["clob_readonly"]["next_action"] == "Run live trade gate report"


def test_live_safety_includes_market_data_freshness(monkeypatch):
    monkeypatch.setattr(server, "_btc_live_now", lambda: 1000.0)
    monkeypatch.setattr(
        server,
        "BTC_LIVE_CACHE",
        {
            "price": 63408.46,
            "timestamp": datetime.fromtimestamp(995, tz=timezone.utc).isoformat(),
            "received_at": 997.0,
            "source": "polymarket_rtds_chainlink",
            "status": "fresh",
            "error": None,
        },
    )

    market_data = server._safety_report_summary()["market_data"]

    assert market_data["ready"] is True
    assert market_data["source"] == "polymarket_rtds_chainlink"
    assert market_data["status"] == "fresh"
    assert market_data["price"] == 63408.46
    assert market_data["price_age_seconds"] == 5
    assert market_data["received_age_seconds"] == 3
    assert market_data["next_action"] == "Market data fresh"


def test_live_safety_marks_stale_market_data(monkeypatch):
    monkeypatch.setattr(server, "_btc_live_now", lambda: 1000.0)
    monkeypatch.setattr(
        server,
        "BTC_LIVE_CACHE",
        {
            "price": 63408.46,
            "timestamp": datetime.fromtimestamp(900, tz=timezone.utc).isoformat(),
            "received_at": 940.0,
            "source": "polymarket_rtds_chainlink",
            "status": "fresh",
            "error": None,
        },
    )

    summary = server._safety_report_summary()
    market_data = summary["market_data"]
    checks = {item["key"]: item for item in summary["checklist"]}

    assert market_data["ready"] is False
    assert market_data["status"] == "stale"
    assert market_data["price_age_seconds"] == 100
    assert market_data["received_age_seconds"] == 60
    assert market_data["next_action"] == "Refresh Chainlink live price feed"
    assert checks["market_data_fresh"]["ok"] is False
    assert checks["market_data_fresh"]["severity"] == "critical"
    assert checks["market_data_fresh"]["expected"] == f"<= {server.BTC_LIVE_MAX_PRICE_AGE_SECONDS}s price, <= {server.BTC_LIVE_MAX_RECEIVED_AGE_SECONDS}s received"
    assert any(item["key"] == "market_data_fresh" for item in summary["readiness_summary"]["top_blockers"])


def test_live_safety_first_order_rail_blocks_on_stale_market_data(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir = tmp_path / "data" / "reports"
    checkpoint_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setattr(server, "_btc_live_now", lambda: 1000.0)
    monkeypatch.setattr(
        server,
        "BTC_LIVE_CACHE",
        {
            "price": 63408.46,
            "timestamp": datetime.fromtimestamp(900, tz=timezone.utc).isoformat(),
            "received_at": 940.0,
            "source": "polymarket_rtds_chainlink",
            "status": "fresh",
            "error": None,
        },
    )

    _write_json(
        report_dir / "live_trade_gate_latest.json",
        {
            "ok": True,
            "ready_for_live_smoke": True,
            "blockers": [],
            "account": {
                "authenticated": True,
                "orders_read_ok": True,
                "balance_read_ok": True,
                "allowance_read_ok": True,
                "open_orders_count": 0,
                "usdc_balance": 12.5,
                "min_allowance": 15.0,
            },
            "funding_requirements": {"funding_ready": True},
            "market_probes": [
                {"direction": "UP", "ok": True, "quote_executable": True},
                {"direction": "DOWN", "ok": True, "quote_executable": True},
            ],
            "checks": [
                {"name": "clob_account_authenticated", "ok": True},
                {"name": "account_balance_read_ok", "ok": True},
                {"name": "account_allowance_read_ok", "ok": True},
                {"name": "account_open_orders_read_ok", "ok": True},
                {"name": "balance_meets_minimum", "ok": True},
                {"name": "allowance_meets_minimum", "ok": True},
            ],
        },
    )
    _write_json(
        report_dir / "live_preflight_chain_latest.json",
        {
            "created_at": datetime.fromtimestamp(995, tz=timezone.utc).isoformat(),
            "ok": True,
            "submitted": False,
            "blockers": [],
            "summary": {"gate_ready": True, "smoke_mode": "preview", "open_orders": 0, "settled": 0, "risk_ok": True},
        },
    )

    rail = server._safety_report_summary()["first_order_rail"]
    read_only_stage = next(stage for stage in rail["stages"] if stage["key"] == "read_only_audit")

    assert rail["current_key"] == "read_only_audit"
    assert read_only_stage["status"] == "blocked"
    assert "Market data fresh" in read_only_stage["blockers"]


def test_live_safety_marks_stale_preflight_report_as_critical(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir = tmp_path / "data" / "reports"
    checkpoint_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)

    _write_json(
        report_dir / "live_preflight_chain_latest.json",
        {
            "created_at": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
            "ok": True,
            "submitted": False,
            "blockers": [],
            "summary": {"gate_ready": True, "smoke_mode": "preview", "open_orders": 0, "settled": 0, "risk_ok": True},
        },
    )

    summary = server._safety_report_summary()
    checks = {item["key"]: item for item in summary["checklist"]}

    assert summary["preflight_chain"]["fresh"] is False
    assert summary["preflight_chain"]["age_seconds"] >= 600
    assert checks["live_preflight_fresh"]["ok"] is False
    assert checks["live_preflight_fresh"]["severity"] == "critical"


def test_live_safety_includes_today_cockpit_summary(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir = tmp_path / "data" / "reports"
    audit_dir = tmp_path / "data" / "events" / "paper_live" / "today"
    shadow_dir = tmp_path / "data" / "events" / "shadow_live" / "today"
    checkpoint_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    audit_dir.mkdir(parents=True)
    shadow_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setenv("DASHBOARD_RUN_SOURCE", "paper_live")
    monkeypatch.setenv("DASHBOARD_MAKER_TARGET_PRICE", "0.49")
    monkeypatch.setattr(
        server,
        "RISK_LIMITS",
        {
            "max_daily_loss_usdc": 20.0,
            "max_daily_trades": 10,
            "max_consecutive_losses": 4,
            "max_open_or_pending_orders": 3,
        },
    )

    now = datetime.now(timezone.utc)
    day_info = server._dashboard_day_info(now=now)
    today_prefix = day_info["day_iso"]
    yesterday = day_info["start_utc"] - timedelta(hours=1)
    checkpoint = {
        "timestamp": now.isoformat(),
        "trades": [
            {
                "status": "SETTLED",
                "won": True,
                "pnl": 26.02,
                "settled_at": f"{today_prefix}T01:00:00Z",
            },
            {
                "status": "SETTLED",
                "won": False,
                "pnl": -25.0,
                "settled_at": f"{today_prefix}T02:00:00Z",
            },
            {
                "status": "SETTLED",
                "won": True,
                "pnl": 26.02,
                "settled_at": yesterday.isoformat(),
            },
        ],
        "pending_orders": [
            {
                "status": "PENDING",
                "entry_bar": 10,
                "settle_bar": 23,
                "created_at": f"{today_prefix}T03:00:00Z",
            }
        ],
        "open_orders": [
            {
                "status": "OPEN",
                "entry_bar": 12,
                "created_at": f"{today_prefix}T03:05:00Z",
            }
        ],
    }
    _write_json(checkpoint_dir / "paper_live.json", checkpoint)
    _write_json(
        checkpoint_dir / "paper_live_ledger.json",
        [
            {
                "status": "would_place",
                "action": "BUY_UP",
                "would_place_order": True,
                "submitted": False,
                "created_at": f"{today_prefix}T00:10:00Z",
            },
            {
                "status": "blocked",
                "action": "BUY_DOWN",
                "would_place_order": False,
                "submitted": False,
                "block_reason": "stale_signal",
                "created_at": f"{today_prefix}T00:15:00Z",
            },
            {
                "status": "would_place",
                "action": "BUY_UP",
                "would_place_order": True,
                "submitted": True,
                "created_at": f"{today_prefix}T00:20:00Z",
            },
            {
                "status": "would_place",
                "action": "BUY_DOWN",
                "would_place_order": True,
                "submitted": False,
                "created_at": yesterday.isoformat(),
            },
        ],
    )

    decision_events = [
        {
            "type": "decision",
            "event_id": "decision-pass",
            "action": "BUY_UP",
            "filt": True,
            "executable": True,
            "_t": f"{today_prefix}T00:00:00Z",
        },
        {
            "type": "decision",
            "event_id": "decision-block",
            "action": "HOLD",
            "filt": False,
            "executable": False,
            "block_reason": "macro_gate",
            "_t": f"{today_prefix}T00:05:00Z",
        },
        {
            "type": "decision",
            "event_id": "decision-old",
            "action": "BUY_DOWN",
            "filt": True,
            "executable": True,
            "_t": yesterday.isoformat(),
        },
    ]
    (checkpoint_dir / "paper_live_events.jsonl").write_text(
        "\n".join(json.dumps(event) for event in decision_events),
        encoding="utf-8",
    )
    shadow_events = [
        {
            "event_id": "watch-open",
            "type": "shadow_watch",
            "decision_event_id": "decision-pass",
            "sample": 0,
            "action": "OPEN",
            "price": 0.49,
            "best_bid": 0.49,
            "_t": f"{today_prefix}T00:00:01Z",
        },
        {
            "event_id": "watch-block",
            "type": "shadow_watch",
            "decision_event_id": "decision-pass",
            "sample": 1,
            "action": "BLOCK",
            "price": 0.51,
            "best_bid": 0.52,
            "reason": "price cap",
            "_t": f"{today_prefix}T00:00:02Z",
        },
        {
            "event_id": "watch-error",
            "type": "shadow_watch_error",
            "decision_event_id": "decision-pass",
            "error": "timeout",
            "_t": f"{today_prefix}T00:00:03Z",
        },
        {
            "event_id": "watch-old",
            "type": "shadow_watch",
            "decision_event_id": "decision-old",
            "sample": 0,
            "action": "OPEN",
            "price": 0.47,
            "best_bid": 0.47,
            "_t": yesterday.isoformat(),
        },
        {
            "event_id": "watch-old-error",
            "type": "shadow_watch_error",
            "decision_event_id": "decision-old",
            "error": "old timeout",
            "_t": yesterday.isoformat(),
        },
    ]
    (shadow_dir / "events_00.jsonl").write_text(
        "\n".join(json.dumps(event) for event in shadow_events),
        encoding="utf-8",
    )

    summary = server._safety_report_summary()
    today = summary["today"]

    assert today["day"] == today_prefix
    assert today["day_utc"] == today_prefix
    assert today["day_tz"] == "Asia/Shanghai"
    assert today["day_start_utc"] == day_info["start_utc_iso"]
    assert today["day_end_utc"] == day_info["end_utc_iso"]
    assert today["signals"]["total"] == 2
    assert today["signals"]["passed"] == 1
    assert today["signals"]["blocked"] == 1
    assert today["signals"]["pass_rate"] == 0.5
    assert today["signals"]["buy_up"] == 1
    assert today["signals"]["buy_down"] == 0
    assert today["signals"]["hold"] == 1
    assert today["signals"]["other"] == 0
    assert today["signals"]["top_block_reason"] == "macro_gate"
    assert today["signals"]["top_block_count"] == 1
    assert today["trades"]["settled"] == 2
    assert today["trades"]["wins"] == 1
    assert today["trades"]["losses"] == 1
    assert today["trades"]["win_rate"] == 0.5
    assert today["trades"]["pnl_usdc"] == 1.02
    assert today["trades"]["pending"] == 1
    assert today["trades"]["open"] == 1
    assert today["risk_usage"]["daily_loss"] == 0.0
    assert today["risk_usage"]["daily_trades"] == 0.2
    assert today["risk_usage"]["open_or_pending"] == 0.6667
    assert today["maker"]["target_price"] == 0.49
    assert today["maker"]["observed_avg_target_price"] == 0.49
    assert today["maker"]["buy_one_rate"] == 0.5
    assert today["maker"]["blocks"] == 1
    assert today["maker"]["api_errors"] == 1
    assert today["dryrun"]["records"] == 3
    assert today["dryrun"]["would_place"] == 2
    assert today["dryrun"]["blocked"] == 1
    assert today["dryrun"]["submitted"] == 1
    assert today["dryrun"]["latest_status"] == "would_place"
    assert today["dryrun"]["latest_action"] == "BUY_UP"
    assert today["dryrun"]["latest_block_reason"] == "stale_signal"
    assert today["activity"]["latest_signal_at"] == f"{today_prefix}T00:05:00Z"
    assert today["activity"]["latest_trade_at"] == f"{today_prefix}T02:00:00Z"
    assert today["activity"]["latest_order_at"] == f"{today_prefix}T03:05:00Z"
    assert today["activity"]["latest_dryrun_at"] == f"{today_prefix}T00:20:00Z"
    assert today["activity"]["latest_signal_age_seconds"] >= 0
    assert today["activity"]["latest_trade_age_seconds"] >= 0
    assert today["activity"]["latest_order_age_seconds"] >= 0
    assert today["activity"]["latest_dryrun_age_seconds"] >= 0


def test_live_safety_backfills_today_summary_from_dashboard_db_when_checkpoint_is_empty(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir = tmp_path / "data" / "reports"
    checkpoint_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    db_path = tmp_path / "dashboard.db"
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setattr(server, "DB_PATH", db_path)
    monkeypatch.setenv("DASHBOARD_RUN_SOURCE", "paper_live")
    monkeypatch.setattr(
        server,
        "RISK_LIMITS",
        {
            "max_daily_loss_usdc": 20.0,
            "max_daily_trades": 10,
            "max_consecutive_losses": 4,
            "max_open_or_pending_orders": 3,
        },
    )

    now = datetime.now(timezone.utc)
    day_info = server._dashboard_day_info(now=now)
    start_utc = day_info["start_utc"]
    today_prefix = day_info["day_iso"]
    _write_json(checkpoint_dir / "paper_live.json", {"trades": [], "pending_orders": [], "open_orders": []})
    (checkpoint_dir / "paper_live_events.jsonl").write_text("", encoding="utf-8")

    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE trades (
            source TEXT,
            pnl REAL,
            won INTEGER,
            regime TEXT,
            direction TEXT,
            size REAL,
            entry_bar TEXT,
            settle_bar TEXT,
            created_at TEXT,
            details TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE events (
            source TEXT,
            kline_n INTEGER,
            action TEXT,
            dir5 TEXT,
            dir4 TEXT,
            regime TEXT,
            filt_passed INTEGER,
            reason TEXT,
            created_at TEXT,
            details TEXT
        )"""
    )
    trade_rows = [
        (
            26.02,
            1,
            "BUY_UP",
            server._iso_utc(start_utc).replace("T", " ").replace("Z", "+00:00"),
            server._iso_utc(start_utc + timedelta(minutes=5)).replace("T", " ").replace("Z", "+00:00"),
        ),
        (
            -25.0,
            0,
            "BUY_DOWN",
            server._iso_utc(start_utc + timedelta(minutes=5)).replace("T", " ").replace("Z", "+00:00"),
            server._iso_utc(start_utc + timedelta(minutes=10)).replace("T", " ").replace("Z", "+00:00"),
        ),
        (
            26.02,
            1,
            "BUY_UP",
            server._iso_utc(start_utc + timedelta(minutes=10)).replace("Z", "+00:00"),
            server._iso_utc(start_utc + timedelta(minutes=15)).replace("Z", "+00:00"),
        ),
        (
            26.02,
            1,
            "BUY_UP",
            server._iso_utc(start_utc - timedelta(minutes=10)),
            server._iso_utc(start_utc - timedelta(minutes=5)),
        ),
    ]
    conn.executemany(
        """INSERT INTO trades (source, pnl, won, regime, direction, size, entry_bar, settle_bar, created_at, details)
           VALUES ('live', ?, ?, 'test', ?, 25.0, ?, ?, ?, '{}')""",
        [
            (pnl, won, direction, entry, settle, server._iso_utc(start_utc + timedelta(hours=1)))
            for pnl, won, direction, entry, settle in trade_rows
        ],
    )
    event_rows = [
        ("BUY_UP", 1, "LONG passed", server._iso_utc(start_utc)),
        ("BUY_DOWN", 1, "SHORT passed", server._iso_utc(start_utc + timedelta(minutes=5))),
        ("HOLD", 0, "macro_gate", server._iso_utc(start_utc + timedelta(minutes=10))),
        ("BUY_UP", 1, "old", server._iso_utc(start_utc - timedelta(minutes=5))),
    ]
    conn.executemany(
        """INSERT INTO events (source, kline_n, action, dir5, dir4, regime, filt_passed, reason, created_at, details)
           VALUES ('live', 0, ?, 'UP', 'UP', 'test', ?, ?, ?, '{}')""",
        event_rows,
    )
    conn.commit()
    conn.close()

    today = server._safety_report_summary()["today"]

    assert today["trades"]["settled"] == 3
    assert today["trades"]["wins"] == 2
    assert today["trades"]["losses"] == 1
    assert today["trades"]["win_rate"] == 0.6667
    assert today["trades"]["pnl_usdc"] == 27.04
    assert today["signals"]["total"] == 3
    assert today["signals"]["passed"] == 2
    assert today["signals"]["blocked"] == 1
    assert today["signals"]["pass_rate"] == 0.6667
    assert today["signals"]["buy_up"] == 1
    assert today["signals"]["buy_down"] == 1
    assert today["signals"]["hold"] == 1
    assert today["signals"]["top_block_reason"] == "macro_gate"
    assert today["risk_usage"]["daily_trades"] == 0.3
    assert today["day"] == today_prefix
    assert today["day_tz"] == "Asia/Shanghai"
    assert today["day_start_utc"] == day_info["start_utc_iso"]
    assert today["day_end_utc"] == day_info["end_utc_iso"]
    assert today["activity"]["latest_trade_at"] == server._iso_utc(start_utc + timedelta(minutes=15))
    assert today["activity"]["latest_signal_at"] == server._iso_utc(start_utc + timedelta(minutes=10))


def test_live_safety_marks_preflight_submission_as_critical(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir = tmp_path / "data" / "reports"
    checkpoint_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)

    _write_json(
        report_dir / "live_preflight_chain_latest.json",
        {
            "ok": False,
            "submitted": True,
            "blockers": ["guarded smoke submitted unexpectedly"],
            "summary": {"gate_ready": True, "smoke_mode": "submitted", "open_orders": 1, "settled": 0, "risk_ok": False},
        },
    )

    summary = server._safety_report_summary()
    checks = {item["key"]: item for item in summary["checklist"]}

    assert summary["preflight_chain"]["submitted"] is True
    assert checks["live_preflight_no_submission"]["ok"] is False
    assert checks["live_preflight_no_submission"]["severity"] == "critical"


def test_live_page_mounts_safety_panels():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "<SafetyStrip safety={safety} health={health} />" in source
    assert "<ReadinessChecklist safety={safety} health={health} intel={intel} />" in source


def test_live_page_uses_configured_source_and_label():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert 'usePolling<StatusData>("/api/status", 5000)' in source
    assert 'usePolling<EventItem[]>(`/api/events?source=${eventSource}&limit=80`, 5000)' in source
    assert 'const eventSource = isLiveRealTab ? "live_real" : "paper"' in source
    assert 'usePolling<TradeItem[]>("/api/trades?limit=200", 5000)' in source
    assert "source_label?: string" in source
    assert "const sourceLabel = safety?.source_label ?? safety?.run_source ?? \"Source\"" in source
    assert "source=live" not in source


def test_api_events_live_real_reads_prediction_artifact_history(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir = tmp_path / "data" / "reports"
    log_dir = tmp_path / "data" / "logs"
    checkpoint_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    log_dir.mkdir(parents=True)
    db_path = tmp_path / "dashboard.db"
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setattr(server, "KRONOS_LOG_DIR", log_dir)
    monkeypatch.setattr(server, "DB_PATH", db_path)

    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE events (
            source TEXT,
            kline_n INTEGER,
            action TEXT,
            dir5 TEXT,
            dir4 TEXT,
            regime TEXT,
            filt_passed INTEGER,
            reason TEXT,
            created_at TEXT,
            details TEXT
        )"""
    )
    conn.commit()
    conn.close()

    records = [
        {
            "decision_id": "2026-06-14T18:25:00Z:HOLD",
            "decision_bar_ts": "2026-06-14T18:25:00Z",
            "artifact_created_at": "2026-06-14T18:25:09Z",
            "action": "HOLD",
            "passed": False,
            "p5_up": 0.51,
            "p1_up": 0.49,
            "p4_up": 0.50,
            "reason_code": "long_score_low",
            "reason": "blocked",
            "failure_codes": ["long_score_low"],
        },
        {
            "decision_id": "2026-06-14T18:30:00Z:BUY_UP",
            "decision_bar_ts": "2026-06-14T18:30:00Z",
            "artifact_created_at": "2026-06-14T18:30:09Z",
            "entry_ts": "2026-06-14T18:35:00Z",
            "settle_ts": "2026-06-14T18:40:00Z",
            "action": "BUY_UP",
            "passed": True,
            "p5_up": 0.8,
            "p1_up": 0.7333,
            "p4_up": 0.6167,
            "long_score": 0.71,
            "short_score": 0.18,
            "reason_code": "long_passed",
            "reason": "LONG passed",
            "failure_codes": [],
        },
    ]
    (checkpoint_dir / "aligned_prod_shift1_predictions.jsonl").write_text(
        "\n".join(json.dumps(record) for record in records),
        encoding="utf-8",
    )

    with server.app.test_client() as client:
        events = client.get("/api/events?source=live_real&limit=10").get_json()
        stats = client.get("/api/signal-stats?source=live_real").get_json()

    assert [event["action"] for event in events] == ["BUY_UP", "HOLD"]
    assert events[0]["source"] == "live_real"
    assert events[0]["source_label"] == "Live Real"
    assert events[0]["filt_passed"] == 1
    assert events[0]["reason"] == "LONG passed"
    assert json.loads(events[0]["details"])["p5_up"] == 0.8
    assert json.loads(events[0]["details"])["reason_code"] == "long_passed"
    assert stats["source"] == "live_real"
    assert stats["total"] == 2
    assert stats["passed"] == 1


def test_api_events_live_real_prefers_current_formal_prediction(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir = tmp_path / "data" / "reports"
    log_dir = tmp_path / "data" / "logs"
    checkpoint_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    log_dir.mkdir(parents=True)
    db_path = tmp_path / "dashboard.db"
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setattr(server, "KRONOS_LOG_DIR", log_dir)
    monkeypatch.setattr(server, "DB_PATH", db_path)

    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE events (
            source TEXT,
            kline_n INTEGER,
            action TEXT,
            dir5 TEXT,
            dir4 TEXT,
            regime TEXT,
            filt_passed INTEGER,
            reason TEXT,
            created_at TEXT,
            details TEXT
        )"""
    )
    conn.commit()
    conn.close()

    (checkpoint_dir / "aligned_prod_shift1_predictions.jsonl").write_text(
        json.dumps(
            {
                "decision_id": "old:20260615T104500Z",
                "decision_bar_ts": "2026-06-15T10:45:00Z",
                "artifact_created_at": "2026-06-15T10:50:11Z",
                "action": "HOLD",
                "passed": False,
                "reason_code": "no_side_passed",
            }
        ),
        encoding="utf-8",
    )
    _write_json(
        report_dir / "prediction_bound_live_formal_latest.json",
        {
            "created_at": "2026-06-16T16:07:37Z",
            "source": "prediction_bound_live_order",
            "ok": False,
            "submitted": False,
            "prediction": {
                "decision_id": "formal:20260616T160000Z",
                "created_at": "2026-06-16T16:05:11Z",
                "decision_bar_ts": "2026-06-16T16:00:00Z",
                "entry_ts": "2026-06-16T16:10:00Z",
                "settle_ts": "2026-06-16T16:15:00Z",
                "execution_market_shift": "next_period",
                "action": "BUY_DOWN",
                "passed": True,
                "p5_up": 0.2,
                "p4_up": 0.3,
                "reason_code": "short_passed",
                "reason": "SHORT passed",
            },
        },
    )
    (report_dir / "prediction_bound_live_formal_predictions.jsonl").write_text(
        json.dumps(
            {
                "created_at": "2026-06-16T16:00:17.733537Z",
                "decision_bar_ts": "2026-06-16T15:55:00Z",
                "entry_ts": "2026-06-16T16:05:00Z",
                "settle_ts": "2026-06-16T16:10:00Z",
                "execution_market_shift": "next_period",
                "action": "HOLD",
                "passed": False,
                "p5_up": 0.52,
                "p1_up": 0.48,
                "p4_up": 0.51,
                "reason_code": "no_side_passed",
                "reason": "full historical prediction details",
            }
        ),
        encoding="utf-8",
    )
    (log_dir / "prediction_bound_live_formal_latest.out.log").write_text(
        "\n".join(
            [
                "2026-06-16T16:00:17.733537Z status=hold ts=2026-06-16 15:55:00+00:00 entry=2026-06-16T16:05:00Z settle=2026-06-16T16:10:00Z action=HOLD reason_code=no_side_passed block=None ledger=0",
                "2026-06-16T16:05:11.827115Z status=order_intent ts=2026-06-16 16:00:00+00:00 entry=2026-06-16T16:10:00Z settle=2026-06-16T16:15:00Z action=BUY_DOWN reason_code=short_passed block=None ledger=0",
            ]
        ),
        encoding="utf-8",
    )

    with server.app.test_client() as client:
        events = client.get("/api/events?source=live_real&limit=10").get_json()
        stats = client.get("/api/signal-stats?source=live_real").get_json()

    assert events[0]["action"] == "BUY_DOWN"
    assert events[0]["created_at"] == "2026-06-16T16:05:11Z"
    assert json.loads(events[0]["details"])["execution_market_shift"] == "next_period"
    assert events[1]["action"] == "HOLD"
    assert events[1]["created_at"] == "2026-06-16T16:00:17.733537Z"
    assert events[1]["reason"] == "full historical prediction details"
    assert json.loads(events[1]["details"])["p5_up"] == 0.52
    assert all(not event["created_at"].startswith("2026-06-15") for event in events)
    assert stats["total"] == 2
    assert stats["passed"] == 1
    assert stats["latest_created_at"] == "2026-06-16T16:05:11Z"


def test_api_events_live_real_reads_utf16_formal_log(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir = tmp_path / "data" / "reports"
    log_dir = tmp_path / "data" / "logs"
    checkpoint_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    log_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setattr(server, "KRONOS_LOG_DIR", log_dir)

    (log_dir / "prediction_bound_live_formal_latest.out.log").write_text(
        "\n".join(
            [
                "2026-06-16T16:00:17.733537Z status=hold ts=2026-06-16 15:55:00+00:00 entry=2026-06-16T16:05:00Z settle=2026-06-16T16:10:00Z action=HOLD reason_code=no_side_passed block=None ledger=0",
                "2026-06-16T16:05:11.827115Z status=order_intent ts=2026-06-16 16:00:00+00:00 entry=2026-06-16T16:10:00Z settle=2026-06-16T16:15:00Z action=BUY_DOWN reason_code=short_passed block=None ledger=0",
            ]
        ),
        encoding="utf-16",
    )

    with server.app.test_client() as client:
        events = client.get("/api/events?source=live_real&limit=10").get_json()
        stats = client.get("/api/signal-stats?source=live_real").get_json()

    assert [event["action"] for event in events] == ["BUY_DOWN", "HOLD"]
    assert stats["total"] == 2
    assert stats["passed"] == 1


def test_api_events_live_real_enriches_formal_log_signal_from_ledger(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir = tmp_path / "data" / "reports"
    log_dir = tmp_path / "data" / "logs"
    checkpoint_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    log_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setattr(server, "KRONOS_LOG_DIR", log_dir)

    (log_dir / "prediction_bound_live_formal_latest.out.log").write_text(
        "2026-06-16T16:50:19.584814Z status=would_place_order ts=2026-06-16 16:45:00+00:00 entry=2026-06-16T16:55:00Z settle=2026-06-16T17:00:00Z action=BUY_UP reason_code=long_passed block=None ledger=1\n",
        encoding="utf-16",
    )
    _write_json(
        checkpoint_dir / "live_real_orders_current_next.json",
        [
            {
                "order_id": "0xabc",
                "status": "OPEN",
                "order_key": "aligned_prod_current_next:20260616T165500Z:LONG",
                "action": "BUY_UP",
                "direction": "UP",
                "side": "LONG",
                "market_slug": "btc-updown-5m-1781628900",
                "decision_bar_ts": "2026-06-16T16:45:00Z",
                "entry_ts": "2026-06-16T16:55:00Z",
                "settle_ts": "2026-06-16T17:00:00Z",
                "price": 0.50,
                "size": 5,
                "filled_size": 0,
                "p5_up": 0.8,
                "p1_up": 0.6333333333,
                "p4_up": 0.7833333333,
                "reason_code": "long_passed",
            }
        ],
    )

    with server.app.test_client() as client:
        events = client.get("/api/events?source=live_real&limit=10").get_json()

    assert events[0]["action"] == "BUY_UP"
    assert events[0]["filt_passed"] == 1
    details = json.loads(events[0]["details"])
    assert details["order_id"] == "0xabc"
    assert details["p5_up"] == 0.8
    assert details["p1_up"] == 0.6333333333
    assert details["p4_up"] == 0.7833333333
    assert details["market_slug"] == "btc-updown-5m-1781628900"


def test_api_events_live_real_attaches_order_chain_and_execution_price(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir = tmp_path / "data" / "reports"
    log_dir = tmp_path / "data" / "logs"
    checkpoint_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    log_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setattr(server, "KRONOS_LOG_DIR", log_dir)

    (report_dir / "prediction_bound_live_formal_predictions.jsonl").write_text(
        json.dumps(
            {
                "created_at": "2026-06-21T18:15:09.293885Z",
                "decision_bar_ts": "2026-06-21T18:10:00Z",
                "entry_ts": "2026-06-21T18:20:00Z",
                "settle_ts": "2026-06-21T18:25:00Z",
                "execution_market_shift": "next_period",
                "action": "BUY_DOWN",
                "passed": True,
                "would_place_order": True,
                "submitted": True,
                "reason_code": "short_passed",
                "reason": "SHORT passed",
                "side": "SHORT",
                "direction": "DOWN",
                "market_slug": "btc-updown-5m-1782066000",
                "order_key": "aligned_prod_current_next:20260621T182000Z:SHORT",
                "price": 0.52,
                "p5_up": 0.4,
                "p1_up": 0.9666666667,
                "p4_up": 0.1666666667,
            }
        ),
        encoding="utf-8",
    )
    _write_json(
        checkpoint_dir / "live_real_orders_current_next.json",
        [
            {
                "order_id": "0xmaker",
                "signal_id": "aligned_prod_current_next:20260621T182000Z:SHORT",
                "order_key": "aligned_prod_current_next:20260621T182000Z:SHORT",
                "status": "CANCELLED",
                "execution_result": "no_fill",
                "market_slug": "btc-updown-5m-1782066000",
                "direction": "DOWN",
                "side": "SHORT",
                "price": 0.49,
                "price_tier": "maker_049",
                "size": 10,
                "filled_size": 0,
                "remaining_size": 10,
                "created_at": "2026-06-21T18:18:09.413151Z",
                "updated_at": "2026-06-21T18:19:26.202872Z",
                "entry_ts": "2026-06-21T18:20:00Z",
                "settle_ts": "2026-06-21T18:25:00Z",
            },
            {
                "order_id": "0xfill",
                "signal_id": "aligned_prod_current_next:20260621T182000Z:SHORT",
                "order_key": "aligned_prod_current_next:20260621T182000Z:SHORT",
                "repost_parent_order_id": "0xmaker",
                "status": "SETTLED",
                "market_slug": "btc-updown-5m-1782066000",
                "direction": "DOWN",
                "side": "SHORT",
                "price": 0.50,
                "price_tier": "taker_050",
                "size": 10,
                "filled_size": 10,
                "remaining_size": 0,
                "created_at": "2026-06-21T18:19:26.809374Z",
                "updated_at": "2026-06-21T18:25:26.567100Z",
                "entry_ts": "2026-06-21T18:20:00Z",
                "settle_ts": "2026-06-21T18:25:00Z",
            },
        ],
    )

    with server.app.test_client() as client:
        events = client.get("/api/events?source=live_real&limit=10").get_json()

    details = json.loads(events[0]["details"])
    assert details["price"] == 0.52
    assert details["execution_price"] == 0.50
    assert details["execution_price_tier"] == "taker_050"
    assert details["execution_order_id"] == "0xfill"
    assert details["execution_status"] == "SETTLED"
    assert details["order_chain_count"] == 2
    assert [order["price_tier"] for order in details["order_chain"]] == ["maker_049", "taker_050"]
    assert [order["status"] for order in details["order_chain"]] == ["CANCELLED", "SETTLED"]


def test_status_bar_uses_source_label_and_configured_status_source():
    source = Path("web/src/components/StatusBar.tsx").read_text(encoding="utf-8")

    assert 'usePolling<StatusData>("/api/status", 5000)' in source
    assert "source_label?: string" in source
    assert "source=live" not in source


def test_status_bar_surfaces_live_alert_counts():
    source = Path("web/src/components/StatusBar.tsx").read_text(encoding="utf-8")

    assert "alerts?: {" in source
    assert "const alertCritical = safety?.alerts?.critical_count ?? 0" in source
    assert "const alertActive = safety?.alerts?.active_count ?? 0" in source
    assert "alertCritical > 0" in source
    assert 'const alertLabel = alertCritical > 0 ? `Alerts ${alertCritical}` : "Alerts 0"' in source
    assert "label={alertLabel}" in source
    assert 'DetailTile label="Alerts" value={`${alertCritical}/${alertActive}`}' in source


def test_live_page_surfaces_preflight_chain_status():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "safety?.preflight_chain?.ok" in source
    assert "safety?.preflight_chain?.fresh" in source
    assert "safety?.preflight_chain?.age_seconds" in source
    assert "Preflight" in source


def test_live_page_surfaces_funnel_conversion_rates():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "const funnelRate" in source
    assert "Pass / Signal" in source
    assert "Orders / Exec" in source
    assert "Filled / Orders" in source
    assert "Settled / Filled" in source
    assert "percent(item.value)" in source


def test_live_page_shows_empty_states_for_activity_lists():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "(events?.length ?? 0) === 0" in source
    assert "No recent signals" in source
    assert "Waiting for aligned-prod decisions" in source
    assert "settledDesc.length === 0" in source
    assert "No completed trades" in source
    assert "Settled trades will appear here" in source


def test_live_page_surfaces_funding_shortfalls():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "safety?.funding?.balance_shortfall_usdc" in source
    assert "safety?.funding?.allowance_shortfall_usdc" in source
    assert "safety?.funding?.funding_ready" in source
    assert "Balance Gap" in source
    assert "Allowance Gap" in source


def test_live_page_formats_funding_amounts_with_money():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert 'label={`Balance ${fundingBalance == null ? "-" : money(fundingBalance)}`}' in source
    assert "const compactAllowance" in source
    assert 'return "Approved"' in source
    assert "const fundingAllowanceLabel = compactAllowance(fundingAllowance, safety?.funding?.allowance_ok === true || fundingReady)" in source
    assert 'label={`Allowance ${fundingAllowanceLabel}`}' in source
    assert 'HealthTile label="Balance" value={fundingBalance == null ? "-" : money(fundingBalance)}' in source
    assert 'HealthTile label="Allowance" value={fundingAllowanceLabel}' in source


def test_live_page_compacts_live_real_allowance_and_guards_card_overflow():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "compactAllowance(funding?.min_allowance" in source
    assert 'StatCard label="CLOB Allowance"' not in source
    assert 'className="rounded-md border border-zinc-800 bg-zinc-950/70 p-4 min-w-0"' in source
    assert "mt-3 truncate font-mono text-2xl font-semibold" in source


def test_live_readiness_checklist_compacts_allowance_values():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "const displayChecklistValue" in source
    assert 'key.includes("allowance")' in source
    assert "{displayChecklistValue(item)}" in source
    assert "{String(item.value ?? \"-\")}" not in source


def test_live_page_mounts_today_cockpit():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "function TodayCockpit" in source
    assert "<TodayCockpit today={todayStats}" in source
    assert "safety?.today" in source
    assert "Maker Target" in source
    assert "0.49" in source


def test_live_page_labels_today_with_configured_day_timezone():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "today?.day_tz" in source
    assert "today?.day ?? today?.day_utc" in source
    assert 'sub={`UTC ${today?.day_utc ?? "-"}`}' not in source


def test_live_page_uses_today_summary_for_top_kpis():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "const todayStats = safety?.today" in source
    assert "const todayPnl = todayStats?.trades.pnl_usdc" in source
    assert "const todaySettled = todayStats?.trades.settled" in source
    assert "const todayWinRate = todayStats?.trades.win_rate" in source
    assert "const todayPassRate = todayStats?.signals.pass_rate" in source
    assert "const paperTotalWinRate = paperMonitor?.stats.win_rate" in source
    assert "const paperTotalPassRate = paperMonitor?.signals.pass_rate" in source
    assert 'StatCard label="Total Win Rate" value={percent(paperTotalWinRate)}' in source
    assert 'StatCard label="Today Win Rate" value={percent(todayWinRate)}' in source
    assert 'StatCard label="Total Pass Rate" value={percent(paperTotalPassRate)}' in source
    assert 'StatCard label="Today Pass Rate" value={percent(todayPassRate)}' in source
    assert 'StatCard label="Today PnL" value={signedMoney(todayPnl)} sub={`${todaySettled} settled today`}' in source


def test_live_page_defaults_to_trading_console_layout():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "Trading Console" not in source
    assert "grid grid-cols-2 gap-3 md:grid-cols-4 2xl:grid-cols-8" in source
    assert 'StatCard label="CLOB Balance"' in source
    assert 'StatCard label="Total PnL"' in source
    assert 'sub={`${liveEquity?.settled ?? 0} settled total`}' in source
    assert 'StatCard label="Realized PnL"' in source
    assert 'StatCard label="Total Win Rate"' in source
    assert 'StatCard label="Today Win Rate"' in source
    assert 'StatCard label="Total Pass Rate"' in source
    assert 'StatCard label="Today Pass Rate"' in source
    assert 'StatCard label="Settled Win Rate"' in source
    assert 'StatCard label="Orders"' in source
    assert 'StatCard label="Signal Pass Rate"' in source
    assert 'sub={`${liveSignalPassed} / ${liveSignalTotal} signals passed`}' in source
    assert 'StatCard label="Mode"' not in source
    assert 'StatCard label="Readiness"' not in source
    assert 'StatCard label="Health"' not in source


def test_live_page_mounts_risk_rules_card():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "risk_controls?:" in source
    assert "function RiskRulesPanel" in source
    assert "<RiskRulesPanel controls={liveReal?.risk_controls} />" in source
    assert 'title="Risk Rules"' in source
    assert "active_max_price" in source
    assert "same_direction_loss_cooldown_minutes" in source


def test_live_page_surfaces_risk_resilience_alert_banner():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "risk?.resilience" in source
    assert "Risk Control Triggered" in source
    assert "riskBlockerItems" in source


def test_live_page_does_not_treat_full_open_order_slot_as_risk_control_trigger():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "riskHardBlockerItems" in source
    assert 'item.key !== "risk_open_or_pending"' in source
    assert "riskOpenPendingHardBlocked" in source
    assert "riskResilience?.ok === false" in source
    assert "riskResilience?.ok === false ? riskResilience?.reason : riskHardBlockerItems" in source
    assert "metrics.open_or_pending_orders <= limits.max_open_or_pending_orders" in source
    assert "openPendingUsed <= openPendingLimit" in source


def test_live_page_scopes_real_order_lock_badge_to_live_real_mode():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert 'const modeStatus = isPaperMonitorTab ? "Simulation only" : safety?.real_orders_enabled ? "REAL ORDERS ENABLED" : "Real orders locked";' in source
    assert "const modeStatusOk = isPaperMonitorTab ? true : !safety?.real_orders_enabled;" in source
    assert '{key === "live-real" ? "Live Real" : "Paper Monitor"}' in source
    assert "<StatusPill ok={modeStatusOk} label={modeStatus} />" in source
    assert '<StatusPill ok={!safety?.real_orders_enabled} label={safety?.real_orders_enabled ? "REAL ORDERS ENABLED" : "Real orders locked"} />' not in source


def test_live_page_moves_audit_panels_into_diagnostics_section():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert 'title="Live Diagnostics"' in source
    assert 'sub="safety gates, audits, and raw signal detail"' in source
    assert "<SafetyStrip safety={safety} health={health} />" in source
    assert "<ReadinessChecklist safety={safety} health={health} intel={intel} />" in source
    assert "<MarketDataPanel data={safety?.market_data} />" in source
    assert "<ClobReadonlyPanel audit={safety?.clob_readonly} />" in source
    assert "<ReportFreshnessPanel refresh={safety?.report_refresh} />" in source
    assert "<FunnelPanel funnel={intel?.funnel} />" in source
    assert "<RiskPanel intel={intel} safety={safety} />" in source
    assert "<MakerPanel intel={intel} />" in source


def test_live_page_groups_equity_curve_with_btc_market_chart_on_main_console():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    live_section = source[source.index('activeTradingTab === "live-real"'):source.index('activeTradingTab === "paper-monitor"')]
    paper_section = source[source.index('activeTradingTab === "paper-monitor"'):]

    assert "<BTCMarketChart />" in live_section
    assert 'EquityPanel title="Equity Curve" sub="real settled ledger"' in live_section
    assert "data={liveEquityPoints}" in live_section
    assert "<BTCMarketChart />" in paper_section
    assert 'EquityPanel title="Equity Curve" sub="paper checkpoint trades"' in paper_section
    assert "data={paperEquityPoints}" in paper_section
    assert "<PaperRuntimePanel paperMonitor={paperMonitor}" in paper_section
    assert '2xl:grid-cols-[minmax(0,1.6fr)_minmax(360px,0.45fr)]' in source
    assert 'xl:grid-cols-[minmax(0,1.1fr)_minmax(340px,0.75fr)]' in source
    assert '<div className="min-w-0 space-y-5">' not in source


def test_live_page_places_weekly_pnl_calendar_below_live_equity_curve():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")
    live_section = source[source.index('activeTradingTab === "live-real"'):source.index('activeTradingTab === "paper-monitor"')]

    assert "weekly_pnl_calendar?: WeeklyPnlCalendar" in source
    assert "function WeeklyPnlCalendarPanel" in source
    assert "<WeeklyPnlCalendarPanel calendar={liveReal?.weekly_pnl_calendar} />" in live_section
    assert live_section.index('EquityPanel title="Equity Curve" sub="real settled ledger"') < live_section.index("<WeeklyPnlCalendarPanel")
    assert 'className="grid min-w-0 gap-5 live-main-console"' in live_section
    assert live_section.index('className="grid min-w-0 gap-5 live-main-console"') < live_section.index("<WeeklyPnlCalendarPanel")


def test_live_weekly_pnl_calendar_uses_horizontal_strip_below_main_console():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")
    panel = source[source.index("function WeeklyPnlCalendarPanel"):source.index("function CollapsiblePanel")]

    live_section = source[source.index('activeTradingTab === "live-real"'):source.index('activeTradingTab === "paper-monitor"')]
    main_console_start = live_section.index('className="grid min-w-0 gap-5 live-main-console"')
    main_console_end = live_section.index('className="space-y-5 live-side-console"', main_console_start)
    main_console = live_section[main_console_start:main_console_end]

    assert "<WeeklyPnlCalendarPanel calendar={liveReal?.weekly_pnl_calendar} />" in main_console
    assert 'className="grid grid-cols-2 gap-2 md:grid-cols-4 2xl:grid-cols-7"' in panel
    assert "HealthTile label=\"Week PnL\"" not in panel
    assert "break-words" not in panel


def test_live_page_uses_source_scoped_equity_summaries():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "equity: EquitySummary" in source
    assert "const liveEquity = liveReal?.equity" in source
    assert "const paperEquity = paperMonitor?.equity" in source
    assert "const liveEquityPoints = liveEquity?.points?.length ? liveEquity.points : [fundingBalance]" in source
    assert "const paperEquityPoints = paperEquity?.points?.length ? paperEquity.points : [paperMonitor?.balance ?? INITIAL_BALANCE]" in source
    assert "const equity = useMemo" not in source


def test_live_page_uses_signal_freshness_for_top_signals_kpi():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "const latestSignalKpiAge = todayStats?.activity?.latest_signal_age_seconds" in source
    assert "const latestSignalFresh = (latestSignalKpiAge ?? 9999) < 600" in source
    assert "const signalKpiTone = todaySignalTotal === 0 ? \"text-zinc-100\" : latestSignalFresh ? \"text-emerald-300\" : \"text-amber-300\"" in source
    assert "<span className=\"text-zinc-500\">Signals</span>" in source
    assert '<span className={`font-mono ${signalKpiTone}`}>{todaySignalPassed}/{todaySignalTotal}</span>' in source
    assert 'StatCard label="Signals"' not in source


def test_live_page_surfaces_dryrun_safety_in_diagnostics():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "const dryrun = safety?.dryrun" in source
    assert "const dryrunSubmitted = dryrun?.submitted_count ?? 0" in source
    assert "const dryrunValue = `${dryrun?.would_place_count ?? 0}/${dryrun?.ledger_count ?? 0}`" in source
    assert "const dryrunSub = `${dryrunSubmitted} submitted / ${dryrun?.blocked_count ?? 0} blocked`" in source
    assert "const dryrunTone = dryrunSubmitted === 0 ? \"text-emerald-300\" : \"text-rose-300\"" in source
    assert '<span className="text-zinc-500">Dry-run</span>' in source
    assert '<span className={`font-mono ${dryrunTone}`}>{dryrunValue}</span>' in source
    assert "grid grid-cols-2 gap-3 md:grid-cols-4 2xl:grid-cols-8" in source


def test_live_page_uses_clob_portfolio_for_top_balance_kpi():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "const funding = safety?.funding" in source
    assert "const portfolio = liveReal?.account_activity?.portfolio" in source
    assert "const clobPortfolioValue = portfolio?.total_value_usdc ?? fundingBalance" in source
    assert "const clobPortfolioSub = portfolio ? `cash ${money(portfolio.cash_balance_usdc ?? 0)} | positions ${money(portfolio.positions_value_usdc ?? 0)}` : fundingBalanceSub" in source
    assert "const fundingBalanceGap = funding?.balance_shortfall_usdc ?? 0" in source
    assert "const fundingAllowanceGap = funding?.allowance_shortfall_usdc ?? 0" in source
    assert "const fundingLargestGap = Math.max(fundingBalanceGap, fundingAllowanceGap)" in source
    assert "fundingBalanceGap > 0" in source
    assert "`Balance gap ${money(fundingBalanceGap)}`" in source
    assert "`Allowance gap ${money(fundingAllowanceGap)}`" in source
    assert "const fundingBalanceTone = funding?.funding_ready === true || funding?.balance_ok === true ? \"text-emerald-300\" : fundingLargestGap > 0 ? \"text-rose-300\" : \"text-zinc-100\"" in source
    assert "value={money(clobPortfolioValue)}" in source
    assert "sub={clobPortfolioSub}" in source
    assert "tone={fundingBalanceTone}" in source


def test_live_page_live_real_balance_subtitle_does_not_use_paper_today_pnl():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "`Today ${signedMoney(todayPnl)}`" not in source
    assert "const fundingBalanceSub" in source
    assert '"CLOB account"' in source


def test_live_page_uses_trade_queue_for_top_pending_kpi():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "const riskLimits = safety?.risk?.limits" in source
    assert "const pendingCount = pending.length" in source
    assert "const openPendingUsed = todayOpen + pendingCount" in source
    assert "const openPendingLimit = riskLimits?.max_open_or_pending_orders" in source
    assert "const openPendingValue = `${todayOpen}/${pendingCount}`" in source
    assert "const openPendingSub = openPendingLimit == null ? \"open / pending\" : `open / pending | risk limit ${openPendingLimit}`" in source
    assert "const openPendingTone = openPendingLimit == null ? \"text-zinc-100\" : openPendingUsed <= openPendingLimit ? \"text-emerald-300\" : \"text-rose-300\"" in source
    assert 'StatCard label="Open/Pending" value={openPendingValue} sub={openPendingSub}' in source
    assert "tone={openPendingTone}" in source


def test_live_page_uses_runtime_freshness_for_top_health_kpi():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "const checkpointAge = health?.checkpoint_age_seconds" in source
    assert "const latestEventAge = health?.latest_event_age_seconds" in source
    assert "const checkpointFresh = (checkpointAge ?? 9999) < 600" in source
    assert "const latestEventFresh = (latestEventAge ?? 9999) < 600" in source
    assert "const runtimeFresh = checkpointFresh && latestEventFresh" in source
    assert 'const healthValue = isHealthy && runtimeFresh ? "OK" : "Review"' in source
    assert "const healthTone = healthValue === \"OK\" ? \"text-emerald-300\" : \"text-amber-300\"" in source
    assert "<span className=\"text-zinc-500\">Health</span>" in source
    assert '<span className={`font-mono ${healthTone}`}>{healthValue}</span>' in source
    assert 'StatCard label="Health"' not in source


def test_live_page_surfaces_today_signal_distribution():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "buy_up: number" in source
    assert "buy_down: number" in source
    assert "top_block_reason?: string" in source
    assert "today?.signals.buy_up" in source
    assert "today?.signals.buy_down" in source
    assert "today?.signals.hold" in source
    assert "today?.signals.top_block_reason" in source
    assert "Buy Up/Down" in source
    assert "Signal Hold" in source
    assert "Top Signal Block" in source


def test_live_page_surfaces_readiness_summary_in_top_kpis():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "const readinessSummary = safety?.readiness_summary" in source
    assert "const readinessPassed = readinessSummary?.passed" in source
    assert "const readinessCritical = readinessSummary?.critical_blockers" in source
    assert "<span className=\"text-zinc-500\">Readiness</span>" in source
    assert '<span className={`font-mono ${readinessTone}`}>{readinessPassed}/{readinessTotal}</span>' in source
    assert "Critical blockers" in source
    assert 'StatCard label="Readiness"' not in source


def test_live_page_top_kpis_wrap_before_ultrawide():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "grid grid-cols-2 gap-3 md:grid-cols-4 2xl:grid-cols-8" in source
    assert "grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6 2xl:grid-cols-11" not in source


def test_live_page_surfaces_market_data_in_top_kpis():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "const marketData = safety?.market_data" in source
    assert "const marketReady = marketData?.ready === true" in source
    assert "const marketValue = marketReady ? \"Fresh\" : marketData?.status ? marketData.status : \"Waiting\"" in source
    assert "<span className=\"text-zinc-500\">Market</span>" in source
    assert '<span className={`font-mono ${marketTone}`}>{marketValue}</span>' in source
    assert "<MarketDataPanel data={safety?.market_data} />" in source
    assert 'StatCard label="Market"' not in source


def test_live_page_today_cockpit_uses_readable_separators():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert " 路 " not in source
    assert " · " not in source
    assert " | ${today?.trades.wins" in source
    assert " | ${percent(passRate)}" in source


def test_live_page_surfaces_readiness_blocker_summary():
    live_source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")
    status_source = Path("web/src/components/StatusBar.tsx").read_text(encoding="utf-8")

    assert "readiness_summary" in live_source
    assert "readiness_summary" in status_source
    assert "critical_blockers" in live_source
    assert "funding_blockers" in live_source
    assert "risk_blockers" in live_source
    assert "Critical" in live_source
    assert "Funding" in status_source
    assert "Risk" in status_source


def test_live_page_surfaces_top_readiness_blockers():
    live_source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")
    status_source = Path("web/src/components/StatusBar.tsx").read_text(encoding="utf-8")

    assert "top_blockers" in live_source
    assert "top_blockers" in status_source
    assert "Top Blockers" in live_source
    assert "Top Blockers" in status_source
    assert "blocker.label" in live_source
    assert "blocker.expected" in status_source
    assert "blocker.action" in live_source
    assert "blocker.action" in status_source
    assert "next {blocker.action}" in live_source
    assert "next {blocker.action}" in status_source


def test_live_page_surfaces_first_order_rail():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "first_order_rail" in source
    assert "function FirstOrderRail" in source
    assert "<FirstOrderRail rail={safety?.first_order_rail}" in source
    assert "First Order Path" in source
    assert "const currentAction = currentStage?.action ?? \"Review readiness\"" in source
    assert "const primaryBlocker = currentStage?.blockers?.[0] ?? \"-\"" in source
    assert 'HealthTile label="Current Stage"' in source
    assert 'HealthTile label="Next Action"' in source
    assert 'HealthTile label="Primary Blocker"' in source
    assert "Manual confirmation" in source
    assert "requires_confirmation" in source


def test_live_page_keeps_operator_summary_out_of_trading_status_card():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")
    panel_source = source[
        source.index("function TradingStatusPanel"):
        source.index("function FirstOrderRail")
    ]

    assert "operator_summary" in source
    assert "operator?.next_action" not in panel_source
    assert "operator?.primary_blocker" not in panel_source
    assert 'title="Trading Status"' in panel_source
    assert "liveStatus?.next_action" in panel_source
    assert "Primary Blocker" not in panel_source


def test_trading_status_panel_avoids_duplicate_top_kpi_values():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")
    panel_source = source[
        source.index("function TradingStatusPanel"):
        source.index("function FirstOrderRail")
    ]

    assert 'HealthTile label="Formal Live"' in panel_source
    assert 'HealthTile label="Order Sync"' in panel_source
    assert 'HealthTile label="Risk State"' in panel_source
    assert 'HealthTile label="Open Orders"' in panel_source
    assert 'HealthTile label="Current Stage"' not in panel_source
    assert 'HealthTile label="Stage Status"' not in panel_source
    assert 'HealthTile label="Manual Confirmation"' not in panel_source
    assert 'HealthTile label="Primary Blocker"' not in panel_source
    assert 'HealthTile label="Balance"' not in panel_source
    assert 'HealthTile label="Allowance"' not in panel_source
    assert 'HealthTile label="Open/Pending"' not in panel_source
    assert 'HealthTile label="Mode"' not in panel_source


def test_live_page_trading_status_uses_live_trading_status_not_first_order_rail():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")
    panel_source = source[
        source.index("function TradingStatusPanel"):
        source.index("function FirstOrderRail")
    ]

    assert "live_trading_status" in source
    assert "liveStatus" in panel_source
    assert "safety?.first_order_rail" not in panel_source
    assert "safety?.operator_summary" not in panel_source
    assert "health?.state" not in panel_source
    assert "Formal Live" in panel_source
    assert "Order Sync" in panel_source
    assert "Risk State" in panel_source


def test_live_page_surfaces_today_dryrun_summary():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "dryrun: {" in source
    assert "today?.dryrun.records" in source
    assert "today?.dryrun.would_place" in source
    assert "today?.dryrun.blocked" in source
    assert "today?.dryrun.submitted" in source
    assert "const dryrunRecords = today?.dryrun.records ?? 0" in source
    assert "const dryrunWouldPlaceRate = dryrunRecords === 0 ? null : (today?.dryrun.would_place ?? 0) / dryrunRecords" in source
    assert "const dryrunBlockedRate = dryrunRecords === 0 ? null : (today?.dryrun.blocked ?? 0) / dryrunRecords" in source
    assert "Dry Place Rate" in source
    assert "Dry Block Rate" in source
    assert "today?.dryrun.latest_block_reason" in source
    assert "Dry-run Today" in source
    assert "Dry Submitted" in source


def test_live_page_surfaces_today_activity_summary():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "activity?: {" in source
    assert "today?.activity?.latest_signal_age_seconds" in source
    assert "today?.activity?.latest_trade_age_seconds" in source
    assert "today?.activity?.latest_order_age_seconds" in source
    assert "today?.activity?.latest_dryrun_age_seconds" in source
    assert "Latest Signal" in source
    assert "Latest Trade" in source
    assert "Latest Order" in source
    assert "Latest Dry-run" in source


def test_live_page_labels_pending_queue_precisely():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert 'Panel title="Pending Queue" sub="awaiting settlement"' in source
    assert "No pending orders" in source
    assert "No open orders" not in source


def test_live_page_labels_overdue_pending_orders_as_awaiting_refresh():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "const pendingStatusLabel = (settle?: number | string)" in source
    assert '"awaiting refresh"' in source
    assert "`settles in ${ageLabel(seconds)}`" in source
    assert "{pendingStatusLabel(trade.settle_bar)}" in source


def test_live_page_completed_trades_uses_per_trade_recorded_time_from_details():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "settled_at?: string" in source
    assert "created_at?: string" in source
    assert "const tradeRecordedAt = (trade: TradeItem, details: SignalDetails | null)" in source
    assert "details?.settled_at || details?.created_at || trade.created_at" in source
    assert "{localTime(tradeRecordedAt(trade, details))}" in source
    assert "{localTime(trade.created_at)}</td>" not in source


def test_live_page_surfaces_clob_readonly_panel():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "clob_readonly" in source
    assert "function ClobReadonlyPanel" in source
    assert "<ClobReadonlyPanel audit={safety?.clob_readonly}" in source
    assert "CLOB Read-only Audit" in source
    assert "Quote Executable" in source
    assert "Open Orders" in source
    assert "Min Allowance" in source
    assert "Allowance Count" in source
    assert "Report Age" in source
    assert "Status Reason" in source
    assert "Next Action" in source
    assert "audit?.status_reason" in source
    assert "audit?.next_action" in source
    assert "audit?.fresh" in source
    assert "audit?.report_age_seconds" in source
    assert "Probe Details" in source
    assert "quote_probes" in source
    assert "probe.quote_executable" in source
    assert "probe.best_bid" in source
    assert "probe.best_ask" in source


def test_live_page_surfaces_current_clob_orders_panel():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")
    live_section = source[source.index('activeTradingTab === "live-real"'):source.index('activeTradingTab === "paper-monitor"')]

    assert "CurrentClobOrdersPanel" in source
    assert "<CurrentClobOrdersPanel audit={safety?.clob_readonly}" in live_section
    assert "Current CLOB Orders" in source
    assert "audit?.open_orders" in source
    assert "order.remaining_size" in source
    assert "No current CLOB open orders" in source


def test_live_page_surfaces_live_ledger_order_records_panel():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")
    live_section = source[source.index('activeTradingTab === "live-real"'):source.index('activeTradingTab === "paper-monitor"')]

    assert "LiveLedgerOrdersPanel" in source
    assert "<LiveLedgerOrdersPanel liveReal={liveReal}" in live_section
    assert "Live Ledger Orders" in source
    assert "liveReal?.order_records" in source
    assert "signal executions with grouped order attempts" in source
    assert "order_chain_count" in source
    assert "<th className=\"px-3 py-2 font-medium\">Attempts</th>" in source
    assert "order.settle_ts || order.settled_at" in source
    assert "order.settlement_source" in source
    assert "hypothetical_pnl" in source
    assert "Would" in source


def test_live_page_surfaces_polymarket_account_activity_panel():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")
    live_section = source[source.index('activeTradingTab === "live-real"'):source.index('activeTradingTab === "paper-monitor"')]

    assert "interface PolymarketAccountActivity" in source
    assert "function PolymarketAccountActivityPanel" in source
    assert "<PolymarketAccountActivityPanel account={liveReal?.account_activity}" in live_section
    assert "PM Account Activity" in source
    assert "Data API trades, active positions, redeemable" in source
    assert "Redeemable Positions" in source
    assert "account?.recent_activity" in source
    assert "account?.positions" in source


def test_live_page_separates_live_real_and_paper_monitor_tabs():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")
    app_source = Path("web/src/App.tsx").read_text(encoding="utf-8")

    assert "live_real?:" in source
    assert "paper_monitor?:" in source
    assert 'type TradingTab = "live-real" | "paper-monitor";' in source
    assert "activeTradingTab: TradingTab" in source
    assert 'const [activeTradingTab, setActiveTradingTab] = useState<TradingTab>("live-real");' in app_source
    assert '"live-real"' in source
    assert '"paper-monitor"' in source
    assert "Live Real" in source
    assert "Paper Monitor" in source
    assert "<Live activeTradingTab={activeTradingTab} onTradingTabChange={setActiveTradingTab}" in app_source
    assert "const liveReal = safety?.live_real" in source
    assert "const paperMonitor = safety?.paper_monitor" in source
    assert "<LiveSoakPanel liveReal={liveReal}" in source
    assert "<PaperRuntimePanel paperMonitor={paperMonitor}" in source


def test_live_formal_process_shows_total_and_child_runtime():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert '<HealthTile label="Total Runtime" value={runtime.label} ok={runtime.ok} />' in source
    assert "const childRuntimeState = runtimeState(childRuntime)" in source
    assert '<HealthTile label="Child Runtime" value={childRuntimeState.label} ok={childRuntimeState.ok} />' in source
    assert '<HealthTile label="Total Started"' in source
    assert '<HealthTile label="Child Started"' in source


def test_app_keeps_top_nav_to_live_analytics_and_research():
    source = Path("web/src/App.tsx").read_text(encoding="utf-8")

    assert 'import Live, { type TradingTab } from "./pages/Live";' in source
    assert 'import Analytics from "./pages/Analytics";' in source
    assert 'type Tab = "live" | "analytics" | "research";' in source
    assert '["live", "Live", Activity]' in source
    assert '["analytics", "Analytics", PieChart]' in source
    assert '["research", "Research", BarChart3]' in source
    assert '["live-real", "Live Real", Activity]' not in source
    assert '["paper-monitor", "Paper Monitor", Activity]' not in source
    assert "Compare" not in source
    assert "setActiveTradingTab" in source
    assert "<Live activeTradingTab={activeTradingTab} onTradingTabChange={setActiveTradingTab}" in source
    assert 'tab === "analytics" && <Analytics />' in source


def test_analytics_page_surfaces_trade_performance_sections():
    source = Path("web/src/pages/Analytics.tsx").read_text(encoding="utf-8")

    assert 'usePolling<AnalyticsData>("/api/live-analytics"' in source
    assert "day_tz?: string" in source
    assert "Today Trading Day" in source
    assert "Today UTC" not in source
    assert "Total PnL" in source
    assert "Win Rate" in source
    assert "Profit Factor" in source
    assert "Max Drawdown" in source
    assert "PnL Composition" in source
    assert "Price Tier Breakdown" in source
    assert "No-Fill Outcome" in source
    assert "Drawdown" in source


def test_chart_pointer_uses_responsive_percent_positioning():
    source = Path("web/src/components/chart.tsx").read_text(encoding="utf-8")

    assert "left: `${activeXPct * 100}%`" in source
    assert "top: `${activeYPct * 100}%`" in source
    assert "cursorPx" not in source
    assert "cursorYpx" not in source


def test_chart_moves_default_pointer_to_latest_after_async_data_load():
    source = Path("web/src/components/chart.tsx").read_text(encoding="utf-8")

    assert "hasPointerInteracted" in source
    assert "if (!hasPointerInteracted.current" in source
    assert "Math.max(0, data.length - 1)" in source


def test_live_page_keeps_paper_trade_tables_out_of_live_real_tab():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    live_section = source[source.index('activeTradingTab === "live-real"'):source.index('activeTradingTab === "paper-monitor"')]
    paper_section = source[source.index('activeTradingTab === "paper-monitor"'):]

    assert "<LiveRealOrdersPanel liveReal={liveReal}" in live_section
    assert "Completed Trades" not in live_section
    assert "Pending Queue" not in live_section
    assert "Completed Trades" in paper_section
    assert "Pending Queue" in paper_section


def test_live_page_shows_live_real_recent_prediction_signals():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")
    live_section = source[source.index('activeTradingTab === "live-real"'):source.index('activeTradingTab === "paper-monitor"')]

    assert 'Panel title="Recent Signals" sub="formal live prediction artifacts"' in live_section
    assert "Waiting for formal live decisions" in live_section
    assert "(events ?? []).slice(0, 14).map((event)" in live_section


def test_live_page_recent_signals_show_directional_probs_and_order_chain():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "function signalDirection" in source
    assert "function signalProb" in source
    assert 'MetricChip label={`5m${signalArrow}`}' in source
    assert 'MetricChip label={`1h${signalArrow}`}' in source
    assert 'MetricChip label={`4h${signalArrow}`}' in source
    assert "details?.execution_price" in source
    assert "details?.order_chain" in source
    assert "Order Chain" in source


def test_live_page_surfaces_report_freshness_panel():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "report_refresh" in source
    assert "legacy_report_refresh" in source
    assert "function ReportFreshnessPanel" in source
    assert "<ReportFreshnessPanel refresh={safety?.report_refresh}" in source
    assert "<LegacyReportFreshnessPanel refresh={safety?.legacy_report_refresh}" in source
    assert "Report Freshness" in source
    assert "Legacy Preflight Reports" in source
    assert "item.next_action" in source
    assert "item.status" in source
    assert "item.age_seconds" in source
    assert "item.report" in source
    panel_source = source[source.index("function ReportFreshnessPanel"):source.index("function MarketDataPanel")]
    assert "formal live, order sync, and account activity" in panel_source
    assert "live gate, preflight, and CLOB audit" not in panel_source


def test_live_page_shows_empty_state_for_missing_report_freshness_items():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "items.length === 0" in source
    assert "No freshness reports" in source
    assert "Waiting for formal live, order sync, and account activity reports" in source
    assert "Waiting for legacy preflight diagnostics" in source


def test_live_page_renders_blocked_report_freshness_as_alert():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert 'if (status === "blocked") return "border-rose-500/25 bg-rose-500/10 text-rose-300"' in source
    assert 'if (status === "stale" || status === "missing")' in source


def test_live_page_surfaces_market_data_panel():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "market_data" in source
    assert "function MarketDataPanel" in source
    assert "<MarketDataPanel data={safety?.market_data}" in source
    assert "Market Data" in source
    assert "data?.price_age_seconds" in source
    assert "data?.received_age_seconds" in source
    assert "const ageBudgetLabel = (age?: number | null, max?: number | null)" in source
    assert "ageBudgetLabel(data?.price_age_seconds, data?.max_price_age_seconds)" in source
    assert "ageBudgetLabel(data?.received_age_seconds, data?.max_received_age_seconds)" in source
    assert 'HealthTile label="Price SLA"' in source
    assert 'HealthTile label="Received SLA"' in source
    assert "data?.next_action" in source
    assert "data?.source" in source


def test_live_collapsible_panels_expose_accessible_expanded_state():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "useId" in source
    assert "const panelId = useId()" in source
    assert "aria-expanded={open}" in source
    assert "aria-controls={panelId}" in source
    assert "id={panelId}" in source


def test_live_page_signal_condition_labels_are_readable():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "5m long probability" in source
    assert "4h long gate" in source
    assert "1h+4h long score" in source
    assert "Short macro veto" in source
    assert "5m short probability" in source
    assert "4h short gate" in source
    assert "1h+4h short score" in source
    assert "Long macro veto" in source


def test_btc_price_bar_uses_chainlink_live_price():
    source = Path("web/src/components/BTCPriceBar.tsx").read_text(encoding="utf-8")

    assert 'usePolling<BTCLivePrice>("/api/btc/live-price"' in source
    assert "/api/btc/price" not in source
    assert "BTC/USD" in source
    assert "BTC/USDT" not in source
    assert "data?.source" in source
    assert "data?.status" in source
    assert "priceAgeLabel(data?.timestamp)" in source


def test_status_bar_surfaces_dryrun_gate_and_preflight_status():
    source = Path("web/src/components/StatusBar.tsx").read_text(encoding="utf-8")

    assert "safety?.dryrun?.submitted_count" in source
    assert "safety?.live_gate?.ready_for_live_smoke" in source
    assert "safety?.preflight_chain?.ok" in source
    assert "safety?.preflight_chain?.fresh" in source
    assert "safety?.preflight_chain?.age_seconds" in source
    assert "market_data_fresh" in source
    assert "Refresh Chainlink live price feed" in source
    assert "Dry-run" in source
    assert "Preflight" in source


def test_status_bar_surfaces_market_data_status():
    source = Path("web/src/components/StatusBar.tsx").read_text(encoding="utf-8")

    assert "market_data?: {" in source
    assert "const marketDataReady = safety?.market_data?.ready === true" in source
    assert "Market Data" in source
    assert "safety?.market_data?.source" in source


def test_status_bar_uses_live_trading_status_for_live_runtime_review():
    source = Path("web/src/components/StatusBar.tsx").read_text(encoding="utf-8")

    assert "live_trading_status?: {" in source
    assert "const liveTradingStatus = safety?.live_trading_status" in source
    assert 'const runtimeLabel = liveEnabled ? liveTradingStatus?.label ?? "Waiting" : healthOk ? "Runtime OK" : "Runtime review"' in source
    assert "const runtimeChipOk = liveEnabled ? liveTradingStatus?.ok === true : healthOk" in source
    assert "liveEnabled ? apiItems : [...apiItems, ...runtimeItems]" in source
    assert "checkpoint_fresh" in source
    assert "events_fresh" in source


def test_status_bar_surfaces_operator_next_action():
    source = Path("web/src/components/StatusBar.tsx").read_text(encoding="utf-8")

    assert "operator_summary?: {" in source
    assert "const operator = safety?.operator_summary" in source
    assert "const operatorNextAction = operator?.next_action" in source
    assert "const operatorStage = operator?.current_stage_label" in source
    assert 'DetailTile label="Operator Stage"' in source
    assert 'DetailTile label="Next Action"' in source


def test_status_bar_surfaces_funding_shortfalls():
    source = Path("web/src/components/StatusBar.tsx").read_text(encoding="utf-8")

    assert "safety?.funding?.balance_shortfall_usdc" in source
    assert "safety?.funding?.allowance_shortfall_usdc" in source
    assert "safety?.funding?.funding_ready" in source
    assert "Balance Gap" in source
    assert "Allowance Gap" in source


def test_status_bar_compacts_unbounded_allowance():
    source = Path("web/src/components/StatusBar.tsx").read_text(encoding="utf-8")

    assert "const compactAllowance" in source
    assert 'return "Approved"' in source
    assert "const fundingAllowanceLabel = compactAllowance(safety?.funding?.min_allowance, safety?.funding?.allowance_ok === true || fundingReady)" in source
    assert 'DetailTile label="Allowance" value={fundingAllowanceLabel}' in source
    assert "displayChecklistValue(item)" in source
    assert 'value={String(safety?.funding?.min_allowance ?? "-")}' not in source


def test_status_bar_surfaces_today_summary():
    source = Path("web/src/components/StatusBar.tsx").read_text(encoding="utf-8")

    assert "today?: {" in source
    assert "const today = safety?.today" in source
    assert "Paper Today PnL" in source
    assert "Paper Today Signals" in source
    assert "Paper Today W/L" in source


def test_status_bar_surfaces_today_activity_freshness():
    source = Path("web/src/components/StatusBar.tsx").read_text(encoding="utf-8")

    assert "activity?: {" in source
    assert "latest_signal_age_seconds?: number | null" in source
    assert "latest_dryrun_age_seconds?: number | null" in source
    assert "const latestSignalAge = ageLabel(today?.activity?.latest_signal_age_seconds)" in source
    assert "const latestDryrunAge = ageLabel(today?.activity?.latest_dryrun_age_seconds)" in source
    assert "const todayActivityFresh = (today?.activity?.latest_signal_age_seconds ?? 9999) < 600" in source
    assert 'DetailTile label="Latest Signal"' in source
    assert 'DetailTile label="Latest Dry-run"' in source


def test_status_bar_merges_runtime_checks_into_readiness_summary():
    source = Path("web/src/components/StatusBar.tsx").read_text(encoding="utf-8")

    assert "function summarizeChecklistReadiness" in source
    assert "const localReadiness = summarizeChecklistReadiness(checklist)" in source
    assert "const readinessPassed = localReadiness.passed" in source
    assert "const criticalBlockers = localReadiness.critical_blockers" in source
    assert "const topBlockers = localReadiness.top_blockers" in source
    assert "readiness?.passed ?? checksPassed" not in source
    assert "original_index" in source
    assert ".map((item, original_index)" in source
    assert "items.indexOf(item)" not in source
    assert "localeCompare" not in source


def test_status_bar_uses_readable_risk_separator():
    source = Path("web/src/components/StatusBar.tsx").read_text(encoding="utf-8")

    assert " | ${metrics.open_or_pending_orders}" in source
    assert " 路 " not in source
