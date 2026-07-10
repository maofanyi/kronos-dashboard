# Dashboard Live Two-Layer Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild Dashboard Live as a concise two-layer trading cockpit and add a ledger-backed, historical monthly PnL calendar with drill-down to the settled orders that produced each day.

**Architecture:** Flask adds two isolated read-only calendar endpoints that aggregate the complete selected ledger with the same trading-day and settled-record helpers used by current live PnL. React keeps orchestration in `Live.tsx`, moves new cockpit, market, monthly calendar, current-action, and operations-detail surfaces into focused files under `web/src/pages/live/`, and keeps high-frequency market polling separate from on-demand historical queries.

**Tech Stack:** Python 3.11, Flask, pytest, React 18, TypeScript, Vite, Tailwind CSS, lucide-react, Playwright.

## Global Constraints

- The dashboard remains read-only: do not submit, cancel, repost, redeem, or mutate any Polymarket order.
- Do not change prediction, settlement, sizing, risk, live-enablement, or trade-profile configuration.
- Do not print `.env`, API keys, wallet secrets, CLOB credentials, or Chainlink credentials.
- Do not add frontend dependencies.
- Monthly and daily PnL must use `DASHBOARD_TRADING_DAY_TZ` and the existing settled-ledger helpers.
- A selected day's order-detail PnL sum must equal both its month-summary day PnL and the existing live PnL definition.
- Monthly data is fetched on month changes; daily details are fetched on day selection. Neither joins the periodic Live polling loop.
- Primary UI copy is Chinese while `PnL`, `CLOB`, `Chainlink`, `Live`, and strategy names may remain English.
- No document-level horizontal overflow is allowed at widths 390, 768, 1024, or 1440 px.
- The current worktree already contains unrelated edits in `api/server.py`, `api/static/`, `tests/test_live_safety_readiness.py`, `tests/test_strategy_comparison.py`, and `web/src/pages/Compare.tsx`. Never revert them and never stage those files wholesale without inspecting the exact staged diff.
- `npm run build` rewrites `api/static/`. Treat generated assets as verification output and leave them unstaged unless a final scoped review proves they represent the intended complete source state.

---

## File Structure

- Create `tests/test_live_pnl_calendar.py` - pure calendar aggregation and Flask route tests.
- Modify `api/server.py` - ledger selection, monthly/day aggregation helpers, and two read-only routes.
- Create `web/src/pages/live/types.ts` - Live feature DTOs and view-model types shared by extracted components.
- Create `web/src/pages/live/useCachedJson.ts` - one-shot, URL-keyed client cache for month/day requests.
- Create `web/src/pages/live/LiveMonthlyPnlCalendar.tsx` - natural-month grid, navigation, and selected-day order detail.
- Create `web/src/pages/live/LiveCockpitSummary.tsx` - six non-truncating decision metrics and critical alert.
- Create `web/src/pages/live/LiveCurrentAction.tsx` - latest decision, active order, and execution funnel.
- Create `web/src/pages/live/LiveMarketSection.tsx` - equity sequence and compact seven-day result strip around the existing BTC chart.
- Create `web/src/pages/live/LiveOperationsDetails.tsx` - four closed-by-default operations sections and mobile ledger records.
- Modify `web/src/components/BTCMarketChart.tsx` - compact chart composition and horizontal result history.
- Modify `web/src/pages/Live.tsx` - data orchestration and assembly of the two layers.
- Create `web/tests/live-redesign.spec.ts` - browser behavior and responsive regression coverage.
- Modify `tests/test_live_safety_readiness.py` only to replace old source-string assertions that are invalidated by the component extraction; do not change runtime expectations.

---

### Task 1: Ledger-Backed Calendar Aggregation

**Files:**
- Create: `tests/test_live_pnl_calendar.py`
- Modify: `api/server.py:1525-1770`

**Interfaces:**
- Produces: `_monthly_pnl_calendar_from_records(records, *, month_value) -> dict`
- Produces: `_daily_pnl_orders_from_records(records, *, day_value) -> dict`
- Produces: `_calendar_ledger_path(source) -> Path`
- Reuses: `_is_equity_settled_record`, `_record_ts`, `_dashboard_day_key`, `_record_pnl`, `_record_won`

