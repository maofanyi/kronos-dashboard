import json
from pathlib import Path

import pytest

from api import server


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def _configure_strategy_files(monkeypatch, tmp_path) -> None:
    checkpoint_dir = tmp_path / "checkpoints"
    report_dir = tmp_path / "reports"
    checkpoint_dir.mkdir()
    report_dir.mkdir()
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    _write_json(
        checkpoint_dir / "live_real_orders_current_next.json",
        [
            {
                "order_id": "filled",
                "entry_ts": "2026-07-10T10:00:00Z",
                "settle_ts": "2026-07-10T10:05:00Z",
                "side": "LONG",
                "status": "SETTLED",
                "filled_size": 10.0,
                "average_fill_price": 0.49,
                "won": True,
                "net_pnl": 5.1,
                "reference_price_source": "chainlink_datastreams_pending",
            },
            {
                "order_id": "no-fill",
                "entry_ts": "2026-07-10T10:05:00Z",
                "settle_ts": "2026-07-10T10:10:00Z",
                "side": "SHORT",
                "status": "NO_FILL",
                "filled_size": 0.0,
                "price": 0.49,
                "reference_price_source": "chainlink_datastreams_pending",
            },
        ],
    )
    _write_jsonl(
        report_dir / "prediction_bound_live_formal_predictions.jsonl",
        [
            {
                "entry_ts": "2026-07-10T10:05:00Z",
                "settle_ts": "2026-07-10T10:10:00Z",
                "side": "SHORT",
                "action": "BUY_DOWN",
                "would_place_order": True,
                "submitted": True,
                "created_at": "2026-07-10T10:00:15Z",
            },
            {
                "entry_ts": "2026-07-10T10:10:00Z",
                "settle_ts": "2026-07-10T10:15:00Z",
                "side": "LONG",
                "action": "BUY_UP",
                "would_place_order": True,
                "submitted": False,
                "guarded_mode": "blocked",
                "guarded_reason": "pre-submit gates blocked prediction-bound order",
                "created_at": "2026-07-10T10:05:15Z",
            },
        ],
    )
    _write_json(
        report_dir / "candidate_no_submit_official_truth_scored_summary.json",
        {
            "candidates": [
                {
                    "candidate_id": "official_truth_14d14d_latest",
                    "trades": [
                        {
                            "record_key": "matched",
                            "entry_ts": "2026-07-10T10:00:00Z",
                            "settle_ts": "2026-07-10T10:05:00Z",
                            "side": "LONG",
                            "size_shares": 10.0,
                            "maker_price": 0.49,
                            "won": True,
                            "pnl_usdc": 5.1,
                        },
                        {
                            "record_key": "no-fill",
                            "entry_ts": "2026-07-10T10:05:00Z",
                            "settle_ts": "2026-07-10T10:10:00Z",
                            "side": "SHORT",
                            "size_shares": 10.0,
                            "maker_price": 0.49,
                            "won": True,
                            "pnl_usdc": 5.1,
                        },
                        {
                            "record_key": "blocked",
                            "entry_ts": "2026-07-10T10:10:00Z",
                            "settle_ts": "2026-07-10T10:15:00Z",
                            "side": "LONG",
                            "size_shares": 10.0,
                            "maker_price": 0.49,
                            "won": False,
                            "pnl_usdc": -4.9,
                        },
                    ],
                }
            ]
        },
    )


def test_strategy_difference_route_returns_reconciling_summaries(monkeypatch, tmp_path):
    _configure_strategy_files(monkeypatch, tmp_path)

    with server.app.test_client() as client:
        response = client.get(
            "/api/strategy-comparison/differences"
            "?candidate=official_truth_14d14d_latest"
            "&window=all&limit=1&offset=0"
        )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["candidate_id"] == "official_truth_14d14d_latest"
    assert payload["overall_summary"]["pnl_delta"] == round(
        payload["overall_summary"]["live_pnl"]
        - payload["overall_summary"]["simulated_pnl"],
        6,
    )
    assert round(
        sum(
            item["pnl_delta"]
            for item in payload["overall_summary"]["by_type"].values()
        ),
        6,
    ) == payload["overall_summary"]["pnl_delta"]
    assert payload["pagination"] == {
        "limit": 1,
        "offset": 0,
        "returned": 1,
        "total": 2,
    }
    assert payload["filtered_summary"]["market_count"] == 2
    assert payload["rows"][0]["primary_type"] != "matched"


