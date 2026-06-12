# Live Trading Safety Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move Kronos from paper/shadow monitoring toward real-money readiness by hardening dry-run and read-only audit gates, without enabling or submitting any real Polymarket orders.

**Architecture:** Keep `G:\kronos-dashboard` as the monitoring surface and `G:\Kronos` as the execution/runtime codebase. Strengthen `live_dryrun_aligned_prod_shift1` first, then surface its ledger and audit evidence in the dashboard. Real order submission remains blocked by existing kill switches and requires a separate user confirmation step outside this plan.

**Tech Stack:** Python, pytest, Flask, React/TypeScript, Polymarket Gamma/CLOB read APIs, JSON checkpoint/ledger files.

---

## File Structure

Primary execution repo changes in `G:\Kronos`:

- Modify: `G:\Kronos\scripts\run_live_dryrun_aligned_prod.py`  
  Adds strict market-end validation, optional account-readiness gating, clearer blocked ledger records, and pre-smoke audit output fields.
- Modify: `G:\Kronos\tests\test_scripts\test_run_live_dryrun_aligned_prod.py`  
  Covers exact market mapping, account-readiness blocks, and guard ordering.
- Create: `G:\Kronos\src\kronos_poly\execution\readiness.py`  
  Pure account/funding/open-order readiness checks shared by dry-run and read-only smoke gates.
- Create: `G:\Kronos\tests\test_execution\test_readiness.py`  
  Unit tests for readiness checks.
- Modify: `G:\Kronos\scripts\check_live_trade_gate.py`  
  Reuses the shared readiness evaluator so CLI and runner gates stay consistent.
- Modify: `G:\Kronos\tests\test_scripts\test_check_live_trade_gate.py`  
  Updates expected blockers and adds allowance/open-order cases.
- Modify: `G:\Kronos\scripts\audit_polymarket_clob_readonly.py`  
  Adds SDK/package metadata and explicit “no write methods called” evidence to JSON reports.
- Modify: `G:\Kronos\tests\test_scripts\test_audit_polymarket_clob_readonly.py`  
  Covers SDK metadata and write-boundary evidence.

Dashboard repo changes in `G:\kronos-dashboard`:

- Modify: `G:\kronos-dashboard\api\server.py`  
  Reads dry-run ledger and read-only audit reports into `/api/live-safety`.
- Create: `G:\kronos-dashboard\tests\test_live_safety_readiness.py`  
  Tests ledger/audit summary and checklist behavior.
- Modify: `G:\kronos-dashboard\web\src\pages\Live.tsx`  
  Mounts the already-defined `SafetyStrip` and `ReadinessChecklist`, and displays dry-run audit status.
- Modify: `G:\kronos-dashboard\web\src\components\StatusBar.tsx`  
  Adds concise dry-run ledger/audit status chips.

---

### Task 1: Strict Market-End Mapping Guard

**Files:**
- Modify: `G:\Kronos\tests\test_scripts\test_run_live_dryrun_aligned_prod.py`
- Modify: `G:\Kronos\scripts\run_live_dryrun_aligned_prod.py`

- [ ] **Step 1: Write failing tests**

Add these tests near the existing dry-run market tests:

```python
class WrongEndResolver:
    def find_next_up_down_market(self, asset, after_iso="", target_end_iso=""):
        wrong_end = (
            pd.Timestamp(target_end_iso).tz_convert("UTC") + pd.Timedelta(minutes=5)
        ).isoformat().replace("+00:00", "Z")
        return MarketSelection(
            market=PolymarketMarket(
                market_id="wrong-end",
                slug="bitcoin-updown-5m-wrong",
                question="Bitcoin Up or Down?",
                active=True,
                closed=False,
                tokens={"UP": "up-token", "DOWN": "down-token"},
                end_date_iso=wrong_end,
            ),
            reason="wrong market",
        )


def test_market_end_mismatch_blocks_before_orderbook_read():
    def raising_book(token_id):
        raise AssertionError("order book should not be read for wrong market")

    result, state, ledger = _process_once(
        resolver=WrongEndResolver(),
        order_book_getter=raising_book,
    )

    assert result["status"] == "blocked"
    assert result["block_reason"] == "market_end_mismatch"
    assert len(ledger) == 1
    assert ledger[0]["would_place_order"] is False
    assert ledger[0]["block_reason"] == "market_end_mismatch"
    assert ledger[0]["market"]["expected_end_iso"] == "2026-06-11T00:50:00Z"
    assert ledger[0]["market"]["end_date_iso"] == "2026-06-11T00:55:00Z"
    assert state["guard_counts"]["market_end_mismatch"] == 1
```

