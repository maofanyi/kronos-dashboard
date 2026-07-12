from __future__ import annotations

from api import server


def test_compact_strategy_scored_summary_drops_bulk_rows():
    scored = {
        "candidate_id": "official_truth_14d14d_latest",
        "wins": 12,
        "losses": 8,
        "win_rate": 0.6,
        "pnl_usdc": 9.5,
        "hourly": [{"bucket": "2026-07-12T00:00:00Z"}],
        "daily": [{"date": "2026-07-12"}],
        "trades": [{"signal_id": f"signal-{index}"} for index in range(100)],
    }

    compact = server._compact_strategy_scored_summary(scored)

    assert compact == {
        "candidate_id": "official_truth_14d14d_latest",
        "wins": 12,
        "losses": 8,
        "win_rate": 0.6,
        "pnl_usdc": 9.5,
    }


def test_strategy_source_fingerprint_changes_with_runtime_file(tmp_path):
    source = tmp_path / "signals.jsonl"
    source.write_text("{}\n", encoding="utf-8")

    first = server._strategy_source_fingerprint([source])
    source.write_text("{}\n{}\n", encoding="utf-8")
    second = server._strategy_source_fingerprint([source])

    assert first != second
