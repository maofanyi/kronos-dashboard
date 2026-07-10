import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  RefreshCw,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

type StrategyWindow = "today" | "24h" | "7d" | "14d" | "all";

export type LiveMatchedSegment = {
  settled: number;
  wins: number;
  losses: number;
  win_rate?: number | null;
  live_actual_pnl: number;
  live_normalized_pnl: number;
  scored_pnl: number;
};

export type LiveMatchedCandidate = {
  candidate_id: string;
  label: string;
  scored_available: boolean;
  overlap_count: number;
  live_only_count: number;
  scored_only_count: number;
  won_mismatches: number;
  side_mismatches?: number;
  pnl_delta?: number | null;
  all_live: LiveMatchedSegment;
  all_scored: LiveMatchedSegment;
  overlap: LiveMatchedSegment;
  live_only: LiveMatchedSegment;
  scored_only: LiveMatchedSegment;
};

export type LiveMatchedComparison = {
  available: boolean;
  ledger_path?: string;
  scored_summary_path?: string;
  live_reference_source?: string;
  size_shares: number;
  maker_price: number;
  live_order_count: number;
  live_settled_count: number;
  candidates: LiveMatchedCandidate[];
};

type MoneySummary = {
  market_count: number;
  live_pnl: number | null;
  simulated_pnl: number | null;
  pnl_delta: number | null;
};

type DifferenceSummary = MoneySummary & {
  difference_count: number;
  matched_count: number;
  by_type: Record<string, MoneySummary>;
};

type DifferenceSide = {
  present: boolean;
  side?: "LONG" | "SHORT" | null;
  attempts?: number;
  filled_size?: number | null;
  average_fill_price?: number | null;
  size?: number | null;
  price?: number | null;
  won?: boolean | null;
};

type DifferenceRow = {
  entry_ts: string;
  settle_ts: string;
  primary_type: string;
  difference_flags: string[];
  reason_label: string;
  live: DifferenceSide | null;
  simulated: DifferenceSide | null;
  live_pnl: number | null;
  simulated_pnl: number | null;
  pnl_delta: number | null;
};

type DifferenceResponse = {
  available: boolean;
  candidate_id: string;
  warnings: string[];
  data_quality: {
    reconcilable: boolean;
    excluded_live_reference_count: number;
    conflicting_scored_markets: Array<[string, string]>;
  };
  overall_summary: DifferenceSummary;
  filtered_summary: DifferenceSummary;
  rows: DifferenceRow[];
  pagination: {
    limit: number;
    offset: number;
    returned: number;
    total: number;
  };
};

type Props = {
  windowValue: StrategyWindow;
  liveStrategyId?: string;
  selectedCandidateIds: string[];
  comparison?: LiveMatchedComparison;
};

const PAGE_SIZE = 100;

const CAUSE_LABELS: Record<string, string> = {
  side_mismatch: "方向不一致",
  outcome_mismatch: "结果不一致",
  live_only: "仅实盘成交",
  simulated_pre_submit_blocked: "提交前风控拦截",
  simulated_no_fill: "实盘未成交",
  simulated_strategy_only: "仅模拟策略信号",
  simulated_only_unknown: "仅模拟（原因未知）",
  execution_mismatch: "执行参数不一致",
  matched: "完全匹配",
};

const DIFFERENCE_FLAG_LABELS: Record<string, string> = {
  side: "方向不同",
  outcome: "结果不同",
  size: "数量不同",
  price: "价格不同",
  attempts: "尝试次数不同",
  pnl: "PnL不同",
  missing_source: "缺少对应记录",
  no_fill: "实盘未成交",
  pre_submit_gate: "提交前风控拦截",
  strategy_signal: "仅模拟策略信号",
};

const REASON_LABELS: Record<string, string> = {
  "live and simulated sides differ": "实盘与模拟方向不同",
  "live and simulated outcomes differ": "实盘与模拟结果不同",
  "live and simulated execution fields differ": "实盘与模拟执行参数不同",
  "live and simulated records match": "实盘与模拟记录完全匹配",
  "no simulated trade for this market": "该市场没有对应模拟交易",
  "live order attempt did not fill": "实盘订单尝试未成交",
  "live settlement source is not comparable": "实盘结算数据源不可对比",
  "formal prediction record is missing": "缺少实盘预测记录",
  "pre-submit risk gate blocked order": "提交前风控拦截订单",
  "candidate strategy has an independent signal": "候选策略产生独立信号",
  "no comparable live fill reason is known": "缺少可对比的实盘成交原因",
};

