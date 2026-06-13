from __future__ import annotations

import json

from sync_service import main as sync


def test_configured_dashboard_db_source_defaults_to_paper(monkeypatch):
    monkeypatch.delenv("DASHBOARD_DB_SOURCE", raising=False)
    assert sync.configured_dashboard_db_source() == "paper"


def test_configured_dashboard_db_source_accepts_live_real(monkeypatch):
    monkeypatch.setenv("DASHBOARD_DB_SOURCE", "live_real")
    assert sync.configured_dashboard_db_source() == "live_real"


def test_configured_dashboard_db_source_rejects_legacy_live(monkeypatch):
    monkeypatch.setenv("DASHBOARD_DB_SOURCE", "live")
    assert sync.configured_dashboard_db_source() == "paper"


def test_import_snapshot_uses_explicit_db_source(tmp_path):
    db = tmp_path / "dashboard.db"
    conn = sync.init_db(str(db))
    checkpoint = tmp_path / "paper_aligned_prod_shift1.json"
    checkpoint.write_text(
        json.dumps({"timestamp": "2026-06-14T00:00:00Z", "balance": 500, "trades": []}),
        encoding="utf-8",
    )

    sync.import_live_snapshot(conn, checkpoint, db_source="paper")

    row = conn.execute("SELECT source, balance FROM snapshots").fetchone()
    assert row[0] == "paper"
    assert row[1] == 500


def test_import_snapshot_without_db_source_maps_paper_mode_to_paper(tmp_path):
    db = tmp_path / "dashboard.db"
    conn = sync.init_db(str(db))
    checkpoint = tmp_path / "paper_live.json"
    checkpoint.write_text(
        json.dumps({"mode": "paper_live", "timestamp": "2026-06-14T00:00:00Z", "trades": []}),
        encoding="utf-8",
    )

    sync.import_live_snapshot(conn, checkpoint)

    row = conn.execute("SELECT source FROM snapshots").fetchone()
    assert row[0] == "paper"
