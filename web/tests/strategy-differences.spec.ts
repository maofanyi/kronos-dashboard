import { expect, test, type Page } from "@playwright/test";

const LIVE_ID = "official_truth_14d14d_latest";
const OTHER_ID = "official_truth_7d7d_latest";

const segment = {
  settled: 1,
  wins: 1,
  losses: 0,
  win_rate: 1,
  live_actual_pnl: 5.1,
  live_normalized_pnl: 5.1,
  scored_pnl: 5.1,
};

const matchedCandidate = (candidate_id: string, label: string) => ({
  candidate_id,
  label,
  scored_available: true,
  overlap_count: 1,
  live_only_count: 0,
  scored_only_count: 0,
  side_mismatches: 0,
  won_mismatches: 0,
  pnl_delta: 0,
  all_live: segment,
  all_scored: segment,
  overlap: segment,
  live_only: { ...segment, settled: 0 },
  scored_only: { ...segment, settled: 0 },
});

const candidateSummary = (candidate_id: string, label: string) => ({
  candidate_id,
  label,
  config_path: `${candidate_id}.json`,
  evaluated: 1,
  passed: 1,
  hold: 0,
  pass_rate: 1,
  long: 1,
  short: 0,
  candidate_only: 0,
  same_side_overlap: 1,
  live_signal_filtered: 0,
  minimum_ready: false,
  preferred_ready: false,
  minimum_passed_signal_target: 200,
  preferred_passed_signal_target: 500,
  scoring_status: "scored",
  wins: 1,
  losses: 0,
  win_rate: 1,
  pnl_usdc: 5.1,
});

const candidates = [
  candidateSummary(LIVE_ID, "Official 14d/14d"),
  candidateSummary(OTHER_ID, "Official 7d/7d"),
];

const summaryResponse = {
  ok: true,
  warnings: [],
  generated_at: "2026-07-10T12:00:00Z",
  collection: {
    signals_file_exists: true,
    latest_file_exists: true,
    daily_summary_exists: true,
    scored_summary_exists: true,
    collection_days: 1,
    minimum_days_target: 7,
    preferred_days_target: 14,
    minimum_passed_signal_target: 200,
    preferred_passed_signal_target: 500,
    minimum_ready: false,
    preferred_ready: false,
  },
  live: {
    strategy_id: LIVE_ID,
    label: "Official 14d/14d",
    prediction_rows: 1,
    would_place: 1,
    submitted: 1,
    pass_rate: 1,
    order_sync_ok: true,
    order_sync_fresh: true,
    notes: [],
  },
  candidates,
  window_candidates: candidates,
  window_coverage: {
    window: "today",
    requested_start_at: "2026-07-10T00:00:00Z",
    covered_days: 1,
    partial: false,
    score_status: "complete",
    score_label: "完整评分",
    day_tz: "Asia/Shanghai",
    time_basis: "live_trading_day",
  },
  live_matched: {
    available: true,
    size_shares: 10,
    maker_price: 0.49,
    live_order_count: 1,
    live_settled_count: 1,
    candidates: [
      matchedCandidate(LIVE_ID, "Official 14d/14d"),
      matchedCandidate(OTHER_ID, "Official 7d/7d"),
    ],
  },
  filters: {
    window: "today",
    bucket: "hour",
    metric: "pnl",
    candidates: [LIVE_ID, OTHER_ID],
    available_metrics: ["signals", "pnl", "win_rate", "overlap"],
    available_buckets: ["hour", "day"],
  },
  timeseries: [],
  recent_signals: [],
};

const detailResponse = (
  candidate_id: string,
  marker: string,
  difference_flags: string[] = [],
  reason_label = marker,
) => ({
  available: true,
  candidate_id,
  warnings: [],
  data_quality: {
    reconcilable: true,
    excluded_live_reference_count: 0,
    conflicting_scored_markets: [],
  },
  overall_summary: {
    market_count: 1,
    live_pnl: 5.1,
    simulated_pnl: 4.9,
    pnl_delta: 0.2,
    difference_count: 1,
    matched_count: 0,
    by_type: {
      execution_mismatch: {
        market_count: 1,
        live_pnl: 5.1,
        simulated_pnl: 4.9,
        pnl_delta: 0.2,
      },
    },
  },
  filtered_summary: {
    market_count: 1,
    live_pnl: 5.1,
    simulated_pnl: 4.9,
    pnl_delta: 0.2,
    difference_count: 1,
    matched_count: 0,
    by_type: {
      execution_mismatch: {
        market_count: 1,
        live_pnl: 5.1,
        simulated_pnl: 4.9,
        pnl_delta: 0.2,
      },
    },
  },
  rows: [
    {
      entry_ts: "2026-07-10T10:00:00Z",
      settle_ts: "2026-07-10T10:05:00Z",
      primary_type: "execution_mismatch",
      difference_flags,
      reason_label,
      live: {
        present: true,
        side: "LONG",
        attempts: 2,
        filled_size: 10,
        average_fill_price: 0.51,
        won: true,
      },
      simulated: {
        present: true,
        side: "LONG",
        attempts: 1,
        size: 10,
        price: 0.49,
        won: true,
      },
      live_pnl: 5.1,
      simulated_pnl: 4.9,
      pnl_delta: 0.2,
    },
  ],
  pagination: { limit: 100, offset: 0, returned: 1, total: 1 },
});

