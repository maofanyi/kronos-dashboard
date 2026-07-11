import { Activity, Bitcoin, ChevronLeft, ChevronRight, Clock3, History, RadioTower, Target, Triangle } from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
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
  chart_status?: "ok" | "degraded";
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

type ChartSeriesPoint = {
  timestamp: string;
  price: number;
};

type ChartRange = {
  min: number;
  max: number;
};

type PriceTick = {
  value: number;
  y: number;
};

const LIVE_CHART_WINDOW_MS = 90_000;
const Y_RANGE_ANIMATION_MS = 420;
const BTC_PRICE_AXIS_MIN_STEP = 50;
const PRICE_TICK_COUNT = 4;
const PRICE_TICK_MAX_COUNT = 7;
const ODOMETER_ANIMATION_MS = 420;
const CHART_WIDTH = 1000;
const CHART_HEIGHT = 320;
const CHART_LEFT = 28;
const CHART_PLOT_RIGHT = 900;
const CHART_PRICE_LABEL_X = 988;
const CHART_TARGET_PILL_X = 878;
const CHART_TARGET_PILL_WIDTH = 82;
const CHART_TARGET_PILL_HEIGHT = 26;
const CHART_TARGET_PILL_NOTCH = 10;
const CHART_TOP = 18;
const CHART_BOTTOM = 42;

const numberFormat = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const axisNumberFormat = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 0,
});

const compactMoney = (value?: number | null) => {
  if (value == null) return "-";
  return `$${numberFormat.format(value)}`;
};

const axisMoney = (value?: number | null) => {
  if (value == null) return "-";
  return `$${axisNumberFormat.format(Math.round(value))}`;
};

const signedMoney = (value?: number | null) => {
  if (value == null) return "-";
  const sign = value > 0 ? "+" : value < 0 ? "-" : "";
  return `${sign}$${numberFormat.format(Math.abs(value))}`;
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
  if (result === "PENDING") return "border-amber-500/25 bg-amber-500/10 text-amber-300";
  if (result === "UPCOMING") return "border-sky-500/25 bg-sky-500/10 text-sky-300";
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

function targetPillPath(centerY: number) {
  const x = CHART_TARGET_PILL_X;
  const y = centerY - CHART_TARGET_PILL_HEIGHT / 2;
  const width = CHART_TARGET_PILL_WIDTH;
  const height = CHART_TARGET_PILL_HEIGHT;
  const notch = CHART_TARGET_PILL_NOTCH;
  const radius = 8;
  return [
    `M ${x + notch} ${y}`,
    `H ${x + width - radius}`,
    `Q ${x + width} ${y} ${x + width} ${y + radius}`,
    `V ${y + height - radius}`,
    `Q ${x + width} ${y + height} ${x + width - radius} ${y + height}`,
    `H ${x + notch}`,
    `L ${x} ${centerY}`,
    "Z",
  ].join(" ");
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

function useLiveChartNow(active: boolean) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!active) {
      setNow(Date.now());
      return;
    }
    let frame = 0;
    const tick = () => {
      setNow(Date.now());
      frame = window.requestAnimationFrame(tick);
    };
    frame = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(frame);
  }, [active]);

  return now;
}

const easeOutCubic = (progress: number) => 1 - (1 - progress) ** 3;

function useAnimatedNumber(value?: number | null, durationMs = ODOMETER_ANIMATION_MS) {
  const [displayValue, setDisplayValue] = useState<number | null>(() => value ?? null);
  const displayValueRef = useRef<number | null>(value ?? null);

  useEffect(() => {
    if (typeof value !== "number" || !Number.isFinite(value)) {
      displayValueRef.current = null;
      setDisplayValue(null);
      return;
    }

    const from = displayValueRef.current ?? value;
    const delta = value - from;
    if (Math.abs(delta) < 0.005) {
      displayValueRef.current = value;
      setDisplayValue(value);
      return;
    }

    const startedAt = window.performance.now();
    let frame = 0;
    const animate = (now: number) => {
      const progress = Math.min(1, (now - startedAt) / durationMs);
      const nextValue = from + delta * easeOutCubic(progress);
      displayValueRef.current = nextValue;
      setDisplayValue(nextValue);
      if (progress < 1) {
        frame = window.requestAnimationFrame(animate);
      } else {
        displayValueRef.current = value;
        setDisplayValue(value);
      }
    };

    frame = window.requestAnimationFrame(animate);
    return () => window.cancelAnimationFrame(frame);
  }, [durationMs, value]);

  return displayValue;
}

