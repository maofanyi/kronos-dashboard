from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from api import server


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _append_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def _plus_minutes(value: str, minutes: int) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (parsed + timedelta(minutes=minutes)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _candidate_record(candidate_id: str, *, passed: bool, side: str | None, created_at: str, overlap: bool = False) -> dict:
    return {
        "type": "candidate_no_submit_signal",
        "record_key": f"{created_at}|{candidate_id}",
        "created_at": created_at,
        "candidate_id": candidate_id,
        "candidate_config_path": f"data/config/research_candidates/{candidate_id}.json",
        "entry_ts": created_at,
        "settle_ts": _plus_minutes(created_at, 5),
        "market_slug": "btc-updown-5m-1782950700",
        "p5_up": 0.72,
        "p1_up": 0.55,
        "p4_up": 0.68,
        "current_live": {"would_place_order": True, "submitted": False, "action": "BUY_UP", "side": "LONG"},
        "candidate": {
            "passed": passed,
            "side": side,
            "action": "BUY_UP" if side == "LONG" else "BUY_DOWN" if side == "SHORT" else "HOLD",
            "reason_code": "long_passed" if side == "LONG" else "short_passed" if side == "SHORT" else "no_side_passed",
            "long_score": 410.0,
            "short_score": 220.0,
        },
        "same_side_overlap": overlap,
        "candidate_only": bool(passed and not overlap),
        "live_signal_filtered": bool(not overlap),
        "submitted": False,
        "no_submit": True,
    }


def test_strategy_comparison_includes_official_30d_candidate():
    ids = {row["candidate_id"] for row in server.STRATEGY_COMPARE_CANDIDATES}
    assert "official_truth_30d30d_latest" in ids


def test_strategy_compare_accepts_finer_buckets():
    assert server._strategy_compare_filters({"bucket": "5m"})["bucket"] == "5m"
    assert server._strategy_compare_filters({"bucket": "30m"})["bucket"] == "30m"
    assert server._strategy_compare_filters({"bucket": "1h"})["bucket"] == "hour"
    assert server._strategy_compare_filters({"bucket": "4h"})["bucket"] == "4h"
    assert server._strategy_compare_filters({"bucket": "6h"})["bucket"] == "6h"
    assert server._strategy_compare_filters({"bucket": "1d"})["bucket"] == "day"


def test_strategy_comparison_reads_more_than_short_tail_for_coverage():
    assert server.STRATEGY_COMPARISON_SIGNAL_READ_LIMIT >= 200000


def test_strategy_timeseries_groups_four_hour_buckets(monkeypatch, tmp_path):
    report_dir = tmp_path / "reports"
    checkpoint_dir = tmp_path / "checkpoints"
    report_dir.mkdir()
    checkpoint_dir.mkdir()
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(
        server,
        "_live_order_sync_summary",
        lambda: {"ok": True, "fresh": True, "available": True, "age_seconds": 12},
    )

    _append_jsonl(
        report_dir / "candidate_no_submit_official_truth_signals.jsonl",
        [
            _candidate_record("official_truth_14d14d_latest", passed=True, side="LONG", created_at="2026-07-02T00:05:00Z"),
            _candidate_record("official_truth_14d14d_latest", passed=False, side=None, created_at="2026-07-02T01:55:00Z"),
            _candidate_record("official_truth_14d14d_latest", passed=True, side="SHORT", created_at="2026-07-02T04:05:00Z"),
            _candidate_record("official_truth_14d14d_latest", passed=False, side=None, created_at="2026-07-02T05:55:00Z"),
        ],
    )

    payload = server._strategy_comparison_payload(
        now=datetime(2026, 7, 2, 8, 0, tzinfo=timezone.utc),
        args={"window": "all", "bucket": "4h", "metric": "signals", "candidates": "official_truth_14d14d_latest"},
    )

    series = [row for row in payload["timeseries"] if row["candidate_id"] == "official_truth_14d14d_latest"]
    assert payload["filters"]["bucket"] == "4h"
    assert [row["evaluated"] for row in series] == [2, 2]
    assert [row["passed"] for row in series] == [1, 1]


def test_strategy_window_uses_entry_ts_not_backfill_created_at(monkeypatch, tmp_path):
    report_dir = tmp_path / "reports"
    checkpoint_dir = tmp_path / "checkpoints"
    report_dir.mkdir()
    checkpoint_dir.mkdir()
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(
        server,
        "_live_order_sync_summary",
        lambda: {"ok": True, "fresh": True, "available": True, "age_seconds": 12},
    )

    old_30d = _candidate_record(
        "official_truth_30d30d_latest",
        passed=True,
        side="LONG",
        created_at="2026-07-04T10:00:00Z",
    )
    old_30d["entry_ts"] = "2026-07-02T00:05:00Z"
    old_30d["settle_ts"] = "2026-07-02T00:10:00Z"
    current_30d = _candidate_record(
        "official_truth_30d30d_latest",
        passed=True,
        side="SHORT",
        created_at="2026-07-04T10:05:00Z",
    )
    current_30d["entry_ts"] = "2026-07-04T10:05:00Z"
    current_30d["settle_ts"] = "2026-07-04T10:10:00Z"
    current_14d = _candidate_record(
        "official_truth_14d14d_latest",
        passed=True,
        side="SHORT",
        created_at="2026-07-04T10:05:00Z",
    )
    current_14d["entry_ts"] = "2026-07-04T10:05:00Z"
    current_14d["settle_ts"] = "2026-07-04T10:10:00Z"
    _append_jsonl(
        report_dir / "candidate_no_submit_official_truth_signals.jsonl",
        [old_30d, current_30d, current_14d],
    )

    payload = server._strategy_comparison_payload(
        now=datetime(2026, 7, 4, 10, 30, tzinfo=timezone.utc),
        args={"window": "today", "bucket": "5m", "metric": "signals"},
    )

    by_id = {row["candidate_id"]: row for row in payload["window_candidates"]}
    assert by_id["official_truth_30d30d_latest"]["evaluated"] == 1
    assert by_id["official_truth_14d14d_latest"]["evaluated"] == 1
    series_30d = [row for row in payload["timeseries"] if row["candidate_id"] == "official_truth_30d30d_latest"]
    assert [row["evaluated"] for row in series_30d] == [1]


def test_strategy_comparison_summarizes_candidate_collection(monkeypatch, tmp_path):
    report_dir = tmp_path / "reports"
    checkpoint_dir = tmp_path / "checkpoints"
    report_dir.mkdir()
    checkpoint_dir.mkdir()
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(
        server,
        "_live_order_sync_summary",
        lambda: {"ok": True, "fresh": True, "available": True, "age_seconds": 12},
    )
    first_ts = (
        datetime.now(timezone.utc) - timedelta(hours=1)
    ).replace(second=0, microsecond=0).isoformat().replace("+00:00", "Z")
    second_ts = _plus_minutes(first_ts, 5)

    _append_jsonl(
        report_dir / "prediction_bound_live_formal_predictions.jsonl",
        [
            {"created_at": first_ts, "would_place_order": True, "submitted": False},
            {"created_at": second_ts, "would_place_order": False, "submitted": False},
        ],
    )
    _append_jsonl(
        report_dir / "candidate_no_submit_official_truth_signals.jsonl",
        [
            _candidate_record("round2_drawdown_density", passed=True, side="LONG", created_at=first_ts, overlap=True),
            _candidate_record("official_truth_14d14d_latest", passed=True, side="SHORT", created_at=first_ts),
            _candidate_record("official_truth_14d14d_latest", passed=False, side=None, created_at=second_ts),
            _candidate_record("official_truth_7d7d_latest", passed=True, side="LONG", created_at=second_ts, overlap=True),
        ],
    )
    _write_json(report_dir / "candidate_no_submit_official_truth_latest.json", {"ok": True, "total_records": 4})

    with server.app.test_client() as client:
        payload = client.get("/api/strategy-comparison").get_json()

    assert payload["ok"] is True
    assert payload["collection"]["signals_file_exists"] is True
    assert payload["live"]["prediction_rows"] == 2
    assert payload["live"]["would_place"] == 1
    by_id = {row["candidate_id"]: row for row in payload["candidates"]}
    assert by_id["official_truth_14d14d_latest"]["evaluated"] == 2
    assert by_id["official_truth_14d14d_latest"]["passed"] == 1
    assert by_id["official_truth_14d14d_latest"]["short"] == 1
    assert by_id["official_truth_7d7d_latest"]["same_side_overlap"] == 1
    assert payload["recent_signals"]
    assert all(row["submitted"] is False for row in payload["recent_signals"])
    assert payload["filters"]["window"] == "7d"
    assert payload["filters"]["bucket"] == "day"
    assert payload["filters"]["metric"] == "signals"
    series = payload["timeseries"]
    assert series
    official_14d_series = [row for row in series if row["candidate_id"] == "official_truth_14d14d_latest"]
    assert official_14d_series[0]["evaluated"] == 2
    assert official_14d_series[0]["passed"] == 1
    assert official_14d_series[0]["pnl_usdc"] is None
    assert official_14d_series[0]["scored"] is False


def test_strategy_collection_coverage_uses_entry_time_not_rebuild_created_at(monkeypatch, tmp_path):
    report_dir = tmp_path / "reports"
    checkpoint_dir = tmp_path / "checkpoints"
    report_dir.mkdir()
    checkpoint_dir.mkdir()
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(
        server,
        "_live_order_sync_summary",
        lambda: {"ok": True, "fresh": True, "available": True, "age_seconds": 12},
    )

    old_entry = _candidate_record(
        "official_truth_14d14d_latest",
        passed=True,
        side="LONG",
        created_at="2026-07-05T16:05:00Z",
    )
    old_entry["entry_ts"] = "2026-07-02T00:05:00Z"
    old_entry["settle_ts"] = "2026-07-02T00:10:00Z"
    new_entry = _candidate_record(
        "official_truth_14d14d_latest",
        passed=False,
        side=None,
        created_at="2026-07-05T16:05:00Z",
    )
    new_entry["entry_ts"] = "2026-07-05T16:05:00Z"
    new_entry["settle_ts"] = "2026-07-05T16:10:00Z"
    _append_jsonl(report_dir / "candidate_no_submit_official_truth_signals.jsonl", [old_entry, new_entry])
    _write_json(report_dir / "candidate_no_submit_official_truth_latest.json", {"ok": True, "total_records": 2})

    payload = server._strategy_comparison_payload(
        now=datetime(2026, 7, 5, 16, 30, tzinfo=timezone.utc),
        args={"window": "7d", "bucket": "day", "metric": "signals"},
    )

    assert payload["collection"]["first_signal_at"] == "2026-07-02T00:05:00Z"
    assert payload["collection"]["collection_days"] > 3.0
    assert payload["window_coverage"]["data_start_at"] == "2026-07-02T00:05:00Z"
    assert payload["window_coverage"]["covered_days"] > 3.0


def test_strategy_24h_window_is_rolling_not_dashboard_day(monkeypatch, tmp_path):
    report_dir = tmp_path / "reports"
    checkpoint_dir = tmp_path / "checkpoints"
    report_dir.mkdir()
    checkpoint_dir.mkdir()
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(
        server,
        "_live_order_sync_summary",
        lambda: {"ok": True, "fresh": True, "available": True, "age_seconds": 12},
    )

    prior_trading_day = _candidate_record(
        "official_truth_14d14d_latest",
        passed=True,
        side="LONG",
        created_at="2026-07-05T16:05:00Z",
    )
    prior_trading_day["entry_ts"] = "2026-07-04T17:00:00Z"
    current_trading_day = _candidate_record(
        "official_truth_14d14d_latest",
        passed=True,
        side="SHORT",
        created_at="2026-07-05T16:05:00Z",
    )
    current_trading_day["entry_ts"] = "2026-07-05T16:05:00Z"
    _append_jsonl(
        report_dir / "candidate_no_submit_official_truth_signals.jsonl",
        [prior_trading_day, current_trading_day],
    )

    payload = server._strategy_comparison_payload(
        now=datetime(2026, 7, 5, 16, 30, tzinfo=timezone.utc),
        args={"window": "24h", "bucket": "hour", "metric": "signals", "candidates": "official_truth_14d14d_latest"},
    )

    by_id = {row["candidate_id"]: row for row in payload["window_candidates"]}
    assert by_id["official_truth_14d14d_latest"]["evaluated"] == 2
    assert by_id["official_truth_14d14d_latest"]["passed"] == 2
    assert payload["window_coverage"]["requested_start_at"] == "2026-07-04T16:30:00Z"
    assert payload["window_coverage"]["time_basis"] == "rolling_24h"


def test_strategy_window_filters_out_future_entry_times(monkeypatch, tmp_path):
    report_dir = tmp_path / "reports"
    checkpoint_dir = tmp_path / "checkpoints"
    report_dir.mkdir()
    checkpoint_dir.mkdir()
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(
        server,
        "_live_order_sync_summary",
        lambda: {"ok": True, "fresh": True, "available": True, "age_seconds": 12},
    )

    settled = _candidate_record(
        "official_truth_7d7d_latest",
        passed=True,
        side="LONG",
        created_at="2026-07-05T16:05:00Z",
    )
    settled["entry_ts"] = "2026-07-05T16:05:00Z"
    future = _candidate_record(
        "official_truth_7d7d_latest",
        passed=True,
        side="SHORT",
        created_at="2026-07-05T16:05:00Z",
    )
    future["entry_ts"] = "2026-07-05T16:35:00Z"
    _append_jsonl(report_dir / "candidate_no_submit_official_truth_signals.jsonl", [settled, future])

    payload = server._strategy_comparison_payload(
        now=datetime(2026, 7, 5, 16, 30, tzinfo=timezone.utc),
        args={"window": "today", "bucket": "5m", "metric": "signals", "candidates": "official_truth_7d7d_latest"},
    )

    by_id = {row["candidate_id"]: row for row in payload["window_candidates"]}
    assert by_id["official_truth_7d7d_latest"]["evaluated"] == 1
    assert by_id["official_truth_7d7d_latest"]["passed"] == 1
    assert [row["bucket"] for row in payload["timeseries"]] == ["2026-07-05T16:05:00Z"]


def test_strategy_comparison_reports_missing_no_submit_file(monkeypatch, tmp_path):
    report_dir = tmp_path / "reports"
    checkpoint_dir = tmp_path / "checkpoints"
    report_dir.mkdir()
    checkpoint_dir.mkdir()
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(
        server,
        "_live_order_sync_summary",
        lambda: {"ok": False, "fresh": False, "available": False, "age_seconds": None},
    )

    with server.app.test_client() as client:
        payload = client.get("/api/strategy-comparison").get_json()

    assert payload["ok"] is False
    assert payload["collection"]["signals_file_exists"] is False
    assert "candidate_no_submit_official_truth_signals.jsonl missing" in payload["warnings"]
    assert payload["candidates"]
    assert all(row["evaluated"] == 0 for row in payload["candidates"])
    assert payload["timeseries"] == []


def test_strategy_comparison_limits_recent_signals(monkeypatch, tmp_path):
    report_dir = tmp_path / "reports"
    checkpoint_dir = tmp_path / "checkpoints"
    report_dir.mkdir()
    checkpoint_dir.mkdir()
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(
        server,
        "_live_order_sync_summary",
        lambda: {"ok": True, "fresh": True, "available": True, "age_seconds": 12},
    )
    rows = [
        _candidate_record(
            "official_truth_14d14d_latest",
            passed=True,
            side="LONG",
            created_at=f"2026-07-02T00:{minute:02d}:00Z",
        )
        for minute in range(12)
    ]
    _append_jsonl(report_dir / "candidate_no_submit_official_truth_signals.jsonl", rows)
    _write_json(report_dir / "candidate_no_submit_official_truth_latest.json", {"ok": True, "total_records": len(rows)})

    with server.app.test_client() as client:
        payload = client.get("/api/strategy-comparison").get_json()

    assert len(payload["recent_signals"]) == 10
    assert payload["recent_signals"][0]["created_at"] == "2026-07-02T00:11:00Z"
    assert payload["recent_signals"][-1]["created_at"] == "2026-07-02T00:02:00Z"


def test_strategy_comparison_uses_scored_summary_for_pnl_chart(monkeypatch, tmp_path):
    report_dir = tmp_path / "reports"
    checkpoint_dir = tmp_path / "checkpoints"
    report_dir.mkdir()
    checkpoint_dir.mkdir()
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(
        server,
        "_live_order_sync_summary",
        lambda: {"ok": True, "fresh": True, "available": True, "age_seconds": 12},
    )
    _append_jsonl(
        report_dir / "candidate_no_submit_official_truth_signals.jsonl",
        [_candidate_record("official_truth_14d14d_latest", passed=True, side="LONG", created_at="2026-07-02T00:00:00Z")],
    )
    _write_json(
        report_dir / "candidate_no_submit_official_truth_scored_summary.json",
        {
            "candidates": [
                {
                    "candidate_id": "official_truth_14d14d_latest",
                    "total_pnl": 12.5,
                    "win_rate": 0.55,
                    "daily": [
                        {
                            "bucket": "2026-07-02",
                            "pnl_usdc": 12.5,
                            "wins": 11,
                            "losses": 9,
                            "win_rate": 0.55,
                        }
                    ],
                }
            ]
        },
    )

    with server.app.test_client() as client:
        payload = client.get("/api/strategy-comparison?metric=pnl&window=all").get_json()

    series = [row for row in payload["timeseries"] if row["candidate_id"] == "official_truth_14d14d_latest"]
    assert series[0]["pnl_usdc"] == 12.5
    assert series[0]["wins"] == 11
    assert series[0]["losses"] == 9
    assert series[0]["win_rate"] == 0.55
    assert series[0]["scored"] is True


def test_strategy_comparison_adds_live_matched_real_comparison(monkeypatch, tmp_path):
    report_dir = tmp_path / "reports"
    checkpoint_dir = tmp_path / "checkpoints"
    report_dir.mkdir()
    checkpoint_dir.mkdir()
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(
        server,
        "_live_order_sync_summary",
        lambda: {"ok": True, "fresh": True, "available": True, "age_seconds": 12},
    )
    _append_jsonl(
        report_dir / "candidate_no_submit_official_truth_signals.jsonl",
        [
            _candidate_record("official_truth_14d14d_latest", passed=True, side="LONG", created_at="2026-07-02T00:00:00Z"),
            _candidate_record("official_truth_14d14d_latest", passed=True, side="SHORT", created_at="2026-07-02T00:05:00Z"),
        ],
    )
    _write_json(report_dir / "candidate_no_submit_official_truth_latest.json", {"ok": True, "total_records": 2})
    _write_json(
        report_dir / "candidate_no_submit_official_truth_scored_summary.json",
        {
            "default_maker_price": 0.49,
            "size_shares": 5.0,
            "candidates": [
                {
                    "candidate_id": "official_truth_14d14d_latest",
                    "trades": [
                        {
                            "entry_ts": "2026-07-02T00:05:00Z",
                            "settle_ts": "2026-07-02T00:10:00Z",
                            "side": "SHORT",
                            "won": True,
                            "pnl_usdc": 2.55,
                            "maker_price": 0.49,
                            "size_shares": 5.0,
                        },
                        {
                            "entry_ts": "2026-07-02T00:05:00Z",
                            "settle_ts": "2026-07-02T00:10:00Z",
                            "side": "LONG",
                            "won": True,
                            "pnl_usdc": 2.55,
                            "maker_price": 0.49,
                            "size_shares": 5.0,
                        },
                        {
                            "entry_ts": "2026-07-02T00:10:00Z",
                            "settle_ts": "2026-07-02T00:15:00Z",
                            "side": "SHORT",
                            "won": False,
                            "pnl_usdc": -2.45,
                            "maker_price": 0.49,
                            "size_shares": 5.0,
                        },
                    ],
                }
            ],
        },
    )
    _write_json(
        checkpoint_dir / "live_real_orders_current_next.json",
        [
            {
                "status": "SETTLED",
                "entry_ts": "2026-07-02T00:05:00Z",
                "settle_ts": "2026-07-02T00:10:00Z",
                "side": "LONG",
                "won": True,
                "fill_size": 10.0,
                "average_fill_price": 0.5,
                "reference_price_source": "chainlink_datastreams_pending",
            },
            {
                "status": "SETTLED",
                "entry_ts": "2026-07-02T00:15:00Z",
                "settle_ts": "2026-07-02T00:20:00Z",
                "side": "SHORT",
                "won": False,
                "fill_size": 10.0,
                "average_fill_price": 0.51,
                "reference_price_source": "chainlink_datastreams_pending",
            },
            {
                "status": "SETTLED",
                "entry_ts": "2026-07-02T00:20:00Z",
                "settle_ts": "2026-07-02T00:25:00Z",
                "side": "LONG",
                "won": True,
                "fill_size": 10.0,
                "average_fill_price": 0.49,
                "reference_price_source": "binance_kline",
            },
        ],
    )

    with server.app.test_client() as client:
        payload = client.get("/api/strategy-comparison?window=all").get_json()

    live_matched = payload["live_matched"]
    assert live_matched["available"] is True
    by_id = {row["candidate_id"]: row for row in live_matched["candidates"]}
    official = by_id["official_truth_14d14d_latest"]
    assert by_id["official_truth_7d7d_latest"]["pnl_delta"] is None
    assert official["overlap_count"] == 1
    assert official["live_only_count"] == 1
    assert official["scored_only_count"] == 1
    assert official["side_mismatches"] == 1
    assert official["won_mismatches"] == 0
    assert official["pnl_delta"] == -0.2
    assert official["all_live"]["settled"] == 2
    assert official["all_scored"]["settled"] == 2
    assert official["overlap"]["live_actual_pnl"] == 5.0
    assert official["overlap"]["live_normalized_pnl"] == 2.55
    assert official["overlap"]["scored_pnl"] == 2.55
    assert official["live_only"]["live_actual_pnl"] == -5.1
    assert official["live_only"]["live_normalized_pnl"] == -2.45
    assert official["scored_only"]["scored_pnl"] == -2.45
    window_by_id = {row["candidate_id"]: row for row in payload["window_candidates"]}
    assert window_by_id["official_truth_14d14d_latest"]["wins"] == 1
    assert window_by_id["official_truth_14d14d_latest"]["losses"] == 1
    assert window_by_id["official_truth_14d14d_latest"]["win_rate"] == 0.5
    assert window_by_id["official_truth_14d14d_latest"]["pnl_usdc"] == 0.1


def test_strategy_live_matched_pnl_delta_is_unknown_without_ledger(
    monkeypatch,
    tmp_path,
):
    report_dir = tmp_path / "reports"
    checkpoint_dir = tmp_path / "checkpoints"
    report_dir.mkdir()
    checkpoint_dir.mkdir()
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    scored_path = (
        report_dir / "candidate_no_submit_official_truth_scored_summary.json"
    )
    _write_json(
        scored_path,
        {
            "candidates": [
                {
                    "candidate_id": "official_truth_14d14d_latest",
                    "trades": [],
                }
            ]
        },
    )
    candidate_specs = [
        spec
        for spec in server.STRATEGY_COMPARE_CANDIDATES
        if spec["candidate_id"] == "official_truth_14d14d_latest"
    ]

    payload = server._strategy_live_matched_comparison(
        candidate_specs,
        now_dt=datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc),
        filters=server._strategy_compare_filters({"window": "all"}),
        scored_by_candidate=server._strategy_scored_summary_by_candidate(
            scored_path
        ),
        scored_path=scored_path,
    )

    assert payload["available"] is False
    assert payload["candidates"][0]["scored_available"] is True
    assert payload["candidates"][0]["pnl_delta"] is None