@pytest.mark.parametrize(
    ("query", "error"),
    [
        ("candidate=unknown", "invalid_candidate"),
        (
            "candidate=official_truth_14d14d_latest&types=not-a-type",
            "invalid_types",
        ),
        (
            "candidate=official_truth_14d14d_latest&limit=not-a-number",
            "invalid_pagination",
        ),
        (
            "candidate=official_truth_14d14d_latest&window=bogus",
            "invalid_window",
        ),
        (
            "candidate=official_truth_14d14d_latest&include_matched=maybe",
            "invalid_include_matched",
        ),
        (
            "candidate=official_truth_14d14d_latest&limit=0",
            "invalid_pagination",
        ),
        (
            "candidate=official_truth_14d14d_latest&limit=501",
            "invalid_pagination",
        ),
        (
            "candidate=official_truth_14d14d_latest&offset=-1",
            "invalid_pagination",
        ),
    ],
)
def test_strategy_difference_route_rejects_invalid_queries(query, error):
    with server.app.test_client() as client:
        response = client.get(f"/api/strategy-comparison/differences?{query}")
    assert response.status_code == 400
    assert response.get_json()["error"] == error


@pytest.mark.parametrize("value", ["", "0", "false", "no"])
def test_strategy_difference_route_accepts_false_include_matched_values(
    monkeypatch,
    tmp_path,
    value,
):
    _configure_strategy_files(monkeypatch, tmp_path)
    with server.app.test_client() as client:
        payload = client.get(
            "/api/strategy-comparison/differences"
            "?candidate=official_truth_14d14d_latest"
            f"&window=all&include_matched={value}"
        ).get_json()

    assert payload["filters"]["include_matched"] is False
    assert payload["pagination"]["total"] == 2


def test_strategy_difference_route_keeps_overall_and_filtered_summaries_separate(
    monkeypatch,
    tmp_path,
):
    _configure_strategy_files(monkeypatch, tmp_path)
    with server.app.test_client() as client:
        payload = client.get(
            "/api/strategy-comparison/differences"
            "?candidate=official_truth_14d14d_latest"
            "&window=all&types=simulated_no_fill"
        ).get_json()

    assert payload["overall_summary"]["market_count"] == 3
    assert payload["filtered_summary"]["market_count"] == 1
    assert payload["pagination"]["total"] == 1
    assert {row["primary_type"] for row in payload["rows"]} == {
        "simulated_no_fill"
    }


def test_strategy_difference_route_keeps_both_summaries_globally_unreconcilable(
    monkeypatch,
    tmp_path,
):
    _configure_strategy_files(monkeypatch, tmp_path)
    (tmp_path / "checkpoints" / "live_real_orders_current_next.json").unlink()

    with server.app.test_client() as client:
        payload = client.get(
            "/api/strategy-comparison/differences"
            "?candidate=official_truth_14d14d_latest"
            "&window=all&types=simulated_pre_submit_blocked"
        ).get_json()

    assert payload["data_quality"]["reconcilable"] is False
    for summary_name in ("overall_summary", "filtered_summary"):
        assert payload[summary_name]["live_pnl"] is None
        assert payload[summary_name]["simulated_pnl"] is None
        assert payload[summary_name]["pnl_delta"] is None


def test_strategy_difference_route_reports_missing_selected_candidate(
    monkeypatch,
    tmp_path,
):
    _configure_strategy_files(monkeypatch, tmp_path)
    _write_json(
        tmp_path / "reports" / "candidate_no_submit_official_truth_scored_summary.json",
        {"candidates": []},
    )

    with server.app.test_client() as client:
        payload = client.get(
            "/api/strategy-comparison/differences"
            "?candidate=official_truth_14d14d_latest"
        ).get_json()

    assert payload["available"] is False
    assert payload["rows"] == []
    assert "candidate scored summary missing" in payload["warnings"]


def test_strategy_difference_route_reports_every_missing_source(
    monkeypatch,
    tmp_path,
):
    _configure_strategy_files(monkeypatch, tmp_path)
    (tmp_path / "checkpoints" / "live_real_orders_current_next.json").unlink()
    (tmp_path / "reports" / "prediction_bound_live_formal_predictions.jsonl").unlink()
    (
        tmp_path
        / "reports"
        / "candidate_no_submit_official_truth_scored_summary.json"
    ).unlink()

    with server.app.test_client() as client:
        payload = client.get(
            "/api/strategy-comparison/differences"
            "?candidate=official_truth_14d14d_latest"
        ).get_json()

    assert payload["available"] is False
    assert payload["rows"] == []
    assert set(payload["warnings"]) == {
        "live ledger missing; live PnL is unknown",
        "formal prediction history missing; simulated-only causes may be unknown",
        "candidate scored summary missing",
    }


def test_strategy_difference_route_missing_formal_history_keeps_money_reconcilable(
    monkeypatch,
    tmp_path,
):
    _configure_strategy_files(monkeypatch, tmp_path)
    (tmp_path / "reports" / "prediction_bound_live_formal_predictions.jsonl").unlink()

    with server.app.test_client() as client:
        payload = client.get(
            "/api/strategy-comparison/differences"
            "?candidate=official_truth_14d14d_latest&window=all"
        ).get_json()

    assert payload["data_quality"]["reconcilable"] is True
    assert payload["overall_summary"]["pnl_delta"] == -0.2
    assert (
        "formal prediction history missing; simulated-only causes may be unknown"
        in payload["warnings"]
    )