function buildPriceAxis(prices: number[]) {
  if (prices.length === 0) {
    return { range: { min: 0, max: 1 }, values: [1, 0] };
  }

  const minPrice = Math.min(...prices);
  const maxPrice = Math.max(...prices);
  const rawSpan = Math.max(maxPrice - minPrice, 1);
  const paddedSpan = Math.max(rawSpan * 1.25, BTC_PRICE_AXIS_MIN_STEP * (PRICE_TICK_COUNT - 1));
  const center = (minPrice + maxPrice) / 2;
  const paddedMin = center - paddedSpan / 2;
  const paddedMax = center + paddedSpan / 2;
  let step = BTC_PRICE_AXIS_MIN_STEP;
  let top = Math.ceil(paddedMax / step) * step;
  let bottom = Math.floor(paddedMin / step) * step;
  let values = Array.from({ length: Math.round((top - bottom) / step) + 1 }, (_, index) => top - index * step);
  while (values.length < PRICE_TICK_COUNT) {
    bottom -= step;
    values = Array.from({ length: Math.round((top - bottom) / step) + 1 }, (_, index) => top - index * step);
  }
  while (values.length > PRICE_TICK_MAX_COUNT) {
    step += BTC_PRICE_AXIS_MIN_STEP;
    top = Math.ceil(paddedMax / step) * step;
    bottom = Math.floor(paddedMin / step) * step;
    values = Array.from({ length: Math.round((top - bottom) / step) + 1 }, (_, index) => top - index * step);
  }
  return {
    range: { min: values[values.length - 1], max: values[0] },
    values,
  };
}

function useAnimatedChartRange(targetRange: ChartRange | null) {
  const [displayRange, setDisplayRange] = useState<ChartRange | null>(targetRange);
  const displayRangeRef = useRef<ChartRange | null>(targetRange);

  useEffect(() => {
    if (!targetRange) {
      displayRangeRef.current = null;
      setDisplayRange(null);
      return;
    }

    const from = displayRangeRef.current ?? targetRange;
    const minDelta = Math.abs(from.min - targetRange.min);
    const maxDelta = Math.abs(from.max - targetRange.max);
    if (minDelta < 0.01 && maxDelta < 0.01) {
      displayRangeRef.current = targetRange;
      setDisplayRange(targetRange);
      return;
    }

    const startedAt = window.performance.now();
    let frame = 0;
    const animate = (now: number) => {
      const progress = Math.min(1, (now - startedAt) / Y_RANGE_ANIMATION_MS);
      const eased = easeOutCubic(progress);
      const nextRange = {
        min: from.min + (targetRange.min - from.min) * eased,
        max: from.max + (targetRange.max - from.max) * eased,
      };
      displayRangeRef.current = nextRange;
      setDisplayRange(nextRange);
      if (progress < 1) {
        frame = window.requestAnimationFrame(animate);
      } else {
        displayRangeRef.current = targetRange;
        setDisplayRange(targetRange);
      }
    };

    frame = window.requestAnimationFrame(animate);
    return () => window.cancelAnimationFrame(frame);
  }, [targetRange?.min, targetRange?.max]);

  return displayRange;
}