def test_strategy_comparison_falls_back_to_live_config_sizing(monkeypatch, tmp_path):
    report_dir = tmp_path / "reports"
    checkpoint_dir = tmp_path / "checkpoints"
    config_dir = tmp_path / "config"
    report_dir.mkdir()
    checkpoint_dir.mkdir()
    config_dir.mkdir()
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_CONFIG_DIR", config_dir)
    monkeypatch.setattr(
        server,
        "_live_order_sync_summary",
        lambda: {"ok": True, "fresh": True, "available": True, "age_seconds": 12},
    )
    _append_jsonl(
        report_dir / "candidate_no_submit_official_truth_signals.jsonl",
        [
            _candidate_record(
                "official_truth_14d14d_latest",
                passed=True,
                side="LONG",
                created_at="2026-07-02T00:00:00Z",
            )
        ],
    )
    _write_json(report_dir / "candidate_no_submit_official_truth_latest.json", {"ok": True, "total_records": 1})
    _write_json(
        config_dir / "aligned_prod_current_next_official_truth_14d14d_live_params.json",
        {
            "size_shares": 10.0,
            "maker_price_assumption": 0.49,
            "live_restart_contract": {
                "order_size_shares": 10.0,
                "min_price": 0.49,
            },
        },
    )
    _write_json(
        report_dir / "candidate_no_submit_official_truth_scored_summary.json",
        {
            "candidates": [
                {
                    "candidate_id": "official_truth_14d14d_latest",
                    "trades": [
                        {
                            "entry_ts": "2026-07-02T00:05:00Z",
                            "settle_ts": "2026-07-02T00:10:00Z",
                            "side": "LONG",
                            "won": True,
                        },
                    ],
                }
            ],
        },
    )
    _write_json(
        checkpoint_dir / "live_real_orders_current_next.json",
        [
            {
                "status": "SETTLED",
                "entry_ts": "2026-07-02T00:05:00Z",
                "settle_ts": "2026-07-02T00:10:00Z",
                "side": "LONG",
                "won": True,
                "fill_size": 10.0,
                "average_fill_price": 0.49,
                "reference_price_source": "chainlink_datastreams_pending",
            },
        ],
    )

    payload = server._strategy_comparison_payload(
        now=datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc),
        args={"window": "all", "bucket": "day", "metric": "pnl"},
    )

    live_by_id = {row["candidate_id"]: row for row in payload["live_matched"]["candidates"]}
    official = live_by_id["official_truth_14d14d_latest"]
    assert payload["live_matched"]["size_shares"] == 10.0
    assert payload["live_matched"]["maker_price"] == 0.49
    assert official["overlap"]["live_normalized_pnl"] == 5.1
    assert official["overlap"]["scored_pnl"] == 5.1
    series = [
        row for row in payload["timeseries"]
        if row["candidate_id"] == "official_truth_14d14d_latest"
    ]
    assert series[0]["pnl_usdc"] == 5.1


