from datetime import datetime, timezone

import pytest

from api.strategy_differences import reconcile_strategy_orders, summarize_difference_rows


NOW = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
START = datetime(2026, 7, 9, 16, 0, tzinfo=timezone.utc)


def _live(**overrides):
    row = {
        "order_id": "0x-live",
        "entry_ts": "2026-07-10T10:10:00Z",
        "settle_ts": "2026-07-10T10:15:00Z",
        "side": "LONG",
        "status": "SETTLED",
        "filled_size": 10.0,
        "average_fill_price": 0.50,
        "won": False,
        "net_pnl": -5.0,
        "reference_price_source": "chainlink_datastreams_pending",
    }
    row.update(overrides)
    return row


def _scored(**overrides):
    row = {
        "record_key": "sim-1",
        "entry_ts": "2026-07-10T10:10:00Z",
        "settle_ts": "2026-07-10T10:15:00Z",
        "side": "LONG",
        "size_shares": 10.0,
        "maker_price": 0.49,
        "won": False,
        "pnl_usdc": -4.9,
    }
    row.update(overrides)
    return row


def test_market_identity_does_not_include_side():
    result = reconcile_strategy_orders(
        live_records=[_live(side="LONG")],
        formal_predictions=[],
        scored_records=[_scored(side="SHORT")],
        start_at=START,
        end_at=NOW,
        ledger_available=True,
        scored_available=True,
    )

    assert len(result["rows"]) == 1
    assert result["rows"][0]["primary_type"] == "side_mismatch"
    assert "side" in result["rows"][0]["difference_flags"]


def test_order_chain_deduplicates_order_ids_and_uses_weighted_fill_price():
    result = reconcile_strategy_orders(
        live_records=[
            _live(order_id="cancelled", status="CANCELLED", filled_size=0, average_fill_price=None, net_pnl=None),
            _live(order_id="filled-a", filled_size=4, average_fill_price=0.49, net_pnl=-1.96),
            _live(order_id="filled-b", filled_size=6, average_fill_price=0.51, net_pnl=-3.06),
            _live(order_id="filled-b", filled_size=6, average_fill_price=0.51, net_pnl=-3.06),
        ],
        formal_predictions=[],
        scored_records=[_scored()],
        start_at=START,
        end_at=NOW,
        ledger_available=True,
        scored_available=True,
    )

    row = result["rows"][0]
    assert row["live"]["attempts"] == 3
    assert row["live"]["filled_size"] == 10.0
    assert row["live"]["average_fill_price"] == 0.502
    assert row["live_pnl"] == -5.02
    assert row["simulated_pnl"] == -4.9
    assert row["pnl_delta"] == -0.12
    assert row["primary_type"] == "execution_mismatch"


def test_summary_reconciles_market_and_reason_totals():
    result = reconcile_strategy_orders(
        live_records=[_live()],
        formal_predictions=[],
        scored_records=[_scored()],
        start_at=START,
        end_at=NOW,
        ledger_available=True,
        scored_available=True,
    )
    summary = summarize_difference_rows(
        result["rows"],
        reconcilable=result["data_quality"]["reconcilable"],
    )

    assert summary["live_pnl"] == -5.0
    assert summary["simulated_pnl"] == -4.9
    assert summary["pnl_delta"] == -0.1
    assert round(
        sum(item["pnl_delta"] for item in summary["by_type"].values()),
        6,
    ) == summary["pnl_delta"]


def test_equal_pair_is_matched():
    result = reconcile_strategy_orders(
        live_records=[_live(average_fill_price=0.49, net_pnl=-4.9)],
        formal_predictions=[],
        scored_records=[_scored()],
        start_at=START,
        end_at=NOW,
        ledger_available=True,
        scored_available=True,
    )
    assert result["rows"][0]["primary_type"] == "matched"