function useChartModel(data?: MarketChartPayload | null, animationNowMs?: number) {
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
    const marketStart = data?.market.start_ts ? new Date(data.market.start_ts).getTime() : NaN;
    const marketEnd = data?.market.end_ts ? new Date(data.market.end_ts).getTime() : NaN;
    const wallClockNowMs = Number.isFinite(animationNowMs) ? Number(animationNowMs) : Date.now();
    const chartNowMs = Number.isFinite(marketEnd) ? Math.min(wallClockNowMs, marketEnd) : wallClockNowMs;
    const isUpcomingWindow = Number.isFinite(marketStart) && wallClockNowMs < marketStart;
    const chartStartMs = isUpcomingWindow ? Math.floor(chartNowMs / 300_000) * 300_000 : marketStart;
    const currentPrice = data?.current_price;
    const liveChartActive = Number.isFinite(marketStart)
      && Number.isFinite(marketEnd)
      && ((marketStart <= wallClockNowMs && wallClockNowMs < marketEnd) || isUpcomingWindow)
      && liveSeries.length >= 2
      && typeof currentPrice === "number";
    const elapsedMs = chartNowMs - chartStartMs;
    const liveWindowFilled = elapsedMs >= LIVE_CHART_WINDOW_MS;
    let series: ChartSeriesPoint[] = liveSeries.length >= 2 ? liveSeries : candleSeries;
    if (liveChartActive) {
      const visibleCutoff = liveWindowFilled ? chartNowMs - LIVE_CHART_WINDOW_MS - 2_000 : chartStartMs - 2_000;
      const visibleLiveSeries = liveSeries.filter((item) => {
        const ts = new Date(item.timestamp).getTime();
        return Number.isFinite(ts) && visibleCutoff <= ts && ts <= chartNowMs + 1_000;
      });
      series = visibleLiveSeries.length >= 2 ? visibleLiveSeries : liveSeries.slice(-2);
      series = [
        ...series,
        {
          timestamp: new Date(chartNowMs).toISOString(),
          price: currentPrice,
        },
      ];
    }
    const yScaleSeries = liveChartActive ? liveSeries : series;
    const prices = yScaleSeries.map((item) => item.price);
    if (liveSeries.length < 2) {
      prices.push(...candles.slice(-24).flatMap((item) => [item.high, item.low, item.close]));
    }
    if (data?.current_price != null) prices.push(data.current_price);
    if (prices.length === 0) {
      return {
        candles,
        liveSeries,
        series: [] as ChartSeriesPoint[],
        range: null as ChartRange | null,
        priceTickValues: [] as number[],
        marketStart,
        marketEnd,
        chartStartMs,
        chartNowMs,
        liveChartActive,
        liveWindowFilled,
      };
    }

    const axis = buildPriceAxis(prices);
    return {
      candles,
      liveSeries,
      series,
      range: axis.range,
      priceTickValues: axis.values,
      marketStart,
      marketEnd,
      chartStartMs,
      chartNowMs,
      liveChartActive,
      liveWindowFilled,
    };
  }, [data, animationNowMs]);
}

function useChartGeometry(data?: MarketChartPayload | null, animationNowMs?: number) {
  const model = useChartModel(data, animationNowMs);
  const animatedRange = useAnimatedChartRange(model.range);

  return useMemo(() => {
    if (!model.range || model.series.length === 0) {
      return {
        points: [] as ChartPoint[],
        path: "",
        areaPath: "",
        targetY: null as number | null,
        currentY: null as number | null,
        priceTicks: [] as PriceTick[],
        min: 0,
        max: 1,
      };
    }

    const projection = { range: animatedRange ?? model.range };
    const { min, max } = projection.range;
    const plotBottom = CHART_HEIGHT - CHART_BOTTOM;
    const innerWidth = CHART_PLOT_RIGHT - CHART_LEFT;
    const innerHeight = CHART_HEIGHT - CHART_TOP - CHART_BOTTOM;
    const yFor = (price: number) => CHART_TOP + ((max - price) / (max - min)) * innerHeight;
    const useMarketScale = Number.isFinite(model.marketStart)
      && Number.isFinite(model.marketEnd)
      && model.marketEnd > model.marketStart
      && model.liveSeries.length >= 2;
    const { chartNowMs, liveWindowFilled, chartStartMs } = model;
    const points = model.series.map((item, index) => {
      const ts = new Date(item.timestamp).getTime();
      const liveProgress = liveWindowFilled ? 1 - (chartNowMs - ts) / LIVE_CHART_WINDOW_MS : (ts - chartStartMs) / LIVE_CHART_WINDOW_MS;
      const progress = model.liveChartActive && Number.isFinite(ts)
        ? Math.min(1, Math.max(0, liveProgress))
        : useMarketScale && Number.isFinite(ts)
        ? Math.min(1, Math.max(0, (ts - model.marketStart) / (model.marketEnd - model.marketStart)))
        : model.series.length <= 1 ? 1 : index / (model.series.length - 1);
      return {
        x: CHART_LEFT + progress * innerWidth,
        y: yFor(item.price),
        price: item.price,
        timestamp: item.timestamp,
      };
    });
    const path = smoothPath(points);
    const areaPath = path ? `${path} L ${points[points.length - 1].x.toFixed(1)} ${plotBottom} L ${points[0].x.toFixed(1)} ${plotBottom} Z` : "";
    const priceTicks = model.priceTickValues.map((value) => ({ value, y: yFor(value) }));
    const currentY = data?.current_price == null ? null : yFor(data.current_price);
    const targetY = data?.target_price == null ? null : Math.min(plotBottom, Math.max(CHART_TOP, yFor(data.target_price)));
    return { points, path, areaPath, targetY, currentY, priceTicks, min, max };
  }, [animatedRange, data?.current_price, data?.target_price, model]);
}