def test_strategy_comparison_today_window_matches_dashboard_trading_day(monkeypatch, tmp_path):
    report_dir = tmp_path / "reports"
    checkpoint_dir = tmp_path / "checkpoints"
    report_dir.mkdir()
    checkpoint_dir.mkdir()
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(
        server,
        "_live_order_sync_summary",
        lambda: {"ok": True, "fresh": True, "available": True, "age_seconds": 12},
    )
    _append_jsonl(
        report_dir / "candidate_no_submit_official_truth_signals.jsonl",
        [
            _candidate_record(
                "official_truth_14d14d_latest",
                passed=True,
                side="LONG",
                created_at="2026-07-03T15:55:00Z",
            ),
            _candidate_record(
                "official_truth_14d14d_latest",
                passed=True,
                side="SHORT",
                created_at="2026-07-03T16:05:00Z",
            ),
        ],
    )
    _write_json(report_dir / "candidate_no_submit_official_truth_latest.json", {"ok": True, "total_records": 2})
    _write_json(
        report_dir / "candidate_no_submit_official_truth_scored_summary.json",
        {
            "default_maker_price": 0.49,
            "size_shares": 5.0,
            "candidates": [
                {
                    "candidate_id": "official_truth_14d14d_latest",
                    "trades": [
                        {
                            "entry_ts": "2026-07-03T15:55:00Z",
                            "settle_ts": "2026-07-03T16:00:00Z",
                            "side": "LONG",
                            "won": True,
                            "pnl_usdc": 2.55,
                        },
                        {
                            "entry_ts": "2026-07-03T16:05:00Z",
                            "settle_ts": "2026-07-03T16:10:00Z",
                            "side": "SHORT",
                            "won": False,
                            "pnl_usdc": -2.45,
                        },
                    ],
                }
            ],
        },
    )
    _write_json(
        checkpoint_dir / "live_real_orders_current_next.json",
        [
            {
                "status": "SETTLED",
                "entry_ts": "2026-07-03T15:55:00Z",
                "settle_ts": "2026-07-03T16:00:00Z",
                "side": "LONG",
                "won": True,
                "fill_size": 10.0,
                "average_fill_price": 0.49,
                "reference_price_source": "chainlink_datastreams_pending",
            },
            {
                "status": "SETTLED",
                "entry_ts": "2026-07-03T16:05:00Z",
                "settle_ts": "2026-07-03T16:10:00Z",
                "side": "SHORT",
                "won": False,
                "fill_size": 10.0,
                "average_fill_price": 0.51,
                "reference_price_source": "chainlink_datastreams_pending",
            },
        ],
    )

    payload = server._strategy_comparison_payload(
        now=datetime(2026, 7, 3, 18, 0, tzinfo=timezone.utc),
        args={"window": "today", "bucket": "day", "metric": "pnl"},
    )

    assert payload["filters"]["window"] == "today"
    assert payload["window_coverage"]["requested_start_at"] == "2026-07-03T16:00:00Z"
    by_id = {row["candidate_id"]: row for row in payload["window_candidates"]}
    assert by_id["official_truth_14d14d_latest"]["evaluated"] == 1
    assert by_id["official_truth_14d14d_latest"]["pnl_usdc"] == -2.45
    live_by_id = {row["candidate_id"]: row for row in payload["live_matched"]["candidates"]}
    official = live_by_id["official_truth_14d14d_latest"]
    assert official["all_live"]["settled"] == 1
    assert official["all_live"]["live_actual_pnl"] == -5.1
    assert official["all_scored"]["scored_pnl"] == -2.45
    assert official["overlap"]["live_normalized_pnl"] == -2.45