const TYPE_OPTIONS = [
  "side_mismatch",
  "outcome_mismatch",
  "live_only",
  "simulated_pre_submit_blocked",
  "simulated_no_fill",
  "simulated_strategy_only",
  "simulated_only_unknown",
  "execution_mismatch",
  "matched",
] as const;

const windowLabel = (value: StrategyWindow) => {
  if (value === "today") return "今天";
  if (value === "24h") return "24小时";
  if (value === "7d") return "7天";
  if (value === "14d") return "14天";
  return "全部";
};

const causeLabel = (value: string) => CAUSE_LABELS[value] ?? value;

const reasonLabel = (row: DifferenceRow) => {
  const details = row.difference_flags
    .map((flag) => DIFFERENCE_FLAG_LABELS[flag] ?? flag)
    .filter(Boolean);
  if (details.length > 0) return details.join("、");
  return REASON_LABELS[row.reason_label] ?? row.reason_label ?? causeLabel(row.primary_type);
};

function Money({ value }: { value: number | null | undefined }) {
  if (value == null || Number.isNaN(value)) {
    return <span className="whitespace-nowrap text-amber-300">不可对账</span>;
  }
  const tone = value > 0 ? "text-emerald-300" : value < 0 ? "text-red-300" : "text-zinc-400";
  return (
    <span className={`whitespace-nowrap font-mono ${tone}`}>
      {value >= 0 ? "+" : "-"}${Math.abs(value).toFixed(2)}
    </span>
  );
}

function MarketTime({ entry, settle }: { entry: string; settle: string }) {
  const format = (value: string) => {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(date);
  };
  return (
    <div className="whitespace-nowrap font-mono text-xs">
      <div className="text-zinc-300">{format(entry)}</div>
      <div className="mt-0.5 text-zinc-600">至 {format(settle)}</div>
    </div>
  );
}

const sideLabel = (side?: DifferenceSide | null) => {
  if (!side?.present) return "无";
  if (side.side === "LONG") return "多";
  if (side.side === "SHORT") return "空";
  return "未知";
};

const executionLabel = (side?: DifferenceSide | null) => {
  if (!side?.present) return "-";
  const size = side.filled_size ?? side.size;
  const price = side.average_fill_price ?? side.price;
  if (size == null && price == null) return "-";
  return `${size == null ? "-" : size.toFixed(2)} @ ${price == null ? "-" : price.toFixed(4)}`;
};

const resultLabel = (side?: DifferenceSide | null) => {
  if (!side?.present || side.won == null) return "-";
  return side.won ? "赢" : "输";
};

