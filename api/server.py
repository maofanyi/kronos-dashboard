"""Flask API server for Kronos Dashboard."""
import asyncio
from calendar import monthrange
import hashlib
import hmac
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd
import requests
from flask import Flask, g, jsonify, request, send_from_directory
from flask_cors import CORS

try:
    from .strategy_differences import (
        DIFFERENCE_TYPES,
        reconcile_strategy_orders,
        summarize_difference_rows,
    )
except ImportError:
    from strategy_differences import (
        DIFFERENCE_TYPES,
        reconcile_strategy_orders,
        summarize_difference_rows,
    )

app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)

DB_PATH = Path(__file__).parent.parent / "data" / "dashboard.db"
BTC_CACHE = {"price": None, "ts": 0}
BTC_LIVE_CACHE = {"price": None, "timestamp": None, "received_at": None, "source": None, "status": "idle", "error": None}
BTC_LIVE_TICKS = deque(maxlen=600)
BTC_LIVE_LOCK = threading.Lock()
BTC_LIVE_WORKER = {"started": False, "thread": None}
BTC_CANDLE_CACHE = {}
BTC_CANDLE_LOCK = threading.Lock()
_DASHBOARD_RESPONSE_CACHE = {}
_DASHBOARD_RESPONSE_CACHE_LOCK = threading.Lock()
KRONOS_CHECKPOINT_DIR = Path(os.environ.get("KRONOS_DATA_DIR", "../Kronos/data/checkpoints"))
KRONOS_LOG_DIR = KRONOS_CHECKPOINT_DIR.parent / "logs"
KRONOS_FEATURE_DIR = KRONOS_CHECKPOINT_DIR.parent / "features"
KRONOS_CONFIG_DIR = KRONOS_CHECKPOINT_DIR.parent / "config"
KRONOS_REPORT_DIR = KRONOS_CHECKPOINT_DIR.parent / "reports"
KRONOS_RUNTIME_DIR = KRONOS_CHECKPOINT_DIR.parent / "runtime"
LOG_ERROR_PATTERNS = ("traceback", "poll error", "unexpected", "exception", "timeout", "network", "oom")
RISK_LIMITS = {
    "max_daily_loss_usdc": float(os.environ.get("MAX_DAILY_LOSS_USDC", "10")),
    "max_daily_trades": int(os.environ.get("MAX_DAILY_TRADES", "20")),
    "max_consecutive_losses": int(os.environ.get("MAX_CONSECUTIVE_LOSSES", "3")),
    "max_open_or_pending_orders": int(os.environ.get("MAX_OPEN_OR_PENDING_ORDERS", "1")),
}
DASHBOARD_INITIAL_BALANCE = float(os.environ.get("DASHBOARD_INITIAL_BALANCE", "500"))
LIVE_PREFLIGHT_MAX_AGE_SECONDS = int(os.environ.get("DASHBOARD_LIVE_PREFLIGHT_MAX_AGE_SECONDS", "180"))
CLOB_READONLY_MAX_AGE_SECONDS = int(os.environ.get("DASHBOARD_CLOB_READONLY_MAX_AGE_SECONDS", "300"))
CLOB_ACCOUNT_REFRESH_SECONDS = int(os.environ.get("DASHBOARD_CLOB_ACCOUNT_REFRESH_SECONDS", "30"))
CLOB_ACCOUNT_READ_TIMEOUT_SECONDS = float(os.environ.get("DASHBOARD_CLOB_ACCOUNT_READ_TIMEOUT_SECONDS", "8"))
CLOB_ACCOUNT_CACHE = {"snapshot": None, "ts": 0.0, "root": ""}
CLOB_ENV_KEYS = {
    "CLOB_API_KEY",
    "CLOB_API_SECRET",
    "CLOB_API_PASSPHRASE",
    "CLOB_FUNDER_ADDRESS",
    "CLOB_SIGNATURE_TYPE",
    "CLOB_PRIVATE_KEY",
    "PK",
}
BTC_LIVE_MAX_PRICE_AGE_SECONDS = int(os.environ.get("DASHBOARD_BTC_LIVE_MAX_PRICE_AGE_SECONDS", "15"))
BTC_LIVE_MAX_RECEIVED_AGE_SECONDS = int(os.environ.get("DASHBOARD_BTC_LIVE_MAX_RECEIVED_AGE_SECONDS", "30"))
DASHBOARD_TRADING_DAY_TZ_DEFAULT = "Asia/Shanghai"


def _dashboard_cache_seconds(env_name: str, default_seconds: float) -> float:
    try:
        return max(0.0, float(os.environ.get(env_name, str(default_seconds))))
    except (TypeError, ValueError):
        return default_seconds


def _cached_dashboard_payload(cache_key, ttl_seconds: float, factory):
    if ttl_seconds <= 0:
        return factory()
    now = time.time()
    with _DASHBOARD_RESPONSE_CACHE_LOCK:
        cached = _DASHBOARD_RESPONSE_CACHE.get(cache_key)
        if cached and now - cached["ts"] <= ttl_seconds:
            return cached["payload"]
    payload = factory()
    with _DASHBOARD_RESPONSE_CACHE_LOCK:
        _DASHBOARD_RESPONSE_CACHE[cache_key] = {"ts": now, "payload": payload}
    return payload


def _configured_paper_source():
    return (os.environ.get("DASHBOARD_RUN_SOURCE") or os.environ.get("RUN_SOURCE") or "").strip()


def _paper_run_source():
    configured = _configured_paper_source()
    if configured:
        return configured
    if KRONOS_CHECKPOINT_DIR.exists():
        files = []
        for pattern in ("paper_aligned*.json", "paper_live*.json", "paper.json"):
            files.extend(KRONOS_CHECKPOINT_DIR.glob(pattern))
        files = [
            path for path in files
            if not any(marker in path.stem for marker in ("_ledger", "_orders", "_events", ".bad_resume", ".before_"))
        ]
        files = sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)
        if files:
            return files[0].stem
    return "paper_live"


def _source_label(source: str) -> str:
    value = str(source or "").strip()
    if value == "live_real":
        return "Live Real"
    if "dryrun" in value:
        return "Dry-run"
    if value == "shadow_live":
        return "Shadow"
    if value == "paper" or value.startswith("paper"):
        return "Paper"
    if value == "history":
        return "History"
    return value or "Unknown"


def _dashboard_db_source():
    value = (os.environ.get("DASHBOARD_DB_SOURCE") or "").strip()
    allowed = {"paper", "dryrun_aligned_prod_shift1", "shadow_live", "live_real", "history"}
    if value in allowed:
        return value
    return "paper"


def _normalize_dashboard_source(source: str | None):
    value = str(source or "").strip()
    if not value:
        return _dashboard_db_source()
    if value == "live":
        return "paper"
    return value


def _request_dashboard_source():
    return _normalize_dashboard_source(request.args.get("source"))


def _paper_checkpoint_path():
    return KRONOS_CHECKPOINT_DIR / f"{_paper_run_source()}.json"


def _paper_ledger_path():
    return KRONOS_CHECKPOINT_DIR / f"{_paper_run_source()}_ledger.json"


def _live_real_ledger_path():
    configured = (os.environ.get("DASHBOARD_LIVE_REAL_LEDGER") or "").strip()
    if configured:
        path = Path(configured)
        return path if path.is_absolute() else KRONOS_CHECKPOINT_DIR / path
    return KRONOS_CHECKPOINT_DIR / "live_real_orders_current_next.json"


def _polymarket_account_activity_path():
    configured = (os.environ.get("DASHBOARD_POLYMARKET_ACCOUNT_ACTIVITY_REPORT") or "").strip()
    if configured:
        path = Path(configured)
        return path if path.is_absolute() else KRONOS_REPORT_DIR / path
    return KRONOS_REPORT_DIR / "polymarket_account_activity_latest.json"


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


def _live_prediction_artifact_history_path():
    return KRONOS_CHECKPOINT_DIR / "aligned_prod_shift1_predictions.jsonl"


def _live_formal_report_path():
    return KRONOS_REPORT_DIR / "prediction_bound_live_formal_latest.json"


def _live_formal_prediction_history_path():
    return KRONOS_REPORT_DIR / "prediction_bound_live_formal_predictions.jsonl"


STRATEGY_COMPARE_MIN_DAYS = 7
STRATEGY_COMPARE_PREFERRED_DAYS = 14
STRATEGY_COMPARE_MIN_PASSED = 400
STRATEGY_COMPARE_PREFERRED_PASSED = 800
STRATEGY_COMPARISON_SIGNAL_READ_LIMIT = 200000
STRATEGY_COMPARE_CANDIDATES = [
    {
        "candidate_id": "round2_drawdown_density",
        "label": "Round2 Current Baseline",
        "config_path": "data/config/research_candidates/aligned_prod_current_next_chainlink_research_round2_drawdown_density_m049_shares5.json",
    },
    {
        "candidate_id": "official_truth_14d14d_latest",
        "label": "Official 14d/14d",
        "config_path": "data/config/research_candidates/aligned_prod_current_next_official_truth_research_14d14d_latest_m049_shares5.json",
    },
    {
        "candidate_id": "official_truth_7d7d_latest",
        "label": "Official 7d/7d",
        "config_path": "data/config/research_candidates/aligned_prod_current_next_official_truth_research_7d7d_latest_m049_shares5.json",
    },
]


def _candidate_no_submit_official_truth_signals_path():
    configured = (os.environ.get("DASHBOARD_CANDIDATE_NO_SUBMIT_SIGNALS") or "").strip()
    if configured:
        path = Path(configured)
        return path if path.is_absolute() else KRONOS_REPORT_DIR / path
    return KRONOS_REPORT_DIR / "candidate_no_submit_official_truth_signals.jsonl"


def _candidate_no_submit_official_truth_latest_path():
    configured = (os.environ.get("DASHBOARD_CANDIDATE_NO_SUBMIT_LATEST") or "").strip()
    if configured:
        path = Path(configured)
        return path if path.is_absolute() else KRONOS_REPORT_DIR / path
    return KRONOS_REPORT_DIR / "candidate_no_submit_official_truth_latest.json"


def _candidate_no_submit_official_truth_daily_summary_path():
    configured = (os.environ.get("DASHBOARD_CANDIDATE_NO_SUBMIT_DAILY_SUMMARY") or "").strip()
    if configured:
        path = Path(configured)
        return path if path.is_absolute() else KRONOS_REPORT_DIR / path
    return KRONOS_REPORT_DIR / "candidate_no_submit_official_truth_daily_summary.json"


def _candidate_no_submit_official_truth_scored_summary_path():
    configured = (os.environ.get("DASHBOARD_CANDIDATE_NO_SUBMIT_SCORED_SUMMARY") or "").strip()
    if configured:
        path = Path(configured)
        return path if path.is_absolute() else KRONOS_REPORT_DIR / path
    return KRONOS_REPORT_DIR / "candidate_no_submit_official_truth_scored_summary.json"


def _live_formal_report():
    path = _live_formal_report_path()
    return path, _read_json(path) or {}


def _live_formal_prediction_record():
    path, report = _live_formal_report()
    prediction = report.get("prediction") if isinstance(report.get("prediction"), dict) else {}
    if not prediction:
        return None
    record = dict(prediction)
    record.setdefault("source", report.get("source") or "prediction_bound_live_order")
    record.setdefault("type", "decision")
    record.setdefault("artifact_created_at", record.get("created_at") or report.get("created_at"))
    record.setdefault("created_at", record.get("artifact_created_at") or report.get("created_at"))
    record.setdefault("submitted", report.get("submitted"))
    record.setdefault("report_created_at", report.get("created_at"))
    record.setdefault("report_path", str(path))
    if "passed" not in record:
        action = str(record.get("action") or "").upper()
        record["passed"] = action not in {"", "HOLD"} and bool(record.get("would_place_order", True))
    return record


def _prediction_record_key(record: dict):
    return (
        record.get("decision_id")
        or record.get("prediction_artifact_decision_id")
        or "|".join(
            str(record.get(key) or "")
            for key in ("decision_bar_ts", "entry_ts", "settle_ts", "action")
        )
    )


def _prediction_record_dedupe_keys(record: dict):
    keys = set()
    for key in ("decision_id", "prediction_artifact_decision_id"):
        value = record.get(key)
        if value:
            keys.add(str(value))
    window_key = "|".join(
        str(record.get(key) or "")
        for key in ("decision_bar_ts", "entry_ts", "settle_ts", "action")
    )
    if window_key.strip("|"):
        keys.add(window_key)
    return keys


def _iso_from_any(value):
    parsed = _parse_dt(value)
    return _iso_utc(parsed) if parsed is not None else value