- [ ] **Step 2: Run test to verify failure**

Run from `G:\Kronos`:

```powershell
python -m pytest tests\test_scripts\test_run_live_dryrun_aligned_prod.py::test_market_end_mismatch_blocks_before_orderbook_read -q
```

Expected: FAIL because the dry-run runner currently accepts the closest market returned by the resolver.

- [ ] **Step 3: Implement strict market-end helper**

Add to `scripts/run_live_dryrun_aligned_prod.py` near `_iso_z`:

```python
def _market_end_matches(actual_iso: str, expected_ts: pd.Timestamp, tolerance_seconds: float = 2.0) -> bool:
    if not actual_iso:
        return False
    try:
        actual = _pd_ts(actual_iso)
    except Exception:
        return False
    expected = _pd_ts(expected_ts)
    return abs((actual - expected).total_seconds()) <= tolerance_seconds
```

After resolving `market` and before `token_id = market.token_for_direction(direction)`, add:

```python
    if not _market_end_matches(market.end_date_iso, settle_ts):
        block = _blocked_record(
            decision_record=record,
            order_key=order_key,
            entry_ts=entry_ts,
            settle_ts=settle_ts,
            direction=direction,
            block_reason="market_end_mismatch",
        )
        block["market"] = {
            "id": market.market_id,
            "slug": market.slug,
            "question": market.question,
            "end_date_iso": market.end_date_iso,
            "expected_end_iso": _iso_z(settle_ts),
            "selection_reason": selection_reason,
        }
        ledger.append(block)
        _increment_guard(state, "market_end_mismatch")
        if events_dir is not None:
            _append_event(events_dir, {"type": "blocked", "order": block})
        return {"status": "blocked", "block_reason": "market_end_mismatch", **record}
```

- [ ] **Step 4: Run focused dry-run tests**

Run:

```powershell
python -m pytest tests\test_scripts\test_run_live_dryrun_aligned_prod.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Do not commit automatically unless the user asks. If committing later:

```powershell
git add scripts\run_live_dryrun_aligned_prod.py tests\test_scripts\test_run_live_dryrun_aligned_prod.py
git commit -m "feat: enforce live dry-run market end guard"
```

### Task 2: Shared Account Readiness Evaluator

**Files:**
- Create: `G:\Kronos\src\kronos_poly\execution\readiness.py`
- Create: `G:\Kronos\tests\test_execution\test_readiness.py`
- Modify: `G:\Kronos\scripts\check_live_trade_gate.py`
- Modify: `G:\Kronos\tests\test_scripts\test_check_live_trade_gate.py`

- [ ] **Step 1: Write failing unit tests**

Create `tests\test_execution\test_readiness.py`:

```python
from kronos_poly.execution.readiness import LiveReadinessConfig, evaluate_account_readiness


def _snapshot(**overrides):
    payload = {
        "authenticated": True,
        "orders_read_ok": True,
        "balance_read_ok": True,
        "allowance_read_ok": True,
        "open_orders_count": 0,
        "usdc_balance": 25.0,
        "min_allowance": 25.0,
    }
    payload.update(overrides)
    return payload


def test_account_readiness_passes_with_balance_allowance_and_no_open_orders():
    result = evaluate_account_readiness(
        _snapshot(),
        LiveReadinessConfig(
            min_balance_usdc=10.0,
            min_allowance_usdc=10.0,
            order_size_shares=5.0,
            max_price=0.52,
        ),
    )

    assert result.ok is True
    assert result.blockers == []


