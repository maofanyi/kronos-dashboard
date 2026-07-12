# Strategy Comparison Redesign Design

## Goal

重构 Strategies 页面，让它首先服务于实盘策略决策，而不是堆叠难读的英文指标和无意义折线图。

## Requirements

- 第一屏展示当前窗口内的策略排名、实盘策略、最佳候选、采集新鲜度和样本可比性。
- 页面文案以中文为主，保留必要的英文缩写和指标名，例如 PnL、Win Rate、Round2、Official 14d/14d。
- 默认图表改为横向对比条形图，清楚对比 PnL、Win Rate、Passed、Overlap。
- 只有当每个选中策略有 2 个及以上时间桶时才展示趋势图；单点数据不得伪装成趋势。
- 30d 回填样本量明显高于其他策略时，必须显示“样本不完全可比”的提示。
- Recent Candidate Signals 与 Live-Matched 明细默认折叠，避免抢占主视图。
- 不改动任何交易配置、订单提交、取消或实盘下单逻辑。

## Layout

页面从上到下分为四块：

1. 决策摘要：当前实盘策略、窗口最佳策略、数据新鲜度、样本可比性。
2. 筛选栏：时间窗口、指标、聚合粒度和策略选择，使用中文标签。
3. 策略排名：主表格展示策略、角色、样本数、Passed、Win Rate、PnL、Overlap、状态。
4. 图表与明细：横向对比条形图置于表格之后；趋势图只在数据点足够时出现；Live-Matched 和最近信号折叠展示。

## Data Rules

- `window_candidates` 是主表和对比图的数据源。
- `timeseries` 仅用于趋势图。
- 样本可比性用已选策略的 `evaluated` 数量判断：最大值超过最小正数的 2 倍时显示混合/回填警告。
- PnL 和 Win Rate 只在 scored 数据存在且当前窗口不是 partial scored metric 时作为可排名指标展示。

## Verification

- Playwright 页面测试确认中文主视图出现，旧英文主标题不再作为主视图。
- `npm run build` 必须通过。
- 现有 Python dashboard API 测试必须通过。