function Metric({
  label,
  value,
  tone = "text-zinc-100",
}: {
  label: string;
  value: string;
  tone?: string;
}) {
  return (
    <div className="min-w-[250px]">
      <div className="text-xs font-semibold uppercase tracking-[0.14em] text-zinc-500">{label}</div>
      <div className={`mt-1 text-3xl font-semibold sm:text-4xl ${tone}`}>{value}</div>
    </div>
  );
}

function RollingText({ value, className = "" }: { value: string; className?: string }) {
  const chars = Array.from(value);
  return (
    <span className={`chainlink-odometer-value ${className}`} aria-label={value}>
      {chars.map((char, index) => {
        return (
          <span
            key={`${index}-${char}`}
            className={`chainlink-odometer-glyph ${char === ":" ? "chainlink-odometer-separator" : ""}`}
            aria-hidden="true"
          >
            {char}
          </span>
        );
      })}
    </span>
  );
}

function AnimatedMetric({
  label,
  value,
  tone = "text-zinc-100",
  formatValue,
}: {
  label: string;
  value?: number | null;
  tone?: string;
  formatValue: (value?: number | null) => string;
}) {
  const animatedValue = useAnimatedNumber(value);
  const renderedValue = formatValue(animatedValue);

  return (
    <div className="min-w-[150px]">
      <div className="text-xs font-semibold uppercase tracking-[0.14em] text-zinc-500">{label}</div>
      <div className={`mt-1 text-xl font-semibold sm:text-2xl ${tone} chainlink-metric-odometer`}>
        <RollingText value={renderedValue} />
      </div>
    </div>
  );
}

