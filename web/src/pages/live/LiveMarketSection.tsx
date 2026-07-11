import BTCMarketChart from "../../components/BTCMarketChart";
import { Chart } from "@/components/chart";

export type EquityView = {
  points: number[];
  pnlUsdc: number;
  settled: number;
  source: string;
};

type WeeklyDay = {
  date: string;
  pnl_usdc: number;
  settled: number;
  wins: number;
  losses: number;
};

export type WeeklyCalendar = {
  start_date: string;
  end_date: string;
  days: WeeklyDay[];
  total_pnl_usdc: number;
  settled: number;
  wins: number;
  losses: number;
  win_rate: number;
  day_tz?: string;
};

type Props = {
  equity: EquityView;
  weeklyCalendar?: WeeklyCalendar | null;
};

function signedMoney(value: number) {
  return `${value > 0 ? "+" : ""}${value.toFixed(2)} USDC`;
}

function maxDrawdown(points: number[]) {
  if (points.length === 0) return 0;
  let peak = points[0];
  let drawdown = 0;
  for (const point of points) {
    peak = Math.max(peak, point);
    drawdown = Math.max(drawdown, peak - point);
  }
  return drawdown;
}

function shortDay(value: string) {
  if (!value) return "--";
  const date = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric", timeZone: "UTC" });
}

function EquityChart({ equity }: { equity: EquityView }) {
  const points = equity.points.length ? equity.points : [0];
  const current = points[points.length - 1];
  const drawdown = maxDrawdown(points);
  const labels = points.map((_, index) => `${index}`);
  const pnlTone = equity.pnlUsdc >= 0 ? "text-emerald-300" : "text-rose-300";

  return (
    <section data-testid="live-equity-chart" className="min-w-0 self-start overflow-hidden rounded-md border border-zinc-800 bg-zinc-950/70">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-zinc-800 px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-zinc-100">账户权益走势</h2>
          <p className="mt-1 text-xs text-zinc-500">{equity.source} · 结算序列</p>
        </div>
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-right text-xs">
          <div><div className="text-zinc-600">当前权益</div><div className="mt-0.5 font-mono text-zinc-200">{current.toFixed(2)}</div></div>
          <div><div className="text-zinc-600">总 PnL</div><div className={`mt-0.5 font-mono ${pnlTone}`}>{signedMoney(equity.pnlUsdc)}</div></div>
          <div><div className="text-zinc-600">最大回撤</div><div className={`mt-0.5 font-mono ${drawdown > 0 ? "text-amber-300" : "text-zinc-300"}`}>-{drawdown.toFixed(2)}</div></div>
        </div>
      </header>
      <div className="h-[230px] min-w-0 px-3 py-4 md:h-[300px]">
        <Chart
          data={points}
          labels={labels}
          name="账户权益"
          color={equity.pnlUsdc >= 0 ? "#34d399" : "#fb7185"}
          formatValue={(value) => `${value.toFixed(2)} USDC`}
          maxWidth="none"
          aspectRatio="16 / 8"
          preserveAspectRatio="none"
          showGrid
          showXAxis={points.length > 1}
          tickCount={5}
        />
      </div>
      <div className="border-t border-zinc-900 px-4 py-2 text-xs text-zinc-600">共 {equity.settled} 笔已结算订单</div>
    </section>
  );
}

function WeeklyPnlStrip({ calendar }: { calendar: WeeklyCalendar }) {
  const maxAbsPnl = Math.max(0, ...calendar.days.map((day) => Math.abs(day.pnl_usdc)));
  return (
    <section className="min-w-0 border-y border-zinc-800 py-3" aria-label="近七日收益">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3 px-1">
        <div>
          <h2 className="text-sm font-semibold text-zinc-100">近七日收益</h2>
          <p className="mt-1 text-xs text-zinc-500">{calendar.day_tz || "交易时区"} · {calendar.settled} 笔结算 · {calendar.wins}胜/{calendar.losses}负</p>
        </div>
        <div className={`font-mono text-sm font-semibold ${calendar.total_pnl_usdc >= 0 ? "text-emerald-300" : "text-rose-300"}`}>{signedMoney(calendar.total_pnl_usdc)}</div>
      </div>
      <div className="grid grid-cols-7 gap-1.5">
        {calendar.days.map((day) => {
          const height = day.settled ? Math.max(8, (Math.abs(day.pnl_usdc) / Math.max(maxAbsPnl, 1)) * 48) : 2;
          const tone = day.pnl_usdc > 0 ? "bg-emerald-400/70" : day.pnl_usdc < 0 ? "bg-rose-400/70" : "bg-zinc-700";
          return (
            <div key={day.date} className="min-w-0 text-center" title={`${day.date} ${signedMoney(day.pnl_usdc)} ${day.wins}胜/${day.losses}负`}>
              <div className="flex h-12 items-end justify-center"><div className={`w-full max-w-8 rounded-sm ${tone}`} style={{ height }} /></div>
              <div className={`mt-1 break-words font-mono text-[9px] leading-tight sm:text-[10px] ${day.pnl_usdc > 0 ? "text-emerald-300" : day.pnl_usdc < 0 ? "text-rose-300" : "text-zinc-600"}`}>
                {day.settled ? signedMoney(day.pnl_usdc).replace(" USDC", "") : "--"}
              </div>
              <div className="mt-0.5 text-[9px] text-zinc-600">{shortDay(day.date)}</div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

export default function LiveMarketSection({ equity, weeklyCalendar }: Props) {
  return (
    <div className="min-w-0 space-y-3">
      <div className="grid min-w-0 gap-3 xl:grid-cols-[minmax(0,1.15fr)_minmax(340px,0.85fr)]">
        <BTCMarketChart />
        <EquityChart equity={equity} />
      </div>
      {weeklyCalendar && <WeeklyPnlStrip calendar={weeklyCalendar} />}
    </div>
  );
}
