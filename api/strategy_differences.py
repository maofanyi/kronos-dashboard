from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence


DIFFERENCE_TYPES = (
    "side_mismatch",
    "outcome_mismatch",
    "live_only",
    "simulated_pre_submit_blocked",
    "simulated_no_fill",
    "simulated_strategy_only",
    "simulated_only_unknown",
    "execution_mismatch",
    "matched",
)

FINAL_STATUSES = {"SETTLED", "WON", "LOST", "CLOSED"}
NO_FILL_STATUSES = {"NO_FILL", "CANCELLED", "CANCELED"}
EPSILON = 1e-6


def market_key(record: Mapping[str, Any]) -> tuple[str, str] | None:
    entry = _parse_dt(record.get("entry_ts") or record.get("target_market_entry_ts"))
    settle = _parse_dt(record.get("settle_ts") or record.get("target_market_settle_ts"))
    if entry is None or settle is None:
        return None
    return (_iso_utc(entry), _iso_utc(settle))


def summarize_difference_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    reconcilable: bool,
) -> dict[str, Any]:
    materialized = list(rows)
    effective_reconcilable = reconcilable and all(
        row.get("reconcilable", True) for row in materialized
    )
    by_type: dict[str, dict[str, Any]] = {}
    for difference_type in DIFFERENCE_TYPES:
        typed = [row for row in materialized if row["primary_type"] == difference_type]
        if not typed:
            continue
        by_type[difference_type] = _money_summary(
            typed,
            reconcilable=effective_reconcilable,
        )
    summary = _money_summary(materialized, reconcilable=effective_reconcilable)
    summary["difference_count"] = sum(row["primary_type"] != "matched" for row in materialized)
    summary["matched_count"] = sum(row["primary_type"] == "matched" for row in materialized)
    summary["by_type"] = by_type
    return summary


def reconcile_strategy_orders(
    *,
    live_records: Sequence[Mapping[str, Any]],
    formal_predictions: Sequence[Mapping[str, Any]],
    scored_records: Sequence[Mapping[str, Any]],
    start_at: datetime | None,
    end_at: datetime,
    ledger_available: bool,
    scored_available: bool,
) -> dict[str, Any]:
    live_by_market, live_quality = _aggregate_live_markets(
        live_records,
        start_at=start_at,
        end_at=end_at,
    )
    scored_by_market, scored_quality = _aggregate_scored_markets(
        scored_records,
        start_at=start_at,
        end_at=end_at,
    )
    formal_by_market = _aggregate_formal_markets(
        formal_predictions,
        start_at=start_at,
        end_at=end_at,
    )
    trusted_live_keys = {key for key, value in live_by_market.items() if value["present"]}
    conflicting_scored_keys = set(scored_quality["conflicting_scored_markets"])
    keys = sorted((trusted_live_keys | set(scored_by_market)) - conflicting_scored_keys)
    rows = [
        _reconcile_market(
            key,
            live=live_by_market.get(key),
            formal=formal_by_market.get(key),
            simulated=scored_by_market.get(key),
            ledger_available=ledger_available,
            scored_available=scored_available,
        )
        for key in keys
    ]
    warnings = [*live_quality["warnings"], *scored_quality["warnings"]]
    if not ledger_available:
        warnings.append("live ledger missing; live PnL is unknown")
    if not scored_available:
        warnings.append("candidate scored summary missing")
    global_reconcilable = (
        ledger_available
        and scored_available
        and not conflicting_scored_keys
        and live_quality["excluded_live_reference_count"] == 0
    )
    return {
        "rows": rows,
        "warnings": warnings,
        "data_quality": {
            "reconcilable": global_reconcilable,
            "excluded_live_reference_count": live_quality[
                "excluded_live_reference_count"
            ],
            "conflicting_scored_markets": scored_quality[
                "conflicting_scored_markets"
            ],
        },
    }


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _side(record: Mapping[str, Any]) -> str | None:
    for key in ("side", "direction", "action"):
        value = str(record.get(key) or "").strip().upper()
        if value in {"LONG", "UP", "BUY_UP"}:
            return "LONG"
        if value in {"SHORT", "DOWN", "BUY_DOWN"}:
            return "SHORT"
    return None