def test_equal_side_with_different_result_is_outcome_mismatch():
    result = reconcile_strategy_orders(
        live_records=[_live(won=True, net_pnl=5.0)],
        formal_predictions=[],
        scored_records=[_scored(won=False, pnl_usdc=-4.9)],
        start_at=START,
        end_at=NOW,
        ledger_available=True,
        scored_available=True,
    )
    assert result["rows"][0]["primary_type"] == "outcome_mismatch"
    assert "outcome" in result["rows"][0]["difference_flags"]


def test_final_live_fill_without_scored_trade_is_live_only():
    result = reconcile_strategy_orders(
        live_records=[_live()],
        formal_predictions=[],
        scored_records=[],
        start_at=START,
        end_at=NOW,
        ledger_available=True,
        scored_available=True,
    )
    assert result["rows"][0]["primary_type"] == "live_only"
    assert result["rows"][0]["simulated_pnl"] == 0.0


def test_non_datastreams_final_fill_is_excluded_not_called_no_fill():
    result = reconcile_strategy_orders(
        live_records=[_live(reference_price_source="binance_kline")],
        formal_predictions=[],
        scored_records=[_scored()],
        start_at=START,
        end_at=NOW,
        ledger_available=True,
        scored_available=True,
    )
    assert result["data_quality"]["excluded_live_reference_count"] == 1
    row = result["rows"][0]
    summary = summarize_difference_rows(
        result["rows"],
        reconcilable=result["data_quality"]["reconcilable"],
    )
    assert row["primary_type"] == "simulated_only_unknown"
    assert "missing_source" in row["difference_flags"]
    assert row["live_pnl"] is None
    assert row["simulated_pnl"] == -4.9
    assert row["pnl_delta"] is None
    assert row["reconcilable"] is False
    assert summary["live_pnl"] is None
    assert summary["simulated_pnl"] is None
    assert summary["pnl_delta"] is None


def test_excluded_only_final_fill_marks_empty_summary_unreconcilable():
    result = reconcile_strategy_orders(
        live_records=[_live(reference_price_source="binance_kline")],
        formal_predictions=[],
        scored_records=[],
        start_at=START,
        end_at=NOW,
        ledger_available=True,
        scored_available=True,
    )

    summary = summarize_difference_rows(
        result["rows"],
        reconcilable=result["data_quality"]["reconcilable"],
    )
    assert result["rows"] == []
    assert result["data_quality"]["excluded_live_reference_count"] == 1
    assert result["data_quality"]["reconcilable"] is False
    assert summary["live_pnl"] is None
    assert summary["simulated_pnl"] is None
    assert summary["pnl_delta"] is None


def test_mixed_trusted_and_excluded_final_fills_are_unreconcilable():
    result = reconcile_strategy_orders(
        live_records=[
            _live(order_id="trusted", average_fill_price=0.49, net_pnl=-4.9),
            _live(
                order_id="excluded",
                filled_size=2.0,
                average_fill_price=0.50,
                net_pnl=-1.0,
                reference_price_source="binance_kline",
            ),
        ],
        formal_predictions=[],
        scored_records=[_scored()],
        start_at=START,
        end_at=NOW,
        ledger_available=True,
        scored_available=True,
    )

    row = result["rows"][0]
    summary = summarize_difference_rows(
        result["rows"],
        reconcilable=result["data_quality"]["reconcilable"],
    )
    assert row["live"]["present"] is True
    assert row["live"]["excluded_final_fill_count"] == 1
    assert row["live_pnl"] is None
    assert row["pnl_delta"] is None
    assert row["reconcilable"] is False
    assert result["data_quality"]["reconcilable"] is False
    assert summary["live_pnl"] is None
    assert summary["simulated_pnl"] is None
    assert summary["pnl_delta"] is None