def _live_formal_out_log_path():
    preferred = KRONOS_LOG_DIR / "prediction_bound_live_formal_latest.out.log"
    if preferred.exists():
        return preferred
    files = sorted(
        KRONOS_LOG_DIR.glob("prediction_bound_live_formal_*.out.log") if KRONOS_LOG_DIR.exists() else [],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return files[0] if files else preferred


def _text_file_encoding(path: Path):
    try:
        with path.open("rb") as f:
            prefix = f.read(4)
    except OSError:
        return "utf-8-sig"
    if prefix.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    if prefix.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    return "utf-8-sig"


def _live_formal_log_prediction_records(limit=100):
    path = _live_formal_out_log_path()
    if not path.exists():
        return []
    lines = deque(maxlen=max(limit * 4, limit, 20))
    try:
        with path.open("r", encoding=_text_file_encoding(path), errors="replace") as f:
            for line in f:
                if " status=" in line and " action=" in line:
                    lines.append(line.strip())
    except OSError:
        return []

    records = []
    pattern = re.compile(
        r"^(?P<created_at>\S+)\s+status=(?P<status>\S+)\s+"
        r"ts=(?P<ts>.+?)\s+entry=(?P<entry>\S+)\s+settle=(?P<settle>\S+)\s+"
        r"action=(?P<action>\S+)\s+reason_code=(?P<reason_code>\S+)"
    )
    for line in lines:
        match = pattern.search(line)
        if not match:
            continue
        action = match.group("action").upper()
        decision_bar_ts = _iso_from_any(match.group("ts"))
        entry_ts = _iso_from_any(match.group("entry"))
        settle_ts = _iso_from_any(match.group("settle"))
        records.append(
            {
                "source": "prediction_bound_live_formal_log",
                "type": "decision",
                "artifact_created_at": match.group("created_at"),
                "created_at": match.group("created_at"),
                "decision_bar_ts": decision_bar_ts,
                "entry_ts": entry_ts,
                "settle_ts": settle_ts,
                "signal_entry_ts": entry_ts,
                "signal_settle_ts": settle_ts,
                "target_market_entry_ts": entry_ts,
                "target_market_settle_ts": settle_ts,
                "execution_market_shift": "next_period",
                "decision_id": f"formal_live:{decision_bar_ts}:{entry_ts}:{settle_ts}:{action}",
                "action": action,
                "passed": action not in {"", "HOLD"},
                "reason_code": match.group("reason_code"),
                "reason": match.group("reason_code"),
                "status": match.group("status"),
            }
        )
    return records[-limit:]


def _live_formal_history_prediction_records(limit=100):
    return [
        record
        for record in _tail_jsonl(_live_formal_prediction_history_path(), limit=max(limit, 1))
        if isinstance(record, dict)
    ]


def _prediction_record_match_key(record: dict):
    return "|".join(
        str(record.get(key) or "")
        for key in ("decision_bar_ts", "entry_ts", "settle_ts", "action")
    )


def _live_ledger_rows():
    payload = _read_json(_live_real_ledger_path())
    return [row for row in (payload if isinstance(payload, list) else []) if isinstance(row, dict)]


def _live_ledger_prediction_records():
    records = []
    for row in _live_ledger_rows():
        action = str(row.get("action") or "").upper()
        if not action or action == "HOLD":
            continue
        record = {
            "source": "live_real_order_ledger",
            "type": "decision",
            "created_at": row.get("created_at"),
            "artifact_created_at": row.get("created_at"),
            "decision_id": row.get("order_key") or row.get("signal_id") or row.get("order_id"),
            "order_id": row.get("order_id"),
            "order_key": row.get("order_key") or row.get("signal_id"),
            "action": action,
            "direction": row.get("direction"),
            "side": row.get("side"),
            "market_slug": row.get("market_slug"),
            "market_id": row.get("market_id"),
            "decision_bar_ts": row.get("decision_bar_ts"),
            "entry_ts": row.get("entry_ts"),
            "settle_ts": row.get("settle_ts"),
            "target_market_entry_ts": row.get("entry_ts"),
            "target_market_settle_ts": row.get("settle_ts"),
            "execution_market_shift": "next_period",
            "passed": True,
            "would_place_order": True,
            "submitted": True,
            "status": row.get("status"),
            "price": row.get("price"),
            "size": row.get("size"),
            "filled_size": row.get("filled_size"),
            "remaining_size": row.get("remaining_size"),
            "reference_price": row.get("reference_price"),
            "reference_price_source": row.get("reference_price_source"),
            "p5_up": row.get("p5_up"),
            "p1_up": row.get("p1_up"),
            "p4_up": row.get("p4_up"),
            "reason_code": row.get("reason_code"),
            "reason": row.get("reason_code") or row.get("status"),
        }
        records.append(record)
    return records


def _order_chain_key_values(row: dict):
    values = [
        row.get("signal_id"),
        row.get("order_key"),
        row.get("decision_id"),
        row.get("prediction_artifact_decision_id"),
        _prediction_record_match_key(row),
    ]
    return [str(value) for value in values if value]


def _order_chain_item(row: dict):
    keys = (
        "order_id",
        "repost_parent_order_id",
        "status",
        "execution_result",
        "market_slug",
        "market_id",
        "direction",
        "side",
        "price",
        "price_tier",
        "size",
        "filled_size",
        "remaining_size",
        "created_at",
        "updated_at",
        "entry_ts",
        "settle_ts",
        "signal_id",
        "order_key",
    )
    return {key: row.get(key) for key in keys if row.get(key) not in (None, "")}


def _order_chain_sort_value(row: dict):
    return (
        _parse_dt(row.get("created_at"))
        or _parse_dt(row.get("updated_at"))
        or _parse_dt(row.get("entry_ts"))
        or datetime.min.replace(tzinfo=timezone.utc)
    )


def _best_execution_order(chain: list[dict]):
    filled = [
        order
        for order in chain
        if _num(order.get("filled_size")) and _num(order.get("filled_size")) > 0
    ]
    candidates = filled or [
        order
        for order in chain
        if str(order.get("status") or "").upper() in {"FILLED", "SETTLED", "OPEN", "SUBMITTED"}
    ] or chain
    if not candidates:
        return None
    return sorted(candidates, key=_order_chain_sort_value)[-1]


def _dedupe_order_chain_rows(rows: list[dict]):
    deduped = []
    seen = set()
    for row in rows:
        key = (
            row.get("order_id"),
            row.get("created_at"),
            row.get("price_tier"),
            row.get("price"),
            row.get("status"),
        )
        fallback_key = tuple(sorted((str(k), str(v)) for k, v in row.items()))
        marker = key if any(value not in (None, "") for value in key) else fallback_key
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(row)
    return deduped


def _enrich_prediction_records_from_live_ledger(records: list[dict]):
    ledger_records = _live_ledger_prediction_records()
    ledger_rows = _live_ledger_rows()
    if not ledger_records and not ledger_rows:
        return records
    by_key = {}
    for row in ledger_records:
        for key in (
            row.get("decision_id"),
            row.get("order_key"),
            _prediction_record_match_key(row),
        ):
            if key:
                by_key[str(key)] = row
    enriched = []
    copy_keys = (
        "order_id",
        "order_key",
        "direction",
        "side",
        "market_slug",
        "market_id",
        "price",
        "size",
        "filled_size",
        "remaining_size",
        "reference_price",
        "reference_price_source",
        "p5_up",
        "p1_up",
        "p4_up",
        "status",
        "submitted",
        "would_place_order",
    )
    chains_by_key: dict[str, list[dict]] = {}
    for row in ledger_rows:
        for key in _order_chain_key_values(row):
            chains_by_key.setdefault(key, []).append(row)
    for record in records:
        match = None
        for key in (
            record.get("decision_id"),
            record.get("order_key"),
            _prediction_record_match_key(record),
        ):
            if key and str(key) in by_key:
                match = by_key[str(key)]
                break
        if match:
            record = dict(record)
            for key in copy_keys:
                if record.get(key) in (None, "") and match.get(key) not in (None, ""):
                    record[key] = match.get(key)
            if record.get("reason") in (None, "", "no_side_passed") and match.get("reason"):
                record["reason"] = match.get("reason")
            if record.get("reason_code") in (None, "") and match.get("reason_code"):
                record["reason_code"] = match.get("reason_code")
            if record.get("passed") is not True:
                record["passed"] = bool(match.get("passed"))
        chain_rows = []
        for key in (
            record.get("decision_id"),
            record.get("order_key"),
            record.get("signal_id"),
            _prediction_record_match_key(record),
        ):
            if key and str(key) in chains_by_key:
                chain_rows = chains_by_key[str(key)]
                break
        if chain_rows:
            record = dict(record)
            chain = [
                _order_chain_item(row)
                for row in sorted(_dedupe_order_chain_rows(chain_rows), key=_order_chain_sort_value)
            ]
            execution_order = _best_execution_order(chain)
            record["order_chain"] = chain
            record["order_chain_count"] = len(chain)
            if record.get("max_price") in (None, "") and record.get("price") not in (None, ""):
                record["max_price"] = record.get("price")
            if execution_order:
                record["execution_order_id"] = execution_order.get("order_id")
                record["execution_status"] = execution_order.get("status")
                record["execution_price"] = execution_order.get("price")
                record["execution_price_tier"] = execution_order.get("price_tier")
                record["execution_filled_size"] = execution_order.get("filled_size")
                record["execution_remaining_size"] = execution_order.get("remaining_size")
        enriched.append(record)
    return enriched


def _artifact_created_at(record: dict):
    for key in ("artifact_created_at", "created_at", "ts", "decision_bar_ts", "entry_ts"):
        value = record.get(key)
        if _parse_dt(value) is not None:
            return value
    return record.get("decision_bar_ts") or record.get("ts") or record.get("created_at")


def _prob_direction(value):
    try:
        probability = float(value)
    except (TypeError, ValueError):
        return ""
    if probability > 0.5:
        return "UP"
    if probability < 0.5:
        return "DOWN"
    return "FLAT"


def _live_prediction_artifact_passed(record: dict):
    if record.get("passed") is True or record.get("filt") is True or record.get("filt_passed") in (1, True):
        return True
    action = str(record.get("action") or "").upper()
    return record.get("executable") is True and action != "HOLD"


def _prediction_artifact_event(record: dict, sequence: int):
    action = str(record.get("action") or "HOLD").upper()
    created_at = _artifact_created_at(record)
    reason = record.get("reason") or record.get("reason_code") or record.get("status_reason") or ""
    details = dict(record)
    details.setdefault("type", "decision")
    details.setdefault("ts", record.get("decision_bar_ts") or created_at)
    return {
        "id": sequence,
        "source": "live_real",
        "source_label": _source_label("live_real"),
        "type": "decision",
        "kline_n": sequence,
        "action": action,
        "dir5": _prob_direction(record.get("p5_up")),
        "dir4": _prob_direction(record.get("p4_up")),
        "regime": record.get("reason_code") or record.get("strategy") or "",
        "filt_passed": 1 if _live_prediction_artifact_passed(record) else 0,
        "reason": reason,
        "created_at": created_at,
        "details": json.dumps(details, ensure_ascii=False),
    }


def _live_prediction_artifact_events(limit=100, since=""):
    records = []
    formal_record = _live_formal_prediction_record()
    if isinstance(formal_record, dict):
        records.append(formal_record)
    formal_history_records = _live_formal_history_prediction_records(limit=max(limit, 1))
    formal_log_records = _live_formal_log_prediction_records(limit=max(limit, 1))
    history_records = []
    if not records and not formal_history_records and not formal_log_records:
        history_records = [
            record
            for record in _tail_jsonl(_live_prediction_artifact_history_path(), limit=max(limit, 1))
            if isinstance(record, dict)
        ]
    seen = set()
    deduped = []
    for record in [*records, *formal_history_records, *formal_log_records, *history_records]:
        keys = _prediction_record_dedupe_keys(record)
        if keys and keys.intersection(seen):
            continue
        seen.update(keys)
        deduped.append(record)
    records = _enrich_prediction_records_from_live_ledger(deduped)
    events = [_prediction_artifact_event(record, index + 1) for index, record in enumerate(records)]
    if since:
        since_ts = _parse_dt(since)
        if since_ts is not None:
            events = [
                event
                for event in events
                if (_parse_dt(event.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc)) >= since_ts
            ]
    events.sort(
        key=lambda event: _parse_dt(event.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return events[:limit]


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


def _live_alert_summary():
    path = KRONOS_REPORT_DIR / "live_alerts_latest.json"
    report = _read_json(path) or {}
    active = report.get("active") if isinstance(report.get("active"), list) else []
    report_mtime, report_age_seconds = _path_age(path if path.exists() else None)
    return {
        "available": path.exists(),
        "report": str(path),
        "report_mtime": report_mtime,
        "report_age_seconds": report_age_seconds,
        "active_count": int(report.get("active_count", len(active)) or 0),
        "selected_count": int(report.get("selected_count", 0) or 0),
        "critical_count": sum(1 for item in active if item.get("severity") == "critical"),
        "warning_count": sum(1 for item in active if item.get("severity") == "warning"),
        "active": active[-12:],
    }


def _live_dryrun_ledger_summary():
    source = _paper_run_source()
    candidates = [
        KRONOS_CHECKPOINT_DIR / f"{source}_ledger.json",
        KRONOS_CHECKPOINT_DIR / "live_dryrun_aligned_prod_shift1_ledger.json",
    ]
    path = next((candidate for candidate in candidates if candidate.exists()), candidates[0])
    payload = _read_json(path)
    rows = payload if isinstance(payload, list) else []
    submitted = [row for row in rows if isinstance(row, dict) and bool(row.get("submitted"))]
    would_place = [
        row
        for row in rows
        if isinstance(row, dict) and row.get("would_place_order") is True
    ]
    blocked = [
        row
        for row in rows
        if isinstance(row, dict)
        and (
            str(row.get("status", "")).lower() == "blocked"
            or bool(row.get("block_reason"))
        )
    ]
    return {
        "ledger": str(path),
        "ledger_count": len(rows),
        "would_place_count": len(would_place),
        "blocked_count": len(blocked),
        "submitted_count": len(submitted),
        "today": _live_dryrun_today_summary(rows),
        "latest": rows[-1] if rows else None,
    }


def _dryrun_record_ts(record):
    for key in ("created_at", "decision_ts", "entry_ts", "settle_ts", "ts"):
        parsed = _parse_dt(record.get(key))
        if parsed is not None:
            return parsed
    return None


def _is_blocked_dryrun_record(record):
    return str(record.get("status", "")).lower() == "blocked" or bool(record.get("block_reason"))


def _iso_utc(ts):
    if ts is None:
        return None
    return ts.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _timestamp_age_seconds(ts, now_dt=None):
    if ts is None:
        return None
    now_dt = now_dt or datetime.now(timezone.utc)
    return max(0, int((now_dt - ts.astimezone(timezone.utc)).total_seconds()))


def _latest_timestamp_summary(timestamps, now_dt=None):
    valid = [ts.astimezone(timezone.utc) for ts in timestamps if ts is not None]
    latest = max(valid) if valid else None
    return _iso_utc(latest), _timestamp_age_seconds(latest, now_dt)


def _live_dryrun_today_summary(rows=None, *, now=None):
    rows = [row for row in (rows or []) if isinstance(row, dict)]
    day_info = _dashboard_day_info(now=now)
    today = day_info["day"]
    todays = []
    for index, row in enumerate(rows):
        ts = _dryrun_record_ts(row)
        if _timestamp_in_dashboard_day(ts, today):
            todays.append((index, row, ts))
    todays_sorted = sorted(todays, key=lambda item: (item[2], item[0]))
    today_rows = [row for _, row, _ in todays_sorted]
    blocked_rows = [row for row in today_rows if _is_blocked_dryrun_record(row)]
    latest = today_rows[-1] if today_rows else {}
    latest_ts = todays_sorted[-1][2] if todays_sorted else None
    latest_blocked = blocked_rows[-1] if blocked_rows else {}
    latest_at, latest_age_seconds = _latest_timestamp_summary([latest_ts])
    return {
        "records": len(today_rows),
        "would_place": sum(1 for row in today_rows if row.get("would_place_order") is True),
        "blocked": len(blocked_rows),
        "submitted": sum(1 for row in today_rows if bool(row.get("submitted"))),
        "latest_status": str(latest.get("status") or ""),
        "latest_action": str(latest.get("action") or ""),
        "latest_block_reason": str(latest_blocked.get("block_reason") or latest_blocked.get("reason") or ""),
        "latest_at": latest_at,
        "latest_age_seconds": latest_age_seconds,
    }


def _btc_live_market_data_summary():
    now_seconds = float(_btc_live_now())
    now_dt = datetime.fromtimestamp(now_seconds, tz=timezone.utc)
    with BTC_LIVE_LOCK:
        cached = dict(BTC_LIVE_CACHE)
    try:
        price = float(cached.get("price"))
    except (TypeError, ValueError):
        price = None
    timestamp = str(cached.get("timestamp") or "")
    price_ts = _parse_dt(timestamp)
    price_age_seconds = None
    if price_ts is not None:
        price_age_seconds = max(0, int((now_dt - price_ts).total_seconds()))
    received_age_seconds = None
    try:
        received_at = float(cached.get("received_at"))
        received_age_seconds = max(0, int(now_seconds - received_at))
    except (TypeError, ValueError):
        received_at = None
    available = price is not None and price_ts is not None
    price_fresh = price_age_seconds is not None and price_age_seconds <= BTC_LIVE_MAX_PRICE_AGE_SECONDS
    received_fresh = received_age_seconds is not None and received_age_seconds <= BTC_LIVE_MAX_RECEIVED_AGE_SECONDS
    ready = available and price_fresh and received_fresh and not cached.get("error")
    if ready:
        status = "fresh"
        next_action = "Market data fresh"
    elif available:
        status = "stale"
        next_action = "Refresh Chainlink live price feed"
    else:
        status = str(cached.get("status") or "warming_up")
        next_action = "Wait for Chainlink live price"
    return {
        "ready": ready,
        "price": price,
        "timestamp": timestamp,
        "source": str(cached.get("source") or "polymarket_rtds_chainlink"),
        "status": status,
        "price_age_seconds": price_age_seconds,
        "received_at": received_at,
        "received_age_seconds": received_age_seconds,
        "max_price_age_seconds": BTC_LIVE_MAX_PRICE_AGE_SECONDS,
        "max_received_age_seconds": BTC_LIVE_MAX_RECEIVED_AGE_SECONDS,
        "next_action": next_action,
        "error": cached.get("error"),
    }


def _live_gate_report_summary(path=None, report=None):
    if report is None:
        path, report = _latest_json_report("live_trade_gate*.json")
    report = report if isinstance(report, dict) else {}
    probes = report.get("market_probes") if isinstance(report.get("market_probes"), list) else []
    blockers = report.get("blockers") if isinstance(report.get("blockers"), list) else []
    funding_requirements = (
        report.get("funding_requirements")
        if isinstance(report.get("funding_requirements"), dict)
        else {}
    )
    return {
        "report": str(path) if path else "",
        "available": bool(report),
        "ok": bool(report.get("ok")),
        "ready_for_live_smoke": bool(report.get("ready_for_live_smoke")),
        "blockers": blockers,
        "market_probe_count": len(probes),
        "quote_executable_count": sum(1 for probe in probes if probe.get("quote_executable")),
        "account": report.get("account") if isinstance(report.get("account"), dict) else {},
        "risk": report.get("risk") if isinstance(report.get("risk"), dict) else {},
        "funding_requirements": funding_requirements,
    }


def _live_preflight_chain_summary(path=None, report=None):
    if report is None:
        path, report = _latest_json_report("live_preflight_chain*.json")
    report = report if isinstance(report, dict) else {}
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    blockers = report.get("blockers") if isinstance(report.get("blockers"), list) else []
    components = report.get("components") if isinstance(report.get("components"), dict) else {}
    created_at = str(report.get("created_at") or "")
    created_ts = _parse_dt(created_at)
    age_seconds = None
    if created_ts is not None:
        age_seconds = max(0, int((datetime.now(timezone.utc) - created_ts).total_seconds()))
    fresh = bool(report) and age_seconds is not None and age_seconds <= LIVE_PREFLIGHT_MAX_AGE_SECONDS
    return {
        "report": str(path) if path else "",
        "available": bool(report),
        "ok": bool(report.get("ok")),
        "submitted": bool(report.get("submitted")),
        "created_at": created_at,
        "age_seconds": age_seconds,
        "fresh": fresh,
        "max_age_seconds": LIVE_PREFLIGHT_MAX_AGE_SECONDS,
        "blockers": blockers,
        "gate_ready": bool(summary.get("gate_ready")),
        "smoke_mode": str(summary.get("smoke_mode") or ""),
        "open_orders": int(summary.get("open_orders", 0) or 0),
        "settled": int(summary.get("settled", 0) or 0),
        "risk_ok": bool(summary.get("risk_ok")),
        "components": components,
    }


def _path_age(path):
    if not path:
        return None, None
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None, None
    return mtime.isoformat(), max(0, int((datetime.now(timezone.utc) - mtime).total_seconds()))


def _kronos_root():
    configured = (os.environ.get("KRONOS_ROOT") or "").strip()
    if configured:
        return Path(configured)
    return KRONOS_CHECKPOINT_DIR.parent.parent


def _load_env_file(path, override_keys=None):
    override_keys = set(override_keys or [])
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            cleaned = value.strip().strip('"').strip("'")
            if key in override_keys:
                os.environ[key] = cleaned
            else:
                os.environ.setdefault(key, cleaned)


def _funding_shortfall(required, observed):
    required_value = _num(required)
    observed_value = _num(observed)
    if required_value is None or observed_value is None:
        return None
    return round(max(0.0, required_value - observed_value), 6)


def _payload_dict(raw):
    if isinstance(raw, dict):
        return raw
    if hasattr(raw, "__dict__"):
        return vars(raw)
    return {}


def _payload_first(payload, *keys, default=None):
    for key in keys:
        if isinstance(payload, dict) and key in payload and payload.get(key) not in (None, ""):
            value = payload.get(key)
            return getattr(value, "value", value)
    return default


def _clob_open_order_summary(raw):
    payload = _payload_dict(raw)
    order_id = str(_payload_first(payload, "order_id", "orderId", "id", "hash", default="") or "")
    market = str(_payload_first(payload, "market", "market_id", "marketId", default="") or "")
    token_id = str(_payload_first(payload, "asset_id", "assetId", "token_id", "tokenId", default="") or "")
    original_size = _num(_payload_first(payload, "original_size", "originalSize", "size"))
    matched_size = _num(
        _payload_first(
            payload,
            "size_matched",
            "sizeMatched",
            "matched_size",
            "matchedSize",
            "filled_size",
            "filledSize",
        )
    )
    if matched_size is None:
        matched_size = 0.0
    remaining_size = None
    if original_size is not None:
        remaining_size = round(max(0.0, original_size - matched_size), 12)
    return {
        "order_id": order_id,
        "market": market,
        "asset_id": token_id,
        "token_id": token_id,
        "side": str(_payload_first(payload, "side", default="") or "").upper(),
        "outcome": str(_payload_first(payload, "outcome", default="") or ""),
        "price": _num(_payload_first(payload, "price")),
        "original_size": original_size,
        "matched_size": matched_size,
        "remaining_size": remaining_size,
        "status": str(_payload_first(payload, "status", "raw_status", "rawStatus", default="") or ""),
        "created_at": str(_payload_first(payload, "created_at", "createdAt", "created", default="") or ""),
        "expiration": str(_payload_first(payload, "expiration", "expiration_time", "expirationTime", default="") or ""),
    }


def _clob_open_order_summaries(raw_orders, limit=20):
    if not isinstance(raw_orders, list):
        return []
    return [_clob_open_order_summary(order) for order in raw_orders[:limit]]


async def _read_live_clob_account(root):
    from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams
    from kronos_poly.execution.clob import PolymarketClobClient, collateral_units_to_usdc

    client = PolymarketClobClient()
    await client.connect()
    try:
        authenticated = bool(getattr(client, "_authenticated", False))
        open_orders = []
        orders_read_ok = False
        orders_error = ""
        balance_read_ok = False
        allowance_read_ok = False
        balance_error = ""
        allowance_error = ""
        open_orders_result_type = ""
        balance_allowance_result_type = ""
        balance = 0.0
        parsed_allowances = {}
        min_allowance = 0.0
        min_allowance_spender = ""

        if authenticated:
            try:
                open_orders = await asyncio.to_thread(client._inner.get_open_orders, params=None) or []  # noqa: SLF001
                open_orders_result_type = type(open_orders).__name__
                orders_read_ok = True
            except Exception as exc:
                orders_error = f"{type(exc).__name__}: {exc}"

            try:
                raw_balance_allowance = await asyncio.to_thread(
                    client._inner.get_balance_allowance,  # noqa: SLF001
                    params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL),
                )
                balance_allowance_result_type = type(raw_balance_allowance).__name__
                if isinstance(raw_balance_allowance, dict):
                    balance = collateral_units_to_usdc(raw_balance_allowance.get("balance", 0.0))
                    allowances = raw_balance_allowance.get("allowances", {})
                    if isinstance(allowances, dict):
                        parsed_allowances = {
                            str(spender): collateral_units_to_usdc(value)
                            for spender, value in allowances.items()
                            if value is not None
                        }
                    allowance_values = list(parsed_allowances.values())
                    min_allowance = min(allowance_values) if allowance_values else 0.0
                    if parsed_allowances:
                        min_allowance_spender = min(parsed_allowances, key=parsed_allowances.get)
                    balance_read_ok = True
                    allowance_read_ok = bool(allowance_values)
            except Exception as exc:
                balance_error = f"{type(exc).__name__}: {exc}"
                allowance_error = balance_error
    finally:
        if hasattr(client, "_connected"):
            client._connected = False  # noqa: SLF001

    account = {
        "authenticated": authenticated,
        "orders_read_ok": orders_read_ok,
        "orders_error": orders_error,
        "orders_result_type": open_orders_result_type,
        "balance_read_ok": balance_read_ok,
        "balance_error": balance_error,
        "balance_allowance_result_type": balance_allowance_result_type,
        "allowance_read_ok": allowance_read_ok,
        "allowance_error": allowance_error,
        "open_orders_count": len(open_orders),
        "open_orders": _clob_open_order_summaries(open_orders),
        "usdc_balance": balance,
        "min_allowance": min_allowance,
        "allowances": parsed_allowances,
        "allowance_count": len(parsed_allowances),
        "min_allowance_spender": min_allowance_spender,
    }
    return {
        "ok": bool(authenticated and (orders_read_ok or balance_read_ok or allowance_read_ok)),
        "source": "live_clob_account_refresh",
        "root": str(root),
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "account": account,
    }


def _live_clob_account_snapshot():
    root = _kronos_root().resolve()
    now = time.time()
    cached = CLOB_ACCOUNT_CACHE.get("snapshot")
    if (
        isinstance(cached, dict)
        and CLOB_ACCOUNT_CACHE.get("root") == str(root)
        and now - float(CLOB_ACCOUNT_CACHE.get("ts") or 0.0) <= CLOB_ACCOUNT_REFRESH_SECONDS
    ):
        return cached

    client_module = root / "src" / "kronos_poly" / "execution" / "clob.py"
    if not client_module.exists():
        return {
            "ok": False,
            "source": "live_clob_account_refresh",
            "root": str(root),
            "error": "kronos clob client not found",
            "account": {},
        }

    _load_env_file(root / ".env.clob.local", override_keys=CLOB_ENV_KEYS)
    _load_env_file(root / ".env")
    for candidate in (root, root / "src"):
        value = str(candidate)
        if value not in sys.path:
            sys.path.insert(0, value)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        running_loop = False
    else:
        running_loop = True
    if running_loop:
        return {
            "ok": False,
            "source": "live_clob_account_refresh",
            "root": str(root),
            "error": "cannot refresh CLOB account from a running event loop",
            "account": {},
        }

    try:
        snapshot = asyncio.run(
            asyncio.wait_for(
                _read_live_clob_account(root),
                timeout=CLOB_ACCOUNT_READ_TIMEOUT_SECONDS,
            )
        )
    except Exception as exc:
        snapshot = {
            "ok": False,
            "source": "live_clob_account_refresh",
            "root": str(root),
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "error": f"{type(exc).__name__}: {exc}",
            "account": {},
        }

    if snapshot.get("ok"):
        CLOB_ACCOUNT_CACHE.update({"snapshot": snapshot, "ts": now, "root": str(root)})
    return snapshot


def _process_summary(patterns):
    patterns = [str(pattern) for pattern in patterns if str(pattern or "").strip()]
    if not patterns:
        return {"running": False, "matches": [], "started_at": None, "uptime_seconds": None, "patterns": []}
    if os.name != "nt":
        return {"running": False, "matches": [], "started_at": None, "uptime_seconds": None, "patterns": patterns}
    patterns_json = json.dumps(patterns)
    command = f"""
$patterns = ConvertFrom-Json @'
{patterns_json}
'@
Get-CimInstance Win32_Process |
  Where-Object {{
    $cmd = [string]$_.CommandLine
    ($_.ProcessId -ne $PID) -and
    ($_.Name -match '^(python|python\\.exe|py|py\\.exe|powershell|powershell\\.exe|pwsh|pwsh\\.exe)$') -and
    (($patterns | Where-Object {{ $cmd -like "*$_*" }} | Select-Object -First 1) -ne $null)
  }} |
  Select-Object ProcessId,Name,
    @{{n='StartedAt';e={{$_.CreationDate.ToUniversalTime().ToString('o')}}}},
    @{{n='CommandLine';e={{$_.CommandLine}}}} |
  ConvertTo-Json -Depth 4
"""
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except Exception as exc:
        return {
            "running": False,
            "matches": [],
            "started_at": None,
            "uptime_seconds": None,
            "patterns": patterns,
            "error": str(exc),
        }
    if completed.returncode != 0 or not completed.stdout.strip():
        return {"running": False, "matches": [], "started_at": None, "uptime_seconds": None, "patterns": patterns}
    try:
        parsed = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {"running": False, "matches": [], "started_at": None, "uptime_seconds": None, "patterns": patterns}
    rows = parsed if isinstance(parsed, list) else [parsed]
    matches = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        started_at = row.get("StartedAt")
        command_line = str(row.get("CommandLine") or "")
        matches.append(
            {
                "pid": row.get("ProcessId"),
                "name": row.get("Name"),
                "started_at": started_at,
                "command": command_line[:260],
                "command_full": command_line,
            }
        )
    started_values = [_parse_dt(item.get("started_at")) for item in matches]
    started_values = [value for value in started_values if value is not None]
    started = min(started_values) if started_values else None
    return {
        "running": bool(matches),
        "matches": matches,
        "started_at": _iso_utc(started),
        "uptime_seconds": _timestamp_age_seconds(started),
        "patterns": patterns,
    }


def _report_refresh_item(
    *,
    key,
    label,
    report,
    available,
    ok,
    fresh,
    age_seconds,
    max_age_seconds,
    blockers=None,
    actions=None,
):
    blockers = blockers if isinstance(blockers, list) else []
    if not available:
        status = "missing"
    elif not fresh:
        status = "stale"
    elif ok:
        status = "ready"
    else:
        status = "blocked"
    actions = actions or {}
    return {
        "key": key,
        "label": label,
        "report": report,
        "available": bool(available),
        "ok": bool(ok),
        "fresh": bool(fresh),
        "age_seconds": age_seconds,
        "max_age_seconds": max_age_seconds,
        "status": status,
        "next_action": actions.get(status, "Review report"),
        "blockers": blockers,
    }


def _report_refresh_summary(items):
    return {
        "ready": bool(items) and all(item.get("status") == "ready" for item in items),
        "total": len(items),
        "missing_count": sum(1 for item in items if item.get("status") == "missing"),
        "stale_count": sum(1 for item in items if item.get("status") == "stale"),
        "blocked_count": sum(1 for item in items if item.get("status") == "blocked"),
        "items": items,
    }


def _check_status(report: dict, name: str):
    for check in report.get("checks", []) or []:
        if check.get("name") == name:
            return check
    return None


def _dashboard_trading_day_timezone():
    configured = (
        os.environ.get("DASHBOARD_TRADING_DAY_TZ")
        or os.environ.get("DASHBOARD_DAY_TZ")
        or DASHBOARD_TRADING_DAY_TZ_DEFAULT
    ).strip()
    normalized = configured.lower()
    if normalized in {"asia/shanghai", "shanghai", "utc+8", "utc+08:00", "+08:00", "cst"}:
        return timezone(timedelta(hours=8), "Asia/Shanghai"), "Asia/Shanghai"
    if normalized in {"utc", "etc/utc", "z"}:
        return timezone.utc, "UTC"
    try:
        return ZoneInfo(configured), configured
    except ZoneInfoNotFoundError:
        return timezone.utc, "UTC"


def _ensure_aware_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _dashboard_day_info(*, now=None, day=None):
    tz, label = _dashboard_trading_day_timezone()
    if isinstance(day, str):
        day_value = date.fromisoformat(day)
    elif isinstance(day, date):
        day_value = day
    else:
        now_dt = _ensure_aware_utc(now) or datetime.now(timezone.utc)
        day_value = now_dt.astimezone(tz).date()
    start_local = datetime.combine(day_value, datetime_time.min, tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_local.astimezone(timezone.utc)
    return {
        "day": day_value,
        "day_iso": day_value.isoformat(),
        "day_tz": label,
        "start_utc": start_utc,
        "end_utc": end_utc,
        "start_utc_iso": _iso_utc(start_utc),
        "end_utc_iso": _iso_utc(end_utc),
    }


def _dashboard_day_key(ts):
    if ts is None:
        return None
    tz, _ = _dashboard_trading_day_timezone()
    return _ensure_aware_utc(ts).astimezone(tz).date()


def _timestamp_in_dashboard_day(ts, day):
    return ts is not None and _dashboard_day_key(ts) == day


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


def _record_won(record: dict):
    value = record.get("won")
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "win", "won"}:
            return True
        if normalized in {"0", "false", "loss", "lost"}:
            return False
    pnl = _record_pnl(record)
    if pnl > 0:
        return True
    if pnl < 0:
        return False
    return None


def _is_settled_record(record: dict):
    status = str(record.get("status", "")).upper()
    if status in {"SETTLED", "FILLED", "WON", "LOST", "CLOSED"}:
        return True
    return any(key in record for key in ("pnl", "realized_pnl", "pnl_usdc"))


def _settled_stats_summary(records):
    settled = [record for record in (records or []) if isinstance(record, dict) and _is_settled_record(record)]
    wins = sum(1 for record in settled if _record_won(record) is True)
    losses = sum(1 for record in settled if _record_won(record) is False)
    return {
        "settled": len(settled),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / len(settled), 4) if settled else 0.0,
    }


def _market_result_stats_summary(records):
    resolved = [
        record
        for record in (records or [])
        if isinstance(record, dict)
        and record.get("actual_up_chainlink") is not None
        and record.get("signal_would_have_won") is not None
    ]
    wins = sum(1 for record in resolved if record.get("signal_would_have_won") is True)
    losses = sum(1 for record in resolved if record.get("signal_would_have_won") is False)
    return {
        "resolved": len(resolved),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / len(resolved), 4) if resolved else 0.0,
    }


def _is_equity_settled_record(record: dict):
    status = str(record.get("status", "")).upper()
    if status in {"CANCELLED", "CANCELED", "REJECTED", "OPEN", "PENDING", "SUBMITTED", "PARTIAL", "LIVE"}:
        return False
    return _is_settled_record(record)


def _equity_summary_from_records(records, *, current_balance=None, prefer_balance_after=False, source=""):
    settled = sorted(
        [record for record in (records or []) if isinstance(record, dict) and _is_equity_settled_record(record)],
        key=lambda item: _record_ts(item) or datetime.min.replace(tzinfo=timezone.utc),
    )
    total_pnl = sum(_record_pnl(record) for record in settled)
    current = _num(current_balance)
    if prefer_balance_after and settled:
        first_balance_after = _num(settled[0].get("balance_after"))
        if first_balance_after is not None:
            start = first_balance_after - _record_pnl(settled[0])
        elif current is not None:
            start = current - total_pnl
        else:
            start = DASHBOARD_INITIAL_BALANCE
    elif current is not None:
        start = current - total_pnl
    else:
        start = DASHBOARD_INITIAL_BALANCE

    points = [round(float(start), 8)]
    for record in settled:
        balance_after = _num(record.get("balance_after")) if prefer_balance_after else None
        if balance_after is None:
            balance_after = points[-1] + _record_pnl(record)
        points.append(round(float(balance_after), 8))

    return {
        "points": points,
        "start": points[0],
        "current": points[-1],
        "pnl_usdc": round(total_pnl, 8),
        "settled": len(settled),
        "source": source,
    }


def _weekly_pnl_calendar_from_records(records, *, now=None):
    current = now if isinstance(now, datetime) else datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    day_info = _dashboard_day_info(now=current)
    today = day_info["day"]
    start = today - timedelta(days=6)
    days = {}
    for offset in range(7):
        day = start + timedelta(days=offset)
        days[day.isoformat()] = {
            "date": day.isoformat(),
            "pnl_usdc": 0.0,
            "settled": 0,
            "wins": 0,
            "losses": 0,
        }

    for record in records or []:
        if not isinstance(record, dict) or not _is_equity_settled_record(record):
            continue
        ts = _record_ts(record)
        if ts is None:
            continue
        day = _dashboard_day_key(ts)
        key = day.isoformat()
        if key not in days:
            continue
        item = days[key]
        item["pnl_usdc"] = round(float(item["pnl_usdc"]) + _record_pnl(record), 8)
        item["settled"] += 1
        won = _record_won(record)
        if won is True:
            item["wins"] += 1
        elif won is False:
            item["losses"] += 1

    ordered_days = list(days.values())
    total_pnl = round(sum(float(day["pnl_usdc"]) for day in ordered_days), 8)
    settled = sum(int(day["settled"]) for day in ordered_days)
    wins = sum(int(day["wins"]) for day in ordered_days)
    losses = sum(int(day["losses"]) for day in ordered_days)
    return {
        "start_date": start.isoformat(),
        "end_date": today.isoformat(),
        "days": ordered_days,
        "total_pnl_usdc": total_pnl,
        "settled": settled,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / settled, 4) if settled else 0.0,
        "day_tz": day_info["day_tz"],
        "source": "settled_ledger_trading_day",
    }


def _calendar_month(value):
    value = str(value)
    if re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", value) is None:
        raise ValueError("month must use YYYY-MM")
    try:
        parsed = datetime.strptime(value, "%Y-%m")
    except (TypeError, ValueError) as exc:
        raise ValueError("month must use YYYY-MM") from exc
    return parsed.year, parsed.month


def _calendar_settled_records(records):
    return [
        record
        for record in (records or [])
        if isinstance(record, dict)
        and _is_equity_settled_record(record)
        and _record_ts(record) is not None
    ]


def _calendar_ledger_path(source):
    if source == "live_real":
        return _live_real_ledger_path()
    if source == "paper_monitor":
        return _paper_ledger_path()
    raise ValueError("unsupported source")


def _monthly_pnl_calendar_from_records(records, *, month_value):
    year, month = _calendar_month(month_value)
    day_tz = _dashboard_day_info(day=date(year, month, 1))["day_tz"]
    day_count = monthrange(year, month)[1]
    days = {
        date(year, month, number).isoformat(): {
            "date": date(year, month, number).isoformat(),
            "pnl_usdc": 0.0,
            "settled": 0,
            "wins": 0,
            "losses": 0,
        }
        for number in range(1, day_count + 1)
    }
    settled_records = _calendar_settled_records(records)
    available_months = sorted({
        _dashboard_day_key(_record_ts(record)).strftime("%Y-%m")
        for record in settled_records
    })
    total_pnl_raw = 0.0
    for record in settled_records:
        day_key = _dashboard_day_key(_record_ts(record)).isoformat()
        if day_key not in days:
            continue
        item = days[day_key]
        pnl = _record_pnl(record)
        item["pnl_usdc"] += pnl
        total_pnl_raw += pnl
        item["settled"] += 1
        won = _record_won(record)
        item["wins"] += int(won is True)
        item["losses"] += int(won is False)
    for item in days.values():
        item["pnl_usdc"] = round(item["pnl_usdc"], 8)
    ordered = list(days.values())
    settled = sum(row["settled"] for row in ordered)
    wins = sum(row["wins"] for row in ordered)
    losses = sum(row["losses"] for row in ordered)
    return {
        "month": f"{year:04d}-{month:02d}",
        "day_tz": day_tz,
        "start_date": ordered[0]["date"],
        "end_date": ordered[-1]["date"],
        "available_months": available_months,
        "days": ordered,
        "total_pnl_usdc": round(total_pnl_raw, 8),
        "settled": settled,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / settled, 4) if settled else 0.0,
        "empty": settled == 0,
        "source": "settled_ledger_trading_day",
    }


def _daily_pnl_orders_from_records(records, *, day_value):
    day_value = str(day_value)
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", day_value) is None:
        raise ValueError("date must use YYYY-MM-DD")
    try:
        day = date.fromisoformat(day_value)
    except (TypeError, ValueError) as exc:
        raise ValueError("date must use YYYY-MM-DD") from exc
    day_tz = _dashboard_day_info(day=day)["day_tz"]
    settled_records = [
        record
        for record in _calendar_settled_records(records)
        if _dashboard_day_key(_record_ts(record)) == day
    ]
    ordered_records = sorted(
        settled_records,
        key=_record_ts,
        reverse=True,
    )
    orders = [
        {
            "order_id": record.get("order_id"),
            "market_slug": record.get("market_slug"),
            "direction": str(
                record.get("direction")
                or record.get("token_outcome")
                or record.get("side")
                or record.get("action")
                or ""
            ).upper() or None,
            "settle_ts": _iso_utc(_record_ts(record)),
            "filled_size": _float_value(record, "filled_size", "fill_size", "size_matched", "matched_size"),
            "average_fill_price": _float_value(record, "average_fill_price", "avg_fill_price"),
            "pnl_usdc": _record_pnl(record),
            "won": _record_won(record),
            "status": str(record.get("status") or "").upper() or None,
            "settlement_source": record.get("settlement_source"),
        }
        for record in ordered_records
    ]
    total_pnl = round(sum(row["pnl_usdc"] for row in orders), 8)
    settled = len(orders)
    wins = sum(1 for row in orders if row["won"] is True)
    losses = sum(1 for row in orders if row["won"] is False)
    return {
        "date": day.isoformat(),
        "day_tz": day_tz,
        "orders": orders,
        "total_pnl_usdc": total_pnl,
        "settled": settled,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / settled, 4) if settled else 0.0,
        "source": "settled_ledger_trading_day",
    }


def _analytics_trade_fact(record, *, hypothetical=False):
    if not isinstance(record, dict):
        return None
    status = str(record.get("status") or "").upper() or "UNKNOWN"
    filled_size = _float_value(record, "filled_size", "fill_size", "size_matched", "matched_size") or 0.0
    size = _float_value(record, "size", "original_size", "target_size", "requested_size", "order_size_shares")
    price = _float_value(record, "average_fill_price", "avg_fill_price", "price", "limit_price")

    if hypothetical:
        raw_price = _float_value(record, "price", "limit_price")
        hypothetical_pnl, hypothetical_won, hypothetical_pnl_basis = _hypothetical_order_result(
            record,
            status=status,
            filled_size=filled_size,
            size=size,
            price=raw_price,
            actual_pnl=_float_value(record, "pnl", "net_pnl", "realized_pnl", "pnl_usdc"),
        )
        if hypothetical_pnl is None or hypothetical_won is None:
            return None
        pnl = hypothetical_pnl
        won = hypothetical_won
        basis = hypothetical_pnl_basis
        price = raw_price
    else:
        if not _is_equity_settled_record(record):
            return None
        pnl = _record_pnl(record)
        won = _record_won(record)
        basis = "actual_settlement"

    ts = _record_ts(record)
    direction = _order_outcome_direction(record) or str(record.get("direction") or record.get("side") or record.get("action") or "").upper() or "UNKNOWN"
    price_tier = str(record.get("price_tier") or "").strip()
    if not price_tier:
        price_tier = f"price_{price:.2f}" if price is not None else "legacy_or_unset"
    return {
        "order_id": record.get("order_id"),
        "market_slug": record.get("market_slug"),
        "status": status,
        "pnl": round(float(pnl), 8),
        "won": won,
        "ts": ts,
        "ts_iso": _iso_utc(ts),
        "direction": direction,
        "price": price,
        "price_tier": price_tier,
        "filled_size": round(float(filled_size), 8),
        "size": size,
        "settlement_source": record.get("settlement_source") or record.get("market_result_source") or record.get("fill_source") or "",
        "execution_result": record.get("execution_result") or status,
        "basis": basis,
    }