def test_strategy_comparison_does_not_fallback_to_stale_scored_buckets(monkeypatch, tmp_path):
    report_dir = tmp_path / "reports"
    checkpoint_dir = tmp_path / "checkpoints"
    report_dir.mkdir()
    checkpoint_dir.mkdir()
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(
        server,
        "_live_order_sync_summary",
        lambda: {"ok": True, "fresh": True, "available": True, "age_seconds": 12},
    )
    _append_jsonl(
        report_dir / "candidate_no_submit_official_truth_signals.jsonl",
        [
            _candidate_record(
                "official_truth_14d14d_latest",
                passed=False,
                side=None,
                created_at="2026-07-05T16:05:00Z",
            ),
        ],
    )
    _write_json(report_dir / "candidate_no_submit_official_truth_latest.json", {"ok": True, "total_records": 1})
    _write_json(
        report_dir / "candidate_no_submit_official_truth_scored_summary.json",
        {
            "candidates": [
                {
                    "candidate_id": "official_truth_14d14d_latest",
                    "hourly": [
                        {
                            "bucket": "2026-07-05T16:00:00Z",
                            "wins": 39,
                            "losses": 36,
                            "pnl_usdc": 22.5,
                            "win_rate": 0.52,
                        }
                    ],
                    "trades": [
                        {
                            "entry_ts": "2026-07-05T15:35:00Z",
                            "settle_ts": "2026-07-05T15:40:00Z",
                            "side": "SHORT",
                            "won": True,
                            "pnl_usdc": 5.1,
                        }
                    ],
                }
            ],
        },
    )

    payload = server._strategy_comparison_payload(
        now=datetime(2026, 7, 5, 16, 30, tzinfo=timezone.utc),
        args={"window": "today", "bucket": "hour", "metric": "pnl", "candidates": "official_truth_14d14d_latest"},
    )

    series = [
        row for row in payload["timeseries"]
        if row["candidate_id"] == "official_truth_14d14d_latest"
    ]
    assert series == [
        {
            "bucket": "2026-07-05T16:00:00Z",
            "candidate_id": "official_truth_14d14d_latest",
            "evaluated": 1,
            "passed": 0,
            "pass_rate": 0.0,
            "candidate_only": 0,
            "same_side_overlap": 0,
            "live_signal_filtered": 1,
            "wins": None,
            "losses": None,
            "win_rate": None,
            "pnl_usdc": None,
            "scored": False,
        }
    ]
    by_id = {row["candidate_id"]: row for row in payload["window_candidates"]}
    assert by_id["official_truth_14d14d_latest"]["pnl_usdc"] is None
    assert by_id["official_truth_14d14d_latest"]["win_rate"] is None


