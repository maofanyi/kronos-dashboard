"""Flask API server for Kronos Dashboard."""
import json
import os
import sqlite3
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests
from flask import Flask, g, jsonify, request, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)

DB_PATH = Path(__file__).parent.parent / "data" / "dashboard.db"
BTC_CACHE = {"price": None, "ts": 0}
KRONOS_CHECKPOINT_DIR = Path(os.environ.get("KRONOS_DATA_DIR", "../Kronos/data/checkpoints"))
KRONOS_LOG_DIR = KRONOS_CHECKPOINT_DIR.parent / "logs"
KRONOS_FEATURE_DIR = KRONOS_CHECKPOINT_DIR.parent / "features"
KRONOS_CONFIG_DIR = KRONOS_CHECKPOINT_DIR.parent / "config"
KRONOS_REPORT_DIR = KRONOS_CHECKPOINT_DIR.parent / "reports"
LOG_ERROR_PATTERNS = ("traceback", "poll error", "unexpected", "exception", "timeout", "network", "cuda", "oom")
RISK_LIMITS = {
    "max_daily_loss_usdc": float(os.environ.get("MAX_DAILY_LOSS_USDC", "10")),
    "max_daily_trades": int(os.environ.get("MAX_DAILY_TRADES", "20")),
    "max_consecutive_losses": int(os.environ.get("MAX_CONSECUTIVE_LOSSES", "3")),
    "max_open_or_pending_orders": int(os.environ.get("MAX_OPEN_OR_PENDING_ORDERS", "1")),
}


def _configured_paper_source():
    return (os.environ.get("DASHBOARD_RUN_SOURCE") or os.environ.get("RUN_SOURCE") or "").strip()


