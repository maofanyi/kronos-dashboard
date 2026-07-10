# 策略对比实盘订单差异设计

日期：2026-07-10

## 目标

在 Strategies 页面中解释实盘订单与策略模拟订单为什么不同，并让每一笔差异都能追溯到明确的数据来源和 PnL 影响。

页面需要回答三个问题：

1. 哪些市场只有实盘订单或只有模拟订单？
2. 同一市场中，方向、成交份额、成交价格、胜负或 PnL 是否不同？
3. 每类差异分别造成了多少 PnL 差额，所有明细是否能与汇总完全对账？

## 当前证据

现有 /api/strategy-comparison 只返回按策略聚合的 overlap_count、live_only_count、scored_only_count 和 won_mismatches，页面无法看到具体订单及原因。

2026-07-10 的一次 today 快照中，Official 14d/14d 有 29 个已成交重合市场，模拟侧额外有 13 个市场。其中 3 个在实盘账本中记录为未成交，另外 10 个在正式预测历史中记录为 would_place_order=true、submitted=false、guarded_mode=blocked。该快照实盘 PnL 为 -15.1，模拟 PnL 为 4.2，总差额为 -19.3。

## 范围

本次修改只涉及 dashboard 的只读聚合、API 和 Strategies 页面展示。

包括：

- 保留现有策略汇总数据。
- 新增独立、按需加载的订单差异接口。
- 展示差异原因、实盘与模拟字段及 PnL 差额。
- 使用现有时间窗口和策略筛选口径。
- 支持只看差异或查看全部对齐市场。

不包括：

- 修改预测、风控、下单、撤单、repost 或结算流程。
- 修改实盘参数和 live_current_formal.json。
- 回写或修复交易账本、正式预测历史、候选评分文件。
- 主机侧 relabel、WFO 或参数优化。

## 数据来源

差异聚合只读取三类本地事实数据：

1. 实盘账本 live_real_orders_current_next.json：订单尝试、最终成交、成交份额、成交价格、状态、胜负和实际 PnL。
2. 正式预测历史 prediction_bound_live_formal_predictions.jsonl：策略动作、是否准备下单、是否实际提交、提交前门禁状态和原因。
3. 候选评分汇总 candidate_no_submit_official_truth_scored_summary.json：每个候选策略的模拟市场、方向、假设份额、假设价格、胜负和模拟 PnL。

任何来源缺失时均保留已有事实并返回 warning，不根据其他来源猜测缺失字段。

## 对齐模型

### 市场身份

同一个 5 分钟市场使用 entry_ts 和 settle_ts 作为身份，不把 side 放入身份键。

这样同一市场中 LONG 与 SHORT 的差异会被识别为方向不一致，而不是错误地拆成一笔仅实盘和一笔仅模拟。

### 实盘订单链

同一市场可能包含取消、未成交、repost 和最终成交等多条账本记录。API 先将其聚合为一个实盘订单链：

- 先按 order_id 或 order_key 去除同步产生的完全重复记录。
- attempts 为该市场的订单尝试数。
- final_status 使用最终有效状态。
- filled_size 为实际成交份额之和。
- average_fill_price 使用成交额加权平均价。
- live_pnl 为该市场所有最终成交记录的实际 PnL 之和。
- 保留是否曾未成交、取消或 repost，供原因说明使用。

### 模拟记录

每个候选策略在同一市场只保留一条评分记录。完全重复记录去重；如果同一策略同一市场存在互相冲突的评分记录，则标记 duplicate_scored_conflict 并返回 warning，不静默选择结果。

## 差异分类

每个市场返回一个 primary_type，并可附带多个 difference_flags。

primary_type 的优先级如下：

1. side_mismatch：实盘与模拟都存在，但方向不同。
2. outcome_mismatch：方向一致，但胜负结果不同。
3. live_only：存在最终实盘成交，但该策略没有模拟交易。
4. simulated_pre_submit_blocked：模拟交易存在，同一市场的正式预测也准备按相同方向下单，但提交前门禁阻止，且没有实际提交。
5. simulated_no_fill：模拟交易存在，实盘有订单尝试但没有成交。
6. simulated_strategy_only：模拟交易存在，正式预测历史也存在，但当时实盘策略没有准备按相同方向下单。
7. simulated_only_unknown：模拟交易存在，但账本和正式预测历史都无法解释实盘缺失。
8. execution_mismatch：双方均有同向、同结果交易，但份额、价格或 PnL 不同。
9. matched：双方关键字段一致。

difference_flags 可包含 side、outcome、size、price、pnl、attempts、pre_submit_gate、no_fill、strategy_signal 和 missing_source。

提交前阻止只根据该市场真实的正式预测记录判定，不因候选策略名称相同而推断。正式预测为 HOLD、方向不同或没有准备下单时，候选独有交易归为 simulated_strategy_only。

## PnL 口径

每个市场统一返回：

- live_pnl：实盘实际 PnL；没有实盘成交时为 0。
- simulated_pnl：候选评分 PnL；没有模拟交易时为 0。
- pnl_delta：live_pnl - simulated_pnl。