def test_account_readiness_blocks_missing_auth_balance_allowance_and_open_orders():
    result = evaluate_account_readiness(
        _snapshot(
            authenticated=False,
            orders_read_ok=False,
            balance_read_ok=False,
            allowance_read_ok=False,
            open_orders_count=1,
            usdc_balance=0.0,
            min_allowance=0.0,
        ),
        LiveReadinessConfig(
            min_balance_usdc=10.0,
            min_allowance_usdc=10.0,
            order_size_shares=5.0,
            max_price=0.52,
        ),
    )

    assert result.ok is False
    assert result.blockers == [
        "clob_account_authenticated",
        "account_open_orders_read_ok",
        "account_balance_read_ok",
        "account_allowance_read_ok",
        "no_open_orders_before_smoke",
        "balance_meets_minimum",
        "balance_covers_smoke_notional",
        "allowance_meets_minimum",
    ]
```

- [ ] **Step 2: Run test to verify failure**

Run:

```powershell
python -m pytest tests\test_execution\test_readiness.py -q
```

Expected: FAIL because `kronos_poly.execution.readiness` does not exist.

- [ ] **Step 3: Implement pure readiness module**

Create `src\kronos_poly\execution\readiness.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LiveReadinessConfig:
    min_balance_usdc: float = 10.0
    min_allowance_usdc: float = 10.0
    order_size_shares: float = 5.0
    max_price: float = 0.52
    allow_open_orders: bool = False


@dataclass(frozen=True)
class LiveReadinessDecision:
    ok: bool
    blockers: list[str]
    checks: list[dict[str, Any]]
    metrics: dict[str, Any]


def _check(name: str, ok: bool, value: Any = None, expected: Any = None) -> dict[str, Any]:
    return {
        "name": name,
        "ok": bool(ok),
        "value": value,
        "expected": expected,
    }


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def evaluate_account_readiness(
    snapshot: dict[str, Any],
    config: LiveReadinessConfig,
) -> LiveReadinessDecision:
    usdc_balance = _float(snapshot.get("usdc_balance"))
    min_allowance = _float(snapshot.get("min_allowance"))
    open_orders_count = int(snapshot.get("open_orders_count", 0) or 0)
    max_notional_usdc = config.order_size_shares * config.max_price

    checks = [
        _check("clob_account_authenticated", bool(snapshot.get("authenticated")), snapshot.get("authenticated"), True),
        _check("account_open_orders_read_ok", bool(snapshot.get("orders_read_ok", True)), snapshot.get("orders_error", ""), "open orders read succeeds"),
        _check("account_balance_read_ok", bool(snapshot.get("balance_read_ok", True)), snapshot.get("balance_error", ""), "balance read succeeds"),
        _check("account_allowance_read_ok", bool(snapshot.get("allowance_read_ok", True)), snapshot.get("allowance_error", ""), "allowance read succeeds"),
        _check("no_open_orders_before_smoke", config.allow_open_orders or open_orders_count == 0, open_orders_count, "0 unless explicitly allowed"),
        _check("balance_meets_minimum", usdc_balance >= config.min_balance_usdc, usdc_balance, f">= {config.min_balance_usdc}"),
        _check("balance_covers_smoke_notional", usdc_balance >= max_notional_usdc, {"balance": usdc_balance, "max_notional_usdc": max_notional_usdc}, "balance >= order size * max price"),
        _check("allowance_meets_minimum", min_allowance >= config.min_allowance_usdc, min_allowance, f">= {config.min_allowance_usdc}"),
    ]
    blockers = [check["name"] for check in checks if not check["ok"]]
    return LiveReadinessDecision(
        ok=not blockers,
        blockers=blockers,
        checks=checks,
        metrics={
            "usdc_balance": usdc_balance,
            "min_allowance": min_allowance,
            "open_orders_count": open_orders_count,
            "max_notional_usdc": max_notional_usdc,
        },
    )
```

- [ ] **Step 4: Reuse evaluator in `check_live_trade_gate.py`**

Import:

```python
from kronos_poly.execution.readiness import LiveReadinessConfig, evaluate_account_readiness
```

Inside `build_gate_report`, replace account-related checks with:

```python
    account_decision = evaluate_account_readiness(
        account_state,
        LiveReadinessConfig(
            min_balance_usdc=min_balance_usdc,
            min_allowance_usdc=float(account_state.get("min_allowance_required", 0.0) or 0.0),
            order_size_shares=order_size_shares,
            max_price=max_price,
            allow_open_orders=allow_open_orders,
        ),
    )
    checks.extend(account_decision.checks)