def test_strategy_comparison_7d_uses_live_trading_day_window_and_buckets(monkeypatch, tmp_path):
    report_dir = tmp_path / "reports"
    checkpoint_dir = tmp_path / "checkpoints"
    report_dir.mkdir()
    checkpoint_dir.mkdir()
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(
        server,
        "_live_order_sync_summary",
        lambda: {"ok": True, "fresh": True, "available": True, "age_seconds": 12},
    )
    _append_jsonl(
        report_dir / "candidate_no_submit_official_truth_signals.jsonl",
        [
            _candidate_record(
                "official_truth_14d14d_latest",
                passed=True,
                side="LONG",
                created_at="2026-06-27T15:55:00Z",
            ),
            _candidate_record(
                "official_truth_14d14d_latest",
                passed=True,
                side="SHORT",
                created_at="2026-06-27T16:05:00Z",
            ),
            _candidate_record(
                "official_truth_14d14d_latest",
                passed=True,
                side="SHORT",
                created_at="2026-07-03T16:05:00Z",
            ),
        ],
    )
    _write_json(report_dir / "candidate_no_submit_official_truth_latest.json", {"ok": True, "total_records": 3})
    _write_json(
        report_dir / "candidate_no_submit_official_truth_scored_summary.json",
        {
            "candidates": [
                {
                    "candidate_id": "official_truth_14d14d_latest",
                    "trades": [
                        {
                            "entry_ts": "2026-06-27T15:55:00Z",
                            "side": "LONG",
                            "won": True,
                            "pnl_usdc": 2.55,
                        },
                        {
                            "entry_ts": "2026-06-27T16:05:00Z",
                            "side": "SHORT",
                            "won": False,
                            "pnl_usdc": -2.45,
                        },
                        {
                            "entry_ts": "2026-07-03T16:05:00Z",
                            "side": "SHORT",
                            "won": True,
                            "pnl_usdc": 2.55,
                        },
                    ],
                }
            ],
        },
    )

    payload = server._strategy_comparison_payload(
        now=datetime(2026, 7, 4, 1, 0, tzinfo=timezone.utc),
        args={"window": "7d", "bucket": "day", "metric": "pnl"},
    )

    assert payload["window_coverage"]["requested_start_at"] == "2026-06-27T16:00:00Z"
    assert payload["window_coverage"]["day_tz"] == "Asia/Shanghai"
    by_id = {row["candidate_id"]: row for row in payload["window_candidates"]}
    assert by_id["official_truth_14d14d_latest"]["evaluated"] == 2
    assert by_id["official_truth_14d14d_latest"]["pnl_usdc"] == 0.1
    series = [
        row for row in payload["timeseries"]
        if row["candidate_id"] == "official_truth_14d14d_latest"
    ]
    assert [row["bucket"] for row in series] == ["2026-06-28", "2026-07-04"]
    assert [row["pnl_usdc"] for row in series] == [-2.45, 2.55]