全窗口汇总和筛选后汇总都必须分别满足以下不变量：

- live_pnl 等于对应范围内市场的 live_pnl 之和。
- simulated_pnl 等于对应范围内市场的 simulated_pnl 之和。
- pnl_delta 等于 live_pnl - simulated_pnl。
- pnl_delta 等于对应范围内各 primary_type 的 PnL 差额之和。

前端不得将归一 PnL、模拟 PnL 或未成交订单的假设收益标成实盘收益。

## API 设计

保留现有 /api/strategy-comparison 作为轻量汇总接口。现有 live_matched 汇总改用相同的市场身份对齐，并只增加可由现有聚合直接计算的 pnl_delta；原因分类和订单明细不塞入该响应。

新增只读接口：

GET /api/strategy-comparison/differences

查询参数：

- candidate：单个候选策略 ID，必填。
- window：today、24h、7d、14d 或 all，沿用现有交易日口径。
- types：可选的 primary_type 逗号列表。
- include_matched：默认 false。
- limit：默认 100，最大 500。
- offset：默认 0。

响应包含：

- generated_at、candidate_id、filters 和 warnings。
- overall_summary：当前 candidate 和 window 内全部市场的订单数、差异数、各原因数量、实盘 PnL、模拟 PnL 和总差额，不受 types 与 include_matched 影响。
- filtered_summary：应用 types 与 include_matched 后的数量、原因和 PnL 汇总，与当前可分页 rows 的完整集合一致。
- rows：按 entry_ts 倒序排列的市场差异行。
- pagination：limit、offset、returned 和应用筛选后的 total。

每行包含市场时间、市场标识、primary_type、difference_flags、reason_label、实盘侧字段、模拟侧字段和三个 PnL 字段。订单 ID 只提供缩短后的显示值，不在列表响应中复制完整订单对象。

接口使用与现有策略比较相同的短时查询缓存。不同 candidate、window、types、include_matched、limit 和 offset 使用不同缓存键。

## 页面设计

现有“实盘匹配明细”面板改为“实盘与模拟差异”。

折叠状态继续展示策略级汇总，包括重合、仅实盘、仅模拟、差异数和 PnL 差额。

展开后：

- 默认选择当前实盘策略；当前实盘策略不可识别时选择第一个已选策略。
- 顶部同时标明全窗口总差额和当前筛选差额，并展示提交前阻止、未成交、候选独有、仅实盘、方向/结果、执行价格与份额等原因贡献。
- 支持策略选择、差异类型筛选和“包含完全匹配”开关。
- 明细表展示北京时间、差异类型、实盘/模拟方向、实盘与模拟份额和价格、胜负、实盘 PnL、模拟 PnL、差额及原因。
- 默认只加载差异行，按最新市场优先。
- 面板展开或筛选变化时请求明细；页面不轮询。保留手动刷新按钮。
- 行数超过 limit 时显示分页或“加载更多”，不一次返回全部历史。

差异类型使用中文标签和稳定颜色，不用颜色作为唯一信息载体。金额正负同时使用符号和颜色。

## 错误与退化

- 评分汇总缺失：显示“模拟评分缺失”，不返回伪造差异。
- 实盘账本缺失：显示“实盘账本缺失”，模拟记录可见但原因标为 missing_source。
- 正式预测历史缺失：无法解释的模拟独有订单标记 simulated_only_unknown。
- 结算来源不符合当前官方 Data Streams 口径：该实盘记录不参与正式 PnL 对账，并在 warning 中给出排除数量。
- API 请求失败：面板保留上一次成功结果并显示错误，不影响 Strategies 页面其他区域。

## 测试与验收

后端测试至少覆盖：

- 同一市场方向不同会得到 side_mismatch。
- 取消或 NO_FILL 的订单链会得到 simulated_no_fill。
- would_place_order=true、submitted=false、guarded_mode=blocked 会得到 simulated_pre_submit_blocked。
- 正式预测为 HOLD、方向不同或没有准备按候选方向下单时会得到 simulated_strategy_only。
- 仅实盘、结果不同、价格/份额不同和完全匹配。
- 重复订单尝试会聚合为一个市场，不重复计算 PnL。
- 每个时间窗口与 dashboard 交易日边界一致。
- 明细 PnL、原因分组和总汇总满足对账不变量。
- 文件缺失、冲突评分、分页和筛选返回稳定 warning。

前端验收包括：

- 默认显示当前实盘策略的差异。
- 切换策略、窗口和差异类型会更新数据。
- “包含完全匹配”默认关闭且可正常切换。
- PnL 差额能从汇总追到具体市场行。
- 小屏幕不发生文字重叠，宽表可横向滚动。
- npm build、相关 pytest 和浏览器桌面/移动端检查通过。

## 安全约束

所有新增代码均为只读。实现、测试和浏览器验证不得提交、取消、repost 或修改任何 Polymarket 订单，不启动带 --submit 的命令，也不读取或打印密钥。
