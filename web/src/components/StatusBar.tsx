import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  CircleAlert,
  Lock,
  Radio,
  ShieldCheck,
  Wallet,
} from "lucide-react";
import { useMemo, useState } from "react";
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
  clob?: {
    authenticated?: boolean;
    account_read_ok?: boolean;
    allowance_read_ok?: boolean;
    open_orders_read_ok?: boolean;
  };
  funding?: {
    balance_ok?: boolean;
    allowance_ok?: boolean;
    balance?: number;
    allowance_count?: number;
    min_allowance?: number;
    min_allowance_spender?: string;
    balance_expected?: string;
    allowance_expected?: string;
    required_min_balance_usdc?: number;
    required_smoke_notional_usdc?: number;
    required_min_allowance_usdc?: number;
    balance_shortfall_usdc?: number;
    smoke_notional_shortfall_usdc?: number;
    allowance_shortfall_usdc?: number;
    funding_ready?: boolean;
  };
  dryrun?: {
    ledger?: string;
    ledger_count?: number;
    would_place_count?: number;
    blocked_count?: number;
    submitted_count?: number;
    latest?: Record<string, unknown> | null;
  };
  live_gate?: {
    report?: string;
    available?: boolean;
    ok?: boolean;
    ready_for_live_smoke?: boolean;
    blockers?: string[];
    market_probe_count?: number;
    quote_executable_count?: number;
  };
  preflight_chain?: {
    report?: string;
    available?: boolean;
    ok?: boolean;
    submitted?: boolean;
    created_at?: string;
    age_seconds?: number | null;
    fresh?: boolean;
    max_age_seconds?: number;
    blockers?: string[];
    gate_ready?: boolean;
    smoke_mode?: string;
    open_orders?: number;
    settled?: number;
    risk_ok?: boolean;
    components?: Record<string, unknown>;
  };
  checklist?: ChecklistItem[];
  risk?: {
    ok: boolean;
    metrics: {
      day_utc: string;
      daily_pnl_usdc: number;
      daily_trades: number;
      consecutive_losses: number;
      open_or_pending_orders: number;
    };
    limits: {
      max_daily_loss_usdc: number;
      max_daily_trades: number;
      max_consecutive_losses: number;
      max_open_or_pending_orders: number;
    };
  };
}

interface LiveIntel {
  health?: {
    run_source?: string;
    state?: "ok" | "warning";
    warnings?: string[];
    checkpoint_age_seconds?: number | null;
    latest_event_age_seconds?: number | null;
    pending_count?: number;
    open_order_count?: number;
    trades_count?: number;
    stale_pending_count?: number;
  };
  issues?: {
    log_errors?: Array<{ file: string; line: string }>;
  };
}

type ChecklistItem = {
  key: string;
  label: string;
  ok: boolean;
  value?: string | number | null;
  expected?: string | null;
  severity?: string;
};

const money = (value?: number | null) =>
  value == null
    ? "-"
    : `$${value.toLocaleString(undefined, {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })}`;

const signedMoney = (value?: number | null) => {
  if (value == null) return "-";
  return `${value >= 0 ? "+" : ""}${money(value)}`;
};

const ageLabel = (seconds?: number | null) => {
  if (seconds == null) return "-";
  if (seconds < 90) return `${Math.round(seconds)}s`;
  return `${Math.round(seconds / 60)}m`;
};

function StatusChip({
  ok,
  label,
  icon,
}: {
  ok?: boolean;
  label: string;
  icon?: "lock" | "shield" | "wallet";
}) {
  const Icon = icon === "lock" ? Lock : icon === "wallet" ? Wallet : ok ? CheckCircle2 : AlertTriangle;
  const tone =
    ok == null
      ? "border-zinc-800 bg-zinc-950 text-zinc-400"
      : ok
        ? "border-emerald-500/25 bg-emerald-500/10 text-emerald-300"
        : "border-amber-500/25 bg-amber-500/10 text-amber-300";
  return (
    <span className={`inline-flex min-h-8 max-w-full items-center gap-1.5 rounded-md border px-2.5 text-xs ${tone}`}>
      <Icon className="h-3.5 w-3.5 shrink-0" />
      <span className="truncate">{label}</span>
    </span>
  );
}

function DetailTile({
  label,
  value,
  ok,
}: {
  label: string;
  value: string;
  ok?: boolean;
}) {
  const tone = ok == null ? "text-zinc-200" : ok ? "text-emerald-300" : "text-amber-300";
  return (
    <div className="rounded-md border border-zinc-800 bg-black/20 px-3 py-2">
      <div className="text-[11px] uppercase tracking-[0.14em] text-zinc-600">{label}</div>
      <div className={`mt-1 truncate font-mono text-sm ${tone}`}>{value}</div>
    </div>
  );
}