def _analytics_metric_summary(facts):
    facts = [fact for fact in (facts or []) if isinstance(fact, dict)]
    count = len(facts)
    wins = sum(1 for fact in facts if fact.get("won") is True)
    losses = sum(1 for fact in facts if fact.get("won") is False)
    gross_profit = sum(float(fact.get("pnl") or 0.0) for fact in facts if float(fact.get("pnl") or 0.0) > 0)
    gross_loss = sum(float(fact.get("pnl") or 0.0) for fact in facts if float(fact.get("pnl") or 0.0) < 0)
    total_pnl = gross_profit + gross_loss
    average_win = gross_profit / wins if wins else None
    average_loss = gross_loss / losses if losses else None
    loss_abs = abs(gross_loss)
    profit_factor = gross_profit / loss_abs if loss_abs > 0 else None
    payoff_ratio = average_win / abs(average_loss) if average_win is not None and average_loss not in (None, 0) else None
    breakeven_win_rate = abs(average_loss) / (average_win + abs(average_loss)) if average_win is not None and average_loss not in (None, 0) else None
    return {
        "count": count,
        "settled": count,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / count, 4) if count else 0.0,
        "total_pnl_usdc": round(total_pnl, 8),
        "gross_profit_usdc": round(gross_profit, 8),
        "gross_loss_usdc": round(gross_loss, 8),
        "average_pnl_usdc": round(total_pnl / count, 8) if count else 0.0,
        "average_win_usdc": round(average_win, 8) if average_win is not None else None,
        "average_loss_usdc": round(average_loss, 8) if average_loss is not None else None,
        "profit_factor": round(profit_factor, 4) if profit_factor is not None else None,
        "payoff_ratio": round(payoff_ratio, 4) if payoff_ratio is not None else None,
        "breakeven_win_rate": round(breakeven_win_rate, 4) if breakeven_win_rate is not None else None,
    }


def _analytics_drawdown(points):
    if not points:
        return {
            "points": [],
            "max_drawdown_usdc": 0.0,
            "max_drawdown_pct": 0.0,
            "current_drawdown_usdc": 0.0,
            "current_drawdown_pct": 0.0,
            "peak": 0.0,
            "trough": 0.0,
        }
    peak = float(points[0])
    max_drawdown = 0.0
    max_drawdown_pct = 0.0
    trough = float(points[0])
    drawdown_points = []
    for raw_value in points:
        value = float(raw_value)
        if value > peak:
            peak = value
        drawdown = peak - value
        drawdown_pct = drawdown / peak if peak else 0.0
        if drawdown > max_drawdown:
            max_drawdown = drawdown
            max_drawdown_pct = drawdown_pct
            trough = value
        drawdown_points.append(round(-drawdown, 8))
    current_peak = max(float(value) for value in points)
    current_value = float(points[-1])
    current_drawdown = current_peak - current_value
    current_drawdown_pct = current_drawdown / current_peak if current_peak else 0.0
    return {
        "points": drawdown_points,
        "max_drawdown_usdc": round(max_drawdown, 8),
        "max_drawdown_pct": round(max_drawdown_pct, 4),
        "current_drawdown_usdc": round(current_drawdown, 8),
        "current_drawdown_pct": round(current_drawdown_pct, 4),
        "peak": round(current_peak, 8),
        "trough": round(trough, 8),
    }


def _analytics_streaks(facts):
    longest_win = 0
    longest_loss = 0
    current_win = 0
    current_loss = 0
    for fact in sorted(facts or [], key=lambda item: item.get("ts") or datetime.min.replace(tzinfo=timezone.utc)):
        if fact.get("won") is True:
            current_win += 1
            current_loss = 0
        elif fact.get("won") is False:
            current_loss += 1
            current_win = 0
        else:
            current_win = 0
            current_loss = 0
        longest_win = max(longest_win, current_win)
        longest_loss = max(longest_loss, current_loss)
    return {
        "longest_win_streak": longest_win,
        "longest_loss_streak": longest_loss,
        "current_win_streak": current_win,
        "current_loss_streak": current_loss,
    }


def _analytics_breakdown(facts, key_fn, *, limit=16):
    groups = {}
    for fact in facts or []:
        key = str(key_fn(fact) or "unknown")
        groups.setdefault(key, []).append(fact)
    rows = []
    for key, items in groups.items():
        summary = _analytics_metric_summary(items)
        rows.append({"key": key, "label": key.replace("_", " "), **summary})
    rows.sort(key=lambda item: (-int(item["count"]), -abs(float(item["total_pnl_usdc"])), str(item["key"])))
    return rows[:limit]


def _analytics_daily_series(facts):
    dated = [fact for fact in facts or [] if isinstance(fact.get("ts"), datetime)]
    if not dated:
        return []
    start = min(_dashboard_day_key(fact["ts"]) for fact in dated)
    end = max(_dashboard_day_key(fact["ts"]) for fact in dated)
    days = []
    current = start
    by_day = {}
    for fact in dated:
        by_day.setdefault(_dashboard_day_key(fact["ts"]), []).append(fact)
    while current <= end:
        day_facts = by_day.get(current, [])
        days.append({"date": current.isoformat(), **_analytics_metric_summary(day_facts)})
        current += timedelta(days=1)
    return days


def _analytics_window(facts, *, now, days=None, today=False):
    if today:
        today_key = _dashboard_day_info(now=now)["day"]
        filtered = [
            fact
            for fact in facts
            if isinstance(fact.get("ts"), datetime) and _dashboard_day_key(fact["ts"]) == today_key
        ]
    elif days is not None:
        cutoff = now - timedelta(days=days)
        filtered = [fact for fact in facts if isinstance(fact.get("ts"), datetime) and fact["ts"] >= cutoff]
    else:
        filtered = facts
    return _analytics_metric_summary(filtered)


def _live_current_balance_value():
    allowance_path, allowance_report = _latest_json_report("polymarket_clob_*allowance*_audit*.json")
    if not allowance_report:
        allowance_path, allowance_report = _latest_json_report("polymarket_clob_account_read_audit*.json")
    gate_path, gate_report = _latest_json_report("live_trade_gate*.json")
    preflight_path, preflight_report = _latest_json_report("live_preflight_chain*.json")
    preflight_components = preflight_report.get("components") if isinstance(preflight_report.get("components"), dict) else {}
    preflight_gate_report = (
        preflight_components.get("gate")
        if isinstance(preflight_components.get("gate"), dict)
        else {}
    )
    gate_mtime = gate_path.stat().st_mtime if gate_path else -1
    preflight_mtime = preflight_path.stat().st_mtime if preflight_path else -1
    use_preflight_gate = bool(preflight_gate_report) and preflight_mtime >= gate_mtime
    effective_gate_path = preflight_path if use_preflight_gate else gate_path
    effective_gate_report = preflight_gate_report if use_preflight_gate else gate_report
    gate_account = effective_gate_report.get("account") if isinstance(effective_gate_report.get("account"), dict) else {}
    network_calls = (allowance_report.get("network") or {}).get("calls") or []
    balance_allowance_call = next(
        (call for call in network_calls if call.get("name") == "get_balance_allowance"),
        {},
    )
    balance_value = _num(gate_account.get("usdc_balance", balance_allowance_call.get("balance")))
    balance_source = "live_gate_report" if gate_account.get("usdc_balance") is not None else "allowance_audit"
    _, effective_gate_age_seconds = _path_age(effective_gate_path)
    if (
        effective_gate_path
        and effective_gate_age_seconds is not None
        and effective_gate_age_seconds > CLOB_READONLY_MAX_AGE_SECONDS
    ):
        account_refresh = _live_clob_account_snapshot()
        refreshed_account = account_refresh.get("account") if isinstance(account_refresh, dict) else {}
        if account_refresh.get("ok") and isinstance(refreshed_account, dict):
            refreshed_balance = _num(refreshed_account.get("usdc_balance"))
            if refreshed_balance is not None:
                return refreshed_balance, str(account_refresh.get("source") or "live_clob_account_refresh")
    return balance_value, balance_source if balance_value is not None else "unavailable"


def _live_analytics_summary():
    ledger_path = _live_real_ledger_path()
    records = _rows_from_ledger_payload(_read_json(ledger_path))
    actual_facts = [
        fact
        for fact in (_analytics_trade_fact(record) for record in records)
        if fact is not None
    ]
    actual_facts = sorted(actual_facts, key=lambda item: item.get("ts") or datetime.min.replace(tzinfo=timezone.utc))
    hypothetical_facts = [
        fact
        for fact in (_analytics_trade_fact(record, hypothetical=True) for record in records)
        if fact is not None
    ]
    now = datetime.now(timezone.utc)
    day_info = _dashboard_day_info(now=now)
    current_balance, current_balance_source = _live_current_balance_value()
    equity = _equity_summary_from_records(
        records,
        current_balance=current_balance,
        source="live_real_orders",
    )
    labels = ["Start"] + [
        fact.get("ts_iso") or ""
        for fact in actual_facts
    ]
    drawdown = _analytics_drawdown(equity.get("points") or [])
    summary = _analytics_metric_summary(actual_facts)
    hypothetical_summary = _analytics_metric_summary(hypothetical_facts)
    return {
        "source": "live_real_orders_current_next",
        "ledger": str(ledger_path),
        "ledger_exists": ledger_path.exists(),
        "record_count": len(records),
        "generated_at": _iso_utc(now),
        "day": day_info["day_iso"],
        "day_tz": day_info["day_tz"],
        "day_start_utc": day_info["start_utc_iso"],
        "day_end_utc": day_info["end_utc_iso"],
        "summary": {
            **summary,
            **_analytics_streaks(actual_facts),
            "max_drawdown_usdc": drawdown["max_drawdown_usdc"],
            "max_drawdown_pct": drawdown["max_drawdown_pct"],
            "current_drawdown_usdc": drawdown["current_drawdown_usdc"],
            "current_drawdown_pct": drawdown["current_drawdown_pct"],
        },
        "windows": {
            "today": _analytics_window(actual_facts, now=now, today=True),
            "today_utc": _analytics_window(actual_facts, now=now, today=True),
            "last_24h": _analytics_window(actual_facts, now=now, days=1),
            "last_7d": _analytics_window(actual_facts, now=now, days=7),
            "all": summary,
        },
        "equity": {
            **equity,
            "labels": labels,
            "drawdown_points": drawdown["points"],
            "drawdown": drawdown,
            "current_balance_source": current_balance_source,
        },
        "pnl_composition": {
            "gross_profit_usdc": summary["gross_profit_usdc"],
            "gross_loss_usdc": summary["gross_loss_usdc"],
            "absolute_loss_usdc": round(abs(summary["gross_loss_usdc"]), 8),
            "profit_share": round(
                summary["gross_profit_usdc"] / (summary["gross_profit_usdc"] + abs(summary["gross_loss_usdc"])),
                4,
            )
            if summary["gross_profit_usdc"] + abs(summary["gross_loss_usdc"]) > 0
            else 0.0,
            "loss_share": round(
                abs(summary["gross_loss_usdc"]) / (summary["gross_profit_usdc"] + abs(summary["gross_loss_usdc"])),
                4,
            )
            if summary["gross_profit_usdc"] + abs(summary["gross_loss_usdc"]) > 0
            else 0.0,
        },
        "breakdowns": {
            "price_tier": _analytics_breakdown(actual_facts, lambda fact: fact.get("price_tier")),
            "direction": _analytics_breakdown(actual_facts, lambda fact: fact.get("direction")),
            "price": _analytics_breakdown(actual_facts, lambda fact: f"{float(fact['price']):.2f}" if fact.get("price") is not None else "unknown"),
            "settlement_source": _analytics_breakdown(actual_facts, lambda fact: fact.get("settlement_source") or "unknown"),
            "status": _analytics_breakdown(actual_facts, lambda fact: fact.get("status") or "unknown"),
        },
        "daily": _analytics_daily_series(actual_facts),
        "hypothetical_no_fill": {
            **hypothetical_summary,
            "breakdowns": {
                "price_tier": _analytics_breakdown(hypothetical_facts, lambda fact: fact.get("price_tier")),
                "direction": _analytics_breakdown(hypothetical_facts, lambda fact: fact.get("direction")),
                "price": _analytics_breakdown(hypothetical_facts, lambda fact: f"{float(fact['price']):.2f}" if fact.get("price") is not None else "unknown"),
            },
            "records": [
                {
                    key: value
                    for key, value in fact.items()
                    if key not in {"ts"}
                }
                for fact in sorted(hypothetical_facts, key=lambda item: item.get("ts") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[:80]
            ],
        },
        "recent": [
            {
                key: value
                for key, value in fact.items()
                if key not in {"ts"}
            }
            for fact in sorted(actual_facts, key=lambda item: item.get("ts") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[:80]
        ],
    }


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


def _risk_excluded(record):
    value = record.get("risk_excluded", record.get("exclude_from_risk", False))
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return False


def _record_direction(record):
    value = str(record.get("direction") or record.get("side") or record.get("action") or "").upper()
    if value in {"SHORT", "BUY_DOWN", "DOWN"}:
        return "DOWN"
    if value in {"LONG", "BUY_UP", "UP"}:
        return "UP"
    return value or "UNKNOWN"


def _max_drawdown(records):
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for record in records:
        equity += _record_pnl(record)
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    return round(max_drawdown, 6)


def _same_direction_cooldowns(records, *, count, minutes, now):
    losses_by_direction = {}
    for record in records:
        direction = _record_direction(record)
        won = _record_won(record)
        ts = _record_ts(record)
        if won is False:
            losses_by_direction.setdefault(direction, []).append(ts)
        elif won is True:
            losses_by_direction[direction] = []

    cooldowns = {}
    duration = timedelta(minutes=max(0, int(minutes)))
    for direction, timestamps in losses_by_direction.items():
        if len(timestamps) < int(count):
            continue
        last_ts = timestamps[-1]
        active_until = last_ts + duration if last_ts is not None else None
        active = active_until is not None and active_until > now
        cooldowns[direction] = {
            "losses": len(timestamps),
            "active": active,
            "active_until": active_until.isoformat().replace("+00:00", "Z") if active_until else None,
        }
    return cooldowns


def _risk_resilience_summary_from_records(records, controls=None):
    controls = controls if isinstance(controls, dict) else {}
    rules = controls.get("summary") if isinstance(controls.get("summary"), dict) else {}
    max_smoke_drawdown = _num(rules.get("max_smoke_drawdown_usdc"))
    if max_smoke_drawdown is None:
        max_smoke_drawdown = 25.0
    cooldown_count = int(_num(rules.get("same_direction_loss_cooldown_count")) or 1)
    cooldown_minutes = int(_num(rules.get("same_direction_loss_cooldown_minutes")) or 30)
    rows = [
        record
        for record in (records or [])
        if isinstance(record, dict) and not _risk_excluded(record)
    ]
    ordered = sorted(rows, key=lambda item: _record_ts(item) or datetime.min.replace(tzinfo=timezone.utc))
    total_pnl = round(sum(_record_pnl(record) for record in ordered), 6)
    wins = sum(1 for record in ordered if _record_won(record) is True)
    losses = sum(1 for record in ordered if _record_won(record) is False)
    gross_profit = sum(max(_record_pnl(record), 0.0) for record in ordered)
    gross_loss = abs(sum(min(_record_pnl(record), 0.0) for record in ordered))
    max_drawdown = _max_drawdown(ordered)
    now = datetime.now(timezone.utc)
    cooldowns = _same_direction_cooldowns(
        ordered,
        count=cooldown_count,
        minutes=cooldown_minutes,
        now=now,
    )
    failures = []
    warnings = []
    if any(record.get("requires_manual_cancel") for record in rows):
        failures.append("manual_cancel_required")
    uncertain = {"UNKNOWN", "CANCEL_FAILED", "FAILED_CANCEL", "ORDER_STATUS_UNKNOWN"}
    if any(str(record.get("status") or "").strip().upper() in uncertain for record in rows):
        failures.append("uncertain_order_lifecycle")
    for record in rows:
        maker_price = _num(record.get("maker_price") or record.get("price"))
        fill_price = _num(record.get("filled_avg_price") or record.get("average_fill_price"))
        if maker_price is not None and fill_price is not None and fill_price - maker_price > 0.02 + 1e-12:
            warnings.append("fill_quality_drift")
            break
    if float(max_smoke_drawdown) > 0 and max_drawdown >= abs(float(max_smoke_drawdown)):
        failures.append("max_smoke_drawdown_usdc")
    for direction, state in cooldowns.items():
        if state.get("active"):
            failures.append(f"same_direction_loss_cooldown:{direction}")
    metrics = {
        "total_trades": len(ordered),
        "total_pnl_usdc": total_pnl,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / len(ordered), 4) if ordered else 0.0,
        "profit_factor": round(gross_profit / gross_loss, 6) if gross_loss > 0 else None,
        "max_drawdown_usdc": max_drawdown,
        "max_consecutive_losses": _consecutive_losses(ordered),
        "same_direction_cooldowns": cooldowns,
        "limits": {
            "max_smoke_drawdown_usdc": float(max_smoke_drawdown),
            "same_direction_loss_cooldown_count": cooldown_count,
            "same_direction_loss_cooldown_minutes": cooldown_minutes,
        },
    }
    return {
        "ok": not failures,
        "reason": "risk-resilience limits passed" if not failures else f"risk-resilience limits breached: {', '.join(failures)}",
        "failures": failures,
        "warnings": warnings,
        "metrics": metrics,
        "source": "live_real_orders_current_next",
    }


def _risk_resilience_checks(resilience):
    if not isinstance(resilience, dict):
        return []
    metrics = resilience.get("metrics") if isinstance(resilience.get("metrics"), dict) else {}
    limits = metrics.get("limits") if isinstance(metrics.get("limits"), dict) else {}
    failures = set(str(item) for item in resilience.get("failures", []) or [])
    max_drawdown = round(float(metrics.get("max_drawdown_usdc") or 0.0), 2)
    max_drawdown_limit = float(limits.get("max_smoke_drawdown_usdc") or 0.0)
    smoke_drawdown_disabled = max_drawdown_limit <= 0.0
    cooldown_failures = sorted(item for item in failures if item.startswith("same_direction_loss_cooldown:"))
    lifecycle_failures = failures.intersection({"manual_cancel_required", "uncertain_order_lifecycle"})
    return [
        {
            "key": "risk_smoke_drawdown",
            "label": "Smoke drawdown",
            "ok": smoke_drawdown_disabled or "max_smoke_drawdown_usdc" not in failures,
            "value": max_drawdown,
            "expected": "disabled" if smoke_drawdown_disabled else f"< {max_drawdown_limit}",
            "severity": "risk",
        },
        {
            "key": "risk_same_direction_cooldown",
            "label": "Direction cooldown",
            "ok": not cooldown_failures,
            "value": ", ".join(cooldown_failures) if cooldown_failures else "clear",
            "expected": "no active cooldown",
            "severity": "risk",
        },
        {
            "key": "risk_order_lifecycle",
            "label": "Order lifecycle risk",
            "ok": not lifecycle_failures,
            "value": ", ".join(sorted(lifecycle_failures)) if lifecycle_failures else "clear",
            "expected": "no uncertain order lifecycle",
            "severity": "risk",
        },
    ]


def _attach_risk_resilience(risk, resilience):
    risk = dict(risk)
    checks = list(risk.get("checks") or [])
    checks.extend(_risk_resilience_checks(resilience))
    risk["checks"] = checks
    risk["resilience"] = resilience
    risk["ok"] = bool(risk.get("ok")) and bool(resilience.get("ok") if isinstance(resilience, dict) else True)
    return risk


def _risk_summary_from_records(records, inputs=None, limits_override=None, *, now=None):
    records = [record for record in (records or []) if isinstance(record, dict)]
    day_info = _dashboard_day_info(now=now)
    today = day_info["day"]
    todays = []
    open_or_pending = 0

    for record in records:
        status = str(record.get("status", "")).upper()
        if status in {"OPEN", "PENDING", "SUBMITTED"}:
            open_or_pending += 1
        ts = _record_ts(record)
        if _timestamp_in_dashboard_day(ts, today):
            todays.append(record)

    settled = [record for record in todays if _is_settled_record(record)]
    daily_pnl = sum(_record_pnl(record) for record in settled)
    daily_trades = len(settled)
    wins = sum(1 for record in settled if _record_won(record) is True)
    losses = sum(1 for record in settled if _record_won(record) is False)
    consecutive_losses = _consecutive_losses(settled)
    limits = dict(RISK_LIMITS)
    if isinstance(limits_override, dict):
        for key, default_value in RISK_LIMITS.items():
            override_value = _num(limits_override.get(key))
            if override_value is None:
                continue
            if isinstance(default_value, int) and not isinstance(default_value, bool):
                limits[key] = int(override_value)
            else:
                limits[key] = float(override_value)
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
            "ok": open_or_pending <= limits["max_open_or_pending_orders"],
            "value": open_or_pending,
            "expected": f"<= {limits['max_open_or_pending_orders']}",
            "severity": "risk",
        },
    ]
    return {
        "ok": all(check["ok"] for check in checks),
        "checks": checks,
        "metrics": {
            "day": today.isoformat(),
            "day_tz": day_info["day_tz"],
            "day_start_utc": day_info["start_utc_iso"],
            "day_end_utc": day_info["end_utc_iso"],
            "day_utc": today.isoformat(),
            "daily_pnl_usdc": round(daily_pnl, 8),
            "daily_trades": daily_trades,
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / daily_trades, 4) if daily_trades else 0.0,
            "consecutive_losses": consecutive_losses,
            "open_or_pending_orders": open_or_pending,
        },
        "limits": limits,
        "inputs": inputs or {"record_count": len(records)},
    }


def _live_risk_summary(checkpoint=None):
    checkpoint = checkpoint if isinstance(checkpoint, dict) else (_read_json(_paper_checkpoint_path()) or {})
    ledger = _read_json(_paper_ledger_path())
    records = _risk_records_from_payload(checkpoint) + _risk_records_from_payload(ledger)
    return _risk_summary_from_records(
        records,
        inputs={
            "checkpoint": str(_paper_checkpoint_path()),
            "ledger": str(_paper_ledger_path()),
            "record_count": len(records),
        },
    )


def _rows_from_ledger_payload(payload):
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("value"), list):
        return [item for item in payload.get("value") if isinstance(item, dict)]
    for key in ("orders", "trades", "records"):
        if isinstance(payload.get(key), list):
            return [item for item in payload.get(key) if isinstance(item, dict)]
    return []


def _order_status_summary(records):
    status_counts = {}
    open_or_pending = 0
    settled = 0
    cancelled = 0
    no_fill_cancelled = 0
    filled_orders = 0
    filled = 0.0
    for record in records:
        status = str(record.get("status") or "").upper()
        status_counts[status or "UNKNOWN"] = status_counts.get(status or "UNKNOWN", 0) + 1
        filled_size = 0.0
        try:
            filled_size = float(record.get("filled_size") or record.get("fill_size") or 0.0)
        except (TypeError, ValueError):
            filled_size = 0.0
        if status in {"OPEN", "PENDING", "SUBMITTED", "PARTIAL", "MATCHED", "LIVE"}:
            open_or_pending += 1
        if status in {"SETTLED", "FILLED", "WON", "LOST", "CLOSED"} or _is_settled_record(record):
            settled += 1
        no_fill = (
            status == "NO_FILL"
            or str(record.get("execution_result") or "").lower() == "no_fill"
            or (status in {"CANCELLED", "CANCELED"} and filled_size <= 0)
        )
        if status in {"CANCELLED", "CANCELED"}:
            cancelled += 1
        if no_fill and filled_size <= 0:
            no_fill_cancelled += 1
        if filled_size > 0:
            filled_orders += 1
        filled += filled_size
    return {
        "total": len(records),
        "open_or_pending": open_or_pending,
        "settled": settled,
        "cancelled": cancelled,
        "no_fill_cancelled": no_fill_cancelled,
        "filled_orders": filled_orders,
        "filled_size": round(filled, 8),
        "status_counts": status_counts,
    }


def _latest_record(records):
    ordered = sorted(
        [record for record in records if isinstance(record, dict)],
        key=lambda item: _record_ts(item) or datetime.min.replace(tzinfo=timezone.utc),
    )
    return ordered[-1] if ordered else None


def _float_value(record, *keys):
    for key in keys:
        try:
            value = record.get(key)
            if value in (None, ""):
                continue
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _bool_value(record, *keys):
    for key in keys:
        value = record.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "win", "won"}:
                return True
            if normalized in {"0", "false", "no", "loss", "lost"}:
                return False
    return None


def _order_outcome_direction(record):
    for key in ("token_outcome", "outcome", "direction", "side", "action"):
        text = str(record.get(key) or "").strip().upper()
        if not text:
            continue
        if text in {"UP", "BUY_UP", "LONG"} or text.endswith("_UP"):
            return "UP"
        if text in {"DOWN", "BUY_DOWN", "SHORT"} or text.endswith("_DOWN"):
            return "DOWN"
    return None


def _hypothetical_order_result(record, *, status, filled_size, size, price, actual_pnl):
    if actual_pnl is not None or filled_size > 0:
        return None, None, None
    execution_result = str(record.get("execution_result") or "").lower()
    exchange_status = str(record.get("exchange_final_status") or "").upper()
    closed_without_fill = (
        status in {"NO_FILL", "CANCELLED", "CANCELED"}
        or exchange_status in {"NO_FILL", "CANCELLED", "CANCELED"}
        or execution_result == "no_fill"
    )
    if not closed_without_fill or size is None or price is None or size <= 0:
        return None, None, None

    won = _bool_value(record, "signal_would_have_won")
    if won is None:
        actual_up = _bool_value(record, "actual_up_chainlink", "actual_up")
        outcome = _order_outcome_direction(record)
        if actual_up is not None and outcome is not None:
            won = actual_up if outcome == "UP" else not actual_up
    if won is None:
        return None, None, None

    hypothetical_pnl = size * (1.0 - price) if won else -(size * price)
    return round(hypothetical_pnl, 8), won, "unfilled_limit"


def _live_order_has_final_settlement(record):
    if record.get("settled_at"):
        return True
    settlement_source = str(record.get("settlement_source") or "").strip().lower()
    if settlement_source in {"chainlink_candlestick", "polymarket_gamma_outcome"}:
        return True
    if record.get("market_result_source") or record.get("market_result"):
        return True
    return False


def _live_order_record_summary(record, *, order_chain=None):
    raw_status = str(record.get("status") or "").upper() or "UNKNOWN"
    filled_size = _float_value(record, "filled_size", "fill_size", "size_matched", "matched_size") or 0.0
    size = _float_value(record, "size", "original_size", "target_size", "requested_size", "order_size_shares")
    remaining = _float_value(record, "remaining_size")
    if remaining is None and size is not None:
        remaining = max(0.0, size - filled_size)
    price = _float_value(record, "price", "limit_price")
    pnl = _float_value(record, "pnl", "net_pnl", "realized_pnl", "pnl_usdc")
    status = raw_status
    exchange_final_status = record.get("exchange_final_status")
    if filled_size > 0 and raw_status != "SETTLED" and _live_order_has_final_settlement(record):
        status = "SETTLED"
        exchange_final_status = exchange_final_status or raw_status
    hypothetical_pnl, hypothetical_won, hypothetical_pnl_basis = _hypothetical_order_result(
        record,
        status=status,
        filled_size=filled_size,
        size=size,
        price=price,
        actual_pnl=pnl,
    )
    chain = list(order_chain or [])
    summary = {
        "order_id": record.get("order_id"),
        "market_slug": record.get("market_slug"),
        "status": status,
        "exchange_final_status": exchange_final_status,
        "risk_excluded": _risk_excluded(record),
        "direction": record.get("direction") or record.get("side") or record.get("action"),
        "token_outcome": record.get("token_outcome") or record.get("outcome"),
        "price": price,
        "average_fill_price": _float_value(record, "average_fill_price", "avg_fill_price"),
        "size": size,
        "filled_size": round(filled_size, 8),
        "remaining_size": round(remaining, 8) if remaining is not None else None,
        "pnl": pnl,
        "won": _record_won(record),
        "hypothetical_pnl": hypothetical_pnl,
        "hypothetical_won": hypothetical_won,
        "hypothetical_pnl_basis": hypothetical_pnl_basis,
        "entry_ts": record.get("entry_ts"),
        "settle_ts": record.get("settle_ts"),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "settled_at": record.get("settled_at"),
        "fill_source": record.get("fill_source"),
        "settlement_source": record.get("settlement_source"),
        "market_result_source": record.get("market_result_source"),
        "execution_result": record.get("execution_result"),
        "signal_id": record.get("signal_id") or record.get("order_key"),
    }
    if chain:
        summary["attempts"] = len(chain)
        summary["order_chain_count"] = len(chain)
        summary["order_chain"] = chain
    return summary


def _live_order_timeline_ts(record):
    for key in ("settle_ts", "entry_ts", "created_at", "settled_at", "updated_at", "ts"):
        parsed = _parse_dt(record.get(key))
        if parsed is not None:
            return parsed
    return None


