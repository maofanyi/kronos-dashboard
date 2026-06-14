import { Activity, BarChart3, GitCompare } from "lucide-react";
import { useState } from "react";
import BTCPriceBar from "./components/BTCPriceBar";
import StatusBar from "./components/StatusBar";
import Backtest from "./pages/Backtest";
import Compare from "./pages/Compare";
import Live, { type TradingTab } from "./pages/Live";

type Tab = TradingTab | "backtest" | "compare";

const tabs: Array<[Tab, string, typeof Activity]> = [
  ["live-real", "Live Real", Activity],
  ["paper-monitor", "Paper Monitor", Activity],
  ["backtest", "Backtest", BarChart3],
  ["compare", "Compare", GitCompare],
];

export default function App() {
  const [tab, setTab] = useState<Tab>("live-real");
  const activeTradingTab = tab === "paper-monitor" ? "paper-monitor" : "live-real";
  const setActiveTradingTab = (next: TradingTab) => setTab(next);

  return (
    <div className="min-h-screen bg-[#090a0f] text-zinc-100">
      <StatusBar />
      <BTCPriceBar />

      <div className="flex border-b border-zinc-800 bg-zinc-950/80 px-4">
        {tabs.map(([key, label, Icon]) => (
          <button
            key={key}
            onClick={() => {
              if (key === "live-real" || key === "paper-monitor") {
                setActiveTradingTab(key);
              } else {
                setTab(key);
              }
            }}
            className={`flex items-center gap-2 border-b-2 px-5 py-3 text-sm font-medium transition-colors ${
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
        {(tab === "live-real" || tab === "paper-monitor") && <Live activeTradingTab={activeTradingTab} />}
        {tab === "backtest" && <Backtest />}
        {tab === "compare" && <Compare />}
      </div>
    </div>
  );
}