```

Keep the existing market-probe checks unchanged.

- [ ] **Step 5: Run tests**

Run:

```powershell
python -m pytest tests\test_execution\test_readiness.py tests\test_scripts\test_check_live_trade_gate.py -q
```

Expected: PASS after adjusting any changed blocker order in `test_check_live_trade_gate.py`.

- [ ] **Step 6: Commit**

Do not commit automatically unless the user asks. If committing later:

```powershell
git add src\kronos_poly\execution\readiness.py tests\test_execution\test_readiness.py scripts\check_live_trade_gate.py tests\test_scripts\test_check_live_trade_gate.py
git commit -m "feat: add shared live account readiness checks"
```

### Task 3: Optional Account Gate in Live Dry-Run

**Files:**
- Modify: `G:\Kronos\tests\test_scripts\test_run_live_dryrun_aligned_prod.py`
- Modify: `G:\Kronos\scripts\run_live_dryrun_aligned_prod.py`

- [ ] **Step 1: Write failing tests**

Add to `tests\test_scripts\test_run_live_dryrun_aligned_prod.py`:

```python
def test_account_readiness_guard_blocks_before_market_lookup():
    class RaisingResolver:
        def find_next_up_down_market(self, *args, **kwargs):
            raise AssertionError("market lookup should not run")

    result, state, ledger = _process_once(
        resolver=RaisingResolver(),
        account_snapshot={
            "authenticated": True,
            "orders_read_ok": True,
            "balance_read_ok": True,
            "allowance_read_ok": True,
            "open_orders_count": 0,
            "usdc_balance": 1.0,
            "min_allowance": 25.0,
        },
        require_account_readiness=True,
    )

    assert result["status"] == "blocked"
    assert result["block_reason"] == "account_readiness"
    assert "balance_meets_minimum" in result["account_blockers"]
    assert len(ledger) == 1
    assert ledger[0]["account_readiness"]["ok"] is False
    assert state["guard_counts"]["account_readiness"] == 1
```

Update `_process_once` helper signature to accept:

```python
    account_snapshot=None,
    require_account_readiness=False,
```

and pass these through to `process_latest_decision`.

- [ ] **Step 2: Run test to verify failure**

Run:

```powershell
python -m pytest tests\test_scripts\test_run_live_dryrun_aligned_prod.py::test_account_readiness_guard_blocks_before_market_lookup -q
```

Expected: FAIL because `process_latest_decision` has no account-readiness arguments.

- [ ] **Step 3: Add runner arguments and block logic**

Import:

```python
from kronos_poly.execution.readiness import LiveReadinessConfig, evaluate_account_readiness
```

Add keyword arguments to `process_latest_decision`:

```python
    account_snapshot: dict[str, Any] | None = None,
    require_account_readiness: bool = False,
    min_balance_usdc: float = 10.0,
    min_allowance_usdc: float = 10.0,
```

After the risk guard and before signal-age/market lookup:

```python
    if require_account_readiness:
        account_decision = evaluate_account_readiness(
            account_snapshot or {},
            LiveReadinessConfig(
                min_balance_usdc=min_balance_usdc,
                min_allowance_usdc=min_allowance_usdc,
                order_size_shares=stake,
                max_price=max_price,
            ),
        )
        state["account_readiness"] = {
            "ok": account_decision.ok,
            "blockers": list(account_decision.blockers),
            "metrics": dict(account_decision.metrics),
            "checks": list(account_decision.checks),
        }
        if not account_decision.ok:
            block = _blocked_record(
                decision_record=record,
                order_key=order_key,
                entry_ts=entry_ts,
                settle_ts=settle_ts,
                direction=direction,
                block_reason="account_readiness",
            )
            block["account_readiness"] = state["account_readiness"]
            ledger.append(block)
            _increment_guard(state, "account_readiness")
            if events_dir is not None:
                _append_event(events_dir, {"type": "blocked", "order": block})
            return {
                "status": "blocked",
                "block_reason": "account_readiness",
                "account_blockers": list(account_decision.blockers),
                **record,
            }