def _live_order_records(records, *, limit=60):
    grouped = {}
    standalone = []
    for record in records:
        if not isinstance(record, dict):
            continue
        signal_key = record.get("signal_id") or record.get("order_key")
        if signal_key:
            grouped.setdefault(str(signal_key), []).append(record)
        else:
            standalone.append(record)

    representatives = []
    for rows in grouped.values():
        chain_rows = sorted(_dedupe_order_chain_rows(rows), key=_order_chain_sort_value)
        representative = _best_execution_order(chain_rows) or chain_rows[-1]
        representatives.append(
            _live_order_record_summary(
                representative,
                order_chain=[_order_chain_item(row) for row in chain_rows],
            )
        )
    representatives.extend(_live_order_record_summary(record) for record in standalone)

    return sorted(
        representatives,
        key=lambda item: _live_order_timeline_ts(item) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )[:limit]


def _unix_timestamp_iso(value):
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _polymarket_activity_ts(row):
    if not isinstance(row, dict):
        return None
    timestamp_iso = _unix_timestamp_iso(row.get("timestamp"))
    if timestamp_iso:
        return _parse_dt(timestamp_iso)
    for key in ("created_at", "createdAt", "updated_at", "updatedAt", "ts"):
        parsed = _parse_dt(row.get(key))
        if parsed is not None:
            return parsed
    return None


def _polymarket_activity_summary(row):
    ts = _polymarket_activity_ts(row)
    return {
        "type": str(row.get("type") or "").upper(),
        "side": str(row.get("side") or "").upper(),
        "outcome": row.get("outcome"),
        "price": _num(row.get("price")),
        "size": _num(row.get("size")),
        "usdc_size": _num(row.get("usdcSize") or row.get("usdc_size")),
        "slug": row.get("slug") or row.get("eventSlug"),
        "event_slug": row.get("eventSlug") or row.get("slug"),
        "title": row.get("title"),
        "timestamp": row.get("timestamp"),
        "timestamp_iso": _iso_utc(ts),
        "transaction_hash": row.get("transactionHash") or row.get("transaction_hash"),
    }


def _polymarket_position_summary(row):
    return {
        "slug": row.get("slug") or row.get("eventSlug"),
        "event_slug": row.get("eventSlug") or row.get("slug"),
        "title": row.get("title"),
        "outcome": row.get("outcome"),
        "size": _num(row.get("size")),
        "avg_price": _num(row.get("avgPrice") or row.get("avg_price")),
        "current_value": _num(row.get("currentValue") or row.get("current_value")),
        "cash_pnl": _num(row.get("cashPnl") or row.get("cash_pnl")),
        "realized_pnl": _num(row.get("realizedPnl") or row.get("realized_pnl")),
        "cur_price": _num(row.get("curPrice") or row.get("cur_price")),
        "redeemable": bool(row.get("redeemable")),
        "mergeable": bool(row.get("mergeable")),
    }


