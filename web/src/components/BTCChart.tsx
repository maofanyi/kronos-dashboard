import { useEffect, useRef, useState } from "react";
import { createChart, ColorType, CandlestickSeries } from "lightweight-charts";

type Timeframe = "5m" | "1h" | "4h";

const TF_LABELS: Record<Timeframe, string> = { "5m": "5分钟", "1h": "1小时", "4h": "4小时" };
const TF_INTERVALS: Record<Timeframe, string> = { "5m": "5", "1h": "60", "4h": "240" };

export default function BTCChart() {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<any>(null);
  const [tf, setTf] = useState<Timeframe>("5m");
  const [price, setPrice] = useState<number | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "#1a1d27" },
        textColor: "#9ca3af",
      },
      grid: {
        vertLines: { color: "#2a2d37" },
        horzLines: { color: "#2a2d37" },
      },
      width: Math.max(260, Math.floor(containerRef.current.getBoundingClientRect().width)),
      height: 300,
      crosshair: { mode: 0 },
      timeScale: {
        borderColor: "#2a2d37",
        timeVisible: true,
      },
      rightPriceScale: {
        borderColor: "#2a2d37",
      },
    });
    chartRef.current = chart;

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#22c55e",
      downColor: "#ef4444",
      borderDownColor: "#ef4444",
      borderUpColor: "#22c55e",
      wickDownColor: "#ef4444",
      wickUpColor: "#22c55e",
    });

    // Load data
    fetch(`/api/btc/klines?tf=${tf}&limit=200`)
      .then((r) => r.json())
      .then((data) => {
        if (Array.isArray(data)) {
          candleSeries.setData(
            data.map((d: any) => ({
              time: (new Date(d.timestamp).getTime() / 1000) as any,
              open: d.open,
              high: d.high,
              low: d.low,
              close: d.close,
            }))
          );
        }
      });

    // Resize handler
    const handleResize = () => {
      if (containerRef.current) {
        const width = Math.max(260, Math.floor(containerRef.current.getBoundingClientRect().width));
        chart.applyOptions({ width });
      }
    };
    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
    };
  }, [tf]);

  // Poll current price
  useEffect(() => {
    const timer = setInterval(async () => {
      try {
        const r = await fetch("/api/btc/price");
        const d = await r.json();
        setPrice(d.price);
      } catch {}
    }, 10000);
    return () => clearInterval(timer);
  }, []);

  return (
    <div className="min-w-0 max-w-full overflow-hidden rounded-lg border border-[#2a2d37] bg-[#1a1d27]">
      <div className="flex items-center justify-between px-4 py-2 border-b border-[#2a2d37]">
        <div className="flex items-center gap-3">
          <span className="text-sm font-medium text-[#e5e7eb]">BTC/USDT</span>
          {price && (
            <span className="text-lg font-bold text-[#e5e7eb] font-mono">
              ${price.toLocaleString(undefined, { minimumFractionDigits: 2 })}
            </span>
          )}
        </div>
        <div className="flex gap-1">
          {(Object.keys(TF_LABELS) as Timeframe[]).map((t) => (
            <button
              key={t}
              onClick={() => setTf(t)}
              className={`px-3 py-1 text-xs rounded transition-colors ${
                tf === t
                  ? "bg-[#3b82f6] text-white"
                  : "bg-[#0f1117] text-[#9ca3af] hover:text-[#e5e7eb]"
              }`}
            >
              {TF_LABELS[t]}
            </button>
          ))}
        </div>
      </div>
      <div ref={containerRef} className="min-w-0 max-w-full overflow-hidden" />
    </div>
  );
}