def test_trusted_final_fill_without_explicit_pnl_is_unreconcilable():
    result = reconcile_strategy_orders(
        live_records=[
            _live(
                pnl_usdc=None,
                realized_pnl=None,
                net_pnl=None,
                pnl=None,
            )
        ],
        formal_predictions=[],
        scored_records=[_scored()],
        start_at=START,
        end_at=NOW,
        ledger_available=True,
        scored_available=True,
    )

    row = result["rows"][0]
    summary = summarize_difference_rows(
        result["rows"],
        reconcilable=result["data_quality"]["reconcilable"],
    )
    assert row["live"]["present"] is True
    assert row["live"]["missing_pnl_count"] == 1
    assert row["live_pnl"] is None
    assert row["pnl_delta"] is None
    assert row["reconcilable"] is False
    assert summary["live_pnl"] is None
    assert summary["simulated_pnl"] is None
    assert summary["pnl_delta"] is None


def test_scored_trade_without_explicit_pnl_is_unreconcilable():
    result = reconcile_strategy_orders(
        live_records=[],
        formal_predictions=[],
        scored_records=[
            _scored(
                pnl_usdc=None,
                realized_pnl=None,
                net_pnl=None,
                pnl=None,
            )
        ],
        start_at=START,
        end_at=NOW,
        ledger_available=True,
        scored_available=True,
    )

    row = result["rows"][0]
    summary = summarize_difference_rows(
        result["rows"],
        reconcilable=result["data_quality"]["reconcilable"],
    )
    assert row["simulated"]["present"] is True
    assert row["simulated"]["side"] == "LONG"
    assert row["simulated"]["missing_pnl_count"] == 1
    assert row["simulated_pnl"] is None
    assert row["pnl_delta"] is None
    assert row["reconcilable"] is False
    assert summary["live_pnl"] is None
    assert summary["simulated_pnl"] is None
    assert summary["pnl_delta"] is None


def test_conflicting_scored_duplicates_are_excluded_with_data_quality_marker():
    result = reconcile_strategy_orders(
        live_records=[],
        formal_predictions=[],
        scored_records=[_scored(side="LONG"), _scored(side="SHORT")],
        start_at=START,
        end_at=NOW,
        ledger_available=True,
        scored_available=True,
    )
    assert result["rows"] == []
    assert result["data_quality"]["conflicting_scored_markets"] == [
        ("2026-07-10T10:10:00Z", "2026-07-10T10:15:00Z")
    ]
    assert result["data_quality"]["reconcilable"] is False
    summary = summarize_difference_rows(
        result["rows"],
        reconcilable=result["data_quality"]["reconcilable"],
    )
    assert summary["live_pnl"] is None
    assert summary["simulated_pnl"] is None
    assert summary["pnl_delta"] is None


def test_conflicting_scored_market_omits_matching_live_market():
    result = reconcile_strategy_orders(
        live_records=[_live(average_fill_price=0.49, net_pnl=-4.9)],
        formal_predictions=[],
        scored_records=[_scored(side="LONG"), _scored(side="SHORT")],
        start_at=START,
        end_at=NOW,
        ledger_available=True,
        scored_available=True,
    )

    assert result["rows"] == []
    assert len(result["data_quality"]["conflicting_scored_markets"]) == 1
    assert result["data_quality"]["reconcilable"] is False
    summary = summarize_difference_rows(
        result["rows"],
        reconcilable=result["data_quality"]["reconcilable"],
    )
    assert summary["live_pnl"] is None
    assert summary["simulated_pnl"] is None
    assert summary["pnl_delta"] is None


def test_records_before_start_are_not_returned():
    result = reconcile_strategy_orders(
        live_records=[],
        formal_predictions=[],
        scored_records=[
            _scored(
                entry_ts="2026-07-09T15:50:00Z",
                settle_ts="2026-07-09T15:55:00Z",
            )
        ],
        start_at=START,
        end_at=NOW,
        ledger_available=True,
        scored_available=True,
    )
    assert result["rows"] == []