def _polymarket_account_activity_summary(limit=30, cash_balance=None):
    path = _polymarket_account_activity_path()
    report = _read_json(path) or {}
    activity = report.get("activity") if isinstance(report.get("activity"), list) else []
    positions = report.get("positions") if isinstance(report.get("positions"), list) else []
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    report_mtime, report_age_seconds = _path_age(path if path.exists() else None)
    ordered_activity = sorted(
        [row for row in activity if isinstance(row, dict)],
        key=lambda row: _polymarket_activity_ts(row) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    position_rows = [row for row in positions if isinstance(row, dict)]
    active_position_rows = [row for row in position_rows if not bool(row.get("redeemable"))]
    redeemable_position_rows = [row for row in position_rows if bool(row.get("redeemable"))]
    ordered_positions = sorted(
        active_position_rows,
        key=lambda row: abs(_num(row.get("cashPnl") or row.get("cash_pnl")) or 0.0),
        reverse=True,
    )
    ordered_redeemable_positions = sorted(
        redeemable_position_rows,
        key=lambda row: abs(_num(row.get("cashPnl") or row.get("cash_pnl")) or 0.0),
        reverse=True,
    )
    position_summaries = [_polymarket_position_summary(row) for row in ordered_positions[:limit]]
    redeemable_position_summaries = [
        _polymarket_position_summary(row) for row in ordered_redeemable_positions[:limit]
    ]
    positions_value = sum(
        value
        for value in (_num(row.get("currentValue") or row.get("current_value")) for row in positions if isinstance(row, dict))
        if value is not None
    )
    cash_balance_usdc = _num(cash_balance) or 0.0
    return {
        "available": path.exists(),
        "ok": bool(report.get("ok")),
        "source": report.get("source") or "polymarket_data_api",
        "report": str(path),
        "report_mtime": report_mtime,
        "report_age_seconds": report_age_seconds,
        "created_at": report.get("created_at"),
        "user": report.get("user"),
        "reason": report.get("reason"),
        "summary": {
            "activity_count": int(summary.get("activity_count", len(activity)) or 0),
            "positions_count": int(summary.get("positions_count", len(positions)) or 0),
            "active_positions_count": len(active_position_rows),
            "redeemable_positions_count": len(redeemable_position_rows),
            "trade_count": int(summary.get("trade_count", 0) or 0),
            "redeem_count": int(summary.get("redeem_count", 0) or 0),
        },
        "portfolio": {
            "cash_balance_usdc": round(cash_balance_usdc, 8),
            "positions_value_usdc": round(positions_value, 8),
            "total_value_usdc": round(cash_balance_usdc + positions_value, 8),
            "positions_count": len(positions),
            "source": "clob_cash_plus_polymarket_positions",
        },
        "recent_activity": [_polymarket_activity_summary(row) for row in ordered_activity[:limit]],
        "positions": position_summaries,
        "redeemable_positions": redeemable_position_summaries,
    }


def _live_soak_summary():
    path = KRONOS_REPORT_DIR / "prediction_bound_live_soak_latest.json"
    report = _read_json(path) or {}
    latest_report = report.get("latest_report") if isinstance(report.get("latest_report"), dict) else {}
    prediction = latest_report.get("prediction") if isinstance(latest_report.get("prediction"), dict) else {}
    report_mtime, report_age_seconds = _path_age(path if path.exists() else None)
    return {
        "available": path.exists(),
        "report": str(path),
        "report_mtime": report_mtime,
        "report_age_seconds": report_age_seconds,
        "ok": bool(report.get("ok")),
        "submit_enabled": bool(report.get("submit_enabled")),
        "terminal_reason": str(report.get("terminal_reason") or ""),
        "attempts": int(report.get("attempts", 0) or 0),
        "submitted_count": int(report.get("submitted_count", 0) or 0),
        "started_at": report.get("started_at"),
        "elapsed_seconds": report.get("elapsed_seconds"),
        "latest_action": prediction.get("action"),
        "latest_reason": prediction.get("reason_code") or prediction.get("reason") or latest_report.get("reason"),
        "latest_decision_id": prediction.get("decision_id") or prediction.get("prediction_artifact_decision_id"),
        "blockers": latest_report.get("blockers", []) if isinstance(latest_report.get("blockers"), list) else [],
    }


def _live_formal_summary():
    path, report = _live_formal_report()
    prediction = report.get("prediction") if isinstance(report.get("prediction"), dict) else {}
    report_mtime, report_age_seconds = _path_age(path if path.exists() else None)
    return {
        "available": path.exists(),
        "report": str(path),
        "report_mtime": report_mtime,
        "report_age_seconds": report_age_seconds,
        "ok": bool(report.get("ok")),
        "submitted": bool(report.get("submitted")),
        "terminal_reason": str(report.get("terminal_reason") or ""),
        "reason": str(report.get("reason") or ""),
        "attempts": int(report.get("attempts", 0) or 0),
        "latest_action": prediction.get("action"),
        "latest_reason": prediction.get("reason_code") or prediction.get("reason") or report.get("reason"),
        "latest_decision_id": prediction.get("decision_id") or prediction.get("prediction_artifact_decision_id"),
        "latest_created_at": prediction.get("created_at") or report.get("created_at"),
        "decision_bar_ts": prediction.get("decision_bar_ts"),
        "entry_ts": prediction.get("entry_ts"),
        "settle_ts": prediction.get("settle_ts"),
        "target_market_entry_ts": prediction.get("target_market_entry_ts"),
        "target_market_settle_ts": prediction.get("target_market_settle_ts"),
        "execution_market_shift": prediction.get("execution_market_shift"),
    }


def _command_has_flag(command: str, flag: str) -> bool:
    tokens = str(command or "").split()
    return flag in tokens


def _command_option_value(command: str, option: str) -> str:
    tokens = str(command or "").split()
    for index, token in enumerate(tokens):
        if token == option and index + 1 < len(tokens):
            return tokens[index + 1]
        prefix = f"{option}="
        if token.startswith(prefix):
            return token[len(prefix):]
    return ""


def _live_real_order_mode(live_real_summary=None):
    live_real_summary = live_real_summary if isinstance(live_real_summary, dict) else {}
    runtime = live_real_summary.get("runtime") if isinstance(live_real_summary.get("runtime"), dict) else {}
    child_runtime = runtime.get("child_runtime") if isinstance(runtime.get("child_runtime"), dict) else {}
    matches = child_runtime.get("matches") if isinstance(child_runtime.get("matches"), list) else []
    for match in matches:
        if not isinstance(match, dict):
            continue
        command = str(match.get("command_full") or match.get("command") or "")
        if "run_prediction_bound_live_order.py" not in command:
            continue
        submit_enabled = _command_has_flag(command, "--submit")
        environment_enabled = _command_option_value(command, "--environment-enabled").upper() == "YES"
        if submit_enabled or environment_enabled:
            return {
                "enabled": submit_enabled and environment_enabled,
                "source": "formal_process",
                "submit": submit_enabled,
                "environment_enabled": environment_enabled,
            }

    formal = live_real_summary.get("formal") if isinstance(live_real_summary.get("formal"), dict) else {}
    submit_enabled = bool(formal.get("submit_enabled"))
    submitted = bool(formal.get("submitted"))
    if submit_enabled or submitted:
        return {
            "enabled": submit_enabled or submitted,
            "source": "formal_report",
            "submit": submit_enabled,
            "submitted": submitted,
        }

    env_enabled = os.environ.get("KRONOS_ENABLE_REAL_ORDERS", "").strip().upper() == "YES"
    return {
        "enabled": env_enabled,
        "source": "environment",
        "submit": env_enabled,
        "environment_enabled": env_enabled,
    }


def _live_process_runtime(*, formal_available, formal=None, soak=None):
    formal = formal if isinstance(formal, dict) else {}
    soak = soak if isinstance(soak, dict) else {}
    if formal_available:
        supervisor_runtime = _process_summary(["run_prediction_bound_live_formal_supervisor.ps1"])
        child_runtime = _process_summary(["run_prediction_bound_live_order.py"])
        runtime = dict(supervisor_runtime if supervisor_runtime.get("running") else child_runtime)
        runtime["role"] = "formal_supervisor" if supervisor_runtime.get("running") else "formal_child"
        runtime["child_runtime"] = child_runtime
        if not runtime.get("started_at") and formal.get("latest_created_at"):
            started = _parse_dt(formal.get("latest_created_at"))
            runtime.update({
                "started_at": _iso_utc(started),
                "uptime_seconds": _timestamp_age_seconds(started),
            })
        return runtime

    runtime = _process_summary(["run_prediction_bound_live_soak.py"])
    runtime = dict(runtime)
    runtime["role"] = "soak"
    if not runtime.get("started_at") and soak.get("started_at"):
        started = _parse_dt(soak.get("started_at"))
        runtime.update({
            "started_at": _iso_utc(started),
            "uptime_seconds": _timestamp_age_seconds(started),
        })
    return runtime


def _live_order_sync_summary():
    path = KRONOS_REPORT_DIR / "live_order_sync_latest.json"
    report = _read_json(path) or {}
    report_mtime, report_mtime_age_seconds = _path_age(path if path.exists() else None)
    created_ts = _parse_dt(report.get("created_at"))
    age_seconds = _timestamp_age_seconds(created_ts) if created_ts is not None else report_mtime_age_seconds
    max_age_seconds = int(os.environ.get("DASHBOARD_LIVE_ORDER_SYNC_MAX_AGE_SECONDS", "240"))
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    runtime = _process_summary(["run_live_order_sync_loop.py"])
    reconcile_errors = int(_num(summary.get("reconcile_errors")) or 0)
    ok = bool(report.get("ok")) and reconcile_errors == 0
    fresh = path.exists() and age_seconds is not None and age_seconds <= max_age_seconds
    return {
        "available": path.exists(),
        "report": str(path),
        "report_mtime": report_mtime,
        "created_at": report.get("created_at"),
        "age_seconds": age_seconds,
        "max_age_seconds": max_age_seconds,
        "fresh": fresh,
        "ok": ok,
        "runtime": runtime,
        "running": bool(runtime.get("running")),
        "reconcile_errors": reconcile_errors,
        "open_orders": int(_num(summary.get("open_orders")) or 0),
        "settled": int(_num(summary.get("settled")) or 0),
        "trade_reconciled": int(_num(summary.get("trade_reconciled")) or 0),
        "settlement_source": summary.get("settlement_source"),
        "summary": summary,
    }


def _strategy_record_ts(record):
    for key in ("created_at", "source_prediction_created_at", "report_created_at", "entry_ts", "settle_ts"):
        parsed = _parse_dt(record.get(key))
        if parsed is not None:
            return parsed
    return None


def _candidate_passed(record):
    candidate = record.get("candidate") if isinstance(record.get("candidate"), dict) else {}
    return bool(candidate.get("passed"))


def _candidate_side(record):
    candidate = record.get("candidate") if isinstance(record.get("candidate"), dict) else {}
    side = str(candidate.get("side") or "").upper()
    return side if side in {"LONG", "SHORT"} else ""


def _strategy_scored_summary_payload(path=None):
    path = path or _candidate_no_submit_official_truth_scored_summary_path()
    payload = _read_json(path) or {}
    return payload if isinstance(payload, dict) else {}


def _strategy_config_num(payload, paths):
    if not isinstance(payload, dict):
        return None
    for path in paths:
        current = payload
        for key in path:
            if not isinstance(current, dict):
                current = None
                break
            current = current.get(key)
        value = _num(current)
        if value is not None:
            return value
    return None


def _strategy_live_scoring_defaults(scored_payload=None):
    scored_payload = scored_payload if isinstance(scored_payload, dict) else {}
    size_shares = _num(scored_payload.get("size_shares"))
    maker_price = _num(scored_payload.get("default_maker_price"))
    config_paths = [
        KRONOS_RUNTIME_DIR / "effective_trade_config.json",
        KRONOS_CONFIG_DIR / "aligned_prod_current_next_official_truth_14d14d_live_params.json",
        KRONOS_CONFIG_DIR / "trade_profiles" / "live_current_formal.json",
    ]
    for path in config_paths:
        if size_shares is not None and maker_price is not None:
            break
        payload = _read_json(path) or {}
        if not isinstance(payload, dict):
            continue
        if size_shares is None:
            size_shares = _strategy_config_num(
                payload,
                (
                    ("size_shares",),
                    ("order_size_shares",),
                    ("live_restart_contract", "order_size_shares"),
                    ("sizing", "order_size_shares"),
                ),
            )
        if maker_price is None:
            maker_price = _strategy_config_num(
                payload,
                (
                    ("maker_price_assumption",),
                    ("default_maker_price",),
                    ("maker_price",),
                    ("live_restart_contract", "maker_price_assumption"),
                    ("live_restart_contract", "min_price"),
                    ("price", "maker_price_assumption"),
                    ("price", "min_price"),
                ),
            )
    return {
        "size_shares": size_shares if size_shares is not None else 5.0,
        "maker_price": maker_price if maker_price is not None else 0.49,
    }


def _strategy_scored_summary_by_candidate(path=None):
    payload = _strategy_scored_summary_payload(path)
    defaults = _strategy_live_scoring_defaults(payload)
    rows = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
    out = {}
    for row in rows:
        if not isinstance(row, dict) or not row.get("candidate_id"):
            continue
        item = dict(row)
        item.setdefault("_scoring_size_shares", defaults["size_shares"])
        item.setdefault("_scoring_maker_price", defaults["maker_price"])
        out[str(row.get("candidate_id") or "")] = item
    return out


def _strategy_compare_filters(args):
    window = str(args.get("window") or "7d").strip().lower()
    if window not in {"today", "24h", "7d", "14d", "all"}:
        window = "7d"
    bucket = str(args.get("bucket") or "day").strip().lower()
    if bucket not in {"hour", "day"}:
        bucket = "day"
    metric = str(args.get("metric") or "signals").strip().lower()
    if metric not in {"signals", "pnl", "win_rate", "overlap"}:
        metric = "signals"
    raw_candidates = str(args.get("candidates") or "").strip()
    allowed = {spec["candidate_id"] for spec in STRATEGY_COMPARE_CANDIDATES}
    if raw_candidates:
        candidates = [item.strip() for item in raw_candidates.split(",") if item.strip() in allowed]
    else:
        candidates = [spec["candidate_id"] for spec in STRATEGY_COMPARE_CANDIDATES]
    if not candidates:
        candidates = [spec["candidate_id"] for spec in STRATEGY_COMPARE_CANDIDATES]
    return {
        "window": window,
        "bucket": bucket,
        "metric": metric,
        "candidates": candidates,
        "available_metrics": ["signals", "pnl", "win_rate", "overlap"],
    }


def _strategy_difference_filters(args):
    candidate_id = str(args.get("candidate") or "").strip()
    allowed_candidates = {
        spec["candidate_id"] for spec in STRATEGY_COMPARE_CANDIDATES
    }
    if candidate_id not in allowed_candidates:
        raise ValueError("invalid_candidate")
    raw_window = args.get("window")
    window = "7d" if raw_window is None else str(raw_window).strip().lower()
    if window not in {"today", "24h", "7d", "14d", "all"}:
        raise ValueError("invalid_window")
    requested_types = [
        value.strip()
        for value in str(args.get("types") or "").split(",")
        if value.strip()
    ]
    invalid_types = [
        value for value in requested_types if value not in DIFFERENCE_TYPES
    ]
    if invalid_types:
        raise ValueError("invalid_types")
    raw_include_matched = args.get("include_matched")
    normalized_include_matched = str(raw_include_matched or "").strip().lower()
    if normalized_include_matched in {"", "0", "false", "no"}:
        include_matched = False
    elif normalized_include_matched in {"1", "true", "yes"}:
        include_matched = True
    else:
        raise ValueError("invalid_include_matched")
    try:
        raw_limit = args.get("limit")
        raw_offset = args.get("offset")
        limit = 100 if raw_limit is None else int(raw_limit)
        offset = 0 if raw_offset is None else int(raw_offset)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_pagination") from exc
    if not 1 <= limit <= 500 or offset < 0:
        raise ValueError("invalid_pagination")
    return {
        "candidate_id": candidate_id,
        "window": window,
        "types": requested_types,
        "include_matched": include_matched,
        "limit": limit,
        "offset": offset,
    }


def _strategy_window_start(now_dt, window):
    now_dt = _ensure_aware_utc(now_dt) or datetime.now(timezone.utc)
    today = _dashboard_day_info(now=now_dt)["day"]
    if window in {"today", "24h"}:
        return _dashboard_day_info(day=today)["start_utc"]
    if window == "7d":
        return _dashboard_day_info(day=today - timedelta(days=6))["start_utc"]
    if window == "14d":
        return _dashboard_day_info(day=today - timedelta(days=13))["start_utc"]
    return None


def _strategy_records_for_filters(records, *, now_dt, filters):
    start = _strategy_window_start(now_dt, filters["window"])
    selected = set(filters["candidates"])
    selected_records = []
    for record in records:
        candidate_id = str(record.get("candidate_id") or "")
        if candidate_id not in selected:
            continue
        ts = _strategy_record_ts(record)
        if ts is None:
            continue
        ts = ts.astimezone(timezone.utc)
        if start is not None and ts < start:
            continue
        selected_records.append(record)
    return selected_records


def _strategy_bucket_key(ts, bucket):
    ts = ts.astimezone(timezone.utc)
    if bucket == "hour":
        return ts.replace(minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")
    return _dashboard_day_key(ts).isoformat()


def _scored_trade_buckets(scored, bucket="day", *, now_dt=None, filters=None):
    trades = scored.get("trades") if isinstance(scored, dict) and isinstance(scored.get("trades"), list) else []
    if not trades:
        return {}
    if filters is not None:
        trades = [
            trade for trade in trades
            if isinstance(trade, dict) and _strategy_trade_in_window(trade, now_dt=now_dt, filters=filters)
        ]
    grouped = _strategy_group_records_by_trade_key(
        trades,
        keep="first",
        key_func=_strategy_scored_bucket_trade_key,
    )
    buckets = {}
    for rows in grouped.values():
        if not rows:
            continue
        trade = rows[0]
        ts = _strategy_trade_entry_ts(trade)
        if ts is None:
            continue
        bucket_key = _strategy_bucket_key(ts, bucket)
        row = buckets.setdefault(
            bucket_key,
            {
                "bucket": bucket_key,
                "settled": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": None,
                "pnl_usdc": 0.0,
            },
        )
        row["settled"] += 1
        won = _record_won(trade)
        if won is True:
            row["wins"] += 1
        elif won is False:
            row["losses"] += 1
        row["pnl_usdc"] += _strategy_scored_pnl(
            trade,
            default_size_shares=_num(scored.get("_scoring_size_shares")) or 5.0,
            default_maker_price=_num(scored.get("_scoring_maker_price")) or 0.49,
        )
    for row in buckets.values():
        row["pnl_usdc"] = round(float(row["pnl_usdc"]), 6)
        row["win_rate"] = round(row["wins"] / row["settled"], 4) if row["settled"] else None
    return buckets


def _scored_buckets_by_candidate(scored_by_candidate, bucket="day", *, now_dt=None, filters=None):
    out = {}
    for candidate_id, scored in scored_by_candidate.items():
        trade_buckets = _scored_trade_buckets(scored, bucket, now_dt=now_dt, filters=filters)
        if trade_buckets:
            out[candidate_id] = trade_buckets
            continue
        key = "hourly" if bucket == "hour" else "daily"
        rows = scored.get(key) if isinstance(scored, dict) and isinstance(scored.get(key), list) else []
        out[candidate_id] = {
            str(row.get("bucket") or row.get("date") or ""): row
            for row in rows
            if isinstance(row, dict) and (row.get("bucket") or row.get("date"))
        }
    return out


def _strategy_timeseries(records, *, now_dt, filters, scored_by_candidate):
    buckets = {}
    for record in _strategy_records_for_filters(records, now_dt=now_dt, filters=filters):
        candidate_id = str(record.get("candidate_id") or "")
        ts = _strategy_record_ts(record)
        if ts is None:
            continue
        ts = ts.astimezone(timezone.utc)
        key = (candidate_id, _strategy_bucket_key(ts, filters["bucket"]))
        bucket = buckets.setdefault(
            key,
            {
                "bucket": key[1],
                "candidate_id": candidate_id,
                "evaluated": 0,
                "passed": 0,
                "pass_rate": 0.0,
                "candidate_only": 0,
                "same_side_overlap": 0,
                "live_signal_filtered": 0,
                "wins": None,
                "losses": None,
                "win_rate": None,
                "pnl_usdc": None,
                "scored": False,
            },
        )
        bucket["evaluated"] += 1
        if _candidate_passed(record):
            bucket["passed"] += 1
        if record.get("candidate_only"):
            bucket["candidate_only"] += 1
        if record.get("same_side_overlap"):
            bucket["same_side_overlap"] += 1
        if record.get("live_signal_filtered"):
            bucket["live_signal_filtered"] += 1
    scored_daily = _scored_buckets_by_candidate(
        scored_by_candidate,
        filters["bucket"],
        now_dt=now_dt,
        filters=filters,
    )
    for bucket in buckets.values():
        if bucket["evaluated"]:
            bucket["pass_rate"] = round(bucket["passed"] / bucket["evaluated"], 4)
        scored_row = scored_daily.get(bucket["candidate_id"], {}).get(bucket["bucket"])
        if isinstance(scored_row, dict):
            bucket["wins"] = scored_row.get("wins")
            bucket["losses"] = scored_row.get("losses")
            bucket["win_rate"] = scored_row.get("win_rate")
            bucket["pnl_usdc"] = scored_row.get("pnl_usdc", scored_row.get("total_pnl"))
            bucket["scored"] = True
    return sorted(buckets.values(), key=lambda row: (row["bucket"], row["candidate_id"]))


def _strategy_window_scores(timeseries):
    scores = {}
    for row in timeseries:
        if not row.get("scored"):
            continue
        candidate_id = str(row.get("candidate_id") or "")
        if not candidate_id:
            continue
        score = scores.setdefault(
            candidate_id,
            {"wins": 0, "losses": 0, "pnl_usdc": 0.0, "pnl_seen": False, "scored": False},
        )
        wins = _num(row.get("wins"))
        losses = _num(row.get("losses"))
        pnl = _num(row.get("pnl_usdc"))
        if wins is not None:
            score["wins"] += int(wins)
            score["scored"] = True
        if losses is not None:
            score["losses"] += int(losses)
            score["scored"] = True
        if pnl is not None:
            score["pnl_usdc"] += float(pnl)
            score["pnl_seen"] = True
            score["scored"] = True
    for score in scores.values():
        settled = score["wins"] + score["losses"]
        score["win_rate"] = round(score["wins"] / settled, 4) if settled else None
        score["pnl_usdc"] = round(score["pnl_usdc"], 6) if score["pnl_seen"] else None
    return scores


def _strategy_window_candidate_summaries(candidate_specs, records, *, now_dt, filters, timeseries):
    window_records = _strategy_records_for_filters(records, now_dt=now_dt, filters=filters)
    scores = _strategy_window_scores(timeseries)
    summaries = []
    for spec in candidate_specs:
        row = _summarize_strategy_candidate(spec, window_records, now_dt=now_dt, scored_by_candidate={})
        score = scores.get(spec["candidate_id"])
        if isinstance(score, dict) and score.get("scored"):
            row["wins"] = score["wins"]
            row["losses"] = score["losses"]
            row["win_rate"] = score["win_rate"]
            row["pnl_usdc"] = score["pnl_usdc"]
            row["scoring_status"] = "scored"
        else:
            row["wins"] = None
            row["losses"] = None
            row["win_rate"] = None
            row["pnl_usdc"] = None
        summaries.append(row)
    return summaries


def _strategy_window_coverage(now_dt, filters, *, first_signal):
    requested_start = _strategy_window_start(now_dt, filters["window"])
    requested_days = {"today": 1.0, "24h": 1.0, "7d": 7.0, "14d": 14.0}.get(filters["window"])
    _, day_tz = _dashboard_trading_day_timezone()
    data_start = _ensure_aware_utc(first_signal)
    effective_start = data_start
    partial = False
    if requested_start is not None:
        if data_start is None:
            effective_start = requested_start
        elif data_start > requested_start:
            effective_start = data_start
            partial = True
        else:
            effective_start = requested_start
    covered_days = 0.0
    if effective_start is not None:
        covered_days = round(max(0.0, (now_dt - effective_start).total_seconds() / 86400.0), 2)
    score_status = "partial" if partial else "complete"
    if partial:
        score_label = f"Partial {covered_days:.2f}d"
    elif requested_days is not None:
        score_label = f"Complete {requested_days:.0f}d"
    else:
        score_label = "All collected"
    return {
        "window": filters["window"],
        "requested_days": requested_days,
        "requested_start_at": _iso_utc(requested_start),
        "data_start_at": _iso_utc(data_start),
        "effective_start_at": _iso_utc(effective_start),
        "covered_days": covered_days,
        "partial": partial,
        "score_status": score_status,
        "score_label": score_label,
        "day_tz": day_tz,
        "time_basis": "live_trading_day",
    }


def _summarize_strategy_candidate(candidate_spec, records, *, now_dt, scored_by_candidate):
    candidate_id = candidate_spec["candidate_id"]
    candidate_records = [
        record for record in records
        if isinstance(record, dict) and str(record.get("candidate_id") or "") == candidate_id
    ]
    passed_records = [record for record in candidate_records if _candidate_passed(record)]
    long_count = sum(1 for record in passed_records if _candidate_side(record) == "LONG")
    short_count = sum(1 for record in passed_records if _candidate_side(record) == "SHORT")
    timestamps = [_strategy_record_ts(record) for record in candidate_records]
    latest_at, latest_age_seconds = _latest_timestamp_summary(timestamps, now_dt)
    passed = len(passed_records)
    evaluated = len(candidate_records)
    scored = scored_by_candidate.get(candidate_id)
    return {
        "candidate_id": candidate_id,
        "label": candidate_spec["label"],
        "config_path": candidate_spec["config_path"],
        "evaluated": evaluated,
        "passed": passed,
        "hold": max(0, evaluated - passed),
        "pass_rate": round(passed / evaluated, 4) if evaluated else 0.0,
        "long": long_count,
        "short": short_count,
        "candidate_only": sum(1 for record in candidate_records if record.get("candidate_only")),
        "same_side_overlap": sum(1 for record in candidate_records if record.get("same_side_overlap")),
        "live_signal_filtered": sum(1 for record in candidate_records if record.get("live_signal_filtered")),
        "latest_signal_at": latest_at,
        "latest_signal_age_seconds": latest_age_seconds,
        "minimum_ready": passed >= STRATEGY_COMPARE_MIN_PASSED,
        "preferred_ready": passed >= STRATEGY_COMPARE_PREFERRED_PASSED,
        "minimum_passed_signal_target": STRATEGY_COMPARE_MIN_PASSED,
        "preferred_passed_signal_target": STRATEGY_COMPARE_PREFERRED_PASSED,
        "scoring_status": "scored" if isinstance(scored, dict) else "pending_official_scoring",
        "scored": scored if isinstance(scored, dict) else None,
    }


def _strategy_live_prediction_summary(now_dt):
    records = [
        record for record in _tail_jsonl(_live_formal_prediction_history_path(), limit=5000)
        if isinstance(record, dict)
    ]
    timestamps = [_strategy_record_ts(record) for record in records]
    latest_at, latest_age_seconds = _latest_timestamp_summary(timestamps, now_dt)
    would_place = sum(1 for record in records if bool(record.get("would_place_order")))
    submitted = sum(1 for record in records if bool(record.get("submitted")))
    sync = _live_order_sync_summary()
    return {
        "strategy_id": "round2_current_formal",
        "label": "Round2 Current Live Baseline",
        "params_path": "data/config/aligned_prod_current_next_chainlink_round2_drawdown_density_live_params.json",
        "prediction_rows": len(records),
        "would_place": would_place,
        "submitted": submitted,
        "pass_rate": round(would_place / len(records), 4) if records else 0.0,
        "latest_prediction_at": latest_at,
        "latest_prediction_age_seconds": latest_age_seconds,
        "order_sync_ok": bool(sync.get("ok")),
        "order_sync_fresh": bool(sync.get("fresh")),
        "order_sync_age_seconds": sync.get("age_seconds"),
        "notes": ["preview/no-submit mode"] if submitted == 0 else ["real submitted rows detected"],
    }


def _strategy_recent_signal(record):
    candidate = record.get("candidate") if isinstance(record.get("candidate"), dict) else {}
    return {
        "created_at": record.get("created_at"),
        "entry_ts": record.get("entry_ts"),
        "settle_ts": record.get("settle_ts"),
        "market_slug": record.get("market_slug"),
        "candidate_id": record.get("candidate_id"),
        "submitted": bool(record.get("submitted")),
        "no_submit": bool(record.get("no_submit")),
        "passed": bool(candidate.get("passed")),
        "side": candidate.get("side"),
        "action": candidate.get("action"),
        "same_side_overlap": bool(record.get("same_side_overlap")),
        "candidate_only": bool(record.get("candidate_only")),
        "live_signal_filtered": bool(record.get("live_signal_filtered")),
        "p5_up": record.get("p5_up"),
        "p1_up": record.get("p1_up"),
        "p4_up": record.get("p4_up"),
        "reason_code": candidate.get("reason_code"),
    }


def _strategy_trade_entry_ts(record):
    for key in ("entry_ts", "source_prediction_created_at", "created_at"):
        parsed = _parse_dt(record.get(key))
        if parsed is not None:
            return parsed
    return None


def _strategy_trade_side(record):
    for key in ("side", "direction", "action", "token_outcome", "outcome"):
        text = str(record.get(key) or "").strip().upper()
        if not text:
            continue
        if text in {"LONG", "UP", "BUY_UP"} or text.endswith("_UP"):
            return "LONG"
        if text in {"SHORT", "DOWN", "BUY_DOWN"} or text.endswith("_DOWN"):
            return "SHORT"
    return ""


def _strategy_trade_key(record):
    entry_ts = _parse_dt(record.get("entry_ts"))
    settle_ts = _parse_dt(record.get("settle_ts"))
    if entry_ts is None or settle_ts is None:
        return None
    return (_iso_utc(entry_ts), _iso_utc(settle_ts))


def _strategy_scored_bucket_trade_key(record):
    market_key = _strategy_trade_key(record)
    if market_key is not None:
        return market_key
    entry_ts = _strategy_trade_entry_ts(record)
    side = _strategy_trade_side(record)
    if entry_ts is None or side not in {"LONG", "SHORT"}:
        return None
    return (_iso_utc(entry_ts), side)


def _strategy_trade_in_window(record, *, now_dt, filters):
    ts = _strategy_trade_entry_ts(record)
    if ts is None:
        return False
    start = _strategy_window_start(now_dt, filters["window"])
    return start is None or ts.astimezone(timezone.utc) >= start


def _strategy_live_reference_source(record):
    for key in ("reference_price_source", "settlement_source", "market_result_source", "fill_source"):
        value = str(record.get(key) or "").strip()
        if value:
            return value
    return ""


def _strategy_live_reference_source_ok(record):
    return _strategy_live_reference_source(record).lower().startswith("chainlink_datastreams")


def _strategy_explicit_pnl(record):
    for key in ("pnl_usdc", "realized_pnl", "net_pnl", "pnl"):
        value = _num(record.get(key))
        if value is not None:
            return value
    return None


def _strategy_filled_size(record):
    return _float_value(record, "filled_size", "fill_size", "size_matched", "matched_size", "filledSize", "sizeMatched") or 0.0


def _strategy_fill_price(record):
    return _float_value(record, "average_fill_price", "avg_fill_price", "filled_avg_price", "price", "limit_price")


def _strategy_is_final_live_settlement(record):
    status = str(record.get("status") or "").upper()
    if status in {"SETTLED", "WON", "LOST", "CLOSED"}:
        return True
    return _strategy_explicit_pnl(record) is not None and _record_won(record) is not None


def _strategy_binary_option_pnl(won, *, size_shares, maker_price):
    if won is None or size_shares is None or maker_price is None:
        return None
    if won:
        return round(float(size_shares) * (1.0 - float(maker_price)), 6)
    return round(-float(size_shares) * float(maker_price), 6)


def _strategy_live_actual_pnl(record):
    explicit = _strategy_explicit_pnl(record)
    if explicit is not None:
        return round(float(explicit), 6)
    return _strategy_binary_option_pnl(
        _record_won(record),
        size_shares=_strategy_filled_size(record),
        maker_price=_strategy_fill_price(record),
    ) or 0.0


def _strategy_live_normalized_pnl(record, *, size_shares, maker_price):
    return _strategy_binary_option_pnl(
        _record_won(record),
        size_shares=size_shares,
        maker_price=maker_price,
    ) or 0.0


def _strategy_scored_pnl(record, *, default_size_shares, default_maker_price):
    explicit = _strategy_explicit_pnl(record)
    if explicit is not None:
        return round(float(explicit), 6)
    return _strategy_binary_option_pnl(
        _record_won(record),
        size_shares=_float_value(record, "size_shares", "order_size_shares", "size") or default_size_shares,
        maker_price=_float_value(record, "maker_price", "price", "limit_price") or default_maker_price,
    ) or 0.0


def _strategy_group_records_by_trade_key(records, *, keep="all", key_func=None):
    key_func = key_func or _strategy_trade_key
    grouped = {}
    for record in records:
        key = key_func(record)
        if key is not None:
            if keep == "first" and key in grouped:
                continue
            grouped.setdefault(key, []).append(record)
    return grouped


def _strategy_live_records_for_match(*, now_dt, filters):
    records = _rows_from_ledger_payload(_read_json(_live_real_ledger_path()))
    matched = []
    for record in records:
        if not _strategy_is_final_live_settlement(record):
            continue
        if not _strategy_live_reference_source_ok(record):
            continue
        if _strategy_filled_size(record) <= 0 and _strategy_explicit_pnl(record) is None:
            continue
        if _strategy_trade_key(record) is None:
            continue
        if not _strategy_trade_in_window(record, now_dt=now_dt, filters=filters):
            continue
        matched.append(record)
    return matched


def _strategy_scored_records_for_match(scored, *, now_dt, filters):
    rows = scored.get("trades") if isinstance(scored, dict) and isinstance(scored.get("trades"), list) else []
    return [
        row for row in rows
        if isinstance(row, dict)
        and _strategy_trade_key(row) is not None
        and _strategy_trade_in_window(row, now_dt=now_dt, filters=filters)
    ]


def _strategy_first_won(rows):
    for row in rows or []:
        won = _record_won(row)
        if won is not None:
            return won
    return None


def _strategy_first_side(rows):
    for row in rows or []:
        side = _strategy_trade_side(row)
        if side in {"LONG", "SHORT"}:
            return side
    return ""


def _strategy_live_scored_segment_summary(keys, *, live_by_key, scored_by_key, size_shares, maker_price):
    keys = sorted(keys)
    wins = 0
    losses = 0
    live_actual_pnl = 0.0
    live_normalized_pnl = 0.0
    scored_pnl = 0.0
    for key in keys:
        live_rows = live_by_key.get(key, [])
        scored_rows = scored_by_key.get(key, [])
        won = _strategy_first_won(live_rows) if live_rows else _strategy_first_won(scored_rows)
        if won is True:
            wins += 1
        elif won is False:
            losses += 1
        for row in live_rows:
            live_actual_pnl += _strategy_live_actual_pnl(row)
        if live_rows:
            live_normalized_pnl += _strategy_live_normalized_pnl(
                live_rows[0],
                size_shares=size_shares,
                maker_price=maker_price,
            )
        for row in scored_rows:
            scored_pnl += _strategy_scored_pnl(
                row,
                default_size_shares=size_shares,
                default_maker_price=maker_price,
            )
    settled = len(keys)
    return {
        "settled": settled,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / settled, 4) if settled else None,
        "live_actual_pnl": round(live_actual_pnl, 6),
        "live_normalized_pnl": round(live_normalized_pnl, 6),
        "scored_pnl": round(scored_pnl, 6),
    }


def _strategy_live_matched_comparison(candidate_specs, *, now_dt, filters, scored_by_candidate, scored_path):
    ledger_path = _live_real_ledger_path()
    ledger_available = ledger_path.exists()
    scored_payload = _strategy_scored_summary_payload(scored_path)
    scoring_defaults = _strategy_live_scoring_defaults(scored_payload)
    size_shares = _num(scoring_defaults.get("size_shares")) or 5.0
    maker_price = _num(scoring_defaults.get("maker_price")) or 0.49
    live_records = _strategy_live_records_for_match(now_dt=now_dt, filters=filters)
    live_by_key = _strategy_group_records_by_trade_key(live_records)
    live_keys = set(live_by_key)
    rows = []
    for spec in candidate_specs:
        candidate_id = spec["candidate_id"]
        scored = scored_by_candidate.get(candidate_id)
        scored_available = isinstance(scored, dict)
        scored_records = _strategy_scored_records_for_match(scored or {}, now_dt=now_dt, filters=filters)
        scored_by_key = _strategy_group_records_by_trade_key(scored_records, keep="first")
        scored_keys = set(scored_by_key)
        if scored_available:
            overlap_keys = live_keys & scored_keys
            live_only_keys = live_keys - scored_keys
            scored_only_keys = scored_keys - live_keys
        else:
            overlap_keys = set()
            live_only_keys = set()
            scored_only_keys = set()
        won_mismatches = sum(
            1 for key in overlap_keys
            if _strategy_first_won(live_by_key.get(key, [])) != _strategy_first_won(scored_by_key.get(key, []))
        )
        side_mismatches = sum(
            1 for key in overlap_keys
            if _strategy_first_side(live_by_key.get(key, [])) != _strategy_first_side(scored_by_key.get(key, []))
        )
        all_live_summary = _strategy_live_scored_segment_summary(
            live_keys if scored_available else set(),
            live_by_key=live_by_key,
            scored_by_key={},
            size_shares=size_shares,
            maker_price=maker_price,
        )
        all_scored_summary = _strategy_live_scored_segment_summary(
            scored_keys,
            live_by_key={},
            scored_by_key=scored_by_key,
            size_shares=size_shares,
            maker_price=maker_price,
        )
        rows.append(
            {
                "candidate_id": candidate_id,
                "label": spec["label"],
                "scored_available": scored_available,
                "overlap_count": len(overlap_keys),
                "live_only_count": len(live_only_keys),
                "scored_only_count": len(scored_only_keys),
                "side_mismatches": side_mismatches,
                "won_mismatches": won_mismatches,
                "pnl_delta": (
                    round(
                        all_live_summary["live_actual_pnl"]
                        - all_scored_summary["scored_pnl"],
                        6,
                    )
                    if ledger_available and scored_available
                    else None
                ),
                "all_live": all_live_summary,
                "all_scored": all_scored_summary,
                "overlap": _strategy_live_scored_segment_summary(
                    overlap_keys,
                    live_by_key=live_by_key,
                    scored_by_key=scored_by_key,
                    size_shares=size_shares,
                    maker_price=maker_price,
                ),
                "live_only": _strategy_live_scored_segment_summary(
                    live_only_keys,
                    live_by_key=live_by_key,
                    scored_by_key={},
                    size_shares=size_shares,
                    maker_price=maker_price,
                ),
                "scored_only": _strategy_live_scored_segment_summary(
                    scored_only_keys,
                    live_by_key={},
                    scored_by_key=scored_by_key,
                    size_shares=size_shares,
                    maker_price=maker_price,
                ),
            }
        )
    return {
        "available": ledger_available and scored_path.exists(),
        "ledger_path": str(ledger_path),
        "scored_summary_path": str(scored_path),
        "live_reference_source": "chainlink_datastreams*",
        "size_shares": size_shares,
        "maker_price": maker_price,
        "live_order_count": len(live_records),
        "live_settled_count": len(live_keys),
        "candidates": rows,
    }


def _strategy_difference_payload(*, candidate_id, filters, now=None):
    now_dt = _ensure_aware_utc(now) or datetime.now(timezone.utc)
    ledger_path = _live_real_ledger_path()
    formal_path = _live_formal_prediction_history_path()
    scored_path = _candidate_no_submit_official_truth_scored_summary_path()
    ledger_available = ledger_path.exists()
    formal_available = formal_path.exists()
    scored_summary_available = scored_path.exists()
    scored_by_candidate = _strategy_scored_summary_by_candidate(scored_path)
    scored = scored_by_candidate.get(candidate_id)
    scored_available = scored_summary_available and isinstance(scored, dict)
    source_warnings = []
    if not ledger_available:
        source_warnings.append("live ledger missing; live PnL is unknown")
    if not formal_available:
        source_warnings.append(
            "formal prediction history missing; simulated-only causes may be unknown"
        )
    if not scored_available:
        source_warnings.append("candidate scored summary missing")
    if not scored_available:
        reconcilable = False
        empty_summary = summarize_difference_rows([], reconcilable=reconcilable)
        return {
            "generated_at": _iso_utc(now_dt),
            "candidate_id": candidate_id,
            "available": False,
            "warnings": source_warnings,
            "data_quality": {
                "reconcilable": reconcilable,
                "excluded_live_reference_count": 0,
                "conflicting_scored_markets": [],
            },
            "filters": filters,
            "overall_summary": empty_summary,
            "filtered_summary": empty_summary,
            "pagination": {
                "limit": filters["limit"],
                "offset": filters["offset"],
                "returned": 0,
                "total": 0,
            },
            "rows": [],
        }

    scored_records = scored.get("trades") if isinstance(scored.get("trades"), list) else []
    result = reconcile_strategy_orders(
        live_records=_rows_from_ledger_payload(_read_json(ledger_path)),
        formal_predictions=_tail_jsonl(
            formal_path,
            limit=STRATEGY_COMPARISON_SIGNAL_READ_LIMIT,
        ),
        scored_records=[row for row in scored_records if isinstance(row, dict)],
        start_at=_strategy_window_start(now_dt, filters["window"]),
        end_at=now_dt,
        ledger_available=ledger_available,
        scored_available=scored_available,
    )
    reconcilable = result["data_quality"]["reconcilable"]
    overall_rows = result["rows"]
    overall_summary = summarize_difference_rows(
        overall_rows,
        reconcilable=reconcilable,
    )
    filtered_rows = [
        row
        for row in overall_rows
        if (filters["include_matched"] or row["primary_type"] != "matched")
        and (not filters["types"] or row["primary_type"] in filters["types"])
    ]
    filtered_summary = summarize_difference_rows(
        filtered_rows,
        reconcilable=reconcilable,
    )
    sorted_rows = sorted(
        filtered_rows,
        key=lambda row: str(row.get("entry_ts") or ""),
        reverse=True,
    )
    offset = filters["offset"]
    limit = filters["limit"]
    rows = sorted_rows[offset:offset + limit]
    warnings = list(result["warnings"])
    for warning in source_warnings:
        if warning not in warnings:
            warnings.append(warning)
    return {
        "generated_at": _iso_utc(now_dt),
        "candidate_id": candidate_id,
        "available": ledger_available and scored_available,
        "warnings": warnings,
        "data_quality": result["data_quality"],
        "filters": filters,
        "overall_summary": overall_summary,
        "filtered_summary": filtered_summary,
        "pagination": {
            "limit": limit,
            "offset": offset,
            "returned": len(rows),
            "total": len(filtered_rows),
        },
        "rows": rows,
    }


def _strategy_comparison_payload(*, now=None, args=None):
    now_dt = _ensure_aware_utc(now) or datetime.now(timezone.utc)
    filters = _strategy_compare_filters(args or {})
    signals_path = _candidate_no_submit_official_truth_signals_path()
    latest_path = _candidate_no_submit_official_truth_latest_path()
    daily_summary_path = _candidate_no_submit_official_truth_daily_summary_path()
    scored_path = _candidate_no_submit_official_truth_scored_summary_path()
    records = [
        record for record in _tail_jsonl(signals_path, limit=20000)
        if isinstance(record, dict) and record.get("type") == "candidate_no_submit_signal"
    ]
    warnings = []
    if not signals_path.exists():
        warnings.append("candidate_no_submit_official_truth_signals.jsonl missing")
    if not latest_path.exists():
        warnings.append("candidate_no_submit_official_truth_latest.json missing")
    timestamps = [_strategy_record_ts(record) for record in records]
    first_signal = min((ts for ts in timestamps if ts is not None), default=None)
    latest_signal_at, latest_signal_age_seconds = _latest_timestamp_summary(timestamps, now_dt)
    latest_signal = _parse_dt(latest_signal_at) if latest_signal_at else None
    collection_days = 0.0
    if first_signal is not None and latest_signal is not None:
        collection_days = round(max(0.0, (latest_signal - first_signal).total_seconds() / 86400.0), 4)
    scored_by_candidate = _strategy_scored_summary_by_candidate(scored_path)
    candidates = [
        _summarize_strategy_candidate(spec, records, now_dt=now_dt, scored_by_candidate=scored_by_candidate)
        for spec in STRATEGY_COMPARE_CANDIDATES
    ]
    timeseries = _strategy_timeseries(records, now_dt=now_dt, filters=filters, scored_by_candidate=scored_by_candidate)
    window_candidates = _strategy_window_candidate_summaries(
        STRATEGY_COMPARE_CANDIDATES,
        records,
        now_dt=now_dt,
        filters=filters,
        timeseries=timeseries,
    )
    window_coverage = _strategy_window_coverage(now_dt, filters, first_signal=first_signal)
    if window_coverage["partial"]:
        warnings.append(
            f"selected {filters['window']} window only has {window_coverage['covered_days']:.2f}d of no-submit collection"
        )
    min_passed = min((row["passed"] for row in candidates), default=0)
    recent_records = sorted(
        records,
        key=lambda record: _strategy_record_ts(record) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )[:10]
    return {
        "generated_at": _iso_utc(now_dt),
        "ok": bool(records) and signals_path.exists() and latest_path.exists(),
        "warnings": warnings,
        "collection": {
            "signals_path": signals_path.as_posix(),
            "latest_path": latest_path.as_posix(),
            "daily_summary_path": daily_summary_path.as_posix(),
            "scored_summary_path": scored_path.as_posix(),
            "signals_file_exists": signals_path.exists(),
            "latest_file_exists": latest_path.exists(),
            "daily_summary_exists": daily_summary_path.exists(),
            "scored_summary_exists": scored_path.exists(),
            "first_signal_at": _iso_utc(first_signal),
            "latest_signal_at": latest_signal_at,
            "latest_signal_age_seconds": latest_signal_age_seconds,
            "collection_days": collection_days,
            "minimum_days_target": STRATEGY_COMPARE_MIN_DAYS,
            "preferred_days_target": STRATEGY_COMPARE_PREFERRED_DAYS,
            "minimum_passed_signal_target": STRATEGY_COMPARE_MIN_PASSED,
            "preferred_passed_signal_target": STRATEGY_COMPARE_PREFERRED_PASSED,
            "minimum_ready": collection_days >= STRATEGY_COMPARE_MIN_DAYS and min_passed >= STRATEGY_COMPARE_MIN_PASSED,
            "preferred_ready": collection_days >= STRATEGY_COMPARE_PREFERRED_DAYS and min_passed >= STRATEGY_COMPARE_PREFERRED_PASSED,
        },
        "live": _strategy_live_prediction_summary(now_dt),
        "candidates": candidates,
        "window_candidates": window_candidates,
        "window_coverage": window_coverage,
        "live_matched": _strategy_live_matched_comparison(
            STRATEGY_COMPARE_CANDIDATES,
            now_dt=now_dt,
            filters=filters,
            scored_by_candidate=scored_by_candidate,
            scored_path=scored_path,
        ),
        "filters": filters,
        "timeseries": timeseries,
        "recent_signals": [_strategy_recent_signal(record) for record in recent_records],
    }


def _live_pause_flag_summary():
    path = KRONOS_RUNTIME_DIR / "live_paused.flag"
    report_mtime, age_seconds = _path_age(path if path.exists() else None)
    reason = ""
    if path.exists():
        payload = _read_json(path)
        if isinstance(payload, dict):
            reason = str(payload.get("reason") or "")
        if not reason:
            try:
                reason = path.read_text(encoding="utf-8-sig").strip()
            except OSError:
                reason = ""
    return {
        "paused": path.exists(),
        "path": str(path),
        "reason": reason or None,
        "mtime": report_mtime,
        "age_seconds": age_seconds,
    }


def _live_trading_status(live_real_summary, sync_summary):
    live_real_summary = live_real_summary if isinstance(live_real_summary, dict) else {}
    sync_summary = sync_summary if isinstance(sync_summary, dict) else _live_order_sync_summary()
    formal = live_real_summary.get("formal") if isinstance(live_real_summary.get("formal"), dict) else {}
    runtime = live_real_summary.get("runtime") if isinstance(live_real_summary.get("runtime"), dict) else {}
    child_runtime = runtime.get("child_runtime") if isinstance(runtime.get("child_runtime"), dict) else {}
    risk = live_real_summary.get("risk") if isinstance(live_real_summary.get("risk"), dict) else {}
    risk_checks = risk.get("checks") if isinstance(risk.get("checks"), list) else []
    orders = live_real_summary.get("orders") if isinstance(live_real_summary.get("orders"), dict) else {}
    pause = _live_pause_flag_summary()

    formal_max_age_seconds = int(os.environ.get("DASHBOARD_FORMAL_LIVE_MAX_AGE_SECONDS", "420"))
    formal_age_seconds = formal.get("report_age_seconds")
    formal_fresh = (
        bool(formal.get("available"))
        and formal_age_seconds is not None
        and formal_age_seconds <= formal_max_age_seconds
    )
    supervisor_running = bool(runtime.get("running"))
    child_running = bool(child_runtime.get("running")) if child_runtime else supervisor_running
    formal_running = supervisor_running and child_running
    risk_blockers = [
        {
            "key": str(check.get("key") or ""),
            "label": str(check.get("label") or check.get("key") or "Risk"),
            "severity": str(check.get("severity") or "risk"),
            "value": check.get("value"),
            "expected": check.get("expected"),
            "action": "Pause trading and review live risk",
        }
        for check in risk_checks
        if isinstance(check, dict) and check.get("severity") == "risk" and not check.get("ok")
    ]
    blockers = []
    if pause["paused"]:
        blockers.append({
            "key": "live_paused",
            "label": "Live paused",
            "severity": "critical",
            "value": pause.get("reason") or "pause flag exists",
            "action": "Remove pause flag only after review",
        })
    if not bool(formal.get("available")):
        blockers.append({
            "key": "formal_live_missing",
            "label": "Formal live report missing",
            "severity": "critical",
            "value": "missing",
            "action": "Start formal live supervisor",
        })
    elif not formal_fresh:
        blockers.append({
            "key": "formal_live_stale",
            "label": "Formal live report stale",
            "severity": "critical",
            "value": formal_age_seconds,
            "expected": f"<= {formal_max_age_seconds}s",
            "action": "Check formal live child",
        })
    if not formal_running:
        blockers.append({
            "key": "formal_live_process",
            "label": "Formal live process",
            "severity": "critical",
            "value": "not running",
            "action": "Restart formal live supervisor",
        })
    if not sync_summary.get("running"):
        blockers.append({
            "key": "live_order_sync_process",
            "label": "Order sync process",
            "severity": "critical",
            "value": "not running",
            "action": "Restart live order sync loop",
        })
    if not sync_summary.get("fresh"):
        blockers.append({
            "key": "live_order_sync_stale",
            "label": "Order sync report stale",
            "severity": "critical",
            "value": sync_summary.get("age_seconds"),
            "expected": f"<= {sync_summary.get('max_age_seconds')}s",
            "action": "Check live order sync loop",
        })
    if not sync_summary.get("ok"):
        blockers.append({
            "key": "live_order_sync_ok",
            "label": "Order sync ok",
            "severity": "critical",
            "value": sync_summary.get("reconcile_errors"),
            "expected": "ok=true and reconcile_errors=0",
            "action": "Review live order sync report",
        })
    blockers.extend(risk_blockers)

    if pause["paused"]:
        status = "paused"
        label = "Paused"
    elif risk_blockers:
        status = "risk_blocked"
        label = "Risk blocked"
    elif blockers:
        status = "needs_review"
        label = "Review"
    else:
        status = "running"
        label = "Running"

    return {
        "ok": not blockers,
        "status": status,
        "label": label,
        "next_action": blockers[0]["action"] if blockers else "Monitoring formal live",
        "blockers": blockers,
        "formal": {
            "available": bool(formal.get("available")),
            "running": formal_running,
            "supervisor_running": supervisor_running,
            "child_running": child_running,
            "fresh": formal_fresh,
            "age_seconds": formal_age_seconds,
            "max_age_seconds": formal_max_age_seconds,
            "report": formal.get("report"),
            "latest_action": formal.get("latest_action"),
            "latest_reason": formal.get("latest_reason") or formal.get("reason"),
            "terminal_reason": formal.get("terminal_reason"),
        },
        "sync": {
            "available": bool(sync_summary.get("available")),
            "running": bool(sync_summary.get("running")),
            "fresh": bool(sync_summary.get("fresh")),
            "ok": bool(sync_summary.get("ok")),
            "age_seconds": sync_summary.get("age_seconds"),
            "max_age_seconds": sync_summary.get("max_age_seconds"),
            "report": sync_summary.get("report"),
            "reconcile_errors": sync_summary.get("reconcile_errors"),
            "open_orders": sync_summary.get("open_orders"),
            "settlement_source": sync_summary.get("settlement_source"),
        },
        "risk": {
            "ok": bool(risk.get("ok")),
            "blockers": risk_blockers,
            "reason": (risk.get("resilience") or {}).get("reason") if isinstance(risk.get("resilience"), dict) else None,
        },
        "pause": pause,
        "orders": {
            "open_or_pending": int(_num(orders.get("open_or_pending")) or 0),
            "total": int(_num(orders.get("total")) or 0),
            "filled_size": _num(orders.get("filled_size")) or 0.0,
        },
    }


def _live_restart_contract_path():
    configured = (os.environ.get("DASHBOARD_LIVE_RESTART_CONTRACT") or "").strip()
    if configured:
        path = Path(configured)
        return path if path.is_absolute() else KRONOS_CONFIG_DIR / path
    return KRONOS_CONFIG_DIR / "aligned_prod_current_next_chainlink_30d30d_m049_shares5_live_params.json"


def _live_restart_contract():
    payload = _read_json(_live_restart_contract_path()) or {}
    contract = payload.get("live_restart_contract") if isinstance(payload.get("live_restart_contract"), dict) else {}
    return contract if isinstance(contract, dict) else {}


def _latest_formal_supervisor_line():
    path = KRONOS_LOG_DIR / "prediction_bound_live_formal_supervisor_latest.log"
    if not path.exists():
        return ""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        if "formal_live_supervisor started" in line:
            return line
    return lines[-1] if lines else ""


def _formal_supervisor_running():
    return bool(_process_summary(["run_prediction_bound_live_formal_supervisor.ps1"]).get("running"))


def _extract_formal_supervisor_limits():
    if not _formal_supervisor_running():
        return {}
    line = _latest_formal_supervisor_line()
    if not line:
        return {}
    mapping = {
        "max_daily_loss": ("max_daily_loss_usdc", float),
        "max_daily_trades": ("max_daily_trades", int),
        "max_consecutive_losses": ("max_consecutive_losses", int),
        "max_open_or_pending": ("max_open_or_pending_orders", int),
    }
    limits = {}
    for key, (target, caster) in mapping.items():
        match = re.search(rf"\b{re.escape(key)}=([0-9.]+)", line)
        if not match:
            continue
        try:
            limits[target] = caster(float(match.group(1))) if caster is int else caster(match.group(1))
        except (TypeError, ValueError):
            continue
    return limits


def _coerce_supervisor_value(value):
    text = str(value or "").strip()
    if text.lower() in {"true", "false"}:
        return text.lower() == "true"
    try:
        if re.fullmatch(r"[+-]?\d+", text):
            return int(text)
        if re.fullmatch(r"[+-]?(?:\d+\.\d*|\d*\.\d+)", text):
            return float(text)
    except ValueError:
        pass
    return text


def _extract_formal_supervisor_config():
    if not _formal_supervisor_running():
        return {}
    line = _latest_formal_supervisor_line()
    if not line:
        return {}
    return {
        match.group(1): _coerce_supervisor_value(match.group(2))
        for match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)=([^\s]+)", line)
    }


