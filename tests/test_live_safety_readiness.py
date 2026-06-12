from __future__ import annotations

import json
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


def test_status_bar_surfaces_dryrun_gate_and_preflight_status():
    source = Path("web/src/components/StatusBar.tsx").read_text(encoding="utf-8")

    assert "safety?.dryrun?.submitted_count" in source
    assert "safety?.live_gate?.ready_for_live_smoke" in source
    assert "safety?.preflight_chain?.ok" in source
    assert "safety?.preflight_chain?.fresh" in source
    assert "safety?.preflight_chain?.age_seconds" in source
    assert "Dry-run" in source
    assert "Preflight" in source


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
