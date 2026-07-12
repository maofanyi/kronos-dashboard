import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  Clock3,
  GitCompareArrows,
  RadioTower,
  RefreshCw,
  Target,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Chart } from "@/components/chart";
import {
  StrategyDifferences,
  type LiveMatchedComparison,
} from "../components/StrategyDifferences";

type CollectionSummary = {
  signals_file_exists: boolean;
  latest_file_exists: boolean;
  daily_summary_exists: boolean;
  scored_summary_exists: boolean;
  first_signal_at?: string | null;
  latest_signal_at?: string | null;
  latest_signal_age_seconds?: number | null;
  collection_days: number;
  minimum_days_target: number;
  preferred_days_target: number;
  minimum_passed_signal_target: number;
  preferred_passed_signal_target: number;
  minimum_ready: boolean;
  preferred_ready: boolean;
};

type LiveSummary = {
  strategy_id: string;
  label: string;
  prediction_rows: number;
  would_place: number;
  submitted: number;
  pass_rate: number;
  latest_prediction_at?: string | null;
  latest_prediction_age_seconds?: number | null;
  order_sync_ok: boolean;
  order_sync_fresh: boolean;
  order_sync_age_seconds?: number | null;
  notes: string[];
};

type CandidateSummary = {
  candidate_id: string;
  label: string;
  config_path: string;
  evaluated: number;
  passed: number;
  hold: number;
  pass_rate: number;
  long: number;
  short: number;
  candidate_only: number;
  same_side_overlap: number;
  live_signal_filtered: number;
  latest_signal_at?: string | null;
  latest_signal_age_seconds?: number | null;
  minimum_ready: boolean;
  preferred_ready: boolean;
  minimum_passed_signal_target: number;
  preferred_passed_signal_target: number;
  scoring_status: "scored" | "pending_official_scoring";
  scored?: Record<string, unknown> | null;
  wins?: number | null;
  losses?: number | null;
  win_rate?: number | null;
  pnl_usdc?: number | null;
};

type StrategyFilters = {
  window: "today" | "24h" | "7d" | "14d" | "all";
  bucket: "5m" | "30m" | "hour" | "4h" | "6h" | "day";
  metric: "signals" | "pnl" | "win_rate" | "overlap";
  candidates: string[];
  available_metrics: string[];
  available_buckets?: string[];
};

type StrategyTimePoint = {
  bucket: string;
  candidate_id: string;
  evaluated: number;
  passed: number;
  pass_rate: number;
  candidate_only: number;
  same_side_overlap: number;
  live_signal_filtered: number;
  wins?: number | null;
  losses?: number | null;
  win_rate?: number | null;
  pnl_usdc?: number | null;
  scored: boolean;
};

type WindowCoverage = {
  window: StrategyFilters["window"];
  requested_days?: number | null;
  requested_start_at?: string | null;
  data_start_at?: string | null;
  effective_start_at?: string | null;
  covered_days: number;
  partial: boolean;
  score_status?: "partial" | "complete";
  score_label?: string | null;
  day_tz?: string | null;
  time_basis?: string | null;
};

type RecentSignal = {
  created_at?: string | null;
  entry_ts?: string | null;
  candidate_id?: string | null;
  submitted: boolean;
  no_submit: boolean;
  passed: boolean;
  side?: string | null;
  action?: string | null;
  same_side_overlap: boolean;
  candidate_only: boolean;
  live_signal_filtered: boolean;
  p5_up?: number | null;
  p1_up?: number | null;
  p4_up?: number | null;
  reason_code?: string | null;
};

type StrategyComparisonData = {
  generated_at?: string;
  ok: boolean;
  warnings: string[];
  collection: CollectionSummary;
  live: LiveSummary;
  candidates: CandidateSummary[];
  window_candidates?: CandidateSummary[];
  window_coverage?: WindowCoverage;
  live_matched?: LiveMatchedComparison;
  filters: StrategyFilters;
  timeseries: StrategyTimePoint[];
  recent_signals: RecentSignal[];
};