def _risk_control_value(supervisor_config, formal_limits, supervisor_keys, formal_keys):
    for key in supervisor_keys:
        if key in supervisor_config and supervisor_config.get(key) is not None:
            return supervisor_config.get(key)
    for key in formal_keys:
        if key in formal_limits and formal_limits.get(key) is not None:
            return formal_limits.get(key)
    return None


def _live_risk_controls(formal_report=None, limits_override=None):
    supervisor_config = _extract_formal_supervisor_config()
    formal_report = formal_report if isinstance(formal_report, dict) else (_live_formal_report()[1] or {})
    formal_limits = limits_override if isinstance(limits_override, dict) else _live_formal_risk_limits()
    summary = {
        "order_size_shares": _risk_control_value(supervisor_config, formal_limits, ("order_size_shares",), ("order_size_shares",)),
        "min_price": _risk_control_value(supervisor_config, formal_limits, ("min_price",), ("min_price",)),
        "active_max_price": _risk_control_value(supervisor_config, formal_limits, ("active_max_price",), ("active_max_price",)),
        "hard_max_price": _risk_control_value(supervisor_config, formal_limits, ("max_price",), ("max_price", "hard_max_price")),
        "max_notional_usdc": _risk_control_value(supervisor_config, formal_limits, ("max_notional", "max_notional_usdc"), ("max_notional_usdc", "max_notional")),
        "max_daily_loss_usdc": _risk_control_value(supervisor_config, formal_limits, ("max_daily_loss", "max_daily_loss_usdc"), ("max_daily_loss_usdc", "max_daily_loss")),
        "max_daily_trades": _risk_control_value(supervisor_config, formal_limits, ("max_daily_trades",), ("max_daily_trades",)),
        "max_consecutive_losses": _risk_control_value(supervisor_config, formal_limits, ("max_consecutive_losses",), ("max_consecutive_losses",)),
        "max_open_or_pending_orders": _risk_control_value(supervisor_config, formal_limits, ("max_open_or_pending", "max_open_or_pending_orders"), ("max_open_or_pending_orders", "max_open_or_pending")),
        "max_smoke_drawdown_usdc": _risk_control_value(supervisor_config, formal_limits, ("max_smoke_drawdown", "max_smoke_drawdown_usdc"), ("max_smoke_drawdown_usdc", "max_smoke_drawdown")),
        "same_direction_loss_cooldown_count": _risk_control_value(supervisor_config, formal_limits, ("same_direction_loss_cooldown_count",), ("same_direction_loss_cooldown_count",)),
        "same_direction_loss_cooldown_minutes": _risk_control_value(supervisor_config, formal_limits, ("same_direction_loss_cooldown_minutes",), ("same_direction_loss_cooldown_minutes",)),
        "signal_max_age_seconds": _risk_control_value(supervisor_config, formal_limits, ("signal_max_age", "signal_max_age_seconds"), ("signal_max_age_seconds", "signal_max_age")),
        "reference_price_source": _risk_control_value(supervisor_config, formal_limits, ("reference_price_source",), ("reference_price_source",)),
        "execution_market_shift": _risk_control_value(supervisor_config, formal_limits, ("execution_market_shift",), ("execution_market_shift",)),
    }
    prediction = formal_report.get("prediction") if isinstance(formal_report.get("prediction"), dict) else {}
    if summary["execution_market_shift"] is None:
        summary["execution_market_shift"] = prediction.get("execution_market_shift")
    cooldown_count = summary["same_direction_loss_cooldown_count"]
    cooldown_minutes = summary["same_direction_loss_cooldown_minutes"]
    return {
        "source": "supervisor_log" if supervisor_config else "formal_report" if formal_limits else "unavailable",
        "summary": summary,
        "groups": {
            "order": {
                "label": "Order limits",
                "order_size_shares": summary["order_size_shares"],
                "max_notional_usdc": summary["max_notional_usdc"],
                "max_open_or_pending_orders": summary["max_open_or_pending_orders"],
            },
            "price": {
                "label": "Price band",
                "min_price": summary["min_price"],
                "active_max_price": summary["active_max_price"],
                "hard_max_price": summary["hard_max_price"],
            },
            "daily_stop": {
                "label": "Daily stops",
                "max_daily_loss_usdc": summary["max_daily_loss_usdc"],
                "max_daily_trades": summary["max_daily_trades"],
                "max_consecutive_losses": summary["max_consecutive_losses"],
                "max_smoke_drawdown_usdc": summary["max_smoke_drawdown_usdc"],
            },
            "cooldown": {
                "label": "Same-direction cooldown",
                "count": cooldown_count,
                "minutes": cooldown_minutes,
                "value": f"{cooldown_count} losses -> {cooldown_minutes}m"
                if cooldown_count is not None and cooldown_minutes is not None
                else None,
            },
            "execution": {
                "label": "Execution",
                "signal_max_age_seconds": summary["signal_max_age_seconds"],
                "reference_price_source": summary["reference_price_source"],
                "execution_market_shift": summary["execution_market_shift"],
            },
        },
    }


def _live_formal_risk_limits(fallback=None):
    limits = dict(RISK_LIMITS)
    if isinstance(fallback, dict):
        limits.update({key: fallback[key] for key in limits if key in fallback})
    effective_keys = (
        "order_size_shares",
        "min_price",
        "active_max_price",
        "max_price",
        "max_notional_usdc",
        "max_daily_loss_usdc",
        "max_daily_trades",
        "max_consecutive_losses",
        "max_open_or_pending_orders",
        "max_smoke_drawdown_usdc",
        "same_direction_loss_cooldown_count",
        "same_direction_loss_cooldown_minutes",
        "signal_max_age_seconds",
        "reference_price_source",
        "execution_market_shift",
    )
    risk_config = _read_json(KRONOS_CONFIG_DIR / "live_formal_risk_limits.json") or {}
    if isinstance(risk_config, dict):
        for key in effective_keys:
            if key in risk_config and risk_config.get(key) is not None:
                limits[key] = risk_config[key]
    formal_path, _ = _live_formal_report()
    if formal_path.exists():
        formal_report = _read_json(formal_path) or {}
        contract = _live_restart_contract()
        contract_mapping = {
            "max_daily_loss_usdc": "max_daily_loss_usdc",
            "max_daily_trades": "max_daily_trades",
            "max_consecutive_losses": "max_consecutive_losses",
            "max_open_or_pending_orders": "max_open_or_pending_orders",
        }
        for source_key, target_key in contract_mapping.items():
            if source_key in contract:
                limits[target_key] = contract[source_key]
        effective = (
            formal_report.get("effective_trade_config")
            if isinstance(formal_report.get("effective_trade_config"), dict)
            else {}
        )
        for key in effective_keys:
            if key in effective and effective.get(key) is not None:
                limits[key] = effective[key]
    limits.update(_extract_formal_supervisor_limits())
    return limits


def _live_real_summary(limits_override=None, current_balance=None):
    ledger_path = _live_real_ledger_path()
    records = _rows_from_ledger_payload(_read_json(ledger_path))
    formal = _live_formal_summary()
    _, formal_report = _live_formal_report()
    risk_controls = _live_risk_controls(formal_report, limits_override=limits_override)
    risk_limits_override = dict(limits_override) if isinstance(limits_override, dict) else {}
    control_summary = (
        risk_controls.get("summary")
        if isinstance(risk_controls.get("summary"), dict)
        else {}
    )
    for key in RISK_LIMITS:
        if control_summary.get(key) is not None:
            risk_limits_override[key] = control_summary.get(key)
    risk_resilience = _risk_resilience_summary_from_records(records, controls=risk_controls)
    risk = _attach_risk_resilience(_risk_summary_from_records(
        records,
        inputs={"ledger": str(ledger_path), "record_count": len(records)},
        limits_override=risk_limits_override,
    ), risk_resilience)
    soak = _live_soak_summary()
    runtime = _live_process_runtime(
        formal_available=bool(formal.get("available")),
        formal=formal,
        soak=soak,
    )
    return {
        "ledger": str(ledger_path),
        "ledger_exists": ledger_path.exists(),
        "runtime": runtime,
        "formal": formal,
        "soak": soak,
        "orders": _order_status_summary(records),
        "stats": _settled_stats_summary(records),
        "market_results": _market_result_stats_summary(records),
        "risk": risk,
        "risk_controls": risk_controls,
        "risk_resilience": risk_resilience,
        "equity": _equity_summary_from_records(
            records,
            current_balance=current_balance,
            source="live_real_orders",
        ),
        "weekly_pnl_calendar": _weekly_pnl_calendar_from_records(records),
        "latest_order": _latest_record(records),
        "order_records": _live_order_records(records),
        "account_activity": _polymarket_account_activity_summary(cash_balance=current_balance),
    }


def _paper_monitor_summary(checkpoint=None, risk_summary=None, today_summary=None):
    checkpoint_path = _paper_checkpoint_path()
    ledger_path = _paper_ledger_path()
    checkpoint = checkpoint if isinstance(checkpoint, dict) else (_read_json(checkpoint_path) or {})
    checkpoint_mtime, checkpoint_age_seconds = _path_age(checkpoint_path if checkpoint_path.exists() else None)
    open_orders = checkpoint.get("open_orders") if isinstance(checkpoint.get("open_orders"), list) else []
    pending_orders = checkpoint.get("pending_orders") if isinstance(checkpoint.get("pending_orders"), list) else []
    trades = checkpoint.get("trades") if isinstance(checkpoint.get("trades"), list) else []
    return {
        "run_source": _paper_run_source(),
        "source_label": _source_label(_paper_run_source()),
        "checkpoint": str(checkpoint_path),
        "ledger": str(ledger_path),
        "checkpoint_exists": checkpoint_path.exists(),
        "checkpoint_mtime": checkpoint_mtime,
        "checkpoint_age_seconds": checkpoint_age_seconds,
        "runtime": _process_summary(["run_paper_aligned_prod.py"]),
        "balance": checkpoint.get("balance"),
        "orders": {
            "open": len(open_orders),
            "pending": len(pending_orders),
            "settled": len([record for record in trades if isinstance(record, dict) and _is_settled_record(record)]),
            "total_trades": len(trades),
        },
        "stats": _settled_stats_summary(trades),
        "signals": _paper_signal_stats_summary(),
        "equity": _equity_summary_from_records(
            trades,
            current_balance=checkpoint.get("balance"),
            prefer_balance_after=True,
            source=_paper_run_source(),
        ),
        "risk": risk_summary if isinstance(risk_summary, dict) else _live_risk_summary(checkpoint),
        "today": today_summary if isinstance(today_summary, dict) else _live_today_summary(checkpoint),
    }


def _maker_target_price():
    for key in ("DASHBOARD_MAKER_TARGET_PRICE", "MAKER_TARGET_PRICE"):
        try:
            return round(float(os.environ.get(key, "")), 4)
        except ValueError:
            continue
    return 0.49


def _usage(numerator, denominator):
    try:
        denominator = float(denominator)
        numerator = float(numerator)
    except (TypeError, ValueError):
        return 0.0
    if denominator <= 0:
        return 0.0
    return round(max(0.0, min(1.0, numerator / denominator)), 4)


def _is_today_record(record, day):
    ts = _record_ts(record)
    return _timestamp_in_dashboard_day(ts, day)


def _is_today_event(event, day):
    ts = _parse_dt(event.get("_t") or event.get("ts") or event.get("created_at"))
    return _timestamp_in_dashboard_day(ts, day)


def _is_decision_event(event):
    return (
        event.get("type") == "decision"
        or "filt" in event
        or "filt_passed" in event
        or "executable" in event
    )


def _decision_passed(event):
    if event.get("passed") is True or event.get("would_place_order") is True:
        return True
    if event.get("filt") is True or event.get("filt_passed") in (1, True):
        return True
    action = str(event.get("action") or "").upper()
    return event.get("executable") is True and action != "HOLD"


def _signal_stats_summary(events):
    decisions = [event for event in (events or []) if isinstance(event, dict) and _is_decision_event(event)]
    passed = sum(1 for event in decisions if _decision_passed(event))
    total = len(decisions)
    return {
        "total": total,
        "passed": passed,
        "blocked": max(total - passed, 0),
        "pass_rate": round(passed / total, 4) if total else 0.0,
    }


def _paper_signal_stats_summary(limit=5000):
    events = _tail_jsonl(_events_checkpoint_path(), limit=limit)
    decisions = [event for event in events if isinstance(event, dict) and _is_decision_event(event)]
    if not decisions:
        decisions = [
            event for event in _tail_source_events(_paper_run_source(), limit=limit)
            if isinstance(event, dict) and _is_decision_event(event)
        ]
    return _signal_stats_summary(decisions)


def _signal_action_counts(events):
    counts = {"buy_up": 0, "buy_down": 0, "hold": 0, "other": 0}
    for event in events:
        action = str(event.get("action") or "").upper()
        if action == "BUY_UP":
            counts["buy_up"] += 1
        elif action == "BUY_DOWN":
            counts["buy_down"] += 1
        elif action == "HOLD":
            counts["hold"] += 1
        else:
            counts["other"] += 1
    return counts


def _top_signal_block_reason(events):
    counts = {}
    for event in events:
        if _decision_passed(event):
            continue
        reason = str(
            event.get("block_reason")
            or event.get("reason_code")
            or event.get("reason")
            or ""
        ).strip()
        if not reason:
            continue
        counts[reason] = counts.get(reason, 0) + 1
    if not counts:
        return "", 0
    reason, count = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0]
    return reason, count


def _dashboard_today_trade_records(day, source="live"):
    if not DB_PATH.exists():
        return []
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT pnl, won, regime, direction, size, entry_bar, settle_bar, created_at, details
               FROM trades
               WHERE source=? AND won!=-1
               ORDER BY settle_bar ASC, entry_bar ASC""",
            (source,),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass

    records = []
    for row in rows:
        if not _timestamp_in_dashboard_day(_parse_dt(row["settle_bar"]), day):
            continue
        won = row["won"]
        records.append(
            {
                "status": "SETTLED",
                "pnl": row["pnl"],
                "won": True if won == 1 else False if won == 0 else None,
                "regime": row["regime"],
                "direction": row["direction"],
                "size": row["size"],
                "entry_ts": row["entry_bar"],
                "settle_ts": row["settle_bar"],
                "settled_at": row["settle_bar"],
                "created_at": row["created_at"],
                "details": row["details"],
            }
        )
    return records


def _dashboard_today_decision_events(day, source="live"):
    if not DB_PATH.exists():
        return []
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT kline_n, action, dir5, dir4, regime, filt_passed, reason, created_at, details
               FROM events
               WHERE source=?
               ORDER BY created_at ASC, kline_n ASC""",
            (source,),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass

    events = []
    for row in rows:
        if not _timestamp_in_dashboard_day(_parse_dt(row["created_at"]), day):
            continue
        passed = row["filt_passed"]
        events.append(
            {
                "type": "decision",
                "n": row["kline_n"],
                "action": row["action"],
                "dir5": row["dir5"],
                "dir4": row["dir4"],
                "regime": row["regime"],
                "filt_passed": passed,
                "filt": True if passed == 1 else False if passed == 0 else None,
                "executable": True if passed == 1 else False if passed == 0 else None,
                "reason": row["reason"],
                "_t": row["created_at"],
                "details": row["details"],
            }
        )
    return events


def _source_events_for_day(source, day, limit=1000):
    return [
        event for event in _tail_source_events(source, limit=limit)
        if isinstance(event, dict) and _is_today_event(event, day)
    ]


def _live_today_summary(checkpoint=None, risk_summary=None, dryrun_summary=None, *, now=None):
    checkpoint = checkpoint if isinstance(checkpoint, dict) else (_read_json(_paper_checkpoint_path()) or {})
    risk_summary = risk_summary if isinstance(risk_summary, dict) else _live_risk_summary(checkpoint)
    dryrun_summary = dryrun_summary if isinstance(dryrun_summary, dict) else _live_dryrun_ledger_summary()
    now_dt = _ensure_aware_utc(now) or datetime.now(timezone.utc)
    day_info = _dashboard_day_info(now=now_dt)
    today = day_info["day"]

    trades = [item for item in checkpoint.get("trades", []) or [] if isinstance(item, dict)]
    pending = [item for item in checkpoint.get("pending_orders", []) or [] if isinstance(item, dict)]
    open_orders = [item for item in checkpoint.get("open_orders", []) or [] if isinstance(item, dict)]
    todays_settled = [
        record for record in trades
        if _is_settled_record(record) and _is_today_record(record, today)
    ]
    if not todays_settled:
        todays_settled = _dashboard_today_trade_records(today)
    wins = sum(1 for record in todays_settled if _record_won(record) is True)
    losses = sum(1 for record in todays_settled if _record_won(record) is False)
    daily_pnl = round(sum(_record_pnl(record) for record in todays_settled), 8)

    events = [
        event for event in _tail_jsonl(_events_checkpoint_path(), limit=1000)
        if isinstance(event, dict) and _is_decision_event(event) and _is_today_event(event, today)
    ]
    if not events:
        events = _dashboard_today_decision_events(today)
    passed = sum(1 for event in events if _decision_passed(event))
    blocked = max(0, len(events) - passed)
    action_counts = _signal_action_counts(events)
    top_block_reason, top_block_count = _top_signal_block_reason(events)

    audit_events = [
        event for event in _tail_audit_events(limit=1000)
        if isinstance(event, dict) and _is_today_event(event, today)
    ]
    maker_quality = _maker_quality_report(
        events,
        audit_events,
        limit=20,
        shadow_events=_source_events_for_day("shadow_live", today, limit=1000),
    )
    maker_summary = maker_quality.get("summary") or {}
    metrics = risk_summary.get("metrics") or {}
    limits = risk_summary.get("limits") or {}
    max_daily_loss = abs(float(limits.get("max_daily_loss_usdc", 0) or 0))
    daily_trades_metric = metrics.get("daily_trades")
    if not daily_trades_metric and todays_settled:
        daily_trades_metric = len(todays_settled)
    dryrun_today = dryrun_summary.get("today") or _live_dryrun_today_summary(now=now_dt)
    latest_signal_at, latest_signal_age_seconds = _latest_timestamp_summary(
        [_parse_dt(event.get("_t") or event.get("ts") or event.get("created_at")) for event in events],
        now_dt,
    )
    latest_trade_at, latest_trade_age_seconds = _latest_timestamp_summary(
        [_record_ts(record) for record in todays_settled],
        now_dt,
    )
    latest_order_at, latest_order_age_seconds = _latest_timestamp_summary(
        [_record_ts(record) for record in [*pending, *open_orders] if _is_today_record(record, today)],
        now_dt,
    )
    latest_dryrun_at = dryrun_today.get("latest_at")
    latest_dryrun_age_seconds = dryrun_today.get("latest_age_seconds")

    return {
        "day": today.isoformat(),
        "day_tz": day_info["day_tz"],
        "day_start_utc": day_info["start_utc_iso"],
        "day_end_utc": day_info["end_utc_iso"],
        "day_utc": today.isoformat(),
        "signals": {
            "total": len(events),
            "passed": passed,
            "blocked": blocked,
            "pass_rate": round(passed / len(events), 4) if events else 0.0,
            "buy_up": action_counts["buy_up"],
            "buy_down": action_counts["buy_down"],
            "hold": action_counts["hold"],
            "other": action_counts["other"],
            "top_block_reason": top_block_reason,
            "top_block_count": top_block_count,
        },
        "trades": {
            "settled": len(todays_settled),
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / len(todays_settled), 4) if todays_settled else 0.0,
            "pnl_usdc": daily_pnl,
            "pending": len(pending),
            "open": len(open_orders),
        },
        "risk_usage": {
            "daily_loss": _usage(max(0.0, -float(metrics.get("daily_pnl_usdc", daily_pnl) or 0)), max_daily_loss),
            "daily_trades": _usage(daily_trades_metric, limits.get("max_daily_trades")),
            "loss_streak": _usage(metrics.get("consecutive_losses", 0), limits.get("max_consecutive_losses")),
            "open_or_pending": _usage(metrics.get("open_or_pending_orders", len(open_orders) + len(pending)), limits.get("max_open_or_pending_orders")),
        },
        "maker": {
            "target_price": _maker_target_price(),
            "observed_avg_target_price": maker_summary.get("avg_target_price"),
            "buy_one_rate": maker_summary.get("avg_buy_one_rate", 0),
            "blocks": maker_summary.get("total_blocks", 0),
            "api_errors": maker_summary.get("api_error_count", 0),
        },
        "dryrun": dryrun_today,
        "activity": {
            "latest_signal_at": latest_signal_at,
            "latest_signal_age_seconds": latest_signal_age_seconds,
            "latest_trade_at": latest_trade_at,
            "latest_trade_age_seconds": latest_trade_age_seconds,
            "latest_order_at": latest_order_at,
            "latest_order_age_seconds": latest_order_age_seconds,
            "latest_dryrun_at": latest_dryrun_at,
            "latest_dryrun_age_seconds": latest_dryrun_age_seconds,
        },
    }


def _live_real_today_summary(live_real_summary, base_today=None, *, now=None):
    base = dict(base_today) if isinstance(base_today, dict) else _live_today_summary(now=now)
    now_dt = _ensure_aware_utc(now) or datetime.now(timezone.utc)
    day_info = _dashboard_day_info(now=now_dt)
    today = day_info["day"]
    risk = live_real_summary.get("risk") if isinstance(live_real_summary, dict) else {}
    risk = risk if isinstance(risk, dict) else {}
    metrics = risk.get("metrics") if isinstance(risk.get("metrics"), dict) else {}
    limits = risk.get("limits") if isinstance(risk.get("limits"), dict) else {}
    orders = live_real_summary.get("orders") if isinstance(live_real_summary, dict) else {}
    orders = orders if isinstance(orders, dict) else {}
    records = live_real_summary.get("order_records") if isinstance(live_real_summary, dict) else []
    records = [record for record in records or [] if isinstance(record, dict)]
    todays_settled = [
        record for record in records
        if _is_settled_record(record) and _is_today_record(record, today)
    ]
    wins = int(metrics.get("wins", sum(1 for record in todays_settled if _record_won(record) is True)) or 0)
    losses = int(metrics.get("losses", sum(1 for record in todays_settled if _record_won(record) is False)) or 0)
    settled = int(metrics.get("daily_trades", len(todays_settled)) or 0)
    win_rate = metrics.get("win_rate")
    if win_rate is None:
        win_rate = round(wins / settled, 4) if settled else 0.0
    daily_pnl = metrics.get("daily_pnl_usdc")
    if daily_pnl is None:
        daily_pnl = round(sum(_record_pnl(record) for record in todays_settled), 8)
    open_or_pending = int(metrics.get("open_or_pending_orders", orders.get("open_or_pending", 0)) or 0)
    latest_trade_at, latest_trade_age_seconds = _latest_timestamp_summary(
        [_record_ts(record) for record in todays_settled],
        now_dt,
    )

    base["trades"] = {
        "settled": settled,
        "wins": wins,
        "losses": losses,
        "win_rate": round(float(win_rate or 0.0), 4),
        "pnl_usdc": round(float(daily_pnl or 0.0), 8),
        "pending": 0,
        "open": open_or_pending,
    }
    max_daily_loss = abs(float(limits.get("max_daily_loss_usdc", 0) or 0))
    base["risk_usage"] = {
        "daily_loss": _usage(max(0.0, -float(metrics.get("daily_pnl_usdc", daily_pnl) or 0)), max_daily_loss),
        "daily_trades": _usage(metrics.get("daily_trades", settled), limits.get("max_daily_trades")),
        "loss_streak": _usage(metrics.get("consecutive_losses", 0), limits.get("max_consecutive_losses")),
        "open_or_pending": _usage(open_or_pending, limits.get("max_open_or_pending_orders")),
    }
    activity = dict(base.get("activity") or {})
    activity.update({
        "latest_trade_at": latest_trade_at,
        "latest_trade_age_seconds": latest_trade_age_seconds,
    })
    base["activity"] = activity
    return base


def _readiness_summary(checklist):
    total = len(checklist)
    passed = sum(1 for item in checklist if item.get("ok"))
    by_severity = {}
    severity_order = {"critical": 0, "funding": 1, "risk": 2, "runtime": 3}
    failed = []
    for index, item in enumerate(checklist):
        if item.get("ok"):
            continue
        severity = str(item.get("severity") or "check")
        by_severity[severity] = by_severity.get(severity, 0) + 1
        failed.append(
            {
                "key": item.get("key"),
                "label": item.get("label"),
                "severity": severity,
                "value": item.get("value"),
                "expected": item.get("expected"),
                "action": _readiness_action(item.get("key")),
                "original_index": index,
            }
        )
    failed = sorted(
        failed,
        key=lambda item: (severity_order.get(item["severity"], 9), int(item.get("original_index") or 0)),
    )
    return {
        "ready": total > 0 and passed == total,
        "total": total,
        "passed": passed,
        "blockers": total - passed,
        "by_severity": by_severity,
        "critical_blockers": by_severity.get("critical", 0),
        "funding_blockers": by_severity.get("funding", 0),
        "risk_blockers": by_severity.get("risk", 0),
        "top_blockers": failed[:6],
    }


def _readiness_action(key):
    actions = {
        "clob_authenticated": "Run CLOB read-only audit",
        "account_read_ok": "Check CLOB account read access",
        "allowance_read_ok": "Check CLOB allowance read access",
        "minimum_balance": "Fund USDC balance",
        "minimum_allowance": "Approve USDC allowance",
        "open_orders_clear": "Cancel or reconcile open orders",
        "dryrun_no_submitted_orders": "Keep dry-run from submitting orders",
        "market_data_fresh": "Refresh Chainlink live price feed",
        "live_trade_gate_available": "Generate live trade gate report",
        "live_trade_gate_ready": "Clear live gate blockers",
        "live_preflight_available": "Run live preflight chain",
        "live_preflight_chain_ok": "Clear preflight blockers",
        "live_preflight_fresh": "Refresh live preflight chain",
        "live_preflight_no_submission": "Use preview-only preflight",
        "no_critical_alerts": "Review live alert report",
        "risk_daily_loss": "Reset or lower daily loss exposure",
        "risk_daily_trades": "Wait for daily trade limit reset",
        "risk_consecutive_losses": "Pause after loss streak",
        "risk_open_or_pending": "Clear open or pending orders",
        "risk_smoke_drawdown": "Pause trading and review live drawdown",
        "risk_same_direction_cooldown": "Wait for direction cooldown to expire",
        "risk_order_lifecycle": "Reconcile uncertain live order lifecycle",
    }
    return actions.get(str(key or ""), "Review readiness check")


def _first_order_rail(checklist):
    checks = {item.get("key"): item for item in checklist if item.get("key")}

    def make_stage(key, label, required_keys, action, prior_complete):
        blockers = [checks[item_key] for item_key in required_keys if not checks.get(item_key, {}).get("ok")]
        complete = not blockers
        status = "complete" if complete else "blocked" if prior_complete else "waiting"
        return {
            "key": key,
            "label": label,
            "status": status,
            "action": "Verified" if complete else action,
            "blocker_keys": [item.get("key") for item in blockers],
            "blockers": [item.get("label") for item in blockers],
        }

    stages = []
    prior_complete = True
    stage_specs = [
        (
            "dry_run",
            "Dry-run audit",
            ["real_orders_locked", "dryrun_no_submitted_orders"],
            "Keep dry-run from submitting orders",
        ),
        (
            "read_only_audit",
            "CLOB read-only audit",
            ["clob_authenticated", "account_read_ok", "allowance_read_ok", "open_orders_clear", "market_data_fresh"],
            "Run CLOB read-only audit",
        ),
        (
            "funding",
            "Funding and allowance",
            ["minimum_balance", "minimum_allowance"],
            "Fund USDC and approve allowance",
        ),
        (
            "preflight",
            "Live preflight",
            [
                "live_trade_gate_available",
                "live_trade_gate_ready",
                "live_preflight_available",
                "live_preflight_chain_ok",
                "live_preflight_fresh",
                "live_preflight_no_submission",
                "risk_daily_loss",
                "risk_daily_trades",
                "risk_consecutive_losses",
                "risk_open_or_pending",
                "risk_smoke_drawdown",
                "risk_same_direction_cooldown",
                "risk_order_lifecycle",
            ],
            "Run live preflight chain",
        ),
    ]
    for key, label, required_keys, action in stage_specs:
        stage = make_stage(key, label, required_keys, action, prior_complete)
        stages.append(stage)
        prior_complete = prior_complete and stage["status"] == "complete"

    guarded_status = "manual" if prior_complete else "waiting"
    stages.append(
        {
            "key": "tiny_guarded_order",
            "label": "Tiny guarded order",
            "status": guarded_status,
            "action": "Wait for explicit user confirmation",
            "blocker_keys": [],
            "blockers": [],
            "requires_confirmation": True,
        }
    )
    stages.append(
        {
            "key": "settle_tracking",
            "label": "Cancel and settle tracking",
            "status": "waiting",
            "action": "Track auto-cancel and settlement",
            "blocker_keys": [],
            "blockers": [],
        }
    )
    current = next((stage for stage in stages if stage["status"] in {"blocked", "manual"}), stages[-1])
    return {
        "current_key": current["key"],
        "ready_for_manual_confirmation": guarded_status == "manual",
        "stages": stages,
    }


