import { useEffect, useMemo, useState } from "react";
import { Chart } from "@/components/chart";
import { usePolling } from "../hooks/usePolling";

interface FeatureFile {
  path: string;
  name: string;
  size: number;
  mtime: string;
  rows: number;
}

interface FeatureRow {
  ts: string;
  close: number;
  settle_ts: string;
  settle_close: number;
  actual_up: boolean;
  p5_up: number;
  p1_up: number;
  p4_up: number;
  long_score: number;
  short_score: number;
  action: string;
  passed: boolean;
  candidate_won: boolean | null;
}

interface FeatureSummary {
  file: string;
  rows: number;
  start_ts: string | null;
  end_ts: string | null;
  actual_up_rate: number | null;
  avg_p5_up: number | null;
  avg_p1_up: number | null;
  avg_p4_up: number | null;
  decision_stats: {
    passed: number;
    blocked: number;
    pass_rate: number;
    candidate_win_rate: number;
    buy_up: number;
    buy_down: number;
  };
  preview: FeatureRow[];
}

const card = "rounded-md border border-zinc-800 bg-zinc-950/70 p-4";

const percent = (value?: number | null, digits = 1) =>
  value == null ? "-" : `${(value * 100).toFixed(digits)}%`;

const localTime = (value?: string | null) => {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
};

const fileSize = (bytes: number) => {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
};

function StatCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className={card}>
      <div className="text-xs uppercase tracking-[0.14em] text-zinc-500">{label}</div>
      <div className="mt-2 font-mono text-2xl font-semibold text-zinc-100">{value}</div>
      {sub && <div className="mt-1 truncate text-xs text-zinc-500">{sub}</div>}
    </div>
  );
}

