import { expect, test } from "@playwright/test";

test("Strategies shows Chinese decision view and hides single-point trend", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /Strategies/ }).click();

  await expect(page.getByText("策略排名")).toBeVisible();
  await expect(page.getByText("时间窗口")).toBeVisible();
  await expect(page.getByText("样本可比性")).toBeVisible();
  await expect(page.getByRole("button", { name: "5分钟" })).toBeVisible();
  await expect(page.getByRole("button", { name: "24小时", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "4小时", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "6小时", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "1天", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "1天", exact: true }).click();
  await expect(page.getByText("趋势图数据点不足")).toBeVisible();
  await expect(page.getByText("Strategy Trends")).toHaveCount(0);
});