export default function StatusBar() {
  const [open, setOpen] = useState(false);
  const { data, error } = usePolling<StatusData>("/api/status?source=live", 5000);
  const { data: safety } = usePolling<SafetyData>("/api/live-safety", 5000);
  const { data: intel } = usePolling<LiveIntel>("/api/live-intel?limit=80", 5000);
  const now = new Date();
  const cooling = (data?.cooldown_left ?? 0) > 0;
  const liveEnabled = safety?.real_orders_enabled === true;
  const health = intel?.health;
  const metrics = safety?.risk?.metrics;
  const limits = safety?.risk?.limits;
  const healthOk = !error && health?.state !== "warning";
  const locked = !liveEnabled && safety?.kill_switch?.state !== "armed";
  const checklist = useMemo(() => {
    const apiItems = safety?.checklist ?? [];
    const runtimeItems: ChecklistItem[] = [
      {
        key: "checkpoint_fresh",
        label: "Checkpoint fresh",
        ok: (health?.checkpoint_age_seconds ?? 9999) < 600,
        value: ageLabel(health?.checkpoint_age_seconds),
        severity: "runtime",
      },
      {
        key: "events_fresh",
        label: "Events fresh",
        ok: (health?.latest_event_age_seconds ?? 9999) < 600,
        value: ageLabel(health?.latest_event_age_seconds),
        severity: "runtime",
      },
      {
        key: "no_log_errors",
        label: "No log errors",
        ok: (intel?.issues?.log_errors?.length ?? 0) === 0,
        value: intel?.issues?.log_errors?.length ?? 0,
        severity: "runtime",
      },
    ];
    return [...apiItems, ...runtimeItems];
  }, [health?.checkpoint_age_seconds, health?.latest_event_age_seconds, intel?.issues?.log_errors?.length, safety?.checklist]);
  const checksPassed = checklist.filter((item) => item.ok).length;
  const checksTotal = checklist.length;
  const checksOk = checksTotal > 0 && checksPassed === checksTotal;
  const riskOk = safety?.risk?.ok ?? true;
  const dryrunOk = (safety?.dryrun?.submitted_count ?? 0) === 0;
  const liveGateReady = safety?.live_gate?.ready_for_live_smoke === true;
  const preflightOk = safety?.preflight_chain?.ok === true;
  const preflightFresh = safety?.preflight_chain?.fresh === true;
  const preflightSubmitted = safety?.preflight_chain?.submitted === true;
  const preflightBlockers = safety?.preflight_chain?.blockers?.length ?? 0;
  const preflightReady = preflightOk && preflightFresh && !preflightSubmitted;
  const preflightAge = ageLabel(safety?.preflight_chain?.age_seconds);
  const fundingReady = safety?.funding?.funding_ready === true;
  const fundingGap = Math.max(
    safety?.funding?.balance_shortfall_usdc ?? 0,
    safety?.funding?.allowance_shortfall_usdc ?? 0,
  );
  const needsReview = Boolean(error) || cooling || liveEnabled || !healthOk || !checksOk || !riskOk || !preflightReady || !fundingReady;
  const source = safety?.run_source ?? health?.run_source ?? "aligned-prod";

  return (
    <div className="border-b border-zinc-800 bg-[#090a0f] text-xs">
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-2">
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className="flex min-w-0 flex-1 flex-wrap items-center gap-2 text-left"
          aria-expanded={open}
        >
          <span className="flex items-center gap-1.5 text-zinc-400">
            <span className={`h-2 w-2 rounded-full ${error ? "bg-rose-400" : "animate-pulse bg-emerald-400"}`} />
            <Radio className="h-3.5 w-3.5" />
            Live
          </span>
          <StatusChip ok={locked} label={liveEnabled ? "Real Enabled" : safety?.kill_switch?.state ?? "Locked"} icon="lock" />
          <StatusChip ok={healthOk} label={healthOk ? "Health OK" : "Review"} icon="shield" />
          <StatusChip ok={checksOk} label={`Checks ${checksPassed}/${checksTotal || 0}`} />
          <StatusChip
            ok={riskOk}
            label={
              metrics && limits
                ? `PnL ${signedMoney(metrics.daily_pnl_usdc)} · ${metrics.open_or_pending_orders}/${limits.max_open_or_pending_orders} open`
                : "Risk -"
            }
          />
          <StatusChip
            ok={dryrunOk}
            label={`Dry-run ${safety?.dryrun?.would_place_count ?? 0}/${safety?.dryrun?.ledger_count ?? 0}`}
          />
          <StatusChip
            ok={fundingReady}
            label={fundingReady ? "Funding ready" : `Funding gap ${money(fundingGap)}`}
            icon="wallet"
          />
          <StatusChip
            ok={liveGateReady}
            label={liveGateReady ? "Gate ready" : `Gate ${safety?.live_gate?.blockers?.length ?? 0}`}
          />
          <StatusChip
            ok={preflightReady}
            label={preflightReady ? `Preflight ${preflightAge}` : `Preflight ${preflightBlockers}`}
          />
          <span className="min-w-0 max-w-[220px] truncate font-mono text-zinc-600 md:max-w-[360px]">{source}</span>
          <ChevronDown className={`h-4 w-4 shrink-0 text-zinc-600 transition-transform ${open ? "rotate-180" : ""}`} />
        </button>

        <div className="flex shrink-0 items-center gap-3 text-zinc-500">
          <span>{now.toLocaleTimeString("zh-CN", { hour12: false })}</span>
          <span className={`flex items-center gap-1.5 ${needsReview ? "text-amber-300" : "text-emerald-300"}`}>
            {needsReview && <CircleAlert className="h-3.5 w-3.5" />}
            {error ? "API review" : cooling ? `Cooldown ${data?.cooldown_left} bar` : needsReview ? "Review" : "Normal"}
          </span>
        </div>
      </div>

      {open && (
        <div className="border-t border-zinc-800 px-4 py-3">
          <div className="grid gap-3 lg:grid-cols-[minmax(260px,0.8fr)_minmax(360px,1.2fr)]">
            <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
              <DetailTile label="Mode" value={`${safety?.mode?.toUpperCase() ?? "PAPER"} / ${safety?.kill_switch?.state ?? "locked"}`} ok={locked} />
              <DetailTile label="CLOB Auth" value={safety?.clob?.authenticated ? "OK" : "-"} ok={safety?.clob?.authenticated} />
              <DetailTile label="Balance" value={money(safety?.funding?.balance)} ok={safety?.funding?.balance_ok} />
              <DetailTile label="Allowance" value={String(safety?.funding?.min_allowance ?? "-")} ok={safety?.funding?.allowance_ok} />
              <DetailTile label="Balance Gap" value={money(safety?.funding?.balance_shortfall_usdc)} ok={(safety?.funding?.balance_shortfall_usdc ?? 0) === 0} />
              <DetailTile label="Allowance Gap" value={money(safety?.funding?.allowance_shortfall_usdc)} ok={(safety?.funding?.allowance_shortfall_usdc ?? 0) === 0} />
              <DetailTile
                label="Daily Trades"
                value={metrics && limits ? `${metrics.daily_trades}/${limits.max_daily_trades}` : "-"}
                ok={metrics && limits ? metrics.daily_trades < limits.max_daily_trades : undefined}
              />
              <DetailTile
                label="Loss Streak"
                value={metrics && limits ? `${metrics.consecutive_losses}/${limits.max_consecutive_losses}` : "-"}
                ok={metrics && limits ? metrics.consecutive_losses < limits.max_consecutive_losses : undefined}
              />
              <DetailTile label="Checkpoint" value={ageLabel(health?.checkpoint_age_seconds)} ok={(health?.checkpoint_age_seconds ?? 9999) < 600} />
              <DetailTile label="Latest Event" value={ageLabel(health?.latest_event_age_seconds)} ok={(health?.latest_event_age_seconds ?? 9999) < 600} />
              <DetailTile label="Open/Pending" value={`${metrics?.open_or_pending_orders ?? health?.pending_count ?? 0}`} ok={riskOk} />
              <DetailTile
                label="Dry-run Ledger"
                value={`${safety?.dryrun?.would_place_count ?? 0}/${safety?.dryrun?.ledger_count ?? 0}`}
                ok={dryrunOk}
              />
              <DetailTile
                label="Live Gate"
                value={liveGateReady ? "ready" : `${safety?.live_gate?.blockers?.length ?? 0} blockers`}
                ok={liveGateReady}
              />
              <DetailTile
                label="Preflight"
                value={preflightSubmitted ? "submitted" : preflightReady ? preflightAge : `${preflightBlockers} blockers`}
                ok={preflightReady}
              />
            </div>

            <div className="rounded-md border border-zinc-800 bg-black/20">
              <div className="flex items-center justify-between border-b border-zinc-800 px-3 py-2">
                <span className="font-medium text-zinc-200">Readiness Details</span>
                <span className={checksOk ? "font-mono text-emerald-300" : "font-mono text-amber-300"}>{checksPassed}/{checksTotal || 0}</span>
              </div>
              <div className="grid max-h-[260px] gap-2 overflow-auto p-3 md:grid-cols-2">
                {checklist.map((item) => (
                  <div
                    key={item.key}
                    className={`rounded border px-2.5 py-2 ${
                      item.ok ? "border-emerald-500/20 bg-emerald-500/5" : "border-amber-500/20 bg-amber-500/5"
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex min-w-0 items-center gap-2">
                        {item.ok ? <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-300" /> : <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-amber-300" />}
                        <span className="truncate text-zinc-200">{item.label}</span>
                      </div>
                      <span className={`shrink-0 font-mono ${item.ok ? "text-emerald-300" : "text-amber-300"}`}>{String(item.value ?? "-")}</span>
                    </div>
                    {item.expected && <div className="mt-1 truncate text-[11px] text-zinc-600">expected {item.expected}</div>}
                  </div>
                ))}
              </div>
            </div>
          </div>
          {(health?.warnings?.length ?? 0) > 0 && (
            <div className="mt-3 rounded-md border border-amber-500/20 bg-amber-500/5 px-3 py-2 font-mono text-xs text-amber-200">
              {health?.warnings?.join(" · ")}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