const panel = "rounded-md border border-zinc-800 bg-zinc-950/70";
const colors = ["var(--chart-1)", "var(--chart-2)", "var(--chart-4)", "#34d399"];

const signedMoneyOrPending = (value: unknown) =>
  typeof value === "number" ? `${value >= 0 ? "+" : "-"}$${Math.abs(value).toFixed(2)}` : "待评分";

const percent = (value?: number | null) =>
  value == null || Number.isNaN(value) ? "-" : `${(value * 100).toFixed(1)}%`;

const age = (seconds?: number | null) =>
  seconds == null
    ? "-"
    : seconds < 60
    ? `${Math.round(seconds)}秒`
    : seconds < 3600
    ? `${Math.round(seconds / 60)}分钟`
    : `${(seconds / 3600).toFixed(1)}小时`;

function formatDateTime(value?: string | null) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function formatBucket(value: string, bucket: StrategyFilters["bucket"]) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  if (bucket === "day") {
    return date.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
  }
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function windowName(value: StrategyFilters["window"]) {
  if (value === "today") return "今天";
  if (value === "24h") return "24小时";
  if (value === "7d") return "7天";
  if (value === "14d") return "14天";
  return "全部";
}

function bucketName(value: StrategyFilters["bucket"]) {
  if (value === "5m") return "5分钟";
  if (value === "30m") return "30分钟";
  if (value === "hour") return "1小时";
  if (value === "4h") return "4小时";
  if (value === "6h") return "6小时";
  return "1天";
}

function metricLabel(metric: StrategyFilters["metric"]) {
  if (metric === "pnl") return "PnL";
  if (metric === "win_rate") return "Win Rate";
  if (metric === "overlap") return "同向重合";
  return "通过信号";
}

function scoreLabel(coverage?: WindowCoverage) {
  if (coverage?.score_label) return coverage.score_label;
  if (coverage?.partial) return `部分评分 ${coverage.covered_days.toFixed(2)}天`;
  return "完整评分";
}

function metricPending(metric: StrategyFilters["metric"], collection?: CollectionSummary) {
  return (metric === "pnl" || metric === "win_rate") && !collection?.scored_summary_exists;
}

function metricIsScored(metric: StrategyFilters["metric"]) {
  return metric === "pnl" || metric === "win_rate";
}

function defaultBucketForWindow(value: StrategyFilters["window"]): StrategyFilters["bucket"] {
  if (value === "today" || value === "24h") return "hour";
  if (value === "7d" || value === "14d") return "6h";
  return "day";
}

function comparisonValue(row: CandidateSummary, metric: StrategyFilters["metric"]) {
  if (metric === "pnl") return typeof row.pnl_usdc === "number" ? row.pnl_usdc : null;
  if (metric === "win_rate") return typeof row.win_rate === "number" ? row.win_rate * 100 : null;
  if (metric === "overlap") return row.same_side_overlap;
  return row.passed;
}

function metricValueLabel(value: number, metric: StrategyFilters["metric"], pending: boolean, pendingLabel = "待评分") {
  if (pending) return pendingLabel;
  if (metric === "pnl") return `${value >= 0 ? "+" : "-"}$${Math.abs(value).toFixed(2)}`;
  if (metric === "win_rate") return `${value.toFixed(1)}%`;
  return value.toFixed(0);
}

function seriesValue(point: StrategyTimePoint, metric: StrategyFilters["metric"]) {
  if (metric === "pnl") return typeof point.pnl_usdc === "number" ? point.pnl_usdc : 0;
  if (metric === "win_rate") return typeof point.win_rate === "number" ? point.win_rate * 100 : 0;
  if (metric === "overlap") return point.same_side_overlap;
  return point.passed;
}