- [ ] **Step 1: Write failing aggregation tests**

Create `tests/test_live_pnl_calendar.py` with a UTF-8 JSON helper and tests covering the Shanghai day boundary, a leap month, empty days, available months, and duplicate ledger rows:

```python
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from api import server


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _records() -> list[dict]:
    return [
        {
            "order_id": "june-last",
            "status": "SETTLED",
            "settled_at": "2026-06-30T15:55:00Z",
            "pnl": 1.25,
            "won": True,
        },
        {
            "order_id": "july-win",
            "signal_id": "signal-win",
            "status": "SETTLED",
            "settled_at": "2026-06-30T16:05:00Z",
            "pnl": 5.10,
            "won": True,
            "market_slug": "btc-updown-5m",
            "token_outcome": "UP",
            "filled_size": 10.0,
            "average_fill_price": 0.49,
            "settlement_source": "official_chainlink",
        },
        {
            "order_id": "july-loss",
            "status": "SETTLED",
            "settled_at": "2026-07-02T02:00:00Z",
            "pnl": -4.90,
            "won": False,
        },
        {
            "order_id": "july-loss",
            "status": "SETTLED",
            "settled_at": "2026-07-02T02:00:00Z",
            "pnl": -4.90,
            "won": False,
        },
        {
            "order_id": "ignored-open",
            "status": "OPEN",
            "created_at": "2026-07-02T03:00:00Z",
            "pnl": 1000.0,
        },
    ]


def test_month_calendar_uses_configured_trading_day_and_dedupes_records(monkeypatch):
    monkeypatch.setenv("DASHBOARD_TRADING_DAY_TZ", "Asia/Shanghai")

    payload = server._monthly_pnl_calendar_from_records(_records(), month_value="2026-07")

    by_date = {row["date"]: row for row in payload["days"]}
    assert len(payload["days"]) == 31
    assert payload["day_tz"] == "Asia/Shanghai"
    assert payload["available_months"] == ["2026-06", "2026-07"]
    assert by_date["2026-07-01"] == {
        "date": "2026-07-01", "pnl_usdc": 5.1, "settled": 1, "wins": 1, "losses": 0
    }
    assert by_date["2026-07-02"]["pnl_usdc"] == -4.9
    assert by_date["2026-07-03"]["settled"] == 0
    assert payload["total_pnl_usdc"] == 0.2
    assert payload["settled"] == 2


def test_month_calendar_returns_all_days_for_empty_leap_month(monkeypatch):
    monkeypatch.setenv("DASHBOARD_TRADING_DAY_TZ", "Asia/Shanghai")

    payload = server._monthly_pnl_calendar_from_records([], month_value="2028-02")

    assert len(payload["days"]) == 29
    assert payload["empty"] is True
    assert payload["total_pnl_usdc"] == 0.0


def test_daily_orders_reconcile_exactly_to_month_day(monkeypatch):
    monkeypatch.setenv("DASHBOARD_TRADING_DAY_TZ", "Asia/Shanghai")
    month = server._monthly_pnl_calendar_from_records(_records(), month_value="2026-07")
    day = server._daily_pnl_orders_from_records(_records(), day_value="2026-07-01")

    month_day = next(row for row in month["days"] if row["date"] == "2026-07-01")
    assert sum(row["pnl_usdc"] for row in day["orders"]) == pytest.approx(month_day["pnl_usdc"])
    assert day["total_pnl_usdc"] == month_day["pnl_usdc"]
    assert day["orders"][0]["settlement_source"] == "official_chainlink"
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
python -m pytest tests/test_live_pnl_calendar.py -q
```

Expected: FAIL because `_monthly_pnl_calendar_from_records` and `_daily_pnl_orders_from_records` do not exist.

- [ ] **Step 3: Implement shared settled-record selection and aggregation**

In `api/server.py`, import `monthrange` from `calendar`, then add helpers near `_weekly_pnl_calendar_from_records`:

