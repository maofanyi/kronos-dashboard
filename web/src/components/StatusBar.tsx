import { CircleAlert, Radio } from "lucide-react";
import { usePolling } from "../hooks/usePolling";

interface StatusData {
  balance: number;
  trades_count: number;
  wr: number;
  cooldown_left: number;
}

interface SafetyData {
  mode?: string;
  run_source?: string;
  real_orders_enabled?: boolean;
  kill_switch?: { state?: string };
}

export default function StatusBar() {
  const { data, error } = usePolling<StatusData>("/api/status?source=live", 5000);
  const { data: safety } = usePolling<SafetyData>("/api/live-safety", 5000);
  const now = new Date();
  const cooling = (data?.cooldown_left ?? 0) > 0;
  const liveEnabled = safety?.real_orders_enabled === true;
  const needsReview = Boolean(error) || cooling || liveEnabled;

  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-zinc-800 bg-[#090a0f] px-4 py-2 text-xs">
      <div className="flex items-center gap-4">
        <span className="flex items-center gap-1.5 text-zinc-400">
          <span className={`h-2 w-2 rounded-full ${error ? "bg-rose-400" : "animate-pulse bg-emerald-400"}`} />
          <Radio className="h-3.5 w-3.5" />
          {error ? "API 异常" : "同步正常"}
        </span>
        <span className="text-zinc-600">{safety?.run_source ?? "aligned-prod"}</span>
        <span className={liveEnabled ? "text-amber-300" : "text-zinc-500"}>
          {safety?.mode?.toUpperCase() ?? "PAPER"} / {safety?.kill_switch?.state ?? "locked"}
        </span>
      </div>

      <div className="flex items-center gap-4 text-zinc-500">
        <span>更新 {now.toLocaleTimeString("zh-CN", { hour12: false })}</span>
        <span className={`flex items-center gap-1.5 ${needsReview ? "text-amber-300" : "text-emerald-300"}`}>
          {needsReview && <CircleAlert className="h-3.5 w-3.5" />}
          {error ? "需要检查" : cooling ? `冷却 ${data?.cooldown_left} bar` : liveEnabled ? "实盘开关已开启" : "运行正常"}
        </span>
      </div>
    </div>
  );
}
