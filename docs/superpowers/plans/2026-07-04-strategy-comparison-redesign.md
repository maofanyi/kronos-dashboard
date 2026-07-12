# Strategy Comparison Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 重构 Strategies 页面，让策略对比以中文决策视图、可比性提示和清晰图表为主。

**Architecture:** 保持现有 `/api/strategy-comparison` 数据接口不变，只在 `Compare.tsx` 内整理 view model 和布局。新增一个 Playwright 页面测试，覆盖中文主视图和单点趋势图隐藏规则。

**Tech Stack:** React 18、TypeScript、Tailwind、lucide-react、Playwright、现有 Python dashboard API。

## Global Constraints

- 不提交、取消、repost、mutate 任何 Polymarket 订单。
- 不运行带 `--submit` 的命令。
- 不设置或依赖 `KRONOS_ENABLE_REAL_ORDERS=YES`。
- 不修改 `G:\Kronos\data\config\trade_profiles\live_current_formal.json`。
- 页面文案以中文为主，保留必要英文缩写。
- 单点 `timeseries` 不显示趋势图。

---

### Task 1: Page-Level Regression Test

**Files:**
- Create: `web/playwright.config.ts`
- Create: `web/tests/strategies-redesign.spec.ts`

**Interfaces:**
- Consumes: running dashboard at `http://127.0.0.1:8090`
- Produces: `npx playwright test tests/strategies-redesign.spec.ts --project=chromium`

- [ ] **Step 1: Write the failing test**

```ts
import { expect, test } from "@playwright/test";

test("Strategies shows Chinese decision view and hides single-point trend", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /Strategies/ }).click();
  await expect(page.getByText("策略排名")).toBeVisible();
  await expect(page.getByText("时间窗口")).toBeVisible();
  await expect(page.getByText("样本可比性")).toBeVisible();
  await expect(page.getByText("趋势图数据点不足")).toBeVisible();
  await expect(page.getByText("Strategy Trends")).toHaveCount(0);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web; npx playwright test tests/strategies-redesign.spec.ts --project=chromium`

Expected: FAIL because the old page does not render `策略排名`.

### Task 2: Compare View Model Helpers

**Files:**
- Modify: `web/src/pages/Compare.tsx`

**Interfaces:**
- Consumes: `CandidateSummary[]`, `StrategyTimePoint[]`, selected candidate ids, metric, coverage.
- Produces: ranked rows, comparable status, bar chart values, trend availability.

- [ ] **Step 1: Implement helper functions near existing formatting helpers**

```ts
function comparisonValue(row: CandidateSummary, metric: StrategyFilters["metric"]) {
  if (metric === "pnl") return typeof row.pnl_usdc === "number" ? row.pnl_usdc : null;
  if (metric === "win_rate") return typeof row.win_rate === "number" ? row.win_rate * 100 : null;
  if (metric === "overlap") return row.same_side_overlap;
  return row.passed;
}
```

- [ ] **Step 2: Add derived rows in `Compare`**

Create `rankedCandidates`, `bestCandidate`, `sampleStatus`, and `hasUsefulTrend` from current API state.

### Task 3: Decision Layout and Chinese Copy

**Files:**
- Modify: `web/src/pages/Compare.tsx`

**Interfaces:**
- Consumes: helpers from Task 2.
- Produces: Chinese decision-first Strategies page.

- [ ] **Step 1: Replace top metric cards**

Render cards for `当前实盘策略`, `窗口最佳`, `最新信号`, and `样本可比性`.

- [ ] **Step 2: Replace `Strategy Trends` main section**

Use Chinese filters: `时间窗口`, `指标`, `聚合粒度`, `策略选择`. Default chart is horizontal bars.

- [ ] **Step 3: Make trend conditional**

If selected strategy points have fewer than 2 buckets, show `趋势图数据点不足`; otherwise render line charts.

- [ ] **Step 4: Simplify tables**

Rename main table to `策略排名`; keep Live-Matched and Recent Signals in collapsed sections with Chinese headings.

### Task 4: Verification

**Files:**
- Build output under `api/static`

**Interfaces:**
- Consumes: changed frontend.
- Produces: passing tests and updated static bundle.

- [ ] **Step 1: Run frontend build**

Run: `cd web; npm run build`

Expected: `tsc && vite build` succeeds.

- [ ] **Step 2: Run Playwright test**

Run: `cd web; npx playwright test tests/strategies-redesign.spec.ts --project=chromium`

Expected: PASS.

- [ ] **Step 3: Run API tests**

Run: `python -m pytest tests/test_strategy_comparison.py -q`

Expected: PASS.