export default function Backtest() {
  const [selected, setSelected] = useState("");
  const { data: files } = usePolling<FeatureFile[]>("/api/backtest/feature-files", 10000);
  const selectedFile = selected || files?.[0]?.path || "";
  const { data: summary } = usePolling<FeatureSummary>(
    `/api/backtest/feature-summary?file=${encodeURIComponent(selectedFile)}&limit=180`,
    10000,
  );

  useEffect(() => {
    if (!selected && files?.[0]?.path) setSelected(files[0].path);
  }, [files, selected]);

  const preview = summary?.preview ?? [];
  const p5Series = useMemo(() => preview.map((row) => row.p5_up * 100), [preview]);
  const labels = useMemo(() => preview.map((row) => localTime(row.ts)), [preview]);
  const passedRows = preview.filter((row) => row.passed);

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 rounded-md border border-zinc-800 bg-zinc-950/70 p-4 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <h2 className="text-sm font-semibold text-zinc-100">Feature CSV</h2>
          <p className="mt-1 text-xs text-zinc-500">aligned-prod shift=1 feature data and current-parameter candidate replay</p>
        </div>
        <select
          value={selectedFile}
          onChange={(event) => setSelected(event.target.value)}
          className="w-full rounded border border-zinc-800 bg-black px-3 py-2 font-mono text-xs text-zinc-200 outline-none lg:w-[560px]"
        >
          {(files ?? []).map((file) => (
            <option key={file.path} value={file.path}>
              {file.path} · {file.rows.toLocaleString()} rows · {fileSize(file.size)}
            </option>
          ))}
        </select>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-6">
        <StatCard label="Rows" value={(summary?.rows ?? 0).toLocaleString()} sub={`${localTime(summary?.start_ts)} -> ${localTime(summary?.end_ts)}`} />
        <StatCard label="Actual Up" value={percent(summary?.actual_up_rate)} sub="settle close > entry close" />
        <StatCard label="Avg 5m" value={percent(summary?.avg_p5_up)} sub={`1h ${percent(summary?.avg_p1_up)} / 4h ${percent(summary?.avg_p4_up)}`} />
        <StatCard label="Candidates" value={`${summary?.decision_stats.passed ?? 0}/${summary?.rows ?? 0}`} sub={`${percent(summary?.decision_stats.pass_rate)} pass rate`} />
        <StatCard label="Candidate WR" value={percent(summary?.decision_stats.candidate_win_rate)} sub="using current params" />
        <StatCard label="Direction" value={`${summary?.decision_stats.buy_up ?? 0}/${summary?.decision_stats.buy_down ?? 0}`} sub="BUY_UP / BUY_DOWN" />
      </div>

      <div className="grid min-w-0 gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(360px,0.55fr)]">
        <section className="rounded-md border border-zinc-800 bg-zinc-950/70">
          <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
            <div>
              <h2 className="text-sm font-semibold text-zinc-100">5m Probability</h2>
              <p className="mt-0.5 text-xs text-zinc-500">latest {preview.length} feature rows</p>
            </div>
            <span className="font-mono text-xs text-zinc-500">{selectedFile || "-"}</span>
          </div>
          <div className="h-[300px] p-4">
            {p5Series.length ? (
              <Chart
                data={p5Series}
                labels={labels}
                color="#34d399"
                name="p5_up"
                formatValue={(value) => `${value.toFixed(1)}%`}
              />
            ) : (
              <div className="flex h-full items-center justify-center text-sm text-zinc-500">No feature rows</div>
            )}
          </div>
        </section>

        <section className="rounded-md border border-zinc-800 bg-zinc-950/70">
          <div className="border-b border-zinc-800 px-4 py-3">
            <h2 className="text-sm font-semibold text-zinc-100">Candidate Snapshot</h2>
            <p className="mt-0.5 text-xs text-zinc-500">rows passing the current aligned-prod params in preview window</p>
          </div>
          <div className="max-h-[300px] divide-y divide-zinc-900 overflow-auto">
            {passedRows.length === 0 ? (
              <div className="px-4 py-8 text-center text-sm text-zinc-500">No passed candidates in preview window</div>
            ) : (
              passedRows.slice(-12).reverse().map((row) => (
                <div key={`${row.ts}-${row.action}`} className="px-4 py-3">
                  <div className="flex items-center justify-between gap-3">
                    <span className={`font-mono text-xs font-semibold ${row.action === "BUY_UP" ? "text-emerald-300" : "text-sky-300"}`}>{row.action}</span>
                    <span className={row.candidate_won ? "text-xs text-emerald-300" : "text-xs text-rose-300"}>
                      {row.candidate_won ? "WIN" : "LOSS"}
                    </span>
                  </div>
                  <div className="mt-1 font-mono text-xs text-zinc-500">{localTime(row.ts)}{" -> "}{localTime(row.settle_ts)}</div>
                  <div className="mt-2 grid grid-cols-3 gap-2 text-xs">
                    <span className="rounded border border-zinc-900 bg-black/20 px-2 py-1 text-zinc-300">5m {percent(row.p5_up)}</span>
                    <span className="rounded border border-zinc-900 bg-black/20 px-2 py-1 text-zinc-300">1h {percent(row.p1_up)}</span>
                    <span className="rounded border border-zinc-900 bg-black/20 px-2 py-1 text-zinc-300">4h {percent(row.p4_up)}</span>
                  </div>
                </div>
              ))
            )}
          </div>
        </section>
      </div>

      <section className="overflow-hidden rounded-md border border-zinc-800 bg-zinc-950/70">
        <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
          <div>
            <h2 className="text-sm font-semibold text-zinc-100">Feature Rows</h2>
            <p className="mt-0.5 text-xs text-zinc-500">latest rows from selected CSV</p>
          </div>
          <span className="font-mono text-xs text-zinc-500">{preview.length} rows</span>
        </div>
        <div className="max-h-[520px] overflow-auto">
          <table className="w-full min-w-[980px] table-fixed text-xs tabular-nums">
            <thead className="sticky top-0 z-10 border-b border-zinc-800 bg-zinc-950/95 text-zinc-500">
              <tr>
                <th className="px-4 py-2 text-left">ts</th>
                <th className="px-3 py-2 text-right">close</th>
                <th className="px-3 py-2 text-right">5m</th>
                <th className="px-3 py-2 text-right">1h</th>
                <th className="px-3 py-2 text-right">4h</th>
                <th className="px-3 py-2 text-right">L score</th>
                <th className="px-3 py-2 text-right">S score</th>
                <th className="px-3 py-2 text-left">action</th>
                <th className="px-3 py-2 text-left">actual</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-900">
              {preview.slice().reverse().map((row) => (
                <tr key={`${row.ts}-${row.settle_ts}`} className="hover:bg-zinc-900/50">
                  <td className="px-4 py-2 font-mono text-zinc-300">{localTime(row.ts)}</td>
                  <td className="px-3 py-2 text-right font-mono text-zinc-400">{row.close?.toFixed(2) ?? "-"}</td>
                  <td className="px-3 py-2 text-right font-mono text-zinc-300">{percent(row.p5_up)}</td>
                  <td className="px-3 py-2 text-right font-mono text-zinc-300">{percent(row.p1_up)}</td>
                  <td className="px-3 py-2 text-right font-mono text-zinc-300">{percent(row.p4_up)}</td>
                  <td className="px-3 py-2 text-right font-mono text-zinc-400">{row.long_score?.toFixed(1) ?? "-"}</td>
                  <td className="px-3 py-2 text-right font-mono text-zinc-400">{row.short_score?.toFixed(1) ?? "-"}</td>
                  <td className={`px-3 py-2 font-mono ${row.passed ? "text-emerald-300" : "text-zinc-500"}`}>{row.action}</td>
                  <td className="px-3 py-2 text-zinc-400">{row.actual_up ? "UP" : "DOWN"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
