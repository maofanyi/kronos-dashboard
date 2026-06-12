from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from api import server


def test_shadow_market_report_extracts_latest_resolution(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    log_dir = tmp_path / "data" / "logs"
    shadow_dir = tmp_path / "data" / "events" / "shadow_live" / "2026-06-09"
    shadow_dir.mkdir(parents=True)
    log_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_LOG_DIR", log_dir)

    event = {
        "event_id": "shadow_sidecar:300:shadow_watch:order:59",
        "type": "shadow_watch",
        "decision_event_id": "session:300:decision",
        "bar_index": 300,
        "sample": 59,
        "action": "HOLD",
        "price": 0.49,
        "best_bid": 0.49,
        "best_ask": 0.50,
        "token_id": "token-up",
        "market": {
            "id": "pm-1",
            "slug": "bitcoin-up-or-down-next",
            "question": "Bitcoin Up or Down?",
        },
        "source_decision": {
            "n": 300,
            "action": "BUY_UP",
            "dir5": "UP",
            "dir4": "UP",
            "_t": "2026-06-09 18:55:00",
        },
        "summary": {
            "samples": 60,
            "open_price": 0.48,
            "latest_price": 0.49,
            "repost_count": 1,
            "block_count": 0,
            "average_target_price": 0.485,
        },
        "_t": "2026-06-09 18:56:00",
    }
    (shadow_dir / "events_18.jsonl").write_text(json.dumps(event), encoding="utf-8")

    report = server._shadow_market_report(now=datetime(2026, 6, 9, 19, 0, 0))

    assert report["event_count"] == 1
    assert report["latest"]["market"]["slug"] == "bitcoin-up-or-down-next"
    assert report["latest"]["token_id"] == "token-up"
    assert report["latest"]["best_bid"] == 0.49
    assert report["latest"]["source_decision"]["action"] == "BUY_UP"
    assert report["latest"]["summary"]["average_target_price"] == 0.485


def test_events_checkpoint_path_prefers_run_source_file(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    checkpoint_dir.mkdir(parents=True)
    legacy = checkpoint_dir / "paper_events.jsonl"
    preferred = checkpoint_dir / "paper_live_events.jsonl"
    legacy.write_text("{}", encoding="utf-8")
    preferred.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)

    assert server._events_checkpoint_path() == preferred


def test_shadow_market_report_filters_old_errors_and_marks_stale_latest(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    log_dir = tmp_path / "data" / "logs"
    shadow_dir = tmp_path / "data" / "events" / "shadow_live" / "2026-06-10"
    shadow_dir.mkdir(parents=True)
    log_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_LOG_DIR", log_dir)

    now = datetime(2026, 6, 10, 12, 0, 0)
    old_watch = {
        "event_id": "watch-old",
        "type": "shadow_watch",
        "decision_event_id": "decision-old",
        "bar_index": 10,
        "market": {"slug": "old-market"},
        "_t": (now - timedelta(hours=8)).isoformat(),
    }
    old_error = {
        "event_id": "error-old",
        "type": "shadow_watch_error",
        "decision_event_id": "decision-old",
        "error": "old failure",
        "_t": (now - timedelta(hours=8)).isoformat(),
    }
    recent_error = {
        "event_id": "error-recent",
        "type": "shadow_watch_error",
        "decision_event_id": "decision-recent",
        "error": "recent failure",
        "_t": (now - timedelta(hours=1)).isoformat(),
    }
    (shadow_dir / "events_12.jsonl").write_text(
        "\n".join(json.dumps(e) for e in [old_watch, old_error, recent_error]),
        encoding="utf-8",
    )

    report = server._shadow_market_report(now=now)

    assert report["latest"] is None
    assert report["latest_stale"] is True
    assert report["last_success_at"] == old_watch["_t"]
    assert [e["error"] for e in report["errors"]] == ["recent failure"]
    assert report["last_error_at"] == recent_error["_t"]


def test_shadow_market_report_filters_events_before_current_sidecar_start(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    log_dir = tmp_path / "data" / "logs"
    shadow_dir = tmp_path / "data" / "events" / "shadow_live" / "2026-06-10"
    shadow_dir.mkdir(parents=True)
    log_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_LOG_DIR", log_dir)

    now = datetime(2026, 6, 10, 12, 0, 0)
    old_error = {
        "event_id": "error-old-session",
        "type": "shadow_watch_error",
        "decision_event_id": "decision-old",
        "error": "previous sidecar failure",
        "_t": (now - timedelta(minutes=31)).isoformat(),
    }
    current_watch = {
        "event_id": "watch-current",
        "type": "shadow_watch",
        "decision_event_id": "decision-current",
        "bar_index": 20,
        "market": {"slug": "current-market"},
        "_t": (now - timedelta(minutes=5)).isoformat(),
    }
    (shadow_dir / "events_12.jsonl").write_text(
        "\n".join(json.dumps(e) for e in [old_error, current_watch]),
        encoding="utf-8",
    )
    sidecar_log = log_dir / "shadow_sidecar_auto_20260610_113000.out.log"
    sidecar_log.write_text("shadow_sidecar initialized\n", encoding="utf-8")

    report = server._shadow_market_report(now=now)

    assert report["current_sidecar_started_at"] == "2026-06-10 11:30:00"
    assert report["errors"] == []
    assert report["latest"]["market"]["slug"] == "current-market"


def test_live_intel_reports_max_open_rejection_opportunity(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    audit_dir = tmp_path / "data" / "events" / "paper_live" / "2026-06-10"
    log_dir = tmp_path / "data" / "logs"
    checkpoint_dir.mkdir(parents=True)
    audit_dir.mkdir(parents=True)
    log_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_LOG_DIR", log_dir)

    checkpoint = {
        "mode": "paper_live",
        "timestamp": datetime.now().isoformat(),
        "bar_index": 113,
        "balance": 500,
        "trades": [],
        "pending_orders": [],
        "open_orders": [],
    }
    (checkpoint_dir / "paper_live.json").write_text(json.dumps(checkpoint), encoding="utf-8")
    decisions = [
        {
            "n": 100,
            "type": "decision",
            "action": "BUY_UP",
            "dir5": "UP",
            "dir4": "UP",
            "regime": "NORMAL",
            "filt": True,
            "executable": True,
            "size": 25.0,
            "reference_price": 100.0,
            "_t": datetime.now().isoformat(),
        }
    ]
    (checkpoint_dir / "paper_live_events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in decisions),
        encoding="utf-8",
    )
    audit_events = [
        {
            "event_id": "reject-100",
            "type": "order_rejected",
            "bar_index": 100,
            "reason": "max open orders exceeded",
            "_t": datetime.now().isoformat(),
        }
    ]
    (audit_dir / "events_12.jsonl").write_text(
        "\n".join(json.dumps(e) for e in audit_events),
        encoding="utf-8",
    )

    with server.app.test_client() as client:
        response = client.get("/api/live-intel?limit=20")

    data = response.get_json()
    risk = data["risk"]
    assert risk["max_open_rejections_count"] == 1
    assert risk["missed_trades"][0]["entry_bar"] == 100
    assert risk["missed_trades"][0]["settle_bar"] == 113
    assert risk["missed_trades"][0]["reason"] == "max open orders exceeded"


def test_live_intel_builds_order_lifecycle_from_signal_execution_and_settlement(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    audit_dir = tmp_path / "data" / "events" / "paper_live" / "2026-06-10"
    log_dir = tmp_path / "data" / "logs"
    checkpoint_dir.mkdir(parents=True)
    audit_dir.mkdir(parents=True)
    log_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_LOG_DIR", log_dir)

    order = {
        "order_id": "sim:100:113:UP:25.00000000",
        "market_id": "btc-5m-next-113",
        "market_slug": "btc-updown-5m-1781137200",
        "market_end_iso": "2026-06-10T11:05:00Z",
        "decision_bar_ts": "2026-06-10T10:00:00Z",
        "entry_ts": "2026-06-10T10:05:00Z",
        "settle_ts": "2026-06-10T11:05:00Z",
        "entry_price_ts": "2026-06-10T10:00:00Z",
        "settle_price_ts": "2026-06-10T11:00:00Z",
        "entry_bar": 100,
        "settle_bar": 113,
        "direction": "UP",
        "size": 25.0,
        "maker_price": 0.49,
        "reference_price_source": "chainlink_candlestick",
        "reference_price": 63000.0,
        "binance_reference_price": 63002.0,
        "chainlink_reference_price": 63000.0,
        "status": "FILLED",
        "filled_bar": 101,
        "repost_count": 2,
    }
    trade = {
        **order,
        "pnl": 26.020408163265305,
        "won": True,
        "dir": "UP",
        "settlement_source": "chainlink_candlestick",
        "binance_entry_price": 63002.0,
        "chainlink_entry_price": 63000.0,
        "binance_settle_price": 63102.0,
        "chainlink_settle_price": 63100.0,
        "paper_actual_up_binance": True,
        "actual_up_chainlink": True,
        "settlement_disagrees": False,
        "settle_price": 63100.0,
        "status": "SETTLED",
    }
    checkpoint = {
        "mode": "paper_live",
        "timestamp": datetime.now().isoformat(),
        "bar_index": 114,
        "balance": 526.02,
        "trades": [trade],
        "pending_orders": [],
        "open_orders": [],
    }
    (checkpoint_dir / "paper_live.json").write_text(json.dumps(checkpoint), encoding="utf-8")
    decision = {
        "n": 100,
        "type": "decision",
        "event_id": "session:100:decision:decision",
        "action": "BUY_UP",
        "dir5": "UP",
        "dir4": "UP",
        "regime": "NORMAL",
        "filt": True,
        "executable": True,
        "size": 25.0,
        "reason": "Confidence 0.60",
        "block_reason": "",
        "_t": "2026-06-10T10:00:00",
    }
    (checkpoint_dir / "paper_live_events.jsonl").write_text(json.dumps(decision), encoding="utf-8")
    audit_events = [
        {
            "event_id": "created-100",
            "type": "order_created",
            "bar_index": 100,
            "order_id": order["order_id"],
            "order": {**order, "status": "OPEN", "filled_bar": None},
            "decision_event_id": decision["event_id"],
            "_t": "2026-06-10T10:00:01",
        },
        {
            "event_id": "filled-100",
            "type": "order_filled",
            "bar_index": 101,
            "order_id": order["order_id"],
            "order": order,
            "_t": "2026-06-10T10:05:00",
        },
        {
            "event_id": "settled-100",
            "type": "order_settled",
            "bar_index": 113,
            "order_id": order["order_id"],
            "order": order,
            "trade": trade,
            "balance": 526.02,
            "_t": "2026-06-10T11:05:00",
        },
    ]
    (audit_dir / "events_10.jsonl").write_text(
        "\n".join(json.dumps(e) for e in audit_events),
        encoding="utf-8",
    )

    with server.app.test_client() as client:
        response = client.get("/api/live-intel?limit=20")

    data = response.get_json()
    lifecycle = data["order_lifecycle"]
    assert lifecycle["anomalies"] == []
    assert lifecycle["recent"][0]["order_id"] == order["order_id"]
    assert lifecycle["recent"][0]["status"] == "SETTLED"
    assert lifecycle["recent"][0]["signal_time"] == "2026-06-10T10:00:00"
    assert lifecycle["recent"][0]["created_at"] == "2026-06-10T10:00:01"
    assert lifecycle["recent"][0]["filled_at"] == "2026-06-10T10:05:00"
    assert lifecycle["recent"][0]["settled_at"] == "2026-06-10T11:05:00"
    assert lifecycle["recent"][0]["pnl"] == 26.020408163265305
    assert lifecycle["recent"][0]["repost_count"] == 2
    assert lifecycle["recent"][0]["market_slug"] == "btc-updown-5m-1781137200"
    assert lifecycle["recent"][0]["market_end_iso"] == "2026-06-10T11:05:00Z"
    assert lifecycle["recent"][0]["entry_price_ts"] == "2026-06-10T10:00:00Z"
    assert lifecycle["recent"][0]["settle_price_ts"] == "2026-06-10T11:00:00Z"
    assert lifecycle["recent"][0]["reference_price_source"] == "chainlink_candlestick"
    assert lifecycle["recent"][0]["chainlink_reference_price"] == 63000.0
    assert lifecycle["recent"][0]["binance_reference_price"] == 63002.0
    assert lifecycle["recent"][0]["settlement_source"] == "chainlink_candlestick"
    assert lifecycle["recent"][0]["chainlink_settle_price"] == 63100.0
    assert lifecycle["recent"][0]["binance_settle_price"] == 63102.0
    assert lifecycle["recent"][0]["actual_up_chainlink"] is True
    assert lifecycle["recent"][0]["settlement_disagrees"] is False


def test_live_intel_reports_overdue_settlement_anomalies(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    audit_dir = tmp_path / "data" / "events" / "paper_live" / "2026-06-10"
    log_dir = tmp_path / "data" / "logs"
    checkpoint_dir.mkdir(parents=True)
    audit_dir.mkdir(parents=True)
    log_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_LOG_DIR", log_dir)

    overdue = {
        "order_id": "sim:100:113:UP:25.00000000",
        "market_id": "btc-5m-next-113",
        "entry_bar": 100,
        "settle_bar": 113,
        "direction": "UP",
        "size": 25.0,
        "maker_price": 0.49,
        "status": "FILLED",
        "filled_bar": 101,
    }
    checkpoint = {
        "mode": "paper_live",
        "timestamp": datetime.now().isoformat(),
        "bar_index": 116,
        "balance": 500,
        "trades": [],
        "pending_orders": [overdue],
        "open_orders": [],
    }
    (checkpoint_dir / "paper_live.json").write_text(json.dumps(checkpoint), encoding="utf-8")
    decision = {
        "n": 100,
        "type": "decision",
        "event_id": "session:100:decision:decision",
        "action": "BUY_UP",
        "dir5": "UP",
        "dir4": "UP",
        "regime": "NORMAL",
        "filt": True,
        "executable": True,
        "size": 25.0,
        "reason": "Confidence 0.60",
        "block_reason": "",
        "_t": "2026-06-10T10:00:00",
    }
    (checkpoint_dir / "paper_live_events.jsonl").write_text(json.dumps(decision), encoding="utf-8")
    (audit_dir / "events_10.jsonl").write_text(
        json.dumps({
            "event_id": "filled-100",
            "type": "order_filled",
            "bar_index": 101,
            "order_id": overdue["order_id"],
            "order": overdue,
            "_t": "2026-06-10T10:05:00",
        }),
        encoding="utf-8",
    )

    with server.app.test_client() as client:
        response = client.get("/api/live-intel?limit=20")

    data = response.get_json()
    anomaly = data["order_lifecycle"]["anomalies"][0]
    assert "settlement_overdue" in data["health"]["warnings"]
    assert anomaly["type"] == "settlement_overdue"
    assert anomaly["order_id"] == overdue["order_id"]
    assert anomaly["overdue_bars"] == 3
    assert data["order_lifecycle"]["recent"][0]["status"] == "SETTLEMENT_OVERDUE"


def test_live_intel_reports_maker_execution_quality_from_shadow_watch(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    audit_dir = tmp_path / "data" / "events" / "paper_live" / "2026-06-10"
    shadow_dir = tmp_path / "data" / "events" / "shadow_live" / "2026-06-10"
    log_dir = tmp_path / "data" / "logs"
    checkpoint_dir.mkdir(parents=True)
    audit_dir.mkdir(parents=True)
    shadow_dir.mkdir(parents=True)
    log_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_LOG_DIR", log_dir)

    checkpoint = {
        "mode": "paper_live",
        "timestamp": datetime.now().isoformat(),
        "bar_index": 120,
        "balance": 500,
        "trades": [],
        "pending_orders": [],
        "open_orders": [],
    }
    (checkpoint_dir / "paper_live.json").write_text(json.dumps(checkpoint), encoding="utf-8")
    decision = {
        "n": 100,
        "type": "decision",
        "event_id": "session:100:decision:decision",
        "action": "BUY_UP",
        "dir5": "UP",
        "dir4": "UP",
        "regime": "NORMAL",
        "filt": True,
        "executable": True,
        "size": 25.0,
        "reason": "Confidence 0.60",
        "block_reason": "",
        "_t": "2026-06-10T10:00:00",
    }
    (checkpoint_dir / "paper_live_events.jsonl").write_text(json.dumps(decision), encoding="utf-8")
    audit_events = [
        {
            "event_id": "reject-100",
            "type": "order_rejected",
            "bar_index": 100,
            "reason": "price cap exceeded",
            "_t": "2026-06-10T10:00:02",
        }
    ]
    (audit_dir / "events_10.jsonl").write_text(
        "\n".join(json.dumps(e) for e in audit_events),
        encoding="utf-8",
    )
    shadow_events = [
        {
            "event_id": f"shadow-{sample}",
            "type": "shadow_watch",
            "decision_event_id": decision["event_id"],
            "bar_index": 100,
            "sample": sample,
            "action": action,
            "price": price,
            "best_bid": bid,
            "best_ask": 0.53,
            "token_id": "token-up",
            "market": {"id": "pm-1", "slug": "bitcoin-up-or-down-next"},
            "_t": f"2026-06-10T10:00:0{sample}",
        }
        for sample, action, price, bid in [
            (0, "OPEN", 0.49, 0.49),
            (1, "HOLD", 0.49, 0.49),
            (2, "REPOST", 0.51, 0.51),
            (3, "BLOCK", 0.52, 0.53),
        ]
    ]
    shadow_events.append({
        "event_id": "shadow-error-100",
        "type": "shadow_watch_error",
        "decision_event_id": decision["event_id"],
        "error": "api timeout",
        "_t": "2026-06-10T10:00:05",
    })
    (shadow_dir / "events_10.jsonl").write_text(
        "\n".join(json.dumps(e) for e in shadow_events),
        encoding="utf-8",
    )

    with server.app.test_client() as client:
        response = client.get("/api/live-intel?limit=20")

    data = response.get_json()
    quality = data["maker_quality"]
    assert quality["summary"]["watch_count"] == 1
    assert quality["summary"]["avg_buy_one_rate"] == 0.75
    assert quality["summary"]["total_reposts"] == 1
    assert quality["summary"]["total_price_cap_blocks"] == 1
    assert quality["summary"]["api_error_count"] == 1
    assert quality["recent"][0]["decision_event_id"] == decision["event_id"]
    assert quality["recent"][0]["buy_one_rate"] == 0.75
    assert quality["recent"][0]["average_target_price"] == 0.5
    assert quality["recent"][0]["max_target_price"] == 0.51
    assert quality["recent"][0]["not_filled_reason"] == "price cap exceeded"