def test_missing_live_ledger_marks_live_pnl_and_summary_unknown():
    result = reconcile_strategy_orders(
        live_records=[_live()],
        formal_predictions=[],
        scored_records=[_scored()],
        start_at=START,
        end_at=NOW,
        ledger_available=False,
        scored_available=True,
    )

    row = result["rows"][0]
    summary = summarize_difference_rows(
        result["rows"],
        reconcilable=result["data_quality"]["reconcilable"],
    )
    assert row["live_pnl"] is None
    assert row["simulated_pnl"] == -4.9
    assert row["pnl_delta"] is None
    assert summary["live_pnl"] is None
    assert summary["simulated_pnl"] is None
    assert summary["pnl_delta"] is None


def test_missing_scored_summary_marks_simulated_pnl_and_summary_unknown():
    result = reconcile_strategy_orders(
        live_records=[_live()],
        formal_predictions=[],
        scored_records=[_scored()],
        start_at=START,
        end_at=NOW,
        ledger_available=True,
        scored_available=False,
    )

    row = result["rows"][0]
    summary = summarize_difference_rows(
        result["rows"],
        reconcilable=result["data_quality"]["reconcilable"],
    )
    assert row["live_pnl"] == -5.0
    assert row["simulated_pnl"] is None
    assert row["pnl_delta"] is None
    assert summary["live_pnl"] is None
    assert summary["simulated_pnl"] is None
    assert summary["pnl_delta"] is None


def test_summary_requires_global_reconcilability_flag():
    with pytest.raises(TypeError):
        summarize_difference_rows([])


def test_missing_ledger_with_no_rows_keeps_summary_money_unknown():
    result = reconcile_strategy_orders(
        live_records=[],
        formal_predictions=[],
        scored_records=[],
        start_at=START,
        end_at=NOW,
        ledger_available=False,
        scored_available=True,
    )

    summary = summarize_difference_rows(
        result["rows"],
        reconcilable=result["data_quality"]["reconcilable"],
    )
    assert result["rows"] == []
    assert summary["live_pnl"] is None
    assert summary["simulated_pnl"] is None
    assert summary["pnl_delta"] is None


def test_no_fill_attempt_is_classified_as_simulated_no_fill():
    result = reconcile_strategy_orders(
        live_records=[
            _live(
                status="NO_FILL",
                filled_size=0,
                average_fill_price=None,
                net_pnl=None,
            )
        ],
        formal_predictions=[],
        scored_records=[_scored()],
        start_at=START,
        end_at=NOW,
        ledger_available=True,
        scored_available=True,
    )

    row = result["rows"][0]
    assert row["primary_type"] == "simulated_no_fill"
    assert row["difference_flags"] == ["no_fill"]


@pytest.mark.parametrize("status", ["NO_FILL", "CANCELLED"])
def test_unscored_no_fill_attempt_is_omitted(status):
    result = reconcile_strategy_orders(
        live_records=[
            _live(
                status=status,
                filled_size=0,
                average_fill_price=None,
                net_pnl=None,
            )
        ],
        formal_predictions=[],
        scored_records=[],
        start_at=START,
        end_at=NOW,
        ledger_available=True,
        scored_available=True,
    )

    assert result["rows"] == []


def test_latest_order_update_is_deduplicated_across_markets():
    latest_entry = "2026-07-10T10:20:00Z"
    latest_settle = "2026-07-10T10:25:00Z"
    result = reconcile_strategy_orders(
        live_records=[
            _live(
                order_id="updated-order",
                updated_at="2026-07-10T10:11:00Z",
            ),
            _live(
                order_id="updated-order",
                entry_ts=latest_entry,
                settle_ts=latest_settle,
                updated_at="2026-07-10T10:21:00Z",
                average_fill_price=0.49,
                net_pnl=-4.9,
            ),
        ],
        formal_predictions=[],
        scored_records=[_scored(entry_ts=latest_entry, settle_ts=latest_settle)],
        start_at=START,
        end_at=NOW,
        ledger_available=True,
        scored_available=True,
    )

    assert len(result["rows"]) == 1
    assert result["rows"][0]["entry_ts"] == latest_entry
    assert result["rows"][0]["primary_type"] == "matched"