```

Add parser args:

```python
    parser.add_argument("--require-account-readiness", action="store_true")
    parser.add_argument("--min-balance-usdc", type=float, default=10.0)
    parser.add_argument("--min-allowance-usdc", type=float, default=10.0)
```

For this task, do not add live network account reads to the long-running runner. Feed `account_snapshot=None` by default, so the flag is off unless explicitly wired by a later audit wrapper.

- [ ] **Step 4: Run focused tests**

Run:

```powershell
python -m pytest tests\test_execution\test_readiness.py tests\test_scripts\test_run_live_dryrun_aligned_prod.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Do not commit automatically unless the user asks. If committing later:

```powershell
git add scripts\run_live_dryrun_aligned_prod.py tests\test_scripts\test_run_live_dryrun_aligned_prod.py
git commit -m "feat: add account readiness gate to live dry-run"
```

### Task 4: Read-Only Audit Evidence Expansion

**Files:**
- Modify: `G:\Kronos\tests\test_scripts\test_audit_polymarket_clob_readonly.py`
- Modify: `G:\Kronos\scripts\audit_polymarket_clob_readonly.py`

- [ ] **Step 1: Write failing tests**

Add:

```python
def test_local_audit_reports_sdk_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        audit,
        "_sdk_import_report",
        lambda: (True, {"py_clob_client_v2": "ok"}),
    )

    report = audit.build_local_audit({})

    assert "sdk" in report
    assert report["sdk"]["importable"] is True
    assert "modules" in report["sdk"]
    assert "version" in report["sdk"]


def test_network_audit_reports_no_write_methods_called() -> None:
    client = FakeReadOnlyClob()

    report = asyncio.run(
        audit.run_readonly_network_audit(
            token_id="token-123",
            account_read=True,
            allowance_read=True,
            client=client,  # type: ignore[arg-type]
        )
    )

    checks = {check["name"]: check for check in report["checks"]}
    assert checks["write_methods_called"]["ok"] is True
    assert checks["write_methods_called"]["value"] == []
    assert client.write_calls == []
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
python -m pytest tests\test_scripts\test_audit_polymarket_clob_readonly.py -q
```

Expected: FAIL on SDK version metadata if absent.

- [ ] **Step 3: Add SDK version helper**

In `audit_polymarket_clob_readonly.py`, import:

```python
import importlib.metadata
```

Add helper:

```python
def _sdk_version() -> str:
    for package_name in ("py-clob-client-v2", "py_clob_client_v2", "py-clob-client"):
        try:
            return importlib.metadata.version(package_name)
        except importlib.metadata.PackageNotFoundError:
            continue
    return ""
```

Set SDK report:

```python
        "sdk": {"importable": sdk_ok, "modules": sdk_modules, "version": _sdk_version()},
```

Keep `run_readonly_network_audit` read-only: do not call `disconnect()`, `cancel_order()`, or `cancel_all()`.

- [ ] **Step 4: Run audit tests**

Run:

```powershell
python -m pytest tests\test_scripts\test_audit_polymarket_clob_readonly.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Do not commit automatically unless the user asks. If committing later:

```powershell
git add scripts\audit_polymarket_clob_readonly.py tests\test_scripts\test_audit_polymarket_clob_readonly.py
git commit -m "feat: expand readonly clob audit evidence"
```

### Task 5: Dashboard Live-Safety Dry-Run Summary

**Files:**
- Create: `G:\kronos-dashboard\tests\test_live_safety_readiness.py`
- Modify: `G:\kronos-dashboard\api\server.py`

- [ ] **Step 1: Write failing tests**

Create `tests\test_live_safety_readiness.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from api import server