def _won(record: Mapping[str, Any]) -> bool | None:
    value = record.get("won")
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().upper()
        if normalized in {"TRUE", "WON", "WIN", "YES"}:
            return True
        if normalized in {"FALSE", "LOST", "LOSS", "NO"}:
            return False
    return None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_number(record: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _number(record.get(key))
        if value is not None:
            return value
    return None


def _explicit_pnl(record: Mapping[str, Any]) -> float | None:
    return _first_number(record, "pnl_usdc", "realized_pnl", "net_pnl", "pnl")


def _in_window(
    record: Mapping[str, Any],
    *,
    start_at: datetime | None,
    end_at: datetime,
) -> bool:
    key = market_key(record)
    if key is None:
        return False
    entry = _parse_dt(key[0])
    end = _parse_dt(end_at)
    start = _parse_dt(start_at)
    return bool(entry is not None and end is not None and entry <= end and (start is None or entry >= start))


def _order_updated_at(record: Mapping[str, Any]) -> datetime:
    return (
        _parse_dt(record.get("updated_at"))
        or _parse_dt(record.get("created_at"))
        or datetime.min.replace(tzinfo=timezone.utc)
    )


def _dedupe_live_records(records: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    materialized = list(records)
    parents = list(range(len(materialized)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    known: dict[tuple[str, str], int] = {}
    identified: set[int] = set()
    for index, record in enumerate(materialized):
        for key in ("order_id", "order_key"):
            value = record.get(key)
            if value in (None, ""):
                continue
            identified.add(index)
            marker = (key, str(value))
            previous = known.get(marker)
            if previous is None:
                known[marker] = index
            else:
                union(index, previous)

    groups: dict[int, list[int]] = defaultdict(list)
    for index in identified:
        groups[find(index)].append(index)
    selected = {
        max(indexes, key=lambda index: (_order_updated_at(materialized[index]), index))
        for indexes in groups.values()
    }
    selected.update(index for index in range(len(materialized)) if index not in identified)
    return [materialized[index] for index in sorted(selected)]


def _is_final_filled(record: Mapping[str, Any]) -> bool:
    status = str(record.get("status") or "").upper()
    filled_size = _first_number(
        record,
        "filled_size",
        "fill_size",
        "size_matched",
        "matched_size",
    )
    return status in FINAL_STATUSES and (filled_size or 0.0) > EPSILON


def _has_datastreams_reference(record: Mapping[str, Any]) -> bool:
    source = str(record.get("reference_price_source") or "").strip().lower()
    return source.startswith("chainlink_datastreams")


def _aggregate_live_markets(
    records: Sequence[Mapping[str, Any]],
    *,
    start_at: datetime | None,
    end_at: datetime,
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, Any]]:
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for record in _dedupe_live_records(records):
        key = market_key(record)
        if key is not None and _in_window(record, start_at=start_at, end_at=end_at):
            grouped[key].append(record)

    excluded_live_reference_count = 0
    markets: dict[tuple[str, str], dict[str, Any]] = {}
    for key, market_records in grouped.items():
        attempts = market_records
        trusted_fills: list[Mapping[str, Any]] = []
        excluded_final_fill_count = 0
        no_fill_attempt_count = 0
        for record in attempts:
            if str(record.get("status") or "").upper() in NO_FILL_STATUSES:
                no_fill_attempt_count += 1
            if not _is_final_filled(record):
                continue
            if not _has_datastreams_reference(record):
                excluded_live_reference_count += 1
                excluded_final_fill_count += 1
                continue
            trusted_fills.append(record)

        filled_size = sum(
            _first_number(record, "filled_size", "fill_size", "size_matched", "matched_size") or 0.0
            for record in trusted_fills
        )
        weighted_price_total = sum(
            ( _first_number(record, "filled_size", "fill_size", "size_matched", "matched_size") or 0.0)
            * (_first_number(record, "average_fill_price", "avg_fill_price", "filled_avg_price", "price", "limit_price") or 0.0)
            for record in trusted_fills
        )
        latest_fill = max(trusted_fills, key=_order_updated_at, default=None)
        explicit_pnls = [_explicit_pnl(record) for record in trusted_fills]
        missing_pnl_count = sum(pnl is None for pnl in explicit_pnls)
        live_pnl = (
            round(sum(pnl for pnl in explicit_pnls if pnl is not None), 6)
            if missing_pnl_count == 0
            else None
        )
        markets[key] = {
            "present": bool(trusted_fills),
            "attempts": len(attempts),
            "filled_size": round(filled_size, 6),
            "average_fill_price": round(weighted_price_total / filled_size, 6) if filled_size > EPSILON else None,
            "side": _side(latest_fill) if latest_fill is not None else None,
            "won": _won(latest_fill) if latest_fill is not None else None,
            "pnl": live_pnl,
            "excluded_final_fill_count": excluded_final_fill_count,
            "missing_pnl_count": missing_pnl_count,
            "no_fill_attempt_count": no_fill_attempt_count,
        }

    warnings = []
    if excluded_live_reference_count:
        warnings.append(
            f"excluded {excluded_live_reference_count} final live fills without Data Streams reference"
        )
    return markets, {
        "warnings": warnings,
        "excluded_live_reference_count": excluded_live_reference_count,
    }


def _scored_signature(record: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        _side(record),
        _won(record),
        _first_number(record, "size_shares", "order_size_shares", "size"),
        _first_number(record, "maker_price", "average_fill_price", "price", "limit_price"),
        _explicit_pnl(record),
    )


def _aggregate_scored_markets(
    records: Sequence[Mapping[str, Any]],
    *,
    start_at: datetime | None,
    end_at: datetime,
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, Any]]:
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        key = market_key(record)
        if key is not None and _in_window(record, start_at=start_at, end_at=end_at):
            grouped[key].append(record)

    conflicts: list[tuple[str, str]] = []
    markets: dict[tuple[str, str], dict[str, Any]] = {}
    for key, market_records in grouped.items():
        signatures = {_scored_signature(record) for record in market_records}
        if len(signatures) != 1:
            conflicts.append(key)
            continue
        side, won, size, price, pnl = signatures.pop()
        markets[key] = {
            "present": True,
            "attempts": 1,
            "side": side,
            "won": won,
            "size": size,
            "price": price,
            "pnl": round(pnl, 6) if pnl is not None else None,
            "missing_pnl_count": int(pnl is None),
        }

    warnings = []
    if conflicts:
        warnings.append(f"excluded {len(conflicts)} conflicting scored market records")
    return markets, {"warnings": warnings, "conflicting_scored_markets": sorted(conflicts)}


def _aggregate_formal_markets(
    records: Sequence[Mapping[str, Any]],
    *,
    start_at: datetime | None,
    end_at: datetime,
) -> dict[tuple[str, str], dict[str, Any]]:
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        key = market_key(record)
        if key is not None and _in_window(record, start_at=start_at, end_at=end_at):
            grouped[key].append(record)
    return {
        key: {
            "side": _side(max(market_records, key=_order_updated_at)),
            "records": len(market_records),
        }
        for key, market_records in grouped.items()
    }


def _numbers_match(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return left is right
    return abs(left - right) <= EPSILON


def _reconcile_market(
    key: tuple[str, str],
    *,
    live: Mapping[str, Any] | None,
    formal: Mapping[str, Any] | None,
    simulated: Mapping[str, Any] | None,
    ledger_available: bool,
    scored_available: bool,
) -> dict[str, Any]:
    live_present = bool(live and live.get("present"))
    simulated_present = bool(simulated and simulated.get("present"))
    has_excluded_final_fill = bool(
        live and live.get("excluded_final_fill_count", 0) > 0
    )
    missing_live_pnl = bool(live and live.get("missing_pnl_count", 0) > 0)
    missing_simulated_pnl = bool(
        simulated and simulated.get("missing_pnl_count", 0) > 0
    )
    if not ledger_available or has_excluded_final_fill or missing_live_pnl:
        live_pnl = None
    elif live_present:
        live_pnl = round(float(live["pnl"]), 6)
    else:
        live_pnl = 0.0
    if not scored_available or missing_simulated_pnl:
        simulated_pnl = None
    elif simulated_present:
        simulated_pnl = round(float(simulated["pnl"]), 6)
    else:
        simulated_pnl = 0.0
    pnl_delta = (
        round(live_pnl - simulated_pnl, 6)
        if live_pnl is not None and simulated_pnl is not None
        else None
    )
    difference_flags: list[str] = []

    if live_present and simulated_present:
        if live.get("side") != simulated.get("side"):
            difference_flags.append("side")
        if live.get("won") != simulated.get("won"):
            difference_flags.append("outcome")
        execution_pairs = (
            ("size", live.get("filled_size"), simulated.get("size")),
            ("price", live.get("average_fill_price"), simulated.get("price")),
            ("attempts", live.get("attempts"), simulated.get("attempts")),
        )
        difference_flags.extend(
            name for name, left, right in execution_pairs if not _numbers_match(left, right)
        )
        if live_pnl is not None and simulated_pnl is not None and not _numbers_match(live_pnl, simulated_pnl):
            difference_flags.append("pnl")
        if "side" in difference_flags:
            primary_type = "side_mismatch"
            reason_label = "live and simulated sides differ"
        elif "outcome" in difference_flags:
            primary_type = "outcome_mismatch"
            reason_label = "live and simulated outcomes differ"
        elif difference_flags:
            primary_type = "execution_mismatch"
            reason_label = "live and simulated execution fields differ"
        else:
            primary_type = "matched"
            reason_label = "live and simulated records match"
    elif live_present:
        primary_type = "live_only"
        difference_flags = ["missing_source"]
        reason_label = "no simulated trade for this market"
    else:
        if live and live.get("excluded_final_fill_count", 0) > 0:
            primary_type = "simulated_only_unknown"
            difference_flags = ["missing_source"]
            reason_label = "live settlement source is not comparable"
        elif live and live.get("no_fill_attempt_count", 0) > 0:
            primary_type = "simulated_no_fill"
            difference_flags = ["no_fill"]
            reason_label = "live order attempt did not fill"
        elif formal:
            primary_type = "simulated_only_unknown"
            difference_flags = ["missing_source"]
            reason_label = "no comparable live fill for this market"
        else:
            primary_type = "simulated_only_unknown"
            difference_flags = ["missing_source"]
            reason_label = "live and formal sources do not explain this market"

    return {
        "entry_ts": key[0],
        "settle_ts": key[1],
        "market": {"entry_ts": key[0], "settle_ts": key[1]},
        "primary_type": primary_type,
        "difference_flags": difference_flags,
        "reason_label": reason_label,
        "live": dict(live) if live is not None else None,
        "formal": dict(formal) if formal is not None else None,
        "simulated": dict(simulated) if simulated is not None else None,
        "reconcilable": (
            ledger_available
            and scored_available
            and not has_excluded_final_fill
            and not missing_live_pnl
            and not missing_simulated_pnl
        ),
        "live_pnl": live_pnl,
        "simulated_pnl": simulated_pnl,
        "pnl_delta": pnl_delta,
    }


def _money_summary(
    rows: Iterable[Mapping[str, Any]],
    *,
    reconcilable: bool,
) -> dict[str, Any]:
    materialized = list(rows)
    summary: dict[str, Any] = {"market_count": len(materialized)}
    if not reconcilable or any(
        row.get("live_pnl") is None or row.get("simulated_pnl") is None
        for row in materialized
    ):
        summary.update({"live_pnl": None, "simulated_pnl": None, "pnl_delta": None})
        return summary
    live_pnl = round(sum(float(row["live_pnl"]) for row in materialized), 6)
    simulated_pnl = round(sum(float(row["simulated_pnl"]) for row in materialized), 6)
    summary.update(
        {
            "live_pnl": live_pnl,
            "simulated_pnl": simulated_pnl,
            "pnl_delta": round(live_pnl - simulated_pnl, 6),
        }
    )
    return summary