```python
def _calendar_month(value):
    try:
        parsed = datetime.strptime(str(value), "%Y-%m")
    except (TypeError, ValueError) as exc:
        raise ValueError("month must use YYYY-MM") from exc
    return parsed.year, parsed.month


def _calendar_settled_records(records):
    selected = []
    seen = set()
    for index, record in enumerate(records or []):
        if not isinstance(record, dict) or not _is_equity_settled_record(record):
            continue
        ts = _record_ts(record)
        if ts is None:
            continue
        identity = str(record.get("order_id") or record.get("transaction_hash") or f"row:{index}")
        if identity in seen:
            continue
        seen.add(identity)
        selected.append(record)
    return selected


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
    for record in settled_records:
        day_key = _dashboard_day_key(_record_ts(record)).isoformat()
        if day_key not in days:
            continue
        item = days[day_key]
        item["pnl_usdc"] = round(item["pnl_usdc"] + _record_pnl(record), 8)
        item["settled"] += 1
        won = _record_won(record)
        item["wins"] += int(won is True)
        item["losses"] += int(won is False)
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
        "total_pnl_usdc": round(sum(row["pnl_usdc"] for row in ordered), 8),
        "settled": settled,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / settled, 4) if settled else 0.0,
        "empty": settled == 0,
        "source": "settled_ledger_trading_day",
    }
```

Implement `_daily_pnl_orders_from_records` from the same `_calendar_settled_records` list. Return only dashboard-safe fields and sort rows by settlement timestamp descending. Assert internally during tests that the rounded order sum equals `total_pnl_usdc`; do not expose ledger paths or raw records.

- [ ] **Step 4: Run focused tests and verify GREEN**

```powershell
python -m pytest tests/test_live_pnl_calendar.py -q
python -m pytest tests/test_live_safety_readiness.py -q
```

Expected: new tests PASS and existing live-safety tests remain PASS.

- [ ] **Step 5: Commit the isolated test file and reviewed server hunks**

Stage `tests/test_live_pnl_calendar.py` normally. Because `api/server.py` was dirty before this task, stage only the calendar import/helper hunks and inspect the cached diff:

```powershell
git diff -- api/server.py
git add tests/test_live_pnl_calendar.py
git diff --cached --check
git diff --cached --name-status
```

Commit only after `git diff --cached` contains no unrelated server change:

```powershell
git commit -m "feat: aggregate live pnl calendar"
```

---

### Task 2: Read-Only Calendar Endpoints

**Files:**
- Modify: `tests/test_live_pnl_calendar.py`
- Modify: `api/server.py:6776-6795`

**Interfaces:**
- Produces: `GET /api/live-pnl-calendar?source=<source>&month=YYYY-MM`
- Produces: `GET /api/live-pnl-calendar/orders?source=<source>&date=YYYY-MM-DD`
- Accepts sources: `live_real`, `paper_monitor`

- [ ] **Step 1: Write failing Flask route tests**

Append tests that write an isolated live ledger fixture, call both endpoints, reject invalid input, and never silently fall back between sources:

```python
def test_live_pnl_calendar_routes_are_read_only_and_reconcile(monkeypatch, tmp_path):
    ledger = tmp_path / "live.json"
    _write_json(ledger, _records())
    monkeypatch.setattr(server, "_live_real_ledger_path", lambda: ledger)
    monkeypatch.setenv("DASHBOARD_TRADING_DAY_TZ", "Asia/Shanghai")

    with server.app.test_client() as client:
        month_response = client.get("/api/live-pnl-calendar?source=live_real&month=2026-07")
        day_response = client.get("/api/live-pnl-calendar/orders?source=live_real&date=2026-07-01")

    assert month_response.status_code == 200
    assert day_response.status_code == 200
    month = month_response.get_json()
    day = day_response.get_json()
    assert month["source"] == "live_real"
    assert day["source"] == "live_real"
    assert day["total_pnl_usdc"] == next(row for row in month["days"] if row["date"] == "2026-07-01")["pnl_usdc"]
    assert "ledger" not in month
    assert "private_key" not in json.dumps(day).lower()


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("/api/live-pnl-calendar?source=live_real&month=2026-13", "month must use YYYY-MM"),
        ("/api/live-pnl-calendar/orders?source=live_real&date=bad", "date must use YYYY-MM-DD"),
        ("/api/live-pnl-calendar?source=unknown&month=2026-07", "unsupported source"),
    ],
)
def test_live_pnl_calendar_routes_reject_invalid_input(url, message):
    with server.app.test_client() as client:
        response = client.get(url)
    assert response.status_code == 400
    assert response.get_json()["error"] == message
```

