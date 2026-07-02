import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  GitCompareArrows,
  RadioTower,
  Target,
} from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { Chart } from "@/components/chart";
import { usePolling } from "../hooks/usePolling";

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
};

type StrategyFilters = {
  window: "24h" | "7d" | "14d" | "all";
  bucket: "hour" | "day";
  metric: "signals" | "pnl" | "win_rate" | "overlap";
  candidates: string[];
  available_metrics: string[];
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
  filters: StrategyFilters;
  timeseries: StrategyTimePoint[];
  recent_signals: RecentSignal[];
};

const panel = "rounded-md border border-zinc-800 bg-zinc-950/70";
const colors = ["var(--chart-1)", "var(--chart-2)", "var(--chart-4)", "#34d399"];

const moneyOrPending = (value: unknown) => (typeof value === "number" ? `$${value.toFixed(2)}` : "Pending");
const percent = (value?: number | null) =>
  value == null || Number.isNaN(value) ? "-" : `${(value * 100).toFixed(1)}%`;
const age = (seconds?: number | null) =>
  seconds == null ? "-" : seconds < 60 ? `${seconds}s` : `${Math.round(seconds / 60)}m`;

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
      className={`inline-flex items-center gap-1 rounded border px-2 py-1 text-xs ${
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

function seriesValue(point: StrategyTimePoint, metric: StrategyFilters["metric"]) {
  if (metric === "pnl") return typeof point.pnl_usdc === "number" ? point.pnl_usdc : 0;
  if (metric === "win_rate") return typeof point.win_rate === "number" ? point.win_rate * 100 : 0;
  if (metric === "overlap") return point.same_side_overlap;
  return point.passed;
}

function metricLabel(metric: StrategyFilters["metric"]) {
  if (metric === "pnl") return "PnL";
  if (metric === "win_rate") return "Win Rate";
  if (metric === "overlap") return "Overlap";
  return "Passed Signals";
}

function metricPending(metric: StrategyFilters["metric"], collection?: CollectionSummary) {
  return (metric === "pnl" || metric === "win_rate") && !collection?.scored_summary_exists;
}

function latestMetricLabel(value: number, metric: StrategyFilters["metric"], pending: boolean) {
  if (pending) return "Pending";
  if (metric === "pnl") return `$${value.toFixed(2)}`;
  if (metric === "win_rate") return `${value.toFixed(1)}%`;
  return value.toFixed(0);
}

export default function Compare() {
  const [windowValue, setWindowValue] = useState<StrategyFilters["window"]>("7d");
  const [bucket, setBucket] = useState<StrategyFilters["bucket"]>("day");
  const [metric, setMetric] = useState<StrategyFilters["metric"]>("signals");
  const [selected, setSelected] = useState<string[]>([
    "round2_drawdown_density",
    "official_truth_14d14d_latest",
    "official_truth_7d7d_latest",
  ]);

  const query = new URLSearchParams({
    window: windowValue,
    bucket,
    metric,
    candidates: selected.join(","),
  });
  const { data } = usePolling<StrategyComparisonData>(`/api/strategy-comparison?${query.toString()}`, 10000);
  const collection = data?.collection;
  const candidates = data?.candidates ?? [];
  const recent = data?.recent_signals ?? [];
  const pendingMetric = metricPending(metric, collection);

  const byCandidate = useMemo(() => {
    const groups: Record<string, StrategyTimePoint[]> = {};
    for (const point of data?.timeseries ?? []) {
      if (!groups[point.candidate_id]) groups[point.candidate_id] = [];
      groups[point.candidate_id].push(point);
    }
    return groups;
  }, [data?.timeseries]);

  const collectionDayProgress = collection ? collection.collection_days / collection.minimum_days_target : 0;
  const minPassed = candidates.length ? Math.min(...candidates.map((row) => row.passed)) : 0;
  const passedProgress = collection ? minPassed / collection.minimum_passed_signal_target : 0;

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
        <div className={panel + " p-4"}>
          <div className="flex items-center justify-between gap-3">
            <span className="text-xs uppercase tracking-[0.16em] text-zinc-500">Collection</span>
            <RadioTower className="h-4 w-4 text-zinc-500" />
          </div>
          <div className="mt-3 font-mono text-2xl font-semibold text-zinc-100">
            {collection?.collection_days?.toFixed(2) ?? "0.00"}d
          </div>
          <div className="mt-2">
            <ProgressBar value={collectionDayProgress} />
          </div>
        </div>

        <div className={panel + " p-4"}>
          <div className="flex items-center justify-between gap-3">
            <span className="text-xs uppercase tracking-[0.16em] text-zinc-500">Min Passed</span>
            <Target className="h-4 w-4 text-zinc-500" />
          </div>
          <div className="mt-3 font-mono text-2xl font-semibold text-zinc-100">{minPassed}</div>
          <div className="mt-2">
            <ProgressBar value={passedProgress} />
          </div>
        </div>

        <div className={panel + " p-4"}>
          <div className="flex items-center justify-between gap-3">
            <span className="text-xs uppercase tracking-[0.16em] text-zinc-500">Round2 Signals</span>
            <GitCompareArrows className="h-4 w-4 text-zinc-500" />
          </div>
          <div className="mt-3 font-mono text-2xl font-semibold text-zinc-100">{data?.live?.would_place ?? 0}</div>
          <div className="mt-1 text-xs text-zinc-500">{percent(data?.live?.pass_rate)} pass rate</div>
        </div>

        <div className={panel + " p-4"}>
          <div className="flex items-center justify-between gap-3">
            <span className="text-xs uppercase tracking-[0.16em] text-zinc-500">Freshness</span>
            <Clock3 className="h-4 w-4 text-zinc-500" />
          </div>
          <div className="mt-3 font-mono text-2xl font-semibold text-zinc-100">
            {age(collection?.latest_signal_age_seconds)}
          </div>
          <div className="mt-1 text-xs text-zinc-500">latest no-submit signal</div>
        </div>
      </div>

      <Panel
        title="Readiness"
        sub="no-submit evidence before formal-live discussion"
        right={<StatusPill ok={Boolean(collection?.minimum_ready)} label={collection?.minimum_ready ? "Minimum ready" : "Collecting"} />}
      >
        <div className="grid gap-3 p-4 md:grid-cols-4">
          <StatusPill ok={Boolean(collection?.signals_file_exists)} label="Signals file" />
          <StatusPill ok={Boolean(collection?.latest_file_exists)} label="Latest summary" />
          <StatusPill ok={Boolean(collection?.daily_summary_exists)} label="Daily summary" />
          <StatusPill ok={Boolean(collection?.scored_summary_exists)} label="Official scoring" />
        </div>
        {data?.warnings?.length ? (
          <div className="border-t border-zinc-800 px-4 py-3 text-sm text-amber-300">{data.warnings.join(" | ")}</div>
        ) : null}
      </Panel>

      <Panel
        title="Strategy Trends"
        sub={pendingMetric ? "pending official/Data Streams scoring" : `${windowValue} / ${bucket} / ${metricLabel(metric)}`}
        right={<StatusPill ok={!pendingMetric} label={pendingMetric ? "Pending scoring" : "Live chart"} />}
      >
        <div className="flex flex-wrap items-center gap-2 border-b border-zinc-800 p-4">
          {(["24h", "7d", "14d", "all"] as const).map((item) => (
            <button
              key={item}
              onClick={() => setWindowValue(item)}
              className={`rounded border px-3 py-1 text-xs ${
                windowValue === item ? "border-emerald-400 text-emerald-300" : "border-zinc-800 text-zinc-400 hover:text-zinc-200"
              }`}
            >
              {item}
            </button>
          ))}
          {(["hour", "day"] as const).map((item) => (
            <button
              key={item}
              onClick={() => setBucket(item)}
              className={`rounded border px-3 py-1 text-xs ${
                bucket === item ? "border-emerald-400 text-emerald-300" : "border-zinc-800 text-zinc-400 hover:text-zinc-200"
              }`}
            >
              {item}
            </button>
          ))}
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

        <div className="grid gap-4 p-4 lg:grid-cols-3">
          {candidates
            .filter((row) => selected.includes(row.candidate_id))
            .map((row, index) => {
              const points = byCandidate[row.candidate_id] ?? [];
              const values = points.map((point) => seriesValue(point, metric));
              const latestValue = values.length ? values[values.length - 1] : 0;
              return (
                <div key={row.candidate_id} className="min-w-0 rounded-md border border-zinc-800 bg-black/20 p-3">
                  <div className="mb-2 flex items-center justify-between gap-3">
                    <span className="truncate text-sm font-medium text-zinc-100">{row.label}</span>
                    <span className="font-mono text-xs text-zinc-500">{latestMetricLabel(latestValue, metric, pendingMetric)}</span>
                  </div>
                  <Chart
                    data={values.length ? values : [0]}
                    color={colors[index % colors.length]}
                    formatValue={(value) => latestMetricLabel(value, metric, pendingMetric)}
                    showZeroLine={metric === "pnl"}
                  />
                </div>
              );
            })}
        </div>
      </Panel>

      <Panel title="Strategy Comparison" sub="Round2 baseline vs official-truth candidates">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-zinc-900 text-sm">
            <thead className="bg-black/20 text-xs uppercase tracking-[0.14em] text-zinc-500">
              <tr>
                <th className="px-4 py-2 text-left font-medium">Strategy</th>
                <th className="px-3 py-2 text-right font-medium">Evaluated</th>
                <th className="px-3 py-2 text-right font-medium">Passed</th>
                <th className="px-3 py-2 text-right font-medium">Pass Rate</th>
                <th className="px-3 py-2 text-right font-medium">Long / Short</th>
                <th className="px-3 py-2 text-right font-medium">Overlap</th>
                <th className="px-3 py-2 text-right font-medium">Candidate Only</th>
                <th className="px-3 py-2 text-right font-medium">Filtered Live</th>
                <th className="px-3 py-2 text-right font-medium">PnL</th>
                <th className="px-3 py-2 text-right font-medium">Win Rate</th>
                <th className="px-3 py-2 text-right font-medium">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-900">
              {candidates.map((row) => (
                <tr key={row.candidate_id} className="hover:bg-zinc-900/40">
                  <td className="px-4 py-2 text-zinc-100">{row.label}</td>
                  <td className="px-3 py-2 text-right font-mono text-zinc-300">{row.evaluated}</td>
                  <td className="px-3 py-2 text-right font-mono text-zinc-300">{row.passed}</td>
                  <td className="px-3 py-2 text-right font-mono text-zinc-300">{percent(row.pass_rate)}</td>
                  <td className="px-3 py-2 text-right font-mono text-zinc-300">
                    {row.long} / {row.short}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-zinc-300">{row.same_side_overlap}</td>
                  <td className="px-3 py-2 text-right font-mono text-zinc-300">{row.candidate_only}</td>
                  <td className="px-3 py-2 text-right font-mono text-zinc-300">{row.live_signal_filtered}</td>
                  <td className="px-3 py-2 text-right font-mono text-zinc-300">{moneyOrPending(row.scored?.["total_pnl"])}</td>
                  <td className="px-3 py-2 text-right font-mono text-zinc-300">
                    {typeof row.scored?.["win_rate"] === "number" ? percent(row.scored["win_rate"] as number) : "Pending"}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <StatusPill ok={row.minimum_ready} label={row.minimum_ready ? "Ready" : "Collecting"} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel title="Recent Candidate Signals" sub="latest no-submit records">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-zinc-900 text-sm">
            <thead className="bg-black/20 text-xs uppercase tracking-[0.14em] text-zinc-500">
              <tr>
                <th className="px-4 py-2 text-left font-medium">Created</th>
                <th className="px-3 py-2 text-left font-medium">Candidate</th>
                <th className="px-3 py-2 text-right font-medium">Action</th>
                <th className="px-3 py-2 text-right font-medium">Side</th>
                <th className="px-3 py-2 text-right font-medium">P5 / P1 / P4</th>
                <th className="px-3 py-2 text-right font-medium">Relation</th>
                <th className="px-3 py-2 text-right font-medium">Reason</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-900">
              {recent.map((row, index) => (
                <tr key={`${row.created_at}-${row.candidate_id}-${index}`} className="hover:bg-zinc-900/40">
                  <td className="px-4 py-2 font-mono text-xs text-zinc-400">{row.created_at ?? "-"}</td>
                  <td className="px-3 py-2 text-zinc-200">{row.candidate_id ?? "-"}</td>
                  <td className="px-3 py-2 text-right font-mono text-zinc-300">{row.action ?? "HOLD"}</td>
                  <td className="px-3 py-2 text-right font-mono text-zinc-300">{row.side ?? "-"}</td>
                  <td className="px-3 py-2 text-right font-mono text-zinc-300">
                    {percent(row.p5_up)} / {percent(row.p1_up)} / {percent(row.p4_up)}
                  </td>
                  <td className="px-3 py-2 text-right text-zinc-300">
                    {row.same_side_overlap ? "overlap" : row.candidate_only ? "candidate only" : row.live_signal_filtered ? "filtered live" : "-"}
                  </td>
                  <td className="px-3 py-2 text-right text-zinc-400">{row.reason_code ?? "-"}</td>
                </tr>
              ))}
              {recent.length === 0 && (
                <tr>
                  <td className="px-4 py-8 text-center text-sm text-zinc-500" colSpan={7}>
                    No no-submit signals
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}