def _operator_summary(readiness_summary, first_order_rail):
    stages = first_order_rail.get("stages") or []
    current_key = first_order_rail.get("current_key")
    current_stage = next((stage for stage in stages if stage.get("key") == current_key), stages[-1] if stages else {})
    ready = bool(first_order_rail.get("ready_for_manual_confirmation"))
    stage_status = str(current_stage.get("status") or "")
    if ready:
        status = "manual_confirmation"
    elif stage_status == "blocked":
        status = "blocked"
    else:
        status = "waiting"

    blocker_keys = current_stage.get("blocker_keys") or []
    blockers = current_stage.get("blockers") or []
    top_blocker = (readiness_summary.get("top_blockers") or [{}])[0] or {}
    primary_blocker_key = blocker_keys[0] if blocker_keys else top_blocker.get("key")
    primary_blocker = blockers[0] if blockers else top_blocker.get("label")
    if status == "manual_confirmation":
        primary_blocker_key = None
        primary_blocker = None

    return {
        "ready": ready,
        "status": status,
        "current_stage_key": current_stage.get("key") or current_key,
        "current_stage_label": current_stage.get("label") or "Live safety",
        "next_action": current_stage.get("action") or top_blocker.get("action") or "Review readiness",
        "primary_blocker_key": primary_blocker_key,
        "primary_blocker": primary_blocker,
        "readiness_passed": readiness_summary.get("passed", 0),
        "readiness_total": readiness_summary.get("total", 0),
        "critical_blockers": readiness_summary.get("critical_blockers", 0),
        "funding_blockers": readiness_summary.get("funding_blockers", 0),
        "risk_blockers": readiness_summary.get("risk_blockers", 0),
    }


def _clob_quote_probe_summary(probe):
    return {
        "direction": str(probe.get("direction") or probe.get("side") or probe.get("outcome") or "").upper(),
        "ok": bool(probe.get("ok")),
        "quote_executable": bool(probe.get("quote_executable")),
        "best_bid": _num(probe.get("best_bid")),
        "best_ask": _num(probe.get("best_ask")),
        "price": _num(probe.get("price")),
        "token_id": str(probe.get("token_id") or probe.get("outcome_token_id") or ""),
        "reason": str(probe.get("reason") or probe.get("block_reason") or ""),
        "error": str(probe.get("error") or ""),
    }


def _clob_readonly_status(
    *,
    available,
    fresh,
    require_quote_probes=True,
    authenticated,
    account_read_ok,
    allowance_read_ok,
    open_orders_read_ok,
    open_orders_count,
    market_probe_count,
    quote_executable_count,
    blockers,
):
    if not available:
        return "missing_report", "Run live trade gate report"
    if not fresh:
        return "stale_report", "Refresh live trade gate report"
    if not authenticated:
        return "auth_blocked", "Run CLOB read-only audit"
    if not account_read_ok:
        return "account_read_blocked", "Check CLOB account read access"
    if not allowance_read_ok:
        return "allowance_read_blocked", "Check CLOB allowance read access"
    if not open_orders_read_ok:
        return "open_orders_read_blocked", "Check CLOB open orders access"
    if open_orders_count != 0:
        return "open_orders_present", "Cancel or reconcile open orders"
    if require_quote_probes and market_probe_count == 0:
        return "missing_quote_probes", "Run live trade gate report"
    if require_quote_probes and quote_executable_count != market_probe_count:
        return "quote_probe_blocked", "Review CLOB quote probes"
    if require_quote_probes and blockers:
        return "live_gate_blocked", "Clear live gate blockers"
    return "ready", "Ready for guarded preflight"


