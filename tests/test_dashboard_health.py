from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from api import server


def test_dashboard_health_reports_runtime_freshness_and_cache(monkeypatch, tmp_path):
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    latest = report_dir / "prediction_bound_live_formal_latest.json"
    latest.write_text("{}\n", encoding="utf-8")
    (report_dir / "live_order_sync_latest.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setattr(server, "_DASHBOARD_RESPONSE_CACHE", {("cached",): {"ts": 1, "payload": {}}})

    payload = server._dashboard_health_payload(
        now=datetime.now(timezone.utc),
    )

    assert payload["ok"] is True
    assert payload["cache"]["entries"] == 1
    assert payload["runtime_sources"]["formal_prediction"]["exists"] is True
    assert payload["runtime_sources"]["formal_prediction"]["age_seconds"] >= 0
    assert "last_internal_error" in payload


def test_dashboard_health_route_is_available():
    with server.app.test_client() as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert "runtime_sources" in response.get_json()


def test_dashboard_start_script_uses_venv_append_logs_and_rotation():
    script = Path("scripts/start_dashboard.ps1").read_text(encoding="utf-8")

    assert ".venv\\Scripts\\python.exe" in script
    assert "*>>" in script
    assert "Rotate-Log" in script
    assert '$pidPath = Join-Path $runtimeDir "$Name.pid"' in script
    assert 'Start-LoggedDashboardProcess -Name "dashboard-api"' in script
    assert 'Start-LoggedDashboardProcess -Name "dashboard-sync"' in script