- [ ] **Step 2: Run route tests and verify RED**

```powershell
python -m pytest tests/test_live_pnl_calendar.py -q
```

Expected: route tests FAIL with HTTP 404.

- [ ] **Step 3: Implement source selection and routes**

Add exact source selection with no fallback:

```python
def _calendar_ledger_path(source):
    if source == "live_real":
        return _live_real_ledger_path()
    if source == "paper_monitor":
        return _paper_ledger_path()
    raise ValueError("unsupported source")
```

Register two GET-only Flask routes near `/api/live-safety`. Each route reads the selected path through `_read_json` and `_rows_from_ledger_payload`, catches only input `ValueError`, and returns `jsonify({"error": str(exc)}), 400`. Add `source` to the helper payload before returning it. Date parsing must use `date.fromisoformat` and normalize its error to `date must use YYYY-MM-DD`.

- [ ] **Step 4: Verify routes and live-safety regression suite**

```powershell
python -m pytest tests/test_live_pnl_calendar.py tests/test_live_safety_readiness.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit only endpoint hunks**

Inspect and stage only the two route/source-selection hunks from the already-dirty `api/server.py`, then:

```powershell
git diff --cached --check
git commit -m "feat: expose read-only live pnl calendar"
```

---

### Task 3: Monthly Calendar Query Model And Component

**Files:**
- Create: `web/src/pages/live/types.ts`
- Create: `web/src/pages/live/useCachedJson.ts`
- Create: `web/src/pages/live/LiveMonthlyPnlCalendar.tsx`
- Create: `web/tests/live-redesign.spec.ts`
- Modify: `web/src/pages/Live.tsx:2644-2651,2734-2888`

**Interfaces:**
- Produces: `LiveMonthlyPnlCalendar({ source, initialMonth }: { source: "live_real" | "paper_monitor"; initialMonth: string })`
- Produces: `useCachedJson<T>(url: string | null)` returning `{ data, loading, error, refetch }`

- [ ] **Step 1: Write the failing monthly-calendar browser test**

Create `web/tests/live-redesign.spec.ts`. Intercept only the two new calendar endpoints, leaving existing read-only Dashboard endpoints untouched:

```typescript
import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.route("**/api/live-pnl-calendar?**", async (route) => {
    const url = new URL(route.request().url());
    const month = url.searchParams.get("month") ?? "2026-07";
    const days = Array.from({ length: month === "2026-06" ? 30 : 31 }, (_, index) => ({
      date: `${month}-${String(index + 1).padStart(2, "0")}`,
      pnl_usdc: index === 0 ? 5.1 : index === 1 ? -4.9 : 0,
      settled: index < 2 ? 1 : 0,
      wins: index === 0 ? 1 : 0,
      losses: index === 1 ? 1 : 0,
    }));
    await route.fulfill({
      json: {
        source: "live_real",
        month,
        day_tz: "Asia/Shanghai",
        start_date: days[0].date,
        end_date: days[days.length - 1].date,
        available_months: ["2026-06", "2026-07"],
        total_pnl_usdc: 0.2,
        settled: 2,
        wins: 1,
        losses: 1,
        win_rate: 0.5,
        empty: false,
        days,
      },
    });
  });
  await page.route("**/api/live-pnl-calendar/orders?**", async (route) => {
    await route.fulfill({
      json: {
        source: "live_real",
        date: "2026-07-01",
        day_tz: "Asia/Shanghai",
        total_pnl_usdc: 5.1,
        settled: 1,
        wins: 1,
        losses: 0,
        orders: [{
          order_id: "order-win",
          market_slug: "btc-updown-5m",
          direction: "UP",
          settle_ts: "2026-06-30T16:05:00Z",
          filled_size: 10,
          average_fill_price: 0.49,
          pnl_usdc: 5.1,
          won: true,
          status: "SETTLED",
          settlement_source: "official_chainlink",
        }],
      },
    });
  });
});

