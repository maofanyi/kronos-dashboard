import { Activity, Bitcoin, ChevronLeft, ChevronRight, Clock3, History, RadioTower, Target } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { usePolling } from "../hooks/usePolling";

type Candle = {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
};

type Tick = {
  timestamp: string;
  price: number;
  source: string;
};

type MarketItem = {
  key: string;
  slug: string;
  label: string;
  start_ts: string;
  end_ts: string;
  target_price: number | null;
  settle_price: number | null;
  result: string;
};

type MarketChartPayload = {
  readonly: boolean;
  source: string;
  live_source: string;
  live_status: string;
  live_error?: string | null;
  symbol: string;
  resolution: string;
  now: string;
  market: {
    slug: string;
    label: string;
    start_ts: string;
    end_ts: string;
    previous_start_ts: string;
    next_start_ts: string;
  };
  markets: MarketItem[];
  target_price: number | null;
  target_source: string;
  current_price: number | null;
  current_price_ts: string | null;
  delta: number | null;
  delta_pct: number | null;
  candles: Candle[];
  ticks: Tick[];
  history: MarketItem[];
  error?: string;
};

type ChartPoint = {
  x: number;
  y: number;
  price: number;
  timestamp: string;
};

const money = new Intl.NumberFormat(undefined, {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const compactMoney = (value?: number | null) => (value == null ? "-" : money.format(value));

const signedMoney = (value?: number | null) => {
  if (value == null) return "-";
  const sign = value > 0 ? "+" : value < 0 ? "" : "";
  return `${sign}${money.format(value)}`;
};

const localTime = (value?: string | null) => {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
};

const shortTime = (value?: string | null) => {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
};

const windowLabel = (start?: string | null, end?: string | null) => `${shortTime(start)}-${shortTime(end)}`;

const formatCountdown = (endTs?: string) => {
  if (!endTs) return "00:00";
  const remaining = Math.max(0, new Date(endTs).getTime() - Date.now());
  const seconds = Math.floor(remaining / 1000);
  const mm = String(Math.floor(seconds / 60)).padStart(2, "0");
  const ss = String(seconds % 60).padStart(2, "0");
  return `${mm}:${ss}`;
};

const resultClass = (result: string) => {
  if (result === "UP") return "border-emerald-500/25 bg-emerald-500/10 text-emerald-300";
  if (result === "DOWN") return "border-rose-500/25 bg-rose-500/10 text-rose-300";
  if (result === "FLAT") return "border-zinc-500/25 bg-zinc-500/10 text-zinc-300";
  return "border-amber-500/25 bg-amber-500/10 text-amber-300";
};

function smoothPath(points: ChartPoint[]) {
  if (points.length === 0) return "";
  if (points.length === 1) return `M ${points[0].x.toFixed(1)} ${points[0].y.toFixed(1)}`;
  if (points.length === 2) {
    return points.map((point, index) => `${index === 0 ? "M" : "L"} ${point.x.toFixed(1)} ${point.y.toFixed(1)}`).join(" ");
  }
  const commands = [`M ${points[0].x.toFixed(1)} ${points[0].y.toFixed(1)}`];
  for (let index = 0; index < points.length - 1; index += 1) {
    const previous = points[Math.max(0, index - 1)];
    const current = points[index];
    const next = points[index + 1];
    const after = points[Math.min(points.length - 1, index + 2)];
    const controlOneX = current.x + (next.x - previous.x) / 6;
    const controlOneY = current.y + (next.y - previous.y) / 6;
    const controlTwoX = next.x - (after.x - current.x) / 6;
    const controlTwoY = next.y - (after.y - current.y) / 6;
    commands.push(
      `C ${controlOneX.toFixed(1)} ${controlOneY.toFixed(1)}, ${controlTwoX.toFixed(1)} ${controlTwoY.toFixed(1)}, ${next.x.toFixed(1)} ${next.y.toFixed(1)}`
    );
  }
  return commands.join(" ");
}

function useCountdown(endTs?: string) {
  const [label, setLabel] = useState(() => formatCountdown(endTs));

  useEffect(() => {
    setLabel(formatCountdown(endTs));
    const timer = window.setInterval(() => setLabel(formatCountdown(endTs)), 1000);
    return () => window.clearInterval(timer);
  }, [endTs]);

  return label;
}

function useChartGeometry(data?: MarketChartPayload | null) {
  return useMemo(() => {
    const candles = data?.candles ?? [];
    const ticks = data?.ticks ?? [];
    const liveSeries = ticks.map((item) => ({
      timestamp: item.timestamp,
      price: item.price,
    }));
    const candleSeries = candles.slice(-24).map((item) => ({
      timestamp: item.timestamp,
      price: item.close,
    }));
    const series = liveSeries.length >= 2 ? liveSeries : candleSeries;
    const prices = series.map((item) => item.price);
    if (liveSeries.length < 2) {
      prices.push(...candles.slice(-24).flatMap((item) => [item.high, item.low, item.close]));
    }
    if (data?.target_price != null) prices.push(data.target_price);
    if (data?.current_price != null) prices.push(data.current_price);
    if (prices.length === 0) {
      return { points: [] as ChartPoint[], path: "", areaPath: "", targetY: null as number | null, min: 0, max: 1 };
    }

    const minPrice = Math.min(...prices);
    const maxPrice = Math.max(...prices);
    const padding = Math.max((maxPrice - minPrice) * 0.2, 4);
    const min = minPrice - padding;
    const max = maxPrice + padding;
    const width = 1000;
    const height = 320;
    const left = 28;
    const right = 70;
    const top = 18;
    const bottom = 42;
    const innerWidth = width - left - right;
    const innerHeight = height - top - bottom;
    const yFor = (price: number) => top + ((max - price) / (max - min)) * innerHeight;
    const marketStart = data?.market.start_ts ? new Date(data.market.start_ts).getTime() : NaN;
    const marketEnd = data?.market.end_ts ? new Date(data.market.end_ts).getTime() : NaN;
    const useMarketScale = Number.isFinite(marketStart) && Number.isFinite(marketEnd) && marketEnd > marketStart && liveSeries.length >= 2;
    const points = series.map((item, index) => {
      const ts = new Date(item.timestamp).getTime();
      const progress = useMarketScale && Number.isFinite(ts)
        ? Math.min(1, Math.max(0, (ts - marketStart) / (marketEnd - marketStart)))
        : series.length <= 1 ? 1 : index / (series.length - 1);
      return {
        x: left + progress * innerWidth,
        y: yFor(item.price),
        price: item.price,
        timestamp: item.timestamp,
      };
    });
    const path = smoothPath(points);
    const areaPath = path ? `${path} L ${points[points.length - 1].x.toFixed(1)} 278 L ${points[0].x.toFixed(1)} 278 Z` : "";
    const targetY = data?.target_price == null ? null : yFor(data.target_price);
    return { points, path, areaPath, targetY, min, max };
  }, [data]);
}

function Metric({
  label,
  value,
  tone = "text-zinc-100",
  valueKey,
  animated = false,
}: {
  label: string;
  value: string;
  tone?: string;
  valueKey?: string;
  animated?: boolean;
}) {
  return (
    <div className="min-w-[150px]">
      <div className="text-xs font-semibold uppercase tracking-[0.14em] text-zinc-500">{label}</div>
      <div key={valueKey} className={`mt-1 font-mono text-xl font-semibold sm:text-2xl ${tone} ${animated ? "chainlink-price-flash" : ""}`}>{value}</div>
    </div>
  );
}

function ResultPill({ result }: { result: string }) {
  return <span className={`inline-flex min-w-16 items-center justify-center rounded border px-2 py-1 text-xs font-semibold ${resultClass(result)}`}>{result}</span>;
}

export default function BTCMarketChart() {
  const [selectedStart, setSelectedStart] = useState<string | null>(null);
  const url = selectedStart
    ? `/api/btc/market-chart?start_ts=${encodeURIComponent(selectedStart)}`
    : "/api/btc/market-chart";
  const { data, error, loading } = usePolling<MarketChartPayload>(url, selectedStart ? 15000 : 1000);
  const countdown = useCountdown(data?.market.end_ts);
  const geometry = useChartGeometry(data);
  const latestPoint = geometry.points[geometry.points.length - 1];
  const deltaTone = (data?.delta ?? 0) >= 0 ? "text-emerald-300" : "text-rose-300";
  const isCurrentWindow = data ? new Date(data.market.start_ts).getTime() <= Date.now() && Date.now() < new Date(data.market.end_ts).getTime() : false;
  const statusLabel = isCurrentWindow ? countdown : new Date(data?.market.end_ts ?? 0).getTime() <= Date.now() ? "settled" : "upcoming";
  const targetIsExact = data?.target_source === "exact";
  const chartAnimationKey = `${data?.market.slug ?? "empty"}-${data?.current_price_ts ?? "none"}-${data?.current_price ?? "none"}`;
  const liveIsFresh = data?.live_status === "fresh";
  const liveSourceLabel = data?.live_source === "polymarket_rtds_chainlink"
    ? "Polymarket RTDS Chainlink"
    : data?.live_source === "chainlink_streams_ws"
      ? "Chainlink Streams"
      : "Chainlink Candlestick";
  const liveStatusLabel = liveIsFresh ? "Streaming" : "Candlestick fallback";

  useEffect(() => {
    if (!selectedStart || !data) return;
    const start = new Date(data.market.start_ts).getTime();
    const end = new Date(data.market.end_ts).getTime();
    if (data.market.start_ts === selectedStart && start <= Date.now() && Date.now() < end) {
      setSelectedStart(null);
    }
  }, [data, selectedStart]);

  return (
    <section className="mx-auto w-full max-w-[1180px] overflow-hidden rounded-md border border-zinc-800 bg-[#11171b]">
      <div className="flex flex-col gap-3 border-b border-zinc-800 px-4 py-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="flex min-w-0 gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-amber-500 text-white shadow-lg shadow-amber-500/10">
            <Bitcoin className="h-6 w-6" />
          </div>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-lg font-bold text-zinc-50 sm:text-xl">BTC Up or Down 5m</h2>
              <span className="rounded border border-emerald-500/20 bg-emerald-500/10 px-2 py-1 text-xs font-semibold uppercase tracking-[0.14em] text-emerald-300">
                read only
              </span>
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-3 text-sm text-zinc-400">
              <span className="inline-flex items-center gap-1.5">
                <RadioTower className="h-4 w-4 text-zinc-500" />
                Chainlink {data?.symbol ?? "BTCUSD"} {data?.resolution ?? "5m"}
              </span>
              <span className={`inline-flex items-center gap-1.5 rounded border px-2 py-1 text-xs font-semibold ${
                liveIsFresh ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-300" : "border-amber-500/20 bg-amber-500/10 text-amber-300"
              }`}>
                <Activity className="h-3.5 w-3.5" />
                {liveStatusLabel}
              </span>
              <span className="inline-flex items-center gap-1.5">
                <Clock3 className="h-4 w-4 text-zinc-500" />
                {windowLabel(data?.market.start_ts, data?.market.end_ts)}
              </span>
              <span className="font-mono text-xs text-zinc-600">{liveSourceLabel}</span>
            </div>
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-5">
          <div className="text-right">
            <div className="font-mono text-2xl font-bold text-rose-400 sm:text-3xl">{statusLabel}</div>
            <div className="mt-1 text-xs font-semibold uppercase tracking-[0.14em] text-zinc-500">market clock</div>
          </div>
        </div>
      </div>

      <div className="grid gap-4 px-4 py-3 lg:grid-cols-[minmax(0,1fr)_220px]">
        <div className="min-w-0 space-y-3">
          <div className="flex flex-wrap gap-6">
            <Metric label={targetIsExact ? "Target price" : "Reference price"} value={compactMoney(data?.target_price)} tone="text-zinc-400" />
            <Metric label="Current price" value={compactMoney(data?.current_price)} tone="text-amber-400" valueKey={chartAnimationKey} animated />
            <Metric label="Difference" value={signedMoney(data?.delta)} tone={deltaTone} valueKey={`delta-${chartAnimationKey}`} animated />
          </div>
          {data && !targetIsExact && (
            <div className="rounded-md border border-amber-500/20 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
              Waiting for the exact Chainlink target candle; showing the latest available close as reference.
            </div>
          )}

          <div className="relative h-[340px] overflow-hidden rounded-md border border-zinc-800 bg-[#11171b]">
            <svg viewBox="0 0 1000 320" className="h-full w-full" preserveAspectRatio="none">
              <defs>
                <linearGradient id="chainlinkLineFill" x1="0" x2="0" y1="0" y2="1">
                  <stop offset="0%" stopColor="#f59e0b" stopOpacity="0.12" />
                  <stop offset="100%" stopColor="#f59e0b" stopOpacity="0" />
                </linearGradient>
              </defs>
              {[60, 125, 190, 255].map((y) => (
                <line key={y} x1="28" x2="930" y1={y} y2={y} stroke="#272f36" strokeWidth="1" />
              ))}
              {geometry.targetY != null && (
                <g>
                  <line x1="28" x2="930" y1={geometry.targetY} y2={geometry.targetY} stroke="#f59e0b" strokeDasharray="6 8" strokeOpacity="0.8" />
                  <rect x="838" y={geometry.targetY - 16} width="92" height="28" rx="8" fill="#64748b" />
                  <text x="884" y={geometry.targetY + 5} textAnchor="middle" fill="#f8fafc" fontSize="14" fontWeight="700">
                    {targetIsExact ? "Target" : "Latest"}
                  </text>
                </g>
              )}
              {geometry.path && (
                <g>
                  <path key={`area-${chartAnimationKey}`} d={geometry.areaPath} fill="url(#chainlinkLineFill)" opacity="0.65" />
                  <path key={`line-${chartAnimationKey}`} className="chainlink-line-draw" d={geometry.path} fill="none" stroke="#ff9900" strokeLinecap="round" strokeLinejoin="round" strokeWidth="3.5" />
                </g>
              )}
              {latestPoint && (
                <g>
                  <line x1={latestPoint.x} x2={latestPoint.x} y1="18" y2="278" stroke="#f59e0b" strokeOpacity="0.08" />
                  <circle className="chainlink-price-pulse" cx={latestPoint.x} cy={latestPoint.y} r="14" fill="#f59e0b" opacity="0.18" />
                  <circle key={`dot-${chartAnimationKey}`} className="chainlink-latest-dot" cx={latestPoint.x} cy={latestPoint.y} r="5.5" fill="#ff9900" />
                </g>
              )}
              <text x="988" y="58" textAnchor="end" fill="#8b98a5" fontSize="16">{compactMoney(geometry.max)}</text>
              <text x="988" y="255" textAnchor="end" fill="#8b98a5" fontSize="16">{compactMoney(geometry.min)}</text>
            </svg>

            {loading && !data && <div className="absolute inset-0 flex items-center justify-center text-sm text-zinc-500">Loading Chainlink chart...</div>}
            {(error || data?.error) && (
              <div className="absolute left-4 top-4 rounded border border-amber-500/20 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
                {data?.error ?? error}
              </div>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              className="inline-flex items-center gap-2 rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm font-semibold text-zinc-300 hover:border-zinc-700 hover:text-zinc-100"
              onClick={() => setSelectedStart(null)}
            >
              <Activity className="h-4 w-4" />
              Live
            </button>
            {(data?.markets ?? []).map((item) => {
              const itemStart = new Date(item.start_ts).getTime();
              const itemEnd = new Date(item.end_ts).getTime();
              const itemIsLiveWindow = itemStart <= Date.now() && Date.now() < itemEnd;
              return (
                <button
                  type="button"
                  key={`${item.key}-${item.start_ts}`}
                  className={`inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm font-semibold transition ${
                    item.start_ts === data?.market.start_ts
                      ? "border-zinc-200 bg-zinc-100 text-zinc-950"
                      : "border-zinc-800 bg-zinc-900 text-zinc-300 hover:border-zinc-700 hover:text-zinc-100"
                  }`}
                  onClick={() => setSelectedStart(itemIsLiveWindow ? null : item.start_ts)}
                >
                  {item.key === "previous" ? <ChevronLeft className="h-4 w-4" /> : item.key === "next" ? <ChevronRight className="h-4 w-4" /> : <Target className="h-4 w-4" />}
                  {shortTime(item.start_ts)}
                  <span className="font-mono text-xs opacity-70">{item.result}</span>
                </button>
              );
            })}
            <span className="ml-auto font-mono text-xs text-zinc-600">Last refresh {localTime(data?.now)}</span>
          </div>
        </div>

        <aside className="min-w-0 rounded-md border border-zinc-800 bg-zinc-950/60">
          <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
            <div className="flex items-center gap-2 text-sm font-semibold text-zinc-100">
              <History className="h-4 w-4 text-zinc-500" />
              Recent results
            </div>
            <span className="font-mono text-xs text-zinc-600">{localTime(data?.current_price_ts)}</span>
          </div>
          <div className="max-h-[420px] divide-y divide-zinc-900 overflow-auto">
            {(data?.history ?? []).length === 0 ? (
              <div className="px-4 py-10 text-center text-sm text-zinc-500">No Chainlink windows yet</div>
            ) : (
              data?.history.map((item) => (
                <button
                  type="button"
                  key={item.slug}
                  className="block w-full px-4 py-3 text-left hover:bg-zinc-900/60"
                  onClick={() => setSelectedStart(item.start_ts)}
                >
                  <div className="flex items-center justify-between gap-3">
                    <span className="font-mono text-sm text-zinc-300">{windowLabel(item.start_ts, item.end_ts)}</span>
                    <ResultPill result={item.result} />
                  </div>
                  <div className="mt-2 grid grid-cols-2 gap-3 text-xs">
                    <div>
                      <div className="text-zinc-600">target</div>
                      <div className="font-mono text-zinc-300">{compactMoney(item.target_price)}</div>
                    </div>
                    <div>
                      <div className="text-zinc-600">settle</div>
                      <div className="font-mono text-zinc-300">{compactMoney(item.settle_price)}</div>
                    </div>
                  </div>
                </button>
              ))
            )}
          </div>
        </aside>
      </div>
    </section>
  );
}
