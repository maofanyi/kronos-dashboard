# Strategy Comparison Finer Buckets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Strategies 趋势图支持 5分钟、30分钟、1小时、4小时、6小时、1天粒度，避免今天窗口被日粒度压成单点。

**Architecture:** 后端继续由 `/api/strategy-comparison` 输出 `timeseries`，扩展 `bucket` 参数并按 dashboard 交易日时区对齐聚合。前端只改变 bucket 类型、默认值和显示标签，不改变交易数据来源。

**Tech Stack:** Flask/Python、React 18、TypeScript、Playwright。

## Global Constraints

- 不提交、取消、repost、mutate 任何 Polymarket 订单。
- 不运行带 `--submit` 的命令。
- 不设置或依赖 `KRONOS_ENABLE_REAL_ORDERS=YES`。
- 不修改交易机 `live_current_formal.json`。
- 默认粒度：今天用 1小时，7天/14天用 6小时，全部用 1天。

---

### Task 1: Backend Bucket Support

**Files:**
- Modify: `api/server.py`
- Test: `tests/test_strategy_comparison.py`

**Interfaces:**
- Consumes: `bucket` query parameter.
- Produces: `filters.bucket` in `5m | 30m | hour | 4h | 6h | day` and bucket keys aligned to dashboard timezone.

- [ ] **Step 1: Write failing tests**

Add tests asserting:

```python
assert server._strategy_compare_filters({"bucket": "5m"})["bucket"] == "5m"
assert server._strategy_compare_filters({"bucket": "1h"})["bucket"] == "hour"
assert server._strategy_compare_filters({"bucket": "1d"})["bucket"] == "day"
```

Add a timeseries test with candidate records at `00:05`, `01:55`, `04:05`, and `05:55` UTC where `bucket=4h` returns two bucket rows.

- [ ] **Step 2: Run tests and verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_strategy_comparison.py::test_strategy_compare_accepts_finer_buckets tests/test_strategy_comparison.py::test_strategy_timeseries_groups_four_hour_buckets -q`

Expected: FAIL because only `hour/day` are accepted.

- [ ] **Step 3: Implement bucket parsing and bucketing**

Update `_strategy_compare_filters` and `_strategy_bucket_key`.

- [ ] **Step 4: Verify backend**

Run: `.venv\Scripts\python.exe -m pytest tests/test_strategy_comparison.py -q`

Expected: PASS.

### Task 2: Frontend Controls and Defaults

**Files:**
- Modify: `web/src/pages/Compare.tsx`
- Modify: `web/tests/strategies-redesign.spec.ts`

**Interfaces:**
- Consumes: new API bucket values.
- Produces: Chinese bucket controls and window-aware defaults.

- [ ] **Step 1: Write failing Playwright assertion**

Assert Strategies shows `5分钟`, `4小时`, `6小时`, and `1天`, and no longer defaults to daily for today.

- [ ] **Step 2: Run Playwright test and verify failure**

Run: `cd web; npx playwright test tests/strategies-redesign.spec.ts --project=chromium`

Expected: FAIL before frontend change.

- [ ] **Step 3: Implement TypeScript bucket options**

Extend `StrategyFilters["bucket"]`; default to `hour`; add an effect that changes bucket to `6h` for `7d/14d` and `day` for `all` only when the current bucket is the previous auto-selected bucket.

- [ ] **Step 4: Verify frontend**

Run: `cd web; npm run build` and Playwright test.

Expected: PASS.