test("monthly pnl calendar changes month and opens reconciled day orders", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "月度收益日历" })).toBeVisible();
  await page.getByRole("button", { name: "2026年7月1日，盈利 5.10 USDC，1 笔结算" }).click();
  await expect(page.getByText("order-win")).toBeVisible();
  await expect(page.getByText("+5.10 USDC")).toBeVisible();
  await page.getByRole("button", { name: "上一个月" }).click();
  await expect(page.getByText("2026年6月")).toBeVisible();
});
```

- [ ] **Step 2: Run Playwright and verify RED**

With the existing dashboard available at port 8090:

```powershell
Set-Location web
npx playwright test tests/live-redesign.spec.ts --project=chromium
Set-Location ..
```

Expected: FAIL because the calendar heading is absent.

- [ ] **Step 3: Add DTOs and one-shot cache**

Define `MonthlyPnlDay`, `MonthlyPnlCalendar`, `DailyPnlOrder`, and `DailyPnlOrders` in `types.ts` using the exact API fields. Implement `useCachedJson` with a module-level `Map<string, unknown>` and `Map<string, Promise<unknown>>` so remounts and repeated date selections reuse data. Abort state updates on unmount; do not create intervals.

- [ ] **Step 4: Implement the accessible calendar**

Build a stable seven-column natural-month grid. Use `ChevronLeft`, `ChevronRight`, and `CalendarDays`; use icon buttons with `title` and `aria-label`. Use a native `input type="month"` for direct selection. Add leading blank cells based on the month's weekday. Day buttons must include full accessible text, use `aria-pressed` for selection, and show PnL plus settled count without truncation.

The detail view uses a fixed right drawer at `md` and above, and an in-flow section below the grid on mobile. It fetches only populated days. Show `--` and a local warning for request failures without hiding the calendar.

- [ ] **Step 5: Mount the calendar for both trading tabs**

Import the component in `Live.tsx`. Mount it below the seven-day Live Real strip with:

```tsx
<LiveMonthlyPnlCalendar
  source="live_real"
  initialMonth={(todayStats?.day ?? new Date().toISOString().slice(0, 10)).slice(0, 7)}