def _formal(**overrides):
    row = {
        "entry_ts": "2026-07-10T10:10:00Z",
        "settle_ts": "2026-07-10T10:15:00Z",
        "side": "LONG",
        "action": "BUY_UP",
        "would_place_order": True,
        "submitted": False,
        "guarded_mode": "blocked",
        "guarded_reason": "pre-submit gates blocked prediction-bound order",
        "created_at": "2026-07-10T10:05:15Z",
    }
    row.update(overrides)
    return row


def _single_type(*, live_records=(), formal_predictions=(), scored_records=None):
    result = reconcile_strategy_orders(
        live_records=list(live_records),
        formal_predictions=list(formal_predictions),
        scored_records=list(scored_records or [_scored()]),
        start_at=START,
        end_at=NOW,
        ledger_available=True,
        scored_available=True,
    )
    return result["rows"][0]


def test_simulated_order_explains_pre_submit_gate():
    row = _single_type(formal_predictions=[_formal()])
    assert row["primary_type"] == "simulated_pre_submit_blocked"
    assert row["difference_flags"] == ["pre_submit_gate"]


def test_simulated_order_explains_no_fill_before_gate_reason():
    row = _single_type(
        live_records=[
            _live(status="NO_FILL", filled_size=0, average_fill_price=None, net_pnl=None),
        ],
        formal_predictions=[_formal(submitted=True, guarded_mode=None)],
    )
    assert row["primary_type"] == "simulated_no_fill"
    assert row["difference_flags"] == ["no_fill"]


def test_candidate_signal_without_same_formal_action_is_strategy_only():
    row = _single_type(
        formal_predictions=[
            _formal(side=None, action="HOLD", would_place_order=False, guarded_mode=None),
        ]
    )
    assert row["primary_type"] == "simulated_strategy_only"
    assert row["difference_flags"] == ["strategy_signal"]


def test_missing_formal_record_is_unknown_but_pnl_still_reconciles():
    row = _single_type()
    assert row["primary_type"] == "simulated_only_unknown"
    assert row["live_pnl"] == 0.0
    assert row["simulated_pnl"] == -4.9
    assert row["pnl_delta"] == 4.9


def test_missing_ledger_marks_amounts_unreconcilable():
    result = reconcile_strategy_orders(
        live_records=[],
        formal_predictions=[],
        scored_records=[_scored()],
        start_at=START,
        end_at=NOW,
        ledger_available=False,
        scored_available=True,
    )
    summary = summarize_difference_rows(
        result["rows"],
        reconcilable=result["data_quality"]["reconcilable"],
    )
    assert summary["live_pnl"] is None
    assert summary["simulated_pnl"] is None
    assert summary["pnl_delta"] is None


def test_formal_selection_prefers_submitted_attempt_over_later_hold():
    row = _single_type(
        formal_predictions=[
            _formal(
                side=None,
                action="HOLD",
                would_place_order=False,
                guarded_mode=None,
                created_at="2026-07-10T10:06:00Z",
            ),
            _formal(submitted=True, guarded_mode=None),
        ]
    )
    assert row["formal"]["submitted"] is True
    assert row["formal"]["created_at"] == "2026-07-10T10:05:15Z"


def test_formal_selection_prefers_would_place_order_over_later_hold():
    row = _single_type(
        formal_predictions=[
            _formal(
                side=None,
                action="HOLD",
                would_place_order=False,
                guarded_mode=None,
                created_at="2026-07-10T10:06:00Z",
            ),
            _formal(),
        ]
    )
    assert row["formal"]["would_place_order"] is True
    assert row["formal"]["created_at"] == "2026-07-10T10:05:15Z"


def test_formal_selection_uses_latest_created_at_after_priority_ties():
    row = _single_type(
        formal_predictions=[
            _formal(created_at="2026-07-10T10:05:15Z"),
            _formal(created_at="2026-07-10T10:06:00Z"),
        ]
    )
    assert row["formal"]["created_at"] == "2026-07-10T10:06:00Z"
