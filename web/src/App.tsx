import { Activity, BarChart3, GitCompareArrows, PieChart } from "lucide-react";
import { useState } from "react";
import BTCPriceBar from "./components/BTCPriceBar";
import StatusBar from "./components/StatusBar";
import Analytics from "./pages/Analytics";
import Backtest from "./pages/Backtest";
import Compare from "./pages/Compare";
import Live, { type TradingTab } from "./pages/Live";

type Tab = "live" | "analytics" | "strategies" | "research";

const tabs: Array<[Tab, string, typeof Activity]> = [
  ["live", "Live", Activity],
  ["analytics", "Analytics", PieChart],
  ["strategies", "Strategies", GitCompareArrows],
  ["research", "Research", BarChart3],
];

export default function App() {
  const [tab, setTab] = useState<Tab>("live");
  const [activeTradingTab, setActiveTradingTab] = useState<TradingTab>("live-real");

  return (
    <div className="min-h-screen bg-[#090a0f] text-zinc-100">
      <StatusBar />
      <BTCPriceBar />

      <div className="flex overflow-x-auto border-b border-zinc-800 bg-zinc-950/80 px-4">
        {tabs.map(([key, label, Icon]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`flex shrink-0 items-center gap-2 border-b-2 px-5 py-3 text-sm font-medium transition-colors ${
              tab === key
                ? "border-emerald-400 text-emerald-300"
                : "border-transparent text-zinc-500 hover:text-zinc-200"
            }`}
          >
            <Icon className="h-4 w-4" />
            {label}
          </button>
        ))}
      </div>

      <div className="p-4 lg:p-5">
        {tab === "live" && <Live activeTradingTab={activeTradingTab} onTradingTabChange={setActiveTradingTab} />}
        {tab === "analytics" && <Analytics />}
        {tab === "strategies" && <Compare />}
        {tab === "research" && <Backtest />}
      </div>
    </div>
  );
}
