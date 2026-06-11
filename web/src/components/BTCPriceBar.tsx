import { Bitcoin, Loader2 } from "lucide-react";
import { usePolling } from "../hooks/usePolling";

interface BTCPrice {
  price: number;
  symbol: string;
}

export default function BTCPriceBar() {
  const { data, error, loading } = usePolling<BTCPrice>("/api/btc/price", 10000);

  return (
    <div className="flex items-center gap-3 border-b border-zinc-800 bg-zinc-950/70 px-4 py-2 text-xs">
      <span className="flex items-center gap-1.5 font-medium text-zinc-500">
        <Bitcoin className="h-3.5 w-3.5 text-amber-300" />
        BTC/USDT
      </span>
      {loading ? (
        <span className="flex items-center gap-1.5 text-zinc-500">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          加载中
        </span>
      ) : error ? (
        <span className="text-rose-300">价格不可用</span>
      ) : (
        <span className="font-mono font-semibold text-zinc-100">
          ${data?.price?.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) ?? "-"}
        </span>
      )}
    </div>
  );
}
