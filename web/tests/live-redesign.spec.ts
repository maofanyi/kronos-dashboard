import { expect, test } from "@playwright/test";

function monthPayload(month: string) {
  const dayCount = month === "2026-06" ? 30 : 31;
  const days = Array.from({ length: dayCount }, (_, index) => ({
    date: `${month}-${String(index + 1).padStart(2, "0")}`,
    pnl_usdc: index === 0 ? 5.1 : index === 1 ? -4.9 : 0,
    settled: index < 2 ? 1 : 0,
    wins: index === 0 ? 1 : 0,
    losses: index === 1 ? 1 : 0,
  }));
  return {
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
  };
}

test("monthly pnl calendar changes month and opens reconciled day orders", async ({ page }) => {
  let orderRequests = 0;
  await page.route("**/api/live-pnl-calendar**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/orders")) {
      orderRequests += 1;
      await route.fulfill({
        json: {
          source: "live_real",
          date: url.searchParams.get("date"),
          day_tz: "Asia/Shanghai",
          total_pnl_usdc: 5.1,
          settled: 1,
          wins: 1,
          losses: 0,
          orders: [{
            order_id: "order-win",
            signal_id: "signal-win",
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
      return;
    }
    const month = url.searchParams.get("month") ?? "2026-07";
    await route.fulfill({ json: monthPayload(month) });
  });

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "月度收益日历" })).toBeVisible();

  await page.getByRole("button", { name: "2026年7月1日，盈利 5.10 USDC，1 笔结算" }).click();
  const orderCard = page.getByRole("article").filter({ hasText: "order-win" });
  await expect(orderCard).toBeVisible();
  await expect(orderCard.getByText("+5.10 USDC", { exact: true })).toBeVisible();
  expect(orderRequests).toBe(1);

  await page.getByRole("button", { name: "2026年7月3日，无结算订单" }).click();
  await expect(page.getByText("当日没有已结算订单")).toBeVisible();
  expect(orderRequests).toBe(1);

  await page.getByRole("button", { name: "上一个月" }).click();
  await expect(page.getByText("2026年6月", { exact: true })).toBeVisible();
});

test("live defaults to a six-metric trading cockpit", async ({ page }) => {
  await page.goto("/");

  const cockpit = page.getByTestId("live-cockpit");
  await expect(cockpit).toBeVisible();
  await expect(cockpit.getByTestId("cockpit-metric")).toHaveCount(6);
  await expect(cockpit.getByText("账户权益", { exact: true })).toBeVisible();
  await expect(cockpit.getByText("今日已实现 PnL", { exact: true })).toBeVisible();
  await expect(cockpit.getByText("结算结果", { exact: true })).toBeVisible();
  await expect(cockpit.getByText("当前风险余量", { exact: true })).toBeVisible();
  await expect(cockpit.getByText("当前敞口", { exact: true })).toBeVisible();
  await expect(cockpit.getByText("最新决策", { exact: true })).toBeVisible();

  const currentAction = page.getByTestId("live-current-action");
  await expect(currentAction).toBeVisible();
  await expect(currentAction.getByText("执行漏斗", { exact: true })).toBeVisible();
});