function sampleComparable(rows: CandidateSummary[]) {
  const positive = rows.map((row) => row.evaluated).filter((value) => value > 0);
  if (positive.length < 2) {
    return { ok: true, label: "样本较少", detail: "至少选择两个策略后再判断可比性" };
  }
  const min = Math.min(...positive);
  const max = Math.max(...positive);
  if (max > min * 2) {
    return {
      ok: false,
      label: "样本不完全可比",
      detail: `最大样本 ${max} / 最小样本 ${min}，可能混入回填数据`,
    };
  }
  return { ok: true, label: "样本可比", detail: `样本范围 ${min} - ${max}` };
}

function roleLabel(row: CandidateSummary, liveStrategyId?: string | null) {
  if (row.candidate_id === liveStrategyId) return "实盘";
  if (row.candidate_id.includes("round2")) return "候选 / Round2";
  return "候选";
}

function ProgressBar({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(100, value * 100));
  return (
    <div className="h-2 rounded bg-zinc-900">
      <div className="h-2 rounded bg-emerald-400" style={{ width: `${pct}%` }} />
    </div>
  );
}

function StatusPill({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded border px-2 py-1 text-xs ${
        ok
          ? "border-emerald-500/25 bg-emerald-500/10 text-emerald-300"
          : "border-amber-500/25 bg-amber-500/10 text-amber-300"
      }`}
    >
      {ok ? <CheckCircle2 className="h-3 w-3" /> : <AlertTriangle className="h-3 w-3" />}
      {label}
    </span>
  );
}

function DecisionCard({
  title,
  value,
  detail,
  icon,
  ok = true,
}: {
  title: string;
  value: ReactNode;
  detail: ReactNode;
  icon: ReactNode;
  ok?: boolean;
}) {
  return (
    <div className={panel + " p-4"}>
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs text-zinc-500">{title}</span>
        <span className={ok ? "text-zinc-500" : "text-amber-300"}>{icon}</span>
      </div>
      <div className="mt-3 min-h-8 text-lg font-semibold leading-tight text-zinc-100">{value}</div>
      <div className="mt-1 text-xs leading-5 text-zinc-500">{detail}</div>
    </div>
  );
}

function Panel({
  title,
  sub,
  right,
  children,
}: {
  title: string;
  sub?: string;
  right?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className={panel}>
      <div className="flex items-center justify-between gap-3 border-b border-zinc-800 px-4 py-3">
        <div className="min-w-0">
          <h2 className="truncate text-sm font-semibold text-zinc-100">{title}</h2>
          {sub && <p className="mt-0.5 truncate text-xs text-zinc-500">{sub}</p>}
        </div>
        {right}
      </div>
      {children}
    </section>
  );
}

function MetricBars({
  rows,
  metric,
  unavailable,
}: {
  rows: CandidateSummary[];
  metric: StrategyFilters["metric"];
  unavailable: boolean;
}) {
  const items = rows.map((row) => ({
    id: row.candidate_id,
    label: row.label,
    value: comparisonValue(row, metric),
  }));
  const numeric = items.map((item) => item.value).filter((value): value is number => typeof value === "number");
  const hasNegative = numeric.some((value) => value < 0);
  const maxAbs = Math.max(1, ...numeric.map((value) => Math.abs(value)));
  const maxPositive = Math.max(1, ...numeric);

  if (unavailable || numeric.length === 0) {
    return <div className="px-4 py-6 text-sm text-amber-300">当前窗口暂无可评分数据，PnL / Win Rate 会在评分文件更新后显示。</div>;
  }

  return (
    <div className="space-y-3 p-4">
      {items.map((item, index) => {
        const value = item.value ?? 0;
        const color = hasNegative ? (value < 0 ? "#ef4444" : value > 0 ? "#34d399" : "#71717a") : colors[index % colors.length];
        const width = hasNegative ? (Math.abs(value) / maxAbs) * 48 : (value / maxPositive) * 100;
        const style = hasNegative
          ? value >= 0
            ? { left: "50%", width: `${width}%`, backgroundColor: color }
            : { left: `${50 - width}%`, width: `${width}%`, backgroundColor: color }
          : { left: 0, width: `${width}%`, backgroundColor: color };
        return (
          <div key={item.id} className="grid gap-2 md:grid-cols-[180px_1fr_92px] md:items-center">
            <div className="min-w-0 truncate text-sm text-zinc-200">{item.label}</div>
            <div className="relative h-8 rounded bg-zinc-900">
              {hasNegative && <div className="absolute bottom-1 top-1 left-1/2 w-px bg-zinc-600" />}
              <div className="absolute top-2 h-4 rounded-sm" style={style} />
            </div>
            <div className="text-right font-mono text-sm text-zinc-300">{metricValueLabel(value, metric, false)}</div>
          </div>
        );
      })}
    </div>
  );
}

export default function Compare() {
  const [windowValue, setWindowValue] = useState<StrategyFilters["window"]>("today");
  const [bucket, setBucket] = useState<StrategyFilters["bucket"]>("hour");
  const [metric, setMetric] = useState<StrategyFilters["metric"]>("pnl");
  const [selected, setSelected] = useState<string[]>([
    "round2_drawdown_density",
    "official_truth_14d14d_latest",
    "official_truth_7d7d_latest",
    "official_truth_30d30d_latest",
  ]);
  const [data, setData] = useState<StrategyComparisonData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showRecent, setShowRecent] = useState(false);

  const queryString = useMemo(() => {
    const query = new URLSearchParams({
      window: windowValue,
      bucket,
      metric,
      candidates: selected.join(","),
    });
    return query.toString();
  }, [bucket, metric, selected, windowValue]);

  const fetchStrategyComparison = useCallback(
    async (signal?: AbortSignal) => {
      setLoading(true);
      try {
        const resp = await fetch(`/api/strategy-comparison?${queryString}`, { signal });
        if (!resp.ok) throw new Error(`${resp.status}`);
        const json = (await resp.json()) as StrategyComparisonData;
        setData(json);
        setError(null);
      } catch (err) {
        if (signal?.aborted) return;
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!signal?.aborted) setLoading(false);
      }
    },
    [queryString],
  );

  useEffect(() => {
    const controller = new AbortController();
    void fetchStrategyComparison(controller.signal);
    return () => controller.abort();
  }, [fetchStrategyComparison]);

  useEffect(() => {
    setBucket(defaultBucketForWindow(windowValue));
  }, [windowValue]);

  const collection = data?.collection;
  const candidates = data?.candidates ?? [];
  const windowCandidates = data?.window_candidates ?? candidates;
  const tableCandidates = windowCandidates.filter((row) => selected.includes(row.candidate_id));
  const recent = data?.recent_signals ?? [];
  const pendingMetric = metricPending(metric, collection);
  const coverage = data?.window_coverage;
  const partialWindow = Boolean(coverage?.partial && windowValue !== "today");
  const partialScoredMetric = partialWindow && metricIsScored(metric);
  const currentScoreLabel = scoreLabel(coverage);
  const currentMetricUnavailable = metricIsScored(metric) && (pendingMetric || partialScoredMetric);
  const liveMatched = data?.live_matched;
  const windowLabel =
    windowValue === "today"
      ? `今天交易日（${formatDateTime(coverage?.requested_start_at)} 起）`
      : coverage?.partial && typeof coverage.covered_days === "number"
      ? `请求 ${windowName(windowValue)} / 已采集 ${coverage.covered_days.toFixed(2)}天`
      : `${windowName(windowValue)}交易日`;

  const byCandidate = useMemo(() => {
    const groups: Record<string, StrategyTimePoint[]> = {};
    for (const point of data?.timeseries ?? []) {
      if (!groups[point.candidate_id]) groups[point.candidate_id] = [];
      groups[point.candidate_id].push(point);
    }
    return groups;
  }, [data?.timeseries]);

  const collectionDayProgress = collection ? collection.collection_days / collection.minimum_days_target : 0;
  const minPassed = tableCandidates.length ? Math.min(...tableCandidates.map((row) => row.passed)) : 0;
  const passedProgress = collection ? minPassed / collection.minimum_passed_signal_target : 0;
  const rankedCandidates = useMemo(() => {
    return [...tableCandidates].sort((a, b) => {
      const av = comparisonValue(a, metric);
      const bv = comparisonValue(b, metric);
      if (av == null && bv == null) return a.label.localeCompare(b.label);
      if (av == null) return 1;
      if (bv == null) return -1;
      return bv - av;
    });
  }, [metric, tableCandidates]);
  const bestCandidate = rankedCandidates.find((row) => comparisonValue(row, metric) != null) ?? rankedCandidates[0];
  const sampleStatus = useMemo(() => sampleComparable(tableCandidates), [tableCandidates]);
  const trendPointCounts = tableCandidates.map((row) => byCandidate[row.candidate_id]?.length ?? 0);
  const hasUsefulTrend = tableCandidates.length > 0 && trendPointCounts.every((count) => count >= 2);
  const trendPointLabel = trendPointCounts.length ? `${Math.min(...trendPointCounts)} 个时间点` : "0 个时间点";

  const toggleCandidate = (candidateId: string) => {
    setSelected((current) => {
      if (current.includes(candidateId)) {
        const next = current.filter((item) => item !== candidateId);
        return next.length ? next : current;
      }
      return [...current, candidateId];
    });
  };

  return (
    <div className="space-y-4">
      <div className="grid gap-3 lg:grid-cols-4">
        <DecisionCard
          title="当前实盘策略"
          icon={<RadioTower className="h-4 w-4" />}
          value={data?.live?.label ?? "-"}
          detail={`${data?.live?.would_place ?? 0} 个预测通过 / 通过率 ${percent(data?.live?.pass_rate)}`}
        />
        <DecisionCard
          title="窗口最佳"
          icon={<Target className="h-4 w-4" />}
          value={bestCandidate?.label ?? "-"}
          detail={
            bestCandidate
              ? `${metricLabel(metric)} ${metricValueLabel(
                  comparisonValue(bestCandidate, metric) ?? 0,
                  metric,
                  currentMetricUnavailable,
                  currentScoreLabel,
                )}`
              : "暂无可用策略"
          }
          ok={!currentMetricUnavailable}
        />
        <DecisionCard
          title="最新信号"
          icon={<Clock3 className="h-4 w-4" />}
          value={age(collection?.latest_signal_age_seconds)}
          detail={`页面生成 ${formatDateTime(data?.generated_at)} / 聚合 ${bucketName(bucket)}`}
        />
        <DecisionCard
          title="样本可比性"
          icon={<GitCompareArrows className="h-4 w-4" />}
          value={sampleStatus.label}
          detail={sampleStatus.detail}
          ok={sampleStatus.ok}
        />
      </div>

      <Panel
        title="数据状态"
        sub={`${windowLabel} / ${coverage?.time_basis ?? "live_trading_day"} / ${coverage?.day_tz ?? "Asia/Shanghai"}`}
        right={<StatusPill ok={Boolean(collection?.minimum_ready)} label={collection?.minimum_ready ? "最低样本已达标" : "采集中"} />}
      >
        <div className="grid gap-3 p-4 md:grid-cols-[1.2fr_1fr_1fr]">
          <div>
            <div className="mb-2 flex items-center justify-between text-xs text-zinc-500">
              <span>采集天数</span>
              <span className="font-mono">{collection?.collection_days?.toFixed(2) ?? "0.00"}天</span>
            </div>
            <ProgressBar value={collectionDayProgress} />
          </div>
          <div>
            <div className="mb-2 flex items-center justify-between text-xs text-zinc-500">
              <span>最少通过信号</span>
              <span className="font-mono">{minPassed}</span>
            </div>
            <ProgressBar value={passedProgress} />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <StatusPill ok={Boolean(collection?.signals_file_exists)} label="信号文件" />
            <StatusPill ok={Boolean(collection?.latest_file_exists)} label="最新摘要" />
            <StatusPill ok={Boolean(collection?.daily_summary_exists)} label="日汇总" />
            <StatusPill ok={Boolean(collection?.scored_summary_exists)} label="官方评分" />
          </div>
        </div>
        {data?.warnings?.length ? (
          <div className="border-t border-zinc-800 px-4 py-3 text-sm text-amber-300">{data.warnings.join(" | ")}</div>
        ) : null}
        {error ? <div className="border-t border-zinc-800 px-4 py-3 text-sm text-red-300">{error}</div> : null}
      </Panel>

      <Panel
        title="筛选"
        sub={pendingMetric ? "等待官方 / Data Streams 评分" : partialScoredMetric ? `${windowLabel} / ${currentScoreLabel}` : `${windowLabel} / ${metricLabel(metric)}`}
        right={
          <div className="flex items-center gap-2">
            <button
              onClick={() => void fetchStrategyComparison()}
              disabled={loading}
              className="inline-flex items-center gap-1 rounded border border-zinc-800 px-2 py-1 text-xs text-zinc-300 hover:text-zinc-100 disabled:opacity-60"
            >
              <RefreshCw className={`h-3 w-3 ${loading ? "animate-spin" : ""}`} />
              刷新
            </button>
            <StatusPill ok={!pendingMetric && !partialScoredMetric} label={pendingMetric ? "待评分" : partialScoredMetric ? currentScoreLabel : "评分可用"} />
          </div>
        }
      >
        <div className="grid gap-3 border-b border-zinc-800 p-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className="w-20 text-xs text-zinc-500">时间窗口</span>
            {(["today", "24h", "7d", "14d", "all"] as const).map((item) => (
              <button
                key={item}
                onClick={() => setWindowValue(item)}
                className={`rounded border px-3 py-1 text-xs ${
                  windowValue === item ? "border-emerald-400 text-emerald-300" : "border-zinc-800 text-zinc-400 hover:text-zinc-200"
                }`}
              >
                {windowName(item)}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="w-20 text-xs text-zinc-500">聚合粒度</span>
            {(["5m", "30m", "hour", "4h", "6h", "day"] as const).map((item) => (
              <button
                key={item}
                onClick={() => setBucket(item)}
                className={`rounded border px-3 py-1 text-xs ${
                  bucket === item ? "border-emerald-400 text-emerald-300" : "border-zinc-800 text-zinc-400 hover:text-zinc-200"
                }`}
              >
                {bucketName(item)}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="w-20 text-xs text-zinc-500">指标</span>
            {(["signals", "pnl", "win_rate", "overlap"] as const).map((item) => (
              <button
                key={item}
                onClick={() => setMetric(item)}
                className={`rounded border px-3 py-1 text-xs ${
                  metric === item ? "border-emerald-400 text-emerald-300" : "border-zinc-800 text-zinc-400 hover:text-zinc-200"
                }`}
              >
                {metricLabel(item)}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="w-20 text-xs text-zinc-500">策略选择</span>
            {candidates.map((row) => (
              <label key={row.candidate_id} className="inline-flex items-center gap-2 rounded border border-zinc-800 px-3 py-1 text-xs text-zinc-300">
                <input
                  type="checkbox"
                  className="accent-emerald-400"
                  checked={selected.includes(row.candidate_id)}
                  onChange={() => toggleCandidate(row.candidate_id)}
                />
                {row.label}
              </label>
            ))}
          </div>
        </div>
        {partialScoredMetric ? (
          <div className="border-b border-zinc-800 px-4 py-3 text-sm text-amber-300">
            {currentScoreLabel}: 候选策略评分只覆盖已采集的 no-submit 信号，不等同于完整交易窗口。
          </div>
        ) : null}
      </Panel>

      {!sampleStatus.ok && (
        <div className="rounded-md border border-amber-500/25 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
          {sampleStatus.detail}。请优先看同样采集范围内的策略，30d 回填结果不能直接和短窗口实时样本混为同一结论。
        </div>
      )}

      <Panel title="策略排名" sub={`${windowLabel} 汇总，按 ${metricLabel(metric)} 排序`}>
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-zinc-900 text-sm">
            <thead className="bg-black/20 text-xs text-zinc-500">
              <tr>
                <th className="px-4 py-2 text-left font-medium">策略</th>
                <th className="px-3 py-2 text-left font-medium">角色</th>
                <th className="px-3 py-2 text-right font-medium">样本</th>
                <th className="px-3 py-2 text-right font-medium">通过</th>
                <th className="px-3 py-2 text-right font-medium">通过率</th>
                <th className="px-3 py-2 text-right font-medium">Win Rate</th>
                <th className="px-3 py-2 text-right font-medium">PnL</th>
                <th className="px-3 py-2 text-right font-medium">同向重合</th>
                <th className="px-3 py-2 text-right font-medium">差异信号</th>
                <th className="px-3 py-2 text-right font-medium">状态</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-900">
              {rankedCandidates.map((row) => (
                <tr key={row.candidate_id} className="hover:bg-zinc-900/40">
                  <td className="px-4 py-2 text-zinc-100">
                    <div className="font-medium">{row.label}</div>
                    <div className="mt-0.5 font-mono text-xs text-zinc-500">{row.candidate_id}</div>
                  </td>
                  <td className="px-3 py-2 text-zinc-300">{roleLabel(row, data?.live?.strategy_id)}</td>
                  <td className="px-3 py-2 text-right font-mono text-zinc-300">{row.evaluated}</td>
                  <td className="px-3 py-2 text-right font-mono text-zinc-300">{row.passed}</td>
                  <td className="px-3 py-2 text-right font-mono text-zinc-300">{percent(row.pass_rate)}</td>
                  <td className={`px-3 py-2 text-right font-mono ${partialWindow ? "text-amber-300" : "text-zinc-300"}`}>
                    {partialWindow ? currentScoreLabel : typeof row.win_rate === "number" ? percent(row.win_rate) : "待评分"}
                  </td>
                  <td className={`px-3 py-2 text-right font-mono ${partialWindow ? "text-amber-300" : "text-zinc-300"}`}>
                    {partialWindow ? currentScoreLabel : signedMoneyOrPending(row.pnl_usdc)}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-zinc-300">{row.same_side_overlap}</td>
                  <td className="px-3 py-2 text-right font-mono text-zinc-300">
                    {row.candidate_only} / {row.live_signal_filtered}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <StatusPill ok={row.minimum_ready} label={row.minimum_ready ? "可用" : "采集中"} />
                  </td>
                </tr>
              ))}
              {rankedCandidates.length === 0 && (
                <tr>
                  <td className="px-4 py-8 text-center text-sm text-zinc-500" colSpan={10}>
                    当前没有选中的策略
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel title="指标对比" sub={`横向对比 ${metricLabel(metric)}，比旧折线更适合当前窗口`}>
        <MetricBars rows={rankedCandidates} metric={metric} unavailable={currentMetricUnavailable} />
      </Panel>

      <Panel title="趋势检查" sub={`${bucketName(bucket)}粒度 / ${trendPointLabel}`}>
        {!hasUsefulTrend ? (
          <div className="px-4 py-5 text-sm text-amber-300">
            趋势图数据点不足：当前选择下每个策略至少需要 2 个时间点。单点数据已改用上方横向对比，避免误读成趋势。
          </div>
        ) : (
          <div className="grid gap-4 p-4 lg:grid-cols-2">
            {rankedCandidates.map((row, index) => {
              const points = byCandidate[row.candidate_id] ?? [];
              const values = points.map((point) => seriesValue(point, metric));
              return (
                <div key={row.candidate_id} className="min-w-0 rounded-md border border-zinc-800 bg-black/20 p-3">
                  <div className="mb-2 flex items-center justify-between gap-3">
                    <span className="truncate text-sm font-medium text-zinc-100">{row.label}</span>
                    <span className="font-mono text-xs text-zinc-500">{values.length}点</span>
                  </div>
                  <Chart
                    data={values}
                    labels={points.map((point) => formatBucket(point.bucket, bucket))}
                    color={colors[index % colors.length]}
                    formatValue={(value) => metricValueLabel(value, metric, currentMetricUnavailable, currentScoreLabel)}
                    showZeroLine={metric === "pnl"}
                    showGrid
                  />
                </div>
              );
            })}
          </div>
        )}
      </Panel>

      <StrategyDifferences
        windowValue={windowValue}
        liveStrategyId={data?.live?.strategy_id}
        selectedCandidateIds={selected}
        comparison={liveMatched}
      />

      <Panel
        title="最近候选信号"
        sub="最新 no-submit 记录，默认折叠"
        right={
          <button
            onClick={() => setShowRecent((value) => !value)}
            className="inline-flex items-center gap-1 rounded border border-zinc-800 px-2 py-1 text-xs text-zinc-300 hover:text-zinc-100"
          >
            <ChevronDown className={`h-3 w-3 transition-transform ${showRecent ? "rotate-180" : ""}`} />
            {showRecent ? "收起" : "展开"}
          </button>
        }
      >
        {!showRecent ? (
          <div className="flex flex-wrap items-center gap-4 px-4 py-3 text-sm text-zinc-400">
            <span>最新：{age(collection?.latest_signal_age_seconds)}</span>
            <span>缓存行数：{recent.length}</span>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-zinc-900 text-sm">
              <thead className="bg-black/20 text-xs text-zinc-500">
                <tr>
                  <th className="px-4 py-2 text-left font-medium">生成时间</th>
                  <th className="px-3 py-2 text-left font-medium">策略</th>
                  <th className="px-3 py-2 text-right font-medium">动作</th>
                  <th className="px-3 py-2 text-right font-medium">方向</th>
                  <th className="px-3 py-2 text-right font-medium">P5 / P1 / P4</th>
                  <th className="px-3 py-2 text-right font-medium">关系</th>
                  <th className="px-3 py-2 text-right font-medium">原因</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-900">
                {recent.map((row, index) => (
                  <tr key={`${row.created_at}-${row.candidate_id}-${index}`} className="hover:bg-zinc-900/40">
                    <td className="px-4 py-2 font-mono text-xs text-zinc-400">{formatDateTime(row.created_at)}</td>
                    <td className="px-3 py-2 text-zinc-200">{row.candidate_id ?? "-"}</td>
                    <td className="px-3 py-2 text-right font-mono text-zinc-300">{row.action ?? "HOLD"}</td>
                    <td className="px-3 py-2 text-right font-mono text-zinc-300">{row.side ?? "-"}</td>
                    <td className="px-3 py-2 text-right font-mono text-zinc-300">
                      {percent(row.p5_up)} / {percent(row.p1_up)} / {percent(row.p4_up)}
                    </td>
                    <td className="px-3 py-2 text-right text-zinc-300">
                      {row.same_side_overlap ? "同向重合" : row.candidate_only ? "仅候选" : row.live_signal_filtered ? "过滤实盘" : "-"}
                    </td>
                    <td className="px-3 py-2 text-right text-zinc-400">{row.reason_code ?? "-"}</td>
                  </tr>
                ))}
                {recent.length === 0 && (
                  <tr>
                    <td className="px-4 py-8 text-center text-sm text-zinc-500" colSpan={7}>
                      暂无 no-submit 信号
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