def _safety_report_summary():
    allowance_path, allowance_report = _latest_json_report("polymarket_clob_*allowance*_audit*.json")
    if not allowance_report:
        allowance_path, allowance_report = _latest_json_report("polymarket_clob_account_read_audit*.json")
    gate_path, gate_report = _latest_json_report("live_trade_gate*.json")
    preflight_path, preflight_report = _latest_json_report("live_preflight_chain*.json")
    dryrun_summary = _live_dryrun_ledger_summary()
    preflight_summary = _live_preflight_chain_summary(preflight_path, preflight_report)
    preflight_components = preflight_report.get("components") if isinstance(preflight_report.get("components"), dict) else {}
    preflight_risk_component = (
        preflight_components.get("risk")
        if isinstance(preflight_components.get("risk"), dict)
        else {}
    )
    preflight_risk_metrics = (
        preflight_risk_component.get("metrics")
        if isinstance(preflight_risk_component.get("metrics"), dict)
        else {}
    )
    preflight_risk_limits = (
        preflight_risk_metrics.get("limits")
        if isinstance(preflight_risk_metrics.get("limits"), dict)
        else None
    )
    preflight_gate_report = (
        preflight_components.get("gate")
        if isinstance(preflight_components.get("gate"), dict)
        else {}
    )
    gate_mtime = gate_path.stat().st_mtime if gate_path else -1
    preflight_mtime = preflight_path.stat().st_mtime if preflight_path else -1
    use_preflight_gate = bool(preflight_gate_report) and preflight_mtime >= gate_mtime
    effective_gate_path = preflight_path if use_preflight_gate else gate_path
    effective_gate_report = preflight_gate_report if use_preflight_gate else gate_report
    live_gate_summary = _live_gate_report_summary(effective_gate_path, effective_gate_report)
    gate_account = effective_gate_report.get("account") if isinstance(effective_gate_report.get("account"), dict) else {}
    gate_funding = (
        effective_gate_report.get("funding_requirements")
        if isinstance(effective_gate_report.get("funding_requirements"), dict)
        else {}
    )
    _, effective_gate_age_seconds = _path_age(effective_gate_path)
    account_refresh = {}
    refreshed_account = {}
    account_source = "live_gate_report"
    if (
        effective_gate_path
        and effective_gate_age_seconds is not None
        and effective_gate_age_seconds > CLOB_READONLY_MAX_AGE_SECONDS
    ):
        account_refresh = _live_clob_account_snapshot()
        candidate_account = account_refresh.get("account") if isinstance(account_refresh, dict) else {}
        if account_refresh.get("ok") and isinstance(candidate_account, dict):
            refreshed_account = candidate_account
            gate_account = {**gate_account, **refreshed_account}
            account_source = str(account_refresh.get("source") or "live_clob_account_refresh")
    execution_path, execution_report = _latest_json_report("live_dryrun_execution_summary_latest.json")
    checkpoint = _read_json(_paper_checkpoint_path()) or {}

    balance_check = _check_status(effective_gate_report, "balance_meets_minimum") or _check_status(allowance_report, "minimum_balance")
    allowance_check = _check_status(effective_gate_report, "allowance_meets_minimum") or _check_status(allowance_report, "minimum_allowance")
    auth_check = _check_status(allowance_report, "readonly_authenticated_client") or _check_status(effective_gate_report, "clob_account_authenticated")
    if auth_check is None:
        auth_check = _check_status(allowance_report, "clob_private_key_present")
    balance_read = _check_status(allowance_report, "readonly_get_balance") or _check_status(effective_gate_report, "account_balance_read_ok")
    allowance_read = _check_status(allowance_report, "readonly_get_balance_allowance") or _check_status(effective_gate_report, "account_allowance_read_ok")
    open_orders_read = _check_status(effective_gate_report, "account_open_orders_read_ok")

    network_calls = (allowance_report.get("network") or {}).get("calls") or []
    balance_allowance_call = next(
        (call for call in network_calls if call.get("name") == "get_balance_allowance"),
        {},
    )
    orders_call = next((call for call in network_calls if call.get("name") == "get_orders"), {})

    paper_run_source = _paper_run_source()
    clob_open_orders = _clob_open_order_summaries(
        gate_account.get("open_orders")
        if isinstance(gate_account.get("open_orders"), list)
        else checkpoint.get("open_orders", []) or []
    )
    open_orders_count = int(gate_account.get("open_orders_count", len(clob_open_orders)) or 0)
    if clob_open_orders:
        open_orders_count = len(clob_open_orders)
    balance_value = gate_account.get("usdc_balance", balance_allowance_call.get("balance"))
    min_allowance_value = gate_account.get("min_allowance", balance_allowance_call.get("min_allowance"))
    allowance_count = gate_account.get("allowance_count", balance_allowance_call.get("allowance_count"))
    min_allowance_spender = gate_account.get("min_allowance_spender", balance_allowance_call.get("min_allowance_spender", ""))
    if refreshed_account:
        balance_shortfall = _funding_shortfall(gate_funding.get("required_min_balance_usdc"), balance_value)
        smoke_notional_shortfall = _funding_shortfall(gate_funding.get("required_smoke_notional_usdc"), balance_value)
        allowance_shortfall = _funding_shortfall(gate_funding.get("required_min_allowance_usdc"), min_allowance_value)
        gate_funding = {
            **gate_funding,
            "balance_shortfall_usdc": balance_shortfall,
            "smoke_notional_shortfall_usdc": smoke_notional_shortfall,
            "allowance_shortfall_usdc": allowance_shortfall,
            "funding_ready": (
                balance_shortfall == 0.0
                and smoke_notional_shortfall == 0.0
                and allowance_shortfall == 0.0
            ),
        }

    clob_authenticated = bool(auth_check and auth_check.get("ok"))
    account_read_ok = bool(balance_read and balance_read.get("ok"))
    allowance_read_ok = bool(allowance_read and allowance_read.get("ok"))
    balance_ok = bool(balance_check and balance_check.get("ok"))
    allowance_ok = bool(allowance_check and allowance_check.get("ok"))
    open_orders_read_ok = bool(orders_call.get("ok")) or bool(open_orders_read and open_orders_read.get("ok"))
    if refreshed_account:
        clob_authenticated = bool(refreshed_account.get("authenticated"))
        account_read_ok = bool(refreshed_account.get("balance_read_ok"))
        allowance_read_ok = bool(refreshed_account.get("allowance_read_ok"))
        open_orders_read_ok = bool(refreshed_account.get("orders_read_ok"))
        balance_ok = gate_funding.get("balance_shortfall_usdc") == 0.0
        allowance_ok = gate_funding.get("allowance_shortfall_usdc") == 0.0
    paper_risk_summary = _live_risk_summary(checkpoint)
    paper_today_summary = _live_today_summary(checkpoint, paper_risk_summary, dryrun_summary)
    live_real_summary = _live_real_summary(
        limits_override=_live_formal_risk_limits(preflight_risk_limits),
        current_balance=balance_value,
    )
    real_order_mode = _live_real_order_mode(live_real_summary)
    real_orders_enabled = bool(real_order_mode.get("enabled"))
    today_summary = (
        _live_real_today_summary(live_real_summary, paper_today_summary)
        if real_orders_enabled
        else paper_today_summary
    )
    run_source = "live_real" if real_orders_enabled else paper_run_source
    source_label = _source_label(run_source)
    mode = "dry-run" if "dryrun" in run_source else "live" if run_source.startswith("live_") else "paper"
    live_real_risk_summary = live_real_summary["risk"]
    live_order_sync_summary = _live_order_sync_summary()
    live_trading_status = _live_trading_status(live_real_summary, live_order_sync_summary)
    paper_monitor_summary = _paper_monitor_summary(checkpoint, paper_risk_summary, paper_today_summary)
    market_data_summary = _btc_live_market_data_summary()
    alert_summary = _live_alert_summary()

    checklist = [
        {
            "key": "real_orders_locked",
            "label": "Real orders locked",
            "ok": not real_orders_enabled,
            "value": "locked" if not real_orders_enabled else "enabled",
            "severity": "critical",
        },
        {
            "key": "no_critical_alerts",
            "label": "No critical alerts",
            "ok": alert_summary["critical_count"] == 0,
            "value": alert_summary["critical_count"],
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
            "value": balance_value,
            "expected": balance_check.get("expected") if balance_check else None,
            "severity": "funding",
        },
        {
            "key": "minimum_allowance",
            "label": "Minimum allowance",
            "ok": allowance_ok,
            "value": min_allowance_value,
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
        {
            "key": "dryrun_no_submitted_orders",
            "label": "Dry-run no submissions",
            "ok": dryrun_summary["submitted_count"] == 0,
            "value": dryrun_summary["submitted_count"],
            "severity": "critical",
        },
        {
            "key": "market_data_fresh",
            "label": "Market data fresh",
            "ok": market_data_summary["ready"],
            "value": market_data_summary.get("price_age_seconds"),
            "expected": f"<= {BTC_LIVE_MAX_PRICE_AGE_SECONDS}s price, <= {BTC_LIVE_MAX_RECEIVED_AGE_SECONDS}s received",
            "severity": "critical",
        },
        {
            "key": "live_trade_gate_available",
            "label": "Live gate report",
            "ok": live_gate_summary["available"],
            "value": "ok" if live_gate_summary["available"] else "missing",
            "severity": "critical",
        },
        {
            "key": "live_trade_gate_ready",
            "label": "Live smoke gate",
            "ok": live_gate_summary["ready_for_live_smoke"],
            "value": len(live_gate_summary["blockers"]),
            "expected": "0 blockers",
            "severity": "critical",
        },
        {
            "key": "live_preflight_available",
            "label": "Live preflight report",
            "ok": preflight_summary["available"],
            "value": "ok" if preflight_summary["available"] else "missing",
            "severity": "critical",
        },
        {
            "key": "live_preflight_chain_ok",
            "label": "Live preflight chain",
            "ok": preflight_summary["ok"],
            "value": len(preflight_summary["blockers"]),
            "expected": "0 blockers",
            "severity": "critical",
        },
        {
            "key": "live_preflight_fresh",
            "label": "Live preflight fresh",
            "ok": preflight_summary["fresh"],
            "value": preflight_summary["age_seconds"],
            "expected": f"<= {LIVE_PREFLIGHT_MAX_AGE_SECONDS}s",
            "severity": "critical",
        },
        {
            "key": "live_preflight_no_submission",
            "label": "Preflight no submission",
            "ok": not preflight_summary["submitted"],
            "value": "submitted" if preflight_summary["submitted"] else "none",
            "severity": "critical",
        },
    ] + live_real_risk_summary["checks"]

    readiness_summary = _readiness_summary(checklist)
    first_order_rail = _first_order_rail(checklist)
    operator_summary = _operator_summary(readiness_summary, first_order_rail)
    market_probe_count = int(live_gate_summary.get("market_probe_count", 0) or 0)
    quote_executable_count = int(live_gate_summary.get("quote_executable_count", 0) or 0)
    gate_market_probes = (
        effective_gate_report.get("market_probes")
        if isinstance(effective_gate_report.get("market_probes"), list)
        else []
    )
    quote_probes = [
        _clob_quote_probe_summary(probe)
        for probe in gate_market_probes
        if isinstance(probe, dict)
    ]
    clob_report_path = effective_gate_path or allowance_path
    account_refresh_dt = _parse_dt(account_refresh.get("created_at")) if refreshed_account else None
    account_refresh_age_seconds = _timestamp_age_seconds(account_refresh_dt) if account_refresh_dt is not None else None
    account_refresh_fresh = (
        bool(refreshed_account)
        and account_refresh_age_seconds is not None
        and account_refresh_age_seconds <= CLOB_READONLY_MAX_AGE_SECONDS
    )
    clob_report_mtime = _iso_utc(account_refresh_dt) if account_refresh_dt is not None else None
    clob_report_age_seconds = account_refresh_age_seconds
    if clob_report_mtime is None and clob_report_path:
        try:
            clob_report_mtime_dt = datetime.fromtimestamp(clob_report_path.stat().st_mtime, tz=timezone.utc)
            clob_report_mtime = clob_report_mtime_dt.isoformat()
            clob_report_age_seconds = max(0, int((datetime.now(timezone.utc) - clob_report_mtime_dt).total_seconds()))
        except OSError:
            clob_report_mtime = None
            clob_report_age_seconds = None
    clob_report_fresh = (
        account_refresh_fresh
        or bool(clob_report_path)
        and clob_report_age_seconds is not None
        and clob_report_age_seconds <= CLOB_READONLY_MAX_AGE_SECONDS
    )
    require_quote_probes = not bool(refreshed_account)
    clob_blockers = [] if refreshed_account else (live_gate_summary.get("blockers", []) or [])
    clob_readonly_available = bool(live_gate_summary.get("available") or allowance_report)
    clob_readonly_ready = (
        clob_authenticated
        and account_read_ok
        and allowance_read_ok
        and open_orders_read_ok
        and open_orders_count == 0
        and (not require_quote_probes or market_probe_count > 0)
        and (not require_quote_probes or quote_executable_count == market_probe_count)
        and clob_report_fresh
        and not clob_blockers
    )
    clob_status_reason, clob_next_action = _clob_readonly_status(
        available=clob_readonly_available,
        fresh=clob_report_fresh,
        require_quote_probes=require_quote_probes,
        authenticated=clob_authenticated,
        account_read_ok=account_read_ok,
        allowance_read_ok=allowance_read_ok,
        open_orders_read_ok=open_orders_read_ok,
        open_orders_count=open_orders_count,
        market_probe_count=market_probe_count,
        quote_executable_count=quote_executable_count,
        blockers=clob_blockers,
    )
    clob_readonly = {
        "available": clob_readonly_available,
        "ready": clob_readonly_ready,
        "status_reason": clob_status_reason,
        "next_action": clob_next_action,
        "report": str(clob_report_path or ""),
        "report_source": account_source,
        "report_mtime": clob_report_mtime,
        "report_age_seconds": clob_report_age_seconds,
        "fresh": clob_report_fresh,
        "max_age_seconds": CLOB_READONLY_MAX_AGE_SECONDS,
        "authenticated": clob_authenticated,
        "account_read_ok": account_read_ok,
        "allowance_read_ok": allowance_read_ok,
        "open_orders_read_ok": open_orders_read_ok,
        "open_orders_count": open_orders_count,
        "open_orders": clob_open_orders,
        "balance": balance_value,
        "min_allowance": min_allowance_value,
        "allowance_count": allowance_count,
        "min_allowance_spender": min_allowance_spender,
        "account_source": account_source,
        "account_refresh": {
            "ok": account_refresh.get("ok"),
            "source": account_refresh.get("source"),
            "created_at": account_refresh.get("created_at"),
            "error": account_refresh.get("error"),
            "authenticated": (account_refresh.get("account") or {}).get("authenticated"),
            "orders_read_ok": (account_refresh.get("account") or {}).get("orders_read_ok"),
            "orders_error": (account_refresh.get("account") or {}).get("orders_error"),
            "orders_result_type": (account_refresh.get("account") or {}).get("orders_result_type"),
            "balance_read_ok": (account_refresh.get("account") or {}).get("balance_read_ok"),
            "balance_error": (account_refresh.get("account") or {}).get("balance_error"),
            "balance_allowance_result_type": (account_refresh.get("account") or {}).get("balance_allowance_result_type"),
            "allowance_read_ok": (account_refresh.get("account") or {}).get("allowance_read_ok"),
            "allowance_error": (account_refresh.get("account") or {}).get("allowance_error"),
        } if account_refresh else {},
        "market_probe_count": market_probe_count,
        "quote_executable_count": quote_executable_count,
        "quote_executable_rate": round(quote_executable_count / market_probe_count, 4) if market_probe_count else 0.0,
        "quote_probes": quote_probes,
        "funding_ready": gate_funding.get("funding_ready"),
        "balance_shortfall_usdc": gate_funding.get("balance_shortfall_usdc"),
        "allowance_shortfall_usdc": gate_funding.get("allowance_shortfall_usdc"),
        "blockers": clob_blockers,
    }
    _, gate_report_age_seconds = _path_age(effective_gate_path)
    gate_report_fresh = (
        bool(gate_path)
        and gate_report_age_seconds is not None
        and gate_report_age_seconds <= CLOB_READONLY_MAX_AGE_SECONDS
    )
    formal_summary = live_real_summary.get("formal") if isinstance(live_real_summary.get("formal"), dict) else {}
    formal_max_age_seconds = int(os.environ.get("DASHBOARD_FORMAL_LIVE_MAX_AGE_SECONDS", "420"))
    formal_age_seconds = formal_summary.get("report_age_seconds")
    formal_fresh = (
        bool(formal_summary.get("available"))
        and formal_age_seconds is not None
        and formal_age_seconds <= formal_max_age_seconds
    )
    account_activity = live_real_summary.get("account_activity") if isinstance(live_real_summary.get("account_activity"), dict) else {}
    account_activity_max_age_seconds = int(os.environ.get("DASHBOARD_ACCOUNT_ACTIVITY_MAX_AGE_SECONDS", "600"))
    account_activity_age_seconds = account_activity.get("report_age_seconds")
    account_activity_fresh = (
        bool(account_activity.get("available"))
        and account_activity_age_seconds is not None
        and account_activity_age_seconds <= account_activity_max_age_seconds
    )
    live_alerts_ready = int(alert_summary.get("critical_count", 0) or 0) == 0
    live_alerts_age_seconds = alert_summary.get("report_age_seconds")
    report_refresh = _report_refresh_summary(
        [
            _report_refresh_item(
                key="formal_live",
                label="Formal Live",
                report=formal_summary.get("report") or "",
                available=bool(formal_summary.get("available")),
                ok=bool(formal_summary.get("available")),
                fresh=formal_fresh,
                age_seconds=formal_age_seconds,
                max_age_seconds=formal_max_age_seconds,
                blockers=[] if formal_summary.get("available") else ["missing_formal_live_report"],
                actions={
                    "missing": "Start formal live runner",
                    "stale": "Check formal live runner",
                    "blocked": "Review formal live report",
                    "ready": "Monitoring formal live",
                },
            ),
            _report_refresh_item(
                key="live_order_sync",
                label="Order Sync",
                report=live_order_sync_summary.get("report") or "",
                available=bool(live_order_sync_summary.get("available")),
                ok=bool(live_order_sync_summary.get("ok")),
                fresh=bool(live_order_sync_summary.get("fresh")),
                age_seconds=live_order_sync_summary.get("age_seconds"),
                max_age_seconds=live_order_sync_summary.get("max_age_seconds"),
                blockers=["reconcile_errors"] if live_order_sync_summary.get("reconcile_errors") else [],
                actions={
                    "missing": "Start live order sync loop",
                    "stale": "Check live order sync loop",
                    "blocked": "Review live order sync report",
                    "ready": "Monitoring live order sync",
                },
            ),
            _report_refresh_item(
                key="polymarket_account_activity",
                label="Account Activity",
                report=account_activity.get("report") or "",
                available=bool(account_activity.get("available")),
                ok=bool(account_activity.get("ok")) or bool(account_activity.get("available")),
                fresh=account_activity_fresh,
                age_seconds=account_activity_age_seconds,
                max_age_seconds=account_activity_max_age_seconds,
                blockers=[] if account_activity.get("available") else ["missing_account_activity"],
                actions={
                    "missing": "Start Polymarket account activity sync",
                    "stale": "Refresh Polymarket account activity sync",
                    "blocked": "Review account activity report",
                    "ready": "Monitoring account activity",
                },
            ),
            _report_refresh_item(
                key="live_alerts",
                label="Live Alerts",
                report=alert_summary.get("report") or "",
                available=True,
                ok=live_alerts_ready,
                fresh=True,
                age_seconds=live_alerts_age_seconds,
                max_age_seconds=None,
                blockers=[item.get("key") or item.get("title") or "critical_alert" for item in (alert_summary.get("active") or []) if item.get("severity") == "critical"],
                actions={
                    "blocked": "Review active live alerts",
                    "ready": "No critical live alerts",
                },
            ),
        ]
    )
    legacy_report_refresh = _report_refresh_summary(
        [
            _report_refresh_item(
                key="live_gate",
                label="Live Gate",
                report=str(effective_gate_path) if effective_gate_path else "",
                available=live_gate_summary["available"],
                ok=live_gate_summary["ready_for_live_smoke"],
                fresh=gate_report_fresh,
                age_seconds=gate_report_age_seconds,
                max_age_seconds=CLOB_READONLY_MAX_AGE_SECONDS,
                blockers=live_gate_summary["blockers"],
                actions={
                    "missing": "Run live trade gate report",
                    "stale": "Refresh live trade gate report",
                    "blocked": "Clear live gate blockers",
                    "ready": "Ready for live preflight",
                },
            ),
            _report_refresh_item(
                key="live_preflight",
                label="Live Preflight",
                report=preflight_summary["report"],
                available=preflight_summary["available"],
                ok=preflight_summary["ok"] and not preflight_summary["submitted"],
                fresh=preflight_summary["fresh"],
                age_seconds=preflight_summary["age_seconds"],
                max_age_seconds=preflight_summary["max_age_seconds"],
                blockers=preflight_summary["blockers"],
                actions={
                    "missing": "Run live preflight chain",
                    "stale": "Refresh live preflight chain",
                    "blocked": "Clear preflight blockers",
                    "ready": "Ready for manual confirmation",
                },
            ),
            _report_refresh_item(
                key="clob_readonly",
                label="CLOB Read-only",
                report=clob_readonly["report"],
                available=clob_readonly_available,
                ok=clob_readonly_ready,
                fresh=clob_report_fresh,
                age_seconds=clob_report_age_seconds,
                max_age_seconds=CLOB_READONLY_MAX_AGE_SECONDS,
                blockers=clob_blockers,
                actions={
                    "missing": clob_next_action,
                    "stale": clob_next_action,
                    "blocked": clob_next_action,
                    "ready": clob_next_action,
                },
            ),
        ]
    )

    return {
        "mode": mode,
        "run_source": run_source,
        "source_label": source_label,
        "real_orders_enabled": real_orders_enabled,
        "kill_switch": {
            "state": "armed" if real_orders_enabled else "locked",
            "env": "YES" if real_order_mode.get("environment_enabled") else "",
            "source": real_order_mode.get("source"),
            "submit": bool(real_order_mode.get("submit")),
            "environment_enabled": bool(real_order_mode.get("environment_enabled")),
        },
        "clob": {
            "authenticated": clob_authenticated,
            "account_read_ok": account_read_ok,
            "allowance_read_ok": allowance_read_ok,
            "open_orders_read_ok": open_orders_read_ok,
            "open_orders_count": open_orders_count,
        },
        "funding": {
            "balance_ok": balance_ok,
            "allowance_ok": allowance_ok,
            "balance": balance_value,
            "account_source": account_source,
            "allowance_count": allowance_count,
            "min_allowance": min_allowance_value,
            "min_allowance_spender": min_allowance_spender,
            "balance_expected": balance_check.get("expected") if balance_check else None,
            "allowance_expected": allowance_check.get("expected") if allowance_check else None,
            "required_min_balance_usdc": gate_funding.get("required_min_balance_usdc"),
            "required_smoke_notional_usdc": gate_funding.get("required_smoke_notional_usdc"),
            "required_min_allowance_usdc": gate_funding.get("required_min_allowance_usdc"),
            "balance_shortfall_usdc": gate_funding.get("balance_shortfall_usdc"),
            "smoke_notional_shortfall_usdc": gate_funding.get("smoke_notional_shortfall_usdc"),
            "allowance_shortfall_usdc": gate_funding.get("allowance_shortfall_usdc"),
            "funding_ready": gate_funding.get("funding_ready"),
        },
        "reports": {
            "allowance": str(allowance_path) if allowance_path else "",
            "execution": str(execution_path) if execution_path else "",
            "live_formal": formal_summary.get("report") or "",
            "live_gate": str(effective_gate_path) if effective_gate_path else "",
            "live_preflight": str(preflight_path) if preflight_path else "",
        },
        "dryrun": dryrun_summary,
        "live_real": live_real_summary,
        "live_order_sync": live_order_sync_summary,
        "live_trading_status": live_trading_status,
        "paper_monitor": paper_monitor_summary,
        "live_gate": live_gate_summary,
        "preflight_chain": preflight_summary,
        "market_data": market_data_summary,
        "alerts": alert_summary,
        "checklist": checklist,
        "readiness_summary": readiness_summary,
        "operator_summary": operator_summary,
        "clob_readonly": clob_readonly,
        "report_refresh": report_refresh,
        "legacy_report_refresh": legacy_report_refresh,
        "first_order_rail": first_order_rail,
        "risk": live_real_risk_summary,
        "today": today_summary,
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


def _latest_log_report(source=None):
    report = {"out_log": "", "err_log": "", "errors": []}
    if not KRONOS_LOG_DIR.exists():
        return report
    source = source or _paper_run_source()
    if source == "live_real":
        patterns = {
            "out_log": ["prediction_bound_live_formal_latest.out.log", "prediction_bound_live_formal_*.out.log"],
            "err_log": ["prediction_bound_live_formal_latest.err.log", "prediction_bound_live_formal_*.err.log"],
        }
    else:
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
        "market_slug",
        "market_end_iso",
        "token_id",
        "outcome_token_id",
        "decision_ts",
        "decision_bar_ts",
        "entry_ts",
        "settle_ts",
        "entry_price_ts",
        "settle_price_ts",
        "entry_bar",
        "settle_bar",
        "entry_price",
        "settle_price",
        "entry_close",
        "settle_close",
        "direction",
        "dir",
        "size",
        "regime",
        "maker_price",
        "price",
        "reference_price",
        "reference_price_source",
        "binance_reference_price",
        "chainlink_reference_price",
        "binance_entry_price",
        "chainlink_entry_price",
        "binance_settle_price",
        "chainlink_settle_price",
        "binance_entry_close",
        "chainlink_entry_close",
        "binance_settle_close",
        "settlement_source",
        "paper_actual_up_binance",
        "actual_up_binance",
        "actual_up_chainlink",
        "settlement_disagrees",
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


def _maker_quality_report(recent_events, recent_audit_events, limit=20, shadow_events=None):
    if shadow_events is None:
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
    source = _request_dashboard_source()
    db = get_db()
    row = db.execute(
        "SELECT * FROM snapshots WHERE source=? ORDER BY id DESC LIMIT 1", (source,)
    ).fetchone()
    if row is None:
        return jsonify({
            "source": source,
            "source_label": _source_label(source),
            "balance": 500,
            "trades_count": 0,
            "wr": 0,
            "cooldown_left": 0,
        })
    payload = dict(row)
    payload["source_label"] = _source_label(str(payload.get("source") or source))
    return jsonify(payload)


@app.route("/api/signal-stats")
def api_signal_stats():
    source = _request_dashboard_source()
    if source == "live_real":
        events = _live_prediction_artifact_events(limit=5000)
        summary = _signal_stats_summary(events)
        latest = events[0].get("created_at") if events else None
        return jsonify(
            {
                "source": source,
                "source_label": _source_label(source),
                **summary,
                "latest_created_at": latest,
            }
        )

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
            "source_label": _source_label(source),
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
    source = _request_dashboard_source()
    limit = int(request.args.get("limit", 100))
    since = request.args.get("since", "")  # Optional: filter by min created_at
    if source == "live_real":
        return jsonify(_live_prediction_artifact_events(limit=limit, since=since))

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
    payload = []
    for row in rows:
        item = dict(row)
        item["source_label"] = _source_label(str(item.get("source") or source))
        payload.append(item)
    return jsonify(payload)


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
    source = _request_dashboard_source()
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
    payload = []
    for row in rows:
        item = dict(row)
        item["source_label"] = _source_label(str(item.get("source") or source))
        payload.append(item)
    return jsonify(payload)


@app.route("/api/live-intel")
def api_live_intel():
    limit = int(request.args.get("limit", 240))
    formal_path, formal_report = _live_formal_report()
    use_live_real = os.environ.get("KRONOS_ENABLE_REAL_ORDERS", "") == "YES" and formal_path.exists()
    if use_live_real:
        checkpoint_path = _live_real_ledger_path()
        ledger_payload = _read_json(checkpoint_path)
        ledger_rows = ledger_payload if isinstance(ledger_payload, list) else []
        checkpoint = {
            "timestamp": _iso_utc(datetime.fromtimestamp(checkpoint_path.stat().st_mtime, tz=timezone.utc)) if checkpoint_path.exists() else None,
            "trades": [row for row in ledger_rows if isinstance(row, dict) and str(row.get("status") or "").upper() == "SETTLED"],
            "pending_orders": [row for row in ledger_rows if isinstance(row, dict) and str(row.get("status") or "").upper() in {"PENDING", "SUBMITTED", "FILLED"}],
            "open_orders": [row for row in ledger_rows if isinstance(row, dict) and str(row.get("status") or "").upper() == "OPEN"],
            "bar_index": len(ledger_rows),
        }
        events = _live_formal_history_prediction_records(limit=limit)
        audit_events = []
        run_source = "live_real"
    else:
        checkpoint_path = _paper_checkpoint_path()
        events_path = _events_checkpoint_path()
        checkpoint = _read_json(checkpoint_path) or {}
        events = _tail_jsonl(events_path, limit=limit)
        audit_events = _tail_audit_events(limit=limit * 3)
        run_source = _paper_run_source()

    trades = checkpoint.get("trades", []) or []
    pending = checkpoint.get("pending_orders", []) or []
    open_orders = checkpoint.get("open_orders", []) or []
    bar_index = int(checkpoint.get("bar_index", 0) or 0)
    if use_live_real and formal_report.get("created_at"):
        checkpoint_age = _age_seconds(formal_report.get("created_at"))
    else:
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

    log_report = _latest_log_report("live_real" if use_live_real else None)
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
                "run_source": run_source,
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
    ttl_seconds = _dashboard_cache_seconds("DASHBOARD_LIVE_SAFETY_CACHE_SECONDS", 30)
    payload = _cached_dashboard_payload(
        ("live-safety", str(KRONOS_CHECKPOINT_DIR), str(KRONOS_REPORT_DIR)),
        ttl_seconds,
        _safety_report_summary,
    )
    return jsonify(payload)


@app.route("/api/live-pnl-calendar")
def api_live_pnl_calendar():
    try:
        source = request.args.get("source")
        ledger_path = _calendar_ledger_path(source)
        records = _rows_from_ledger_payload(_read_json(ledger_path))
        payload = _monthly_pnl_calendar_from_records(
            records,
            month_value=request.args.get("month"),
        )
        payload["source"] = source
        return jsonify(payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/live-pnl-calendar/orders")
def api_live_pnl_calendar_orders():
    try:
        source = request.args.get("source")
        ledger_path = _calendar_ledger_path(source)
        records = _rows_from_ledger_payload(_read_json(ledger_path))
        payload = _daily_pnl_orders_from_records(
            records,
            day_value=request.args.get("date"),
        )
        payload["source"] = source
        return jsonify(payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/live-risk")
def api_live_risk():
    return jsonify(_live_risk_summary())


@app.route("/api/live-analytics")
def api_live_analytics():
    return jsonify(_live_analytics_summary())


# ---------------------------------------------------------------------------
# BTC market chart (read-only Chainlink reference)
# ---------------------------------------------------------------------------

def _kronos_root():
    configured = os.environ.get("KRONOS_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return KRONOS_CHECKPOINT_DIR.expanduser().resolve().parent.parent


def _ensure_kronos_src_on_path():
    src = _kronos_root() / "src"
    if src.exists() and str(src) not in sys.path:
        sys.path.insert(0, str(src))


def _load_kronos_env():
    env_path = _kronos_root() / ".env"
    if not env_path.exists():
        return
    try:
        lines = env_path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


def _btc_live_now():
    return time.time()


def _chainlink_streams_body_hash(body=b""):
    if isinstance(body, str):
        body = body.encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _chainlink_streams_auth_headers(method, full_path, api_key, secret, timestamp_ms=None, body=b""):
    timestamp = str(timestamp_ms if timestamp_ms is not None else int(_btc_live_now() * 1000))
    body_hash = _chainlink_streams_body_hash(body)
    string_to_sign = f"{str(method).upper()} {full_path} {body_hash} {api_key} {timestamp}"
    signature = hmac.new(str(secret).encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return {
        "Authorization": api_key,
        "X-Authorization-Timestamp": timestamp,
        "X-Authorization-Signature-SHA256": signature,
    }


def _chainlink_streams_config_status():
    _load_kronos_env()
    user_id = os.environ.get("CHAINLINK_STREAMS_USER_ID")
    secret = os.environ.get("CHAINLINK_STREAMS_SECRET")
    feed_id = os.environ.get("CHAINLINK_STREAMS_BTC_FEED_ID")
    endpoint = os.environ.get("CHAINLINK_STREAMS_WS_ENDPOINT", "wss://ws.dataengine.chain.link").rstrip("/")
    if not feed_id:
        return {
            "source": "chainlink_streams_ws",
            "status": "missing_feed_id",
            "message": "Set CHAINLINK_STREAMS_BTC_FEED_ID to enable direct Chainlink Streams WebSocket.",
            "endpoint": endpoint,
        }
    missing = [name for name, value in (("user_id", user_id), ("secret", secret)) if not value]
    if missing:
        return {
            "source": "chainlink_streams_ws",
            "status": "missing_credentials",
            "message": f"Missing Chainlink Streams {', '.join(missing)}.",
            "endpoint": endpoint,
            "feed_id_hint": f"{feed_id[:8]}...{feed_id[-6:]}",
        }
    return {
        "source": "chainlink_streams_ws",
        "status": "configured",
        "endpoint": endpoint,
        "feed_id_hint": f"{feed_id[:8]}...{feed_id[-6:]}",
    }


def _latest_chainlink_streams_price(now=None):
    status = _chainlink_streams_config_status()
    return {
        "price": None,
        "timestamp": None,
        "source": status["source"],
        "status": status["status"],
        "error": status.get("message"),
    }


def _polymarket_rtds_subscription():
    return {
        "action": "subscribe",
        "subscriptions": [
            {
                "topic": "crypto_prices_chainlink",
                "type": "*",
                "filters": "",
            }
        ],
    }


def _store_btc_live_price(price, timestamp_ms=None, source="polymarket_rtds_chainlink", status="fresh"):
    try:
        numeric_price = float(price)
    except (TypeError, ValueError):
        return
    if timestamp_ms is None:
        timestamp_ms = int(_btc_live_now() * 1000)
    try:
        ts = datetime.fromtimestamp(float(timestamp_ms) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        ts = datetime.now(timezone.utc)
    record = {
        "price": numeric_price,
        "timestamp": _iso_timestamp(ts),
        "source": source,
        "status": status,
    }
    with BTC_LIVE_LOCK:
        BTC_LIVE_CACHE.update({**record, "received_at": _btc_live_now(), "error": None})
        BTC_LIVE_TICKS.append(record)


def _handle_polymarket_rtds_message(raw_message):
    if not raw_message:
        return
    try:
        message = json.loads(raw_message) if isinstance(raw_message, str) else raw_message
    except (TypeError, json.JSONDecodeError):
        return
    if not isinstance(message, dict):
        return
    payload = message.get("payload") or {}
    if not isinstance(payload, dict):
        return
    if isinstance(payload.get("data"), list):
        for item in payload["data"]:
            if not isinstance(item, dict):
                continue
            _store_btc_live_price(
                item.get("value"),
                timestamp_ms=item.get("timestamp") or message.get("timestamp"),
                source="polymarket_rtds_chainlink",
            )
        return
    if message.get("topic") != "crypto_prices_chainlink":
        return
    symbol = str(payload.get("symbol") or "").lower()
    if symbol != "btc/usd":
        return
    value = payload.get("value")
    timestamp_ms = payload.get("timestamp") or message.get("timestamp")
    _store_btc_live_price(value, timestamp_ms=timestamp_ms, source="polymarket_rtds_chainlink")


def _mark_btc_live_error(error):
    with BTC_LIVE_LOCK:
        BTC_LIVE_CACHE.update({"status": "error", "error": str(error)[:160], "received_at": _btc_live_now()})


def _run_polymarket_rtds_worker():
    url = os.environ.get("POLYMARKET_RTDS_WS_URL", "wss://ws-live-data.polymarket.com")
    while True:
        try:
            import websocket

            ws = websocket.create_connection(url, timeout=10)
            ws.settimeout(2)
            ws.send(json.dumps(_polymarket_rtds_subscription()))
            last_ping = 0.0
            while True:
                if _btc_live_now() - last_ping >= 5:
                    ws.send("PING")
                    last_ping = _btc_live_now()
                try:
                    message = ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                if message and message not in ("PONG", "PING"):
                    _handle_polymarket_rtds_message(message)
        except Exception as exc:
            _mark_btc_live_error(exc)
            time.sleep(3)


def _ensure_polymarket_rtds_worker_started():
    if os.environ.get("DISABLE_POLYMARKET_RTDS") == "1":
        return
    with BTC_LIVE_LOCK:
        if BTC_LIVE_WORKER["started"]:
            return
        BTC_LIVE_WORKER["started"] = True
    thread = threading.Thread(target=_run_polymarket_rtds_worker, name="polymarket-rtds-chainlink-btc", daemon=True)
    BTC_LIVE_WORKER["thread"] = thread
    thread.start()


def _latest_polymarket_chainlink_price(now=None, start_worker=True):
    if start_worker:
        _ensure_polymarket_rtds_worker_started()
    now_ts = _utc_timestamp(now) if now is not None else pd.Timestamp(datetime.now(timezone.utc))
    with BTC_LIVE_LOCK:
        cached = dict(BTC_LIVE_CACHE)
    price = cached.get("price")
    timestamp = cached.get("timestamp")
    if price is None or not timestamp:
        return {
            "price": None,
            "timestamp": None,
            "source": "polymarket_rtds_chainlink",
            "status": cached.get("status") or "warming_up",
            "error": cached.get("error"),
        }
    age = abs((now_ts - _utc_timestamp(timestamp)).total_seconds())
    status = "fresh" if age <= 15 else "stale"
    return {
        "price": float(price),
        "timestamp": timestamp,
        "source": cached.get("source") or "polymarket_rtds_chainlink",
        "status": status,
        "error": cached.get("error"),
    }


def _latest_btc_live_price(now=None):
    direct = _latest_chainlink_streams_price(now=now)
    if direct.get("price") is not None:
        return direct
    rtds = _latest_polymarket_chainlink_price(now=now)
    if rtds.get("price") is not None:
        return rtds
    return {
        "price": None,
        "timestamp": None,
        "source": "polymarket_rtds_chainlink",
        "status": rtds.get("status") or direct.get("status") or "warming_up",
        "error": rtds.get("error") or direct.get("error"),
    }


def _live_ticks_between(start_ts, end_ts):
    start = _utc_timestamp(start_ts)
    end = _utc_timestamp(end_ts)
    with BTC_LIVE_LOCK:
        ticks = list(BTC_LIVE_TICKS)
    return [
        {
            "timestamp": item["timestamp"],
            "price": round(float(item["price"]), 4),
            "source": item.get("source") or "polymarket_rtds_chainlink",
        }
        for item in ticks
        if start <= _utc_timestamp(item["timestamp"]) <= end
    ]


def _get_cached_btc_candles(cache_key, ttl_seconds, fetcher, cache_validator=None):
    now = _btc_live_now()
    with BTC_CANDLE_LOCK:
        cached = BTC_CANDLE_CACHE.get(cache_key)
        if cached and now - cached["ts"] <= float(ttl_seconds):
            cached_frame = cached["frame"].copy()
            if cache_validator is None or cache_validator(cached_frame):
                return cached_frame

    frame = fetcher()
    normalized = frame.copy() if hasattr(frame, "copy") else frame
    with BTC_CANDLE_LOCK:
        BTC_CANDLE_CACHE[cache_key] = {"ts": now, "frame": normalized.copy() if hasattr(normalized, "copy") else normalized}
    return normalized


def _btc_candle_cache_has_boundaries(frame, boundaries):
    if not boundaries:
        return True
    normalized = _normalize_ohlc_frame(frame)
    if normalized.empty:
        return False
    index = normalized.index
    return all(_utc_timestamp(boundary) in index for boundary in boundaries)


def _utc_timestamp(value=None):
    if value is None:
        return pd.Timestamp(datetime.now(timezone.utc))
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _iso_timestamp(value):
    return _utc_timestamp(value).to_pydatetime().isoformat()


def _btc_market_window(now=None, start_ts=None):
    now_ts = _utc_timestamp(now)
    anchor = _utc_timestamp(start_ts) if start_ts is not None else now_ts
    minute = (anchor.minute // 5) * 5
    start = anchor.replace(minute=minute, second=0, microsecond=0, nanosecond=0)
    end = start + pd.Timedelta(minutes=5)
    previous_start = start - pd.Timedelta(minutes=5)
    next_start = end
    return {
        "slug": f"btc-updown-5m-{int(end.timestamp())}",
        "label": f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')}",
        "start_ts": _iso_timestamp(start),
        "end_ts": _iso_timestamp(end),
        "previous_start_ts": _iso_timestamp(previous_start),
        "next_start_ts": _iso_timestamp(next_start),
    }


def _normalize_ohlc_frame(frame):
    if frame is None:
        return pd.DataFrame(columns=["open", "high", "low", "close"])
    normalized = frame.copy()
    if not isinstance(normalized.index, pd.DatetimeIndex):
        if "timestamp" not in normalized.columns:
            return pd.DataFrame(columns=["open", "high", "low", "close"])
        normalized.index = pd.to_datetime(normalized.pop("timestamp"), utc=True)
    elif normalized.index.tz is None:
        normalized.index = normalized.index.tz_localize("UTC")
    else:
        normalized.index = normalized.index.tz_convert("UTC")

    columns = [col for col in ("open", "high", "low", "close") if col in normalized.columns]
    normalized = normalized[columns].copy()
    for col in ("open", "high", "low", "close"):
        if col not in normalized.columns:
            normalized[col] = pd.NA
        normalized[col] = pd.to_numeric(normalized[col], errors="coerce")
    return normalized.dropna(subset=["open", "high", "low", "close"]).sort_index()


def _price_at_or_before(frame, ts):
    if frame.empty:
        return None, None
    target = _utc_timestamp(ts)
    eligible = frame.loc[frame.index <= target]
    if eligible.empty:
        return None, None
    row_ts = eligible.index[-1]
    return row_ts, float(eligible.iloc[-1]["close"])


def _price_at(frame, ts):
    target = _utc_timestamp(ts)
    if target not in frame.index:
        return None
    return float(frame.loc[target, "close"])


def _result_label(target_price, settle_price):
    if target_price is None or settle_price is None:
        return "PENDING"
    if settle_price >= target_price:
        return "UP"
    return "DOWN"


def _build_history(frame, current_start, limit=8):
    history = []
    starts = [ts for ts in frame.index if ts < current_start]
    for start in reversed(starts):
        settle = start + pd.Timedelta(minutes=5)
        if settle not in frame.index:
            continue
        target_price = _price_at(frame, start)
        settle_price = _price_at(frame, settle)
        history.append(
            {
                "slug": f"btc-updown-5m-{int(settle.timestamp())}",
                "label": f"{start.strftime('%H:%M')}-{settle.strftime('%H:%M')}",
                "start_ts": _iso_timestamp(start),
                "end_ts": _iso_timestamp(settle),
                "target_price": target_price,
                "settle_price": settle_price,
                "result": _result_label(target_price, settle_price),
            }
        )
        if len(history) >= limit:
            break
    return history


def _market_result_for_window(frame, start, end, now_ts):
    if now_ts < start:
        return "UPCOMING"
    if start <= now_ts < end:
        return "PENDING"
    return _result_label(_price_at(frame, start), _price_at(frame, end))


def _build_market_switcher(frame, market, now=None):
    now_ts = _utc_timestamp(now)
    current_start = _utc_timestamp(market["start_ts"])
    windows = [
        ("previous", current_start - pd.Timedelta(minutes=5)),
        ("current", current_start),
        ("next", current_start + pd.Timedelta(minutes=5)),
    ]
    items = []
    for key, start in windows:
        end = start + pd.Timedelta(minutes=5)
        target_price = _price_at(frame, start)
        settle_price = _price_at(frame, end)
        items.append(
            {
                "key": key,
                "slug": f"btc-updown-5m-{int(end.timestamp())}",
                "label": f"{start.strftime('%H:%M')}",
                "start_ts": _iso_timestamp(start),
                "end_ts": _iso_timestamp(end),
                "target_price": target_price,
                "settle_price": settle_price,
                "result": _market_result_for_window(frame, start, end, now_ts),
            }
        )
    return items


def _build_btc_market_chart_payload(frame, now=None, start_ts=None):
    now_ts = _utc_timestamp(now)
    market = _btc_market_window(now_ts, start_ts=start_ts)
    normalized = _normalize_ohlc_frame(frame)
    current_start = _utc_timestamp(market["start_ts"])
    current_end = _utc_timestamp(market["end_ts"])
    is_upcoming_market = now_ts < current_start
    live_market = _btc_market_window(now_ts)
    live_start = _utc_timestamp(live_market["start_ts"]) if is_upcoming_market else current_start
    live_end = _utc_timestamp(live_market["end_ts"]) if is_upcoming_market else current_end

    target_price = _price_at(normalized, current_start)
    target_source = "exact"
    if is_upcoming_market:
        target_price = None
        target_source = "upcoming"
    elif target_price is None:
        _, target_price = _price_at_or_before(normalized, current_start)
        target_source = "previous_close" if target_price is not None else "missing"

    price_cursor = min(now_ts, current_end)
    latest_ts, current_price = _price_at_or_before(normalized, price_cursor)
    live = {"price": None, "timestamp": None, "source": "chainlink_candlestick", "status": "fallback", "error": None}
    if live_start <= now_ts < live_end:
        live = _latest_btc_live_price(now=now_ts)
        live_ts = _utc_timestamp(live.get("timestamp")) if live.get("timestamp") else None
        if live.get("price") is not None and live_ts is not None and live_start <= live_ts <= live_end + pd.Timedelta(seconds=5):
            current_price = float(live["price"])
            latest_ts = live_ts
    delta = None
    delta_pct = None
    if target_price is not None and current_price is not None:
        delta = round(current_price - target_price, 2)
        delta_pct = round(delta / target_price, 6) if target_price else None

    candles = [
        {
            "timestamp": _iso_timestamp(ts),
            "open": round(float(row["open"]), 2),
            "high": round(float(row["high"]), 2),
            "low": round(float(row["low"]), 2),
            "close": round(float(row["close"]), 2),
        }
        for ts, row in normalized.tail(80).iterrows()
    ]
    if current_price is not None and latest_ts is not None and live_start <= latest_ts <= live_end + pd.Timedelta(seconds=5):
        live_close = round(float(current_price), 2)
        if candles and candles[-1]["timestamp"] == _iso_timestamp(latest_ts):
            candles[-1]["high"] = max(candles[-1]["high"], live_close)
            candles[-1]["low"] = min(candles[-1]["low"], live_close)
            candles[-1]["close"] = live_close
        else:
            candles.append(
                {
                    "timestamp": _iso_timestamp(latest_ts),
                    "open": live_close,
                    "high": live_close,
                    "low": live_close,
                    "close": live_close,
                }
            )
    live_ticks = _live_ticks_between(live_start, live_end)
    if current_price is not None and latest_ts is not None and live_start <= latest_ts <= live_end + pd.Timedelta(seconds=5):
        live_tick_ts = _iso_timestamp(latest_ts)
        if not any(item["timestamp"] == live_tick_ts for item in live_ticks):
            live_ticks.append(
                {
                    "timestamp": live_tick_ts,
                    "price": round(float(current_price), 4),
                    "source": live.get("source") or "chainlink_candlestick",
                }
            )

    return {
        "readonly": True,
        "chart_status": "ok",
        "source": "chainlink_candlestick",
        "live_source": live.get("source") or "chainlink_candlestick",
        "live_status": live.get("status") or "fallback",
        "live_error": live.get("error"),
        "symbol": os.environ.get("CHAINLINK_SYMBOL", "BTCUSD"),
        "resolution": "5m",
        "now": _iso_timestamp(now_ts),
        "market": market,
        "markets": _build_market_switcher(normalized, market, now=now_ts),
        "target_price": round(target_price, 2) if target_price is not None else None,
        "target_source": target_source,
        "current_price": round(current_price, 2) if current_price is not None else None,
        "current_price_ts": _iso_timestamp(latest_ts) if latest_ts is not None else None,
        "delta": delta,
        "delta_pct": delta_pct,
        "candles": candles,
        "ticks": live_ticks,
        "history": _build_history(normalized, current_start),
    }


def _fetch_chainlink_btc_candles(symbol=None, now=None, start_ts=None, lookback_minutes=120):
    _load_kronos_env()
    _ensure_kronos_src_on_path()
    from kronos_poly.data.chainlink_candles import ChainlinkCandlestickClient, ChainlinkCredentials

    user_id = os.environ.get("CHAINLINK_CANDLESTICK_USER_ID") or os.environ.get("CHAINLINK_STREAMS_USER_ID")
    api_key = os.environ.get("CHAINLINK_CANDLESTICK_API_KEY")
    if not user_id or not api_key:
        raise RuntimeError("missing Chainlink Candlestick credentials")

    now_ts = _utc_timestamp(now)
    window = _btc_market_window(now_ts, start_ts=start_ts)
    current_start = _utc_timestamp(window["start_ts"])
    current_end = current_start + pd.Timedelta(minutes=5)
    start = current_start - pd.Timedelta(minutes=int(lookback_minutes))
    end = current_end
    symbol = symbol or os.environ.get("CHAINLINK_SYMBOL", "BTCUSD")
    base_url = os.environ.get("CHAINLINK_CANDLESTICK_BASE_URL", "https://priceapi.dataengine.chain.link")
    client = ChainlinkCandlestickClient(
        ChainlinkCredentials(user_id=user_id, api_key=api_key),
        base_url=base_url,
        timeout_seconds=float(os.environ.get("CHAINLINK_CANDLESTICK_TIMEOUT_SECONDS", "12")),
    )
    cache_key = (
        symbol,
        "5m",
        _iso_timestamp(current_start),
        int(lookback_minutes),
        base_url,
    )
    cache_seconds = float(os.environ.get("CHAINLINK_CANDLE_CACHE_SECONDS", "20"))
    required_boundaries = []
    if now_ts >= current_start:
        required_boundaries.append(current_start)
    if now_ts >= current_end:
        required_boundaries.append(current_end)
    return _get_cached_btc_candles(
        cache_key,
        cache_seconds,
        lambda: client.fetch_history_chunked(
            symbol=symbol,
            resolution="5m",
            start=start,
            end=end,
            max_window_days=1,
        ),
        cache_validator=lambda frame: _btc_candle_cache_has_boundaries(frame, required_boundaries),
    )


@app.route("/api/btc/market-chart")
def api_btc_market_chart():
    now_arg = request.args.get("now")
    start_arg = request.args.get("start_ts")
    now_ts = _parse_dt(now_arg) if now_arg else datetime.now(timezone.utc)
    start_ts = _parse_dt(start_arg) if start_arg else None
    try:
        frame = _fetch_chainlink_btc_candles(now=now_ts, start_ts=start_ts)
        return jsonify(_build_btc_market_chart_payload(frame, now=now_ts, start_ts=start_ts))
    except Exception as exc:
        payload = _build_btc_market_chart_payload(pd.DataFrame(), now=now_ts, start_ts=start_ts)
        payload["chart_status"] = "degraded"
        payload["error"] = str(exc)
        return jsonify(payload)


@app.route("/api/btc/live-price")
def api_btc_live_price():
    return jsonify(_latest_btc_live_price())


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

@app.route("/api/strategy-comparison")
def api_strategy_comparison():
    ttl_seconds = _dashboard_cache_seconds("DASHBOARD_STRATEGY_COMPARISON_CACHE_SECONDS", 30)
    query = request.query_string.decode("utf-8", errors="replace")
    payload = _cached_dashboard_payload(
        ("strategy-comparison", str(KRONOS_CHECKPOINT_DIR), str(KRONOS_REPORT_DIR), query),
        ttl_seconds,
        lambda: _strategy_comparison_payload(args=request.args),
    )
    return jsonify(payload)


@app.route("/api/strategy-comparison/differences")
def api_strategy_comparison_differences():
    try:
        filters = _strategy_difference_filters(request.args)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    ttl_seconds = _dashboard_cache_seconds(
        "DASHBOARD_STRATEGY_DIFFERENCES_CACHE_SECONDS",
        30,
    )
    query = request.query_string.decode("utf-8", errors="replace")
    payload = _cached_dashboard_payload(
        (
            "strategy-comparison-differences",
            str(KRONOS_CHECKPOINT_DIR),
            str(KRONOS_REPORT_DIR),
            query,
        ),
        ttl_seconds,
        lambda: _strategy_difference_payload(
            candidate_id=filters["candidate_id"],
            filters=filters,
        ),
    )
    return jsonify(payload)


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
