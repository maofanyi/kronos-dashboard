import {
  Activity,
  BarChart3,
  CircleDollarSign,
  ListChecks,
  Percent,
  Target,
  TrendingDown,
} from "lucide-react";
import type { ReactNode } from "react";
import { Chart } from "@/components/chart";
import { usePolling } from "../hooks/usePolling";

type MetricSummary = {
  count: number;
  settled: number;
  wins: number;
  losses: number;
  win_rate: number;
  total_pnl_usdc: number;
  gross_profit_usdc: number;
  gross_loss_usdc: number;
  average_pnl_usdc: number;
  average_win_usdc?: number | null;
  average_loss_usdc?: number | null;
  profit_factor?: number | null;
  payoff_ratio?: number | null;
  breakeven_win_rate?: number | null;
};

type AnalyticsSummary = MetricSummary & {
  max_drawdown_usdc: number;
  max_drawdown_pct: number;
  current_drawdown_usdc: number;
  current_drawdown_pct: number;
  longest_win_streak: number;
  longest_loss_streak: number;
  current_win_streak: number;
  current_loss_streak: number;
};

type BreakdownRow = MetricSummary & {
  key: string;
  label: string;
};

type DailyRow = MetricSummary & {
  date: string;
};

type AnalyticsData = {
  source: string;
  ledger: string;
  ledger_exists: boolean;
  record_count: number;
  generated_at?: string | null;
  summary: AnalyticsSummary;
  windows: Record<"today_utc" | "last_24h" | "last_7d" | "all", MetricSummary>;
  equity: {
    points: number[];
    labels?: string[];
    pnl_usdc: number;
    settled: number;
    drawdown_points: number[];
    drawdown: {
      max_drawdown_usdc: number;
      max_drawdown_pct: number;
      current_drawdown_usdc: number;
      current_drawdown_pct: number;
      peak: number;
      trough: number;
    };
  };
  pnl_composition: {
    gross_profit_usdc: number;
    gross_loss_usdc: number;
    absolute_loss_usdc: number;
    profit_share: number;
    loss_share: number;
  };
  breakdowns: {
    price_tier: BreakdownRow[];
    direction: BreakdownRow[];
    price: BreakdownRow[];
    settlement_source: BreakdownRow[];
    status: BreakdownRow[];
  };
  daily: DailyRow[];
  hypothetical_no_fill: MetricSummary & {
    breakdowns: {
      price_tier: BreakdownRow[];
      direction: BreakdownRow[];
      price: BreakdownRow[];
    };
    records: Array<Record<string, unknown>>;
  };
  recent: Array<Record<string, unknown>>;
};

const money = (value?: number | null, digits = 2) =>
  value == null || Number.isNaN(value) ? "-" : `$${value.toFixed(digits)}`;

const signedMoney = (value?: number | null, digits = 2) => {
  if (value == null || Number.isNaN(value)) return "-";
  const prefix = value > 0 ? "+" : value < 0 ? "-" : "";
  return `${prefix}$${Math.abs(value).toFixed(digits)}`;
};

const percent = (value?: number | null) =>
  value == null || Number.isNaN(value) ? "-" : `${(value * 100).toFixed(1)}%`;

const ratio = (value?: number | null) =>
  value == null || Number.isNaN(value) ? "-" : value.toFixed(2);

const shortDate = (value?: string | null) => {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString(undefined, { month: "2-digit", day: "2-digit" });
};

const labelText = (value?: string | null) => (value || "-").replace(/_/g, " ");