def _paper_run_source():
    configured = _configured_paper_source()
    if configured:
        return configured
    if KRONOS_CHECKPOINT_DIR.exists():
        files = sorted(
            KRONOS_CHECKPOINT_DIR.glob("paper_live*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if files:
            return files[0].stem
    return "paper_live"


def _paper_checkpoint_path():
    return KRONOS_CHECKPOINT_DIR / f"{_paper_run_source()}.json"


def _paper_ledger_path():
    return KRONOS_CHECKPOINT_DIR / f"{_paper_run_source()}_ledger.json"


def _safe_feature_path(rel_path: str):
    root = KRONOS_FEATURE_DIR.resolve()
    candidate = (root / rel_path).resolve()
    if root != candidate and root not in candidate.parents:
        return None
    if not candidate.exists() or candidate.suffix.lower() != ".csv":
        return None
    return candidate


def _load_aligned_prod_params():
    path = KRONOS_CONFIG_DIR / "aligned_prod_shift1_params.json"
    payload = _read_json(path) or {}
    return payload.get("best_params") or payload


def _feature_row_count(path: Path):
    try:
        with path.open("r", encoding="utf-8") as f:
            return max(sum(1 for _ in f) - 1, 0)
    except Exception:
        return 0


def _json_float(value, digits: int | None = None):
    try:
        if pd.isna(value):
            return None
        value = float(value)
        return round(value, digits) if digits is not None else value
    except Exception:
        return None


def _summarize_feature_decisions(df: pd.DataFrame, params: dict):
    if df.empty or not params:
        return df, {
            "passed": 0,
            "blocked": int(len(df)),
            "pass_rate": 0.0,
            "candidate_win_rate": 0.0,
            "buy_up": 0,
            "buy_down": 0,
        }

    p5 = df["p5_up"].astype(float)
    p1 = df["p1_up"].astype(float)
    p4 = df["p4_up"].astype(float)
    actual_up = df["actual_up"].astype(bool)

    long_macro_gate = p4
    short_macro_gate = 1.0 - p4
    long_macro_score = (p1 + p4) / 2.0
    short_macro_score = ((1.0 - p1) + (1.0 - p4)) / 2.0
    long_score = p5 * long_macro_score * 1000.0
    short_score = (1.0 - p5) * short_macro_score * 1000.0

    micro_up = float(params.get("micro_up", 1.0))
    micro_down = float(params.get("micro_down", 1.0))
    macro_up = float(params.get("macro_up", 1.0))
    macro_down = float(params.get("macro_down", 1.0))
    prod_score_long = float(params.get("prod_score_long", float("inf")))
    prod_score_short = float(params.get("prod_score_short", float("inf")))
    macro_veto_long = float(params.get("macro_veto_long", 0.0))
    macro_veto_short = float(params.get("macro_veto_short", 0.0))

    long_pass = (
        (p5 >= micro_up)
        & (long_macro_gate >= macro_up)
        & (long_score >= prod_score_long)
        & (short_macro_gate < macro_veto_long)
    )
    short_pass = (
        ((1.0 - p5) >= micro_down)
        & (short_macro_gate >= macro_down)
        & (short_score >= prod_score_short)
        & (long_macro_gate < macro_veto_short)
    )

    long_strength = long_score / prod_score_long
    short_strength = short_score / prod_score_short
    buy_up = long_pass & (~short_pass | (long_strength > short_strength))
    buy_down = short_pass & (~long_pass | (short_strength > long_strength))
    passed = buy_up | buy_down
    won = (buy_up & actual_up) | (buy_down & ~actual_up)

    out = df.copy()
    out["long_score"] = long_score
    out["short_score"] = short_score
    out["action"] = "HOLD"
    out.loc[buy_up, "action"] = "BUY_UP"
    out.loc[buy_down, "action"] = "BUY_DOWN"
    out["passed"] = passed
    out["candidate_won"] = won.where(passed, False)

    passed_count = int(passed.sum())
    buy_up_count = int(buy_up.sum())
    buy_down_count = int(buy_down.sum())
    return out, {
        "passed": passed_count,
        "blocked": int(len(df) - passed_count),
        "pass_rate": passed_count / len(df) if len(df) else 0.0,
        "candidate_win_rate": int(won[passed].sum()) / passed_count if passed_count else 0.0,
        "buy_up": buy_up_count,
        "buy_down": buy_down_count,
    }


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def _tail_jsonl(path: Path, limit: int = 500):
    if not path.exists():
        return []
    lines = deque(maxlen=limit)
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    lines.append(line)
    except Exception:
        return []

    result = []
    for line in lines:
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return result


def _events_checkpoint_path():
    source = _paper_run_source()
    preferred = KRONOS_CHECKPOINT_DIR / f"{source}_events.jsonl"
    if preferred.exists():
        return preferred
    preferred = KRONOS_CHECKPOINT_DIR.parent / "events" / source / f"{source}.jsonl"
    if preferred.exists():
        return preferred
    preferred = KRONOS_CHECKPOINT_DIR / "paper_live_events.jsonl"
    legacy = KRONOS_CHECKPOINT_DIR / "paper_events.jsonl"
    return preferred if preferred.exists() else legacy


def latest_file(root: Path, pattern: str):
    if not root.exists():
        return None
    files = sorted(root.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _latest_json_report(pattern: str):
    path = latest_file(KRONOS_REPORT_DIR, pattern)
    if path is None:
        return None, {}
    return path, _read_json(path) or {}


def _check_status(report: dict, name: str):
    for check in report.get("checks", []) or []:
        if check.get("name") == name:
            return check
    return None


def _parse_dt(value):
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _record_ts(record: dict):
    for key in ("settled_at", "settle_ts", "created_at", "entry_ts", "ts"):
        parsed = _parse_dt(record.get(key))
        if parsed is not None:
            return parsed
    return None


def _record_pnl(record: dict):
    for key in ("pnl_usdc", "realized_pnl", "pnl"):
        try:
            return float(record.get(key))
        except (TypeError, ValueError):
            continue
    return 0.0


def _is_settled_record(record: dict):
    status = str(record.get("status", "")).upper()
    if status in {"SETTLED", "FILLED", "WON", "LOST", "CLOSED"}:
        return True
    return any(key in record for key in ("pnl", "realized_pnl", "pnl_usdc"))


def _risk_records_from_payload(payload):
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    records = []
    for key in ("trades", "orders", "open_orders", "pending_orders"):
        items = payload.get(key)
        if isinstance(items, list):
            records.extend(item for item in items if isinstance(item, dict))
    return records


def _consecutive_losses(records):
    streak = 0
    sorted_records = sorted(records, key=lambda item: _record_ts(item) or datetime.min.replace(tzinfo=timezone.utc))
    for record in sorted_records:
        if _record_pnl(record) < 0:
            streak += 1
        else:
            streak = 0
    return streak


def _live_risk_summary(checkpoint=None):
    checkpoint = checkpoint if isinstance(checkpoint, dict) else (_read_json(_paper_checkpoint_path()) or {})
    ledger = _read_json(_paper_ledger_path())
    records = _risk_records_from_payload(checkpoint) + _risk_records_from_payload(ledger)
    today = datetime.now(timezone.utc).date()
    todays = []
    open_or_pending = 0

    for record in records:
        status = str(record.get("status", "")).upper()
        if status in {"OPEN", "PENDING", "SUBMITTED"}:
            open_or_pending += 1
        ts = _record_ts(record)
        if ts is not None and ts.date() == today:
            todays.append(record)

    settled = [record for record in todays if _is_settled_record(record)]
    daily_pnl = sum(_record_pnl(record) for record in settled)
    daily_trades = len(settled)
    consecutive_losses = _consecutive_losses(settled)
    limits = dict(RISK_LIMITS)
    checks = [
        {
            "key": "risk_daily_loss",
            "label": "Daily loss",
            "ok": daily_pnl > -abs(limits["max_daily_loss_usdc"]),
            "value": round(daily_pnl, 2),
            "expected": f"> -{limits['max_daily_loss_usdc']}",
            "severity": "risk",
        },
        {
            "key": "risk_daily_trades",
            "label": "Daily trades",
            "ok": daily_trades < limits["max_daily_trades"],
            "value": daily_trades,
            "expected": f"< {limits['max_daily_trades']}",
            "severity": "risk",
        },
        {
            "key": "risk_consecutive_losses",
            "label": "Consecutive losses",
            "ok": consecutive_losses < limits["max_consecutive_losses"],
            "value": consecutive_losses,
            "expected": f"< {limits['max_consecutive_losses']}",
            "severity": "risk",
        },
        {
            "key": "risk_open_or_pending",
            "label": "Open or pending",
            "ok": open_or_pending < limits["max_open_or_pending_orders"],
            "value": open_or_pending,
            "expected": f"< {limits['max_open_or_pending_orders']}",
            "severity": "risk",
        },
    ]
    return {
        "ok": all(check["ok"] for check in checks),
        "checks": checks,
        "metrics": {
            "day_utc": today.isoformat(),
            "daily_pnl_usdc": round(daily_pnl, 8),
            "daily_trades": daily_trades,
            "consecutive_losses": consecutive_losses,
            "open_or_pending_orders": open_or_pending,
        },
        "limits": limits,
        "inputs": {
            "checkpoint": str(_paper_checkpoint_path()),
            "ledger": str(_paper_ledger_path()),
            "record_count": len(records),
        },
    }


def _safety_report_summary():
    allowance_path, allowance_report = _latest_json_report("polymarket_clob_*allowance*_audit*.json")
    if not allowance_report:
        allowance_path, allowance_report = _latest_json_report("polymarket_clob_account_read_audit*.json")
    execution_path, execution_report = _latest_json_report("live_dryrun_execution_summary_latest.json")
    checkpoint = _read_json(_paper_checkpoint_path()) or {}

    balance_check = _check_status(allowance_report, "minimum_balance")
    allowance_check = _check_status(allowance_report, "minimum_allowance")
    auth_check = _check_status(allowance_report, "readonly_authenticated_client")
    if auth_check is None:
        auth_check = _check_status(allowance_report, "clob_private_key_present")
    balance_read = _check_status(allowance_report, "readonly_get_balance")
    allowance_read = _check_status(allowance_report, "readonly_get_balance_allowance")

    network_calls = (allowance_report.get("network") or {}).get("calls") or []
    balance_allowance_call = next(
        (call for call in network_calls if call.get("name") == "get_balance_allowance"),
        {},
    )
    orders_call = next((call for call in network_calls if call.get("name") == "get_orders"), {})

    real_orders_env = os.environ.get("KRONOS_ENABLE_REAL_ORDERS", "")
    run_source = _paper_run_source()
    mode = "live" if run_source.startswith("live_") else "dry-run" if "dryrun" in run_source else "paper"
    open_orders_count = len(checkpoint.get("open_orders", []) or [])

    clob_authenticated = bool(auth_check and auth_check.get("ok"))
    account_read_ok = bool(balance_read and balance_read.get("ok"))
    allowance_read_ok = bool(allowance_read and allowance_read.get("ok"))
    balance_ok = bool(balance_check and balance_check.get("ok"))
    allowance_ok = bool(allowance_check and allowance_check.get("ok"))
    real_orders_enabled = real_orders_env == "YES"
    risk_summary = _live_risk_summary(checkpoint)

    checklist = [
        {
            "key": "real_orders_locked",
            "label": "Real orders locked",
            "ok": not real_orders_enabled,
            "value": "locked" if not real_orders_enabled else "enabled",
            "severity": "critical",
        },
        {
            "key": "clob_authenticated",
            "label": "CLOB authenticated",
            "ok": clob_authenticated,
            "value": "ok" if clob_authenticated else "missing",
            "severity": "critical",
        },
        {
            "key": "account_read_ok",
            "label": "Account read",
            "ok": account_read_ok,
            "value": "ok" if account_read_ok else "failed",
            "severity": "critical",
        },
        {
            "key": "allowance_read_ok",
            "label": "Allowance read",
            "ok": allowance_read_ok,
            "value": "ok" if allowance_read_ok else "failed",
            "severity": "critical",
        },
        {
            "key": "minimum_balance",
            "label": "Minimum balance",
            "ok": balance_ok,
            "value": balance_allowance_call.get("balance"),
            "expected": balance_check.get("expected") if balance_check else None,
            "severity": "funding",
        },
        {
            "key": "minimum_allowance",
            "label": "Minimum allowance",
            "ok": allowance_ok,
            "value": balance_allowance_call.get("min_allowance"),
            "expected": allowance_check.get("expected") if allowance_check else None,
            "severity": "funding",
        },
        {
            "key": "open_orders_clear",
            "label": "Open orders clear",
            "ok": open_orders_count == 0,
            "value": open_orders_count,
            "severity": "critical",
        },
    ] + risk_summary["checks"]

    return {
        "mode": mode,
        "run_source": run_source,
        "real_orders_enabled": real_orders_enabled,
        "kill_switch": {
            "state": "armed" if real_orders_enabled else "locked",
            "env": "YES" if real_orders_enabled else "",
        },
        "clob": {
            "authenticated": clob_authenticated,
            "account_read_ok": account_read_ok,
            "allowance_read_ok": allowance_read_ok,
            "open_orders_read_ok": bool(orders_call.get("ok")),
            "open_orders_count": open_orders_count,
        },
        "funding": {
            "balance_ok": balance_ok,
            "allowance_ok": allowance_ok,
            "balance": balance_allowance_call.get("balance"),
            "allowance_count": balance_allowance_call.get("allowance_count"),
            "min_allowance": balance_allowance_call.get("min_allowance"),
            "balance_expected": balance_check.get("expected") if balance_check else None,
            "allowance_expected": allowance_check.get("expected") if allowance_check else None,
        },
        "reports": {
            "allowance": str(allowance_path) if allowance_path else "",
            "execution": str(execution_path) if execution_path else "",
        },
        "checklist": checklist,
        "risk": risk_summary,
        "execution_summary": execution_report.get("summary") or execution_report,
    }


def _tail_source_events(source: str, limit: int = 500):
    audit_dir = KRONOS_CHECKPOINT_DIR.parent / "events" / source
    if not audit_dir.exists():
        return []
    files = sorted(
        list(audit_dir.glob("*.jsonl")) + list(audit_dir.glob("*/*.jsonl")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    lines = deque(maxlen=limit)
    for path in reversed(files[:24]):
        try:
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        lines.append(line)
        except OSError:
            continue

    events = []
    seen = set()
    for line in lines:
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        event_id = ev.get("event_id")
        if event_id and event_id in seen:
            continue
        if event_id:
            seen.add(event_id)
        events.append(ev)
    return events[-limit:]


def _tail_audit_events(limit: int = 500):
    return _tail_source_events(_paper_run_source(), limit=limit)


def _latest_sidecar_started_at():
    latest = latest_file(KRONOS_LOG_DIR, "shadow_sidecar_auto_*.out.log")
    if latest is None:
        return None
    stem = latest.name
    prefix = "shadow_sidecar_auto_"
    if not stem.startswith(prefix):
        return None
    stamp = stem[len(prefix):].split(".")[0]
    try:
        return datetime.strptime(stamp, "%Y%m%d_%H%M%S")
    except ValueError:
        return None


def _shadow_market_report(limit: int = 120, now: datetime | None = None, recent_hours: int = 6):
    now = now or datetime.now()
    cutoff = now - timedelta(hours=recent_hours)
    sidecar_started_at = _latest_sidecar_started_at()
    effective_cutoff = max(cutoff, sidecar_started_at) if sidecar_started_at else cutoff
    events = _tail_source_events("shadow_live", limit=limit)
    watch_events = [e for e in events if e.get("type") == "shadow_watch"]
    errors = [e for e in events if e.get("type") == "shadow_watch_error"]

    def is_recent(ev):
        ts = _parse_ts(ev.get("_t"))
        return ts is not None and ts >= effective_cutoff

    latest_by_decision = {}
    for ev in watch_events:
        decision_id = ev.get("decision_event_id") or ev.get("order_id") or ev.get("event_id")
        if not decision_id:
            continue
        latest_by_decision[decision_id] = ev

    latest_success = list(latest_by_decision.values())[-1] if latest_by_decision else None
    recent_watch = [ev for ev in latest_by_decision.values() if is_recent(ev)]

    recent = []
    for ev in list(recent_watch)[-12:]:
        source_decision = ev.get("source_decision") or {}
        summary = ev.get("summary") or {}
        market = ev.get("market") or {}
        recent.append({
            "decision_event_id": ev.get("decision_event_id"),
            "bar_index": ev.get("bar_index"),
            "created_at": ev.get("_t"),
            "sample": ev.get("sample"),
            "action": ev.get("action"),
            "price": ev.get("price"),
            "best_bid": ev.get("best_bid"),
            "best_ask": ev.get("best_ask"),
            "token_id": ev.get("token_id"),
            "market": {
                "id": market.get("id"),
                "slug": market.get("slug"),
                "question": market.get("question"),
            },
            "source_decision": {
                "bar_index": source_decision.get("bar_index") or source_decision.get("n"),
                "action": source_decision.get("action"),
                "dir5": source_decision.get("dir5"),
                "dir4": source_decision.get("dir4"),
                "created_at": source_decision.get("_t") or source_decision.get("ts"),
            },
            "summary": {
                "samples": summary.get("samples"),
                "open_price": summary.get("open_price"),
                "latest_price": summary.get("latest_price"),
                "repost_count": summary.get("repost_count"),
                "block_count": summary.get("block_count"),
                "average_target_price": summary.get("average_target_price"),
            },
        })

    latest = recent[-1] if recent else None
    recent_errors = [e for e in errors if is_recent(e)]
    last_error = errors[-1] if errors else None
    return {
        "latest": latest,
        "latest_stale": bool(latest_success and not latest),
        "last_success_at": latest_success.get("_t") if latest_success else None,
        "last_error_at": last_error.get("_t") if last_error else None,
        "current_sidecar_started_at": str(sidecar_started_at) if sidecar_started_at else None,
        "recent_hours": recent_hours,
        "recent": list(reversed(recent)),
        "errors": [
            {
                "decision_event_id": e.get("decision_event_id"),
                "error": e.get("error"),
                "created_at": e.get("_t"),
            }
            for e in recent_errors[-12:]
        ],
        "event_count": len(watch_events),
        "updated_at": latest.get("created_at") if latest else None,
    }


def _parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        try:
            return pd.Timestamp(value).to_pydatetime()
        except Exception:
            return None


def _age_seconds(value):
    ts = _parse_ts(value)
    if ts is None:
        return None
    if ts.tzinfo is not None:
        return max(0, (pd.Timestamp.now(tz=ts.tzinfo).to_pydatetime() - ts).total_seconds())
    return max(0, (datetime.now() - ts).total_seconds())


def _latest_log_report():
    report = {"out_log": "", "err_log": "", "errors": []}
    if not KRONOS_LOG_DIR.exists():
        return report
    source = _paper_run_source()
    patterns = {
        "out_log": [f"{source}_*.out.log", "paper_live_rest_*.out.log"],
        "err_log": [f"{source}_*.err.log", "paper_live_rest_*.err.log"],
    }
    for key, candidates in patterns.items():
        files = []
        for pattern in candidates:
            files = sorted(KRONOS_LOG_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
            if files:
                break
        if files:
            report[key] = str(files[0])
            try:
                tail = files[0].read_text(encoding="utf-8", errors="replace").splitlines()[-220:]
            except Exception:
                tail = []
            for line in tail:
                lowered = line.lower()
                if any(pat in lowered for pat in LOG_ERROR_PATTERNS):
                    report["errors"].append({"file": files[0].name, "line": line[-400:]})
    report["errors"] = report["errors"][-20:]
    return report


def _order_key(item):
    if not item:
        return None
    if item.get("order_id"):
        return item.get("order_id")
    entry_bar = item.get("entry_bar", item.get("bar_index"))
    settle_bar = item.get("settle_bar")
    direction = item.get("direction") or item.get("dir") or item.get("dir5")
    if entry_bar is None:
        return None
    return f"bar:{entry_bar}:{settle_bar}:{direction}"


def _copy_order_fields(target, order):
    if not order:
        return
    for key in (
        "order_id",
        "market_id",
        "entry_bar",
        "settle_bar",
        "entry_price",
        "settle_price",
        "direction",
        "dir",
        "size",
        "regime",
        "maker_price",
        "price",
        "filled_bar",
        "repost_count",
    ):
        value = order.get(key)
        if value is not None and target.get(key) is None:
            target[key] = value


def _build_order_lifecycle(recent_events, recent_audit_events, trades, pending, open_orders, bar_index, limit=40):
    records = {}

    def ensure_record(key, entry_bar=None):
        if key is None and entry_bar is not None:
            key = f"bar:{entry_bar}:None:None"
        if key not in records:
            records[key] = {
                "order_id": None,
                "market_id": None,
                "entry_bar": entry_bar,
                "settle_bar": None,
                "direction": None,
                "size": None,
                "regime": None,
                "maker_price": None,
                "entry_price": None,
                "settle_price": None,
                "signal_time": None,
                "created_at": None,
                "filled_at": None,
                "settled_at": None,
                "rejected_at": None,
                "rejected_reason": None,
                "status": "UNKNOWN",
                "source": "event",
                "pnl": None,
                "won": None,
                "balance_after": None,
                "repost_count": None,
                "countdown_bars": None,
                "overdue_bars": 0,
            }
        return records[key]

    decision_by_n = {e.get("n"): e for e in recent_events if e.get("n") is not None}

    for ev in recent_events:
        if ev.get("type") != "decision" or ev.get("executable") is not True:
            continue
        entry_bar = ev.get("n") or ev.get("bar_index")
        rec = ensure_record(f"signal:{entry_bar}", entry_bar=entry_bar)
        rec.update({
            "entry_bar": entry_bar,
            "direction": str(ev.get("dir5") or ev.get("direction") or "").upper() or None,
            "size": ev.get("size"),
            "regime": ev.get("regime"),
            "signal_time": ev.get("_t") or ev.get("ts"),
            "status": "SIGNAL",
            "source": "signal",
        })

    for ev in recent_audit_events:
        ev_type = ev.get("type")
        if ev_type not in {"order_created", "order_filled", "order_settled", "order_rejected"}:
            continue
        order = ev.get("order") or {}
        key = ev.get("order_id") or _order_key(order)
        if ev_type == "order_rejected":
            key = key or f"rejected:{ev.get('bar_index')}"
        rec = ensure_record(key, entry_bar=order.get("entry_bar") or ev.get("bar_index"))
        _copy_order_fields(rec, order)
        rec["order_id"] = ev.get("order_id") or rec.get("order_id")
        entry_bar = rec.get("entry_bar") or ev.get("bar_index")
        decision = decision_by_n.get(entry_bar) or {}
        if decision:
            rec["signal_time"] = rec.get("signal_time") or decision.get("_t") or decision.get("ts")
            rec["direction"] = rec.get("direction") or str(decision.get("dir5") or "").upper() or None
            rec["size"] = rec.get("size") if rec.get("size") is not None else decision.get("size")
            rec["regime"] = rec.get("regime") or decision.get("regime")
        if ev_type == "order_created":
            rec["created_at"] = ev.get("_t")
            rec["status"] = "OPEN"
            rec["source"] = "maker"
        elif ev_type == "order_filled":
            rec["filled_at"] = ev.get("_t")
            rec["status"] = "FILLED"
            rec["source"] = "maker"
        elif ev_type == "order_settled":
            trade = ev.get("trade") or {}
            _copy_order_fields(rec, trade)
            rec["settled_at"] = ev.get("_t")
            rec["status"] = "SETTLED"
            rec["source"] = "settlement"
            rec["pnl"] = trade.get("pnl")
            rec["won"] = trade.get("won")
            rec["balance_after"] = ev.get("balance")
        elif ev_type == "order_rejected":
            rec["rejected_at"] = ev.get("_t")
            rec["rejected_reason"] = ev.get("reason", "")
            rec["status"] = "REJECTED"
            rec["source"] = "maker"

    for collection, status in ((open_orders, "OPEN"), (pending, "FILLED"), (trades, "SETTLED")):
        for item in collection:
            key = _order_key(item)
            rec = ensure_record(key, entry_bar=item.get("entry_bar"))
            _copy_order_fields(rec, item)
            rec["source"] = "checkpoint"
            if status == "SETTLED":
                rec["pnl"] = item.get("pnl")
                rec["won"] = item.get("won")
                rec["settled_at"] = rec.get("settled_at") or item.get("settled_at") or item.get("created_at")
            if rec.get("status") not in {"SETTLED", "REJECTED"}:
                rec["status"] = status

    by_entry = {}
    for rec in records.values():
        entry_bar = rec.get("entry_bar")
        if entry_bar is not None:
            by_entry.setdefault(entry_bar, []).append(rec)
    for rec in list(records.values()):
        if rec.get("source") != "signal":
            continue
        matches = [r for r in by_entry.get(rec.get("entry_bar"), []) if r is not rec and r.get("order_id")]
        if matches:
            match = matches[-1]
            match["signal_time"] = match.get("signal_time") or rec.get("signal_time")
            match["direction"] = match.get("direction") or rec.get("direction")
            match["size"] = match.get("size") if match.get("size") is not None else rec.get("size")
            del records[next(k for k, v in records.items() if v is rec)]

    anomalies = []
    for rec in records.values():
        settle_bar = rec.get("settle_bar")
        if settle_bar is not None and rec.get("status") != "SETTLED":
            rec["countdown_bars"] = int(settle_bar) - int(bar_index or 0)
            if int(settle_bar) <= int(bar_index or 0) and rec.get("status") in {"FILLED", "OPEN"}:
                rec["status"] = "SETTLEMENT_OVERDUE"
                rec["overdue_bars"] = int(bar_index or 0) - int(settle_bar)
                anomalies.append({
                    "type": "settlement_overdue",
                    "order_id": rec.get("order_id"),
                    "entry_bar": rec.get("entry_bar"),
                    "settle_bar": settle_bar,
                    "status": rec.get("status"),
                    "overdue_bars": rec["overdue_bars"],
                })

    def sort_key(rec):
        return (
            int(rec.get("settle_bar") or rec.get("entry_bar") or -1),
            str(rec.get("order_id") or ""),
        )

    recent = sorted(records.values(), key=sort_key, reverse=True)
    return {
        "recent": recent[:limit],
        "anomalies": anomalies[-20:],
        "counts": {
            "open": sum(1 for r in records.values() if r.get("status") == "OPEN"),
            "filled": sum(1 for r in records.values() if r.get("status") == "FILLED"),
            "settled": sum(1 for r in records.values() if r.get("status") == "SETTLED"),
            "rejected": sum(1 for r in records.values() if r.get("status") == "REJECTED"),
            "overdue": len(anomalies),
        },
    }


def _num(value):
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _maker_quality_report(recent_events, recent_audit_events, limit=20):
    shadow_events = _tail_source_events("shadow_live", limit=limit * 12)
    watch_events = [e for e in shadow_events if e.get("type") == "shadow_watch"]
    error_events = [e for e in shadow_events if e.get("type") == "shadow_watch_error"]
    rejected_events = [e for e in recent_audit_events if e.get("type") == "order_rejected"]
    decisions = {
        e.get("event_id"): e
        for e in recent_events
        if e.get("type") == "decision" and e.get("event_id")
    }
    rejected_by_bar = {}
    for ev in rejected_events:
        rejected_by_bar.setdefault(ev.get("bar_index"), []).append(ev)

    grouped = {}
    for ev in watch_events:
        decision_id = ev.get("decision_event_id") or ev.get("order_id") or ev.get("event_id")
        grouped.setdefault(decision_id, []).append(ev)
    error_by_decision = {}
    for ev in error_events:
        decision_id = ev.get("decision_event_id") or ev.get("order_id") or ev.get("event_id")
        error_by_decision.setdefault(decision_id, []).append(ev)

    recent = []
    for decision_id, events in grouped.items():
        events = sorted(events, key=lambda e: int(e.get("sample", 0) or 0))
        samples = len(events)
        if samples == 0:
            continue
        buy_one_samples = 0
        price_cap_blocks = 0
        liquidity_blocks = 0
        repost_prices = []
        target_prices = []
        best_bids = []
        best_asks = []
        latest = events[-1]
        for ev in events:
            action = str(ev.get("action") or "").upper()
            price = _num(ev.get("price"))
            best_bid = _num(ev.get("best_bid"))
            best_ask = _num(ev.get("best_ask"))
            reason = str(ev.get("reason") or "").lower()
            if best_bid is not None:
                best_bids.append(best_bid)
            if best_ask is not None:
                best_asks.append(best_ask)
            if action in {"OPEN", "HOLD", "REPOST"} and price is not None and (best_bid is None or price >= best_bid):
                buy_one_samples += 1
            if action == "BLOCK":
                if "cap" in reason or (price is not None and best_bid is not None and price < best_bid):
                    price_cap_blocks += 1
                else:
                    liquidity_blocks += 1
            if action == "REPOST" and price is not None:
                repost_prices.append(price)
            if action in {"OPEN", "REPOST"} and price is not None:
                target_prices.append(price)

        decision = decisions.get(decision_id) or latest.get("source_decision") or {}
        entry_bar = decision.get("bar_index") or decision.get("n") or latest.get("bar_index")
        rejected = rejected_by_bar.get(entry_bar, [])
        api_errors = error_by_decision.get(decision_id, [])
        average_target = round(sum(target_prices) / len(target_prices), 4) if target_prices else None
        max_target = max(target_prices) if target_prices else None
        buy_one_rate = round(buy_one_samples / samples, 4) if samples else 0
        recent.append({
            "decision_event_id": decision_id,
            "entry_bar": entry_bar,
            "action": decision.get("action") or latest.get("action"),
            "direction": decision.get("dir5") or decision.get("direction"),
            "created_at": decision.get("_t") or decision.get("ts") or latest.get("_t"),
            "market": latest.get("market") or {},
            "token_id": latest.get("token_id"),
            "samples": samples,
            "buy_one_samples": buy_one_samples,
            "buy_one_rate": buy_one_rate,
            "repost_count": len(repost_prices),
            "block_count": price_cap_blocks + liquidity_blocks,
            "price_cap_blocks": price_cap_blocks,
            "liquidity_blocks": liquidity_blocks,
            "api_error_count": len(api_errors),
            "average_target_price": average_target,
            "max_target_price": max_target,
            "latest_target_price": _num(latest.get("price")),
            "latest_best_bid": _num(latest.get("best_bid")),
            "latest_best_ask": _num(latest.get("best_ask")),
            "max_best_bid": max(best_bids) if best_bids else None,
            "min_best_ask": min(best_asks) if best_asks else None,
            "price_cap_triggered": price_cap_blocks > 0,
            "not_filled_reason": rejected[-1].get("reason") if rejected else (api_errors[-1].get("error") if api_errors else ""),
            "updated_at": latest.get("_t"),
        })

    recent = sorted(
        recent,
        key=lambda row: (_parse_ts(row.get("updated_at")) or datetime.min, int(row.get("entry_bar") or -1)),
        reverse=True,
    )[:limit]
    watch_count = len(recent)
    summary = {
        "watch_count": watch_count,
        "avg_buy_one_rate": round(sum(row["buy_one_rate"] for row in recent) / watch_count, 4) if watch_count else 0,
        "total_reposts": sum(row["repost_count"] for row in recent),
        "total_blocks": sum(row["block_count"] for row in recent),
        "total_price_cap_blocks": sum(row["price_cap_blocks"] for row in recent),
        "api_error_count": len(error_events),
        "avg_target_price": round(
            sum(row["average_target_price"] for row in recent if row["average_target_price"] is not None)
            / max(1, sum(1 for row in recent if row["average_target_price"] is not None)),
            4,
        ) if recent else None,
        "max_target_price": max(
            [row["max_target_price"] for row in recent if row["max_target_price"] is not None],
            default=None,
        ),
    }
    return {"summary": summary, "recent": recent}


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

@app.route("/api/status")
def api_status():
    source = request.args.get("source", "live")
    db = get_db()
    row = db.execute(
        "SELECT * FROM snapshots WHERE source=? ORDER BY id DESC LIMIT 1", (source,)
    ).fetchone()
    if row is None:
        return jsonify({"balance": 500, "trades_count": 0, "wr": 0, "cooldown_left": 0})
    return jsonify(dict(row))


@app.route("/api/signal-stats")
def api_signal_stats():
    source = request.args.get("source", "live")
    db = get_db()
    row = db.execute(
        """SELECT
             COUNT(*) AS total,
             SUM(CASE WHEN filt_passed=1 THEN 1 ELSE 0 END) AS passed,
             MAX(created_at) AS latest_created_at
           FROM events
           WHERE source=?""",
        (source,),
    ).fetchone()
    total = int(row["total"] or 0)
    passed = int(row["passed"] or 0)
    return jsonify(
        {
            "source": source,
            "total": total,
            "passed": passed,
            "blocked": max(total - passed, 0),
            "pass_rate": passed / total if total else 0,
            "latest_created_at": row["latest_created_at"],
        }
    )


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

@app.route("/api/events")
def api_events():
    source = request.args.get("source", "live")
    limit = int(request.args.get("limit", 100))
    since = request.args.get("since", "")  # Optional: filter by min created_at
    db = get_db()
    if since:
        rows = db.execute(
            "SELECT * FROM events WHERE source=? AND created_at >= ? ORDER BY created_at DESC LIMIT ?",
            (source, since, limit),
        ).fetchall()
    else:
        # Default: last 12 hours
        rows = db.execute(
            "SELECT * FROM events WHERE source=? AND created_at >= datetime('now', '-12 hours') ORDER BY created_at DESC LIMIT ?",
            (source, limit),
        ).fetchall()
    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------------------
# Backtest feature CSVs
# ---------------------------------------------------------------------------

@app.route("/api/backtest/feature-files")
def api_backtest_feature_files():
    if not KRONOS_FEATURE_DIR.exists():
        return jsonify([])
    files = []
    for path in sorted(KRONOS_FEATURE_DIR.rglob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            rel = path.relative_to(KRONOS_FEATURE_DIR).as_posix()
            stat = path.stat()
            files.append(
                {
                    "path": rel,
                    "name": path.name,
                    "size": stat.st_size,
                    "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "rows": _feature_row_count(path),
                }
            )
        except Exception:
            continue
    return jsonify(files)


@app.route("/api/backtest/feature-summary")
def api_backtest_feature_summary():
    rel = request.args.get("file", "")
    limit = max(20, min(int(request.args.get("limit", 160)), 500))
    path = _safe_feature_path(rel)
    if path is None:
        return jsonify({"error": "feature csv not found", "file": rel}), 404

    try:
        df = pd.read_csv(path)
    except Exception as exc:
        return jsonify({"error": f"failed to read feature csv: {exc}", "file": rel}), 500

    params = _load_aligned_prod_params()
    scored, decision_stats = _summarize_feature_decisions(df, params)
    tail = scored.tail(limit)
    rows = []
    for _, row in tail.iterrows():
        rows.append(
            {
                "ts": row.get("ts"),
                "close": _json_float(row.get("close"), 2),
                "settle_ts": row.get("settle_ts"),
                "settle_close": _json_float(row.get("settle_close"), 2),
                "actual_up": bool(row.get("actual_up")) if not pd.isna(row.get("actual_up")) else None,
                "p5_up": _json_float(row.get("p5_up")),
                "p1_up": _json_float(row.get("p1_up")),
                "p4_up": _json_float(row.get("p4_up")),
                "p5_conf": _json_float(row.get("p5_conf")),
                "p1_conf": _json_float(row.get("p1_conf")),
                "p4_conf": _json_float(row.get("p4_conf")),
                "long_score": _json_float(row.get("long_score"), 1),
                "short_score": _json_float(row.get("short_score"), 1),
                "action": row.get("action"),
                "passed": bool(row.get("passed")),
                "candidate_won": bool(row.get("candidate_won")) if bool(row.get("passed")) else None,
            }
        )

    return jsonify(
        {
            "file": rel,
            "rows": int(len(df)),
            "start_ts": None if df.empty else str(df.iloc[0].get("ts")),
            "end_ts": None if df.empty else str(df.iloc[-1].get("ts")),
            "actual_up_rate": _json_float(df["actual_up"].mean()) if "actual_up" in df else None,
            "avg_p5_up": _json_float(df["p5_up"].mean()) if "p5_up" in df else None,
            "avg_p1_up": _json_float(df["p1_up"].mean()) if "p1_up" in df else None,
            "avg_p4_up": _json_float(df["p4_up"].mean()) if "p4_up" in df else None,
            "decision_stats": decision_stats,
            "params": params,
            "preview": rows,
        }
    )


# ---------------------------------------------------------------------------
# Trades
# ---------------------------------------------------------------------------

@app.route("/api/trades")
def api_trades():
    source = request.args.get("source", "live")
    limit = int(request.args.get("limit", 100))
    db = get_db()
    rows = db.execute(
        """SELECT * FROM trades
           WHERE source=?
           ORDER BY
             CASE WHEN won=-1 THEN 0 ELSE 1 END,
             settle_bar DESC,
             entry_bar DESC
           LIMIT ?""",
        (source, limit),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/live-intel")
def api_live_intel():
    limit = int(request.args.get("limit", 240))
    checkpoint_path = _paper_checkpoint_path()
    events_path = _events_checkpoint_path()
    checkpoint = _read_json(checkpoint_path) or {}
    events = _tail_jsonl(events_path, limit=limit)
    audit_events = _tail_audit_events(limit=limit * 3)

    trades = checkpoint.get("trades", []) or []
    pending = checkpoint.get("pending_orders", []) or []
    open_orders = checkpoint.get("open_orders", []) or []
    bar_index = int(checkpoint.get("bar_index", 0) or 0)
    checkpoint_age = _age_seconds(checkpoint.get("timestamp") or checkpoint.get("updated_at"))
    latest_event = events[-1] if events else {}
    latest_event_age = _age_seconds(
        latest_event.get("_t") or latest_event.get("ts") or latest_event.get("created_at")
    )

    trade_entry_bars = {t.get("entry_bar") for t in trades}
    pending_entry_bars = {p.get("entry_bar") for p in pending}
    open_entry_bars = {o.get("entry_bar") for o in open_orders}
    rejected_events = [e for e in audit_events if e.get("type") == "order_rejected"]
    rejected_bars = {e.get("bar_index") for e in rejected_events}
    order_entry_bars = trade_entry_bars | pending_entry_bars | open_entry_bars | rejected_bars

    trade_keys = {
        (t.get("entry_bar"), t.get("settle_bar"), t.get("dir") or t.get("direction"), t.get("size"))
        for t in trades
    }
    stale_pending = [
        p
        for p in pending
        if p.get("settle_bar") is not None and int(p.get("settle_bar", 0) or 0) <= bar_index
    ]
    duplicated_pending = [
        p
        for p in pending
        if (p.get("entry_bar"), p.get("settle_bar"), p.get("direction"), p.get("size")) in trade_keys
    ]

    recent_events = events[-limit:]
    recent_audit_events = audit_events[-limit * 3:]
    executable_events = [e for e in recent_events if e.get("executable") is True]
    generated_events = [e for e in executable_events if e.get("n") in order_entry_bars]
    settled_events = [e for e in executable_events if e.get("n") in trade_entry_bars]
    non_hold_events = [e for e in recent_events if e.get("action") and e.get("action") != "HOLD"]
    filt_passed_events = [e for e in recent_events if e.get("filt") is True]
    blocked_events = [e for e in recent_events if e.get("executable") is False]
    max_open_rejections = [
        e for e in rejected_events
        if "max open orders" in str(e.get("reason", "")).lower()
    ]
    maker_opened_events = [e for e in recent_audit_events if e.get("type") == "order_created" and e.get("order", {}).get("status") == "OPEN"]
    filled_events = [e for e in recent_audit_events if e.get("type") == "order_filled"]
    settled_audit_events = [e for e in recent_audit_events if e.get("type") == "order_settled"]

    created_by_order = {
        e.get("order_id"): e
        for e in recent_audit_events
        if e.get("type") == "order_created" and e.get("order_id")
    }
    fill_delays = []
    for ev in filled_events:
        order_id = ev.get("order_id")
        created = created_by_order.get(order_id)
        if not created:
            continue
        fill_delays.append({
            "order_id": order_id,
            "entry_bar": created.get("bar_index"),
            "filled_bar": ev.get("bar_index"),
            "delay_bars": max(0, int(ev.get("bar_index", 0) or 0) - int(created.get("bar_index", 0) or 0)),
            "market_id": ev.get("order", {}).get("market_id") or created.get("order", {}).get("market_id"),
            "price": ev.get("order", {}).get("maker_price") or ev.get("order", {}).get("price"),
            "created_at": created.get("_t"),
            "filled_at": ev.get("_t"),
        })
    avg_fill_delay = (
        sum(item["delay_bars"] for item in fill_delays) / len(fill_delays)
        if fill_delays else 0
    )
    order_lifecycle = _build_order_lifecycle(
        recent_events,
        recent_audit_events,
        trades,
        pending,
        open_orders,
        bar_index,
        limit=40,
    )
    maker_quality = _maker_quality_report(recent_events, recent_audit_events, limit=20)

    missing_orders = [
        {
            "n": e.get("n"),
            "action": e.get("action"),
            "reason": e.get("reason", ""),
            "created_at": e.get("_t") or e.get("ts"),
        }
        for e in executable_events
        if e.get("n") not in order_entry_bars
    ][-12:]

    inconsistent_events = []
    for e in recent_events[-80:]:
        executable = e.get("executable")
        block_reason = e.get("block_reason", "")
        if executable is True and block_reason:
            inconsistent_events.append({"n": e.get("n"), "issue": "executable_with_block_reason", "block_reason": block_reason})
        if executable is False and e.get("action") != "HOLD" and not block_reason:
            inconsistent_events.append({"n": e.get("n"), "issue": "blocked_without_reason", "action": e.get("action")})

    event_by_n = {
        str(e.get("n")): {
            "n": e.get("n"),
            "action": e.get("action"),
            "dir5": e.get("dir5"),
            "dir4": e.get("dir4"),
            "regime": e.get("regime"),
            "filt": e.get("filt"),
            "executable": e.get("executable"),
            "block_reason": e.get("block_reason", ""),
            "reason": e.get("reason", ""),
            "created_at": e.get("_t") or e.get("ts"),
        }
        for e in recent_events
        if e.get("n") is not None
    }

    decision_by_n = {e.get("n"): e for e in recent_events if e.get("n") is not None}
    missed_trades = []
    for rejected in max_open_rejections[-20:]:
        entry_bar = rejected.get("bar_index")
        decision = decision_by_n.get(entry_bar) or {}
        size = float(decision.get("size", 0) or 0)
        direction = str(decision.get("dir5") or decision.get("direction") or "").upper()
        missed_trades.append({
            "entry_bar": entry_bar,
            "settle_bar": int(entry_bar or 0) + 13 if entry_bar is not None else None,
            "action": decision.get("action"),
            "direction": direction,
            "size": size,
            "reason": rejected.get("reason", ""),
            "created_at": rejected.get("_t"),
            "signal_time": decision.get("_t") or decision.get("ts"),
        })

    log_report = _latest_log_report()
    warnings = []
    if checkpoint_age is None or checkpoint_age > 600:
        warnings.append("checkpoint_stale")
    if latest_event_age is None or latest_event_age > 600:
        warnings.append("events_stale")
    if stale_pending:
        warnings.append("stale_pending")
    if order_lifecycle["anomalies"]:
        warnings.append("settlement_overdue")
    if duplicated_pending:
        warnings.append("duplicated_pending")
    if missing_orders:
        warnings.append("executable_without_order")
    if inconsistent_events:
        warnings.append("event_consistency")
    hard_log_errors = [
        e for e in log_report["errors"]
        if "poll error" not in (e.get("line", "").lower())
    ]
    if log_report["errors"] and (hard_log_errors or checkpoint_age is None or checkpoint_age > 600 or latest_event_age is None or latest_event_age > 600):
        warnings.append("log_errors")

    return jsonify(
        {
            "health": {
                "run_source": _paper_run_source(),
                "state": "warning" if warnings else "ok",
                "warnings": warnings,
                "checkpoint_age_seconds": checkpoint_age,
                "latest_event_age_seconds": latest_event_age,
                "bar_index": bar_index,
                "checkpoint_timestamp": checkpoint.get("timestamp"),
                "latest_event_n": latest_event.get("n"),
                "latest_event_time": latest_event.get("_t") or latest_event.get("ts"),
                "pending_count": len(pending),
                "open_order_count": len(open_orders),
                "trades_count": len(trades),
                "stale_pending_count": len(stale_pending),
                "duplicated_pending_count": len(duplicated_pending),
            },
            "funnel": {
                "total": len(recent_events),
                "filt_passed": len(filt_passed_events),
                "non_hold": len(non_hold_events),
                "executable": len(executable_events),
                "orders_generated": len(generated_events),
                "maker_opened": len(maker_opened_events),
                "filled": len(filled_events),
                "settled": len(settled_events),
                "settled_audit": len(settled_audit_events),
                "rejected": len(rejected_events),
                "blocked": len(blocked_events),
            },
            "maker": {
                "open_orders": open_orders[-20:],
                "pending_positions": pending[-20:],
                "rejected_orders": [
                    {
                        "bar_index": e.get("bar_index"),
                        "reason": e.get("reason", ""),
                        "created_at": e.get("_t"),
                    }
                    for e in rejected_events[-20:]
                ],
                "fill_delays": fill_delays[-20:],
                "avg_fill_delay_bars": avg_fill_delay,
            },
            "order_lifecycle": order_lifecycle,
            "maker_quality": maker_quality,
            "risk": {
                "max_open_orders": int(os.environ.get("MAX_OPEN_ORDERS", "8")),
                "max_open_rejections_count": len(max_open_rejections),
                "missed_trades": missed_trades,
            },
            "issues": {
                "missing_orders": missing_orders,
                "inconsistent_events": inconsistent_events[-12:],
                "stale_pending": stale_pending[-12:],
                "duplicated_pending": duplicated_pending[-12:],
                "log_errors": log_report["errors"],
            },
            "logs": {
                "out_log": log_report["out_log"],
                "err_log": log_report["err_log"],
            },
            "shadow_market": _shadow_market_report(limit=limit * 3),
            "events_by_n": event_by_n,
        }
    )


@app.route("/api/live-safety")
def api_live_safety():
    return jsonify(_safety_report_summary())


@app.route("/api/live-risk")
def api_live_risk():
    return jsonify(_live_risk_summary())


# ---------------------------------------------------------------------------
# BTC price (proxied from Binance, cached 30s)
# ---------------------------------------------------------------------------

@app.route("/api/btc/price")
def api_btc_price():
    now = time.time()
    if BTC_CACHE["price"] and (now - BTC_CACHE["ts"]) < 30:
        return jsonify(BTC_CACHE["price"])

    try:
        resp = requests.get(
            "https://api.binance.com/api/v3/ticker/price", params={"symbol": "BTCUSDT"}, timeout=5
        )
        data = resp.json()
        BTC_CACHE["price"] = {"price": float(data["price"]), "symbol": "BTCUSDT"}
        BTC_CACHE["ts"] = now
    except Exception:
        if BTC_CACHE["price"] is None:
            return jsonify({"price": 0, "error": "unavailable"}), 503
    return jsonify(BTC_CACHE["price"])


@app.route("/api/btc/klines")
def api_btc_klines():
    tf = request.args.get("tf", "5m")
    limit = int(request.args.get("limit", 200))
    db = get_db()

    # Map timeframe to Binance interval and table
    table = "btc_klines" if tf == "5m" else f"btc_klines_{tf}"
    interval = {"5m": "5m", "1h": "1h", "4h": "4h"}.get(tf, "5m")

    # Create table if not exists
    if tf != "5m":
        db.execute(f"""CREATE TABLE IF NOT EXISTS {table} (
            timestamp TEXT PRIMARY KEY, open REAL, high REAL, low REAL, close REAL, volume REAL)""")
        db.commit()

    rows = db.execute(f"SELECT * FROM {table} ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
    if len(rows) >= limit:
        return jsonify([dict(r) for r in reversed(rows)])

    # Not enough cached — fetch from Binance
    try:
        resp = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": "BTCUSDT", "interval": interval, "limit": limit},
            timeout=10,
        )
        data = resp.json()
        for k in data:
            db.execute(
                f"INSERT OR IGNORE INTO {table} VALUES (?, ?, ?, ?, ?, ?)",
                (str(pd.Timestamp(k[0], unit="ms")), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])),
            )
        db.commit()
        rows = db.execute(f"SELECT * FROM {table} ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
        return jsonify([dict(r) for r in reversed(rows)])
    except Exception as e:
        return jsonify({"error": str(e)}), 503


# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------

@app.route("/api/compare")
def api_compare():
    sources = request.args.get("sources", "live,history").split(",")
    limit = int(request.args.get("limit", 200))
    db = get_db()

    result = {}
    for src in sources:
        events = db.execute(
            "SELECT * FROM events WHERE source=? ORDER BY id DESC LIMIT ?", (src, limit)
        ).fetchall()
        trades = db.execute(
            "SELECT * FROM trades WHERE source=? ORDER BY id DESC LIMIT ?", (src, limit)
        ).fetchall()
        snap = db.execute(
            "SELECT * FROM snapshots WHERE source=? ORDER BY id DESC LIMIT 1", (src,)
        ).fetchone()
        result[src] = {
            "events": [dict(r) for r in reversed(events)],
            "trades": [dict(r) for r in reversed(trades)],
            "snapshot": dict(snap) if snap else {},
        }
    return jsonify(result)


# ---------------------------------------------------------------------------
# Serve React SPA
# ---------------------------------------------------------------------------

@app.route("/")
def serve_index():
    return send_from_directory("static", "index.html")


@app.route("/favicon.ico")
def favicon():
    return "", 204


@app.route("/<path:path>")
def serve_static(path):
    if os.path.exists(os.path.join("static", path)):
        return send_from_directory("static", path)
    return send_from_directory("static", "index.html")


if __name__ == "__main__":
    host = os.environ.get("DASHBOARD_HOST", "0.0.0.0")
    port = int(os.environ.get("DASHBOARD_PORT", "8090"))
    app.run(host=host, port=port, debug=False, threaded=True)
