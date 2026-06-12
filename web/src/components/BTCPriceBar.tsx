import { Bitcoin, Loader2 } from "lucide-react";
import { usePolling } from "../hooks/usePolling";

interface BTCLivePrice {
  price?: number | null;
  timestamp?: string | null;
  source?: string;
  status?: string;
  error?: string | null;
}

const money = (value?: number | null) =>
  value == null
    ? "-"
    : `$${value.toLocaleString(undefined, {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })}`;

const priceAgeLabel = (timestamp?: string | null) => {
  if (!timestamp) return "-";
  const ts = new Date(timestamp).getTime();
  if (Number.isNaN(ts)) return "-";
  const seconds = Math.max(0, Math.round((Date.now() - ts) / 1000));
  if (seconds < 90) return `${seconds}s`;
  return `${Math.round(seconds / 60)}m`;
};

const sourceLabel = (source?: string) => (source ? source.replace(/_/g, " ") : "chainlink live");

const statusTone = (status?: string, hasPrice?: boolean) => {
  if (status === "fresh" && hasPrice) return "text-emerald-300";
  if (status === "stale") return "text-amber-300";
  if (status === "error") return "text-rose-300";
  return "text-zinc-500";
};

export default function BTCPriceBar() {
  const { data, error, loading } = usePolling<BTCLivePrice>("/api/btc/live-price", 5000);
  const hasPrice = data?.price != null;
  const status = error ? "error" : data?.status;
  const tone = statusTone(status, hasPrice);

  return (
    <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 border-b border-zinc-800 bg-zinc-950/70 px-4 py-2 text-xs">
      <span className="flex items-center gap-1.5 font-medium text-zinc-500">
        <Bitcoin className="h-3.5 w-3.5 text-amber-300" />
        BTC/USD
      </span>
      {loading && !data ? (
        <span className="flex items-center gap-1.5 text-zinc-500">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          Loading live price
        </span>
      ) : error ? (
        <span className="text-rose-300">Live price unavailable</span>
      ) : (
        <>
          <span className="font-mono font-semibold text-zinc-100">{money(data?.price)}</span>
          <span className={`font-mono uppercase ${tone}`}>{data?.status || "warming"}</span>
          <span className="truncate text-zinc-500">{sourceLabel(data?.source)}</span>
          <span className="font-mono text-zinc-500">age {priceAgeLabel(data?.timestamp)}</span>
        </>
      )}
      {data?.error && <span className="truncate text-rose-300">{data.error}</span>}
    </div>
  );
}