export function StrategyDifferences({
  windowValue,
  liveStrategyId,
  selectedCandidateIds,
  comparison,
}: Props) {
  const candidates = useMemo(
    () =>
      (comparison?.candidates ?? []).filter((row) =>
        selectedCandidateIds.includes(row.candidate_id),
      ),
    [comparison?.candidates, selectedCandidateIds],
  );
  const preferredCandidate =
    candidates.find((row) => row.candidate_id === liveStrategyId)?.candidate_id ??
    candidates[0]?.candidate_id ??
    "";
  const selectedKey = selectedCandidateIds.join(",");
  const [expanded, setExpanded] = useState(false);
  const [candidateId, setCandidateId] = useState(preferredCandidate);
  const [typeFilter, setTypeFilter] = useState("all");
  const [includeMatched, setIncludeMatched] = useState(false);
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<DifferenceResponse | null>(null);
  const [dataQueryKey, setDataQueryKey] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestIdRef = useRef(0);

  useEffect(() => {
    setCandidateId(preferredCandidate);
    setOffset(0);
  }, [liveStrategyId, preferredCandidate, selectedKey]);

  useEffect(() => {
    setOffset(0);
  }, [candidateId, includeMatched, typeFilter, windowValue]);

  const queryString = useMemo(() => {
    const query = new URLSearchParams({
      candidate: candidateId,
      window: windowValue,
      include_matched: String(includeMatched),
      limit: String(PAGE_SIZE),
      offset: String(offset),
    });
    if (typeFilter !== "all") query.set("types", typeFilter);
    return query.toString();
  }, [candidateId, includeMatched, offset, typeFilter, windowValue]);

  const fetchDifferences = useCallback(async (signal?: AbortSignal) => {
    if (!expanded || !candidateId) return;
    const requestId = ++requestIdRef.current;
    const requestedQueryKey = queryString;
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(
        "/api/strategy-comparison/differences?" + requestedQueryKey,
        { signal },
      );
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const nextData = (await response.json()) as DifferenceResponse;
      if (signal?.aborted || requestId !== requestIdRef.current) return;
      setData(nextData);
      setDataQueryKey(requestedQueryKey);
      setError(null);
    } catch (requestError) {
      if (signal?.aborted || requestId !== requestIdRef.current) return;
      setError(requestError instanceof Error ? requestError.message : String(requestError));
    } finally {
      if (!signal?.aborted && requestId === requestIdRef.current) setLoading(false);
    }
  }, [candidateId, expanded, queryString]);

  useEffect(() => {
    if (!expanded || !candidateId) return;
    const controller = new AbortController();
    void fetchDifferences(controller.signal);
    return () => {
      controller.abort();
      requestIdRef.current += 1;
    };
  }, [candidateId, expanded, fetchDifferences]);

  const selectedSummary =
    candidates.find((row) => row.candidate_id === candidateId) ?? candidates[0];
  const total = data?.pagination.total ?? 0;
  const pageStart = total === 0 ? 0 : offset + 1;
  const pageEnd = Math.min(offset + PAGE_SIZE, total);
  const canPrevious = offset > 0;
  const canNext = offset + PAGE_SIZE < total;
  const dataIsStale = data != null && dataQueryKey !== queryString;

  return (
    <section className="border-y border-zinc-800 bg-zinc-950/35">
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-zinc-100">实盘与模拟差异</h2>
          <p className="mt-0.5 text-xs text-zinc-500">
            {windowLabel(windowValue)}窗口 / {selectedSummary?.label ?? "暂无候选策略"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {expanded && (
            <button
              type="button"
              title="刷新差异数据"
              aria-label="刷新差异数据"
              disabled={loading || !candidateId}
              onClick={() => void fetchDifferences()}
              className="inline-flex h-8 w-8 items-center justify-center border border-zinc-800 text-zinc-400 hover:text-zinc-100 disabled:opacity-50"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            </button>
          )}
          <button
            type="button"
            aria-expanded={expanded}
            onClick={() => setExpanded((value) => !value)}
            className="inline-flex items-center gap-1 border border-zinc-800 px-2 py-1.5 text-xs text-zinc-300 hover:text-zinc-100"
          >
            <ChevronDown className={`h-3.5 w-3.5 transition-transform ${expanded ? "rotate-180" : ""}`} />
            {expanded ? "收起" : "展开"}
          </button>
        </div>
      </div>

      {!expanded ? (
        <div className="flex flex-wrap gap-x-5 gap-y-2 border-t border-zinc-900 px-4 py-3 text-sm text-zinc-400">
          <span>重合 <strong className="font-mono font-medium text-zinc-200">{selectedSummary?.overlap_count ?? 0}</strong></span>
          <span>仅实盘 <strong className="font-mono font-medium text-zinc-200">{selectedSummary?.live_only_count ?? 0}</strong></span>
          <span>仅模拟 <strong className="font-mono font-medium text-zinc-200">{selectedSummary?.scored_only_count ?? 0}</strong></span>
          <span>方向不一致 <strong className="font-mono font-medium text-zinc-200">{selectedSummary?.side_mismatches ?? 0}</strong></span>
          <span>差额 <Money value={selectedSummary?.pnl_delta} /></span>
        </div>
      ) : (
        <div className="border-t border-zinc-900">
          <div className="overflow-x-auto">
            <table className="min-w-[760px] w-full divide-y divide-zinc-900 text-sm">
              <thead className="bg-black/20 text-xs text-zinc-500">
                <tr>
                  <th className="px-4 py-2 text-left font-medium">策略</th>
                  <th className="px-3 py-2 text-right font-medium">重合</th>
                  <th className="px-3 py-2 text-right font-medium">仅实盘</th>
                  <th className="px-3 py-2 text-right font-medium">仅模拟</th>
                  <th className="px-3 py-2 text-right font-medium">方向不一致</th>
                  <th className="px-4 py-2 text-right font-medium">PnL差额</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-900">
                {candidates.map((row) => (
                  <tr
                    key={row.candidate_id}
                    className={row.candidate_id === candidateId ? "bg-emerald-500/5" : "hover:bg-zinc-900/40"}
                  >
                    <td className="px-4 py-2 text-zinc-100">
                      <button
                        type="button"
                        aria-label={`选择 ${row.label}`}
                        aria-pressed={row.candidate_id === candidateId}
                        onClick={() => {
                          setOffset(0);
                          setCandidateId(row.candidate_id);
                        }}
                        className="block w-full text-left outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
                      >
                        <span className="flex items-center gap-2 font-medium">
                          {row.label}
                          {row.candidate_id === candidateId && (
                            <span className="text-xs font-normal text-emerald-300">当前选择</span>
                          )}
                        </span>
                        <span className="mt-0.5 block font-mono text-xs text-zinc-600">{row.candidate_id}</span>
                      </button>
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-zinc-300">{row.overlap_count}</td>
                    <td className="px-3 py-2 text-right font-mono text-zinc-300">{row.live_only_count}</td>
                    <td className="px-3 py-2 text-right font-mono text-zinc-300">{row.scored_only_count}</td>
                    <td className="px-3 py-2 text-right font-mono text-zinc-300">{row.side_mismatches ?? 0}</td>
                    <td className="px-4 py-2 text-right"><Money value={row.pnl_delta} /></td>
                  </tr>
                ))}
                {candidates.length === 0 && (
                  <tr><td colSpan={6} className="px-4 py-6 text-center text-zinc-500">暂无已选候选策略</td></tr>
                )}
              </tbody>
            </table>
          </div>

          <div className="flex flex-wrap items-end justify-between gap-3 border-t border-zinc-900 px-4 py-3">
            <div className="flex flex-wrap items-end gap-4">
              <label className="grid gap-1 text-xs text-zinc-500">
                差异类型
                <select
                  value={typeFilter}
                  onChange={(event) => {
                    setOffset(0);
                    setTypeFilter(event.target.value);
                  }}
                  className="h-8 border border-zinc-800 bg-zinc-950 px-2 text-sm text-zinc-200 outline-none focus:border-emerald-500"
                >
                  <option value="all">全部差异</option>
                  {TYPE_OPTIONS.map((type) => <option key={type} value={type}>{causeLabel(type)}</option>)}
                </select>
              </label>
              <label className="inline-flex h-8 items-center gap-2 text-sm text-zinc-300">
                <input
                  type="checkbox"
                  checked={includeMatched}
                  onChange={(event) => {
                    setOffset(0);
                    setIncludeMatched(event.target.checked);
                  }}
                  className="accent-emerald-400"
                />
                包含完全匹配
              </label>
            </div>
            <div className="flex items-center gap-2 text-xs text-zinc-500">
              <span>{pageStart}-{pageEnd} / {total}</span>
              <button
                type="button"
                title="上一页"
                aria-label="上一页"
                disabled={!canPrevious || loading}
                onClick={() => setOffset((value) => Math.max(0, value - PAGE_SIZE))}
                className="inline-flex h-8 w-8 items-center justify-center border border-zinc-800 hover:text-zinc-100 disabled:opacity-40"
              ><ChevronLeft className="h-4 w-4" /></button>
              <button
                type="button"
                title="下一页"
                aria-label="下一页"
                disabled={!canNext || loading}
                onClick={() => setOffset((value) => value + PAGE_SIZE)}
                className="inline-flex h-8 w-8 items-center justify-center border border-zinc-800 hover:text-zinc-100 disabled:opacity-40"
              ><ChevronRight className="h-4 w-4" /></button>
            </div>
          </div>

          {error && (
            <div className="border-t border-red-500/20 bg-red-500/5 px-4 py-3 text-sm text-red-300">
              刷新失败，保留上次成功数据：{error}
            </div>
          )}
          {dataIsStale && (
            <div className="border-t border-amber-500/20 bg-amber-500/5 px-4 py-3 text-sm text-amber-300">
              当前筛选已变化，以下为上次成功查询结果。
            </div>
          )}
          {data?.warnings.length ? (
            <div className="border-t border-amber-500/20 px-4 py-3 text-sm text-amber-300">
              数据源提示：{data.warnings.join("；")}
            </div>
          ) : null}

          {loading && !data ? (
            <div className="border-t border-zinc-900 px-4 py-8 text-center text-sm text-zinc-500">正在加载差异数据...</div>
          ) : data ? (
            <>
              <div className="grid border-t border-zinc-900 sm:grid-cols-2">
                <div className="px-4 py-3 sm:border-r sm:border-zinc-900">
                  <div className="text-xs text-zinc-500">全窗口差额</div>
                  <div className="mt-1 text-lg"><Money value={data.overall_summary.pnl_delta} /></div>
                  <div className="mt-1 text-xs text-zinc-600">{data.overall_summary.market_count} 个市场</div>
                </div>
                <div className="border-t border-zinc-900 px-4 py-3 sm:border-t-0">
                  <div className="text-xs text-zinc-500">筛选后差额</div>
                  <div className="mt-1 text-lg"><Money value={data.filtered_summary.pnl_delta} /></div>
                  <div className="mt-1 text-xs text-zinc-600">{data.filtered_summary.market_count} 个市场</div>
                </div>
              </div>

              <div className="flex flex-wrap gap-x-5 gap-y-2 border-t border-zinc-900 px-4 py-3 text-sm">
                {Object.entries(data.filtered_summary.by_type).map(([type, summary]) => (
                  <div key={type} className="flex items-center gap-2">
                    <span className="text-zinc-400">{causeLabel(type)} {summary.market_count}</span>
                    <Money value={summary.pnl_delta} />
                  </div>
                ))}
                {Object.keys(data.filtered_summary.by_type).length === 0 && <span className="text-zinc-500">当前筛选没有差异原因</span>}
              </div>

              {!data.data_quality.reconcilable && (
                <div className="border-t border-amber-500/20 bg-amber-500/5 px-4 py-3 text-sm text-amber-300">
                  当前数据不可完整对账，金额差额按“不可对账”显示。
                </div>
              )}

              <div className="overflow-x-auto border-t border-zinc-900">
                <table className="min-w-[1180px] w-full divide-y divide-zinc-900 text-sm">
                  <thead className="bg-black/20 text-xs text-zinc-500">
                    <tr>
                      <th className="px-4 py-2 text-left font-medium">北京时间</th>
                      <th className="px-3 py-2 text-left font-medium">差异类型</th>
                      <th className="px-3 py-2 text-left font-medium">实盘方向</th>
                      <th className="px-3 py-2 text-left font-medium">模拟方向</th>
                      <th className="px-3 py-2 text-right font-medium">实盘 数量@价格</th>
                      <th className="px-3 py-2 text-right font-medium">模拟 数量@价格</th>
                      <th className="px-3 py-2 text-center font-medium">结果</th>
                      <th className="px-3 py-2 text-right font-medium">实盘PnL</th>
                      <th className="px-3 py-2 text-right font-medium">模拟PnL</th>
                      <th className="px-3 py-2 text-right font-medium">差额</th>
                      <th className="px-4 py-2 text-left font-medium">原因</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-900">
                    {data.rows.map((row) => (
                      <tr key={`${row.entry_ts}-${row.settle_ts}`} className="hover:bg-zinc-900/40">
                        <td className="px-4 py-2"><MarketTime entry={row.entry_ts} settle={row.settle_ts} /></td>
                        <td className="px-3 py-2 text-zinc-200">{causeLabel(row.primary_type)}</td>
                        <td className="px-3 py-2 text-zinc-300">{sideLabel(row.live)}</td>
                        <td className="px-3 py-2 text-zinc-300">{sideLabel(row.simulated)}</td>
                        <td className="px-3 py-2 text-right font-mono text-xs text-zinc-300">{executionLabel(row.live)}</td>
                        <td className="px-3 py-2 text-right font-mono text-xs text-zinc-300">{executionLabel(row.simulated)}</td>
                        <td className="px-3 py-2 text-center text-zinc-300">{resultLabel(row.live)} / {resultLabel(row.simulated)}</td>
                        <td className="px-3 py-2 text-right"><Money value={row.live_pnl} /></td>
                        <td className="px-3 py-2 text-right"><Money value={row.simulated_pnl} /></td>
                        <td className="px-3 py-2 text-right"><Money value={row.pnl_delta} /></td>
                        <td className="px-4 py-2 text-zinc-400">{reasonLabel(row)}</td>
                      </tr>
                    ))}
                    {data.rows.length === 0 && (
                      <tr><td colSpan={11} className="px-4 py-8 text-center text-zinc-500">当前筛选没有差异记录</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </>
          ) : (
            <div className="border-t border-zinc-900 px-4 py-8 text-center text-sm text-zinc-500">
              {candidateId ? "展开后加载差异数据" : "请先选择候选策略"}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
