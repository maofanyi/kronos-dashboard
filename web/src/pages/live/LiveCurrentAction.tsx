import { Clock3, ListChecks, RadioTower } from "lucide-react";

export type CurrentDecision = {
  action: string;
  side: string;
  market: string;
  settleAt: string;
  reason: string;
};

export type ExecutionFunnel = {
  submitted: number;
  filled: number;
  noFill: number;
  settled: number;
};

type Props = {
  decision: CurrentDecision;
  openOrders: number;
  funnel: ExecutionFunnel;
};

function compactTime(value: string) {
  const date = new Date(value);
  if (!value || Number.isNaN(date.getTime())) return "--";
  return date.toLocaleString("zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export default function LiveCurrentAction({ decision, openOrders, funnel }: Props) {
  return (
    <section data-testid="live-current-action" className="grid min-w-0 gap-2 border-y border-zinc-800 py-2 sm:gap-3 sm:py-3 lg:grid-cols-[minmax(0,1fr)_auto]">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <RadioTower className="h-4 w-4 text-zinc-500" />
          <h2 className="text-sm font-semibold text-zinc-100">当前动作</h2>
          <span className="rounded border border-zinc-700 px-2 py-0.5 font-mono text-xs text-zinc-300">{decision.action}</span>
          <span className="font-mono text-xs text-zinc-400">{decision.side}</span>
        </div>
        <div className="mt-2 flex min-w-0 flex-wrap gap-x-5 gap-y-1 text-xs text-zinc-500">
          <span className="break-all">市场 {decision.market}</span>
          <span className="inline-flex items-center gap-1"><Clock3 className="h-3.5 w-3.5" />结算 {compactTime(decision.settleAt)}</span>
          <span className="break-words">{decision.reason}</span>
        </div>
        <div className="mt-2 text-xs text-zinc-500">
          {openOrders > 0 ? `当前 ${openOrders} 个 CLOB 挂单` : "当前没有 CLOB 挂单"}
        </div>
      </div>

      <div className="min-w-0 lg:min-w-[330px]">
        <div className="flex items-center gap-2 text-xs font-medium text-zinc-400">
          <ListChecks className="h-4 w-4" />
          <span>执行漏斗</span>
        </div>
        <div className="mt-2 grid grid-cols-4 gap-1 text-center">
          {[
            ["提交", funnel.submitted],
            ["成交", funnel.filled],
            ["未成交", funnel.noFill],
            ["结算", funnel.settled],
          ].map(([label, value]) => (
            <div key={label} className="min-w-0 border-l border-zinc-800 px-1 first:border-l-0">
              <div className="font-mono text-sm font-semibold text-zinc-200">{value}</div>
              <div className="mt-0.5 text-[10px] text-zinc-600">{label}</div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
