from __future__ import annotations

import json
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


def _candidate_record(candidate_id: str, *, passed: bool, side: str | None, created_at: str, overlap: bool = False) -> dict:
    return {
        "type": "candidate_no_submit_signal",
        "record_key": f"{created_at}|{candidate_id}",
        "created_at": created_at,
        "candidate_id": candidate_id,
        "candidate_config_path": f"data/config/research_candidates/{candidate_id}.json",
        "entry_ts": "2026-07-02T00:05:00Z",
        "settle_ts": "2026-07-02T00:10:00Z",
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

    _append_jsonl(
        report_dir / "prediction_bound_live_formal_predictions.jsonl",
        [
            {"created_at": "2026-07-02T00:00:00Z", "would_place_order": True, "submitted": False},
            {"created_at": "2026-07-02T00:05:00Z", "would_place_order": False, "submitted": False},
        ],
    )
    _append_jsonl(
        report_dir / "candidate_no_submit_official_truth_signals.jsonl",
        [
            _candidate_record("round2_drawdown_density", passed=True, side="LONG", created_at="2026-07-02T00:00:00Z", overlap=True),
            _candidate_record("official_truth_14d14d_latest", passed=True, side="SHORT", created_at="2026-07-02T00:00:00Z"),
            _candidate_record("official_truth_14d14d_latest", passed=False, side=None, created_at="2026-07-02T00:05:00Z"),
            _candidate_record("official_truth_7d7d_latest", passed=True, side="LONG", created_at="2026-07-02T00:05:00Z", overlap=True),
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

    with server.app.test_client() as client:
        payload = client.get("/api/strategy-comparison?metric=pnl&bucket=hour&window=24h").get_json()

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
