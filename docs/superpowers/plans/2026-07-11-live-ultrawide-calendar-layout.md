# Live Ultrawide And Calendar Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Live charts stable and aligned on desktop and ultrawide displays, and constrain selected-day settlement orders to an independently scrolling panel.

**Architecture:** Keep the existing Live component boundaries and data flow. Apply stable grid and height constraints in `LiveMarketSection`, cap the Live console width in `Live`, and make the order list inside `LiveMonthlyPnlCalendar` the only vertically scrolling region. Verify layout behavior through Playwright viewport and computed-size assertions.

**Tech Stack:** React, TypeScript, Tailwind CSS, Vite, Playwright

## Global Constraints

- Do not change API requests, settlement calculations, or trading behavior.
- Use 230px plot height on compact screens and 300px from the medium breakpoint upward.
- Test a standard desktop viewport and a 3440x1440 ultrawide viewport.
- Preserve keyboard-accessible calendar day controls and selected-day close behavior.
- Keep existing dirty files and generated static assets out of focused commits.

---

### Task 1: Stable Desktop And Ultrawide Charts

**Files:**
- Modify: `web/src/pages/live/LiveMarketSection.tsx`
- Modify: `web/src/pages/Live.tsx`
- Test: `web/tests/live-redesign.spec.ts`

**Interfaces:**
- Consumes: `LiveMarketSection({ equity, weeklyCalendar })`
- Produces: `data-testid="live-market-chart-grid"` with equal-height chart cards and a centered bounded Live console

- [ ] **Step 1: Write failing layout assertions**

Add a Playwright test that sets a 3440x1440 viewport, loads `/`, reads the bounding boxes for `.chainlink-market-chart` and `[data-testid="live-equity-chart"]`, and asserts that their heights differ by no more than 1px. Assert that `[data-testid="live-main-console"]` is no wider than 1920px and the page has no horizontal overflow.

```ts
test("live charts stay aligned on an ultrawide viewport", async ({ page }) => {
  await page.setViewportSize({ width: 3440, height: 1440 });
  await page.goto("/");
  const market = page.locator(".chainlink-market-chart");
  const equity = page.getByTestId("live-equity-chart");
  const [marketBox, equityBox] = await Promise.all([market.boundingBox(), equity.boundingBox()]);
  expect(marketBox).not.toBeNull();
  expect(equityBox).not.toBeNull();
  expect(Math.abs(marketBox!.height - equityBox!.height)).toBeLessThanOrEqual(1);
  const consoleBox = await page.getByTestId("live-main-console").boundingBox();
  expect(consoleBox!.width).toBeLessThanOrEqual(1920);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
});
```

- [ ] **Step 2: Run the test and verify RED**

Run: `npx playwright test tests/live-redesign.spec.ts --project=chromium --grep "ultrawide viewport"`

Expected: FAIL because chart heights differ or the console lacks the test id and maximum width.

- [ ] **Step 3: Implement stable dimensions**

In `LiveMarketSection.tsx`, add `data-testid="live-market-chart-grid"`, use equal grid columns at `xl`, apply `items-stretch`, and make both chart card roots `h-full`. Keep both plots at `h-[230px] md:h-[300px]`. Use a three-row grid for the equity card so its plot does not absorb header-height differences.

In `Live.tsx`, add `data-testid="live-main-console"` and center the main console with `mx-auto w-full max-w-[1920px]`.

- [ ] **Step 4: Run focused test and build**

Run:

```powershell
npx playwright test tests/live-redesign.spec.ts --project=chromium --grep "ultrawide viewport"
npm run build
```

Expected: PASS and a successful Vite production build.

- [ ] **Step 5: Commit chart layout**

```powershell
git add web/src/pages/live/LiveMarketSection.tsx web/src/pages/Live.tsx web/tests/live-redesign.spec.ts
git commit -m "fix(dashboard): stabilize live charts on ultrawide screens"
```

### Task 2: Bounded Daily Settlement Order List

**Files:**
- Modify: `web/src/pages/live/LiveMonthlyPnlCalendar.tsx`
- Test: `web/tests/live-redesign.spec.ts`