def test_strategy_comparison_uses_hourly_scored_summary_when_bucket_is_hour(monkeypatch, tmp_path):
    report_dir = tmp_path / "reports"
    checkpoint_dir = tmp_path / "checkpoints"
    report_dir.mkdir()
    checkpoint_dir.mkdir()
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(
        server,
        "_live_order_sync_summary",
        lambda: {"ok": True, "fresh": True, "available": True, "age_seconds": 12},
    )
    _append_jsonl(
        report_dir / "candidate_no_submit_official_truth_signals.jsonl",
        [
            _candidate_record(
                "official_truth_14d14d_latest",
                passed=True,
                side="LONG",
                created_at="2026-07-02T00:02:00Z",
            )
        ],
    )
    _write_json(
        report_dir / "candidate_no_submit_official_truth_scored_summary.json",
        {
            "candidates": [
                {
                    "candidate_id": "official_truth_14d14d_latest",
                    "total_pnl": 2.55,
                    "win_rate": 1.0,
                    "daily": [
                        {
                            "bucket": "2026-07-02",
                            "pnl_usdc": 2.55,
                            "wins": 1,
                            "losses": 0,
                            "win_rate": 1.0,
                        }
                    ],
                    "hourly": [
                        {
                            "bucket": "2026-07-02T00:00:00Z",
                            "pnl_usdc": 2.55,
                            "wins": 1,
                            "losses": 0,
                            "win_rate": 1.0,
                        }
                    ],
                }
            ]
        },
    )

    payload = server._strategy_comparison_payload(
        now=datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc),
        args={"metric": "pnl", "bucket": "hour", "window": "24h"},
    )

    series = [
        row for row in payload["timeseries"]
        if row["candidate_id"] == "official_truth_14d14d_latest"
    ]
    assert series[0]["bucket"] == "2026-07-02T00:00:00Z"
    assert series[0]["pnl_usdc"] == 2.55
    assert series[0]["wins"] == 1
    assert series[0]["losses"] == 0
    assert series[0]["win_rate"] == 1.0
    assert series[0]["scored"] is True