/>
```

Mount the same component in Paper Monitor with `source="paper_monitor"`. Keep the component outside every periodic `usePolling` call.

- [ ] **Step 6: Run Playwright and build**

Run:

```powershell
Set-Location web
npx playwright test tests/live-redesign.spec.ts --project=chromium
npm run build
Set-Location ..
```

Expected: PASS and TypeScript build succeeds.

- [ ] **Step 7: Commit new frontend files and the reviewed mount hunk**

```powershell
git add web/src/pages/live/types.ts web/src/pages/live/useCachedJson.ts web/src/pages/live/LiveMonthlyPnlCalendar.tsx web/tests/live-redesign.spec.ts
git diff -- web/src/pages/Live.tsx
git diff --cached --check
git commit -m "feat: add live monthly pnl calendar"
```

Before committing, selectively stage only the new calendar import and two mount hunks from `Live.tsx`, then confirm them in `git diff --cached`.

---

### Task 4: Trading Cockpit Summary And Current Action

**Files:**
- Create: `web/src/pages/live/LiveCockpitSummary.tsx`
- Create: `web/src/pages/live/LiveCurrentAction.tsx`
- Modify: `web/src/pages/Live.tsx:2459-2670`
- Modify: `web/tests/live-redesign.spec.ts`
- Modify: `tests/test_live_safety_readiness.py:3912-4007`

**Interfaces:**
- Produces: `CockpitMetric` with `id`, `label`, `value`, `detail`, `tone`, and Lucide icon.
- Produces: `LiveCockpitSummary({ metrics, mode, alert, updatedAt })`.
- Produces: `LiveCurrentAction({ decision, clobOrders, funnel })`.
- Consumes: existing `LiveSafety`, latest formal prediction, CLOB read-only orders, and order counters.

- [ ] **Step 1: Add failing cockpit acceptance assertions**

Extend `web/tests/live-redesign.spec.ts`:

```typescript
test("live defaults to a six-metric trading cockpit", async ({ page }) => {
  await page.goto("/");
  const cockpit = page.getByTestId("live-cockpit");
  await expect(cockpit).toBeVisible();
  await expect(cockpit.getByTestId("cockpit-metric")).toHaveCount(6);
  await expect(page.getByText("账户权益")).toBeVisible();
  await expect(page.getByText("今日已实现 PnL")).toBeVisible();
  await expect(page.getByText("当前风险余量")).toBeVisible();
  await expect(page.getByText("最新决策")).toBeVisible();
});
```

Replace brittle Python source assertions for eight `StatCard` instances with assertions that `Live.tsx` imports/mounts `LiveCockpitSummary` and builds exactly six metric models. Keep existing tests for `todayStats`, risk-control semantics, live/paper mode scoping, and configured day timezone.

- [ ] **Step 2: Verify RED**

```powershell
python -m pytest tests/test_live_safety_readiness.py -q
Set-Location web
npx playwright test tests/live-redesign.spec.ts --project=chromium
Set-Location ..
```

Expected: cockpit assertions FAIL.

- [ ] **Step 3: Implement compact non-truncating summary metrics**

Create a responsive summary with `grid-cols-2`, `lg:grid-cols-3`, and `2xl:grid-cols-6`. Values use `break-words`, `tabular-nums`, and responsive fixed sizes; do not use `truncate`. Combine total PnL and win rate into one result metric. Compute risk remaining as `max(0, max_daily_loss_usdc + daily_pnl_usdc)` and retain the configured limit in secondary text.

Use these six IDs exactly: `equity`, `today-pnl`, `settled-result`, `risk-budget`, `exposure`, and `latest-decision`.

- [ ] **Step 4: Implement the current-action strip**

Show the latest prediction action/side/window/settle time, current CLOB order count, and funnel values `submitted -> filled -> no-fill -> settled`. When there is no current order, render the compact neutral text `当前没有 CLOB 挂单` instead of an empty table.

- [ ] **Step 5: Replace the eight-card Live Real header**

In `Live.tsx`, keep data derivation but replace the eight visible `StatCard`s with the six-model `LiveCockpitSummary`, then mount `LiveCurrentAction`. Preserve the monthly-calendar mounts added in Task 3 for both Live Real and Paper Monitor.

- [ ] **Step 6: Verify focused tests and build**

```powershell
python -m pytest tests/test_live_safety_readiness.py -q
Set-Location web
npx playwright test tests/live-redesign.spec.ts --project=chromium
npm run build
Set-Location ..
```

Expected: PASS.

- [ ] **Step 7: Commit new files and only reviewed integration hunks**

Do not stage the entire dirty `tests/test_live_safety_readiness.py` or generated `api/static`. Inspect staged content before committing:

```powershell
git diff --cached --check
git commit -m "feat: simplify live trading cockpit"
```

---

### Task 5: Market, Equity, And Seven-Day Chart Redesign

**Files:**
- Create: `web/src/pages/live/LiveMarketSection.tsx`
- Modify: `web/src/components/BTCMarketChart.tsx:664-874`
- Modify: `web/src/pages/Live.tsx:980-1098,2644-2655`
- Modify: `web/tests/live-redesign.spec.ts`

**Interfaces:**
- Produces: `LiveMarketSection({ equity, weeklyCalendar })`.
- Keeps: `BTCMarketChart` internal 3-second current-market polling.

- [ ] **Step 1: Add failing chart-layout assertions**

Add a desktop test at 1440 x 900 that asserts the `BTC 5分钟市场` and `账户权益走势` headings both intersect the first viewport. Add assertions that the old `Start`, `Current`, and `Move` mini-card labels are absent from the equity section.

Add a 390 x 844 test asserting the BTC heading and current/target metrics are reachable at `boundingBox().y < 900` and that chart containers have stable non-zero heights.

- [ ] **Step 2: Verify RED**

```powershell
Set-Location web
npx playwright test tests/live-redesign.spec.ts --project=chromium
Set-Location ..
```

Expected: FAIL on first-viewport and obsolete mini-card assertions.

- [ ] **Step 3: Compact the BTC chart**

Translate primary labels, retain Chainlink/source terminology, change the plot to `h-[230px] md:h-[300px]`, and remove the 220 px history aside. Render recent windows as a horizontally scrollable result strip below the plot with stable buttons and contained overflow. Keep target/current/gap/countdown prominent. Remove decorative nested borders where the outer section already provides separation.

- [ ] **Step 4: Build the market section**

Move equity and weekly rendering from `Live.tsx` into `LiveMarketSection.tsx`. Equity header shows current equity, total PnL, settled count, and derived maximum drawdown. Because the current DTO has numeric points but no timestamps, label the X axis `结算序列` and do not imply clock time. Remove the three mini cards.

Replace seven day cards with a seven-column compact bar/heat strip. Each day includes date, signed PnL, and W/L in accessible text. Scale bars relative to the maximum absolute PnL while preserving a minimum visible zero state.

- [ ] **Step 5: Verify charts**

```powershell
Set-Location web
npx playwright test tests/live-redesign.spec.ts --project=chromium
npm run build
Set-Location ..
```

Expected: PASS with both desktop chart headings in the first viewport and stable mobile dimensions.

- [ ] **Step 6: Commit scoped chart changes**

```powershell
git add web/src/pages/live/LiveMarketSection.tsx web/src/components/BTCMarketChart.tsx
git diff --cached --check
git commit -m "feat: clarify live market charts"
```

Stage only the matching `Live.tsx` integration hunk after reviewing it against the pre-existing worktree diff.

---

### Task 6: Collapsed Operations Layer And Mobile Ledger

**Files:**
- Create: `web/src/pages/live/LiveOperationsDetails.tsx`
- Modify: `web/src/pages/Live.tsx:1101-2432,2652-2730`
- Modify: `web/tests/live-redesign.spec.ts`
- Modify: `tests/test_live_safety_readiness.py:3969-4067,4400-4440`

**Interfaces:**
- Produces four sections: `交易健康`, `账户与持仓`, `订单账本`, `详细诊断`.
- Accepts prebuilt section summaries and existing read-only payloads; critical blockers remain outside the closed sections.

- [ ] **Step 1: Add failing collapse and overflow tests**

Add browser checks that all four sections start closed, opening one does not open the others, and a warning count remains visible in the closed health header. At 390 x 844 assert:

```typescript
const overflow = await page.evaluate(() => ({
  scrollWidth: document.documentElement.scrollWidth,
  clientWidth: document.documentElement.clientWidth,
}));
expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.clientWidth);
```

Open `订单账本` and repeat the overflow assertion. Assert mobile records expose market, direction, PnL, and settlement source without an 11-column table.

- [ ] **Step 2: Verify RED**

```powershell
Set-Location web
npx playwright test tests/live-redesign.spec.ts --project=chromium
Set-Location ..
```

Expected: FAIL because the old ledger table expands the document and panels are separately visible.

- [ ] **Step 3: Merge repeated operational summaries**

Move the visible content of Live Formal Process, Risk Rules, Trading Status, account activity, order ledger, and diagnostics into the four grouped collapsibles. The health header must summarize runtime, order sync, risk state, and warning count. Remove repeated standalone runtime/risk/order summary panels from Layer 1.

Use the same `/api/live-intel?limit=80` URL as `StatusBar`; the existing shared `usePolling` cache will deduplicate that request. Remove the separate `limit=260` subscription. Keep live status at 5 seconds, signal stats at 10 seconds, and safety/account payload at 30 seconds.

- [ ] **Step 4: Replace the mobile ledger table**

Render stacked ledger records below `md`, each with a fixed label/value grid. Keep the desktop table at `md` and above inside `w-full min-w-0 max-w-full overflow-x-auto`. Do not use a wide table on mobile. Limit initial expanded records to 10-12 and retain the grouped-attempt tooltip/details.

- [ ] **Step 5: Update source-structure tests without weakening behavior**

Change Python source assertions to read `LiveOperationsDetails.tsx` for the moved panel labels and fields. Keep assertions for risk resilience, current CLOB orders, Polymarket account activity, hypothetical no-fill PnL, settlement source, and live/paper mode scoping.

- [ ] **Step 6: Run the full focused suite**

```powershell
python -m pytest tests/test_live_pnl_calendar.py tests/test_live_safety_readiness.py -q
Set-Location web
npx playwright test tests/live-redesign.spec.ts --project=chromium
npm run build
Set-Location ..
```

Expected: PASS and no page-level overflow.

- [ ] **Step 7: Commit scoped operations changes**

```powershell
git add web/src/pages/live/LiveOperationsDetails.tsx
git diff --cached --check
git diff --cached
git commit -m "feat: layer live operations details"
```

Stage modifications to previously dirty files only through reviewed task-specific hunks.

---

### Task 7: Integration, Visual Verification, And Runtime Handoff

**Files:**
- Verify: `api/server.py`
- Verify: `tests/test_live_pnl_calendar.py`
- Verify: `tests/test_live_safety_readiness.py`
- Verify: `web/src/pages/Live.tsx`
- Verify: `web/src/pages/live/*.tsx`
- Verify: `web/src/components/BTCMarketChart.tsx`
- Verify: `web/tests/live-redesign.spec.ts`
- Generated by build: `api/static/index.html`, `api/static/assets/*`

**Interfaces:**
- End-to-end Dashboard at `http://127.0.0.1:8090/`
- Read-only API at `/api/live-pnl-calendar` and `/api/live-pnl-calendar/orders`

- [ ] **Step 1: Run static and diff checks**

```powershell
git diff --check
git status --short
rg -n "KRONOS_ENABLE_REAL_ORDERS|submit|cancel|repost|mutate" web/src/pages/live web/src/pages/Live.tsx
```

Review every match and verify it is display copy or an existing read-only field, never an action call.

- [ ] **Step 2: Run backend tests**

```powershell
python -m pytest tests/test_live_pnl_calendar.py tests/test_live_safety_readiness.py -q
```

Expected: PASS.

- [ ] **Step 3: Build and run browser tests**

```powershell
Set-Location web
npm run build
npx playwright test tests/live-redesign.spec.ts --project=chromium
Set-Location ..
```

Expected: TypeScript/Vite build PASS and all Live redesign tests PASS.

- [ ] **Step 4: Verify API invariants against the local ledger**

Use only aggregate fields; do not print order details:

```powershell
$month = (Get-Date).ToString('yyyy-MM')
$calendar = Invoke-RestMethod -Uri "http://127.0.0.1:8090/api/live-pnl-calendar?source=live_real&month=$month" -TimeoutSec 30
$calendar | Select-Object month,day_tz,total_pnl_usdc,settled,wins,losses,win_rate
```

Select one populated date, request its detail endpoint, and compare only the summed `pnl_usdc` to the calendar day total. Do not print identifiers or raw order rows.

- [ ] **Step 5: Perform in-app browser visual QA**

Inspect at 1440 x 900 and 390 x 844:

- live mode, six metrics, critical warning, BTC chart, and equity heading are readable;
- monthly calendar changes month and opens one day's detail;
- four operations groups start collapsed and expand independently;
- no text overlap, clipped money, blank charts, or document-level horizontal overflow;
- mobile daily detail stays inside the viewport;
- page remains usable when calendar endpoints return 400/500 or an empty month.

- [ ] **Step 6: Re-check trading processes were untouched**

Read process state only. Confirm no trading runner, supervisor, order sync, candidate sidecar, or scoring loop was stopped or restarted as part of this Dashboard task. Confirm no order submit/cancel/mutation command was run.

- [ ] **Step 7: Review final staged scope and commit**

```powershell
git diff --cached --check
git diff --cached --stat
git diff --cached
```

Do not include pre-existing Compare work or unrelated generated assets. Commit the final integration only when the staged diff is scoped:

```powershell
git commit -m "feat: redesign live dashboard console"
```

- [ ] **Step 8: Report completion**

Report:

- branch and final commit;
- backend test count/result;
- frontend build and Playwright result;
- desktop/mobile visual QA result;
- current month API aggregate and reconciliation invariant result without order details;
- whether any order was submitted/cancelled/mutated: must be `false`;
- whether any trading configuration changed: must be `false`;
- whether generated `api/static` assets were committed or intentionally left dirty.