**Interfaces:**
- Consumes: selected `MonthlyPnlDay` and `DailyPnlOrders`
- Produces: `data-testid="daily-settlement-orders"` as the independently scrollable list and `data-testid="monthly-pnl-detail"` as the bounded detail panel

- [ ] **Step 1: Write failing order-list assertions**

Extend the monthly calendar browser test to select a day with settled orders, then assert the detail panel and order list exist. Verify `overflow-y` is `auto`, the list client height is at most 420px on a standard desktop viewport, and `scrollHeight >= clientHeight`.

```ts
const detailPanel = page.getByTestId("monthly-pnl-detail");
const orderList = page.getByTestId("daily-settlement-orders");
await expect(detailPanel).toBeVisible();
await expect(orderList).toBeVisible();
const layout = await orderList.evaluate((node) => ({
  overflowY: getComputedStyle(node).overflowY,
  clientHeight: node.clientHeight,
  scrollHeight: node.scrollHeight,
}));
expect(layout.overflowY).toBe("auto");
expect(layout.clientHeight).toBeLessThanOrEqual(420);
expect(layout.scrollHeight).toBeGreaterThanOrEqual(layout.clientHeight);
```

- [ ] **Step 2: Run the test and verify RED**

Run: `npx playwright test tests/live-redesign.spec.ts --project=chromium --grep "monthly calendar"`

Expected: FAIL because the detail panel and order list do not expose bounded scrolling test targets.

- [ ] **Step 3: Implement the bounded flex layout**

Make the calendar/detail grid `items-stretch`. At `xl`, give the detail panel a bounded height derived from the calendar area and use `flex min-h-0 flex-col`. Keep the title and daily summary outside the scrolling region. Wrap only rendered order cards in:

```tsx
<div data-testid="daily-settlement-orders" className="min-h-0 max-h-[420px] space-y-3 overflow-y-auto overscroll-contain pr-1 xl:max-h-none xl:flex-1">
  {detail.orders.map(...)}
</div>
```

Add `data-testid="monthly-pnl-detail"` to the aside. Empty/loading/error states remain outside the scrolling list.

- [ ] **Step 4: Run focused tests and build**

Run:

```powershell
npx playwright test tests/live-redesign.spec.ts --project=chromium --grep "monthly calendar"
npm run build
```

Expected: PASS and a successful Vite production build.

- [ ] **Step 5: Commit calendar layout**

```powershell
git add web/src/pages/live/LiveMonthlyPnlCalendar.tsx web/tests/live-redesign.spec.ts
git commit -m "fix(dashboard): constrain daily settlement order details"
```

### Task 3: Cross-Viewport Visual Verification

**Files:**
- Modify: `web/tests/live-redesign.spec.ts` only if verification exposes a missing assertion

**Interfaces:**
- Consumes: built dashboard at `http://127.0.0.1:8090/`
- Produces: verified standard, ultrawide, and mobile layouts without overflow or overlap

- [ ] **Step 1: Run the complete focused Live suite**

Run: `npx playwright test tests/live-redesign.spec.ts --project=chromium`

Expected: all Live redesign tests pass.

- [ ] **Step 2: Inspect standard desktop and ultrawide screenshots**

Use the in-app browser at 1440x900 and 3440x1440. Confirm chart card boundaries align, neither chart is distorted, the Live console remains centered, and the monthly order list scrolls without increasing page height.

- [ ] **Step 3: Inspect mobile layout**

Use a 390x844 viewport. Confirm charts stack, calendar cells remain usable, the daily order list is capped at 420px, and the document has no horizontal overflow.

- [ ] **Step 4: Final verification**

Run:

```powershell
npm run build
npx playwright test tests/live-redesign.spec.ts --project=chromium
```

Expected: production build succeeds and all focused browser tests pass.

- [ ] **Step 5: Commit any verification-only assertion**

If Step 2 or Step 3 required a test-only correction:

```powershell
git add web/tests/live-redesign.spec.ts
git commit -m "test(dashboard): cover live responsive layout"
```

If no correction was required, do not create an empty commit.