function CurrentPriceMetric({
  price,
  delta,
  showDelta = true,
}: {
  price?: number | null;
  delta?: number | null;
  showDelta?: boolean;
}) {
  const animatedPrice = useAnimatedNumber(price);
  const animatedDelta = useAnimatedNumber(delta);
  const deltaIsUp = (animatedDelta ?? 0) >= 0;

  return (
    <div className="chainlink-current-price-metric">
      <div className="flex items-center gap-4">
        <div className="text-xs font-semibold uppercase tracking-[0.14em] text-amber-400">Current price</div>
        {showDelta && typeof delta === "number" && (
          <div className={`chainlink-current-delta ${deltaIsUp ? "text-emerald-300" : "text-rose-300"}`}>
            <Triangle className={`h-3.5 w-3.5 fill-current ${deltaIsUp ? "" : "rotate-180"}`} />
            <RollingText value={signedMoney(animatedDelta)} />
          </div>
        )}
      </div>
      <div className="mt-1 text-3xl font-semibold text-amber-400 sm:text-4xl chainlink-metric-odometer">
        <RollingText value={compactMoney(animatedPrice)} />
      </div>
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
  const { data, error, loading } = usePolling<MarketChartPayload>(url, selectedStart ? 15000 : 3000);
  const wallClockNowMs = Date.now();
  const marketStartMs = data ? new Date(data.market.start_ts).getTime() : NaN;
  const marketEndMs = data ? new Date(data.market.end_ts).getTime() : NaN;
  const isUpcomingWindow = data ? wallClockNowMs < marketStartMs : false;
  const liveChartActive = Boolean(
    data
    && data.ticks.length >= 2
    && typeof data.current_price === "number"
    && ((marketStartMs <= wallClockNowMs && wallClockNowMs < marketEndMs) || isUpcomingWindow)
  );
  const animationNowMs = useLiveChartNow(liveChartActive);
  const countdown = useCountdown(data?.market.end_ts);
  const geometry = useChartGeometry(data, animationNowMs);
  const latestPoint = geometry.points[geometry.points.length - 1];
  const isCurrentWindow = data ? marketStartMs <= Date.now() && Date.now() < marketEndMs : false;
  const statusLabel = isCurrentWindow ? countdown : new Date(data?.market.end_ts ?? 0).getTime() <= Date.now() ? "settled" : "upcoming";
  const targetIsExact = data?.target_source === "exact";
  const showTargetPrice = !isUpcomingWindow && data?.target_price != null;
  const marketSlug = data?.market.slug ?? "empty";
  const renderedMarketRef = useRef<string | null>(null);
  const marketTabRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const [marketPillStyle, setMarketPillStyle] = useState({ transform: "translateX(0px)", width: "0px" });
  const hasRenderedChart = renderedMarketRef.current === marketSlug;
  const liveIsFresh = data?.live_status === "fresh";
  const chartIsDegraded = data?.chart_status === "degraded";
  const liveSourceLabel = data?.live_source === "polymarket_rtds_chainlink"
    ? "Polymarket RTDS Chainlink"
    : data?.live_source === "chainlink_streams_ws"
      ? "Chainlink Streams"
      : "Chainlink Candlestick";
  const liveStatusLabel = chartIsDegraded ? "Data degraded" : liveIsFresh ? "Streaming" : "Candlestick fallback";

  useEffect(() => {
    if (!selectedStart || !data) return;
    const start = new Date(data.market.start_ts).getTime();
    const end = new Date(data.market.end_ts).getTime();
    if (data.market.start_ts === selectedStart && start <= Date.now() && Date.now() < end) {
      setSelectedStart(null);
    }
  }, [data, selectedStart]);

  useEffect(() => {
    if (!geometry.path || !marketSlug) return;
    const frame = window.requestAnimationFrame(() => {
      renderedMarketRef.current = marketSlug;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [geometry.path, marketSlug]);

  useLayoutEffect(() => {
    const activeKey = data?.market.start_ts;
    if (!activeKey) return;
    const movePill = () => {
      const activeTab = marketTabRefs.current[activeKey];
      if (!activeTab) return;
      setMarketPillStyle({
        transform: `translateX(${activeTab.offsetLeft}px)`,
        width: `${activeTab.offsetWidth}px`,
      });
    };
    movePill();
    window.addEventListener("resize", movePill);
    return () => window.removeEventListener("resize", movePill);
  }, [data?.market.start_ts, data?.markets.length]);

  return (
    <section className="chainlink-market-chart w-full min-w-0 overflow-hidden rounded-md border border-zinc-800 bg-[#11171b]">
      <div className="flex flex-col gap-3 border-b border-zinc-800 px-4 py-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="flex min-w-0 gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-amber-500 text-white shadow-lg shadow-amber-500/10">
            <Bitcoin className="h-6 w-6" />
          </div>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-lg font-bold text-zinc-50">BTC 5分钟市场</h2>
              <span className="rounded border border-emerald-500/20 bg-emerald-500/10 px-2 py-1 text-xs font-semibold text-emerald-300">
                只读
              </span>
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-3 text-sm text-zinc-400">
              <span className="inline-flex items-center gap-1.5">
                <RadioTower className="h-4 w-4 text-zinc-500" />
                Chainlink {data?.symbol ?? "BTCUSD"} {data?.resolution ?? "5m"}
              </span>
              <span className={`inline-flex items-center gap-1.5 rounded border px-2 py-1 text-xs font-semibold ${
                liveIsFresh && !chartIsDegraded ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-300" : "border-amber-500/20 bg-amber-500/10 text-amber-300"
              }`}>
                <Activity className="h-3.5 w-3.5" />
                {liveStatusLabel}
              </span>
              <span className="inline-flex items-center gap-1.5">
                <Clock3 className="h-4 w-4 text-zinc-500" />
                {windowLabel(data?.market.start_ts, data?.market.end_ts)}
              </span>
              <span className="text-xs text-zinc-600">{liveSourceLabel}</span>
            </div>
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-5">
          <div className="text-right">
            <div className="text-2xl font-bold text-rose-400 sm:text-3xl">
              <RollingText value={statusLabel} className="chainlink-clock-odometer" />
            </div>
            <div className="mt-1 text-xs font-semibold text-zinc-500">市场倒计时</div>
          </div>
        </div>
      </div>

      <div className="min-w-0 space-y-3 px-4 py-3">
        <div className="min-w-0 space-y-3">
          <div className="flex flex-wrap items-start gap-9">
            {showTargetPrice && (
              <Metric label={targetIsExact ? "Target price" : "Reference price"} value={compactMoney(data?.target_price)} tone="text-zinc-400" />
            )}
            <CurrentPriceMetric price={data?.current_price} delta={data?.delta} showDelta={!isUpcomingWindow} />
          </div>
          {data && showTargetPrice && !targetIsExact && (
            <div className="rounded-md border border-amber-500/20 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
              Waiting for the exact Chainlink target candle; showing the latest available close as reference.
            </div>
          )}

          <div data-testid="btc-market-plot" className="relative h-[230px] overflow-hidden rounded-md border border-zinc-800 bg-[#11171b] md:h-[300px]">
            <svg viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`} className="h-full w-full" preserveAspectRatio="none">
              <defs>
                <linearGradient id="chainlinkLineFill" x1="0" x2="0" y1="0" y2="1">
                  <stop offset="0%" stopColor="#f59e0b" stopOpacity="0.12" />
                  <stop offset="100%" stopColor="#f59e0b" stopOpacity="0" />
                </linearGradient>
              </defs>
              {geometry.priceTicks.map((tick) => (
                <line key={tick.value} x1={CHART_LEFT} x2={CHART_PLOT_RIGHT} y1={tick.y} y2={tick.y} stroke="#272f36" strokeWidth="1" />
              ))}
              {showTargetPrice && geometry.targetY != null && (
                <g>
                  <line className="chainlink-target-line" x1={CHART_LEFT} x2={CHART_PLOT_RIGHT} y1={geometry.targetY} y2={geometry.targetY} />
                  <path className="chainlink-target-pill" d={targetPillPath(geometry.targetY)} />
                  <text x={CHART_TARGET_PILL_X + 39} y={geometry.targetY + 5} textAnchor="middle" fill="#f8fafc" fontSize="14" fontWeight="700">
                    {targetIsExact ? "Target" : "Latest"}
                  </text>
                  <path
                    className="chainlink-target-pill-chevron"
                    d={`M ${CHART_TARGET_PILL_X + 61} ${geometry.targetY - 5} L ${CHART_TARGET_PILL_X + 67} ${geometry.targetY + 1} L ${CHART_TARGET_PILL_X + 73} ${geometry.targetY - 5}`}
                  />
                  <path
                    className="chainlink-target-pill-chevron"
                    d={`M ${CHART_TARGET_PILL_X + 61} ${geometry.targetY + 1} L ${CHART_TARGET_PILL_X + 67} ${geometry.targetY + 7} L ${CHART_TARGET_PILL_X + 73} ${geometry.targetY + 1}`}
                  />
                </g>
              )}
              {geometry.currentY != null && (
                <line className="chainlink-current-line" x1={CHART_LEFT} x2={CHART_PLOT_RIGHT} y1={geometry.currentY} y2={geometry.currentY} />
              )}
              {geometry.path && (
                <g>
                  <path d={geometry.areaPath} className="chainlink-area-live" fill="url(#chainlinkLineFill)" opacity="0.65" />
                  <path
                    key={`line-${marketSlug}`}
                    className={hasRenderedChart ? "chainlink-line-live" : "chainlink-line-draw chainlink-line-live"}
                    d={geometry.path}
                    fill="none"
                    stroke="#ff9900"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth="3.5"
                  />
                </g>
              )}
              {latestPoint && (
                <g>
                  <line x1={latestPoint.x} x2={latestPoint.x} y1={CHART_TOP} y2={CHART_HEIGHT - CHART_BOTTOM} stroke="#f59e0b" strokeOpacity="0.08" />
                  <g className="chainlink-latest-marker" style={{ transform: `translate(${latestPoint.x}px, ${latestPoint.y}px)` }}>
                    <circle className="chainlink-price-pulse" cx="0" cy="0" r="14" fill="#f59e0b" opacity="0.18" />
                    <circle className="chainlink-latest-dot" cx="0" cy="0" r="5.5" fill="#ff9900" />
                  </g>
                </g>
              )}
              {geometry.priceTicks.map((tick) => (
                <text key={`price-${tick.value}`} x={CHART_PRICE_LABEL_X} y={tick.y + 5} textAnchor="end" fill="#8b98a5" fontSize="16">
                  {axisMoney(tick.value)}
                </text>
              ))}
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
            <div className="chainlink-market-switcher t-tabs" role="tablist">
              <span className="t-tabs-pill" style={marketPillStyle} aria-hidden="true" />
              {(data?.markets ?? []).map((item) => {
                const itemStart = new Date(item.start_ts).getTime();
                const itemEnd = new Date(item.end_ts).getTime();
                const itemIsLiveWindow = itemStart <= Date.now() && Date.now() < itemEnd;
                const itemIsSelected = item.start_ts === data?.market.start_ts;
                return (
                  <button
                    type="button"
                    role="tab"
                    aria-selected={itemIsSelected}
                    ref={(node) => {
                      marketTabRefs.current[item.start_ts] = node;
                    }}
                    key={`${item.key}-${item.start_ts}`}
                    className={`t-tab inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm font-semibold ${
                      itemIsSelected
                        ? "border-zinc-500 bg-zinc-800/80 text-zinc-50 shadow-inner shadow-black/20"
                        : "border-zinc-800 bg-zinc-900 text-zinc-300 hover:border-zinc-700 hover:text-zinc-100"
                    }`}
                    onClick={() => setSelectedStart(itemIsLiveWindow ? null : item.start_ts)}
                  >
                    {item.key === "previous" ? <ChevronLeft className="h-4 w-4" /> : item.key === "next" ? <ChevronRight className="h-4 w-4" /> : <Target className="h-4 w-4" />}
                    {shortTime(item.start_ts)}
                    <ResultPill result={item.result} />
                  </button>
                );
              })}
            </div>
            <span className="ml-auto text-xs text-zinc-600">Last refresh {localTime(data?.now)}</span>
          </div>
        </div>

        <div className="min-w-0 border-t border-zinc-800 pt-3 t-panel-slide" data-open={data ? "true" : "false"}>
          <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
            <div className="flex items-center gap-2 text-sm font-semibold text-zinc-100">
              <History className="h-4 w-4 text-zinc-500" />
              最近结算
            </div>
            <span className="text-xs text-zinc-600">{localTime(data?.current_price_ts)}</span>
          </div>
          <div className="flex max-w-full gap-2 overflow-x-auto px-1 py-2">
            {(data?.history ?? []).length === 0 ? (
              <div className="w-full px-4 py-5 text-center text-sm text-zinc-500">暂无 Chainlink 结算窗口</div>
            ) : (
              data?.history.map((item) => (
                <button
                  type="button"
                  key={item.slug}
                  className="min-w-[190px] rounded border border-zinc-800 bg-zinc-950/60 px-3 py-2 text-left hover:border-zinc-700 hover:bg-zinc-900/60"
                  onClick={() => setSelectedStart(item.start_ts)}
                >
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-sm text-zinc-300">{windowLabel(item.start_ts, item.end_ts)}</span>
                    <ResultPill result={item.result} />
                  </div>
                  <div className="mt-1 flex items-center justify-between gap-3 font-mono text-[11px] text-zinc-500">
                    <span>目标 {compactMoney(item.target_price)}</span>
                    <span>结算 {compactMoney(item.settle_price)}</span>
                  </div>
                </button>
              ))
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
