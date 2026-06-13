from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from api import server


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


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
    items = {item["key"]: item for item in refresh["items"]}

    assert refresh["ready"] is False
    assert refresh["total"] == 3
    assert refresh["stale_count"] == 1
    assert refresh["blocked_count"] >= 1
    assert items["live_gate"]["report"].endswith("live_trade_gate_latest.json")
    assert items["live_gate"]["available"] is True
    assert items["live_gate"]["fresh"] is True
    assert items["live_gate"]["status"] == "blocked"
    assert items["live_gate"]["next_action"] == "Clear live gate blockers"
    assert items["live_gate"]["blockers"] == ["balance_meets_minimum"]
    assert items["live_preflight"]["fresh"] is False
    assert items["live_preflight"]["status"] == "stale"
    assert items["live_preflight"]["next_action"] == "Refresh live preflight chain"
    assert items["clob_readonly"]["status"] == "blocked"
    assert items["clob_readonly"]["next_action"] == "Run live trade gate report"


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
    today_prefix = now.strftime("%Y-%m-%d")
    yesterday = now - timedelta(days=1)
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

    assert today["day_utc"] == today_prefix
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


def test_live_page_mounts_today_cockpit():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "function TodayCockpit" in source
    assert "<TodayCockpit today={safety?.today}" in source
    assert "safety?.today" in source
    assert "Maker Target" in source
    assert "0.49" in source


def test_live_page_uses_today_summary_for_top_kpis():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "const todayStats = safety?.today" in source
    assert "const todayPnl = todayStats?.trades.pnl_usdc" in source
    assert "const todaySettled = todayStats?.trades.settled" in source
    assert "const todayWinRate = todayStats?.trades.win_rate" in source
    assert "const todaySignalPassRate = todayStats?.signals.pass_rate" in source
    assert "Today ${signedMoney(todayPnl)}" in source
    assert "today trades" in source
    assert "today pass rate" in source


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
    assert 'label="Readiness"' in source
    assert 'value={`${readinessPassed}/${readinessTotal}`}' in source
    assert "Critical ${readinessCritical}" in source
    assert "xl:grid-cols-9" in source


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
    assert "Manual confirmation" in source
    assert "requires_confirmation" in source


def test_live_page_surfaces_operator_summary_card():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "operator_summary" in source
    assert "const operator = safety?.operator_summary" in source
    assert "operator?.current_stage_label" in source
    assert "operator?.next_action" in source
    assert "operator?.primary_blocker" in source
    assert 'label="Next"' in source
    assert "xl:grid-cols-9" in source


def test_live_page_surfaces_today_dryrun_summary():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "dryrun: {" in source
    assert "today?.dryrun.records" in source
    assert "today?.dryrun.would_place" in source
    assert "today?.dryrun.blocked" in source
    assert "today?.dryrun.submitted" in source
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


def test_live_page_surfaces_report_freshness_panel():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "report_refresh" in source
    assert "function ReportFreshnessPanel" in source
    assert "<ReportFreshnessPanel refresh={safety?.report_refresh}" in source
    assert "Report Freshness" in source
    assert "item.next_action" in source
    assert "item.status" in source
    assert "item.age_seconds" in source
    assert "item.report" in source


def test_live_page_surfaces_market_data_panel():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "market_data" in source
    assert "function MarketDataPanel" in source
    assert "<MarketDataPanel data={safety?.market_data}" in source
    assert "Market Data" in source
    assert "data?.price_age_seconds" in source
    assert "data?.received_age_seconds" in source
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
    assert "const marketDataAge = ageLabel(safety?.market_data?.price_age_seconds)" in source
    assert "Market Data" in source
    assert "Market ${marketDataReady ? marketDataAge : safety?.market_data?.status ?? \"-\"}" in source
    assert "safety?.market_data?.source" in source


def test_status_bar_surfaces_operator_next_action():
    source = Path("web/src/components/StatusBar.tsx").read_text(encoding="utf-8")

    assert "operator_summary?: {" in source
    assert "const operator = safety?.operator_summary" in source
    assert "const operatorNextAction = operator?.next_action" in source
    assert "const operatorStage = operator?.current_stage_label" in source
    assert "Next ${operatorStage}" in source
    assert 'DetailTile label="Operator Stage"' in source
    assert 'DetailTile label="Next Action"' in source


def test_status_bar_surfaces_funding_shortfalls():
    source = Path("web/src/components/StatusBar.tsx").read_text(encoding="utf-8")

    assert "safety?.funding?.balance_shortfall_usdc" in source
    assert "safety?.funding?.allowance_shortfall_usdc" in source
    assert "safety?.funding?.funding_ready" in source
    assert "Balance Gap" in source
    assert "Allowance Gap" in source


def test_status_bar_surfaces_today_summary():
    source = Path("web/src/components/StatusBar.tsx").read_text(encoding="utf-8")

    assert "today?: {" in source
    assert "const today = safety?.today" in source
    assert "Today ${signedMoney(todayPnl)}" in source
    assert "Signal ${todaySignalsPassed}/${todaySignalsTotal}" in source
    assert "W/L ${todayWins}/${todayLosses}" in source
    assert "Today PnL" in source
    assert "Today Signals" in source


def test_status_bar_surfaces_today_activity_freshness():
    source = Path("web/src/components/StatusBar.tsx").read_text(encoding="utf-8")

    assert "activity?: {" in source
    assert "latest_signal_age_seconds?: number | null" in source
    assert "latest_dryrun_age_seconds?: number | null" in source
    assert "const latestSignalAge = ageLabel(today?.activity?.latest_signal_age_seconds)" in source
    assert "const latestDryrunAge = ageLabel(today?.activity?.latest_dryrun_age_seconds)" in source
    assert "const todayActivityFresh = (today?.activity?.latest_signal_age_seconds ?? 9999) < 600" in source
    assert "Activity ${latestSignalAge}" in source
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