def test_strategy_comparison_exposes_window_scoped_candidate_summary(monkeypatch, tmp_path):
    report_dir = tmp_path / "reports"
    checkpoint_dir = tmp_path / "checkpoints"
    report_dir.mkdir()
    checkpoint_dir.mkdir()
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(
        server,
        "_live_order_sync_summary",
        lambda: {"ok": True, "fresh": True, "available": True, "age_seconds": 12},
    )
    _append_jsonl(
        report_dir / "candidate_no_submit_official_truth_signals.jsonl",
        [
            _candidate_record(
                "official_truth_14d14d_latest",
                passed=True,
                side="LONG",
                created_at="2026-06-29T00:02:00Z",
            ),
            _candidate_record(
                "official_truth_14d14d_latest",
                passed=True,
                side="SHORT",
                created_at="2026-07-02T00:02:00Z",
            ),
        ],
    )
    _write_json(
        report_dir / "candidate_no_submit_official_truth_scored_summary.json",
        {
            "candidates": [
                {
                    "candidate_id": "official_truth_14d14d_latest",
                    "total_pnl": -7.5,
                    "win_rate": 0.5,
                    "daily": [
                        {
                            "bucket": "2026-06-29",
                            "pnl_usdc": -10.0,
                            "wins": 0,
                            "losses": 1,
                            "win_rate": 0.0,
                        },
                        {
                            "bucket": "2026-07-02",
                            "pnl_usdc": 2.5,
                            "wins": 1,
                            "losses": 0,
                            "win_rate": 1.0,
                        },
                    ],
                }
            ]
        },
    )

    payload = server._strategy_comparison_payload(
        now=datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc),
        args={"window": "24h", "bucket": "day", "metric": "pnl"},
    )

    all_by_id = {row["candidate_id"]: row for row in payload["candidates"]}
    window_by_id = {row["candidate_id"]: row for row in payload["window_candidates"]}
    assert all_by_id["official_truth_14d14d_latest"]["evaluated"] == 2
    assert window_by_id["official_truth_14d14d_latest"]["evaluated"] == 1
    assert window_by_id["official_truth_14d14d_latest"]["passed"] == 1
    assert window_by_id["official_truth_14d14d_latest"]["pnl_usdc"] == 2.5
    assert window_by_id["official_truth_14d14d_latest"]["wins"] == 1
    assert window_by_id["official_truth_14d14d_latest"]["losses"] == 0
    assert window_by_id["official_truth_14d14d_latest"]["win_rate"] == 1.0
    assert payload["window_coverage"]["partial"] is False