function Panel({ title, sub, children, right }: { title: string; sub?: string; children: ReactNode; right?: ReactNode }) {
  return (
    <section className="w-full min-w-0 overflow-hidden rounded-md border border-zinc-800 bg-zinc-950/70">
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

function StatCard({
  label,
  value,
  sub,
  icon: Icon,
  tone = "text-zinc-100",
}: {
  label: string;
  value: string;
  sub?: string;
  icon: typeof Activity;
  tone?: string;
}) {
  return (
    <div className="min-w-0 rounded-md border border-zinc-800 bg-zinc-950/70 p-4">
      <div className="flex items-center justify-between gap-3">
        <span className="truncate text-xs uppercase tracking-[0.16em] text-zinc-500">{label}</span>
        <Icon className="h-4 w-4 text-zinc-500" />
      </div>
      <div className={`mt-3 truncate font-mono text-2xl font-semibold ${tone}`}>{value}</div>
      {sub && <div className="mt-1 truncate text-xs text-zinc-500">{sub}</div>}
    </div>
  );
}

function StatusPill({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span className={`inline-flex items-center rounded border px-2 py-1 text-xs ${ok ? "border-emerald-500/25 bg-emerald-500/10 text-emerald-300" : "border-amber-500/25 bg-amber-500/10 text-amber-300"}`}>
      {label}
    </span>
  );
}

function BreakdownTable({ rows, title, sub }: { rows?: BreakdownRow[]; title: string; sub: string }) {
  const data = rows ?? [];
  return (
    <Panel title={title} sub={sub} right={<StatusPill ok={data.length > 0} label={`${data.length} groups`} />}>
      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-zinc-900 text-sm">
          <thead className="bg-black/20 text-xs uppercase tracking-[0.14em] text-zinc-500">
            <tr>
              <th className="px-4 py-2 text-left font-medium">Group</th>
              <th className="px-3 py-2 text-right font-medium">Trades</th>
              <th className="px-3 py-2 text-right font-medium">W/L</th>
              <th className="px-3 py-2 text-right font-medium">Win Rate</th>
              <th className="px-3 py-2 text-right font-medium">PnL</th>
              <th className="px-3 py-2 text-right font-medium">PF</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-900">
            {data.map((row) => {
              const tone = row.total_pnl_usdc >= 0 ? "text-emerald-300" : "text-rose-300";
              return (
                <tr key={row.key} className="hover:bg-zinc-900/40">
                  <td className="max-w-[220px] truncate px-4 py-2 text-zinc-200" title={row.label}>{labelText(row.label)}</td>
                  <td className="px-3 py-2 text-right font-mono text-zinc-300">{row.count}</td>
                  <td className="px-3 py-2 text-right font-mono text-zinc-400">{row.wins}/{row.losses}</td>
                  <td className="px-3 py-2 text-right font-mono text-zinc-300">{percent(row.win_rate)}</td>
                  <td className={`px-3 py-2 text-right font-mono font-semibold ${tone}`}>{signedMoney(row.total_pnl_usdc)}</td>
                  <td className="px-3 py-2 text-right font-mono text-zinc-300">{ratio(row.profit_factor)}</td>
                </tr>
              );
            })}
            {data.length === 0 && (
              <tr>
                <td className="px-4 py-8 text-center text-sm text-zinc-500" colSpan={6}>No settled records</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function WindowPanel({ windows }: { windows?: AnalyticsData["windows"] }) {
  const rows: Array<[string, MetricSummary | undefined]> = [
    ["Today UTC", windows?.today_utc],
    ["Last 24h", windows?.last_24h],
    ["Last 7d", windows?.last_7d],
    ["All", windows?.all],
  ];
  return (
    <Panel title="Performance Windows" sub="actual settled orders only">
      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-zinc-900 text-sm">
          <thead className="bg-black/20 text-xs uppercase tracking-[0.14em] text-zinc-500">
            <tr>
              <th className="px-4 py-2 text-left font-medium">Window</th>
              <th className="px-3 py-2 text-right font-medium">Trades</th>
              <th className="px-3 py-2 text-right font-medium">Win Rate</th>
              <th className="px-3 py-2 text-right font-medium">PnL</th>
              <th className="px-3 py-2 text-right font-medium">Avg</th>
              <th className="px-3 py-2 text-right font-medium">PF</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-900">
            {rows.map(([label, row]) => (
              <tr key={label} className="hover:bg-zinc-900/40">
                <td className="px-4 py-2 text-zinc-200">{label}</td>
                <td className="px-3 py-2 text-right font-mono text-zinc-300">{row?.count ?? 0}</td>
                <td className="px-3 py-2 text-right font-mono text-zinc-300">{percent(row?.win_rate)}</td>
                <td className={`px-3 py-2 text-right font-mono font-semibold ${(row?.total_pnl_usdc ?? 0) >= 0 ? "text-emerald-300" : "text-rose-300"}`}>{signedMoney(row?.total_pnl_usdc)}</td>
                <td className="px-3 py-2 text-right font-mono text-zinc-300">{signedMoney(row?.average_pnl_usdc)}</td>
                <td className="px-3 py-2 text-right font-mono text-zinc-300">{ratio(row?.profit_factor)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function PnlCompositionPanel({ data }: { data?: AnalyticsData["pnl_composition"] }) {
  const profitShare = Math.max(0, Math.min(100, (data?.profit_share ?? 0) * 100));
  const lossShare = Math.max(0, Math.min(100, (data?.loss_share ?? 0) * 100));
  return (
    <Panel title="PnL Composition" sub="gross profit vs gross loss">
      <div className="space-y-4 p-4">
        <div>
          <div className="mb-1 flex items-center justify-between gap-3 text-xs">
            <span className="text-zinc-500">Gross Profit</span>
            <span className="font-mono text-emerald-300">{signedMoney(data?.gross_profit_usdc)}</span>
          </div>
          <div className="h-2 overflow-hidden rounded bg-zinc-900">
            <div className="h-full bg-emerald-400" style={{ width: `${profitShare}%` }} />
          </div>
        </div>
        <div>
          <div className="mb-1 flex items-center justify-between gap-3 text-xs">
            <span className="text-zinc-500">Gross Loss</span>
            <span className="font-mono text-rose-300">{signedMoney(data?.gross_loss_usdc)}</span>
          </div>
          <div className="h-2 overflow-hidden rounded bg-zinc-900">
            <div className="h-full bg-rose-400" style={{ width: `${lossShare}%` }} />
          </div>
        </div>
        <div className="grid grid-cols-2 gap-2 text-xs">
          <div className="rounded border border-zinc-800 bg-black/20 px-3 py-2">
            <div className="uppercase tracking-[0.14em] text-zinc-600">Profit Share</div>
            <div className="mt-1 font-mono text-zinc-200">{percent(data?.profit_share)}</div>
          </div>
          <div className="rounded border border-zinc-800 bg-black/20 px-3 py-2">
            <div className="uppercase tracking-[0.14em] text-zinc-600">Loss Share</div>
            <div className="mt-1 font-mono text-zinc-200">{percent(data?.loss_share)}</div>
          </div>
        </div>
      </div>
    </Panel>
  );
}

function DailyPnlPanel({ rows }: { rows?: DailyRow[] }) {
  const recent = (rows ?? []).slice(-14).reverse();
  const maxAbs = Math.max(1, ...recent.map((row) => Math.abs(row.total_pnl_usdc || 0)));
  return (
    <Panel title="Daily PnL" sub="last 14 UTC days with settled orders">
      <div className="space-y-2 p-4">
        {recent.map((row) => {
          const pnl = row.total_pnl_usdc || 0;
          const tone = pnl >= 0 ? "bg-emerald-400" : "bg-rose-400";
          return (
            <div key={row.date} className="grid grid-cols-[70px_1fr_90px] items-center gap-3 text-xs">
              <div className="font-mono text-zinc-500">{shortDate(row.date)}</div>
              <div className="h-2 overflow-hidden rounded bg-zinc-900">
                <div className={`h-full ${tone}`} style={{ width: `${Math.max(4, (Math.abs(pnl) / maxAbs) * 100)}%` }} />
              </div>
              <div className={`text-right font-mono font-semibold ${pnl >= 0 ? "text-emerald-300" : "text-rose-300"}`}>{signedMoney(pnl)}</div>
            </div>
          );
        })}
        {recent.length === 0 && <div className="py-8 text-center text-sm text-zinc-500">No settled daily records</div>}
      </div>
    </Panel>
  );
}

function HypotheticalNoFillPanel({ data }: { data?: AnalyticsData["hypothetical_no_fill"] }) {
  const pnl = data?.total_pnl_usdc ?? 0;
  return (
    <Panel
      title="No-Fill Outcome"
      sub="closed limit orders with known market result, not counted in real PnL"
      right={<StatusPill ok={pnl >= 0} label={data?.count ? signedMoney(pnl) : "No sample"} />}
    >
      <div className="grid grid-cols-2 gap-2 p-4 md:grid-cols-4">
        <div className="rounded border border-zinc-800 bg-black/20 px-3 py-2">
          <div className="uppercase tracking-[0.14em] text-zinc-600">Samples</div>
          <div className="mt-1 font-mono text-zinc-200">{data?.count ?? 0}</div>
        </div>
        <div className="rounded border border-zinc-800 bg-black/20 px-3 py-2">
          <div className="uppercase tracking-[0.14em] text-zinc-600">Win Rate</div>
          <div className="mt-1 font-mono text-zinc-200">{percent(data?.win_rate)}</div>
        </div>
        <div className="rounded border border-zinc-800 bg-black/20 px-3 py-2">
          <div className="uppercase tracking-[0.14em] text-zinc-600">Would PnL</div>
          <div className={`mt-1 font-mono ${pnl >= 0 ? "text-emerald-300" : "text-rose-300"}`}>{signedMoney(pnl)}</div>
        </div>
        <div className="rounded border border-zinc-800 bg-black/20 px-3 py-2">
          <div className="uppercase tracking-[0.14em] text-zinc-600">Avg</div>
          <div className="mt-1 font-mono text-zinc-200">{signedMoney(data?.average_pnl_usdc)}</div>
        </div>
      </div>
    </Panel>
  );
}

export default function Analytics() {
  const { data, error, loading } = usePolling<AnalyticsData>("/api/live-analytics", 5000);
  const summary = data?.summary;
  const pnl = summary?.total_pnl_usdc ?? 0;
  const drawdown = summary?.max_drawdown_usdc ?? 0;
  const equityPoints = data?.equity?.points ?? [];
  const drawdownPoints = data?.equity?.drawdown_points ?? [];

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-base font-semibold text-zinc-100">Analytics</h1>
          <p className="mt-1 text-xs text-zinc-500">{data?.ledger_exists ? data.ledger : "Waiting for live ledger"}</p>
        </div>
        <div className="flex items-center gap-2">
          {loading && <StatusPill ok label="Refreshing" />}
          {error && <StatusPill ok={false} label={`API ${error}`} />}
          <StatusPill ok={data?.ledger_exists === true} label={data?.generated_at ? `Updated ${new Date(data.generated_at).toLocaleTimeString()}` : "No data"} />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 2xl:grid-cols-6">
        <StatCard label="Total PnL" value={signedMoney(pnl)} sub={`${summary?.settled ?? 0} settled`} icon={CircleDollarSign} tone={pnl >= 0 ? "text-emerald-300" : "text-rose-300"} />
        <StatCard label="Win Rate" value={percent(summary?.win_rate)} sub={`${summary?.wins ?? 0}W / ${summary?.losses ?? 0}L`} icon={Target} tone={(summary?.win_rate ?? 0) >= 0.51 ? "text-emerald-300" : "text-amber-300"} />
        <StatCard label="Profit Factor" value={ratio(summary?.profit_factor)} sub={`payoff ${ratio(summary?.payoff_ratio)}`} icon={Percent} tone={(summary?.profit_factor ?? 0) >= 1 ? "text-emerald-300" : "text-amber-300"} />
        <StatCard label="Max Drawdown" value={money(drawdown)} sub={percent(summary?.max_drawdown_pct)} icon={TrendingDown} tone={drawdown > 0 ? "text-rose-300" : "text-emerald-300"} />
        <StatCard label="Avg PnL" value={signedMoney(summary?.average_pnl_usdc)} sub={`avg win ${signedMoney(summary?.average_win_usdc)}`} icon={Activity} tone={(summary?.average_pnl_usdc ?? 0) >= 0 ? "text-emerald-300" : "text-rose-300"} />
        <StatCard label="Streaks" value={`${summary?.current_win_streak ?? 0}W / ${summary?.current_loss_streak ?? 0}L`} sub={`max loss ${summary?.longest_loss_streak ?? 0}`} icon={ListChecks} />
      </div>

      <div className="grid min-w-0 gap-5 2xl:grid-cols-[minmax(0,1.4fr)_minmax(360px,0.7fr)]">
        <Panel
          title="Equity Curve"
          sub="actual settled ledger"
          right={<StatusPill ok={pnl >= 0} label={signedMoney(pnl)} />}
        >
          <div className="p-4">
            <div className="rounded-md border border-zinc-800 bg-zinc-950/80 px-3 py-4">
              <Chart
                data={equityPoints}
                labels={data?.equity?.labels}
                color={pnl >= 0 ? "#34d399" : "#fb7185"}
                formatValue={(value) => money(value, 2)}
                maxWidth="none"
                aspectRatio="16 / 5"
                preserveAspectRatio="none"
                showGrid
                showZeroLine
              />
            </div>
          </div>
        </Panel>
        <Panel
          title="Drawdown"
          sub="distance from running equity peak"
          right={<StatusPill ok={drawdown === 0} label={money(drawdown)} />}
        >
          <div className="p-4">
            <div className="rounded-md border border-zinc-800 bg-zinc-950/80 px-3 py-4">
              <Chart
                data={drawdownPoints}
                color="#fb7185"
                formatValue={(value) => signedMoney(value, 2)}
                maxWidth="none"
                aspectRatio="16 / 7"
                preserveAspectRatio="none"
                showGrid
                showZeroLine
              />
            </div>
          </div>
        </Panel>
      </div>

      <div className="grid min-w-0 gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(320px,0.55fr)]">
        <WindowPanel windows={data?.windows} />
        <PnlCompositionPanel data={data?.pnl_composition} />
      </div>

      <div className="grid min-w-0 gap-5 xl:grid-cols-2">
        <BreakdownTable title="Price Tier Breakdown" sub="maker/taker/last-chance execution quality" rows={data?.breakdowns.price_tier} />
        <BreakdownTable title="Direction Breakdown" sub="UP vs DOWN realized performance" rows={data?.breakdowns.direction} />
      </div>

      <div className="grid min-w-0 gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(320px,0.65fr)]">
        <DailyPnlPanel rows={data?.daily} />
        <HypotheticalNoFillPanel data={data?.hypothetical_no_fill} />
      </div>

      <div className="grid min-w-0 gap-5 xl:grid-cols-2">
        <BreakdownTable title="Entry Price Breakdown" sub="actual filled entry price buckets" rows={data?.breakdowns.price} />
        <BreakdownTable title="Settlement Source Breakdown" sub="source used to write outcome and PnL" rows={data?.breakdowns.settlement_source} />
      </div>
    </div>
  );
}