def test_live_safety_includes_dryrun_ledger_summary(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir = tmp_path / "data" / "reports"
    checkpoint_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    ledger = [
        {"status": "would_place", "would_place_order": True, "submitted": False},
        {"status": "blocked", "block_reason": "market_end_mismatch", "submitted": False},
    ]
    (checkpoint_dir / "live_dryrun_aligned_prod_shift1_ledger.json").write_text(
        json.dumps(ledger),
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setenv("DASHBOARD_RUN_SOURCE", "live_dryrun_aligned_prod_shift1")

    summary = server._safety_report_summary()

    assert summary["dryrun"]["ledger_count"] == 2
    assert summary["dryrun"]["would_place_count"] == 1
    assert summary["dryrun"]["blocked_count"] == 1
    assert summary["dryrun"]["submitted_count"] == 0
    assert any(item["key"] == "dryrun_no_submitted_orders" for item in summary["checklist"])


def test_live_safety_marks_dryrun_submitted_order_as_critical(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "data" / "checkpoints"
    report_dir = tmp_path / "data" / "reports"
    checkpoint_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    (checkpoint_dir / "live_dryrun_aligned_prod_shift1_ledger.json").write_text(
        json.dumps([{"status": "would_place", "submitted": True}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "KRONOS_CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr(server, "KRONOS_REPORT_DIR", report_dir)
    monkeypatch.setenv("DASHBOARD_RUN_SOURCE", "live_dryrun_aligned_prod_shift1")

    summary = server._safety_report_summary()
    checks = {item["key"]: item for item in summary["checklist"]}

    assert checks["dryrun_no_submitted_orders"]["ok"] is False
    assert checks["dryrun_no_submitted_orders"]["severity"] == "critical"
```

- [ ] **Step 2: Run tests to verify failure**

Run from `G:\kronos-dashboard`:

```powershell
python -m pytest tests\test_live_safety_readiness.py -q
```

Expected: FAIL because `/api/live-safety` has no `dryrun` section.

- [ ] **Step 3: Implement ledger summary helper**

Add to `api/server.py` near `_safety_report_summary`:

```python
def _live_dryrun_ledger_summary():
    source = _paper_run_source()
    candidates = [
        KRONOS_CHECKPOINT_DIR / f"{source}_ledger.json",
        KRONOS_CHECKPOINT_DIR / "live_dryrun_aligned_prod_shift1_ledger.json",
    ]
    path = next((candidate for candidate in candidates if candidate.exists()), candidates[0])
    rows = _read_json(path) or []
    if not isinstance(rows, list):
        rows = []
    submitted = [row for row in rows if bool(row.get("submitted"))]
    would_place = [row for row in rows if row.get("would_place_order") is True]
    blocked = [row for row in rows if str(row.get("status", "")).lower() == "blocked"]
    return {
        "ledger": str(path),
        "ledger_count": len(rows),
        "would_place_count": len(would_place),
        "blocked_count": len(blocked),
        "submitted_count": len(submitted),
        "latest": rows[-1] if rows else None,
    }
```

In `_safety_report_summary`, set:

```python
    dryrun_summary = _live_dryrun_ledger_summary()
```

Add checklist item:

```python
        {
            "key": "dryrun_no_submitted_orders",
            "label": "Dry-run no submissions",
            "ok": dryrun_summary["submitted_count"] == 0,
            "value": dryrun_summary["submitted_count"],
            "severity": "critical",
        },
```

Add response field:

```python
        "dryrun": dryrun_summary,
```

- [ ] **Step 4: Run dashboard backend tests**

Run:

```powershell
python -m pytest tests -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Do not commit automatically unless the user asks. If committing later:

```powershell
git add api\server.py tests\test_live_safety_readiness.py
git commit -m "feat: expose live dry-run safety summary"
```

### Task 6: Dashboard Safety UI Wiring

**Files:**
- Modify: `G:\kronos-dashboard\web\src\pages\Live.tsx`
- Modify: `G:\kronos-dashboard\web\src\components\StatusBar.tsx`

- [ ] **Step 1: Extend TypeScript types**

In `LiveSafety`, add:

```ts
  dryrun?: {
    ledger: string;
    ledger_count: number;
    would_place_count: number;
    blocked_count: number;
    submitted_count: number;
    latest?: Record<string, unknown> | null;
  };
```

Make the same optional addition to `SafetyData` in `StatusBar.tsx`.

- [ ] **Step 2: Mount existing safety panels on Live page**

In `Live.tsx`, after `<BTCMarketChart />`, add:

```tsx
      <div className="grid gap-5 xl:grid-cols-[minmax(0,0.9fr)_minmax(420px,1.1fr)]">
        <SafetyStrip safety={safety} health={health} />
        <ReadinessChecklist safety={safety} health={health} intel={intel} />
      </div>
```

- [ ] **Step 3: Add dry-run chips in `StatusBar.tsx`**

Inside the main status chip row, add:

```tsx
          <StatusChip
            ok={(safety?.dryrun?.submitted_count ?? 0) === 0}
            label={`Dry-run ${safety?.dryrun?.would_place_count ?? 0}/${safety?.dryrun?.ledger_count ?? 0}`}
          />
```

Inside the expanded detail tiles, add:

```tsx
              <DetailTile
                label="Dry-run Ledger"
                value={`${safety?.dryrun?.would_place_count ?? 0}/${safety?.dryrun?.ledger_count ?? 0}`}
                ok={(safety?.dryrun?.submitted_count ?? 0) === 0}
              />
```

- [ ] **Step 4: Build frontend**

Run from `G:\kronos-dashboard\web`:

```powershell
npm run build
```

Expected: PASS.

- [ ] **Step 5: Commit**

Do not commit automatically unless the user asks. If committing later:

```powershell
git add web\src\pages\Live.tsx web\src\components\StatusBar.tsx
git commit -m "feat: show dry-run readiness on live dashboard"
```

### Task 7: Verification Pass

**Files:**
- No planned source changes.

- [ ] **Step 1: Verify Kronos execution tests**

Run from `G:\Kronos`:

```powershell
python -m pytest tests\test_execution\test_readiness.py tests\test_execution\test_real_order_safety.py tests\test_execution\test_order_ledger.py tests\test_scripts\test_run_live_dryrun_aligned_prod.py tests\test_scripts\test_check_live_trade_gate.py tests\test_scripts\test_audit_polymarket_clob_readonly.py -q
```

Expected: PASS.

- [ ] **Step 2: Verify dashboard backend tests**

Run from `G:\kronos-dashboard`:

```powershell
python -m pytest tests -q
```

Expected: PASS.

- [ ] **Step 3: Verify dashboard frontend build**

Run from `G:\kronos-dashboard\web`:

```powershell
npm run build
```

Expected: PASS.

- [ ] **Step 4: Run read-only audit only**

Run from `G:\Kronos`:

```powershell
python scripts\audit_polymarket_clob_readonly.py --json --output data\reports\polymarket_clob_readonly_audit_latest.json
```

Expected: completes without calling write methods. This command is local-only unless `--network-read` is explicitly passed.

- [ ] **Step 5: Confirm real-order path remains blocked**

Run from `G:\Kronos` without setting `KRONOS_ENABLE_REAL_ORDERS`:

```powershell
python scripts\validate_real_order_safety.py --json
```

Expected: exit code 1 and JSON reason `real orders disabled by code flag`.

---

## Explicit Real-Order Gate

This plan must not place, cancel, or settle real orders. The first real order is a separate future task and requires all of the following, in a fresh user-confirmed step:

- User explicitly says to place the tiny live smoke order.
- `KRONOS_ENABLE_REAL_ORDERS=YES` is set only for that command/session.
- `validate_real_order_safety.py` passes with `--enable-real-orders --i-understand-real-orders I_UNDERSTAND_REAL_ORDERS`.
- `check_live_trade_gate.py --strict` passes immediately before submission.
- Dry-run ledger has zero `submitted=true` records.
- Authenticated account read shows zero open orders unless user explicitly overrides.
- The exact target market end time matches the intended `settle_ts`.
- Last orderbook check passes post-only, min-size, max-price, max-notional, and market-close safety gates.

## Self-Review

Spec coverage:

- Paper/live/shadow inventory: covered through dashboard and `G:\Kronos` file responsibilities.
- Required real-market data: covered by strict market mapping, account readiness, orderbook quote, ledger, and audit tasks.
- Paper/live divergence: handled by dry-run ledger, market-end guard, orderbook guard, and explicit lifecycle limitations.
- Minimal safe path: dry-run -> read-only audit -> dashboard evidence -> separate future tiny smoke confirmation.
- Real order confirmation: explicitly excluded from this plan and gated in the final section.

Marker scan:

- No marker words remain.

Type consistency:

- New Python names are consistent: `LiveReadinessConfig`, `LiveReadinessDecision`, `evaluate_account_readiness`, `_market_end_matches`, `_live_dryrun_ledger_summary`.
