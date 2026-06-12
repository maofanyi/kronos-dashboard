from __future__ import annotations

import json
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
    assert checks["dryrun_no_submitted_orders"]["ok"] is True
    assert checks["live_trade_gate_ready"]["ok"] is False
    assert checks["minimum_balance"]["ok"] is False
    assert checks["minimum_allowance"]["ok"] is False


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


def test_live_page_mounts_safety_panels():
    source = Path("web/src/pages/Live.tsx").read_text(encoding="utf-8")

    assert "<SafetyStrip safety={safety} health={health} />" in source
    assert "<ReadinessChecklist safety={safety} health={health} intel={intel} />" in source


def test_status_bar_surfaces_dryrun_gate_status():
    source = Path("web/src/components/StatusBar.tsx").read_text(encoding="utf-8")

    assert "safety?.dryrun?.submitted_count" in source
    assert "safety?.live_gate?.ready_for_live_smoke" in source
    assert "Dry-run" in source