async function mockSummary(page: Page) {
  await page.route("**/api/**", async (route) => {
    await route.fulfill({ status: 503, json: { error: "not mocked" } });
  });
  await page.route("**/api/strategy-comparison?*", async (route) => {
    await route.fulfill({ status: 200, json: summaryResponse });
  });
}

async function openDifferences(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "Strategies", exact: true }).click();
  const panel = page.locator("section").filter({
    has: page.getByRole("heading", { name: "实盘与模拟差异", exact: true }),
  });
  await expect(panel).toBeVisible();
  return panel;
}

test.beforeEach(async ({ page }) => {
  await mockSummary(page);
});

test("detail data is requested only after expansion", async ({ page }) => {
  let detailRequests = 0;
  await page.route("**/api/strategy-comparison/differences?*", async (route) => {
    detailRequests += 1;
    const candidate = new URL(route.request().url()).searchParams.get("candidate")!;
    await route.fulfill({ status: 200, json: detailResponse(candidate, "展开后数据") });
  });

  const panel = await openDifferences(page);
  expect(detailRequests).toBe(0);

  await panel.getByRole("button", { name: "展开", exact: true }).click();
  await expect.poll(() => detailRequests).toBe(1);
  await expect(panel.getByText("展开后数据", { exact: true })).toBeVisible();
});

test("delayed manual refresh cannot overwrite a newer strategy response", async ({ page }) => {
  let liveRequests = 0;
  let releaseManual!: () => void;
  let finishManual!: () => void;
  const manualGate = new Promise<void>((resolve) => { releaseManual = resolve; });
  const manualFinished = new Promise<void>((resolve) => { finishManual = resolve; });

  await page.route("**/api/strategy-comparison/differences?*", async (route) => {
    const candidate = new URL(route.request().url()).searchParams.get("candidate")!;
    if (candidate === LIVE_ID) {
      liveRequests += 1;
      if (liveRequests === 2) {
        await manualGate;
        await route.fulfill({ status: 200, json: detailResponse(candidate, "延迟刷新A") });
        finishManual();
        return;
      }
      await route.fulfill({ status: 200, json: detailResponse(candidate, "初始A") });
      return;
    }
    await route.fulfill({ status: 200, json: detailResponse(candidate, "新版B") });
  });

  const panel = await openDifferences(page);
  await panel.getByRole("button", { name: "展开", exact: true }).click();
  await expect(panel.getByText("初始A", { exact: true })).toBeVisible();

  await panel.getByRole("button", { name: "刷新差异数据", exact: true }).click();
  await panel.getByRole("button", { name: "选择 Official 7d/7d", exact: true }).click();
  await expect(panel.getByText("新版B", { exact: true })).toBeVisible();

  releaseManual();
  await manualFinished;
  await expect(panel.getByText("新版B", { exact: true })).toBeVisible();
  await expect(panel.getByText("延迟刷新A", { exact: true })).toHaveCount(0);
});

test("failed new query labels retained rows as previous results", async ({ page }) => {
  await page.route("**/api/strategy-comparison/differences?*", async (route) => {
    const candidate = new URL(route.request().url()).searchParams.get("candidate")!;
    if (candidate === OTHER_ID) {
      await route.fulfill({ status: 503, json: { error: "unavailable" } });
      return;
    }
    await route.fulfill({ status: 200, json: detailResponse(candidate, "A保留行") });
  });

  const panel = await openDifferences(page);
  await panel.getByRole("button", { name: "展开", exact: true }).click();
  await expect(panel.getByText("A保留行", { exact: true })).toBeVisible();

  await panel.getByRole("button", { name: "选择 Official 7d/7d", exact: true }).click();
  await expect(panel.getByText("当前筛选已变化，以下为上次成功查询结果。", { exact: true })).toBeVisible();
  await expect(panel.getByText("刷新失败，保留上次成功数据：HTTP 503", { exact: true })).toBeVisible();
  await expect(panel.getByText("A保留行", { exact: true })).toBeVisible();
});

test("reason flags are Chinese details and strategy selection is keyboard accessible", async ({ page }) => {
  await page.route("**/api/strategy-comparison/differences?*", async (route) => {
    const candidate = new URL(route.request().url()).searchParams.get("candidate")!;
    await route.fulfill({
      status: 200,
      json: detailResponse(candidate, `数据-${candidate}`, ["size", "price", "attempts", "pnl"]),
    });
  });

  const panel = await openDifferences(page);
  await panel.getByRole("button", { name: "展开", exact: true }).click();
  await expect(panel.getByRole("cell", { name: "数量不同、价格不同、尝试次数不同、PnL不同", exact: true })).toBeVisible();

  const otherStrategy = panel.getByRole("button", { name: "选择 Official 7d/7d", exact: true });
  await otherStrategy.focus();
  await otherStrategy.press("Enter");
  await expect(otherStrategy).toHaveAttribute("aria-pressed", "true");
  await expect(otherStrategy).toContainText("当前选择");
});
