import { useState } from "react";
import { usePolling } from "../hooks/usePolling";
import { Chart } from "@/components/chart";

interface CompareData { [s: string]: { events: any[]; trades: any[]; snapshot: any }; }
const COLORS: Record<string, string> = { live: "var(--chart-2)", history: "var(--chart-4)", backtest: "var(--chart-1)" };
const LABELS: Record<string, string> = { live: "实时", history: "历史", backtest: "回测" };
const card = "bg-[#1a1d27] rounded-lg p-4 border border-[#2a2d37]";

export default function Compare() {
  const [sources, setSources] = useState("live,history");
  const { data } = usePolling<CompareData>(`/api/compare?sources=${sources}&limit=200`, 10000);
  const srcList = sources.split(",").filter(Boolean);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <span className="text-sm text-[#9ca3af]">对比:</span>
        {["live", "history", "backtest"].map((s) => (
          <label key={s} className="flex items-center gap-1.5 text-sm cursor-pointer text-[#e5e7eb]">
            <input type="checkbox" checked={sources.includes(s)} onChange={(e) => {
              const cur = sources.split(",").filter(Boolean);
              setSources((e.target.checked ? [...cur, s] : cur.filter((x) => x !== s)).join(","));
            }} className="accent-[#3b82f6]" />
            {LABELS[s]}
          </label>
        ))}
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {srcList.map((src) => {
          const snap = data?.[src]?.snapshot;
          const tr = data?.[src]?.trades?.filter((t: any) => t.won !== -1) ?? [];
          const wins = tr.filter((t: any) => t.won === 1).length;
          const wr = tr.length > 0 ? wins / tr.length : 0;
          return (
            <div key={src} className={card}>
              <div className="text-xs text-[#9ca3af] uppercase">{LABELS[src] ?? src}</div>
              <div className="text-lg font-bold text-[#e5e7eb]">${(snap?.balance ?? 500).toLocaleString()}</div>
              <div className="text-xs text-[#9ca3af] mt-1">{tr.length} 笔 | WR {(wr * 100).toFixed(1)}%</div>
            </div>
          );
        })}
      </div>

      <div className={card}>
        <div className="text-sm font-medium mb-2 text-[#e5e7eb]">权益对比</div>
        {srcList.map((src) => {
          const tr = data?.[src]?.trades?.filter((t: any) => t.won !== -1) ?? [];
          const eq = [500];
          tr.forEach((t: any) => eq.push(eq[eq.length - 1] + (t.pnl ?? 0)));
          return (
            <div key={src} className="mb-3">
              <div className="text-xs text-[#9ca3af] mb-1">{LABELS[src] ?? src}</div>
              <Chart data={eq} color={COLORS[src] ?? "var(--chart-2)"} formatValue={(v) => `$${v.toFixed(0)}`} />
            </div>
          );
        })}
      </div>
    </div>
  );
}