def test_strategy_comparison_marks_partial_window_coverage(monkeypatch, tmp_path):
    report_dir = tmp_path / "reports"
    checkpoint_dir = tmp_path / "checkpoints"
    report_dir.mkdir()
    checkpoint_dir.mkdir()
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(
        server,
        "_live_order_sync_summary",
        lambda: {"ok": True, "fresh": True, "available": True, "age_seconds": 12},
    )
    _append_jsonl(
        report_dir / "candidate_no_submit_official_truth_signals.jsonl",
        [
            _candidate_record(
                "official_truth_14d14d_latest",
                passed=True,
                side="LONG",
                created_at="2026-07-02T00:02:00Z",
            ),
        ],
    )

    payload = server._strategy_comparison_payload(
        now=datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc),
        args={"window": "7d", "bucket": "day", "metric": "signals"},
    )

    assert payload["window_coverage"]["partial"] is True
    assert payload["window_coverage"]["covered_days"] == 0.5
    assert payload["window_coverage"]["score_status"] == "partial"
    assert payload["window_coverage"]["score_label"] == "Partial 0.50d"
    assert any("selected 7d window only has 0.50d" in item for item in payload["warnings"])


def test_strategy_live_summary_uses_running_formal_params_json(monkeypatch, tmp_path):
    report_dir = tmp_path / "reports"
    checkpoint_dir = tmp_path / "checkpoints"
    report_dir.mkdir()
    checkpoint_dir.mkdir()
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(
        server,
        "_live_order_sync_summary",
        lambda: {"ok": True, "fresh": True, "available": True, "age_seconds": 12},
    )
    monkeypatch.setattr(
        server,
        "_process_summary",
        lambda patterns: {
            "running": True,
            "matches": [
                {
                    "command_full": (
                        "python scripts/run_prediction_bound_live_order.py "
                        "--params-json data/config/aligned_prod_current_next_official_truth_14d14d_live_params.json "
                        "--environment-enabled NO"
                    )
                }
            ],
        },
    )

    summary = server._strategy_live_prediction_summary(datetime(2026, 7, 4, 8, 0, tzinfo=timezone.utc))

    assert summary["strategy_id"] == "official_truth_14d14d_latest"
    assert summary["label"] == "Official 14d/14d"
    assert summary["params_path"] == "data/config/aligned_prod_current_next_official_truth_14d14d_live_params.json"


def test_strategy_compare_frontend_uses_window_scoped_rows():
    source = Path("web/src/pages/Compare.tsx").read_text(encoding="utf-8")

    assert "const windowCandidates = data?.window_candidates ?? candidates;" in source
    assert "const tableCandidates = windowCandidates" in source
    assert "return [...tableCandidates].sort" in source
    assert "{rankedCandidates.map((row) => (" in source
    assert "rows={rankedCandidates}" in source
    assert "row.pnl_usdc" in source


def test_strategy_compare_frontend_exposes_24h_window():
    source = Path("web/src/pages/Compare.tsx").read_text(encoding="utf-8")

    assert 'window: "today" | "24h" | "7d" | "14d" | "all";' in source
    assert 'if (value === "24h")' in source
    assert '(["today", "24h", "7d", "14d", "all"] as const)' in source
