"""Sync service: polls Kronos data files and writes to SQLite."""
import json
import os
import sqlite3
import time
from pathlib import Path
from datetime import datetime


def get_kronos_dir():
    return Path(os.environ.get("KRONOS_DATA_DIR", "../Kronos/data/checkpoints"))


def configured_paper_source():
    return (os.environ.get("DASHBOARD_RUN_SOURCE") or os.environ.get("RUN_SOURCE") or "").strip()


def paper_run_source(kronos_dir: Path):
    configured = configured_paper_source()
    if configured:
        return configured
    files = sorted(kronos_dir.glob("paper_live*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0].stem if files else "paper_live"


def event_jsonl_path(kronos_dir: Path, source: str):
    if source != "history":
        preferred = kronos_dir / f"{source}_events.jsonl"
        if preferred.exists():
            return preferred
        preferred = kronos_dir.parent / "events" / source / f"{source}.jsonl"
        if preferred.exists():
            return preferred
        preferred = kronos_dir / "paper_live_events.jsonl"
        return preferred if preferred.exists() else kronos_dir / "paper_events.jsonl"
    return kronos_dir / "paper_hist.jsonl"


def event_action(data: dict):
    if data.get("action"):
        return data.get("action")
    if isinstance(data.get("decision"), dict):
        return data["decision"].get("action")
    if isinstance(data.get("order"), dict):
        return data["order"].get("action")
    return data.get("type", "?")


def decision_payload(data: dict):
    if isinstance(data.get("decision"), dict):
        return data["decision"]
    if isinstance(data.get("order"), dict):
        return data["order"]
    return data


def prob_label(name: str, value):
    try:
        return f"{name} {float(value):.3f}"
    except (TypeError, ValueError):
        return "?"


def event_reason(data: dict):
    if data.get("reason"):
        return data.get("reason")
    if isinstance(data.get("decision"), dict):
        return data["decision"].get("reason") or data["decision"].get("reason_code", "")
    if isinstance(data.get("order"), dict):
        return data["order"].get("reason") or data["order"].get("reason_code", "")
    return data.get("type", "")


def event_created_at(data: dict):
    if data.get("_t") or data.get("ts") or data.get("created_at"):
        return data.get("_t") or data.get("ts") or data.get("created_at")
    if isinstance(data.get("decision"), dict):
        return data["decision"].get("created_at") or data["decision"].get("ts")
    if isinstance(data.get("order"), dict):
        return data["order"].get("created_at") or data["order"].get("entry_ts")
    return str(datetime.now())


def event_details(data: dict):
    return json.dumps(decision_payload(data), ensure_ascii=False, sort_keys=True)


def trade_reason(trade: dict):
    return (
        trade.get("reason_code")
        or trade.get("strategy")
        or trade.get("regime")
        or trade.get("reason")
        or trade.get("status")
        or ""
    )


def trade_details(trade: dict):
    return json.dumps(trade, ensure_ascii=False, sort_keys=True)


def ensure_column(conn, table: str, column: str, definition: str):
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db(db_path: str):
    conn = sqlite3.connect(db_path)
    schema = Path(__file__).parent / "schema.sql"
    conn.executescript(schema.read_text())
    ensure_column(conn, "events", "details", "TEXT")
    ensure_column(conn, "trades", "details", "TEXT")
    conn.commit()
    return conn


def import_jsonl(conn, source: str, path: Path, last_count: int):
    """Import new lines from a JSONL file. Returns new line count."""
    if not path.exists():
        return last_count
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    if not lines or lines[0] == "":
        return last_count
    new_lines = lines[last_count:]
    for line in new_lines:
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if data.get("type") in ("opened", "settled"):
            continue
        payload = decision_payload(data)
        conn.execute(
            """INSERT OR IGNORE INTO events (source, kline_n, action, dir5, dir4,
               regime, filt_passed, reason, details, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                source,
                data.get("n", data.get("kline_n", 0)),
                event_action(data),
                data.get("dir5") or prob_label("5m", payload.get("p5_up")),
                data.get("dir4") or prob_label("4h", payload.get("p4_up")),
                data.get("regime") or data.get("type", "?"),
                1 if data.get("filt") or payload.get("passed") else 0,
                event_reason(data)[:100],
                event_details(data),
                event_created_at(data),
            ),
        )
    conn.commit()
    return len(lines)


def import_live_snapshot(conn, path: Path, db_source: str | None = None):
    """Read paper_live.json and write snapshot + new trades."""
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    source = db_source or ("live" if str(data.get("mode", "")).startswith("paper_live") else "history")

    # Snapshot
    trades_list = data.get("trades", [])
    wins = sum(1 for t in trades_list if t.get("won"))
    wr = wins / len(trades_list) if trades_list else 0
    conn.execute(
        """INSERT INTO snapshots (source, balance, trades_count, wr, cooldown_left, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            source,
            data.get("balance", 500),
            len(trades_list),
            wr,
            data.get("cooldown_bars", 0),
            data.get("timestamp", str(datetime.now())),
        ),
    )

    # Remove the previous pending view before importing the current checkpoint.
    # Pending orders are a live queue, not historical records.
    conn.execute("DELETE FROM trades WHERE source=? AND won=-1", (source,))

    # Trades (settled outcomes). Use upsert so a previously-pending order becomes settled.
    for t in trades_list:
        if t.get("won") is None:
            continue  # Skip pending orders
        conn.execute(
            """INSERT INTO trades (source, pnl, won, regime, direction, size,
               entry_bar, settle_bar, details, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(source, entry_bar, settle_bar) DO UPDATE SET
                    pnl=excluded.pnl,
                    won=excluded.won,
                    regime=excluded.regime,
                    direction=excluded.direction,
                    size=excluded.size,
                    details=excluded.details,
                    created_at=excluded.created_at""",
            (
                source,
                t.get("pnl", 0),
                1 if t.get("won") else 0,
                trade_reason(t),
                t.get("dir", t.get("direction", t.get("side", "?"))),
                t.get("size", t.get("stake", 0)),
                t.get("entry_bar", t.get("entry_ts", 0)),
                t.get("settle_bar", t.get("settle_ts", 0)),
                trade_details(t),
                data.get("timestamp", str(datetime.now())),
            ),
        )

    # Pending orders live outside the settled trades list in paper_live.json.
    for t in data.get("pending_orders", []):
        conn.execute(
            """INSERT INTO trades (source, pnl, won, regime, direction, size,
               entry_bar, settle_bar, details, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(source, entry_bar, settle_bar) DO UPDATE SET
                    pnl=excluded.pnl,
                    won=excluded.won,
                    regime=excluded.regime,
                    direction=excluded.direction,
                    size=excluded.size,
                    details=excluded.details,
                    created_at=excluded.created_at""",
            (
                source,
                0, -1,  # won=-1 means pending
                trade_reason(t),
                t.get("dir", t.get("direction", t.get("side", "?"))),
                t.get("size", t.get("stake", 0)),
                t.get("entry_bar", t.get("entry_ts", 0)),
                t.get("settle_bar", t.get("settle_ts", 0)),
                trade_details(t),
                data.get("timestamp", str(datetime.now())),
            ),
        )
    conn.commit()


def main():
    kronos_dir = get_kronos_dir()
    print(f"Watching: {kronos_dir}")

    db_path = Path(__file__).parent.parent / "data" / "dashboard.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = init_db(str(db_path))

    # Track JSONL positions
    live_event_count = 0
    hist_event_count = 0
    live_event_path = None

    # First run: check if empty, do full import
    cur = conn.execute("SELECT COUNT(*) FROM events")
    if cur.fetchone()[0] == 0:
        print("First run 鈥?full import...")
        live_source = paper_run_source(kronos_dir)
        live_event_path = event_jsonl_path(kronos_dir, live_source)
        live_event_count = import_jsonl(conn, "live",
                                        live_event_path, 0)
        hist_event_count = import_jsonl(conn, "history",
                                        event_jsonl_path(kronos_dir, "history"), 0)
        print(f"  Imported {live_event_count} live + {hist_event_count} history events")

    while True:
        try:
            # Live paper
            live_source = paper_run_source(kronos_dir)
            current_live_event_path = event_jsonl_path(kronos_dir, live_source)
            if live_event_path != current_live_event_path:
                live_event_path = current_live_event_path
                live_event_count = 0
            import_live_snapshot(conn, kronos_dir / f"{live_source}.json", db_source="live")
            live_event_count = import_jsonl(
                conn, "live", live_event_path, live_event_count
            )

            # Historical paper
            hist_file = kronos_dir / "paper_hist.json"
            if hist_file.exists():
                import_live_snapshot(conn, hist_file)
            hist_event_count = import_jsonl(
                conn, "history", event_jsonl_path(kronos_dir, "history"), hist_event_count
            )

        except Exception as e:
            print(f"Sync error: {e}")

        time.sleep(3)


if __name__ == "__main__":
    main()

