import type { LucideIcon } from "lucide-react";

export type CockpitMetric = {
  id: string;
  label: string;
  value: string;
  detail: string;
  tone?: string;
  icon: LucideIcon;
};

type Props = {
  metrics: CockpitMetric[];
  updatedAt?: string | null;
};

function updatedLabel(value?: string | null) {
  if (!value) return "等待首次更新";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "更新时间未知";
  return `更新于 ${date.toLocaleTimeString("zh-CN", { hour12: false })}`;
}

export default function LiveCockpitSummary({ metrics, updatedAt }: Props) {
  return (
    <section data-testid="live-cockpit" aria-label="实盘决策摘要" className="min-w-0">
      <div className="mb-2 flex items-center justify-between gap-3">
        <h2 className="text-sm font-semibold text-zinc-100">实盘驾驶舱</h2>
        <span className="text-xs text-zinc-600">{updatedLabel(updatedAt)}</span>
      </div>
      <div className="grid grid-cols-2 gap-2 lg:grid-cols-3 2xl:grid-cols-6">
        {metrics.map((metric) => {
          const Icon = metric.icon;
          return (
            <article key={metric.id} data-testid="cockpit-metric" className="min-w-0 rounded border border-zinc-800 bg-zinc-950/70 p-3">
              <div className="flex min-w-0 items-center gap-2 text-xs text-zinc-500">
                <Icon className="h-4 w-4 shrink-0" />
                <span className="break-words leading-tight">{metric.label}</span>
              </div>
              <div className={`mt-2 break-words font-mono text-lg font-semibold leading-tight tabular-nums sm:text-xl ${metric.tone || "text-zinc-100"}`}>
                {metric.value}
              </div>
              <div className="mt-1 break-words text-[11px] leading-snug text-zinc-600">{metric.detail}</div>
            </article>
          );
        })}
      </div>
    </section>
  );
}
